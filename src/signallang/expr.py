from __future__ import annotations

import math
import random
import re
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
    return random.gauss(mean, stddev)


def _uniform(low: float, high: float) -> float:
    """A single draw, uniformly distributed over [low, high] - the
    `random()` builtin is the fixed [0, 1] case of this."""
    return random.uniform(low, high)


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
    return random.randint(int(low), int(high))


def _poisson(lam: float) -> int:
    """A single draw from a Poisson distribution with rate lam (the
    expected count of independent events in one interval) - Knuth's
    algorithm, pure Python, no numpy dependency. lam must be positive;
    large lam (roughly > 700) is slow by this method's own nature, not a
    guarded limit here. Returns a genuine Int - an event count."""
    if lam <= 0:
        raise ValueError(f"poisson(): lam must be greater than 0, got {lam}")
    threshold = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= threshold:
            return k - 1


def _binomial(n: float, p: float) -> int:
    """A single draw from a binomial distribution: the count of
    successes out of n independent trials, each succeeding with
    probability p. n must be a non-negative whole number. Returns a
    genuine Int - a success count."""
    if n < 0 or n != int(n):
        raise ValueError(f"binomial(): n must be a non-negative whole number, got {n}")
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"binomial(): p must be between 0 and 1, got {p}")
    return sum(1 for _ in range(int(n)) if random.random() < p)


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
    }
)
# name -> (0-indexed argument position, its parameter name) for the one
# argument each of these needs to be Duration-typed - checked against
# is_duration_text() on that argument's own source span before the call
# is made. The index is the position in the FULLY EXPANDED call expr.py
# actually parses, so it's the same whether the user wrote the call
# directly (linear(t, a, b, dur)) or via `!` bang sugar, which injects
# _t as argument 0 and shifts nothing else - dur/period always lands at
# this same index either way. exponential's `rate` and damped_wave's
# `decay` are NOT durations (they're 1/seconds rate constants), so
# neither function appears here despite being time-shaped.
_DURATION_PARAMS = {
    "linear": (3, "dur"),
    "square": (3, "period"),
    "triangle": (3, "period"),
    "sawtooth": (3, "period"),
    "damped_wave": (3, "period"),
    "sinusoidal_wave": (2, "period"),
    "pulse": (3, "period"),
}

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
    "random": random.random,
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
    # terop(cond, then, else) - a plain named function, not a `? :` symbol.
    # Both branches are evaluated eagerly (this parser evaluates as it
    # parses, it doesn't build an AST to defer either side) - a
    # division-by-zero in the branch NOT taken still raises, which reads as
    # "fix your unreached branch too" rather than a silent trap.
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
# The Duration type is compile-time-only (see README's "Int and Float"/
# Duration docs) - it never becomes its own runtime Value, so there's
# nothing to check via isinstance() the way Bool/Int/Float are. Instead
# it's recognized purely by shape, and deliberately by TWO different
# rules depending on where the check happens:
#
# - At a call-site ARGUMENT (is_duration_text): a bare number is fine,
#   unit suffix optional - `square(t, 0, 1, 5)` already means 5 seconds,
#   exactly like every duration-shaped parameter in this language
#   always has, and the literal sits right there at the call site,
#   directly reviewable. This only needs to catch something that was
#   never a duration at all (an arithmetic expression, an unrelated
#   var), not a literal that never needed a unit to begin with.
# - At a `var` DECLARATION (is_duration_decl_text): a bare number does
#   NOT mark the var Duration-typed - only an explicit unit suffix
#   does. A var's declaration might be referenced far from the call
#   site that eventually uses it, so "is this genuinely a duration"
#   needs the same explicit signal a duration literal always carries;
#   without that, every plain numeric var (`var count = 5;`) would
#   silently count as one too, and the whole point of tracking
#   Duration-typed vars - catching one that never was - would be lost.
#
# Both accept a bare reference to an already-tracked var either way.
# parser.py uses is_duration_decl_text for `var` declarations; expr.py
# uses is_duration_text to check a Duration-required call argument
# (linear's dur, square/triangle/sawtooth/damped_wave/sinusoidal_wave/
# pulse's period, .shift's offset) against the caller-supplied
# duration_vars set.
_DURATION_DECL_RE = re.compile(r"^-?\d+(\.\d+)?(ms|s|m)$")
_DURATION_ARG_RE = re.compile(r"^-?\d+(\.\d+)?(ms|s|m)?$")
BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_duration_decl_text(text: str, duration_vars: frozenset) -> bool:
    """True if `text` (a var's entire right-hand side, already
    .strip()'d by the caller) should mark that var Duration-typed."""
    text = text.strip()
    if _DURATION_DECL_RE.match(text):
        return True
    return bool(BARE_IDENT_RE.match(text)) and text in duration_vars


def is_duration_text(text: str, duration_vars: frozenset) -> bool:
    """True if `text` (a call argument's own source span, already
    .strip()'d by the caller) is acceptable where a Duration-required
    parameter expects one."""
    text = text.strip()
    if _DURATION_ARG_RE.match(text):
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


class ExprError(ValueError):
    pass


def _type_name(value: Value) -> str:
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
    """Like _type_name, but Int and Float collapse into one "number"
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
            raise ExprError(f"array index must be a number, got {_type_name(index)}")
        i = int(index)
        if i != index:
            raise ExprError(f"array index must be a whole number, got {index}")
        if i < 0 or i >= len(container):
            raise ExprError(f"array index {i} out of range (length {len(container)})")
        return container[i]
    if isinstance(container, dict):
        if not isinstance(index, str):
            raise ExprError(f"object key must be a string, got {_type_name(index)}")
        if index not in container:
            raise ExprError(f"object has no key {index!r}")
        return container[index]
    raise ExprError(f"cannot index into a {_type_name(container)}")


class _Parser:
    """Tiny recursive-descent parser/evaluator for a restricted arithmetic
    and boolean expression language - deliberately NOT eval()/exec(): the
    grammar below (numbers, + - * / %, comparisons, and/or/not, parens, a
    fixed function whitelist, and whatever variables the caller injects)
    has no way to reach outside itself - no assignment, no loops, no
    attribute/name access beyond the whitelist, nothing Python's eval()
    would give away for free.

    Precedence, loosest to tightest: or > and > not > comparison
    (< > <= >= == !=, non-chaining) > + - > * / % > unary +/- > atom.
    """

    def __init__(self, text: str, variables: dict, duration_vars: frozenset = frozenset()):
        self.text = text
        self.pos = 0
        self.variables = variables
        self.duration_vars = duration_vars

    def parse(self) -> Value:
        value = self._or()
        self._skip_ws()
        if self.pos != len(self.text):
            raise ExprError(f"unexpected trailing input at position {self.pos}: {self.text[self.pos:]!r}")
        return value

    def _skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str:
        self._skip_ws()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _looking_at_word(self, word: str) -> bool:
        self._skip_ws()
        end = self.pos + len(word)
        if self.text[self.pos : end] != word:
            return False
        # word boundary - "android" shouldn't match the "and" keyword.
        return end >= len(self.text) or not (self.text[end].isalnum() or self.text[end] == "_")

    def _or(self) -> Value:
        value = self._and()
        while self._looking_at_word("or"):
            self.pos += 2
            right = self._and()
            value = is_truthy(value) or is_truthy(right)
        return value

    def _and(self) -> Value:
        value = self._not()
        while self._looking_at_word("and"):
            self.pos += 3
            right = self._not()
            value = is_truthy(value) and is_truthy(right)
        return value

    def _not(self) -> Value:
        if self._looking_at_word("not"):
            self.pos += 3
            value = self._not()
            return not is_truthy(value)
        return self._comparison()

    def _comparison(self) -> Value:
        """A single, non-chaining comparison (1 < t < 5 isn't supported -
        write `t > 1 and t < 5` instead) - binds tighter than and/or/not,
        looser than +-, so `t + 1 < 5` parses as `(t + 1) < 5` and
        `a > 1 and b > 2` parses as `(a > 1) and (b > 2)`. `==`/`!=` work
        between any two values (a str is simply never equal to a float,
        same as Python) - `< <= > >=` require both sides to be the same
        orderable type (both numbers - never bool, which is a distinct
        type here despite Python's own bool-is-an-int - or both strings,
        lexicographic ordering), otherwise it's a clear error. This can't
        rely on catching a TypeError from Python's own operators the way
        `==`/`!=` do: Python's list and bool both define working `<`/`>`
        (list lexicographically, bool via its int-subclass semantics),
        so `[1] < [2]` or `true < false` would silently succeed with no
        exception to catch - the type check below has to happen first,
        explicitly, not after the fact."""
        left = self._expr()
        self._skip_ws()
        for op in _COMPARISON_OPS:
            if self.text[self.pos : self.pos + len(op)] == op:
                self.pos += len(op)
                right = self._expr()
                if op in ("==", "!="):
                    result = _COMPARISONS[op](left, right)
                else:
                    numbers = is_number(left) and is_number(right)
                    strings = isinstance(left, str) and isinstance(right, str)
                    if not (numbers or strings):
                        raise ExprError(f"cannot compare {_type_name(left)} and {_type_name(right)} with '{op}'")
                    result = _COMPARISONS[op](left, right)
                return bool(result)
        return left

    def _expr(self) -> Value:
        value = self._term()
        while True:
            c = self._peek()
            if c == "+":
                self.pos += 1
                right = self._term()
                # str + str concatenates, float + float adds - anything
                # else (including an array or object on either side) is a
                # clear error rather than Python's own TypeError text.
                # Branching explicitly (rather than an isinstance guard
                # followed by one `value + right`) lets mypy actually prove
                # each arm's operand types instead of seeing a 4-way union.
                if isinstance(value, str) and isinstance(right, str):
                    value = value + right
                elif is_number(value) and is_number(right):
                    value = value + right
                else:
                    raise ExprError(f"cannot use '+' between {_type_name(value)} and {_type_name(right)}")
            elif c == "-":
                self.pos += 1
                right = self._term()
                if not (is_number(value) and is_number(right)):
                    raise ExprError(f"'-' requires two numbers, got {_type_name(value)} and {_type_name(right)}")
                value = value - right
            else:
                return value

    def _term(self) -> Value:
        value = self._unary()
        while True:
            c = self._peek()
            if c == "*":
                self.pos += 1
                right = self._unary()
                if not (is_number(value) and is_number(right)):
                    raise ExprError(f"'*' requires two numbers, got {_type_name(value)} and {_type_name(right)}")
                value = value * right
            elif c == "/":
                self.pos += 1
                right = self._unary()
                if not (is_number(value) and is_number(right)):
                    raise ExprError(f"'/' requires two numbers, got {_type_name(value)} and {_type_name(right)}")
                if right == 0:
                    raise ExprError("division by zero")
                value = value / right
            elif c == "%":
                self.pos += 1
                right = self._unary()
                if not (is_number(value) and is_number(right)):
                    raise ExprError(f"'%' requires two numbers, got {_type_name(value)} and {_type_name(right)}")
                value = value % right
            else:
                return value

    def _unary(self) -> Value:
        c = self._peek()
        if c == "-":
            self.pos += 1
            value = self._unary()
            if not is_number(value):
                raise ExprError(f"unary '-' requires a number, got {_type_name(value)}")
            return -value
        if c == "+":
            self.pos += 1
            return self._unary()
        return self._atom()

    def _match_time_unit(self, start: int) -> str | None:
        """If `start` (no leading whitespace skip - callers control that)
        begins with a time-unit suffix at a word boundary, return it."""
        for suffix in _TIME_UNIT_SUFFIXES:
            end = start + len(suffix)
            if self.text[start:end] != suffix:
                continue
            if end < len(self.text) and (self.text[end].isalnum() or self.text[end] == "_"):
                continue
            return suffix
        return None

    def _consume_call_postfix_chain(self, name: str, args: list) -> tuple:
        """Scans the ENTIRE postfix chain immediately following a function
        call's closing ')', in one pass, before the call is made.

        This can't be done as an ordinary left-to-right _postfix() pass
        after the fact: .shift(offset) rewrites the call's own first
        argument, and it can appear anywhere in the chain - even after a
        .scale(...)/.add(...) that's meant to apply to the RESULT. By the
        time a left-to-right pass reached a trailing .shift(), the call
        would already have happened with the un-shifted argument, too
        late to fix. So every .shift(offset) found here, wherever it's
        written, is applied to args[0] immediately (before any call
        happens); every other recognized postfix (.scale/.add/.bias/
        .s/.m/.ms) is deferred instead, and replayed - in its own
        original relative order - on the call's result afterward, via
        _apply_pending_postfix(). Multiple .shift()s chain: each
        subtracts from whatever args[0] currently holds.

        Returns (args, pending) - args mutated in place with every shift
        applied, pending a list of (kind, payload) to replay on the
        result."""
        pending: list[tuple[str, Value]] = []
        while True:
            save = self.pos
            self._skip_ws()
            if self.pos >= len(self.text) or self.text[self.pos] != ".":
                self.pos = save
                break
            after_dot = self.pos + 1
            unit = self._match_time_unit(after_dot)
            if unit is not None:
                self.pos = after_dot + len(unit)
                pending.append(("unit", unit))
                continue
            name_end = after_dot
            while name_end < len(self.text) and (self.text[name_end].isalnum() or self.text[name_end] == "_"):
                name_end += 1
            word = self.text[after_dot:name_end]
            if not (name_end < len(self.text) and self.text[name_end] == "("):
                self.pos = save
                break
            if word == "shift":
                if not args:
                    raise ExprError(f"'.shift(...)' requires '{name}' to take at least one argument")
                if not is_number(args[0]):
                    raise ExprError(
                        f"'.shift(...)' is not supported - '{name}''s first argument is {_type_name(args[0])}"
                    )
                self.pos = name_end + 1
                offset_start = self.pos
                offset = self._or()
                offset_span = self.text[offset_start : self.pos]
                if not is_number(offset):
                    raise ExprError(f"'.shift(...)' requires a number argument, got {_type_name(offset)}")
                if not is_duration_text(offset_span, self.duration_vars):
                    raise ExprError(
                        f"'.shift(...)' needs a duration for its offset (a literal like 5s, "
                        f"or a var assigned from one) - got {offset_span.strip()!r}"
                    )
                if self._peek() != ")":
                    raise ExprError("expected ')'")
                self.pos += 1
                args[0] = args[0] - offset
                continue
            if word in _VALUE_METHODS:
                self.pos = name_end + 1
                arg = self._or()
                if self._peek() != ")":
                    raise ExprError("expected ')'")
                self.pos += 1
                pending.append((word, arg))
                continue
            self.pos = save
            break
        return args, pending

    def _apply_pending_postfix(self, value: Value, pending: list) -> Value:
        """Replays the deferred (non-shift) half of
        _consume_call_postfix_chain's scan onto a call's result, in the
        order those operators were originally written."""
        for kind, payload in pending:
            if kind == "unit":
                if not is_number(value):
                    raise ExprError(f"'.{payload}' requires a number, got {_type_name(value)}")
                value = value / _TIME_UNITS[payload]
            else:
                if not is_number(value):
                    raise ExprError(f"'.{kind}(...)' requires a number, got {_type_name(value)}")
                if not is_number(payload):
                    raise ExprError(f"'.{kind}(...)' requires a number argument, got {_type_name(payload)}")
                value = _VALUE_METHODS[kind](value, payload)
        return value

    def _postfix(self, value: Value) -> Value:
        """Chainable postfix operators/accessors, applied left to right:
        - [expr] - indexes into an array (expr must be a whole number) or
          an object (expr must be a string key). Chainable: arr[1][2].
        - .identifier - sugar for ["identifier"], recognized only when
          value is an object, and takes priority over the fixed operator
          names below (none of which mean anything on an object anyway).
        - .s/.m/.ms - a unit *view* onto a number already stored in
          seconds (X.s == X, X.m == X/60, X.ms == X*1000). No argument.
        - .scale(expr) - multiplies a number by expr.
        - .add(expr) / .bias(expr) - adds expr to a number (two names,
          one operation: "add" for plain arithmetic, "bias" for a
          signal's DC offset).
        Chainable in any combination (`x.s.scale(2).add(1)`,
        `arr[0].header`). Anything requiring a number rejects a string,
        array, or object operand with a clear error instead of silently
        leaving it unconsumed as "unexpected trailing input"."""
        while True:
            save = self.pos
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == "[":
                self.pos += 1
                index = self._or()
                if self._peek() != "]":
                    raise ExprError("expected ']'")
                self.pos += 1
                value = _index_into(value, index)
                continue
            if self.pos >= len(self.text) or self.text[self.pos] != ".":
                self.pos = save
                return value
            after_dot = self.pos + 1
            if isinstance(value, dict):
                name_end = after_dot
                while name_end < len(self.text) and (self.text[name_end].isalnum() or self.text[name_end] == "_"):
                    name_end += 1
                key = self.text[after_dot:name_end]
                if not key:
                    self.pos = save
                    return value
                self.pos = name_end
                value = _index_into(value, key)
                continue
            unit = self._match_time_unit(after_dot)
            if unit is not None:
                if not is_number(value):
                    raise ExprError(f"'.{unit}' requires a number, got {_type_name(value)}")
                self.pos = after_dot + len(unit)
                value = value / _TIME_UNITS[unit]
                continue
            name_end = after_dot
            while name_end < len(self.text) and (self.text[name_end].isalnum() or self.text[name_end] == "_"):
                name_end += 1
            method_name = self.text[after_dot:name_end]
            if method_name in _VALUE_METHODS and name_end < len(self.text) and self.text[name_end] == "(":
                if not is_number(value):
                    raise ExprError(f"'.{method_name}(...)' requires a number, got {_type_name(value)}")
                self.pos = name_end + 1
                arg = self._or()
                if not is_number(arg):
                    raise ExprError(f"'.{method_name}(...)' requires a number argument, got {_type_name(arg)}")
                if self._peek() != ")":
                    raise ExprError("expected ')'")
                self.pos += 1
                value = _VALUE_METHODS[method_name](value, arg)
                continue
            self.pos = save
            return value

    def _parse_string_literal(self) -> str:
        """A quoted string atom - no escape sequences (a stated design
        decision, not an accident: this language favors simplicity over
        punctuation everywhere else too), so a literal `"` can't appear
        inside a string at all."""
        start = self.pos
        self.pos += 1  # opening quote
        while self.pos < len(self.text) and self.text[self.pos] != '"':
            self.pos += 1
        if self.pos >= len(self.text):
            raise ExprError(f"unterminated string literal starting at position {start}")
        value = self.text[start + 1 : self.pos]
        self.pos += 1  # closing quote
        return value

    def _parse_json_object(self) -> dict:
        """`{ key: expr, key: expr, ... }`, called with self.pos already
        past the opening '{'. A key is a bare identifier or a quoted
        string - either way it becomes a plain string dict key. Values
        are ordinary expressions, so json objects nest freely inside
        each other and inside array literals: `json { a: [1, 2], b:
        json { c: 3 } }`."""
        self.pos += 1  # '{'
        obj: dict = {}
        self._skip_ws()
        if self._peek() != "}":
            self._parse_json_entry(obj)
            while self._peek() == ",":
                self.pos += 1
                self._parse_json_entry(obj)
        if self._peek() != "}":
            raise ExprError("expected '}'")
        self.pos += 1
        return obj

    def _parse_json_entry(self, obj: dict) -> None:
        self._skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == '"':
            key = self._parse_string_literal()
        elif self.pos < len(self.text) and (self.text[self.pos].isalpha() or self.text[self.pos] == "_"):
            start = self.pos
            while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
                self.pos += 1
            key = self.text[start : self.pos]
        else:
            raise ExprError("expected a json key (identifier or string)")
        self._skip_ws()
        if self._peek() != ":":
            raise ExprError("expected ':' after json key")
        self.pos += 1
        obj[key] = self._or()

    def _atom(self) -> Value:
        self._skip_ws()
        if self.pos >= len(self.text):
            raise ExprError("unexpected end of expression")
        c = self.text[self.pos]
        if c == '"':
            return self._postfix(self._parse_string_literal())
        if c == "[":
            self.pos += 1
            elements: list = []
            self._skip_ws()
            if self._peek() != "]":
                elements.append(self._or())
                while self._peek() == ",":
                    self.pos += 1
                    elements.append(self._or())
            if self._peek() != "]":
                raise ExprError("expected ']'")
            self.pos += 1
            return self._postfix(elements)
        if self._looking_at_word("json"):
            save = self.pos
            self.pos += 4
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == "{":
                return self._postfix(self._parse_json_object())
            self.pos = save  # "json" used as an ordinary identifier, not a literal
        if c == "(":
            self.pos += 1
            value = self._or()
            if self._peek() != ")":
                raise ExprError("expected ')'")
            self.pos += 1
            return self._postfix(value)
        if c.isdigit() or c == ".":
            start = self.pos
            has_dot = False
            while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] == "."):
                if self.text[self.pos] == ".":
                    has_dot = True
                self.pos += 1
            text = self.text[start : self.pos]
            unit = self._match_time_unit(self.pos)
            if unit is not None:
                # a duration literal (10s, 3m, 500ms) - no dot needed
                # between the number and unit, glued straight onto it,
                # normalizes to seconds immediately. Always a Float - a
                # length of time is never an Int, whether or not the
                # written number itself had a decimal point.
                self.pos += len(unit)
                value = float(text) * _TIME_UNITS[unit]
            elif has_dot:
                value = float(text)
            else:
                value = int(text)
            return self._postfix(value)
        if c.isalpha() or c == "_":
            start = self.pos
            while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
                self.pos += 1
            name = self.text[start : self.pos]
            if self._peek() == "(":
                self.pos += 1
                args = []
                arg_spans = []
                if self._peek() != ")":
                    arg_start = self.pos
                    args.append(self._or())
                    arg_spans.append(self.text[arg_start : self.pos])
                    while self._peek() == ",":
                        self.pos += 1
                        self._skip_ws()
                        arg_start = self.pos
                        args.append(self._or())
                        arg_spans.append(self.text[arg_start : self.pos])
                if self._peek() != ")":
                    raise ExprError("expected ')'")
                self.pos += 1
                if name not in _FUNCTIONS:
                    raise ExprError(f"unknown function '{name}'")
                if name in _DURATION_PARAMS:
                    idx, param_name = _DURATION_PARAMS[name]
                    if idx < len(arg_spans) and not is_duration_text(arg_spans[idx], self.duration_vars):
                        raise ExprError(
                            f"'{name}()' needs a duration for '{param_name}' (a literal like 5s, "
                            f"or a var assigned from one) - got {arg_spans[idx].strip()!r}"
                        )
                if name == "terop" and len(args) == 3:
                    then_category = _value_category(args[1])
                    else_category = _value_category(args[2])
                    if then_category != else_category:
                        raise ExprError(
                            f"terop()'s then/else branches must be the same type, got "
                            f"{_type_name(args[1])} and {_type_name(args[2])}"
                        )
                args, pending = self._consume_call_postfix_chain(name, args)
                try:
                    result = _FUNCTIONS[name](*args)
                except TypeError as exc:
                    raise ExprError(f"function '{name}' got an invalid argument type: {exc}") from None
                except (ValueError, ZeroDivisionError) as exc:
                    raise ExprError(f"function '{name}' failed: {exc}") from None
                # No normalization needed here anymore - every builtin's
                # own Python return type (int for floor()/ceil(), float
                # for the rest, or whatever terop's chosen branch already
                # was) is already a valid Value on its own.
                result = self._apply_pending_postfix(result, pending)
                # _consume_call_postfix_chain only understands .shift/
                # .scale/.add/.bias/.s/.m/.ms - indexing (arr[i], obj.key)
                # isn't part of that scan, so anything it left unconsumed
                # (terop(...).a, a call returning an array then [0], ...)
                # still needs an ordinary _postfix() pass.
                return self._postfix(result)
            if name in self.variables:
                # a caller-supplied int (e.g. a test's variables dict, or
                # vm.py's own scope) now passes through as a genuine Int
                # value - no coercion to float needed, Int is a real
                # first-class type on its own.
                return self._postfix(self.variables[name])
            if name in _CONSTANTS:
                return self._postfix(_CONSTANTS[name])
            raise ExprError(f"unknown identifier '{name}'")
        raise ExprError(f"unexpected character '{c}' at position {self.pos}")


def evaluate(expression: str, variables: dict, duration_vars: frozenset = frozenset()) -> Value:
    return _Parser(expression, variables, duration_vars).parse()


def parse_one(text: str, variables: dict) -> tuple[Value, int]:
    """Parses a single expression (including and/or/not/comparisons)
    starting at text[0] WITHOUT requiring the rest of the string to be
    consumed - unlike evaluate(), for embedding inside a larger grammar
    (a future statement interpreter built on this module, which is text
    made of expressions plus `field = ` and `if {} else {}` around them).
    Returns (value, chars_consumed) so the caller can advance its own
    position past exactly what was parsed."""
    p = _Parser(text, variables)
    value = p._or()  # noqa: SLF001 - the statement layer and this module are two halves of one grammar, this is the seam
    return value, p.pos
