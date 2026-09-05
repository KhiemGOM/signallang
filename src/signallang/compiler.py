"""AST -> a flat instruction tape. Real lowering of if/repeat/for into
Jump/JumpIfFalse, exactly like a compiler backend targeting a small ISA -
not a declarative timeline with special-cased patterns. `Send` stays one
atomic instruction (see the design plan for why decomposing it further
wouldn't have changed anything at the layer that actually matters).
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass

from . import expr
from .ast_nodes import (
    ArrayLit,
    Assign,
    Block,
    Default,
    ExprSpan,
    ExternDecl,
    For,
    If,
    LiveBlock,
    Program,
    Reassign,
    Repeat,
    Seed,
    Send,
    StaticDecl,
    StaticReassign,
    TimerDecl,
    TimerReset,
    VarDecl,
    VarIndexAssign,
    Wait,
)
from .errors import ScriptError
from .resources import positive_number

MAX_HZ = 50.0  # mirrors dashboard/graph/fake_publisher.py's MAX_RATE_HZ - this
# package's own constant, not a shared dependency on that file.


# -- instructions -------------------------------------------------------


@dataclass
class SetVar:
    name: str
    expr: ExprSpan


@dataclass
class LoopValue:
    name: str
    expr: ExprSpan
    nonnegative: bool = False


@dataclass
class ClearVar:
    name: str


@dataclass
class SetVarIndex:
    name: str
    accessors: list  # list[tuple] - ("dot", str) | ("index", ExprSpan), passed through unchanged from the AST
    expr: ExprSpan


@dataclass
class SetField:
    path: list  # [] means the whole msg
    value: object  # ExprSpan | Default | ArrayLit | LiveBinding


@dataclass
class LiveBinding:
    body: list  # list[LiveSetVar | LiveStaticSetVar | LiveIf]
    return_expr: ExprSpan
    timer_name: str
    # (name, init_expr) pairs, pulled out of body entirely - each evaluates
    # once, at the same moment timer_name's own CreateTimer (re)fires, into
    # persistent per-binding storage the VM keeps keyed by timer_name (that
    # id is already unique per live block and already resets on the right
    # schedule, so it's reused rather than minting a second one).
    static_inits: list


@dataclass
class LiveSetVar:
    name: str
    expr: ExprSpan


@dataclass
class LiveStaticSetVar:
    """Same as LiveSetVar, but the write targets the live binding's
    persistent static storage (self.statics[timer_name] in vm.py)
    instead of the per-tick-fresh local scope."""

    name: str
    expr: ExprSpan


@dataclass
class LiveIf:
    cond: ExprSpan
    then_body: list
    else_body: list


@dataclass
class SendInstr:
    hz: float | ExprSpan
    dur_kind: str  # "wall" | "tick" | "inf"
    dur_value: float | ExprSpan | None
    max_hz: float = MAX_HZ


@dataclass
class SeedInstr:
    value: ExprSpan


@dataclass
class WaitInstr:
    hz: float  # 1 / duration, pre-clamped to max_hz like SendInstr.hz
    duration: ExprSpan | None = None


@dataclass
class Jump:
    target: int


@dataclass
class JumpIfFalse:
    cond: ExprSpan
    target: int


@dataclass
class CreateTimer:
    name: str
    kind: str  # "eager" | "latching"


@dataclass
class ResetTimer:
    name: str


# -- compiler -------------------------------------------------------------


class Compiler:
    def __init__(self, max_hz: float = MAX_HZ):
        self.instrs: list = []
        self.max_hz = positive_number(max_hz, "max_hz")
        self._unique = 0

    def compile(self, program: Program) -> list:
        self._compile_block(program.body)
        self._compile_expressions(self.instrs)
        self._compile_expressions(program.externs)
        return self.instrs

    def _compile_expressions(self, value):
        if isinstance(value, ExprSpan):
            try:
                value.node = expr.compile_expression(value.text)
            except expr.ExprError as error:
                pos = (value.pos or 0) + (0 if value.generated else error.pos or 0)
                raise expr.ExprError(error.message, pos) from None
        elif isinstance(value, (list, tuple)):
            for child in value:
                self._compile_expressions(child)
        elif is_dataclass(value):
            for item in fields(value):
                self._compile_expressions(getattr(value, item.name))

    def _emit(self, instr) -> int:
        self.instrs.append(instr)
        return len(self.instrs) - 1

    def _next_name(self, prefix: str) -> str:
        self._unique += 1
        return f"__{prefix}_{self._unique}"

    def _compile_block(self, stmts: list) -> None:
        for s in stmts:
            self._compile_stmt(s)

    def _compile_stmt(self, s) -> None:
        if isinstance(s, (VarDecl, Reassign)):
            self._emit(SetVar(s.name, s.value))
        elif isinstance(s, VarIndexAssign):
            self._emit(SetVarIndex(s.name, s.accessors, s.value))
        elif isinstance(s, TimerDecl):
            self._emit(CreateTimer(s.name, s.kind))
        elif isinstance(s, TimerReset):
            self._emit(ResetTimer(s.name))
        elif isinstance(s, Assign):
            self._emit(SetField(s.path, self._compile_value(s.value)))
        elif isinstance(s, If):
            self._compile_if(s)
        elif isinstance(s, Repeat):
            self._compile_repeat(s)
        elif isinstance(s, For):
            self._compile_for_core(s.var, s.start, s.end, s.body)
        elif isinstance(s, Send):
            self._compile_send(s)
        elif isinstance(s, Seed):
            self._emit(SeedInstr(s.value))
        elif isinstance(s, Wait):
            self._compile_wait(s)
        elif isinstance(s, Block):
            self._compile_block(s.body)
        elif isinstance(s, ExternDecl):
            pass  # resolved once at new_run() time, not an instruction - see Program.externs
        else:
            raise ScriptError(f"internal: unknown statement {s!r}")

    # -- values (recursive - arrays and live blocks can nest) -----------

    def _compile_value(self, v):
        if isinstance(v, (ExprSpan, Default)):
            return v
        if isinstance(v, ArrayLit):
            return ArrayLit([self._compile_value(e) for e in v.elements])
        if isinstance(v, LiveBlock):
            timer_name = self._next_name("live")
            self._emit(CreateTimer(timer_name, "latching"))
            static_inits = [(s.name, s.value) for s in v.body if isinstance(s, StaticDecl)]
            body = [self._compile_live_stmt(s) for s in v.body if not isinstance(s, StaticDecl)]
            return LiveBinding(body=body, return_expr=v.return_expr, timer_name=timer_name, static_inits=static_inits)
        raise ScriptError(f"internal: unknown value {v!r}")

    def _compile_live_stmt(self, s):
        if isinstance(s, (VarDecl, Reassign)):
            return LiveSetVar(s.name, s.value)
        if isinstance(s, StaticReassign):
            return LiveStaticSetVar(s.name, s.value)
        if isinstance(s, If):
            return LiveIf(
                s.cond,
                [self._compile_live_stmt(x) for x in s.then_body],
                [self._compile_live_stmt(x) for x in s.else_body],
            )
        raise ScriptError(f"internal: unexpected statement inside live block: {s!r}")

    # -- control flow -----------------------------------------------------

    def _compile_if(self, s: If) -> None:
        jf = self._emit(JumpIfFalse(s.cond, -1))
        self._compile_block(s.then_body)
        if s.else_body:
            jmp = self._emit(Jump(-1))
            else_start = len(self.instrs)
            self.instrs[jf] = JumpIfFalse(s.cond, else_start)
            self._compile_block(s.else_body)
            self.instrs[jmp] = Jump(len(self.instrs))
        else:
            self.instrs[jf] = JumpIfFalse(s.cond, len(self.instrs))

    def _compile_repeat(self, s: Repeat) -> None:
        if s.count is None:
            self._check_no_mid_body_infinite_send(s.body, context="an infinite repeat")
            start = len(self.instrs)
            self._compile_block(s.body)
            self._emit(Jump(start))
        else:
            self._compile_for_core(self._next_name("count"), ExprSpan("0"), s.count, s.body, repeat=True)

    def _compile_for_core(
        self, var: str, start_expr: ExprSpan, end_expr: ExprSpan | None, body: list, repeat=False
    ) -> None:
        self._emit(LoopValue(var, start_expr))
        bound = None
        if end_expr is not None:
            bound = self._next_name("bound")
            self._emit(LoopValue(bound, end_expr, nonnegative=repeat))
        loop_start = len(self.instrs)
        jf_idx = None
        if end_expr is None:
            # `..inf` - unconditional back-edge, no guard needed.
            self._check_no_mid_body_infinite_send(body, context="an infinite `for` loop")
        else:
            jf_idx = self._emit(JumpIfFalse(ExprSpan(f"({var}) < ({bound})"), -1))
        self._compile_block(body)
        self._emit(SetVar(var, ExprSpan(f"({var}) + 1")))
        self._emit(Jump(loop_start))
        if jf_idx is not None:
            assert bound is not None
            self.instrs[jf_idx] = JumpIfFalse(self.instrs[jf_idx].cond, len(self.instrs))
            self._emit(ClearVar(var))
            self._emit(ClearVar(bound))

    def _check_no_mid_body_infinite_send(self, body: list, context: str) -> None:
        """`send dur inf;` as a non-last top-level statement of an
        unconditionally-infinite loop body means the loop's own back-edge
        can never be reached - almost certainly a mistake, not a deliberate
        pattern (a conditional `if cond { send dur inf; }` is unaffected,
        since only one branch is unreachable-after, not the whole loop)."""
        for i, stmt in enumerate(body):
            if isinstance(stmt, Send) and stmt.dur_kind == "inf" and i != len(body) - 1:
                raise ScriptError(
                    f"`send dur inf;` inside {context} makes everything after it "
                    "in this loop body unreachable - the loop can never lap again"
                )

    # -- send ---------------------------------------------------------------

    def _compile_send(self, s: Send) -> None:
        if s.value is not None:
            self._emit(SetField([], self._compile_value(s.value)))
        if s.hz is not None and not isinstance(s.hz, ExprSpan):
            # left uncaught, this becomes a raw ZeroDivisionError (or a
            # negative sleep) deep inside the VM/driver - a genuine
            # authoring mistake, not an internal error, so it needs to be
            # a clear ScriptError here instead.
            positive_number(s.hz, "hz")
        if s.dur_value is not None and not isinstance(s.dur_value, ExprSpan):
            positive_number(s.dur_value, "send duration")
            if s.dur_kind == "tick" and int(s.dur_value) != s.dur_value:
                raise ScriptError("tick count must be a positive whole number")
        hz = s.hz if isinstance(s.hz, ExprSpan) else self.max_hz if s.hz is None else min(s.hz, self.max_hz)
        self._emit(SendInstr(hz=hz, dur_kind=s.dur_kind, dur_value=s.dur_value, max_hz=self.max_hz))

    def _compile_wait(self, s: Wait) -> None:
        # unlike SendInstr.hz, never clamped to max_hz - that ceiling
        # exists to cap how often a message actually publishes, and
        # wait never publishes, so clamping here would just silently
        # stretch a short requested gap into a longer one for no reason.
        if isinstance(s.duration, ExprSpan):
            self._emit(WaitInstr(hz=1.0, duration=s.duration))
        else:
            duration = positive_number(s.duration, "wait duration")
            self._emit(WaitInstr(hz=1.0 / duration))


def compile_program(program: Program, max_hz: float = MAX_HZ) -> list:
    return Compiler(max_hz=max_hz).compile(program)
