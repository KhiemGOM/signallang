"""SchemaProvider - the pluggable seam between this package's core (which
knows nothing about ROS or any other message system) and whatever real
message schema `default` and positional array fill need to consult. The
real ROS-backed implementation is a Phase 3 concern, outside this package;
DictSchemaProvider here exists for tests.
"""

from typing import Protocol


class SchemaProvider(Protocol):
    def fields_at(self, path: list) -> list:
        """Ordered sub-field names of the message/sub-message living at
        `path` (`[]` means the top-level message itself)."""
        ...

    def default_at(self, path: list):
        """The zero-initialized value for the field at `path` - a plain
        float/str for a leaf, or a nested dict for a sub-message."""
        ...


class DictSchemaProvider:
    """A schema described as a plain nested dict of field-name -> default
    value (float/str for a leaf, another dict for a sub-message) - a test
    double, not meant to model any real ROS type generation."""

    def __init__(self, schema: dict):
        self._schema = schema

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
        if isinstance(node, dict):
            return {k: self.default_at(path + [k]) for k in node}
        return node
