"""Deterministic work and value limits; no clock or operating-system sandbox."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import ScriptError

MAX_SOURCE_CHARS = 200_000
MAX_DEPTH = 64
MAX_VALUE_ITEMS = 100_000
MAX_STRING_CHARS = 1_000_000
MAX_INT_BITS = 4096
DEFAULT_OPERATION_BUDGET = 1_000_000


def positive_integer(value, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ScriptError(f"{name} must be a positive integer")
    return value


def positive_number(value, name: str) -> float:
    if type(value) not in (int, float):
        raise ScriptError(f"{name} must be a positive finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ScriptError(f"{name} must be a positive finite number") from None
    if not math.isfinite(number) or number <= 0 or not math.isfinite(1.0 / number):
        raise ScriptError(f"{name} must be a positive finite number with a finite reciprocal")
    return number


@dataclass
class Budget:
    remaining: int = DEFAULT_OPERATION_BUDGET

    def spend(self, amount: int = 1) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise ScriptError("operation_budget exceeded while evaluating the script")


def check_scalar(value):
    kind = type(value)
    if kind is float and not math.isfinite(value):
        raise ScriptError("non-finite numbers are not supported")
    if kind is int and value.bit_length() > MAX_INT_BITS:
        raise ScriptError("integer exceeds the maximum bit length")
    if kind is str and len(value) > MAX_STRING_CHARS:
        raise ScriptError("string exceeds the maximum length")
    if kind not in (bool, int, float, str, list, dict):
        raise ScriptError(f"unsupported value type: {kind.__name__}")
    if kind in (list, dict) and len(value) > MAX_VALUE_ITEMS:
        raise ScriptError("container exceeds the maximum item count")
    return value


def clone_value(value, budget: Budget | None = None):
    """Copy a bounded plain value tree, rejecting cycles and custom objects.

    Visiting shared subtrees separately bounds the size of the resulting copy,
    including adversarial DAGs whose expanded size exceeds their input size.
    """
    active: set = set()
    count = 0

    def visit(item, depth):
        nonlocal count
        count += 1
        if budget is not None:
            budget.spend()
        if count > MAX_VALUE_ITEMS or depth > MAX_DEPTH:
            raise ScriptError("value exceeds the maximum size or nesting depth")
        check_scalar(item)
        if type(item) not in (list, dict):
            return item
        identity = id(item)
        if identity in active:
            raise ScriptError("cyclic values are not supported")
        active.add(identity)
        try:
            if type(item) is list:
                return [visit(child, depth + 1) for child in item]
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise ScriptError("object keys must be strings")
                check_scalar(key)
                result[key] = visit(child, depth + 1)
            return result
        finally:
            active.remove(identity)

    return visit(value, 0)
