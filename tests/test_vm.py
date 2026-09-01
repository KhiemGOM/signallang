import pytest

from signallang import DictSchemaProvider, ExprError, ScriptError, compile_script


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


def test_live_static_persists_across_ticks():
    src = """
    data = live {
        static value = 0;
        value = value + 1;
        return value;
    };
    send hz 1 dur 4t;
    """
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [1.0, 2.0, 3.0, 4.0]


def test_live_static_resets_when_binding_is_rebound():
    # same reset-on-rebind rule as _t: re-executing the assignment
    # statement (here, once per repeat lap) starts the static over.
    src = """
    repeat 2 {
        data = live {
            static value = 0;
            value = value + 1;
            return value;
        };
        send hz 1 dur 2t;
    }
    """
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [1.0, 2.0, 1.0, 2.0]


def test_live_static_can_be_reassigned_conditionally_inside_if():
    # the restriction is on where `static NAME = init;` may appear (top
    # level only) - reading/reassigning an already-declared static from
    # inside if/else is ordinary and unrestricted.
    src = """
    data = live {
        static value = 0;
        if t < 1.5 {
            value = value + 1;
        } else {
            value = value - 1;
        }
        return value;
    };
    send hz 1 dur 4t;
    """
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [1.0, 2.0, 1.0, 0.0]


def test_live_static_two_bindings_do_not_share_storage():
    # two separate live blocks, each with their own `static value`, must
    # not collide just because they happen to use the same local name.
    src = """
    a = live {
        static value = 0;
        value = value + 1;
        return value;
    };
    b = live {
        static value = 100;
        value = value + 10;
        return value;
    };
    send hz 1 dur 2t;
    """
    results, _ = run_all(src)
    assert [r.value["a"] for r in results] == [1.0, 2.0]
    assert [r.value["b"] for r in results] == [110.0, 120.0]


def test_live_static_random_walk_pattern():
    # the motivating use case: a random walk built entirely in userland,
    # no dedicated builtin - just noise() plus a persisted accumulator.
    # Not a determinism test (noise() is genuinely random); just confirms
    # it accumulates rather than resetting to a fresh draw each tick.
    src = """
    walk = live {
        static value = 0;
        value = value + noise(0, 0.01);
        return value;
    };
    send hz 1 dur 20t;
    """
    results, _ = run_all(src)
    values = [r.value["walk"] for r in results]
    running = 0.0
    for v in values:
        assert abs(v - running) < 1.0  # each step is one small noise() draw, not a fresh unrelated value
        running = v


def test_live_static_discrete_random_walk_pattern():
    # the discrete-lattice sibling: same pattern, discrete_uniform(-1, 1)
    # instead of noise() - each step is exactly -1, 0, or 1, not a draw
    # from the continuum.
    src = """
    walk = live {
        static value = 0;
        value = value + discrete_uniform(-1, 1);
        return value;
    };
    send hz 1 dur 20t;
    """
    results, _ = run_all(src)
    values = [r.value["walk"] for r in results]
    running = 0.0
    for v in values:
        assert (v - running) in (-1.0, 0.0, 1.0)
        running = v


def test_rand_walk_bang_call_accumulates_like_the_hand_written_pattern():
    results, _ = run_all("walk = rand_walk!(-1, 1);\nsend hz 1 dur 20t;")
    values = [r.value["walk"] for r in results]
    running = 0.0
    for v in values:
        assert (v - running) in (-1.0, 0.0, 1.0)
        running = v


def test_brown_motion_bang_call_accumulates_like_the_hand_written_pattern():
    results, _ = run_all("bm = brown_motion!(0, 0.01);\nsend hz 1 dur 20t;")
    values = [r.value["bm"] for r in results]
    running = 0.0
    for v in values:
        assert abs(v - running) < 1.0  # each step is one small noise() draw
        running = v


def test_rand_walk_resets_when_binding_is_rebound():
    # same reset-on-rebind schedule as any other static, since it's
    # built on the identical mechanism.
    src = """
    repeat 2 {
        walk = rand_walk!(1, 1);
        send hz 1 dur 2t;
    }
    """
    results, _ = run_all(src)
    assert [r.value["walk"] for r in results] == [1.0, 2.0, 1.0, 2.0]


def test_timer_reset_zeros_eager_timer_immediately():
    src = "var mt = timer();\nsend hz 1 dur 1t;\nmt.reset();\ndata = mt.s;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[1].value["data"] == pytest.approx(0.0, abs=1e-9)


def test_seed_makes_a_random_sequence_reproducible():
    src = "seed(42);\ndata = noise!(0, 1);\nsend hz 1 dur 5t;"

    def run():
        run = compile_script(src).new_run()
        return [round(run.step().value["data"], 9) for _ in range(5)]

    assert run() == run()


def test_seed_with_different_values_diverges():
    def run(seed_value):
        src = f"seed({seed_value});\ndata = noise!(0, 1);\nsend hz 1 dur 3t;"
        run = compile_script(src).new_run()
        return [run.step().value["data"] for _ in range(3)]

    assert run(1) != run(2)


def test_wait_advances_time_without_producing_a_sent_result():
    src = "data = 1;\nsend hz 10 dur 1t;\nwait 0.5s;\ndata = t;\nsend hz 10 dur 1t;"
    run = compile_script(src).new_run()
    first = run.step()
    gap = run.step()
    second = run.step()
    assert first.sent is True and first.value == {"data": 1.0}
    assert gap.sent is False and gap.value is None
    assert gap.hz == pytest.approx(2.0)  # 1 / 0.5s
    assert second.sent is True
    assert second.value["data"] == pytest.approx(0.1 + 0.5)  # first tick's t + the wait


def test_wait_inside_a_loop_can_simulate_dropped_ticks():
    # the packet-loss pattern: same cadence either way, some laps publish
    # nothing instead of a value.
    src = """
    seed(7);
    repeat {
        if uniform(0, 1) < 0.5 {
            data = 1;
            send hz 10 dur 1t;
        } else {
            wait 0.1s;
        }
    }
    """
    run = compile_script(src).new_run()
    results = [run.step() for _ in range(20)]
    assert any(r.sent for r in results)
    assert any(not r.sent for r in results)
    for r in results:
        assert r.hz == pytest.approx(10.0)


def test_default_and_positional_fill_against_schema():
    schema = DictSchemaProvider({"header": {"stamp": 0.0, "frame_id": ""}, "temperature": 0.0, "variance": 0.0})
    results, _ = run_all("send [default, 20.0, 0.1];", schema_provider=schema)
    assert results[0].value == {"header": {"stamp": 0.0, "frame_id": ""}, "temperature": 20.0, "variance": 0.1}


def test_positional_fill_length_mismatch_is_an_error():
    schema = DictSchemaProvider({"x": 0.0, "y": 0.0, "z": 0.0})
    with pytest.raises(ScriptError):
        run_all("msg = [1, 2];", schema_provider=schema)


def test_array_literal_without_schema_provider_publishes_as_a_plain_array():
    # no schema - not an error, and not positional fill either. A bare
    # array literal with nothing to map it against is just published as
    # a real array value. path=[] wraps non-dict values under "data",
    # same convention as a whole-msg scalar assignment already uses.
    results, _ = run_all("msg = [1, 2];\nsend hz 1 dur 1t;")
    assert results[0].value == {"data": [1.0, 2.0]}


def test_array_literal_without_schema_provider_at_a_nested_field():
    results, _ = run_all("points = [1, 2, 3];\nsend hz 1 dur 1t;")
    assert results[0].value == {"points": [1.0, 2.0, 3.0]}


def test_nested_array_and_json_without_schema_provider():
    results, _ = run_all('points = [1, json { x: 2, y: 3 }];\nsend hz 1 dur 1t;')
    assert results[0].value == {"points": [1.0, {"x": 2.0, "y": 3.0}]}


def test_default_inside_a_schema_free_array_is_still_an_error():
    # default has no schema-free meaning - there's no field to ask a
    # missing schema for a zero value at.
    with pytest.raises(ScriptError):
        run_all("msg = [default, 2];")


def test_live_reads_field_assigned_earlier_in_same_statement_order():
    # a live block may read outer vars/t/i freely; this checks the ordering
    # rule doesn't spuriously break normal live reads of vars.
    src = "var base = 10;\ndata = live { return base + t; };\nsend hz 1 dur 2t;"
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [10.0, 11.0]


def test_bool_field_publishes_as_a_genuine_bool_not_a_number():
    src = "is_valid = true;\nlevel = 5;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["is_valid"] is True
    level = results[0].value["level"]
    assert isinstance(level, (int, float)) and not isinstance(level, bool)


def test_comparison_result_written_to_a_field_is_a_bool():
    src = "var threshold = 5;\nover = threshold > 3;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["over"] is True


def test_int_field_publishes_as_a_genuine_int_not_a_float():
    src = "count = 5;\nratio = count / 2;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    count = results[0].value["count"]
    ratio = results[0].value["ratio"]
    assert isinstance(count, int) and not isinstance(count, bool)
    assert isinstance(ratio, float)
    assert ratio == 2.5


def test_int_var_index_assign_still_works():
    src = "var arr = [1, 2, 3];\narr[1] = 99;\ndata = arr;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == [1, 99, 3]


def test_duration_var_propagates_through_bang_call():
    src = "var d = 10s;\ndata = linear!(0, 1, d);\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 0.0


def test_duration_var_copy_still_propagates():
    src = "var d = 5s;\nvar d2 = d;\ndata = square(0, 0, 1, d2);\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 0.0


def test_plain_var_rejected_where_duration_required():
    # regression test for the actual motivating gap: a var that was
    # never declared from a duration literal, silently passed where one
    # was expected.
    src = "var count = 5;\ndata = square(0, 0, 1, count);\nsend;"
    with pytest.raises((ScriptError, ExprError)):
        run_all(src)


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


def test_bang_call_reevaluates_every_tick():
    # hz=1 -> _t advances by 1.0 (second) per tick: 0, 1, 2, 3. period=2s
    # (square's argument is plain seconds, same units as _t - not a
    # tick-count literal, which only send's own dur modifier understands).
    src = "data = square!(0, 1, 2);\nsend hz 1 dur 4t;"
    results, _ = run_all(src)
    # low for _t=0, high for _t=1, low again for _t=2, high for _t=3 -
    # genuinely re-evaluating _t each step, not frozen after the first tick.
    assert [r.value["data"] for r in results] == [0.0, 1.0, 0.0, 1.0]


def test_bang_call_shift_delays_the_waveform_start():
    # same script as above, .shift(1) later - the whole sequence delays
    # by exactly one tick (negative effective _t wraps via % same as any
    # other negative input to square's modulo).
    src = "data = square!(0, 1, 2).shift(1);\nsend hz 1 dur 4t;"
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [1.0, 0.0, 1.0, 0.0]


def test_bang_call_scale_and_add_apply_to_the_live_result_every_tick():
    src = "data = square!(0, 1, 2).scale(10).add(1);\nsend hz 1 dur 4t;"
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [1.0, 11.0, 1.0, 11.0]


def test_live_shorthand_without_bang_reevaluates_every_tick():
    # `live <expr>;` - the keyword-prefix shorthand for the general case
    # (not a single bang-callable function), still real live semantics.
    src = "data = live t * 2;\nsend hz 1 dur 3t;"
    results, _ = run_all(src)
    assert [r.value["data"] for r in results] == [0.0, 2.0, 4.0]


def test_plain_call_without_bang_is_static_once():
    # sin(t) with no '!' is an ordinary one-shot call - evaluated once at
    # assignment time and frozen, exactly like a plain number would be.
    src = "data = sin(t);\nsend hz 1 dur 3t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == results[1].value["data"] == results[2].value["data"]


def test_var_holding_an_array_can_be_indexed():
    src = "var arr = [10, 20, 30];\ndata = arr[1];\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 20.0


def test_json_object_as_whole_message_needs_no_schema():
    # this is the point of json {} - unlike positional [] array fill, it
    # names its own fields, so it works with schema_provider=None.
    src = 'msg = json { header: json { frame_id: "map" }, temperature: 20.0 };\nsend hz 1 dur 1t;'
    results, _ = run_all(src)
    assert results[0].value == {"header": {"frame_id": "map"}, "temperature": 20.0}


def test_json_object_as_a_positional_array_fill_element():
    # a json {} element inside the OUTER positional-fill array still
    # needs a schema (the outer [] hasn't changed meaning), but the
    # element itself is schema-free.
    schema = DictSchemaProvider({"first": 0.0, "second": 0.0})
    results, _ = run_all("send [json { a: 1, b: 2 }, 5.0];", schema_provider=schema)
    assert results[0].value == {"first": {"a": 1.0, "b": 2.0}, "second": 5.0}


# -- optional schema type-checking (DictSchemaProvider.type_at) --------------

def test_schema_type_check_passes_when_types_match():
    schema = DictSchemaProvider({"is_valid": False, "level": 0, "ratio": 0.0, "name": ""})
    src = "is_valid = true;\nlevel = 5;\nratio = 2.5;\nname = \"x\";\nsend hz 1 dur 1t;"
    results, _ = run_all(src, schema_provider=schema)
    assert results[0].value == {"is_valid": True, "level": 5, "ratio": 2.5, "name": "x"}


def test_schema_type_check_rejects_bool_field_written_a_number():
    schema = DictSchemaProvider({"is_valid": False})
    with pytest.raises(ScriptError):
        run_all("is_valid = 5;\nsend;", schema_provider=schema)


def test_schema_type_check_rejects_int_field_written_a_float():
    schema = DictSchemaProvider({"level": 0})
    with pytest.raises(ScriptError):
        run_all("level = 5.5;\nsend;", schema_provider=schema)


def test_schema_type_check_rejects_float_field_written_an_int():
    schema = DictSchemaProvider({"ratio": 0.0})
    with pytest.raises(ScriptError):
        run_all("ratio = 5;\nsend;", schema_provider=schema)


def test_schema_type_check_skips_a_whole_sub_message_write():
    # a dict being written at once (a json {} literal assigned directly
    # to an object-typed field) has no single leaf type to check against
    # - each of ITS OWN leaves would get checked if written individually
    # instead, but this form is deliberately not walked recursively.
    schema = DictSchemaProvider({"header": {"frame_id": ""}})
    results, _ = run_all('header = json { frame_id: "map" };\nsend hz 1 dur 1t;', schema_provider=schema)
    assert results[0].value == {"header": {"frame_id": "map"}}


def test_schema_without_type_at_is_completely_unaffected():
    # a SchemaProvider implementation that predates type_at (a real
    # ROS-backed one, for instance) must keep publishing exactly what it
    # always did - type-checking is opt-in, never assumed.
    class LegacySchemaProvider:
        def fields_at(self, path):
            return []

        def default_at(self, path):
            return {} if not path else 0.0

    results, _ = run_all("is_valid = 5;\nsend hz 1 dur 1t;", schema_provider=LegacySchemaProvider())
    assert results[0].value == {"is_valid": 5}


# -- msg starts fully defaulted when a schema is given -----------------------

def test_bare_send_with_a_schema_sends_the_fully_defaulted_message():
    # no field assignments at all - the message still starts as the
    # schema's own default tree, not empty. default_at([]) already
    # builds this whole-tree default for an explicit `msg = default;`;
    # this applies it automatically at run start instead.
    schema = DictSchemaProvider({"header": {"stamp": 0.0, "frame_id": ""}, "temperature": 0.0})
    results, _ = run_all("send hz 1 dur 1t;", schema_provider=schema)
    assert results[0].value == {"header": {"stamp": 0.0, "frame_id": ""}, "temperature": 0.0}


def test_partial_field_assignment_leaves_the_rest_at_default():
    schema = DictSchemaProvider({"temperature": 0.0, "variance": 0.0})
    results, _ = run_all("temperature = 25.0;\nsend hz 1 dur 1t;", schema_provider=schema)
    assert results[0].value == {"temperature": 25.0, "variance": 0.0}


def test_bare_send_without_a_schema_still_starts_empty():
    results, _ = run_all("send hz 1 dur 1t;")
    assert results[0].value == {}


def test_two_runs_sharing_a_schema_provider_do_not_alias_each_others_default_msg():
    # regression guard for the deepcopy in ScriptRun.__init__: mutating
    # one run's defaulted message must never affect a second run built
    # from the same schema_provider.
    schema = DictSchemaProvider({"header": {"frame_id": ""}})
    run_a = compile_script('header.frame_id = "map";\nsend hz 1 dur 1t;', schema_provider=schema).new_run()
    run_b = compile_script("send hz 1 dur 1t;", schema_provider=schema).new_run()
    a_result = run_a.step()
    b_result = run_b.step()
    assert a_result.value == {"header": {"frame_id": "map"}}
    assert b_result.value == {"header": {"frame_id": ""}}


# -- msg.field reads back the message currently being built ------------------

def test_msg_dot_access_reads_a_previously_assigned_field():
    src = "angular = 5;\ndata = msg.angular + 1;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 6.0


def test_msg_reads_a_nested_path():
    src = 'header = json { frame_id: "map" };\ndata = msg.header.frame_id;\nsend hz 1 dur 1t;'
    results, _ = run_all(src)
    assert results[0].value["data"] == "map"


def test_msg_read_reflects_the_schemas_auto_filled_defaults():
    schema = DictSchemaProvider({"temperature": 0.0, "variance": 0.0})
    src = "variance = msg.temperature + 5;\nsend hz 1 dur 1t;"
    results, _ = run_all(src, schema_provider=schema)
    assert results[0].value["variance"] == 5.0


def test_msg_access_is_deep_copied_not_aliased():
    # a var capturing part of msg must not be silently mutated later by
    # an unrelated field write reusing the same nested dict in place.
    src = """
    header = json { frame_id: "map" };
    var h = msg.header;
    header = json { frame_id: "odom" };
    data = h.frame_id;
    send hz 1 dur 1t;
    """
    results, _ = run_all(src)
    assert results[0].value["data"] == "map"
    assert results[0].value["header"]["frame_id"] == "odom"


def test_msg_is_reserved_and_cannot_be_a_var_name():
    with pytest.raises(ScriptError):
        run_all("var msg = 5;")


# -- bare-name sugar for a top-level msg field --------------------------

def test_bare_name_reads_a_top_level_msg_field_when_unambiguous():
    src = "angular = 5;\ndata = angular + 1;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 6.0


def test_var_shadows_the_bare_name_sugar():
    src = "angular = 5;\nvar angular = 100;\ndata = angular;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 100.0


def test_explicit_msg_dot_still_reaches_a_shadowed_field():
    src = "angular = 5;\nvar angular = 100;\ndata = msg.angular;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 5.0


def test_bare_name_sugar_does_not_apply_to_nested_fields():
    # two different paths could share a leaf name (linear.x, angular.x) -
    # only top-level fields get the sugar, never a nested one. This
    # errors during expression evaluation (ExprError), not parsing.
    src = 'header = json { frame_id: "map" };\ndata = frame_id;\nsend hz 1 dur 1t;'
    with pytest.raises(ExprError):
        run_all(src)


def test_bare_name_sugar_does_not_shadow_a_builtin_or_constant_name():
    # a field literally named "sin" or "pi" stays reachable only via
    # msg.sin / msg.pi - the reserved-name set wins the same way it
    # already does for var/for-loop declarations.
    src = "sin = 5;\ndata = msg.sin;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 5.0
    with pytest.raises(ExprError):
        run_all("sin = 5;\ndata = sin;\nsend hz 1 dur 1t;")


def test_bare_name_sugar_reflects_schema_auto_filled_defaults():
    schema = DictSchemaProvider({"temperature": 0.0, "variance": 0.0})
    results, _ = run_all("variance = temperature + 5;\nsend hz 1 dur 1t;", schema_provider=schema)
    assert results[0].value["variance"] == 5.0


# -- assigning into a var-held array/object -----------------------------

def test_dot_assign_into_a_var_held_object():
    # regression test: config.retries = 5; used to silently write an
    # unrelated top-level message field named "config" instead of
    # mutating the var, since only single-segment paths were ever
    # checked against known_vars.
    src = "var config = json { retries: 3 };\nconfig.retries = 5;\ndata = config.retries;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value == {"data": 5.0}  # no phantom "config" field in the message


def test_bracket_assign_into_a_var_held_array():
    src = "var arr = [1, 2, 3];\narr[1] = 99;\ndata = arr[1];\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == 99.0


def test_chained_dot_and_bracket_assign():
    src = (
        "var config = json { points: [1, 2, json { x: 0 }] };\n"
        "config.points[2].x = 42;\n"
        "data = config.points[2].x;\n"
        "send hz 1 dur 1t;"
    )
    results, _ = run_all(src)
    assert results[0].value["data"] == 42.0


def test_intermediate_missing_dict_key_auto_vivifies():
    src = 'var config = json {};\nconfig.header.frame_id = "map";\ndata = config.header.frame_id;\nsend hz 1 dur 1t;'
    results, _ = run_all(src)
    assert results[0].value["data"] == "map"


def test_auto_vivify_does_not_clobber_an_existing_list():
    # regression test for the auto-vivify fix itself: walking .points
    # (an existing array) must never replace it with {} just because
    # it isn't a dict - only a genuinely MISSING key auto-vivifies.
    src = "var config = json { points: [1, 2, 3] };\nconfig.points[0] = 99;\ndata = config.points;\nsend hz 1 dur 1t;"
    results, _ = run_all(src)
    assert results[0].value["data"] == [99.0, 2.0, 3.0]


@pytest.mark.parametrize(
    "src",
    [
        "var arr = [1, 2];\narr[5] = 1;\nsend hz 1 dur 1t;",  # out of range
        'var arr = [1, 2];\narr["a"] = 1;\nsend hz 1 dur 1t;',  # string index into a list
        "var config = json {a: 1};\nconfig[0] = 1;\nsend hz 1 dur 1t;",  # number key into an object
        "var x = 5;\nx.y = 1;\nsend hz 1 dur 1t;",  # not an object at all
    ],
)
def test_var_index_assign_type_errors(src):
    with pytest.raises(ScriptError):
        run_all(src)


def test_bracket_assign_on_a_message_field_is_rejected_at_parse_time():
    # message fields are addressed by name, never by index - only a var
    # holding an array/object supports [...] assignment.
    with pytest.raises(ScriptError):
        compile_script("header[0] = 5;")


def test_msg_prefixed_field_assignment_still_works():
    # unaffected by the var-index rewrite of _parse_path_stmt - "msg" is
    # never a declared var (it's reserved), so this stays a plain
    # message-field write, exactly as before.
    results, _ = run_all("msg.angular = 5;\nsend hz 1 dur 1t;")
    assert results[0].value == {"angular": 5.0}


# -- func macros ----------------------------------------------------------

def test_macro_ramp_pattern_end_to_end():
    src = """
    func ramp(field, from, to, duration) {
        field = live { return linear(_t, from, to, duration); };
    }
    ramp(linear.x, 0, 1, 3s);
    send hz 10 dur 3s;
    ramp(linear.x, 1, 0, 3s);
    send hz 10 dur 3s;
    """
    results, halted = run_all(src)
    assert halted
    assert len(results) == 60
    assert results[0].value == {"linear": {"x": 0.0}}
    assert results[29].value["linear"]["x"] == pytest.approx(1.0, abs=0.05)
    assert results[30].value["linear"]["x"] == pytest.approx(1.0, abs=0.05)
    assert results[-1].value["linear"]["x"] == pytest.approx(0.0, abs=0.05)


def test_macro_called_twice_does_not_collide_hygienically():
    src = """
    func bump(field, amount) {
        var step = amount;
        field = step;
    }
    bump(a, 5);
    bump(b, 10);
    send hz 1 dur 1t;
    """
    results, _ = run_all(src)
    assert results[0].value == {"a": 5, "b": 10}


def test_macro_call_inside_repeat_runs_once_per_lap():
    src = """
    func flash(field) {
        field = 1;
        send hz 10 dur 1t;
        field = 0;
        send hz 10 dur 1t;
    }
    repeat 2 {
        flash(data);
    }
    """
    results, halted = run_all(src)
    assert halted
    assert [r.value["data"] for r in results] == [1, 0, 1, 0]


def test_macro_calling_another_macro_works_end_to_end():
    src = """
    func inner(field, value) {
        field = value;
    }
    func outer(field, value) {
        inner(field, value);
    }
    outer(data, 42);
    send hz 1 dur 1t;
    """
    results, _ = run_all(src)
    assert results[0].value == {"data": 42}


def test_infinite_loop_with_no_send_or_wait_raises_instead_of_hanging():
    # `_check_no_mid_body_infinite_send` (compiler.py) only catches a
    # `send dur inf;` made unreachable - a loop with no send/wait at all
    # compiles cleanly and, without this guard, would spin step()'s
    # `while True:` forever.
    run = compile_script("var x = 0;\nrepeat {\n x = x + 1;\n}").new_run(step_instruction_budget=1_000)
    with pytest.raises(ScriptError, match="step_instruction_budget"):
        run.step()


def test_step_instruction_budget_can_be_raised_for_a_legitimate_long_loop():
    src = "var x = 0;\nrepeat 5000 {\n x = x + 1;\n}\ndata = x;\nsend hz 1 dur 1t;"
    run = compile_script(src).new_run(step_instruction_budget=30_000)
    result = run.step()
    assert result.value["data"] == 5000.0
