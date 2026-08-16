"""Source text -> AST. Structural only - never evaluates an expression, just
figures out the shape of the program. Both branches of every if/else are
parsed eagerly (a syntax error inside a branch that might never run at
runtime is still a parse-time error), matching the old script.py prototype's
own precedent.
"""

from __future__ import annotations

from . import span
from .ast_nodes import (
    ArrayLit,
    Assign,
    Default,
    ExprSpan,
    For,
    If,
    LiveBlock,
    Program,
    Reassign,
    Repeat,
    Send,
    TimerDecl,
    TimerReset,
    VarDecl,
)
from .errors import ScriptError
from .expr import TIME_SHAPED_FUNCTIONS

_UNIT_SUFFIXES = ("ms", "s", "m", "t")  # longest match first
_KEYWORDS = frozenset(
    {
        "var",
        "if",
        "else",
        "repeat",
        "for",
        "in",
        "send",
        "hz",
        "dur",
        "inf",
        "default",
        "live",
        "return",
        "timer",
        "latching_timer",
        "reset",
        "msg",
    }
)


def _strip_comments(text: str) -> str:
    """Blank out `// ...` to end-of-line, preserving every other character's
    position (including inside string literals, where `//` is just text)."""
    out = []
    in_string = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


class Parser:
    def __init__(self, text: str):
        self.text = _strip_comments(text)
        self.pos = 0
        self.known_vars: set = set()

    # -- low-level helpers --------------------------------------------------

    def _skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _peek_char(self) -> str:
        self._skip_ws()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _looking_at_word(self, word: str) -> bool:
        self._skip_ws()
        end = self.pos + len(word)
        if self.text[self.pos : end] != word:
            return False
        return end >= len(self.text) or not (self.text[end].isalnum() or self.text[end] == "_")

    def _advance_word(self, word: str) -> None:
        if not self._looking_at_word(word):
            raise ScriptError(f"expected '{word}'", self.pos)
        self.pos += len(word)

    def _expect_char(self, c: str) -> None:
        self._skip_ws()
        if self.pos >= len(self.text) or self.text[self.pos] != c:
            raise ScriptError(f"expected '{c}'", self.pos)
        self.pos += 1

    def _parse_ident(self) -> str:
        self._skip_ws()
        start = self.pos
        if self.pos >= len(self.text) or not (self.text[self.pos].isalpha() or self.text[self.pos] == "_"):
            raise ScriptError("expected an identifier", self.pos)
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        name = self.text[start : self.pos]
        if name in _KEYWORDS:
            raise ScriptError(f"'{name}' is a reserved word, not a valid name here", start)
        return name

    def _parse_path_ident(self) -> str:
        """A message-field path segment - unlike _parse_ident, not restricted
        against the keyword set: field names come from the target ROS
        schema, not this language, so a real field literally named `linear`
        (geometry_msgs/Twist) must coexist with the `linear(...)` builtin -
        the two are never ambiguous, since a path segment and a function
        call are distinguished by context, not by the name itself."""
        self._skip_ws()
        start = self.pos
        if self.pos >= len(self.text) or not (self.text[self.pos].isalpha() or self.text[self.pos] == "_"):
            raise ScriptError("expected a field name", self.pos)
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        return self.text[start : self.pos]

    def _match_unit_suffix(self) -> str | None:
        """Adjacent (no whitespace skip) unit suffix check, longest match
        first, at a word boundary - used right after a raw number token."""
        for suffix in _UNIT_SUFFIXES:
            end = self.pos + len(suffix)
            if self.text[self.pos : end] != suffix:
                continue
            if end < len(self.text) and (self.text[end].isalnum() or self.text[end] == "_"):
                continue
            return suffix
        return None

    def _parse_number(self) -> float:
        self._skip_ws()
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] == "."):
            self.pos += 1
        if self.pos == start:
            raise ScriptError("expected a number", start)
        return float(self.text[start : self.pos])

    def _scan_expr_span(self, stop_chars: str) -> ExprSpan:
        self._skip_ws()
        start = self.pos
        end = span.scan_span(self.text, start, stop_chars)
        text = self.text[start:end].strip()
        if not text:
            raise ScriptError("expected an expression", start)
        self.pos = end
        return ExprSpan(text)

    # -- program / blocks -----------------------------------------------

    def parse_program(self) -> Program:
        body = self._parse_block_until("", self._parse_stmt)
        self._skip_ws()
        if self.pos != len(self.text):
            raise ScriptError(f"unexpected trailing input: {self.text[self.pos:].strip()!r}", self.pos)
        return Program(body=body)

    def _parse_block_until(self, end_char: str, stmt_parser) -> list:
        stmts = []
        while True:
            self._skip_ws()
            if self.pos >= len(self.text) or (end_char and self.text[self.pos] == end_char):
                break
            stmts.append(stmt_parser())
        return stmts

    # -- statements (main program / loop / if bodies) --------------------

    def _parse_stmt(self):
        self._skip_ws()
        if self._looking_at_word("var"):
            return self._parse_var_decl()
        if self._looking_at_word("if"):
            return self._parse_if(self._parse_stmt)
        if self._looking_at_word("repeat"):
            return self._parse_repeat()
        if self._looking_at_word("for"):
            return self._parse_for()
        if self._looking_at_word("send"):
            return self._parse_send()
        return self._parse_path_stmt()

    def _parse_var_decl(self):
        self._advance_word("var")
        name = self._parse_ident()
        self._expect_char("=")
        self._skip_ws()
        if self._looking_at_word("timer"):
            self._advance_word("timer")
            self._expect_char("(")
            self._expect_char(")")
            self._expect_char(";")
            self.known_vars.add(name)
            return TimerDecl(name=name, kind="eager")
        if self._looking_at_word("latching_timer"):
            self._advance_word("latching_timer")
            self._expect_char("(")
            self._expect_char(")")
            self._expect_char(";")
            self.known_vars.add(name)
            return TimerDecl(name=name, kind="latching")
        value = self._scan_expr_span(";")
        self._expect_char(";")
        self.known_vars.add(name)
        return VarDecl(name=name, value=value)

    def _parse_path_stmt(self):
        path = [self._parse_path_ident()]
        while self._peek_char() == ".":
            self.pos += 1
            ident = self._parse_ident_or_keyword_reset()
            if ident == "reset":
                self._expect_char("(")
                self._expect_char(")")
                self._expect_char(";")
                if len(path) != 1:
                    raise ScriptError("'.reset()' only applies to a single timer name", self.pos)
                return TimerReset(name=path[0])
            path.append(ident)
        self._expect_char("=")
        if len(path) == 1 and path[0] in self.known_vars:
            value = self._scan_expr_span(";")
            self._expect_char(";")
            return Reassign(name=path[0], value=value)
        value = self._parse_value(";")
        self._expect_char(";")
        if path and path[0] == "msg":
            path = path[1:]
        return Assign(path=path, value=value)

    def _parse_ident_or_keyword_reset(self) -> str:
        """Like _parse_ident, but allows the single keyword 'reset' through
        (it's reserved everywhere else, but `.reset()` is the one place it's
        syntax, not a name)."""
        if self._looking_at_word("reset"):
            self.pos += len("reset")
            return "reset"
        return self._parse_path_ident()

    def _parse_if(self, stmt_parser):
        self._advance_word("if")
        cond = self._scan_expr_span("{")
        self._expect_char("{")
        then_body = self._parse_block_until("}", stmt_parser)
        self._expect_char("}")
        else_body: list = []
        save = self.pos
        self._skip_ws()
        if self._looking_at_word("else"):
            self._advance_word("else")
            self._skip_ws()
            if self._looking_at_word("if"):
                else_body = [self._parse_if(stmt_parser)]
            else:
                self._expect_char("{")
                else_body = self._parse_block_until("}", stmt_parser)
                self._expect_char("}")
        else:
            self.pos = save
        return If(cond=cond, then_body=then_body, else_body=else_body)

    def _parse_repeat(self):
        self._advance_word("repeat")
        self._skip_ws()
        count = None
        if self._peek_char() != "{":
            count = self._scan_expr_span("{")
        self._expect_char("{")
        body = self._parse_block_until("}", self._parse_stmt)
        self._expect_char("}")
        return Repeat(count=count, body=body)

    def _parse_for(self):
        self._advance_word("for")
        var = self._parse_ident()
        self._advance_word("in")
        self._skip_ws()
        dots = span.scan_until_token(self.text, self.pos, "..")
        start_text = self.text[self.pos : dots].strip()
        if not start_text:
            raise ScriptError("expected a range start", self.pos)
        start_span = ExprSpan(start_text)
        self.pos = dots + 2
        self._skip_ws()
        end = None
        if self._looking_at_word("inf"):
            self._advance_word("inf")
        else:
            end = self._scan_expr_span("{")
        self._expect_char("{")
        self.known_vars.add(var)
        body = self._parse_block_until("}", self._parse_stmt)
        self._expect_char("}")
        return For(var=var, start=start_span, end=end, body=body)

    def _parse_send(self):
        # hz/dur/value are all optional and fully order-independent - each
        # loop iteration takes whichever of them comes next, so `send true
        # hz 5 dur 4.5;`, `send hz 5 true dur 4.5;`, and `send hz 5 dur 4.5
        # true;` all mean the same thing. A plain-expression value always
        # stops at the next `hz`/`dur` keyword as well as `;`, so it never
        # swallows a modifier that happens to follow it.
        self._advance_word("send")
        hz = None
        dur_kind = "inf"
        dur_value = None
        value = None
        while True:
            self._skip_ws()
            if self._peek_char() in (";", ""):
                break
            if self._looking_at_word("hz"):
                hz = self._parse_hz_modifier()
                continue
            if self._looking_at_word("dur"):
                dur_kind, dur_value = self._parse_dur_modifier()
                continue
            if value is not None:
                raise ScriptError("a `send` statement can only have one value", self.pos)
            value = self._parse_value(";", stop_at_send_modifiers=True)
        self._expect_char(";")
        return Send(hz=hz, dur_kind=dur_kind, dur_value=dur_value, value=value)

    def _parse_hz_modifier(self) -> float | None:
        self._advance_word("hz")
        self._skip_ws()
        if self._looking_at_word("inf"):
            self._advance_word("inf")
            return None
        return self._parse_number()

    def _parse_dur_modifier(self) -> tuple:
        self._advance_word("dur")
        self._skip_ws()
        if self._looking_at_word("inf"):
            self._advance_word("inf")
            return "inf", None
        num = self._parse_number()
        unit = self._match_unit_suffix()
        if unit == "t":
            self.pos += 1
            return "tick", num
        if unit is not None:
            self.pos += len(unit)
        mult = {"s": 1.0, "m": 60.0, "ms": 0.001, None: 1.0}[unit]
        return "wall", num * mult

    def _word_at(self, pos: int, word: str) -> bool:
        end = pos + len(word)
        if self.text[pos:end] != word:
            return False
        return end >= len(self.text) or not (self.text[end].isalnum() or self.text[end] == "_")

    def _scan_send_leading_value_span(self) -> ExprSpan:
        """Like _scan_expr_span(";"), but also stops right before a
        subsequent `hz`/`dur` modifier keyword - needed for the
        value-first `send VALUE hz H dur D;` form, where a plain
        expression value isn't necessarily followed straight by `;`."""
        self._skip_ws()
        start = self.pos
        text = self.text
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
            if c in "([":
                depth += 1
            elif depth == 0 and c == ";":
                break
            elif c in ")]":
                if depth == 0:
                    raise ScriptError(f"unmatched '{c}'", i)
                depth -= 1
            elif depth == 0 and c.isspace():
                j = i
                while j < len(text) and text[j].isspace():
                    j += 1
                if self._word_at(j, "hz") or self._word_at(j, "dur"):
                    break
            i += 1
        if i >= len(text):
            raise ScriptError("unexpected end of input while scanning a send value", start)
        value_text = text[start:i].strip()
        if not value_text:
            raise ScriptError("expected a value", start)
        self.pos = i
        return ExprSpan(value_text)

    # -- values (assignment RHS / array elements / send's bare value) ---

    def _parse_value(self, stop_chars: str, stop_at_send_modifiers: bool = False):
        self._skip_ws()
        if self._looking_at_word("default"):
            self._advance_word("default")
            return Default()
        c = self._peek_char()
        # A leading '"' is NOT special-cased here (unlike default/[/live,
        # which really are distinct grammar forms) - a string literal is
        # just an ordinary atom in expr.py now, so "map" and "prefix_" +
        # suffix both fall through to the plain ExprSpan path below;
        # span.scan_span already treats quoted text as opaque, so a
        # stop_char or a `.` inside the string can't be mistaken for the
        # span's own terminator.
        if c == "[":
            return self._parse_array_lit()
        if self._looking_at_word("live"):
            return self._parse_live_block(stop_chars, stop_at_send_modifiers)
        bang_call = self._try_parse_bang_call()
        if bang_call is not None:
            return bang_call
        return self._scan_value_span(stop_chars, stop_at_send_modifiers)

    def _scan_value_span(self, stop_chars: str, stop_at_send_modifiers: bool) -> ExprSpan:
        """The plain-expression fallback shared by _parse_value's own tail
        and live's one-line shorthand below - scans to whichever
        terminator applies in this context (a stop char, or the sendvalue
        scanner that also stops before a trailing hz/dur keyword)."""
        if stop_at_send_modifiers:
            return self._scan_send_leading_value_span()
        span_end = span.scan_span(self.text, self.pos, stop_chars)
        text = self.text[self.pos : span_end].strip()
        if not text:
            raise ScriptError("expected a value", self.pos)
        self.pos = span_end
        return ExprSpan(text)

    def _parse_array_lit(self):
        self.pos += 1  # '['
        elements = []
        self._skip_ws()
        if self._peek_char() != "]":
            elements.append(self._parse_value(",]"))
            self._skip_ws()
            while self._peek_char() == ",":
                self.pos += 1
                elements.append(self._parse_value(",]"))
                self._skip_ws()
        self._expect_char("]")
        return ArrayLit(elements)

    def _parse_live_block(self, stop_chars: str, stop_at_send_modifiers: bool):
        self._advance_word("live")
        if self._peek_char() == "{":
            self._expect_char("{")
            saved_known = self.known_vars
            self.known_vars = set()  # live blocks can only write their own locals
            body = self._parse_block_until_return()
            self._advance_word("return")
            ret = self._scan_expr_span(";")
            self._expect_char(";")
            self._expect_char("}")
            self.known_vars = saved_known
            return LiveBlock(body=body, return_expr=ret)
        # shorthand: `live <expr>;` desugars to `live { return <expr>; };` -
        # same LiveBlock AST (empty body, no locals), for the common case
        # of one live expression that needs no locals or branching.
        ret = self._scan_value_span(stop_chars, stop_at_send_modifiers)
        return LiveBlock(body=[], return_expr=ret)

    def _parse_block_until_return(self) -> list:
        stmts = []
        while True:
            self._skip_ws()
            if self._looking_at_word("return"):
                break
            stmts.append(self._parse_live_stmt())
        return stmts

    def _parse_live_stmt(self):
        self._skip_ws()
        if self._looking_at_word("var"):
            return self._parse_var_decl()
        if self._looking_at_word("if"):
            return self._parse_if(self._parse_live_stmt)
        name = self._parse_ident()
        self._expect_char("=")
        value = self._scan_expr_span(";")
        self._expect_char(";")
        if name not in self.known_vars:
            raise ScriptError(
                f"a live block can't write to '{name}' - only its own locals "
                "declared with var inside this same block (no outer vars, no message fields)",
                self.pos,
            )
        return Reassign(name=name, value=value)

    def _try_parse_bang_call(self):
        """`name!(args)` - live-call sugar, recognized only as the WHOLE
        value (not nested inside a larger expression - `data = 1 +
        sin!(t);` isn't supported, matching how the old linear(...) sugar
        was also whole-RHS-only). Desugars to `live { return name(args);
        }` - or, for the fixed set of TIME_SHAPED_FUNCTIONS (linear/
        square/triangle/sawtooth/damped_wave), `live { return name(_t,
        args); }`, injecting the elapsed-time argument those specific
        builtins expect first so the call site keeps the ergonomic shape
        it would otherwise lose (`linear!(20, 30, 10s)`, not `linear!(_t,
        20, 30, 10s)`). name isn't validated here - an unknown function
        surfaces the same "unknown function" error at eval time as any
        other bad call would, structural parsing doesn't need to know the
        exact whitelist. Backtracks fully on any mismatch (no identifier,
        no '!', '!' not followed directly by '(') so the caller can fall
        through to a plain value."""
        save = self.pos
        self._skip_ws()
        start = self.pos
        if self.pos >= len(self.text) or not (self.text[self.pos].isalpha() or self.text[self.pos] == "_"):
            self.pos = save
            return None
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        name = self.text[start : self.pos]
        if self.pos >= len(self.text) or self.text[self.pos] != "!":
            self.pos = save
            return None
        self.pos += 1  # '!'
        if self.pos >= len(self.text) or self.text[self.pos] != "(":
            self.pos = save
            return None
        self.pos += 1  # '('
        args_end = span.scan_span(self.text, self.pos, ")")
        args_text = self.text[self.pos : args_end].strip()
        self.pos = args_end
        self._expect_char(")")
        if name in TIME_SHAPED_FUNCTIONS:
            call_text = f"{name}(_t, {args_text})" if args_text else f"{name}(_t)"
        else:
            call_text = f"{name}({args_text})"
        return LiveBlock(body=[], return_expr=ExprSpan(call_text))


def parse(source: str) -> Program:
    return Parser(source).parse_program()
