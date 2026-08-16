"""Plain dataclasses for the statement-grammar AST. Structural only - no
evaluation happens while building or holding these; expression leaves stay
as verbatim source-text spans (ExprSpan), sliced and handed to
expr.evaluate() only at VM render time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExprSpan:
    text: str


@dataclass
class Default:
    pass


@dataclass
class ArrayLit:
    elements: list  # list[Value]


@dataclass
class LiveBlock:
    body: list  # list[VarDecl | Assign(local var only) | If] - no Send/loops/msg-field writes
    return_expr: ExprSpan


# Value = ExprSpan | Default | ArrayLit | LiveBlock - a bare string literal
# ("map") is just an ExprSpan whose text happens to be a quoted atom;
# expr.py evaluates it to a Python str like any other expression.


@dataclass
class VarDecl:
    name: str
    value: ExprSpan


@dataclass
class TimerDecl:
    """`var name = timer();` / `var name = latching_timer();` - syntactically
    a VarDecl but semantically distinct: creates VM timer state, not a plain
    numeric var, so the parser recognizes this call form specially."""

    name: str
    kind: str  # "eager" | "latching"


@dataclass
class TimerReset:
    """`name.reset();` - name is `t`, `_t`, or a var previously bound by TimerDecl."""

    name: str


@dataclass
class Reassign:
    name: str
    value: ExprSpan


@dataclass
class Assign:
    path: list  # list[str] - dotted field path, e.g. ["linear", "x"]
    value: object  # Value


@dataclass
class If:
    cond: ExprSpan
    then_body: list
    else_body: list  # empty if no else; `else if` = a single If node inside else_body


@dataclass
class Repeat:
    count: ExprSpan | None  # None = infinite
    body: list


@dataclass
class For:
    var: str
    start: ExprSpan
    end: ExprSpan | None  # None = `inf`
    body: list


@dataclass
class Send:
    hz: float | None  # None -> MAX_HZ (compiler clamps)
    dur_kind: str  # "wall" | "tick" | "inf"
    dur_value: float | None  # None iff dur_kind == "inf"
    value: object | None  # Value | None - for `send [..]` / `send a;` sugar


@dataclass
class Program:
    body: list = field(default_factory=list)
