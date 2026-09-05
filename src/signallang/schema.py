"""SchemaProvider - the pluggable seam between this package's core (which
knows nothing about ROS or any other message system) and whatever real
message schema `default` and positional array fill need to consult. The
real ROS-backed implementation is a Phase 3 concern, outside this package;
DictSchemaProvider here exists for tests.
"""

from __future__ import annotations

from typing import Protocol

from .errors import ScriptError
from .resources import clone_value


class SchemaProvider(Protocol):
    def fields_at(self, path: list) -> list:
        """Ordered sub-field names of the message/sub-message living at
        `path` (`[]` means the top-level message itself)."""
        ...

    def default_at(self, path: list):
        """The zero-initialized value for the field at `path` - a plain
        float/str for a leaf, or a nested dict for a sub-message."""
        ...


class TypedSchemaProvider(SchemaProvider, Protocol):
    def type_at(self, path: list) -> str | None:
        """Optional. The expected value-type name for the leaf field at
        `path` - one of "bool"/"int"/"float"/"string"/"array"/"object",
        or None for "no opinion" (an unrecognized path, or a sub-message
        rather than a leaf). vm.py checks for this method's existence
        with getattr(..., "type_at", None) before ever calling it, so an
        existing SchemaProvider implementation that predates this method
        (a real ROS-backed one, say) keeps working completely unchanged
        - type-checking is opt-in, never required."""
        ...


class DictSchemaProvider:
    """A schema described as a plain nested dict of field-name -> default
    value (float/str for a leaf, another dict for a sub-message) - a test
    double, not meant to model any real ROS type generation."""

    def __init__(self, schema: dict, *, strict: bool = False):
        if type(schema) is not dict:
            raise ScriptError("schema must be an object")
        self._schema = clone_value(schema)
        self.strict = strict

    def _node_at(self, path: list) -> dict:
        node = self._schema
        for seg in path:
            if not isinstance(node, dict) or seg not in node:
                raise KeyError(f"no schema field at '{'.'.join(path)}'")
            node = node[seg]
        if not isinstance(node, dict):
            raise KeyError(f"'{'.'.join(path)}' is a leaf field, not a sub-message")
        return node

    def fields_at(self, path: list) -> list:
        return list(self._node_at(path).keys())

    def default_at(self, path: list):
        if not path:
            node = self._schema
        else:
            parent = self._node_at(path[:-1])
            if path[-1] not in parent:
                raise KeyError(f"no schema field at '{'.'.join(path)}'")
            node = parent[path[-1]]
        return clone_value(node)

    def type_at(self, path: list) -> str | None:
        """Derived straight from the default value's own Python type -
        no separate type-declaration syntax needed, since the schema
        dict already has to specify a default of the right type anyway
        (write `{"level": 0}` for an Int field, `{"ratio": 0.0}` for a
        Float one, `{"valid": False}` for a Bool one)."""
        if not path:
            return None
        node = self._node_at(path[:-1]).get(path[-1])
        if isinstance(node, dict):
            return None  # a sub-message, not a leaf - no single type
        return _python_value_type_name(node)

    def validate_at(self, path: list, value) -> None:
        """Strict mode checks shape, unknown keys, and homogeneous arrays.

        A nonempty array default uses its first element as an element schema;
        an empty default allows any supported element type. Int/Float conversion
        is never implicit. Defaults remain values, not mutable schema objects.
        """
        if not self.strict:
            return
        template = self._schema
        for segment in path:
            if type(template) is not dict or segment not in template:
                raise ScriptError(f"unknown schema field '{'.'.join(path)}'")
            template = template[segment]

        def check(expected, actual, where):
            if type(expected) is not type(actual):
                raise ScriptError(f"'{where}' expects {type(expected).__name__}, got {type(actual).__name__}")
            if type(expected) is dict:
                missing = expected.keys() - actual.keys()
                extra = actual.keys() - expected.keys()
                if missing or extra:
                    raise ScriptError(
                        f"'{where}' schema keys mismatch: missing {sorted(missing)}, unknown {sorted(extra)}"
                    )
                for key in expected:
                    check(expected[key], actual[key], f"{where}.{key}")
            elif type(expected) is list and expected:
                for i, item in enumerate(actual):
                    check(expected[0], item, f"{where}[{i}]")

        check(template, value, ".".join(path) or "msg")


def _python_value_type_name(value) -> str | None:
    # bool checked first - Python's bool is an int subclass.
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
    return None
