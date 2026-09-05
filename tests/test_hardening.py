"""Cross-feature regressions from the repository audit."""

import asyncio
import math
import random
import statistics

import pytest

from signallang import (
    DictSchemaProvider,
    ExprError,
    ScriptError,
    compile_script,
    evaluate,
    run_async,
    run_realtime,
)
from signallang.cli import main
from signallang.resources import MAX_DEPTH, MAX_SOURCE_CHARS, MAX_STRING_CHARS


def values(source, **options):
    return [r.value for r in compile_script(source).new_run(**options).collect(100) if r.sent]


def test_extern_aliases_cannot_mutate_host_or_extern_namespace():
    params = {"config": {"x": [1]}}
    compiled = compile_script("extern config; var alias = config; alias.x[0] = 9; data = config.x[0]; send dur 1t;")
    run = compiled.new_run(external_params=params)
    assert run.step().value == {"data": 1}
    assert params == {"config": {"x": [1]}}
    assert run.externs == params
    assert compiled.new_run(external_params=params).step().value == {"data": 1}


def test_container_assignments_and_published_results_are_snapshots():
    run = compile_script("var a = json {x: 1}; var b = a; data = a; b.x = 9; send dur 2t;").new_run()
    first = run.step()
    first.value["data"]["x"] = 100
    assert run.step().value == {"data": {"x": 1}}


@pytest.mark.parametrize("payload", [object(), {"a": float("nan")}, [float("inf")], {1: "x"}])
def test_externs_reject_unsupported_value_trees(payload):
    with pytest.raises(ScriptError):
        compile_script("extern config;").new_run(external_params={"config": payload})


def test_cycles_and_expanding_shared_trees_are_bounded():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ScriptError, match="cyclic"):
        compile_script("extern config;").new_run(external_params={"config": cycle})
    tree = [1]
    for _ in range(20):
        tree = [tree, tree]
    with pytest.raises(ScriptError, match="size|operation_budget"):
        compile_script("extern config;").new_run(external_params={"config": tree})


@pytest.mark.parametrize(
    "source",
    [
        "data = binomial(1000, 0.5); send dur 1t;",
        "data = binomial!(1000, 0.5); send dur 1t;",
        "data = poisson!(1000); send dur 1t;",
    ],
)
def test_operation_budget_covers_static_live_and_builtin_work(source):
    run = compile_script(source).new_run(operation_budget=50, seed=42)
    with pytest.raises(ScriptError, match="operation_budget"):
        run.step()
    assert run.step() is None  # failures cannot retry partially advanced state


@pytest.mark.parametrize("expression", ["poisson(100001)", "binomial(1000001, 0.5)", "noise(0, -1)", "uniform(2, 1)"])
def test_distribution_parameter_limits(expression):
    with pytest.raises(ExprError):
        evaluate(expression, {})


@pytest.mark.parametrize("lam", [0.1, 20, 1000])
def test_poisson_mean_and_variance(lam):
    rng = random.Random(123)
    draws = [evaluate(f"poisson({lam})", {}, rng=rng) for _ in range(2000)]
    assert abs(statistics.mean(draws) - lam) < 6 * math.sqrt(lam / len(draws))
    assert statistics.variance(draws) == pytest.approx(lam, rel=0.2)
    assert all(type(value) is int and value >= 0 for value in draws)


def test_seeded_runs_are_independent_of_interleaving_and_host_randomness():
    source = "seed(42); data = live [random(), noise(0, 1), poisson(1000), binomial(10, 0.5)]; send dur 4t;"
    compiled = compile_script(source)
    expected = [r.value for r in compiled.new_run().collect(4)]
    a, b = compiled.new_run(), compiled.new_run()
    host_state = random.getstate()
    actual_a, actual_b = [], []
    for _ in range(4):
        actual_a.append(a.step().value)
        actual_b.append(b.step().value)
    assert actual_a == actual_b == expected
    assert random.getstate() == host_state


def test_host_seed_and_compiled_expressions_hold_no_run_state():
    source = "data = random!(); send dur 3t;"
    assert values(source, seed=9) == values(source, seed=9)
    assert values(source, seed=9) != values(source, seed=10)


@pytest.mark.parametrize("rhs", ['"wrong"', 'live "wrong"', "json {x: 1}", "live json {x: 1}"])
def test_schema_leaf_types_apply_to_every_write_form(rhs):
    run = compile_script(f"level = {rhs}; send dur 1t;", DictSchemaProvider({"level": 0})).new_run()
    with pytest.raises(ScriptError, match="expects"):
        run.step()


@pytest.mark.parametrize(
    "source",
    [
        "unknown = 1; send dur 1t;",
        'header = json {x: "wrong"}; send dur 1t;',
        'header = live json {x: "wrong"}; send dur 1t;',
        'send json {header: json {x: 1}, points: [1, "wrong"]} dur 1t;',
        "send json {header: json {x: 1}} dur 1t;",
    ],
)
def test_strict_schema_rejects_recursive_shape_errors(source):
    schema = DictSchemaProvider({"header": {"x": 1}, "points": [0]}, strict=True)
    with pytest.raises(ScriptError):
        compile_script(source, schema).new_run().step()


def test_schema_array_fields_defaults_and_defensive_copy():
    raw = {"points": [0]}
    schema = DictSchemaProvider(raw, strict=True)
    raw["points"][0] = 99
    schema.default_at([])["points"][0] = 88
    assert schema.default_at([]) == {"points": [0]}
    run = compile_script("points = [1, 2, 3]; send dur 1t;", schema).new_run()
    assert run.step().value == {"points": [1, 2, 3]}


def test_latching_timer_only_starts_on_a_read_and_reset_unlatches():
    source = "var lt = latching_timer(); unrelated = 123; wait 2s; elapsed = lt.s; send dur 1t; lt.reset(); unrelated = 456; wait 3s; elapsed = lt.s; send dur 1t;"
    assert [v["elapsed"] for v in values(source)] == [0, 0]


def test_short_circuit_does_not_latch_unread_timer_or_draw_randomness():
    source = "var lt = latching_timer(); data = false and lt > 0; wait 2s; data = lt; send dur 1t;"
    assert values(source) == [{"data": 0}]
    assert evaluate("false and (1 / 0)", {}) is False
    assert evaluate("true or (1 / 0)", {}) is True
    rng = random.Random(42)
    state = rng.getstate()
    evaluate("true or random()", {}, rng=rng)
    assert rng.getstate() == state


@pytest.mark.parametrize(
    "source, expected",
    [
        ("header.x = live 1; send dur 1t; header = json {x: 9}; send dur 1t;", {"header": {"x": 9}}),
        ("x = live 1; send dur 1t; send json {y: 9} dur 1t;", {"y": 9}),
        ("header = live json {x: 1}; send dur 1t; header.x = 9; send dur 1t;", {"header": {"x": 9}}),
    ],
)
def test_new_writes_take_ownership_of_overlapping_live_paths(source, expected):
    assert values(source)[-1] == expected


@pytest.mark.parametrize(
    "source",
    [
        "data = 1 + ; send dur 1t;",
        "send dur 1t; if false { data = unknown(1); }",
        "data = sin(1, 2); send dur 1t;",
        "data = 1.2.3; send dur 1t;",
    ],
)
def test_compile_rejects_bad_expressions_before_execution(source):
    with pytest.raises(ExprError) as error:
        compile_script(source)
    assert error.value.pos is not None


def test_compile_does_not_sample_randomness_or_execute_expressions():
    state = random.getstate()
    compile_script("data = random(); broken = 1 / 0; send dur 1t;")
    assert random.getstate() == state


@pytest.mark.parametrize(
    "expression", ["1 % 0", "1 / 0", "exponential(1000, 1, 1)", "+true", "sqrt(true)", "sin(false)"]
)
def test_runtime_errors_are_located_language_errors(expression):
    source = f"first = 1;\ndata = {expression}; send dur 1t;"
    with pytest.raises(ExprError) as error:
        compile_script(source).new_run().step()
    assert source.index(expression) <= error.value.pos < source.index("; send")


def test_live_static_initializers_and_duration_expressions_compose():
    source = "data = live { static a = _t; static b = a + 2; var d = 500ms + 500ms; return linear(_t, a, b, d); }; send hz 2 dur 3t;"
    assert [v["data"] for v in values(source)] == [0, 1, 2]


def test_macro_hygiene_preserves_keys_properties_and_unit_suffixes():
    source = "func build(field) { var x = 1; var s = 2; var obj = json {x: x, s: 2s}; field = json {x: obj.x, s: obj.s}; } build(out); send dur 1t;"
    assert values(source) == [{"out": {"x": 1, "s": 2.0}}]


def test_macro_error_points_to_original_call_site():
    source = "func bad(field) { field = 1 + ; }\n\nbad(data);"
    with pytest.raises(ExprError) as error:
        compile_script(source)
    assert error.value.pos == source.index("bad(data)")


@pytest.mark.parametrize("max_hz", [0, -1, True, float("inf"), float("nan"), "10"])
def test_invalid_rate_ceiling_fails_at_compile(max_hz):
    with pytest.raises(ScriptError):
        compile_script("send;", max_hz=max_hz)


@pytest.mark.parametrize(
    "source", ["send dur 0t;", "send dur 1.5t;", "send dur 0s;", "send hz 0;", "wait 0s;", "send hz 1.2.3;"]
)
def test_invalid_literal_schedule_parameters(source):
    with pytest.raises(ScriptError):
        compile_script(source)


@pytest.mark.parametrize(
    "options", [{"operation_budget": 0}, {"step_instruction_budget": True}, {"seed": []}, {"seed": float("nan")}]
)
def test_invalid_host_run_options(options):
    with pytest.raises(ScriptError):
        compile_script("send;").new_run(**options)


def test_parameterized_schedule_and_trace_metadata():
    run = compile_script(
        "extern rate; extern length; data = live t; send hz (rate) dur (length)t; wait (500ms + 500ms); send hz (rate * 2) dur 1t;"
    ).new_run(external_params={"rate": 2, "length": 2})
    trace = run.collect(10)
    assert [r.timestamp for r in trace] == [0, 0.5, 1, 2]
    assert [r.sequence for r in trace] == [0, 1, 2, 3]
    assert [r.delay for r in trace] == [0.5, 0.5, 1, 0.25]
    assert [r.sent for r in trace] == [True, True, False, True]


@pytest.mark.parametrize("source", ["send hz (0);", "send dur (1.5)t;", "wait (-1);", "send hz (true);"])
def test_dynamic_schedule_parameters_are_checked_before_publish(source):
    with pytest.raises(ScriptError):
        compile_script(source).new_run().step()


def test_keyframes_interpolation_hold_repeat_and_bang():
    assert evaluate("keyframes(1, [[0s, 0], [2s, 10]])", {}) == 5
    assert evaluate('keyframes(1, [[0, 0], [2, 10]], "hold")', {}) == 0
    assert evaluate('keyframes(3, [[0, 0], [2, 10]], "linear", true)', {}) == 5
    assert [v["data"] for v in values("data = keyframes!([[0s, 0], [2s, 10]]); send hz 1 dur 4t;")] == [0, 5, 10, 10]
    with pytest.raises(ExprError):
        evaluate("keyframes(1, [[2, 0], [1, 10]])", {})


def test_source_and_value_limits_fail_cleanly():
    with pytest.raises(ScriptError):
        compile_script(" " * (MAX_SOURCE_CHARS + 1))
    with pytest.raises(ScriptError):
        compile_script("data = " + "(" * (MAX_DEPTH + 1) + "1" + ")" * (MAX_DEPTH + 1) + ";")
    with pytest.raises(ScriptError):
        evaluate("x", {"x": "a" * (MAX_STRING_CHARS + 1)})


def test_cli_reports_expression_locations_missing_files_and_invalid_limits(tmp_path, capsys):
    path = tmp_path / "bad.signal"
    path.write_text("data = 1 + ;")
    assert main(["validate", str(path)]) == 1
    assert ":1:" in capsys.readouterr().err
    assert main(["validate", str(tmp_path / "missing.signal")]) == 1
    assert main(["run", str(path), "--ticks", "-1"]) == 1


def test_cli_trace_includes_waits(tmp_path, capsys):
    import json

    path = tmp_path / "trace.signal"
    path.write_text("wait 1s; send dur 1t;")
    assert main(["run", str(path), "--trace"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["sent"] is False and rows[0]["value"] is None
    assert rows[1]["timestamp"] == 1


def test_both_comment_styles_work_and_strings_are_unchanged():
    assert values('data = "# // text"; # comment\nsend dur 1t; // comment') == [{"data": "# // text"}]


def test_realtime_deadlines_include_step_cost(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("signallang.realtime.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("signallang.realtime.time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    run = compile_script("send hz 10 dur 3t;").new_run()
    step = run.step

    def costly_step():
        clock[0] += 0.02
        return step()

    monkeypatch.setattr(run, "step", costly_step)
    sent = []
    run_realtime(run, lambda value: sent.append(clock[0]))
    assert sent == pytest.approx([0.02, 0.12, 0.22])


def test_realtime_forwards_externs_and_can_cancel_during_wait(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("signallang.realtime.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("signallang.realtime.time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    received = []
    run_realtime(
        compile_script("extern x; send x hz 1;"),
        received.append,
        external_params={"x": 7},
        cancelled=lambda: clock[0] >= 0.2,
    )
    assert received == [{"data": 7}] and clock[0] <= 0.3


@pytest.mark.parametrize("policy", ["delay", "catch_up", "error"])
def test_realtime_overrun_policies(monkeypatch, policy):
    clock = [0.0]
    monkeypatch.setattr("signallang.realtime.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("signallang.realtime.time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))

    def send(value):
        clock[0] += 0.2

    if policy == "error":
        with pytest.raises(ScriptError, match="deadline"):
            run_realtime(compile_script("send hz 10 dur 2t;"), send, late_policy=policy)
    else:
        run_realtime(compile_script("send hz 10 dur 2t;"), send, late_policy=policy)
        assert clock[0] == pytest.approx(0.4)


def test_async_driver_skips_waits_and_serializes_callbacks(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("signallang.realtime.time.monotonic", lambda: clock[0])

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr("signallang.realtime.asyncio.sleep", sleep)
    received = []

    async def send(value):
        received.append(value)

    asyncio.run(run_async(compile_script("wait 1s; send 5 dur 2t;"), send))
    assert received == [{"data": 5}, {"data": 5}]


def test_async_task_cancellation_propagates():
    async def scenario():
        started = asyncio.Event()

        async def send(value):
            started.set()

        task = asyncio.create_task(run_async(compile_script("send hz 1;"), send))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
