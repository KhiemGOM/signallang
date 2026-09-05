"""A restricted expression AST. Parsing never executes user expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from . import expr
from .errors import ScriptError
from .resources import MAX_DEPTH, MAX_SOURCE_CHARS, Budget, check_scalar, clone_value


@dataclass(frozen=True)
class Node:
    kind: str
    args: tuple
    pos: int


# Bounds are language arities, independent of Python callable introspection.
ARITIES = {
    "sin": (1, 1),
    "cos": (1, 1),
    "abs": (1, 1),
    "sqrt": (1, 1),
    "floor": (1, 1),
    "ceil": (1, 1),
    "min": (2, None),
    "max": (2, None),
    "floordiv": (2, 2),
    "random": (0, 0),
    "noise": (2, 2),
    "uniform": (2, 2),
    "discrete_uniform": (2, 2),
    "poisson": (1, 1),
    "binomial": (2, 2),
    "linear": (4, 4),
    "square": (4, 4),
    "triangle": (4, 4),
    "sawtooth": (4, 4),
    "damped_wave": (4, 4),
    "sinusoidal_wave": (3, 3),
    "pulse": (5, 5),
    "exponential": (3, 3),
    "polynomial": (1, None),
    "terop": (3, 3),
    "keyframes": (2, 4),
}
NUMBER = re.compile(r"(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?(?:ms|s|m)?")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PRECEDENCE = {
    "or": 1,
    "and": 2,
    "==": 4,
    "!=": 4,
    "<": 4,
    "<=": 4,
    ">": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "%": 6,
}


class Parser:
    def __init__(self, text: str):
        if len(text) > MAX_SOURCE_CHARS:
            raise expr.ExprError("expression exceeds the maximum source size", 0)
        self.text = text
        self.pos = 0

    def ws(self):
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def peek(self):
        self.ws()
        return self.text[self.pos : self.pos + 1]

    def take(self, token):
        self.ws()
        if self.text.startswith(token, self.pos):
            end = self.pos + len(token)
            if token.isalpha() and end < len(self.text) and (self.text[end].isalnum() or self.text[end] == "_"):
                return False
            self.pos = end
            return True
        return False

    def require(self, token):
        if not self.take(token):
            raise expr.ExprError(f"expected '{token}'", self.pos)

    def ident(self):
        self.ws()
        match = IDENT.match(self.text, self.pos)
        if not match:
            raise expr.ExprError("expected an identifier", self.pos)
        self.pos = match.end()
        return match.group()

    def expression(self, minimum=0, depth=0):
        if depth > MAX_DEPTH:
            raise expr.ExprError("expression exceeds maximum nesting depth", self.pos)
        self.ws()
        pos = self.pos
        if self.take("not"):
            node = Node("unary", ("not", self.expression(3, depth + 1)), pos)
        elif self.peek() in ("+", "-"):
            op: str | None = self.text[self.pos]
            self.pos += 1
            node = Node("unary", (op, self.expression(7, depth + 1)), pos)
        else:
            node = self.atom(depth + 1)
        compared = False
        while True:
            self.ws()
            save = self.pos
            op = next(
                (
                    op
                    for op in ("<=", ">=", "==", "!=", "or", "and", "<", ">", "+", "-", "*", "/", "%")
                    if self.take(op)
                ),
                None,
            )
            if op is None:
                break
            precedence = PRECEDENCE[op]
            if precedence < minimum:
                self.pos = save
                break
            if precedence == 4:
                if compared:
                    raise expr.ExprError("chained comparisons are not supported; use and", save)
                compared = True
            right = self.expression(precedence + 1, depth + 1)
            node = Node("binary", (op, node, right), save)
        return node

    def string(self):
        self.ws()
        start = self.pos
        self.require('"')
        end = self.text.find('"', self.pos)
        if end < 0:
            raise expr.ExprError("unterminated string literal", start)
        value = self.text[self.pos : end]
        self.pos = end + 1
        return value

    def atom(self, depth):
        self.ws()
        pos = self.pos
        char = self.peek()
        call = None
        if char == '"':
            node = Node("literal", (self.string(),), pos)
        elif self.take("["):
            children = self.sequence("]", depth)
            node = Node("array", tuple(children), pos)
        elif self.take("("):
            node = self.expression(depth=depth)
            self.require(")")
        elif char and (char.isdigit() or char == "."):
            match = NUMBER.match(self.text, self.pos)
            if not match:
                raise expr.ExprError("invalid number", pos)
            raw = match.group()
            self.pos = match.end()
            unit = next((u for u in ("ms", "s", "m") if raw.endswith(u)), None)
            try:
                if unit:
                    value = float(raw[: -len(unit)]) * expr._TIME_UNITS[unit]
                else:
                    value = float(raw) if any(c in raw for c in ".eE") else int(raw)
                check_scalar(value)
            except (ScriptError, ValueError, OverflowError) as error:
                raise expr.ExprError(str(error), pos) from None
            node = Node("literal", (value,), pos)
        else:
            name = self.ident()
            if name == "json" and self.take("{"):
                entries: list = []
                keys: set = set()
                if not self.take("}"):
                    while True:
                        key = self.string() if self.peek() == '"' else self.ident()
                        self.require(":")
                        if key in keys:
                            raise expr.ExprError(f"duplicate object key {key!r}", self.pos)
                        keys.add(key)
                        entries.append((key, self.expression(depth=depth)))
                        if self.take("}"):
                            break
                        self.require(",")
                node = Node("object", tuple(entries), pos)
            elif self.take("("):
                args = self.sequence(")", depth)
                if name not in ARITIES:
                    raise expr.ExprError(f"unknown function '{name}'", pos)
                low, high = ARITIES[name]
                if len(args) < low or (high is not None and len(args) > high):
                    raise expr.ExprError(f"function '{name}' got an invalid argument count", pos)
                node = Node("call", (name, tuple(args), ()), pos)
                call = node
            else:
                node = Node("name", (name,), pos)
        # A call retains its initial chain so shifts precede result transforms.
        chain: list = []
        while True:
            if self.take("["):
                if call is not None:
                    node = Node("call", (call.args[0], call.args[1], tuple(chain)), call.pos)
                    call = None
                index = self.expression(depth=depth)
                self.require("]")
                node = Node("index", (node, index), self.pos)
                continue
            if not self.take("."):
                break
            name = self.ident()
            if self.take("("):
                if name not in ("shift", "scale", "add", "bias"):
                    raise expr.ExprError(f"unknown postfix method {name!r}", self.pos)
                argument = self.expression(depth=depth)
                self.require(")")
                if name == "shift" and (call is None or not call.args[1]):
                    raise expr.ExprError(".shift requires a function call with an argument", self.pos)
                if call is not None:
                    chain.append((name, argument))
                else:
                    node = Node("method", (name, node, argument), self.pos)
            else:
                if call is not None and name in expr._TIME_UNITS:
                    chain.append((name, None))
                else:
                    if call is not None:
                        node = Node("call", (call.args[0], call.args[1], tuple(chain)), call.pos)
                        call = None
                    node = Node("dot", (node, name), self.pos)
        return Node("call", (call.args[0], call.args[1], tuple(chain)), call.pos) if call is not None else node

    def sequence(self, close, depth):
        result = []
        if not self.take(close):
            while True:
                result.append(self.expression(depth=depth))
                if self.take(close):
                    break
                self.require(",")
        return result


@lru_cache(maxsize=256)
def compile_expression(text: str) -> Node:
    parser = Parser(text)
    try:
        node = parser.expression()
        if parser.peek():
            raise expr.ExprError("unexpected trailing input", parser.pos)
        # Left-associative chains do not recurse during parsing; bound their AST too.
        pending = [(node, 0)]
        while pending:
            current, depth = pending.pop()
            if depth > MAX_DEPTH:
                raise expr.ExprError("expression exceeds maximum nesting depth", node.pos)
            if isinstance(current, Node):
                pending.append((current.args, depth + 1))
            elif isinstance(current, tuple):
                pending.extend((child, depth) for child in current)
        return node
    except RecursionError:
        raise expr.ExprError("expression exceeds maximum nesting depth", parser.pos) from None


def number(value):
    if not expr.is_number(value):
        raise expr.ExprError(f"expected a number, got {expr.type_name(value)}")
    return value


def transform(name, value, argument=None):
    number(value)
    if name in expr._TIME_UNITS:
        return value / expr._TIME_UNITS[name]
    number(argument)
    return expr._VALUE_METHODS[name](value, argument)


def execute(node: Node, scope, budget: Budget):
    def run(current):
        kind, args = current.kind, current.args
        try:
            budget.spend()
            if kind == "literal":
                value = args[0]
            elif kind == "name":
                name = args[0]
                if name in scope:
                    value = scope[name]
                elif name in expr._CONSTANTS:
                    value = expr._CONSTANTS[name]
                else:
                    raise expr.ExprError(f"unknown identifier '{name}'")
            elif kind == "array":
                value = [run(child) for child in args]
            elif kind == "object":
                value = {key: run(child) for key, child in args}
            elif kind == "unary":
                op, child = args
                value = run(child)
                value = not expr.is_truthy(value) if op == "not" else (+number(value) if op == "+" else -number(value))
            elif kind == "binary":
                op, left, right = args
                left = run(left)
                if op == "and":
                    return expr.is_truthy(left) and expr.is_truthy(run(right))
                if op == "or":
                    return expr.is_truthy(left) or expr.is_truthy(run(right))
                right = run(right)
                if op in expr._COMPARISONS:
                    if op not in ("==", "!=") and not (
                        (expr.is_number(left) and expr.is_number(right)) or (type(left) is str and type(right) is str)
                    ):
                        raise expr.ExprError("ordering requires two numbers or two strings")
                    # Account for structural comparisons before invoking Python.
                    if type(left) in (list, dict):
                        clone_value(left, budget)
                    if type(right) in (list, dict):
                        clone_value(right, budget)
                    if type(left) is str and type(right) is str:
                        budget.spend(min(len(left), len(right)))
                    value = expr._COMPARISONS[op](left, right)
                elif op == "+" and type(left) is str and type(right) is str:
                    budget.spend(len(left) + len(right))
                    value = left + right
                else:
                    number(left)
                    number(right)
                    if op == "+":
                        value = left + right
                    elif op == "-":
                        value = left - right
                    elif op == "*":
                        value = left * right
                    elif op == "/":
                        value = left / right
                    else:
                        value = left % right
            elif kind == "index":
                value = expr._index_into(run(args[0]), run(args[1]))
            elif kind == "dot":
                value = run(args[0])
                value = (
                    expr._index_into(value, args[1])
                    if type(value) is dict
                    else transform(args[1], value)
                    if args[1] in expr._TIME_UNITS
                    else expr._index_into(value, args[1])
                )
            elif kind == "method":
                value = transform(args[0], run(args[1]), run(args[2]))
            elif kind == "call":
                name, children, chain = args
                values = [run(child) for child in children]
                pending = []
                for method, child in chain:
                    argument = run(child) if child is not None else None
                    if method == "shift":
                        values[0] = number(values[0]) - number(argument)
                    else:
                        pending.append((method, argument))
                if name == "terop":
                    if expr._value_category(values[1]) != expr._value_category(values[2]):
                        raise expr.ExprError("terop branches must be the same type")
                elif name in ("min", "max"):
                    if not (all(expr.is_number(v) for v in values) or all(type(v) is str for v in values)):
                        raise expr.ExprError(f"{name} requires all numbers or all strings")
                elif name != "keyframes":
                    for value in values:
                        number(value)
                value = expr._FUNCTIONS[name](*values)
                for method, argument in pending:
                    value = (
                        expr._index_into(value, method)
                        if type(value) is dict and argument is None
                        else transform(method, value, argument)
                    )
            else:
                raise expr.ExprError(f"internal: unknown node {kind}")
            return check_scalar(value)
        except (ScriptError, ValueError, TypeError, ArithmeticError, KeyError) as error:
            if isinstance(error, expr.ExprError) and error.pos is not None:
                raise
            raise expr.ExprError(str(error), current.pos) from None

    return run(node)
