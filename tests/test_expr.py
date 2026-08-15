import math

import pytest

from patternlang import ExprError, evaluate


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
