import math
import random


def _terop(cond: float, then: float, otherwise: float) -> float:
    return then if cond else otherwise


_FUNCTIONS = {
    "sin": math.sin,
    "cos": math.cos,
    "abs": abs,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "min": min,
    "max": max,
    "random": random.random,
    # terop(cond, then, else) - a plain named function, not a `? :` symbol
    # (this language favors words over punctuation throughout - and/or/not,
    # true/false - no reason for this to be the exception), and not called
    # `if` either, so it never collides with the statement-level `if {}
    # else {}` block a future statement grammar built on this module would
    # add. Both branches are evaluated eagerly (this parser evaluates as it
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

    def parse(self) -> float:
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

    def _or(self) -> float:
        value = self._and()
        while self._looking_at_word("or"):
            self.pos += 2
            right = self._and()
            value = 1.0 if (value != 0.0 or right != 0.0) else 0.0
        return value

    def _and(self) -> float:
        value = self._not()
        while self._looking_at_word("and"):
            self.pos += 3
            right = self._not()
            value = 1.0 if (value != 0.0 and right != 0.0) else 0.0
        return value

    def _not(self) -> float:
        if self._looking_at_word("not"):
            self.pos += 3
            value = self._not()
            return 0.0 if value != 0.0 else 1.0
        return self._comparison()

    def _comparison(self) -> float:
        """A single, non-chaining comparison (1 < t < 5 isn't supported -
        write `t > 1 and t < 5` instead) - binds tighter than and/or/not,
        looser than +-, so `t + 1 < 5` parses as `(t + 1) < 5` and
        `a > 1 and b > 2` parses as `(a > 1) and (b > 2)`."""
        left = self._expr()
        self._skip_ws()
        for op in _COMPARISON_OPS:
            if self.text[self.pos : self.pos + len(op)] == op:
                self.pos += len(op)
                right = self._expr()
                return 1.0 if _COMPARISONS[op](left, right) else 0.0
        return left

    def _expr(self) -> float:
        value = self._term()
        while True:
            c = self._peek()
            if c == "+":
                self.pos += 1
                value += self._term()
            elif c == "-":
                self.pos += 1
                value -= self._term()
            else:
                return value

    def _term(self) -> float:
        value = self._unary()
        while True:
            c = self._peek()
            if c == "*":
                self.pos += 1
                value *= self._unary()
            elif c == "/":
                self.pos += 1
                divisor = self._unary()
                if divisor == 0:
                    raise ExprError("division by zero")
                value /= divisor
            elif c == "%":
                self.pos += 1
                value %= self._unary()
            else:
                return value

    def _unary(self) -> float:
        c = self._peek()
        if c == "-":
            self.pos += 1
            return -self._unary()
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

    def _postfix(self, value: float) -> float:
        """Postfix .s/.m/.ms - a unit *view* onto a value already stored in
        seconds (X.s == X, X.m == X/60, X.ms == X*1000). Applies uniformly to
        timers (t.s, _t.ms, a `var`-bound timer()'s .s) and to any other
        expression, since nothing about it is really "attribute access" -
        it's three fixed, always-available postfix operators."""
        save = self.pos
        self._skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == ".":
            unit = self._match_time_unit(self.pos + 1)
            if unit is not None:
                self.pos += 1 + len(unit)
                return value / _TIME_UNITS[unit]
        self.pos = save
        return value

    def _atom(self) -> float:
        self._skip_ws()
        if self.pos >= len(self.text):
            raise ExprError("unexpected end of expression")
        c = self.text[self.pos]
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
                return self._postfix(float(_FUNCTIONS[name](*args)))
            if name in self.variables:
                return self._postfix(float(self.variables[name]))
            if name in _CONSTANTS:
                return self._postfix(_CONSTANTS[name])
            raise ExprError(f"unknown identifier '{name}'")
        raise ExprError(f"unexpected character '{c}' at position {self.pos}")


def evaluate(expression: str, variables: dict) -> float:
    return _Parser(expression, variables).parse()


def parse_one(text: str, variables: dict) -> tuple[float, int]:
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
