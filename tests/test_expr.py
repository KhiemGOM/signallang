import math

import pytest

from signallang import ExprError, evaluate


# -- correctness -------------------------------------------------------

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("2 + 3 * 4", {}, 14.0),
        ("(2 + 3) * 4", {}, 20.0),
        ("0.1 * t", {"t": 5.0}, 0.5),
        ("2 * sin(0)", {}, 0.0),
        ("min(10, 0.5*t)", {"t": 100.0}, 10.0),
        ("max(1, 2, 3)", {}, 3.0),
        ("-5 + 2", {}, -3.0),
        ("10 % 3", {}, 1.0),
        ("i * 2", {"i": 7}, 14.0),
        ("pi", {}, math.pi),
        ("true", {}, 1.0),
        ("false", {}, 0.0),
    ],
)
def test_arithmetic(expr, variables, expected):
    assert evaluate(expr, variables) == pytest.approx(expected)


# -- comparisons ---------------------------------------------------------

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("t < 4.5", {"t": 2.0}, 1.0),
        ("t < 4.5", {"t": 6.0}, 0.0),
        ("t + 1 < 5", {"t": 2.0}, 1.0),
        ("t + 1 < 5", {"t": 10.0}, 0.0),
        ("t == 5", {"t": 5.0}, 1.0),
        ("t != 5", {"t": 5.0}, 0.0),
        ("t >= 5", {"t": 5.0}, 1.0),
    ],
)
def test_comparisons(expr, variables, expected):
    assert evaluate(expr, variables) == pytest.approx(expected)


def test_chained_comparison_rejected():
    with pytest.raises(ExprError):
        evaluate("1 < 2 < 3", {})


# -- boolean operators ----------------------------------------------------

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("t > 1 and t < 5", {"t": 3.0}, 1.0),
        ("t > 1 and t < 5", {"t": 10.0}, 0.0),
        ("t < 1 or t > 5", {"t": 10.0}, 1.0),
        ("t < 1 or t > 5", {"t": 3.0}, 0.0),
        ("not (t < 5)", {"t": 10.0}, 1.0),
        ("not (t < 5)", {"t": 1.0}, 0.0),
        # precedence: `a or b and c` reads as `a or (b and c)`
        ("false or true and false", {}, 0.0),
        ("false or (true and true)", {}, 1.0),
        # a variable that merely starts with a reserved word isn't
        # mistaken for the keyword (word-boundary check).
        ("android", {"android": 5.0}, 5.0),
    ],
)
def test_boolean_operators(expr, variables, expected):
    assert evaluate(expr, variables) == pytest.approx(expected)


# -- terop -----------------------------------------------------------------

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("terop(t < 4.5, 1, 0)", {"t": 2.0}, 1.0),
        ("terop(t < 4.5, 1, 0)", {"t": 6.0}, 0.0),
        ("terop(t < 2, sin(t), 0)", {"t": 1.0}, math.sin(1.0)),
        # nesting
        ("terop(t < 2, 1, terop(t < 4, 2, 3))", {"t": 5.0}, 3.0),
        ("terop(t < 2, 1, terop(t < 4, 2, 3))", {"t": 3.0}, 2.0),
        ("terop(t < 2, 1, terop(t < 4, 2, 3))", {"t": 1.0}, 1.0),
    ],
)
def test_terop(expr, variables, expected):
    assert evaluate(expr, variables) == pytest.approx(expected)


def test_terop_dead_branch_still_raises():
    # both branches evaluate eagerly - a bad not-taken branch still errors.
    with pytest.raises(ExprError):
        evaluate("terop(1 == 1, 1, 1/0)", {})


# -- signal-shape builtins --------------------------------------------------
# Plain, pure functions of an explicit elapsed-time argument - no magic,
# same as sin/cos. `name!(args)` sugar (parser.py) is what injects _t as
# that argument automatically for the fixed TIME_SHAPED_FUNCTIONS set.

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("linear(0, 20, 30, 10)", {}, 20.0),
        ("linear(5, 20, 30, 10)", {}, 25.0),
        ("linear(10, 20, 30, 10)", {}, 30.0),
        ("linear(15, 20, 30, 10)", {}, 30.0),  # clamps past dur, holds at b
        ("square(0, 0, 1, 2)", {}, 0.0),
        ("square(1.5, 0, 1, 2)", {}, 1.0),
        ("square(2, 0, 1, 2)", {}, 0.0),  # wraps to the next period
        ("triangle(0, 0, 10, 4)", {}, 0.0),
        ("triangle(2, 0, 10, 4)", {}, 10.0),  # peak at the half-period
        ("triangle(4, 0, 10, 4)", {}, 0.0),  # back to 0 at a full period
        ("sawtooth(0, 0, 10, 4)", {}, 0.0),
        ("sawtooth(2, 0, 10, 4)", {}, 5.0),
        ("damped_wave(0, 5, 0, 1)", {}, 0.0),  # sin(0) == 0 regardless of decay
        ("sinusoidal_wave(0, 5, 1)", {}, 0.0),  # sin(0) == 0
        ("sinusoidal_wave(0.25, 5, 1)", {}, 5.0),  # quarter period -> peak
        ("sinusoidal_wave(1, 5, 1)", {}, 0.0),  # full period -> back to 0
        ("pulse(0, 0, 1, 2, 0.25)", {}, 1.0),  # inside the high window
        ("pulse(0.5, 0, 1, 2, 0.25)", {}, 0.0),  # just past it
        ("pulse(2, 0, 1, 2, 0.25)", {}, 1.0),  # wraps to the next period
        ("exponential(0, 2, 1)", {}, 2.0),  # t=0 -> just the initial value
        ("exponential(1, 1, 0)", {}, 1.0),  # rate=0 -> constant
        ("polynomial(3, 1, 2, 3)", {}, 34.0),  # 1 + 2*3 + 3*9 == 34
        ("polynomial(5)", {}, 0.0),  # no coefficients -> 0
    ],
)
def test_signal_shape_builtins(expr, variables, expected):
    assert evaluate(expr, variables) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        "linear(0, 0, 1, 0)",  # dur must be > 0
        "linear(0, 0, 1, -1)",
        "square(0, 0, 1, 0)",  # period must be > 0
        "triangle(0, 0, 1, 0)",
        "sawtooth(0, 0, 1, 0)",
        "damped_wave(0, 1, 0, 0)",
        "sinusoidal_wave(0, 1, 0)",
        "pulse(0, 0, 1, 0, 0.5)",  # period must be > 0
        "pulse(0, 0, 1, 2, -0.1)",  # duty must be in [0, 1]
        "pulse(0, 0, 1, 2, 1.1)",
    ],
)
def test_signal_shape_builtins_reject_non_positive_duration(expr):
    with pytest.raises(ExprError):
        evaluate(expr, {})


def test_noise_returns_a_float():
    # not a determinism test (random.gauss is genuinely random) - just
    # confirms it's wired up and returns a plain float, not a crash.
    for _ in range(20):
        assert isinstance(evaluate("noise(0, 1)", {}), float)


def test_uniform_stays_within_bounds():
    for _ in range(200):
        v = evaluate("uniform(2, 5)", {})
        assert isinstance(v, float)
        assert 2.0 <= v <= 5.0


def test_discrete_uniform_stays_within_bounds_and_is_always_whole():
    for _ in range(200):
        v = evaluate("discrete_uniform(-1, 1)", {})
        assert isinstance(v, float)
        assert v in (-1.0, 0.0, 1.0)


def test_discrete_uniform_single_value_range_is_always_that_value():
    for _ in range(20):
        assert evaluate("discrete_uniform(4, 4)", {}) == 4.0


@pytest.mark.parametrize(
    "expr",
    [
        "discrete_uniform(0.5, 2)",  # low must be a whole number
        "discrete_uniform(0, 2.5)",  # high must be a whole number
        "discrete_uniform(3, 1)",  # low must not be greater than high
    ],
)
def test_discrete_uniform_rejects_invalid_arguments(expr):
    with pytest.raises(ExprError):
        evaluate(expr, {})


def test_poisson_returns_a_non_negative_whole_number_as_a_float():
    for _ in range(200):
        v = evaluate("poisson(3)", {})
        assert isinstance(v, float)
        assert v >= 0.0
        assert v == int(v)


def test_poisson_rejects_non_positive_lam():
    with pytest.raises(ExprError):
        evaluate("poisson(0)", {})
    with pytest.raises(ExprError):
        evaluate("poisson(-1)", {})


def test_binomial_stays_within_n_trials():
    for _ in range(200):
        v = evaluate("binomial(10, 0.5)", {})
        assert isinstance(v, float)
        assert 0.0 <= v <= 10.0
        assert v == int(v)


def test_binomial_zero_probability_is_always_zero():
    assert evaluate("binomial(10, 0)", {}) == 0.0


def test_binomial_certain_probability_is_always_n():
    assert evaluate("binomial(10, 1)", {}) == 10.0


@pytest.mark.parametrize(
    "expr",
    [
        "binomial(-1, 0.5)",  # n must be non-negative
        "binomial(2.5, 0.5)",  # n must be a whole number
        "binomial(10, -0.1)",  # p must be in [0, 1]
        "binomial(10, 1.1)",
    ],
)
def test_binomial_rejects_invalid_arguments(expr):
    with pytest.raises(ExprError):
        evaluate(expr, {})


def test_bang_syntax_is_a_parse_time_construct_not_an_expr_operator():
    # '!' is parser.py sugar (name!(args) as the whole assignment RHS) -
    # expr.py itself has no idea what '!' means outside of '!=', so a
    # bang call that leaks into an actual expr.py string (e.g. nested
    # inside a larger expression, which the parser doesn't recognize as
    # bang sugar) is a plain syntax error here, not silently live.
    with pytest.raises(ExprError):
        evaluate("1 + sin!(t)", {"t": 1.0})


# -- postfix result transforms: .scale/.add/.bias ---------------------------

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("(5).scale(2)", {}, 10.0),
        ("(5).add(2)", {}, 7.0),
        ("(5).bias(2)", {}, 7.0),  # .bias is an alias for .add
        ("sin(0).add(1)", {}, 1.0),
        ("(5).scale(2).add(3)", {}, 13.0),  # chained left to right
        ("(5).add(3).scale(2)", {}, 16.0),  # order matters
        ("t.s.scale(2)", {"t": 5.0}, 10.0),  # chains with the .s time-unit view
    ],
)
def test_value_method_postfix(expr, variables, expected):
    assert evaluate(expr, variables) == pytest.approx(expected)


def test_value_methods_chain_freely_with_time_unit_postfix():
    # .s/.m/.ms and .scale/.add/.bias chain in any order and any count -
    # _postfix() loops instead of matching a single suffix.
    assert evaluate("(120).m", {}) == pytest.approx(2.0)  # 120s -> 2 minutes
    assert evaluate("(120).m.scale(3)", {}) == pytest.approx(6.0)
    assert evaluate("(1).m.add(0.5)", {}) == pytest.approx(1 / 60 + 0.5)


@pytest.mark.parametrize(
    "expr",
    [
        '"a".scale(2)',
        '"a".add(1)',
        '(5).scale("a")',
        '(5).add("a")',
    ],
)
def test_value_method_rejects_string_operands(expr):
    with pytest.raises(ExprError):
        evaluate(expr, {})


# -- .shift(offset): rewrites a call's first argument before calling -------

@pytest.mark.parametrize(
    "expr, expected",
    [
        ("square(5, 0, 100, 10).shift(1)", 0.0),  # == square(4, 0, 100, 10)
        ("square(4, 0, 100, 10)", 0.0),
        ("square(0, 0, 1, 2).shift(-1)", 1.0),  # negative shift == square(1, ...)
        ("square(1, 0, 1, 2)", 1.0),
        ("square(0, 0, 1, 2).shift(1)", 1.0),  # negative effective t wraps via %
    ],
)
def test_shift_rewrites_first_argument(expr, expected):
    assert evaluate(expr, {}) == pytest.approx(expected)


def test_shift_chains_with_value_methods():
    # shift happens before the call; scale/add apply to the call's result.
    assert evaluate("square(5, 0, 100, 10).shift(1).scale(2).add(3)", {}) == pytest.approx(3.0)


@pytest.mark.parametrize(
    "expr",
    [
        "square(5, 0, 100, 10).shift(1).scale(2)",
        "square(5, 0, 100, 10).scale(2).shift(1)",  # shift written AFTER scale
        "square(5, 0, 100, 10).add(0).shift(1).scale(2)",  # shift sandwiched
    ],
)
def test_shift_is_order_independent_within_the_chain(expr):
    # regression: .shift(...) rewrites the CALL's argument, which must
    # happen before the call fires no matter where in the postfix chain
    # it's textually written - a naive left-to-right pass would apply
    # .scale(2) to the call's un-shifted result first, then have nothing
    # left to rewrite once it reached a trailing .shift(). All three
    # forms here must produce the same value: square(4, 0, 100, 10) * 2.
    assert evaluate(expr, {}) == pytest.approx(0.0)


def test_multiple_shifts_accumulate_regardless_of_position():
    a = evaluate("square(5, 0, 100, 10).add(1).shift(1).scale(2).shift(2).add(3)", {})
    b = evaluate("square(5, 0, 100, 10).shift(3).add(1).scale(2).add(3)", {})
    assert a == pytest.approx(b)


def test_shift_requires_at_least_one_argument():
    with pytest.raises(ExprError):
        evaluate("random().shift(1)", {})


def test_shift_requires_a_number_offset():
    with pytest.raises(ExprError):
        evaluate('square(5, 0, 100, 10).shift("a")', {})


def test_shift_only_recognized_within_a_call_postfix_chain():
    # a bare variable/atom isn't a call, so it has no call-postfix chain
    # for '.shift' to participate in - just unconsumed trailing input.
    with pytest.raises(ExprError):
        evaluate("t.shift(1)", {"t": 5.0})


# -- arrays and objects ------------------------------------------------------

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("[1, 2, 3]", {}, [1.0, 2.0, 3.0]),
        ("[]", {}, []),
        ("[1, 2, 3][0]", {}, 1.0),
        ("[1, 2, 3][2]", {}, 3.0),
        ("[[1, 2], [3, 4]][1][0]", {}, 3.0),
        ('json { a: 1, b: 2 }["a"]', {}, 1.0),
        ("json { a: 1, b: 2 }.b", {}, 2.0),
        ('json { "x": 5, y: 6 }.x', {}, 5.0),  # quoted and bare keys both work
        ('json { "x": 5, y: 6 }["y"]', {}, 6.0),
        ('json { header: json { frame_id: "map" }, pts: [1, 2] }.header.frame_id', {}, "map"),
        ('json { header: json { frame_id: "map" }, pts: [1, 2] }.pts[1]', {}, 2.0),
        ("[i, i + 1, i + 2]", {"i": 5.0}, [5.0, 6.0, 7.0]),
        ("json {}", {}, {}),
    ],
)
def test_array_and_object_literals(expr, variables, expected):
    assert evaluate(expr, variables) == expected


def test_terop_can_select_between_two_objects():
    assert evaluate("terop(true, json {a: 1}, json {b: 2}).a", {}) == 1.0
    assert evaluate("terop(false, json {a: 1}, json {b: 2}).b", {}) == 2.0


@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ("terop([], 1, 0)", {}, 0.0),  # empty array is falsy
        ("terop([1], 1, 0)", {}, 1.0),
        ("terop(json {}, 1, 0)", {}, 0.0),  # empty object is falsy
        ("terop(json {a: 1}, 1, 0)", {}, 1.0),
    ],
)
def test_array_and_object_truthiness(expr, variables, expected):
    assert evaluate(expr, variables) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "[1, 2, 3][5]",  # out of range
        "[1, 2, 3][-1]",  # negative index rejected, not Python-style wraparound
        '[1, 2, 3]["a"]',  # array index must be a number
        "[1, 2, 3][1.5]",  # array index must be a whole number
        'json {a: 1}["z"]',  # missing key
        "json {a: 1}[1]",  # object key must be a string
        "(5)[0]",  # cannot index into a number
        '"x"[0]',  # cannot index into a string
        "[1, 2] + [3, 4]",  # arithmetic rejected on arrays
        "[1, 2] - [3, 4]",
        "-([1, 2])",
        "json {a: 1}.b",  # dot-access on a missing key
        "[1, 2].s",  # time-unit view rejected on an array
    ],
)
def test_array_and_object_type_errors(expr):
    with pytest.raises(ExprError):
        evaluate(expr, {})


def test_dot_access_then_numeric_postfix_composes():
    # once .a resolves to a plain number, the usual numeric postfixes
    # apply to it normally - only the *object* itself rejects them.
    assert evaluate("json {a: 1}.a.scale(2)", {}) == 2.0


def test_equality_works_structurally_on_arrays_and_objects():
    # Python's own == already does deep structural comparison for free -
    # no extra code needed in _COMPARISONS for this.
    assert evaluate("[1, 2] == [1, 2]", {}) == 1.0
    assert evaluate("[1, 2] == [1, 3]", {}) == 0.0
    assert evaluate("json {a: 1} == json {a: 1}", {}) == 1.0


def test_var_can_hold_and_be_indexed_as_a_compound_value():
    # exercises the variable-lookup path specifically (not just a fresh
    # literal), since that's where the int/list/dict coercion logic lives.
    assert evaluate("arr[1]", {"arr": [10.0, 20.0, 30.0]}) == 20.0
    assert evaluate('obj["k"]', {"obj": {"k": 42.0}}) == 42.0


# -- strings ---------------------------------------------------------------

@pytest.mark.parametrize(
    "expr, variables, expected",
    [
        ('"map"', {}, "map"),
        ('"a" + "b"', {}, "ab"),
        ('"prefix_" + suffix', {"suffix": "odom"}, "prefix_odom"),
        ('frame + "_link"', {"frame": "base"}, "base_link"),
        ('"a" == "a"', {}, 1.0),
        ('"a" == "b"', {}, 0.0),
        ('"a" != "b"', {}, 1.0),
        ('"a" < "b"', {}, 1.0),
        ('"b" <= "b"', {}, 1.0),
        ('"b" > "a"', {}, 1.0),
        # mismatched types are never equal - no error, just false/true,
        # same as Python's own `1 == "1"`.
        ('"5" == 5', {}, 0.0),
        ('"5" != 5', {}, 1.0),
        ('"" and true', {}, 0.0),  # empty string is falsy
        ('"x" and true', {}, 1.0),  # non-empty string is truthy
        ('not ""', {}, 1.0),
        ('terop(status == "ok", 1, 0)', {"status": "ok"}, 1.0),
        ('terop(true, "a", "b")', {}, "a"),
        ('min("b", "a")', {}, "a"),
        ('max("b", "a")', {}, "b"),
    ],
)
def test_string_values(expr, variables, expected):
    assert evaluate(expr, variables) == expected


@pytest.mark.parametrize(
    "expr, variables",
    [
        ('"a" - "b"', {}),
        ('"a" * "b"', {}),
        ('"a" / "b"', {}),
        ('"a" % "b"', {}),
        ('-("a")', {}),
        ('"a" + 1', {}),
        ('1 + "a"', {}),
        ('"a" < 1', {}),
        ('1 < "a"', {}),
        ('"a".s', {}),
        ('sin("a")', {}),
        ('min("a", 1)', {}),
        ('"unterminated', {}),
    ],
)
def test_string_type_errors(expr, variables):
    with pytest.raises(ExprError):
        evaluate(expr, variables)


# -- safety: never actually executes anything ------------------------------

@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "1; 2",
        "t.__class__",
        "exec('pass')",
        "1/0",
        "unknown_var + 1",
        "sin(",
        "unknown_function(1)",
    ],
)
def test_rejects_everything_outside_the_grammar(expr):
    with pytest.raises(ExprError):
        evaluate(expr, {"t": 1.0, "i": 1.0})
