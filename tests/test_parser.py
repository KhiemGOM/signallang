import pytest

from signallang.ast_nodes import (
    Assign,
    ExprSpan,
    For,
    If,
    LiveBlock,
    Repeat,
    Send,
    StaticDecl,
    StaticReassign,
    VarDecl,
)
from signallang.errors import ScriptError
from signallang.parser import parse


def test_static_assignment():
    prog = parse("data = 1 + 2;")
    assert prog.body == [Assign(path=["data"], value=ExprSpan("1 + 2"))]


def test_nested_field_path():
    prog = parse("linear.x = 5;")
    assert prog.body[0].path == ["linear", "x"]


def test_msg_prefix_stripped():
    prog = parse("msg.linear.x = 5;")
    assert prog.body[0].path == ["linear", "x"]


def test_var_decl_and_reassign():
    prog = parse("var x = 5;\nx = x + 1;")
    assert isinstance(prog.body[0], VarDecl)
    assert prog.body[1].name == "x"


def test_var_index_assign_ast_shape():
    from signallang.ast_nodes import VarIndexAssign

    prog = parse('var config = json { a: 1 };\nconfig.a[0] = 5;')
    node = prog.body[1]
    assert isinstance(node, VarIndexAssign)
    assert node.name == "config"
    assert node.accessors[0] == ("dot", "a")
    assert node.accessors[1][0] == "index"
    assert node.accessors[1][1].text == "0"
    assert node.value.text == "5"


def test_reassign_without_prior_var_is_a_field_assign():
    prog = parse("data = 1;")
    assert isinstance(prog.body[0], Assign)


def test_string_literal():
    # a bare string literal is just an ExprSpan now - expr.py evaluates
    # the quoted text like any other atom (see test_expr.py for the
    # string-value semantics themselves: concatenation, comparison, etc.)
    prog = parse('frame_id = "map";')
    assert prog.body[0].value == ExprSpan('"map"')


def test_string_concatenation_is_a_plain_expr_span():
    prog = parse('frame_id = "prefix_" + suffix;')
    assert prog.body[0].value == ExprSpan('"prefix_" + suffix')


def test_if_else():
    prog = parse("if t < 1 {\n data = 1;\n} else {\n data = 2;\n}")
    node = prog.body[0]
    assert isinstance(node, If)
    assert node.cond.text == "t < 1"
    assert len(node.then_body) == 1
    assert len(node.else_body) == 1


def test_else_if_chains_as_nested_if():
    prog = parse("if t < 1 {\n data=1;\n} else if t < 2 {\n data=2;\n} else {\n data=3;\n}")
    node = prog.body[0]
    assert len(node.else_body) == 1
    assert isinstance(node.else_body[0], If)


def test_repeat_forever():
    prog = parse("repeat {\n data = 1;\n send;\n}")
    assert isinstance(prog.body[0], Repeat)
    assert prog.body[0].count is None


def test_repeat_n():
    prog = parse("repeat 5 {\n data = 1;\n send;\n}")
    assert prog.body[0].count.text == "5"


def test_for_loop_range():
    prog = parse("for i in 0..7 {\n data = i;\n send;\n}")
    node = prog.body[0]
    assert isinstance(node, For)
    assert node.var == "i"
    assert node.start.text == "0"
    assert node.end.text == "7"


def test_for_loop_inf_end():
    prog = parse("for i in 0..inf {\n data = i;\n send;\n}")
    assert prog.body[0].end is None


def test_send_defaults():
    prog = parse("send;")
    node = prog.body[0]
    assert isinstance(node, Send)
    assert node.hz is None
    assert node.dur_kind == "inf"


def test_send_hz_dur_wall():
    prog = parse("send hz 10 dur 4.5s;")
    node = prog.body[0]
    assert node.hz == 10.0
    assert node.dur_kind == "wall"
    assert node.dur_value == 4.5


def test_send_dur_bare_number_means_seconds():
    prog = parse("send dur 15;")
    assert prog.body[0].dur_kind == "wall"
    assert prog.body[0].dur_value == 15.0


def test_send_dur_minutes_and_millis():
    prog = parse("send dur 2m;")
    assert prog.body[0].dur_value == 120.0
    prog2 = parse("send dur 500ms;")
    assert prog2.body[0].dur_value == 0.5


def test_send_dur_tick_count():
    prog = parse("send hz 1 dur 10t;")
    assert prog.body[0].dur_kind == "tick"
    assert prog.body[0].dur_value == 10.0


def test_send_value_sugar_bare():
    prog = parse("send 5;")
    assert prog.body[0].value == ExprSpan("5")


def test_send_value_sugar_array():
    prog = parse("send [1, 2, 3];")
    assert len(prog.body[0].value.elements) == 3


def test_send_hz_dur_and_value_are_order_independent():
    # value first, last, or wedged between hz/dur, and hz/dur swapped
    # between themselves, all parse to the same Send node.
    forms = [
        "send true hz 5 dur 4.5;",
        "send hz 5 dur 4.5 true;",
        "send hz 5 true dur 4.5;",
        "send dur 4.5 hz 5 true;",
        "send dur 4.5 true hz 5;",
    ]
    nodes = [parse(f).body[0] for f in forms]
    for n in nodes:
        assert n.hz == 5.0
        assert n.dur_kind == "wall"
        assert n.dur_value == 4.5
        assert n.value.text == "true"


def test_send_with_two_unambiguous_values_is_a_parse_error():
    # two bare expression values with nothing to disambiguate them (no
    # keyword, no closing bracket) isn't parse-time-catchable - the same
    # "raw source span" trade-off as a missing `;` - but two values with
    # their own unambiguous boundaries (arrays here) is a real, checkable
    # "only one value per send" violation.
    with pytest.raises(ScriptError):
        parse("send [1, 2] [3, 4];")


def test_send_value_first_stops_before_trailing_hz_keyword():
    # the value-first form must not swallow the trailing `hz`/`dur` text
    # into the value's own expression span.
    prog = parse("send 1 + 2 hz 5;")
    node = prog.body[0]
    assert node.value.text == "1 + 2"
    assert node.hz == 5.0


def test_default_placeholder():
    prog = parse("header = default;")
    from signallang.ast_nodes import Default

    assert prog.body[0].value == Default()


def test_default_nested_in_array():
    prog = parse("send [default, 20.0, 0.1];")
    elems = prog.body[0].value.elements
    from signallang.ast_nodes import Default

    assert elems[0] == Default()
    assert elems[1].text == "20.0"


def test_nested_array_literal():
    prog = parse("msg = [[1, 2, 3], [0, 0, 0.5]];")
    outer = prog.body[0].value
    assert len(outer.elements) == 2
    assert len(outer.elements[0].elements) == 3


def test_json_object_nested_inside_a_positional_array_element():
    # regression test for span.py's {}-depth tracking: the array-element
    # scanner (stop_chars=",]") must not mistake a json object's own
    # internal ',' for the outer array's element separator.
    prog = parse("send [json { a: 1, b: 2 }, 5];")
    elements = prog.body[0].value.elements
    assert len(elements) == 2
    assert elements[0].text == "json { a: 1, b: 2 }"
    assert elements[1].text == "5"


def test_live_block_desugars_and_reads_underscore_t():
    prog = parse("temperature = live { return 20 + _t.s; };")
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.return_expr.text == "20 + _t.s"


def test_live_block_with_locals_and_if():
    src = "x = live {\n var a = 1;\n if a > 0 {\n a = a + 1;\n }\n return a;\n};"
    prog = parse(src)
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert len(node.body) == 2


def test_live_block_cannot_write_undeclared_name():
    with pytest.raises(ScriptError):
        parse("x = live {\n outer_var = 5;\n return outer_var;\n};")


def test_live_block_static_decl_and_reassign():
    src = "x = live {\n static value = 0;\n value = value + 1;\n return value;\n};"
    prog = parse(src)
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.body == [
        StaticDecl(name="value", value=ExprSpan("0")),
        StaticReassign(name="value", value=ExprSpan("value + 1")),
    ]


def test_live_block_static_cannot_collide_with_var():
    with pytest.raises(ScriptError):
        parse("x = live {\n var a = 1;\n static a = 2;\n return a;\n};")
    with pytest.raises(ScriptError):
        parse("x = live {\n static a = 1;\n var a = 2;\n return a;\n};")


def test_live_block_static_not_allowed_nested_in_if():
    with pytest.raises(ScriptError):
        parse("x = live {\n if t > 0 {\n static a = 1;\n }\n return 1;\n};")


def test_live_block_static_scope_does_not_leak_across_blocks():
    # a static declared in one live block must not make a bare reassignment
    # to the same name legal in a later, unrelated live block.
    with pytest.raises(ScriptError):
        parse("a = live {\n static v = 0;\n return v;\n};\nb = live {\n v = 1;\n return v;\n};")


def test_bang_call_desugars_to_live_block():
    prog = parse("temperature = linear!(20, 30, 10s);")
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.return_expr.text == "linear(_t, 20, 30, 10s)"


def test_bang_call_on_non_time_shaped_function_does_not_inject_t():
    prog = parse("data = sin!(t);")
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.return_expr.text == "sin(t)"


def test_bang_call_with_no_args():
    prog = parse("data = noise!();")
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.return_expr.text == "noise()"


def test_rand_walk_bang_call_desugars_to_a_static_accumulator():
    prog = parse("walk = rand_walk!(-1, 1);")
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.body == [
        StaticDecl(name="value", value=ExprSpan("0")),
        StaticReassign(name="value", value=ExprSpan("value + discrete_uniform(-1, 1)")),
    ]
    assert node.return_expr.text == "value"


def test_brown_motion_bang_call_desugars_to_a_static_accumulator():
    prog = parse("bm = brown_motion!(0, 1);")
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.body == [
        StaticDecl(name="value", value=ExprSpan("0")),
        StaticReassign(name="value", value=ExprSpan("value + noise(0, 1)")),
    ]
    assert node.return_expr.text == "value"


def test_rand_walk_bang_call_trailing_postfix_applies_to_the_accumulator():
    prog = parse("walk = rand_walk!(-1, 1).scale(10);")
    node = prog.body[0].value
    assert node.return_expr.text == "value.scale(10)"


def test_rand_walk_and_brown_motion_are_reserved_names():
    with pytest.raises(ScriptError):
        parse("var rand_walk = 5;")
    with pytest.raises(ScriptError):
        parse("var brown_motion = 5;")


def test_random_distribution_bang_calls_do_not_inject_t():
    # none of these are time-shaped - a fresh draw every tick is already
    # what live-wrapping a random function means, no _t argument to add.
    for src, expected in [
        ("data = uniform!(0, 1);", "uniform(0, 1)"),
        ("data = poisson!(3);", "poisson(3)"),
        ("data = binomial!(10, 0.5);", "binomial(10, 0.5)"),
    ]:
        prog = parse(src)
        node = prog.body[0].value
        assert isinstance(node, LiveBlock)
        assert node.return_expr.text == expected


def test_bare_call_without_bang_is_a_plain_expr_span():
    # no '!' - an ordinary one-shot function call, not live sugar.
    prog = parse("data = sin(t);")
    assert prog.body[0].value == ExprSpan("sin(t)")


def test_bang_call_carries_trailing_postfix_chain_verbatim():
    # .shift/.scale/.add/.bias are expr.py syntax, not parser syntax - the
    # parser just carries the raw text through onto the desugared call.
    prog = parse("data = square!(0, 1, 2s).shift(1s).scale(5).add(1);")
    node = prog.body[0].value
    assert isinstance(node, LiveBlock)
    assert node.return_expr.text == "square(_t, 0, 1, 2s).shift(1s).scale(5).add(1)"


def test_bang_call_with_no_trailing_chain_is_unaffected():
    prog = parse("data = sin!(t);")
    assert prog.body[0].value.return_expr.text == "sin(t)"


def test_field_name_can_shadow_a_function_name():
    # geometry_msgs/Twist really does have a field called `linear`, distinct
    # from the linear!(...) builtin - the collision must not be an error.
    prog = parse("linear.x = 1.0;\nsend;")
    assert prog.body[0].path == ["linear", "x"]


# -- shadowing is always rejected -------------------------------------------

@pytest.mark.parametrize(
    "src",
    [
        "var i = 1;\nfor i in 0..3 {\n data = i;\n send hz 1 dur 1t;\n}",  # outer var, then for
        "for i in 0..3 {\n for i in 0..2 {\n data = i;\n send hz 1 dur 1t;\n }\n}",  # nested for, same name
        "for i in 0..3 {\n var i = 5;\n data = i;\n send hz 1 dur 1t;\n}",  # for, then inner var
        "var x = 1;\nvar x = 2;\ndata = x;\nsend hz 1 dur 1t;",  # plain redeclaration
        "data = live {\n var y = 1;\n var y = 2;\n return y;\n};\nsend hz 1 dur 1t;",  # live-local redeclaration
    ],
)
def test_shadowing_is_always_rejected(src):
    # a for-loop variable is actively mutated throughout the loop's
    # execution, not just given an initial value - reusing the name of
    # anything already in scope means both bindings share the same flat
    # runtime slot and silently corrupt each other while the loop runs.
    # Applies uniformly everywhere a new binding is introduced (var
    # declarations, for-loop variables), not just the for-loop case.
    with pytest.raises(ScriptError):
        parse(src)


@pytest.mark.parametrize(
    "src",
    [
        "var linear = 5;",
        "var sin = 5;",
        "var pi = 5;",
        "var true = 5;",
        "for sin in 0..3 {\n data = 1;\n send hz 1 dur 1t;\n}",
    ],
)
def test_var_and_for_loop_names_cannot_shadow_a_builtin_or_constant(src):
    # var linear = 5; would otherwise silently work today, but
    # linear(...)/linear!(...) in the same scope would then be genuinely
    # ambiguous between the var and the builtin - blocked outright rather
    # than relying on a syntax-position tiebreak (the '(' check in
    # _atom()) to paper over it.
    with pytest.raises(ScriptError):
        parse(src)


def test_sequential_non_overlapping_for_loops_can_reuse_a_name():
    # shadowing is about overlapping scope, not the name itself - two
    # sibling for loops that never nest are fine reusing "i".
    prog = parse("for i in 0..2 {\n data = i;\n send hz 1 dur 1t;\n}\nfor i in 0..2 {\n data = i;\n send hz 1 dur 1t;\n}")
    assert len(prog.body) == 2


def test_comments_are_stripped():
    prog = parse("data = 1; // this is fine\nsend;")
    assert prog.body[0].value.text == "1"


def test_timer_and_latching_timer_decl():
    prog = parse("var a = timer();\nvar b = latching_timer();")
    from signallang.ast_nodes import TimerDecl

    assert prog.body[0] == TimerDecl(name="a", kind="eager")
    assert prog.body[1] == TimerDecl(name="b", kind="latching")


def test_timer_reset_statement():
    prog = parse("var a = timer();\na.reset();")
    from signallang.ast_nodes import TimerReset

    assert prog.body[1] == TimerReset(name="a")


def test_unmatched_brace_is_a_parse_error():
    with pytest.raises(ScriptError):
        parse("if t < 1 {\n data = 1;\n")


def test_missing_semicolon_before_end_of_input_is_a_parse_error():
    # a missing `;` mid-source silently widens the following expression's
    # raw span rather than erroring immediately (a known trade-off of
    # keeping expression leaves as unparsed source text until eval time) -
    # but a missing `;` with nothing left in the source to (mis)absorb into
    # still fails structurally, since the span scanner runs off the end
    # looking for the terminator.
    with pytest.raises(ScriptError):
        parse("data = 1")


def test_both_if_branches_parsed_eagerly_even_if_one_never_runs():
    # a syntax error inside a branch is caught at parse time regardless of
    # whether that branch would ever execute at runtime.
    with pytest.raises(ScriptError):
        parse("if false {\n data = 1;\n} else {\n data = \n}")
