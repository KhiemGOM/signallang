from .errors import ScriptError


def scan_span(text: str, start: int, stop_chars: str) -> int:
    """Return the index of the first `stop_chars` character found at
    paren/bracket depth 0, outside a string literal, scanning from `start`.

    This is the one place that understands "where does this expression /
    array-literal element end" - it never parses the content, just finds
    its boundary, so a nested call's `,` or a nested array's `]` doesn't
    get mistaken for the enclosing one's terminator, and a `;` inside a
    string literal doesn't get mistaken for a statement end.
    """
    depth = 0
    in_string = False
    i = start
    while i < len(text):
        c = text[i]
        if in_string:
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if depth == 0 and c in stop_chars:
            # a stop char wins even if it's also a bracket character -
            # e.g. ")" terminating a function-call argument span, or "{"
            # terminating an if-condition/live-block-opener span (that
            # opening brace IS the terminator being scanned for, not a
            # nested region to skip past).
            return i
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                raise ScriptError(f"unmatched '{c}'", i)
            depth -= 1
        i += 1
    raise ScriptError(
        f"unexpected end of input while scanning for one of {stop_chars!r}", start
    )


def scan_until_token(text: str, start: int, token: str) -> int:
    """Same depth/quote-aware scan as scan_span, but for a multi-character
    stop token (`..` for a `for` loop's range) rather than a set of stop
    characters - a single-char scan would wrongly stop inside a decimal
    number's own '.'."""
    depth = 0
    in_string = False
    i = start
    n = len(token)
    while i < len(text):
        c = text[i]
        if in_string:
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if depth == 0 and text[i : i + n] == token:
            return i
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                raise ScriptError(f"unmatched '{c}'", i)
            depth -= 1
        i += 1
    raise ScriptError(f"unexpected end of input while scanning for {token!r}", start)
