"""Statement AST nodes. Expression spans retain source locations and compiled trees."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExprSpan:
    text: str
    pos: int | None = field(default=None, compare=False)
    node: object = field(default=None, compare=False, repr=False)
    generated: bool = field(default=False, compare=False, repr=False)


@dataclass
class Default:
    pass


@dataclass
class ArrayLit:
    elements: list  # list[Value]


@dataclass
class LiveBlock:
    body: list  # list[VarDecl | Reassign | StaticDecl | StaticReassign | If] - no Send/loops/msg-field writes
    return_expr: ExprSpan


# Value = ExprSpan | Default | ArrayLit | LiveBlock - a bare string literal
# ("map") is just an ExprSpan whose text happens to be a quoted atom;
# expr.py evaluates it to a Python str like any other expression.


@dataclass
class VarDecl:
    name: str
    value: ExprSpan


@dataclass
class ExternDecl:
    """`extern name;` / `extern name = expr;` - declares a parameter the
    *host* supplies (ScriptRun's `external_params`), not something the
    script computes. Lives in its own namespace, separate from `self.vars`
    - readable from the host side independent of anything the script
    does with its own vars (`run.externs["name"]`), and read-only from
    the script's own side (no bare `name = expr;` reassignment - see
    parser.py's extern_names / is_extern check). `default` is used only
    when the host doesn't supply a value for `name`; with no default and
    no host-supplied value, resolving it is a new_run()-time error, not
    a lazy one deep in a live block."""

    name: str
    default: ExprSpan | None


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
class Seed:
    """`seed(expr);` - reseeds the run-owned random generator used by every
    random-distribution builtin, for reproducible runs. Top-level only,
    not valid inside a `live` block (reseeding every tick would make a
    rand_walk!/brown_motion! replay the same step every time, defeating
    the point of either)."""

    value: ExprSpan


@dataclass
class Wait:
    """`wait <duration>;` - a gap in the schedule: paces exactly like a
    one-tick `send` (the same StepResult.hz-based real-time cadence),
    but publishes nothing. `<duration>` is a duration literal, normalized
    to seconds at parse time like any other - never `Nt` (ticks), since
    a bare `wait` has no surrounding hz to convert a tick count against."""

    duration: float | ExprSpan


@dataclass
class Reassign:
    name: str
    value: ExprSpan


@dataclass
class StaticDecl:
    """`static name = expr;` - only valid at the top level of a `live`
    block's body (not nested inside if/else). `value` evaluates once,
    at the same moment the block's own `_t` is (re)created - not once
    per tick, unlike everything else in the block's body. Compiled out
    of the body entirely into the live binding's own init step; see
    compiler.py's LiveBinding.static_inits."""

    name: str
    value: ExprSpan


@dataclass
class StaticReassign:
    """`name = expr;` inside a `live` block, where `name` was declared
    with `static` rather than `var` - same surface syntax as Reassign,
    a distinct node only so the compiler can route the write into the
    live binding's persistent static storage instead of the ordinary
    per-tick-fresh local scope."""

    name: str
    value: ExprSpan


@dataclass
class Block:
    """A sequence of statements standing in for a single one - what a
    `func` declaration (an empty Block: it has no runtime effect of its
    own) and a macro call (a Block of that macro's own body, expanded
    and parameter-substituted at parse time) both compile to. Nothing
    downstream needs to know a macro was ever involved: compiler.py
    just compiles each statement inside in order, exactly as if they'd
    been written out by hand at that spot."""

    body: list


@dataclass
class VarIndexAssign:
    """`name(.ident | [expr])+ = expr;`, where `name` is an existing var -
    mutates the var's own array/object value in place, rather than
    writing a message field. Message-field paths and var-index
    assignment share the same `.`/`[` syntax at the statement level;
    which one a given statement is gets disambiguated by whether the
    leading name is a declared var, not by any different syntax."""

    name: str
    accessors: list  # list[tuple] - ("dot", str) | ("index", ExprSpan)
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
    hz: float | ExprSpan | None  # expressions resolve once on entering send
    dur_kind: str  # "wall" | "tick" | "inf"
    dur_value: float | ExprSpan | None
    value: object | None  # Value | None - for `send [..]` / `send a;` sugar


@dataclass
class Program:
    body: list = field(default_factory=list)
    # Legacy declaration metadata, not a type restriction.
    duration_vars: frozenset = field(default_factory=frozenset)
    # ExternDecl nodes, collected in source order (same nodes also sit in
    # `body` at whatever point they were declared - compiler.py compiles
    # them there to a no-op, since resolving one is a new_run()-time
    # concern, not an instruction to run in sequence). This is the
    # convenient side-channel vm.py's compile_script() actually reads.
    externs: list = field(default_factory=list)
