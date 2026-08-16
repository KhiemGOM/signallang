from __future__ import annotations

import math
import random
from typing import Callable, Union


Value = Union[float, str, list, dict]


def is_truthy(value: Value) -> bool:
    """The one truthiness rule for and/or/not, terop's cond, and if/repeat
    conditions: plain Python truthiness (0.0/""/[]/{} are falsy, anything
    else truthy), made explicit rather than left to a bare `value != 0.0`
    comparison, which is wrong for a non-float value - a str is never ==
    or != a float, so that check would call every string truthy, empty or
    not, and the same problem would recur for every future value type."""
    return bool(value)


def _terop(cond: Value, then: Value, otherwise: Value) -> Value:
    return then if is_truthy(cond) else otherwise


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


def _noise(mean: float, stddev: float) -> float:
    """A single Gaussian-distributed random draw - call it inside a live
    context (`noise!(mean, stddev)`) for fresh jitter every tick, or bare
    for a one-shot random value frozen at assignment time, exactly like
    any other function."""
    return random.gauss(mean, stddev)


# The fixed set of "time-shaped" builtins - the only ones whose bang-call
# sugar (name!(args)) also injects _t as a leading argument, matching the
# ergonomic call shape they'd otherwise lose (linear!(20, 30, 10s), not
# linear!(_t, 20, 30, 10s)). Every other function's bang call wraps its
# arguments exactly as written - see parser.py's _try_parse_bang_call.
TIME_SHAPED_FUNCTIONS = frozenset(
    {"linear", "square", "triangle", "sawtooth", "damped_wave", "sinusoidal_wave"}
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
    "random": random.random,
    "noise": _noise,
    "linear": _linear,
    "square": _square,
    "triangle": _triangle,
    "sawtooth": _sawtooth,
    "damped_wave": _damped_wave,
    "sinusoidal_wave": _sinusoidal_wave,
    # terop(cond, then, else) - a plain named function, not a `? :` symbol.
    # Both branches are evaluated eagerly (this parser evaluates as it
    # parses, it doesn't build an AST to defer either side) - a
    # division-by-zero in the branch NOT taken still raises, which reads as
    # "fix your unreached branch too" rather than a silent trap.
    "terop": _terop,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "true": 1.0, "false": 0.0}
# Every time-like quantity in this language is stored internally as a plain
# float in seconds - a duration literal (10s, 3m, 500ms) normalizes to
# seconds at parse time; the postfix .s/.m/.ms operator is the reverse view
# onto any expression already in seconds. "ms" must be checked before "s"/"m"
# (longest match first) so ".ms" isn't swallowed as ".m" leaving a stray "s".
_TIME_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0}
_TIME_UNIT_SUFFIXES = ("ms", "s", "m")
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
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "number"


def _index_into(container: Value, index: Value) -> Value:
    """arr[i] / obj["key"] - a whole-number index into an array, or a
    string key into an object. The one place that actually reads out of
    a compound value; everything upstream just builds them."""
    if isinstance(container, list):
        if not isinstance(index, float):
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

    def __init__(self, text: str, variables: dict):
        self.text = text
        self.pos = 0
        self.variables = variables

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
            value = 1.0 if (is_truthy(value) or is_truthy(right)) else 0.0
        return value

    def _and(self) -> Value:
        value = self._not()
        while self._looking_at_word("and"):
            self.pos += 3
            right = self._not()
            value = 1.0 if (is_truthy(value) and is_truthy(right)) else 0.0
        return value

    def _not(self) -> Value:
        if self._looking_at_word("not"):
            self.pos += 3
            value = self._not()
            return 0.0 if is_truthy(value) else 1.0
        return self._comparison()

    def _comparison(self) -> Value:
        """A single, non-chaining comparison (1 < t < 5 isn't supported -
        write `t > 1 and t < 5` instead) - binds tighter than and/or/not,
        looser than +-, so `t + 1 < 5` parses as `(t + 1) < 5` and
        `a > 1 and b > 2` parses as `(a > 1) and (b > 2)`. `==`/`!=` work
        between any two values (a str is simply never equal to a float,
        same as Python) - `< <= > >=` require both sides to be the same
        type (both numbers, or both strings - lexicographic ordering),
        otherwise it's a clear error rather than a raw TypeError."""
        left = self._expr()
        self._skip_ws()
        for op in _COMPARISON_OPS:
            if self.text[self.pos : self.pos + len(op)] == op:
                self.pos += len(op)
                right = self._expr()
                try:
                    result = _COMPARISONS[op](left, right)
                except TypeError:
                    raise ExprError(
                        f"cannot compare {_type_name(left)} and {_type_name(right)} with '{op}'"
                    ) from None
                return 1.0 if result else 0.0
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
                elif isinstance(value, float) and isinstance(right, float):
                    value = value + right
                else:
                    raise ExprError(f"cannot use '+' between {_type_name(value)} and {_type_name(right)}")
            elif c == "-":
                self.pos += 1
                right = self._term()
                if not (isinstance(value, float) and isinstance(right, float)):
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
                if not (isinstance(value, float) and isinstance(right, float)):
                    raise ExprError(f"'*' requires two numbers, got {_type_name(value)} and {_type_name(right)}")
                value = value * right
            elif c == "/":
                self.pos += 1
                right = self._unary()
                if not (isinstance(value, float) and isinstance(right, float)):
                    raise ExprError(f"'/' requires two numbers, got {_type_name(value)} and {_type_name(right)}")
                if right == 0:
                    raise ExprError("division by zero")
                value = value / right
            elif c == "%":
                self.pos += 1
                right = self._unary()
                if not (isinstance(value, float) and isinstance(right, float)):
                    raise ExprError(f"'%' requires two numbers, got {_type_name(value)} and {_type_name(right)}")
                value = value % right
            else:
                return value

    def _unary(self) -> Value:
        c = self._peek()
        if c == "-":
            self.pos += 1
            value = self._unary()
            if not isinstance(value, float):
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
                if not isinstance(args[0], float):
                    raise ExprError(
                        f"'.shift(...)' is not supported - '{name}''s first argument is {_type_name(args[0])}"
                    )
                self.pos = name_end + 1
                offset = self._or()
                if not isinstance(offset, float):
                    raise ExprError(f"'.shift(...)' requires a number argument, got {_type_name(offset)}")
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
                if not isinstance(value, float):
                    raise ExprError(f"'.{payload}' requires a number, got {_type_name(value)}")
                value = value / _TIME_UNITS[payload]
            else:
                if not isinstance(value, float):
                    raise ExprError(f"'.{kind}(...)' requires a number, got {_type_name(value)}")
                if not isinstance(payload, float):
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
                if not isinstance(value, float):
                    raise ExprError(f"'.{unit}' requires a number, got {_type_name(value)}")
                self.pos = after_dot + len(unit)
                value = value / _TIME_UNITS[unit]
                continue
            name_end = after_dot
            while name_end < len(self.text) and (self.text[name_end].isalnum() or self.text[name_end] == "_"):
                name_end += 1
            method_name = self.text[after_dot:name_end]
            if method_name in _VALUE_METHODS and name_end < len(self.text) and self.text[name_end] == "(":
                if not isinstance(value, float):
                    raise ExprError(f"'.{method_name}(...)' requires a number, got {_type_name(value)}")
                self.pos = name_end + 1
                arg = self._or()
                if not isinstance(arg, float):
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
            while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] == "."):
                self.pos += 1
            value = float(self.text[start : self.pos])
            # a duration literal (10s, 3m, 500ms) - no dot, glued straight
            # onto the number - normalizes to seconds immediately.
            unit = self._match_time_unit(self.pos)
            if unit is not None:
                self.pos += len(unit)
                value *= _TIME_UNITS[unit]
            return self._postfix(value)
        if c.isalpha() or c == "_":
            start = self.pos
            while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
                self.pos += 1
            name = self.text[start : self.pos]
            if self._peek() == "(":
                self.pos += 1
                args = []
                if self._peek() != ")":
                    args.append(self._or())
                    while self._peek() == ",":
                        self.pos += 1
                        args.append(self._or())
                if self._peek() != ")":
                    raise ExprError("expected ')'")
                self.pos += 1
                if name not in _FUNCTIONS:
                    raise ExprError(f"unknown function '{name}'")
                args, pending = self._consume_call_postfix_chain(name, args)
                try:
                    result = _FUNCTIONS[name](*args)
                except TypeError as exc:
                    raise ExprError(f"function '{name}' got an invalid argument type: {exc}") from None
                except (ValueError, ZeroDivisionError) as exc:
                    raise ExprError(f"function '{name}' failed: {exc}") from None
                # floor()/ceil() return a plain int, not float - normalize
                # that; str/list/dict (terop can produce any Value) pass
                # through untouched.
                if not isinstance(result, (str, list, dict)):
                    result = float(result)
                result = self._apply_pending_postfix(result, pending)
                # _consume_call_postfix_chain only understands .shift/
                # .scale/.add/.bias/.s/.m/.ms - indexing (arr[i], obj.key)
                # isn't part of that scan, so anything it left unconsumed
                # (terop(...).a, a call returning an array then [0], ...)
                # still needs an ordinary _postfix() pass.
                return self._postfix(result)
            if name in self.variables:
                value = self.variables[name]
                # callers (e.g. tests, or vm.py's own scope dict) may
                # hand in a plain int for a numeric variable - coerce
                # that to float, same as every literal in this language
                # already is, without touching str/list/dict.
                if isinstance(value, int):
                    value = float(value)
                return self._postfix(value)
            if name in _CONSTANTS:
                return self._postfix(_CONSTANTS[name])
            raise ExprError(f"unknown identifier '{name}'")
        raise ExprError(f"unexpected character '{c}' at position {self.pos}")


def evaluate(expression: str, variables: dict) -> Value:
    return _Parser(expression, variables).parse()


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
