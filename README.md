# signallang

A small, safe scripting language for publishing synthetic signals —
structured data that changes over time — no `eval`/`exec`, no
user-defined functions, nothing that reaches outside the value it's
computing.

It was designed to drive a ROS2 "fake publisher" (make a topic emit
synthetic sensor/status data for testing a subscriber in isolation), but
nothing in the language itself is ROS-specific — it's a general way to
describe "this value, but changing over time, at this rate, for this
long," usable anywhere that pattern shows up.

## What's here

This repo ships the full language: the **expression layer** (arithmetic,
comparisons, `and`/`or`/`not`, a function whitelist, `terop`, and a
postfix `.s`/`.m`/`.ms` unit-view operator) and the **statement layer** on
top of it — `var`, `if {} else if {} else {}`, `repeat`/`repeat N`/`for i
in A..B`, `live { }` blocks with an automatic per-binding `_t`,
`timer()`/`latching_timer()`/`.reset()`, `send hz/dur`, string literals,
positional array fill, and the `default` placeholder.

```python
from signallang import evaluate, compile_script

evaluate("sin(t)", {"t": 1.57})
# 0.9999996829318346

evaluate("terop(t < 4.5 and battery > 0, 1, 0)", {"t": 2.0, "battery": 80.0})
# 1.0

compiled = compile_script("temperature = linear(20, 30, 10s);\nsend hz 2 dur inf;")
run = compiled.new_run()
run.step()  # StepResult(value={'temperature': 20.0}, hz=2.0)
run.step()  # StepResult(value={'temperature': 20.5}, hz=2.0)
```

A script compiles to a small, flat instruction tape (mostly `Send`, plus
`SetVar`/`SetField`/`Jump`/`JumpIfFalse`/`CreateTimer`/`ResetTimer`) and
runs as a stepped VM: `ScriptRun.step()` executes instructions until it
hits the next `Send` tick and returns immediately — no thread, no clock,
no `sleep()` anywhere in `compiler.py`/`vm.py`. Real-time pacing between
`step()` calls is the caller's job; `run_realtime()` is a small opt-in
convenience driver (the only place in the package that imports `time`) for
anyone using signallang outside a framework that already owns its own
timer loop (like ROS's `rclpy.Timer` will in a future adapter).

```python
from signallang import run_realtime

run_realtime(compiled, on_send=lambda msg: print(msg))  # blocks, runs in real time
```

## Grammar

Loosest to tightest binding: `or` → `and` → `not` → comparison
(`< > <= >= == !=`, non-chaining — write `a > 1 and a < 5`, not
`1 < a < 5`) → `+ -` → `* / %` → unary `+/-` → atom.

| | |
|---|---|
| Numbers | `20`, `0.5`, `-3.2` |
| Constants | `true`, `false`, `pi`, `e` |
| Variables | whatever you pass in the `variables` dict — `t`, `i`, or anything else |
| Functions | `sin cos abs sqrt floor ceil min max random` |
| `terop(cond, then, else)` | inline choice — both branches evaluate eagerly |

Whatever names you inject via `variables` are the only identifiers
available beyond the fixed whitelist above — there's no way to reach a
name, attribute, or module that wasn't explicitly handed in.

## Install

```bash
pip install -e .
```

```bash
pip install -e ".[test]" && pytest
```

## Roadmap

What's left is wiring this up to a real message system — a ROS2 adapter
(`SchemaProvider` backed by real message reflection, ROS type-coercion,
and a real `rclpy.Timer` driving `step()` in place of `run_realtime()`)
that the `robohome_ws` dashboard's fake-publisher feature would depend on.
Nothing in this package itself is ROS-specific, by design.
