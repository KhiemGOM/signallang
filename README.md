# signallang

[![PyPI](https://img.shields.io/pypi/v/signallang)](https://pypi.org/project/signallang/)
[![tests](https://github.com/KhiemGOM/signallang/actions/workflows/test.yml/badge.svg)](https://github.com/KhiemGOM/signallang/actions/workflows/test.yml)

A small, safe scripting language for **publishing synthetic signals** —
structured data that changes over time, on a schedule you control. No
`eval`/`exec`, no user-defined functions, no way to reach a name,
attribute, or module that wasn't explicitly handed in.

It was designed to drive a ROS2 "fake publisher" (make a topic emit
synthetic sensor/status data so you can test a subscriber in isolation,
without real hardware), but nothing in the language itself is
ROS-specific. It's a general way to describe *"this value, but ramping
from 20 to 30 over 10 seconds, sent at 2Hz, for 10 seconds"* — useful
anywhere that shape shows up: mocking a sensor feed, driving a UI demo,
load-testing a message consumer, feeding a simulator.

```python
from signallang import compile_script

compiled = compile_script("""
    temperature = linear!(20, 30, 10s);
    send hz 2 dur inf;
""")
run = compiled.new_run()
run.step()   # StepResult(value={'temperature': 20.0}, hz=2.0)
run.step()   # StepResult(value={'temperature': 20.5}, hz=2.0)
```

## Why not just a Python loop?

You can write the loop by hand — `while True: msg.temp += 0.5; pub.publish(msg); sleep(0.5)`
— and for a one-off script that's often the right call. signallang earns
its keep once you want any of these on top:

- **Ramps, holds, and repeats composed declaratively** — `linear!(...)`,
  `if`/`repeat`/`for`, multiple `send` phases in sequence — without hand
  rolling tick-counting and phase-transition bookkeeping every time.
- **A script as *data***, not code — safe to accept from a config file, a
  web form, or a REST body. A UI for faking a topic publish, for example,
  can let someone type a script straight into a textarea and have a
  backend compile and run it, with no `eval` in sight.
- **Decoupled from whoever owns the clock.** A script never sleeps or
  blocks; it's driven one logical tick at a time by whatever real timer
  the host already has (a `while` loop, an `rclpy.Timer`, an `asyncio`
  event loop) — see [Two layers](#two-layers-compile-time-vs-real-time) below.

## Install

```bash
pip install signallang
```

```bash
git clone https://github.com/KhiemGOM/signallang && cd signallang
pip install -e ".[test]" && pytest
```

## Two layers: compile time vs. real time

A script compiles to a small, flat instruction tape (`SetVar` / `SetField`
/ `Send` / `Jump` / `JumpIfFalse` / `CreateTimer` / `ResetTimer`) and runs
as a stepped VM. `ScriptRun.step()` executes instructions until it hits
the next `Send` and returns immediately — **no thread, no clock, no
`sleep()` anywhere in `compiler.py` or `vm.py`.** Time inside the VM is
counted, not waited for.

Pacing those `step()` calls in real time is the caller's job:

```python
run = compiled.new_run()
while (result := run.step()) is not None:
    publish(result.value)
    sleep(1.0 / result.hz)
```

`run_realtime()` ships a minimal version of exactly that loop as an
opt-in convenience — the *only* place in the package that imports `time`:

```python
from signallang import run_realtime

run_realtime(compiled, on_send=lambda msg: print(msg))  # blocks, real time
```

Anything that already owns a timer loop — ROS's `rclpy.Timer`, a
game/simulator tick, an `asyncio` task — skips `run_realtime()` and calls
`step()` directly from its own loop instead. [`examples/`](examples/) has
two small, non-ROS proofs of this: [`stdout_signal.py`](examples/stdout_signal.py)
pipes NDJSON to stdout (consumable by any language, C++ included), and
[`websocket_signal.py`](examples/websocket_signal.py) broadcasts to
WebSocket clients from an `asyncio` event loop instead of a blocking
thread.

## Language tour

### Fields and values

```
data = 5;                    # a static field, holds until reassigned
frame_id = "map";             # string literal
linear.x = 1.5;               # dotted path into a nested field
header = default;             # ask the schema provider for this field's zero value
send [default, 20.0, 0.1];    # positional array fill against the schema's field order
```

### Expressions

Loosest to tightest binding: `or` → `and` → `not` → comparison
(`< > <= >= == !=`, non-chaining — write `a > 1 and a < 5`, not
`1 < a < 5`) → `+ -` → `* / %` → unary `+ -` → atom.

| | |
|---|---|
| Numbers | `20`, `0.5`, `-3.2` |
| Constants | `true`, `false`, `pi`, `e` |
| Variables | `t`, `i`, `_t`, or anything else in scope |
| Functions | `sin cos abs sqrt floor ceil min max random` |
| `terop(cond, then, else)` | inline choice, both branches evaluate eagerly |
| Duration literals | `10s`, `3m`, `500ms`, `10t` (ticks) — normalized to seconds at parse time |
| `.s` / `.m` / `.ms` | postfix unit view — `_t.s` reads a timer back out in seconds |

Only names you declare with `var`, or that the language defines (`t`,
`_t`, `i` inside a `for`), are ever in scope — there's no ambient global
namespace to reach into.

### Strings

A string flows through the same expression grammar as a number, with the
operators that make sense for it:

```
var frame = "map";
frame_id = frame + "_link";        # concatenation - "map_link"
if frame == "map" {                 # ==/!=/< <= > >= all work on strings
    status = "ok";
} else {
    status = "unexpected frame";
}
if frame and true { ... }           # truthy iff non-empty, like Python
```

`==`/`!=` work between any two values — a string is simply never equal to
a number, no error, same as Python's own `1 == "1"`. Ordering (`< <= >
>=`) is lexicographic and requires both sides to be strings. Mixing a
string with a number anywhere else — arithmetic (`- * / %`, unary `-`),
ordering, or the `.s`/`.m`/`.ms` unit view — is a compile- or eval-time
`ExprError`, not a silent coercion.

### Control flow

```
if battery < 20 {
    status = "low";
} else if battery < 50 {
    status = "ok";
} else {
    status = "full";
}

repeat 3 { send hz 1 dur 1t; }        # fixed count
repeat { send hz 1 dur 1t; }          # forever
for i in 0..7 { data = i * 30; send hz 1 dur 1t; }   # bounded range, i in scope
for i in 0..inf { data = i; send hz 1 dur 1t; }      # unbounded — no eager unrolling
```

`if`/`repeat`/`for` lower to real jump-based control flow at compile
time, so a `repeat`/`for` body can contain any number of `send`s, and an
unbounded loop costs nothing extra per `step()` — it's just an ordinary
instruction pointer moving through the tape, the same as a bounded one.

### `send` — the one instruction that spends time

```
send;                          # send the current msg once, hz defaults to 50 (MAX_HZ), dur inf
send hz 2 dur inf;              # 2Hz forever
send hz 10 dur 4.5s;            # 10Hz for 4.5 wall-clock seconds
send hz 1 dur 10t;               # exactly 10 ticks at 1Hz, no trailing wait
send 5;                          # value sugar: send this scalar directly, no prior field assign
send [1, 2, 3];                  # value sugar: send this array directly
```

`hz`, `dur`, and a bare value can appear in any order on the same `send`
statement. `hz` is clamped to a 50Hz safety ceiling (`MAX_HZ`), enforced
by the compiler.

### Live vs. static — one rule, no exceptions

A plain assignment evaluates **once** and freezes until reassigned —
`data = sin(t);` computes `sin(t)` at the moment that instruction runs and
holds it, exactly like `data = 5;` holds `5`. To make something
re-evaluate every tick, you always write `live` — there's no function
whose name secretly makes it live behind your back:

```
temperature = live { return 20 + sin(t); };   # the full form: locals, if/else allowed
temperature = live 20 + sin(t);                # shorthand: sugar for the block above
temperature = live sin(t);                     # any function works the same way live
```

`name!(args)` is a second, narrower shorthand for the single most common
case — wrapping one call in `live` with nothing else going on:

```
temperature = linear!(20, 30, 10s);   # sugar for: live { return linear(_t, 20, 30, 10s); };
data = sin!(t);                        # sugar for: live { return sin(t); };
```

`!` is only recognized as the *entire* right-hand side — `data = 1 +
sin!(t);` doesn't parse as live sugar (it's a syntax error at that `!`);
write `data = live 1 + sin(t);` instead. `t` is the VM's running tick
counter; `_t` is a `latching_timer()` implicitly created per `live`
binding — unstarted until first read, and a fresh one created each time
its assignment statement (re-)executes, so a ramp inside a `repeat`
restarts from zero on every lap, for free.

### Signal-shape builtins

Plain, pure functions of an explicit elapsed-time argument — nothing
about them is implicitly wired to the VM's clock, so they behave exactly
like `sin`/`cos` do: call them bare for a frozen one-shot value, or with
`!` for a value that moves every tick.

| | |
|---|---|
| `linear(t, a, b, dur)` | ramps a → b over dur seconds, then holds at b |
| `square(t, low, high, period)` | 50% duty cycle: low for the first half of each period, high for the second |
| `triangle(t, low, high, period)` | ramps low → high over the first half, high → low over the second |
| `sawtooth(t, low, high, period)` | ramps low → high over the whole period, then snaps back to low |
| `damped_wave(t, amplitude, decay, period)` | a decaying sinusoid — the natural response shape of an underdamped 2nd-order system (an RLC circuit's own step response): `amplitude * e^(-decay·t) * sin(2π·t/period)` |
| `noise(mean, stddev)` | one Gaussian-distributed random draw (no time argument — it's not time-shaped, just non-deterministic) |

`linear`/`square`/`triangle`/`sawtooth`/`damped_wave` are exactly the set
whose `!` sugar also injects `_t` as that first argument for you —
`square!(0, 1, 2s)`, not `square!(_t, 0, 1, 2s)`. Every other function's
`!` wraps its arguments exactly as written.

```
var mt = timer();               # an explicit named timer
mt.reset();                     # zero it immediately, mid-script
data = mt.s;                    # read it back out in seconds
```

## Full worked example

```
repeat {
    linear.x = linear!(0, 1.0, 3s);
    send hz 10 dur 3s;

    linear.x = 1.0;
    send hz 10 dur 5s;

    linear.x = linear!(1.0, 0, 3s);
    send hz 10 dur 3s;

    linear.x = 0.0;
    send hz 10 dur 2s;
}
```

Ramp `linear.x` from 0 to 1 over 3s, hold at 1 for 5s, ramp back down to
0 over 3s, hold at 0 for 2s, repeat forever — a 13-second accelerate/
cruise/decelerate/stop cycle, e.g. for faking a `geometry_msgs/Twist` on
a mobile-base test rig.

## Safety model

- No `eval`/`exec`, no attribute access, no imports, no user-defined
  functions — every callable is one of nine fixed math functions.
- The only names in scope are `t`/`i`/`_t`, whatever `var` declares, and
  fields on the message being built. There's no way to reference
  anything outside the value currently being computed.
- `hz` is clamped to a 50Hz ceiling at compile time; `hz <= 0` is a
  compile error, not a runtime crash.
- Malformed scripts (bad field names, wrong array shape, mismatched
  positional fill) are supposed to fail at `compile_script()` /
  the first `step()` — fail fast, before anything is ever sent.

## Why I made this

A UI for faking a ROS2 topic publish needed to let someone type *"ramp
this topic's temperature from 20 to 30 over 10 seconds, at 2Hz"* into a
browser textarea and have a backend run it safely — no `eval`, nothing
able to reach outside the value it's computing.

Nothing off-the-shelf fit that shape. `Faker`/`Mimesis` generate one-shot
fake *values*, not a *schedule* of them over time. JSON-Schema-driven
fuzzers produce structurally valid data but nothing intentionally
time-varying. General sandboxed interpreters (`RestrictedPython`,
`asteval`, embedded Lua) are safe but have no built-in notion of ticks,
ramps, or `send hz/dur` — reaching for one would still mean building this
exact scheduling layer on top by hand. So this became its own small
thing: an expression sandbox plus six statement primitives, compiled to a
flat instruction tape and driven one tick at a time by whoever owns the
real clock.

## Development

```bash
pip install -e ".[test]"
pytest -v
```

`compiler.py`/`vm.py` never import `time` — the whole VM is timing-free
by construction, and CI enforces this with a grep, alongside a check that
`src/signallang` never references `rclpy`/`ros2` (this package has zero
ROS dependency; a ROS2 adapter is expected to live in the *consuming*
project instead, wrapping `SchemaProvider` around real message reflection
and driving `step()` from an `rclpy.Timer`).

Type-checking: `pip install -e ".[dev]" && mypy src/signallang`.

## License

Apache-2.0
