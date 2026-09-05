"""ScriptRun - the stepped interpreter. Executes the flat instruction tape
one step() at a time. No `time` import anywhere in this file: `t`/`_t` are
counted (advanced only inside a Send tick, by 1/hz), never read off a real
clock, and step() never blocks. Real-time pacing between step() calls is
entirely the caller's job (see realtime.py for the one opt-in convenience
driver, or a host application's own timer/event-loop callback).
"""

from __future__ import annotations

import pathlib
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import expr
from .ast_nodes import ArrayLit, Default, ExprSpan
from .compiler import (
    MAX_HZ,
    ClearVar,
    CreateTimer,
    Jump,
    JumpIfFalse,
    LiveBinding,
    LiveIf,
    LiveSetVar,
    LiveStaticSetVar,
    LoopValue,
    ResetTimer,
    SeedInstr,
    SendInstr,
    SetField,
    SetVar,
    SetVarIndex,
    WaitInstr,
    compile_program,
)
from .errors import ScriptError
from .parser import parse
from .resources import DEFAULT_OPERATION_BUDGET, Budget, clone_value, positive_integer, positive_number

# step() runs a plain Python `while True:` across instructions that don't
# themselves yield (everything except SendInstr/WaitInstr - see
# _check_no_mid_body_infinite_send in compiler.py for the one narrow case
# caught at compile time). A script with no send/wait at all inside an
# unconditional loop - `repeat { var x = x + 1; }` - compiles cleanly and
# would otherwise hang the host's thread forever on the first step() call.
# This is a per-step ceiling on non-yielding instructions, not a limit on
# how many sends a script can produce overall.
DEFAULT_STEP_INSTRUCTION_BUDGET = 100_000


@dataclass
class TimerState:
    kind: str  # "eager" | "latching"
    offset: float | None  # None only while a latching timer is unstarted


@dataclass
class StepResult:
    value: dict | None  # None iff sent is False (a wait tick - nothing to publish)
    hz: float
    sent: bool = True
    timestamp: float = 0.0
    sequence: int = 0

    @property
    def delay(self) -> float:
        """Simulated seconds until the caller should take the next step."""
        return 1.0 / self.hz


class _Scope(Mapping):
    """Resolve only identifiers actually read by the expression evaluator."""

    def __init__(self, run, live_timer_name=None, extra=None):
        self.run = run
        self.live_timer_name = live_timer_name
        self.extra = extra or {}
        self.snapshot = None

    def __iter__(self):
        names = set(self.run.vars) | set(self.run.externs) | set(self.run.msg) | set(self.extra)
        names.update(name for name in self.run.timers if not name.startswith("__live_"))
        names.update(("t", "msg"))
        if self.live_timer_name is not None:
            names.add("_t")
        return iter(names)

    def __len__(self):
        return sum(1 for _ in self)

    def __contains__(self, name):
        return (
            name in self.extra
            or name in self.run.vars
            or name in self.run.externs
            or name in ("t", "msg")
            or (name == "_t" and self.live_timer_name is not None)
            or (name in self.run.timers and not name.startswith("__live_"))
            or (name in self.run.msg and name not in expr.RESERVED_NAMES)
        )

    def __getitem__(self, name):
        run = self.run
        if name in self.extra:
            return self.extra[name]
        if name == "t":
            return run.master_t
        if name == "_t" and self.live_timer_name is not None:
            return run._timer_value(run.timers[self.live_timer_name])
        if name in run.externs:
            return clone_value(run.externs[name], run._budget)
        if name in run.timers and not name.startswith("__live_"):
            return run._timer_value(run.timers[name])
        if name in run.vars:
            return run.vars[name]
        if name == "msg" or (name in run.msg and name not in expr.RESERVED_NAMES):
            if self.snapshot is None:
                self.snapshot = clone_value(run.msg, run._budget)
            return self.snapshot if name == "msg" else self.snapshot[name]
        raise KeyError(name)


class ScriptRun:
    def __init__(
        self,
        instructions: list,
        schema_provider=None,
        duration_vars: frozenset = frozenset(),
        externs: Sequence = (),
        external_params: dict | None = None,
        step_instruction_budget: int = DEFAULT_STEP_INSTRUCTION_BUDGET,
        operation_budget: int = DEFAULT_OPERATION_BUDGET,
        seed: float | str | None = None,
    ):
        self.instructions = instructions
        self.schema_provider = schema_provider
        self.duration_vars = duration_vars
        self.step_instruction_budget = positive_integer(step_instruction_budget, "step_instruction_budget")
        self.operation_budget = positive_integer(operation_budget, "operation_budget")
        self._budget = Budget(self.operation_budget)
        if seed is not None:
            if type(seed) not in (int, float, str):
                raise ScriptError("seed must be a number or string")
            clone_value(seed)
        self.rng = random.Random(seed)
        self.sequence = 0

        self.ip = 0
        self.halted = False
        self.master_t = 0.0
        self.vars: dict = {}
        self.timers: dict = {}  # name -> TimerState; "__live_*" names are private per-binding _t's
        # timer_name -> {static var name -> current value}; persists across
        # every tick's re-evaluation of that live binding (mutated in place,
        # same "no write-back needed" trick as self.vars), reinitialized
        # from scratch each time the binding's own LiveBinding is (re)bound
        # in _apply_field - same key, same reset schedule as that binding's
        # own _t, deliberately, rather than tracking a second identity.
        self.statics: dict = {}
        # With a schema, the message starts fully defaulted - the same
        # tree default_at([]) already builds for an explicit `msg =
        # default;` statement, just applied automatically at run start
        # instead of requiring that statement. A field assignment later
        # in the script overwrites only that field; a bare `send;` with
        # no assignments at all still sends a complete, schema-shaped
        # message. Without a schema there is nothing to default from, so
        # the message starts empty exactly as before.
        self.msg: dict = (
            clone_value(schema_provider.default_at([]), self._budget) if schema_provider is not None else {}
        )
        self.live_bindings: dict = {}  # path tuple -> LiveBinding

        self._send_ip: int | None = None
        self._send_ticks_done = 0
        self._send_start_t = 0.0
        self._send_hz = MAX_HZ
        self._send_duration: float | None = None

        # Resolved once, upfront, before any instruction runs - see
        # ExternDecl (ast_nodes.py) for why this is its own dict rather
        # than folded into self.vars: it's the host's own namespace,
        # readable independent of anything the script does with its own
        # vars (run.externs["name"]), and never write-accessible from
        # the script side (parser.py rejects assigning to an extern
        # name). A default can't reference another extern - resolved in
        # declaration order, into self.externs as it's built, so a
        # later default technically *could* see an earlier extern's
        # resolved value, but that's not a guarantee to rely on.
        self.externs: dict = {}
        params = external_params or {}
        for decl in externs:
            if decl.name in params:
                self.externs[decl.name] = clone_value(params[decl.name], self._budget)
            elif decl.default is not None:
                self.externs[decl.name] = clone_value(self._eval(decl.default), self._budget)
            else:
                raise ScriptError(
                    f"extern '{decl.name}' has no default and wasn't supplied - "
                    "pass it via new_run(external_params={...}) or give the "
                    "declaration a default value"
                )

    # -- stepping -----------------------------------------------------------

    def step(self) -> StepResult | None:
        self._budget = Budget(self.operation_budget)
        try:
            return self._step()
        except (ScriptError, ValueError, TypeError, ArithmeticError, KeyError, RecursionError) as error:
            self.halted = True
            if isinstance(error, ScriptError):
                raise
            raise ScriptError(str(error)) from None

    def __iter__(self):
        return self

    def __next__(self):
        result = self.step()
        if result is None:
            raise StopIteration
        return result

    def collect(self, ticks: int) -> list[StepResult]:
        """Consume at most ticks steps, including wait events."""
        positive_integer(ticks, "ticks")
        result = []
        for _ in range(ticks):
            item = self.step()
            if item is None:
                break
            result.append(item)
        return result

    def _step(self) -> StepResult | None:
        if self.halted:
            return None
        executed = 0
        while True:
            if self.ip >= len(self.instructions):
                self.halted = True
                return None
            instr = self.instructions[self.ip]
            if isinstance(instr, SendInstr):
                return self._do_send_tick(instr)
            if isinstance(instr, WaitInstr):
                return self._do_wait_tick(instr)
            executed += 1
            if executed > self.step_instruction_budget:
                raise ScriptError(
                    f"step() executed more than {self.step_instruction_budget} instructions "
                    "without a `send` or `wait` - likely an infinite loop with neither inside "
                    "it (e.g. `repeat { var x = x + 1; }`). Pass a larger step_instruction_budget "
                    "to new_run() if this is intentional."
                )
            self._exec_instant(instr)

    def _exec_instant(self, instr) -> None:
        if isinstance(instr, LoopValue):
            value = self._eval(instr.expr)
            if not expr.is_number(value) or int(value) != value or (instr.nonnegative and value < 0):
                raise ScriptError("loop bounds must be whole numbers; repeat count must be nonnegative", instr.expr.pos)
            self.vars[instr.name] = int(value)
            self.ip += 1
        elif isinstance(instr, ClearVar):
            self.vars.pop(instr.name, None)
            self.ip += 1
        elif isinstance(instr, SeedInstr):
            value = self._eval(instr.value)
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ScriptError("seed() needs a number or string")
            self.rng.seed(value)
            self.ip += 1
        elif isinstance(instr, SetVar):
            self.vars[instr.name] = clone_value(self._eval(instr.expr), self._budget)
            self.ip += 1
        elif isinstance(instr, SetVarIndex):
            self._apply_var_index(instr.name, instr.accessors, instr.expr)
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
            self.ip = self.ip + 1 if expr.is_truthy(self._eval(instr.cond)) else instr.target
        else:
            raise ScriptError(f"internal: unknown instruction {instr!r}")

    def _do_send_tick(self, instr: SendInstr) -> StepResult:
        if self._send_ip != self.ip:
            self._send_ip = self.ip
            self._send_ticks_done = 0
            self._send_start_t = self.master_t
            hz = self._eval(instr.hz) if isinstance(instr.hz, ExprSpan) else instr.hz
            self._send_hz = min(positive_number(hz, "hz"), instr.max_hz)
            duration = self._eval(instr.dur_value) if isinstance(instr.dur_value, ExprSpan) else instr.dur_value
            if duration is not None:
                duration = positive_number(duration, "send duration")
                if instr.dur_kind == "tick" and int(duration) != duration:
                    raise ScriptError("tick count must be a positive whole number")
            self._send_duration = duration

        value = self._current_msg()
        timestamp = self.master_t
        period = 1.0 / self._send_hz
        self._advance_time(period)
        self._send_ticks_done += 1

        done = self._send_is_done(instr)
        if done:
            self.ip += 1
            self._send_ip = None

        result = StepResult(value=value, hz=self._send_hz, timestamp=timestamp, sequence=self.sequence)
        self.sequence += 1
        return result

    def _send_is_done(self, instr: SendInstr) -> bool:
        if instr.dur_kind == "inf":
            return False
        if instr.dur_kind == "tick":
            assert self._send_duration is not None  # guaranteed by the compiler for dur_kind="tick"
            return self._send_ticks_done >= self._send_duration
        if instr.dur_kind == "wall":
            assert self._send_duration is not None  # guaranteed by the compiler for dur_kind="wall"
            return (self._send_ticks_done / self._send_hz) >= self._send_duration - min(
                1e-9, self._send_duration * 1e-12
            )
        raise ScriptError(f"internal: unknown dur_kind {instr.dur_kind!r}")

    def _advance_time(self, delay):
        import math

        next_time = self.master_t + delay
        if not math.isfinite(next_time) or next_time <= self.master_t:
            raise ScriptError("schedule exceeds simulated clock precision or range")
        self.master_t = next_time

    def _do_wait_tick(self, instr: WaitInstr) -> StepResult:
        hz = instr.hz if instr.duration is None else 1.0 / positive_number(self._eval(instr.duration), "wait duration")
        timestamp = self.master_t
        self._advance_time(1.0 / hz)
        self.ip += 1
        result = StepResult(value=None, hz=hz, sent=False, timestamp=timestamp, sequence=self.sequence)
        self.sequence += 1
        return result

    # -- expression evaluation -----------------------------------------------

    def _timer_value(self, ts: TimerState) -> float:
        if ts.offset is None:  # latching, unstarted - first read latches now
            ts.offset = self.master_t
        return self.master_t - ts.offset

    def _flat_scope(self, live_timer_name=None, extra=None):
        return _Scope(self, live_timer_name, extra)

    def _eval(self, span: ExprSpan, live_timer_name: str | None = None, extra: dict | None = None) -> expr.Value:
        if span.node is None:
            span.node = expr.compile_expression(span.text)
        try:
            return expr.evaluate_node(
                span.node, self._flat_scope(live_timer_name, extra), rng=self.rng, budget=self._budget
            )
        except expr.ExprError as error:
            raise expr.ExprError(error.message, (span.pos or 0) + (0 if span.generated else error.pos or 0)) from None

    # -- var-held array/object mutation --------------------------------

    def _apply_var_index(self, name: str, accessors: list, value_expr: ExprSpan) -> None:
        """`config.retries = 5;` / `arr[0] = 5;`, where `name` is a
        declared var - walks `accessors` into the var's own array/object
        value and mutates it in place (Python lists/dicts are mutable,
        so self.vars[name] still points at the same top-level object
        afterward - no write-back needed). A missing dict key during the
        walk (not the final accessor) auto-vivifies an empty object,
        mirroring how a message-field path already auto-creates
        intermediate dicts; an array index is always bounds-checked
        strictly, never auto-extended."""
        value = clone_value(self._eval(value_expr), self._budget)
        container = self.vars[name]
        for kind, key in accessors[:-1]:
            if kind == "dot":
                if not isinstance(container, dict):
                    raise ScriptError(f"cannot access '.{key}' on '{name}' - not an object at that point")
                if key not in container:
                    # auto-vivify only a genuinely missing key - an
                    # EXISTING value (a list, say, walked further by an
                    # upcoming [index] accessor) must never be clobbered
                    # just for not being a dict itself.
                    container[key] = {}
                container = container[key]
            else:
                container = self._var_index_read(name, container, self._eval(key))
        last_kind, last_key = accessors[-1]
        if last_kind == "dot":
            if not isinstance(container, dict):
                raise ScriptError(f"cannot assign '.{last_key}' on '{name}' - not an object at that point")
            container[last_key] = value
        else:
            self._var_index_write(name, container, self._eval(last_key), value)

    def _var_index_read(self, name: str, container, index_value):
        if isinstance(container, list):
            i = self._var_list_index(name, index_value, len(container))
            return container[i]
        if isinstance(container, dict):
            key = self._var_dict_key(name, index_value)
            if key not in container:
                raise ScriptError(f"'{name}' has no key {key!r} at that point")
            return container[key]
        raise ScriptError(f"cannot index into '{name}' - not an array/object at that point")

    def _var_index_write(self, name: str, container, index_value, value) -> None:
        if isinstance(container, list):
            i = self._var_list_index(name, index_value, len(container))
            container[i] = value
            return
        if isinstance(container, dict):
            container[self._var_dict_key(name, index_value)] = value
            return
        raise ScriptError(f"cannot assign into '{name}' - not an array/object at that point")

    def _var_list_index(self, name: str, index_value, length: int) -> int:
        if not expr.is_number(index_value):
            raise ScriptError(f"array index into '{name}' must be a number")
        i = int(index_value)
        if i != index_value or i < 0 or i >= length:
            raise ScriptError(f"array index {index_value} out of range on '{name}' (length {length})")
        return i

    def _var_dict_key(self, name: str, index_value) -> str:
        if not isinstance(index_value, str):
            raise ScriptError(f"object key into '{name}' must be a string")
        return index_value

    # -- fields -----------------------------------------------------------

    def _apply_field(self, path: list, value) -> None:
        key = tuple(path)
        # The most recent write owns overlapping paths in either direction.
        for old in list(self.live_bindings):
            if old[: len(key)] == key or key[: len(old)] == old:
                binding = self.live_bindings.pop(old)
                self.statics.pop(binding.timer_name, None)
        if isinstance(value, LiveBinding):
            self.live_bindings[key] = value
            # Each static's init evaluates once, right now - the same
            # instant this binding's own _t (re)latches - never per tick.
            statics: dict = {}
            for name, init in value.static_inits:
                statics[name] = clone_value(self._eval(init, value.timer_name, statics), self._budget)
            self.statics[value.timer_name] = statics
            return
        self.live_bindings.pop(key, None)
        if isinstance(value, ExprSpan):
            self._set_path(path, self._eval(value))
        elif isinstance(value, Default):
            self._require_schema(path, "`default`")
            self._set_path(path, self.schema_provider.default_at(path))
        elif isinstance(value, ArrayLit):
            # A schema maps positions to named fields (unchanged). With
            # none, the script author shouldn't need to know or care -
            # this is just published as a plain array value, the same
            # way json {} already needs no schema for its own (self-
            # naming) fields. Whether a schema exists at all is the
            # integration layer's concern, not the script's.
            type_at = getattr(self.schema_provider, "type_at", None)
            array_field = type_at is not None and type_at(path) == "array"
            if self.schema_provider is not None and not array_field:
                field_names = self.schema_provider.fields_at(path)
                if len(field_names) != len(value.elements):
                    raise ScriptError(
                        f"positional fill length mismatch at '{'.'.join(path) or '<msg>'}': "
                        f"expected {len(field_names)} field(s), got {len(value.elements)}"
                    )
                for name, elem in zip(field_names, value.elements):
                    self._apply_field(path + [name], elem)
            else:
                self._set_path(path, [self._resolve_schema_free_element(e, path) for e in value.elements])
        else:
            raise ScriptError(f"internal: unexpected field value {value!r}")

    def _resolve_schema_free_element(self, elem, path: list):
        """Resolves one element of a schema-free ArrayLit to a plain
        value, for embedding in a list rather than writing into self.msg
        under a name the way _apply_field does. Only the element kinds
        that make sense with no name to write to and no schema to
        consult: a plain expression, or another nested array literal.
        `default` has no schema-free meaning (there's no field to ask a
        schema for a zero value at), and a live binding has no
        per-element path to track re-evaluation against - both are a
        clear error here instead of silently doing something surprising."""
        if isinstance(elem, ExprSpan):
            return self._eval(elem)
        if isinstance(elem, ArrayLit):
            return [self._resolve_schema_free_element(e, path) for e in elem.elements]
        if isinstance(elem, Default):
            raise ScriptError(f"'default' at '{'.'.join(path) or '<msg>'}' needs a schema_provider")
        raise ScriptError(
            f"a live value ('live'/'!') isn't supported inside a schema-free array literal "
            f"at '{'.'.join(path) or '<msg>'}'"
        )

    def _require_schema(self, path: list, what: str) -> None:
        if self.schema_provider is None:
            raise ScriptError(f"{what} at '{'.'.join(path) or '<msg>'}' needs a schema_provider")

    def _set_path(self, path: list, value) -> None:
        value = clone_value(value, self._budget)
        if not path:
            message = value if isinstance(value, dict) else {"data": value}
            self._check_schema_type(path, message)
            self.msg = message
            return
        self._check_schema_type(path, value)
        cur = self.msg
        for seg in path[:-1]:
            nxt = cur.get(seg)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[seg] = nxt
            cur = nxt
        cur[path[-1]] = value

    def _check_schema_type(self, path: list, value) -> None:
        """Apply an optional full validator and the adapter's leaf type checks."""
        if self.schema_provider is None:
            return
        validate = getattr(self.schema_provider, "validate_at", None)
        if validate is not None:
            validate(path, value)
        if isinstance(value, dict):
            # Even permissive schemas must reject an object replacing a typed leaf.
            type_at = getattr(self.schema_provider, "type_at", None)
            expected = type_at(path) if type_at is not None else None
            if expected is not None and expected != "object":
                raise ScriptError(f"'{'.'.join(path)}' expects {expected}, got object")
            return
        type_at = getattr(self.schema_provider, "type_at", None)
        if type_at is None:
            return
        expected = type_at(path)
        if expected is None:
            return
        actual = expr.type_name(value)
        if actual != expected:
            raise ScriptError(f"'{'.'.join(path)}' expects {expected}, got {actual}")

    def _current_msg(self) -> dict:
        result = clone_value(self.msg, self._budget)
        for path, binding in self.live_bindings.items():
            value = clone_value(self._eval_live_block(binding), self._budget)
            self._check_schema_type(list(path), value if path or isinstance(value, dict) else {"data": value})
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
        self._check_schema_type([], result)
        return clone_value(result, self._budget)

    def _eval_live_block(self, binding: LiveBinding) -> expr.Value:
        local_vars: dict = {}
        statics = self.statics.get(binding.timer_name, {})

        def eval_span(span: ExprSpan) -> expr.Value:
            return self._eval(span, live_timer_name=binding.timer_name, extra={**statics, **local_vars})

        def exec_stmt(s) -> None:
            if isinstance(s, LiveSetVar):
                local_vars[s.name] = clone_value(eval_span(s.expr), self._budget)
            elif isinstance(s, LiveStaticSetVar):
                statics[s.name] = clone_value(eval_span(s.expr), self._budget)
            elif isinstance(s, LiveIf):
                branch = s.then_body if expr.is_truthy(eval_span(s.cond)) else s.else_body
                for x in branch:
                    exec_stmt(x)
            else:
                raise ScriptError(f"internal: unexpected live-block statement {s!r}")

        for s in binding.body:
            exec_stmt(s)
        return eval_span(binding.return_expr)


class CompiledScript:
    def __init__(
        self,
        instructions: list,
        schema_provider=None,
        duration_vars: frozenset = frozenset(),
        externs: Sequence = (),
    ):
        self.instructions = instructions
        self.schema_provider = schema_provider
        self.duration_vars = duration_vars
        self.externs = list(externs)  # ExternDecl nodes, source order

    def new_run(
        self,
        external_params: dict | None = None,
        step_instruction_budget: int = DEFAULT_STEP_INSTRUCTION_BUDGET,
        operation_budget: int = DEFAULT_OPERATION_BUDGET,
        seed: float | str | None = None,
    ) -> ScriptRun:
        return ScriptRun(
            self.instructions,
            schema_provider=self.schema_provider,
            duration_vars=self.duration_vars,
            externs=self.externs,
            external_params=external_params,
            step_instruction_budget=step_instruction_budget,
            operation_budget=operation_budget,
            seed=seed,
        )


def compile_script(source: str, schema_provider=None, max_hz: float = MAX_HZ) -> CompiledScript:
    try:
        program = parse(source)
    except RecursionError:
        raise ScriptError("script exceeds maximum parser nesting depth") from None
    instructions = compile_program(program, max_hz=max_hz)
    return CompiledScript(
        instructions,
        schema_provider=schema_provider,
        duration_vars=program.duration_vars,
        externs=program.externs,
    )


def compile_file(path, schema_provider=None, max_hz: float = MAX_HZ) -> CompiledScript:
    """Same as compile_script(), reading the source from a file - the
    `.signal` extension is this language's own naming convention (not
    enforced here; a file using a different extension still compiles
    fine, `.signal` is just the recommended name so editors/tooling
    have something to key syntax highlighting off of)."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    return compile_script(text, schema_provider=schema_provider, max_hz=max_hz)
