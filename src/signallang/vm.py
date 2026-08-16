"""ScriptRun - the stepped interpreter. Executes the flat instruction tape
one step() at a time. No `time` import anywhere in this file: `t`/`_t` are
counted (advanced only inside a Send tick, by 1/hz), never read off a real
clock, and step() never blocks. Real-time pacing between step() calls is
entirely the caller's job (see realtime.py for the one opt-in convenience
driver, or a host application's own timer/event-loop callback).
"""

import copy
from dataclasses import dataclass

from . import expr
from .ast_nodes import ArrayLit, Default, ExprSpan, StringLit
from .compiler import (
    MAX_HZ,
    CreateTimer,
    Jump,
    JumpIfFalse,
    LiveBinding,
    LiveIf,
    LiveSetVar,
    ResetTimer,
    SendInstr,
    SetField,
    SetVar,
    compile_program,
)
from .errors import ScriptError
from .parser import parse


@dataclass
class TimerState:
    kind: str  # "eager" | "latching"
    offset: float | None  # None only while a latching timer is unstarted


@dataclass
class StepResult:
    value: dict
    hz: float


class ScriptRun:
    def __init__(self, instructions: list, schema_provider=None):
        self.instructions = instructions
        self.schema_provider = schema_provider

        self.ip = 0
        self.halted = False
        self.master_t = 0.0
        self.vars: dict = {}
        self.timers: dict = {}  # name -> TimerState; "__live_*" names are private per-binding _t's
        self.msg: dict = {}
        self.live_bindings: dict = {}  # path tuple -> LiveBinding

        self._send_ip: int | None = None
        self._send_ticks_done = 0
        self._send_start_t = 0.0

    # -- stepping -----------------------------------------------------------

    def step(self) -> StepResult | None:
        if self.halted:
            return None
        while True:
            if self.ip >= len(self.instructions):
                self.halted = True
                return None
            instr = self.instructions[self.ip]
            if isinstance(instr, SendInstr):
                return self._do_send_tick(instr)
            self._exec_instant(instr)

    def _exec_instant(self, instr) -> None:
        if isinstance(instr, SetVar):
            self.vars[instr.name] = self._eval(instr.expr)
            self.ip += 1
        elif isinstance(instr, SetField):
            self._apply_field(instr.path, instr.value)
            self.ip += 1
        elif isinstance(instr, CreateTimer):
            offset = self.master_t if instr.kind == "eager" else None
            self.timers[instr.name] = TimerState(kind=instr.kind, offset=offset)
            self.ip += 1
        elif isinstance(instr, ResetTimer):
            ts = self.timers[instr.name]
            ts.offset = self.master_t if ts.kind == "eager" else None
            self.ip += 1
        elif isinstance(instr, Jump):
            self.ip = instr.target
        elif isinstance(instr, JumpIfFalse):
            self.ip = instr.target if self._eval(instr.cond) == 0.0 else self.ip + 1
        else:
            raise ScriptError(f"internal: unknown instruction {instr!r}")

    def _do_send_tick(self, instr: SendInstr) -> StepResult:
        if self._send_ip != self.ip:
            self._send_ip = self.ip
            self._send_ticks_done = 0
            self._send_start_t = self.master_t

        value = self._current_msg()
        period = 1.0 / instr.hz
        self.master_t += period
        self._send_ticks_done += 1

        done = self._send_is_done(instr)
        if done:
            self.ip += 1
            self._send_ip = None

        return StepResult(value=value, hz=instr.hz)

    def _send_is_done(self, instr: SendInstr) -> bool:
        if instr.dur_kind == "inf":
            return False
        if instr.dur_kind == "tick":
            assert instr.dur_value is not None  # guaranteed by the compiler for dur_kind="tick"
            return self._send_ticks_done >= instr.dur_value
        if instr.dur_kind == "wall":
            assert instr.dur_value is not None  # guaranteed by the compiler for dur_kind="wall"
            return (self.master_t - self._send_start_t) >= instr.dur_value - 1e-9
        raise ScriptError(f"internal: unknown dur_kind {instr.dur_kind!r}")

    # -- expression evaluation -----------------------------------------------

    def _timer_value(self, ts: TimerState) -> float:
        if ts.offset is None:  # latching, unstarted - first read latches now
            ts.offset = self.master_t
        return self.master_t - ts.offset

    def _flat_scope(self, live_timer_name: str | None = None) -> dict:
        scope = dict(self.vars)
        scope["t"] = self.master_t
        for name, ts in self.timers.items():
            if name.startswith("__live_"):
                continue
            scope[name] = self._timer_value(ts)
        if live_timer_name is not None:
            scope["_t"] = self._timer_value(self.timers[live_timer_name])
        return scope

    def _eval(self, span: ExprSpan, live_timer_name: str | None = None, extra: dict | None = None) -> float:
        scope = self._flat_scope(live_timer_name)
        if extra:
            scope.update(extra)
        return expr.evaluate(span.text, scope)

    # -- fields -----------------------------------------------------------

    def _apply_field(self, path: list, value) -> None:
        key = tuple(path)
        if isinstance(value, LiveBinding):
            self.live_bindings[key] = value
            return
        self.live_bindings.pop(key, None)
        if isinstance(value, ExprSpan):
            self._set_path(path, self._eval(value))
        elif isinstance(value, StringLit):
            self._set_path(path, value.value)
        elif isinstance(value, Default):
            self._require_schema(path, "`default`")
            self._set_path(path, self.schema_provider.default_at(path))
        elif isinstance(value, ArrayLit):
            self._require_schema(path, "positional array fill")
            field_names = self.schema_provider.fields_at(path)
            if len(field_names) != len(value.elements):
                raise ScriptError(
                    f"positional fill length mismatch at '{'.'.join(path) or '<msg>'}': "
                    f"expected {len(field_names)} field(s), got {len(value.elements)}"
                )
            for name, elem in zip(field_names, value.elements):
                self._apply_field(path + [name], elem)
        else:
            raise ScriptError(f"internal: unexpected field value {value!r}")

    def _require_schema(self, path: list, what: str) -> None:
        if self.schema_provider is None:
            raise ScriptError(f"{what} at '{'.'.join(path) or '<msg>'}' needs a schema_provider")

    def _set_path(self, path: list, value) -> None:
        if not path:
            self.msg = value if isinstance(value, dict) else {"data": value}
            return
        cur = self.msg
        for seg in path[:-1]:
            nxt = cur.get(seg)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[seg] = nxt
            cur = nxt
        cur[path[-1]] = value

    def _current_msg(self) -> dict:
        result = copy.deepcopy(self.msg)
        for path, binding in self.live_bindings.items():
            value = self._eval_live_block(binding)
            if not path:
                result = value if isinstance(value, dict) else {"data": value}
                continue
            cur = result
            for seg in path[:-1]:
                nxt = cur.get(seg)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[seg] = nxt
                cur = nxt
            cur[path[-1]] = value
        return result

    def _eval_live_block(self, binding: LiveBinding) -> float:
        local_vars: dict = {}

        def eval_span(span: ExprSpan) -> float:
            return self._eval(span, live_timer_name=binding.timer_name, extra=local_vars)

        def exec_stmt(s) -> None:
            if isinstance(s, LiveSetVar):
                local_vars[s.name] = eval_span(s.expr)
            elif isinstance(s, LiveIf):
                branch = s.then_body if eval_span(s.cond) != 0.0 else s.else_body
                for x in branch:
                    exec_stmt(x)
            else:
                raise ScriptError(f"internal: unexpected live-block statement {s!r}")

        for s in binding.body:
            exec_stmt(s)
        return eval_span(binding.return_expr)


class CompiledScript:
    def __init__(self, instructions: list, schema_provider=None):
        self.instructions = instructions
        self.schema_provider = schema_provider

    def new_run(self) -> ScriptRun:
        return ScriptRun(self.instructions, schema_provider=self.schema_provider)


def compile_script(source: str, schema_provider=None, max_hz: float = MAX_HZ) -> CompiledScript:
    program = parse(source)
    instructions = compile_program(program, max_hz=max_hz)
    return CompiledScript(instructions, schema_provider=schema_provider)
