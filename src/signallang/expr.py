from __future__ import annotations

import math
import random
import re
from contextvars import ContextVar

from .errors import ScriptError
from .resources import DEFAULT_OPERATION_BUDGET, Budget, clone_value

_rng = ContextVar("signallang_rng", default=random)
_budget: ContextVar[Budget | None] = ContextVar("signallang_budget", default=None)


def _spend(amount=1):
    budget = _budget.get()
    if budget is not None:
        budget.spend(amount)


from typing import TYPE_CHECKING, Callable, Union

if TYPE_CHECKING:
    # typing_extensions is a transitive dependency of mypy itself, so
    # this is always available wherever mypy actually runs (CI's `dev`
    # extra) - guarded under TYPE_CHECKING so it's never an actual
    # runtime import, keeping the package's real dependency list empty.
    from typing_extensions import TypeGuard


Value = Union[int, float, bool, str, list, dict]


def is_truthy(value: Value) -> bool:
    """The one truthiness rule for and/or/not, terop's cond, and if/repeat
    conditions: plain Python truthiness (0.0/False/""/[]/{} are falsy,
    anything else truthy), made explicit rather than left to a bare
    `value != 0.0` comparison, which is wrong for a non-float value - a
    str is never == or != a float, so that check would call every string
    truthy, empty or not, and the same problem would recur for every
    other value type."""
    return bool(value)


def _terop(cond: Value, then: Value, otherwise: Value) -> Value:
    return then if is_truthy(cond) else otherwise


def _floordiv(a: float, b: float) -> int:
    """a // b as a plain function, not an operator: `//` is already the
    language's comment marker (stripped before any expression is even
    parsed - see parser.py's _strip_comments), so it can't double as a
    division operator without silently eating the rest of the line.
    Always returns a genuine Int, regardless of whether a/b were Int or
    Float - the whole point is a guaranteed whole-number result."""
    return int(a // b)


def _linear(t: float, a: float, b: float, dur: float) -> float:
    """Ramp from a to b over dur seconds of elapsed time t, then hold at
    b. A plain, pure function - the "elapsed time" it ramps against is
    just its first argument, same as any other function; nothing about it
    is implicitly wired to the VM's own clock. `field = linear!(a, b,
    dur);` is parser sugar that passes `_t` as this argument for you."""
    if dur <= 0:
        raise ValueError(f"linear(): dur must be greater than 0, got {dur}")
    return a + (b - a) * min(1.0, t / dur)


def _square(t: float, low: float, high: float, period: float) -> float:
    """A 50% duty cycle square wave: low for the first half of each
    period, high for the second half."""
    if period <= 0:
        raise ValueError(f"square(): period must be greater than 0, got {period}")
    return high if (t % period) >= period / 2.0 else low


def _triangle(t: float, low: float, high: float, period: float) -> float:
    """Ramps low -> high over the first half of each period, high -> low
    over the second half."""
    if period <= 0:
        raise ValueError(f"triangle(): period must be greater than 0, got {period}")
    half = period / 2.0
    phase = t % period
    if phase < half:
        return low + (high - low) * (phase / half)
    return high - (high - low) * ((phase - half) / half)


def _sawtooth(t: float, low: float, high: float, period: float) -> float:
    """Ramps low -> high over the whole period, then snaps back to low."""
    if period <= 0:
        raise ValueError(f"sawtooth(): period must be greater than 0, got {period}")
    return low + (high - low) * ((t % period) / period)


def _damped_wave(t: float, amplitude: float, decay: float, period: float) -> float:
    """A decaying sinusoid - the natural (unforced) response shape of an
    underdamped 2nd-order system like an RLC circuit: amplitude * e^(-decay
    * t) * sin(2*pi*t / period). `decay` is the damping rate in 1/s;
    `period` is the oscillation period in seconds."""
    if period <= 0:
        raise ValueError(f"damped_wave(): period must be greater than 0, got {period}")
    return amplitude * math.exp(-decay * t) * math.sin(2.0 * math.pi * t / period)


def _sinusoidal_wave(t: float, amplitude: float, period: float) -> float:
    """A plain sinusoid, centered at 0: amplitude * sin(2*pi*t / period).
    Equivalent to damped_wave with decay = 0."""
    if period <= 0:
        raise ValueError(f"sinusoidal_wave(): period must be greater than 0, got {period}")
    return amplitude * math.sin(2.0 * math.pi * t / period)


def _pulse(t: float, low: float, high: float, period: float, duty: float) -> float:
    """A generalized square wave: high for the first `duty` fraction of
    each period, low for the rest. `square` is the fixed-50%-duty case;
    this is that same shape with the split exposed as a parameter."""
    if period <= 0:
        raise ValueError(f"pulse(): period must be greater than 0, got {period}")
    if not (0.0 <= duty <= 1.0):
        raise ValueError(f"pulse(): duty must be between 0 and 1, got {duty}")
    return high if (t % period) < period * duty else low


def _exponential(t: float, initial: float, rate: float) -> float:
    """Plain exponential growth (rate > 0) or decay (rate < 0):
    initial * e^(rate * t). Monotonic and unbounded, unlike damped_wave
    (which oscillates while decaying) or linear (which ramps to a fixed
    target and holds)."""
    return initial * math.exp(rate * t)


def _polynomial(t: float, *coefficients: float) -> float:
    """a0 + a1*t + a2*t^2 + ... for however many coefficients are given,
    evaluated by Horner's method. No coefficients at all evaluates to 0."""
    result = 0.0
    for c in reversed(coefficients):
        result = result * t + c
    return result


def _noise(mean: float, stddev: float) -> float:
    """A single Gaussian-distributed random draw - call it inside a live
    context (`noise!(mean, stddev)`) for fresh jitter every tick, or bare
    for a one-shot random value frozen at assignment time, exactly like
    any other function."""
    if stddev < 0:
        raise ValueError("noise(): stddev must be non-negative")
    return _rng.get().gauss(mean, stddev)


def _uniform(low: float, high: float) -> float:
    """A single draw, uniformly distributed over [low, high] - the
    `random()` builtin is the fixed [0, 1] case of this."""
    if low > high:
        raise ValueError("uniform(): low must not exceed high")
    return _rng.get().uniform(low, high)


def _discrete_uniform(low: float, high: float) -> int:
    """A single draw, uniform over the whole numbers in [low, high]
    inclusive (not the continuum in between, unlike uniform) - both
    bounds must themselves be whole numbers (an Int literal already is
    one trivially; a whole-number Float also passes). Returns a genuine
    Int, not a Float that happens to be whole - a count, not a
    measurement. The building block for a fixed-step-size random walk:
    discrete_uniform(-1, 1) draws a fresh -1, 0, or 1 each call."""
    if low != int(low) or high != int(high):
        raise ValueError(f"discrete_uniform(): low and high must be whole numbers, got {low}, {high}")
    if low > high:
        raise ValueError(f"discrete_uniform(): low must not be greater than high, got {low}, {high}")
    return _rng.get().randint(int(low), int(high))


def _poisson(lam: float) -> int:
    """Exact additive Poisson sampling in bounded chunks, avoiding underflow."""
    if not 0 < lam <= 100_000:
        raise ValueError("poisson(): rate must be in (0, 100000]")
    total = 0
    while lam > 0:
        chunk = min(lam, 20.0)
        threshold = math.exp(-chunk)
        product = 1.0
        count = 0
        while product > threshold:
            _spend()
            product *= _rng.get().random()
            count += 1
        total += count - 1
        lam -= chunk
    return total


def _binomial(n: float, p: float) -> int:
    if n < 0 or n != int(n) or n > 1_000_000:
        raise ValueError("binomial(): n must be a whole number in [0, 1000000]")
    if not 0 <= p <= 1:
        raise ValueError("binomial(): p must be between 0 and 1")
    if p == 0 or n == 0:
        return 0
    if p == 1:
        return int(n)
    _spend(int(n))
    return sum(_rng.get().random() < p for _ in range(int(n)))


def _keyframes(t, points, mode="linear", repeat=False):
    """Interpolate ordered [seconds, numeric value] points; clamp or repeat."""
    if not is_number(t) or type(points) is not list or not points:
        raise ValueError("keyframes(): needs time and a nonempty array of [time, value] points")
    if mode not in ("linear", "hold") or type(repeat) is not bool:
        raise ValueError("keyframes(): mode must be linear/hold and repeat must be Bool")
    previous = None
    for point in points:
        _spend()
        if type(point) is not list or len(point) != 2 or not all(is_number(x) for x in point):
            raise ValueError("keyframes(): each point must contain two numbers")
        if previous is not None and point[0] <= previous:
            raise ValueError("keyframes(): point times must strictly increase")
        previous = point[0]
    first, last = points[0], points[-1]
    if repeat:
        if last[0] <= first[0]:
            raise ValueError("keyframes(): repeating needs at least two distinct times")
        t = first[0] + (t - first[0]) % (last[0] - first[0])
    if t <= first[0]:
        return first[1]
    if t >= last[0]:
        return last[1]
    for left, right in zip(points, points[1:]):
        _spend()
        if t < right[0]:
            if mode == "hold":
                return left[1]
            return left[1] + (right[1] - left[1]) * (t - left[0]) / (right[0] - left[0])
    return last[1]


# The fixed set of "time-shaped" builtins - the only ones whose bang-call
# sugar (name!(args)) also injects _t as a leading argument, matching the
# ergonomic call shape they'd otherwise lose (linear!(20, 30, 10s), not
# linear!(_t, 20, 30, 10s)). Every other function's bang call wraps its
# arguments exactly as written - see parser.py's _try_parse_bang_call.
TIME_SHAPED_FUNCTIONS = frozenset(
    {
        "linear",
        "square",
        "triangle",
        "sawtooth",
        "damped_wave",
        "sinusoidal_wave",
        "pulse",
        "exponential",
        "polynomial",
        "keyframes",
    }
)
_FUNCTIONS: dict[str, Callable[..., Value]] = {
    "sin": math.sin,
    "cos": math.cos,
    "abs": abs,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "min": min,
    "max": max,
    "floordiv": _floordiv,
    "random": lambda: _rng.get().random(),
    "noise": _noise,
    "uniform": _uniform,
    "discrete_uniform": _discrete_uniform,
    "poisson": _poisson,
    "binomial": _binomial,
    "linear": _linear,
    "square": _square,
    "triangle": _triangle,
    "sawtooth": _sawtooth,
    "damped_wave": _damped_wave,
    "sinusoidal_wave": _sinusoidal_wave,
    "pulse": _pulse,
    "exponential": _exponential,
    "polynomial": _polynomial,
    "keyframes": _keyframes,
    # terop remains eager; and/or are short-circuit AST operators.
    "terop": _terop,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "true": True, "false": False}
# Every built-in function and constant name - reserved so a var or
# for-loop variable can never shadow one. Without this, `var linear = 5;`
# would silently work, but then `linear(...)`/`linear!(...)` inside the
# same scope would be genuinely ambiguous between the var and the
# builtin (function-call syntax happens to win via the `(` check in
# _atom(), but a bare `data = linear;` would resolve to the var instead -
# confusing either way, so the name is blocked outright rather than
# relying on a syntax-position tiebreak to paper over it).
RESERVED_NAMES = frozenset(_FUNCTIONS) | frozenset(_CONSTANTS)
# Every time-like quantity in this language is stored internally as a plain
# float in seconds - a duration literal (10s, 3m, 500ms) normalizes to
# seconds at parse time; the postfix .s/.m/.ms operator is the reverse view
# onto any expression already in seconds. "ms" must be checked before "s"/"m"
# (longest match first) so ".ms" isn't swallowed as ".m" leaving a stray "s".
_TIME_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0}
_TIME_UNIT_SUFFIXES = ("ms", "s", "m")
# Legacy declaration metadata for AST consumers; numeric duration arguments
# are no longer restricted by syntactic provenance.
_DURATION_DECL_RE = re.compile(r"^-?\d+(\.\d+)?(ms|s|m)$")
BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_duration_decl_text(text: str, duration_vars: frozenset) -> bool:
    """True if `text` (a var's entire right-hand side, already
    .strip()'d by the caller) is a literal-duration declaration for legacy inspection metadata."""
    text = text.strip()
    if _DURATION_DECL_RE.match(text):
        return True
    return bool(BARE_IDENT_RE.match(text)) and text in duration_vars


# Postfix result transforms - .scale(k) multiplies, .add(k)/.bias(k) add
# (two names for the same operation: "add" for plain arithmetic, "bias"
# for the DC-offset framing of a signal). Unlike .s/.m/.ms these take a
# parenthesized argument, itself an arbitrary expression.
_VALUE_METHODS = {
    "scale": lambda v, arg: v * arg,
    "add": lambda v, arg: v + arg,
    "bias": lambda v, arg: v + arg,
}
_COMPARISONS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}
# Longest-match-first so "<=" isn't parsed as "<" followed by a stray "=".
_COMPARISON_OPS = sorted(_COMPARISONS, key=len, reverse=True)


class ExprError(ScriptError):
    """A located syntax or evaluation error in a language expression."""


def type_name(value: Value) -> str:
    # bool is checked first deliberately - Python's bool is an int
    # subclass (isinstance(True, int) is True), so the int check below
    # would otherwise also match it and misreport it as "int".
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "number"


def _value_category(value: Value) -> str:
    """Like type_name, but Int and Float collapse into one "number"
    category - used only for terop()'s then/else type-agreement check,
    where the two are meant to be as interchangeable as they already
    are in arithmetic (terop(cond, 5, 5.0) is fine; terop(cond, 5,
    "x") isn't). Bool stays its own category, same reasoning as
    everywhere else it's kept separate from Int/Float."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise AssertionError(f"unreachable: unrecognized Value type {type(value)!r}")


def is_number(value: Value) -> TypeGuard[int | float]:
    """True for int or float, explicitly false for bool despite Python's
    bool being an int subclass - the one check nearly every arithmetic/
    comparison/postfix operator needs, factored out so that exclusion
    doesn't have to be repeated inline at every call site. A real
    TypeGuard (not just a bool return), so mypy actually narrows the
    checked value to int | float afterward, the same way it would for
    an inline isinstance() check."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _index_into(container: Value, index: Value) -> Value:
    """arr[i] / obj["key"] - a whole-number index into an array, or a
    string key into an object. The one place that actually reads out of
    a compound value; everything upstream just builds them."""
    if isinstance(container, list):
        if not is_number(index):
            raise ExprError(f"array index must be a number, got {type_name(index)}")
        i = int(index)
        if i != index:
            raise ExprError(f"array index must be a whole number, got {index}")
        if i < 0 or i >= len(container):
            raise ExprError(f"array index {i} out of range (length {len(container)})")
        return container[i]
    if isinstance(container, dict):
        if not isinstance(index, str):
            raise ExprError(f"object key must be a string, got {type_name(index)}")
        if index not in container:
            raise ExprError(f"object has no key {index!r}")
        return container[index]
    raise ExprError(f"cannot index into a {type_name(container)}")


def compile_expression(expression: str):
    from .expression_tree import compile_expression as compile_tree

    return compile_tree(expression)


def evaluate_node(node, variables, *, rng=None, budget=None):
    from .expression_tree import execute

    active_budget = budget if budget is not None else Budget()
    rng_token = _rng.set(rng if rng is not None else random)
    budget_token = _budget.set(active_budget)
    try:
        return execute(node, variables, active_budget)
    finally:
        _rng.reset(rng_token)
        _budget.reset(budget_token)


def evaluate(
    expression: str,
    variables: dict,
    duration_vars: frozenset = frozenset(),
    *,
    rng=None,
    operation_budget=DEFAULT_OPERATION_BUDGET,
) -> Value:
    """Evaluate a restricted expression. Durations compose as numeric seconds.

    duration_vars is accepted for source compatibility but no longer constrains
    numeric arguments. The AST cache never retains evaluation state.
    """
    from .resources import positive_integer

    budget = Budget(positive_integer(operation_budget, "operation_budget"))
    scope = {name: clone_value(value, budget) for name, value in variables.items()}
    return evaluate_node(compile_expression(expression), scope, rng=rng, budget=budget)


def parse_one(text: str, variables: dict) -> tuple[Value, int]:
    from .expression_tree import Parser

    parser = Parser(text)
    try:
        node = parser.expression()
        return evaluate_node(node, variables), parser.pos
    except RecursionError:
        raise ExprError("expression exceeds maximum nesting depth", parser.pos) from None
