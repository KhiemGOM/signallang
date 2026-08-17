"""The one opt-in convenience driver, and the only module in this package
that imports `time`. Real-time pacing was never signallang's job - `step()`
returns instantly with no waiting - but this loop is genuinely useful to
anyone using the package standalone, so it ships as a small, swappable
wrapper on top of the pure core rather than being required. A host
application with its own timer (a robotics framework's scheduler, a game
loop) would likely drive `step()` from that instead.
"""

import time

from .vm import CompiledScript


def run_realtime(compiled: CompiledScript, on_send) -> None:
    run = compiled.new_run()
    while True:
        result = run.step()
        if result is None:
            return
        t0 = time.monotonic()
        if result.sent:
            on_send(result.value)
        remaining = (1.0 / result.hz) - (time.monotonic() - t0)
        if remaining > 0:
            time.sleep(remaining)
