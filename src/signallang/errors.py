from __future__ import annotations


class ScriptError(ValueError):
    """A statement-grammar error - unmatched braces, missing `;`, an
    unrecognized construct, a length mismatch in positional fill, etc.
    Wraps ExprError too, so callers only need to catch one exception type."""

    def __init__(self, message: str, pos: int | None = None):
        self.pos = pos
        super().__init__(message if pos is None else f"{message} (at position {pos})")
