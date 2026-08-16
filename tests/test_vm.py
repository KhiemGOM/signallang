import pytest

from signallang import DictSchemaProvider, ScriptError, compile_script


def run_all(src, limit=10_000, schema_provider=None):
    run = compile_script(src, schema_provider=schema_provider).new_run()
    out = []
    for _ in range(limit):
        r = run.step()
        if r is None:
            return out, True  # halted
        out.append(r)
    return out, False


def test_static_field_freezes_until_reassigned():
    results, halted = run_all("data = 5;\nsend hz 1 dur 3t;")
    assert halted
    assert [r.value["data"] for r in results] == [5.0, 5.0, 5.0]


def test_live_block_reevaluates_every_tick():
    results, _ = run_all("data = live { return t; };\nsend hz 1 dur 3t;")
    assert [round(r.value["data"], 3) for r in results] == [0.0, 1.0, 2.0]


def test_bounded_for_loop_halts_with_exactly_n_ticks():
    results, halted = run_all("for i in 0..7 {\n data = i * 30;\n send hz 1 dur 1t;\n}")
    assert halted
    assert [r.value["data"] for r in results] == [0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0]


def test_unbounded_repeat_with_self_referencing_var_no_unrolling_artifact():
    # regression test for "no eager unrolling, ever" - a Jump-based loop
    # only ever runs as many laps as it's stepped, so stepping this a large
    # number of times must produce the correct running count with no
    # special-cased recurrence handling anywhere.
    src = "var counter = 0;\nrepeat {\n counter = counter + 1;\n data = counter;\n send hz 1 dur 1t;\n}"
    run = compile_script(src).new_run()
    last = None
    for i in range(1, 2001):
        r = run.step()
        assert r.value["data"] == float(i)
        last = r
    assert last.value["data"] == 2000.0


def test_multi_tick_send_resumes_correctly_across_step_calls():
    results, halted = run_all("send hz 2 dur 10t;")
    assert halted
    assert len(results) == 10
    # evenly spaced at 1/hz = 0.5s, no trailing wait after the 10th
    run = compile_script("send hz 2 dur 10t;").new_run()
    for _ in range(10):
        run.step()
    assert run.step() is None


def test_t_and_underscore_t_latching_timer_semantics():
    # _t is a fresh latching_timer() per (re-)established live binding;
    # re-executing the assignment restarts it (example 5's "ramp restarts
    # cleanly on every lap" behavior).
    src = """
    repeat 2 {
        x = live { return _t.s; };
        send hz 1 dur 2t;
    }
    """
    results, _ = run_all(src)
    assert [round(r.value["x"], 3) for r in results] == [0.0, 1.0, 0.0, 1.0]


def test_timer_reset_zeros_eager_timer_immediately():
    src = "var mt = timer();\nsend hz 1 dur 1t;\nmt.reset();\ndata = mt.s;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[1].value["data"] == pytest.approx(0.0, abs=1e-9)


def test_default_and_positional_fill_against_schema():
    schema = DictSchemaProvider({"header": {"stamp": 0.0, "frame_id": ""}, "temperature": 0.0, "variance": 0.0})
    results, _ = run_all("send [default, 20.0, 0.1];", schema_provider=schema)
    assert results[0].value == {"header": {"stamp": 0.0, "frame_id": ""}, "temperature": 20.0, "variance": 0.1}


def test_positional_fill_length_mismatch_is_an_error():
    schema = DictSchemaProvider({"x": 0.0, "y": 0.0, "z": 0.0})
    with pytest.raises(ScriptError):
        run_all("msg = [1, 2];", schema_provider=schema)


def test_positional_fill_without_schema_provider_is_a_clear_error():
    with pytest.raises(ScriptError):
        run_all("msg = [1, 2];")


def test_live_reads_field_assigned_earlier_in_same_statement_order():
    # a live block may read outer vars/t/i freely; this checks the ordering
    # rule doesn't spuriously break normal live reads of vars.
    src = "var base = 10;\ndata = live { return base + t; };\nsend hz 1 dur 2t;"
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [10.0, 11.0]


def test_if_branches_on_string_comparison():
    src = 'var frame = "map";\nif frame == "map" {\n data = 1;\n} else {\n data = 0;\n}\nsend hz 1 dur 1t;'
    results, _ = run_all(src)
    assert results[0].value["data"] == 1.0


def test_if_condition_empty_string_is_falsy():
    # regression test: JumpIfFalse used to check `cond == 0.0`, which is
    # never true for a str (mismatched types never compare equal in
    # Python), so every string condition - even an empty one - took the
    # "true" branch. Fixed to use expr.is_truthy().
    src = 'if "" {\n data = 1;\n} else {\n data = 2;\n}\nsend hz 1 dur 1t;'
    results, _ = run_all(src)
    assert results[0].value["data"] == 2.0


def test_string_field_concatenation():
    src = 'var suffix = "link";\nframe_id = "base_" + suffix;\nsend hz 1 dur 1t;'
    results, _ = run_all(src)
    assert results[0].value["frame_id"] == "base_link"
