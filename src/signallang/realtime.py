"""Optional deadline-based drivers. Time and waiting stay outside the VM."""

from __future__ import annotations

import asyncio
import inspect
import time

from .errors import ScriptError
from .resources import positive_integer
from .vm import CompiledScript, ScriptRun


def _prepare(compiled, options, max_ticks, late_policy):
    if max_ticks is not None:
        positive_integer(max_ticks, "max_ticks")
    if late_policy not in ("delay", "catch_up", "error"):
        raise ScriptError("late_policy must be delay, catch_up, or error")
    if isinstance(compiled, ScriptRun):
        if options:
            raise ScriptError("new_run options cannot be supplied with an existing ScriptRun")
        return compiled
    return compiled.new_run(**options)


def _remaining(deadline, late_policy):
    now = time.monotonic()
    if now > deadline:
        if late_policy == "error":
            raise ScriptError("real-time delivery missed its next deadline")
        if late_policy == "delay":
            deadline = now
    return deadline, max(0.0, deadline - now)


def run_realtime(
    compiled: CompiledScript | ScriptRun,
    on_send,
    *,
    cancelled=None,
    max_ticks: int | None = None,
    late_policy: str = "delay",
    **run_options,
) -> None:
    """Deliver serially; callback completion provides bounded backpressure.

    delay rebases after overruns, catch_up retains deadlines, error fails on an
    overrun. cancelled is an optional zero-argument predicate, checked during
    waits at intervals of at most 100 ms. Exceptions propagate to the caller.
    new_run keyword options are forwarded when given a CompiledScript.
    """
    run = _prepare(compiled, run_options, max_ticks, late_policy)
    deadline = time.monotonic()
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        if cancelled is not None and cancelled():
            return
        result = run.step()
        if result is None:
            return
        ticks += 1
        if result.sent:
            on_send(result.value)
        deadline += result.delay
        deadline, remaining = _remaining(deadline, late_policy)
        if cancelled is None:
            if remaining > 0:
                time.sleep(remaining)
        else:
            while remaining > 0:
                if cancelled():
                    return
                time.sleep(min(remaining, 0.1))
                remaining = max(0.0, deadline - time.monotonic())


async def run_async(
    compiled: CompiledScript | ScriptRun,
    on_send,
    *,
    cancelled=None,
    max_ticks: int | None = None,
    late_policy: str = "delay",
    **run_options,
) -> None:
    """Async counterpart; supports sync/async callbacks and task cancellation.

    At most one callback is in flight. No background tasks or message queue
    are created, and cancellation/errors propagate to the caller.
    """
    run = _prepare(compiled, run_options, max_ticks, late_policy)
    deadline = time.monotonic()
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        if cancelled is not None and cancelled():
            return
        result = run.step()
        if result is None:
            return
        ticks += 1
        if result.sent:
            pending = on_send(result.value)
            if inspect.isawaitable(pending):
                await pending
        deadline += result.delay
        deadline, remaining = _remaining(deadline, late_policy)
        if cancelled is None:
            await asyncio.sleep(remaining)
        else:
            while remaining > 0:
                if cancelled():
                    return
                await asyncio.sleep(min(remaining, 0.1))
                remaining = max(0.0, deadline - time.monotonic())
            await asyncio.sleep(0)
