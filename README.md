# signallang

[![PyPI](https://img.shields.io/pypi/v/signallang)](https://pypi.org/project/signallang/)
[![tests](https://github.com/KhiemGOM/signallang/actions/workflows/test.yml/badge.svg)](https://github.com/KhiemGOM/signallang/actions/workflows/test.yml)

A scripting language for publishing synthetic signals: structured data
that changes over time, on a defined schedule. No `eval`/`exec`, no
user-defined functions, no name, attribute, or module accessible beyond
what is explicitly provided as input.

The language is not tied to ROS2 or any other framework. It describes a
value that changes over time on a defined schedule — e.g. a value ramping
from 20 to 30 over 10 seconds, sent at 2Hz — independent of the system
consuming that value. Applicable use cases include mocking a sensor feed,
driving a UI demo, load-testing a message consumer, and feeding a
simulator.

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

## Comparison to a manually written loop

A single time-varying value can be produced with a manually written loop
(`while True: msg.temp += 0.5; pub.publish(msg); sleep(0.5)`). signallang
provides three properties a manual loop does not:

- **Declarative composition of ramps, holds, and repeats.** `linear!(...)`,
  `if`/`repeat`/`for`, and multiple `send` phases in sequence are
  expressed without manually tracking tick counts and phase transitions.
- **Scripts as data, not code.** A script is safe to accept from a config
  file, a web form, or a REST body without `eval`. For example, a UI for
  faking a topic publish can accept a script typed into a textarea and
  have a backend compile and run it directly.
- **No dependency on a particular clock.** A script does not sleep or
  block; it is driven one logical tick at a time by whatever real timer
  the host provides (a `while` loop, an `rclpy.Timer`, an `asyncio` event
  loop — see [Two layers](#two-layers-compile-time-vs-real-time)).

## Install

```bash
pip install signallang
```

```bash
git clone https://github.com/KhiemGOM/signallang && cd signallang
pip install -e ".[test]" && pytest
```

## Two layers: compile time vs. real time

A script compiles to a flat instruction tape (`SetVar` / `SetField` /
`Send` / `Jump` / `JumpIfFalse` / `CreateTimer` / `ResetTimer`) executed
by a stepped VM. `ScriptRun.step()` executes instructions until it
reaches the next `Send` and returns. `compiler.py` and `vm.py` contain no
thread, no clock, and no call to `sleep()`; time within the VM is counted
as a value, not measured by waiting.

Pacing `step()` calls in real time is the responsibility of the caller:

```python
run = compiled.new_run()
while (result := run.step()) is not None:
    publish(result.value)
    sleep(1.0 / result.hz)
```

`run_realtime()` provides this loop as an optional convenience function.
It is the only module in the package that imports `time`:

```python
from signallang import run_realtime

run_realtime(compiled, on_send=lambda msg: print(msg))  # blocks, real time
```

A caller that already owns a timer loop — an `rclpy.Timer`, a
game/simulator tick, an `asyncio` task — calls `step()` directly from
that loop instead of using `run_realtime()`. [`examples/`](examples/)
contains two such integrations with no ROS dependency:
[`stdout_signal.py`](examples/stdout_signal.py) writes NDJSON to stdout,
and [`websocket_signal.py`](examples/websocket_signal.py) broadcasts to
WebSocket clients from an `asyncio` event loop.

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
| Variables | `t`, `_t`, a `for` loop's own variable, or any name declared with `var` |
| Functions | `sin cos abs sqrt floor ceil min max random noise` (see also [Signal-shape builtins](#signal-shape-builtins)) |
| `terop(cond, then, else)` | conditional expression; both branches are evaluated |
| Duration literals | `10s`, `3m`, `500ms`, `10t` (ticks); normalized to seconds at parse time |
| `.s` / `.m` / `.ms` | postfix unit view — `_t.s` reads a timer's value in seconds |
| `.scale(k)` | postfix result transform — multiplies the value by `k` |
| `.add(k)` / `.bias(k)` | postfix result transform — adds `k` to the value; two names for the same operation |

Only names declared with `var`, a `for` loop's own variable (bound to
whatever identifier follows `for` — `i` is convention, not a reserved
name; `for row in 0..7` works identically), and the language-defined
names `t`/`_t`, are in scope. There is no ambient global namespace.

`.s`, `.m`, `.ms`, `.scale(k)`, and `.add(k)`/`.bias(k)` chain in any
order and any count: `_t.s.scale(2).add(1)`. None accept a string
operand.

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
a number, no error, same as Python's own `1 == "1"`. Ordering
(`<`, `<=`, `>`, `>=`) is lexicographic and requires both sides to be
strings. Mixing a string with a number anywhere else — arithmetic
(`- * / %`, unary `-`), ordering, or the `.s`/`.m`/`.ms` unit view — is a
compile- or eval-time `ExprError`, not a silent coercion.

### Arrays and objects

```
[expr, expr, ...]
json { key: expr, key: expr, ... }
```

An array literal, and an object literal. A key is a bare identifier or a
quoted string, either form accepted. Both nest freely inside each other
and inside any expression, and are ordinary values — assignable to a
`var`, passed to `terop`, held in a field.

```
var arr = [10, 20, 30];
data = arr[1];                      # 20.0

var config = json { retries: 3, timeout: 5s };
data = config["retries"];            # 3.0 - bracket access
data = config.retries;               # 3.0 - dot access, sugar for the above

msg = json {
    header: json { frame_id: "map" },
    points: [1, 2, 3],
};
```

Bracket indexing (`arr[0]`) and dot access (`obj.key`) chain in any
combination: `arr[0].header.frame_id`, `points[1][2]`. Dot access is
recognized only when the value being accessed is an object — it takes
priority there over the fixed postfix operator names (`.s`, `.scale`,
...), none of which mean anything on an object.

An array index must be a whole number within range; an object key that
does not exist is a compile- or eval-time `ExprError`, not `null`/`None`.
Arithmetic, ordering, and the numeric postfix operators all reject an
array or object operand the same way they reject a string. `==`/`!=`
compare arrays and objects structurally, for free, via Python's own
equality — no special-casing needed. `terop` may return an array or
object, so it can select between two of them conditionally.

An array literal or `default` sentinel written directly as the entire
value of a field assignment or `send` is interpreted against whatever
`schema_provider` the host application passed to `compile_script()` — a
script never branches on whether one exists; that choice belongs to the
integration layer, not the script:

```
send json { temperature: 20.0, variance: 0.1 };   # own field names, no schema needed either way
send [20.0, 0.1];                                   # schema present: fills its fields by position
                                                     # no schema: published as a plain 2-element array
send [default, 20.0, 0.1];                          # `default` always needs a schema - no fallback meaning
```

`json {}` needs no schema either way, since its own keys already name
its fields. A plain array literal does too, *if* a schema is present, to
map each position to a field name; with none, it is simply published as
an array value, exactly as written — never an error. `default` is the
one exception: it always requires a schema, with or without an
enclosing array, since there is no field to ask a missing schema for a
zero value at. To index into a freshly written array rather than publish
it, assign it to a `var` first (`var tmp = [1, 2, 3]; data = tmp[0];`) —
an array literal written directly as the whole value of a field/`send`
assignment is always resolved against the schema (or its absence),
never indexed in place.

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
for row in 0..7 { data = row; send hz 1 dur 1t; }    # any identifier - i is convention, not special
for i in 0..inf { data = i; send hz 1 dur 1t; }      # unbounded — no eager unrolling
```

`if`, `repeat`, and `for` compile to jump-based control flow. A `repeat`
or `for` body may contain any number of `send` statements. An unbounded
loop has the same per-`step()` cost as a bounded one; both advance an
instruction pointer through the tape.

### `send`

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

### Evaluation timing: static vs. live

`field = expr;` evaluates `expr` once, at the point the instruction
executes, and `field` holds the result until reassigned. This is
unconditional: `data = sin(t);` evaluates once and freezes, the same as
`data = 5;`. No function name changes this behavior.

**`live` block**

```
field = live { <statements> return <expr>; };
```

`<expr>` is evaluated once per tick instead of once total. `<statements>`
may declare local variables (`var`) and branch (`if`/`else`); it may not
assign to an outer variable or a message field.

**`live` shorthand**

```
field = live <expr>;
```

Equivalent to `field = live { return <expr>; };`.

**Bang-call shorthand**

```
field = name!(args);
```

Recognized only when `name!(args)` is the entire right-hand side.
Equivalent to `field = live { return name(args); };`. For the fixed set
of time-shaped builtins — `linear`, `square`, `triangle`, `sawtooth`,
`damped_wave`, `sinusoidal_wave` — the elapsed-time argument is also
inserted as the first argument: `linear!(20, 30, 10s)` is equivalent to
`live { return linear(_t, 20, 30, 10s); };`. For every other function,
arguments are passed exactly as written: `sin!(t)` is equivalent to
`live { return sin(t); };`.

A bang call is not recognized when embedded inside a larger expression;
`data = 1 + sin!(t);` is a syntax error at `!`. Use the `live` shorthand
instead: `data = live 1 + sin(t);`.

**`t` and `_t`**

`t` is a single value for the whole script, starting at 0 and never
reset, in scope everywhere. It is not a count of ticks — it accumulates
elapsed time in seconds, advancing by `1/hz` each time any `Send`
instruction fires a tick, so its rate of advance follows whatever `hz`
was active at that tick. `_t` is a `latching_timer()` scoped to a `live`
binding: unset until first read, and reset to zero each time the
binding's assignment statement is (re-)executed. A `live` expression
inside a `repeat` body therefore restarts from zero on every iteration,
unlike `t`, which never restarts.

### Signal-shape builtins

Pure functions of an explicit elapsed-time argument, with the same
evaluation semantics as `sin`/`cos`: a bare call is evaluated once; `!`
evaluates the call once per tick.

| | |
|---|---|
| `linear(t, a, b, dur)` | ramps a → b over dur seconds, then holds at b |
| `square(t, low, high, period)` | 50% duty cycle: low for the first half of each period, high for the second |
| `triangle(t, low, high, period)` | ramps low → high over the first half, high → low over the second |
| `sawtooth(t, low, high, period)` | ramps low → high over the whole period, then resets to low |
| `sinusoidal_wave(t, amplitude, period)` | `amplitude * sin(2π·t/period)` |
| `damped_wave(t, amplitude, decay, period)` | `amplitude * e^(-decay·t) * sin(2π·t/period)` — a decaying sinusoid; the natural response of an underdamped 2nd-order system such as an RLC circuit |
| `noise(mean, stddev)` | one Gaussian-distributed random draw; not time-shaped, no time argument |

`linear`, `square`, `triangle`, `sawtooth`, `damped_wave`, and
`sinusoidal_wave` are the set whose `!` sugar also inserts `_t` as the
first argument: `square!(0, 1, 2s)`, not `square!(_t, 0, 1, 2s)`. Every
other function's `!` passes its arguments exactly as written.

### `.shift(offset)`

```
name(args).shift(offset)
```

Subtracts `offset` from the call's first argument, then makes the call
with the modified argument list; the result is otherwise ordinary —
evaluated once if the call is not wrapped in `live`/`!`, once per tick if
it is.

```
square!(0, 1, 10s).shift(3s)    # equivalent to: live { return square(_t - 3s, 0, 1, 10s); };
square(5, 0, 1, 10s).shift(3s)  # equivalent to: square(5 - 3s, 0, 1, 10s), evaluated once
```

Recognized anywhere within the postfix chain that follows a function
call's closing `)`, not only as the first operator in that chain — the
chain is scanned as a whole before the call is made, so `.shift(offset)`
always applies to the call's argument regardless of its position
relative to `.scale(...)`/`.add(...)`/`.bias(...)`, which apply to the
call's result instead:

```
square(5, 0, 1, 10s).shift(3s).scale(2)   # == square(5, 0, 1, 10s).scale(2).shift(3s)
```

Multiple `.shift(...)` calls in one chain accumulate. Requires the call
to take at least one argument; requires the first argument and `offset`
to both be numbers. A value below the underlying function's normal
domain (a negative elapsed time) is passed through unmodified —
`square`/`triangle`/`sawtooth` use `%`, which is defined for negative
input; `linear` extrapolates below `a` rather than clamping to it.
`.shift(...)` following anything other than a function call (a bare
variable, a parenthesized expression) is a syntax error.

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

This script ramps `linear.x` from 0 to 1 over 3 seconds, holds at 1 for 5
seconds, ramps back to 0 over 3 seconds, holds at 0 for 2 seconds, and
repeats. The result is a 13-second accelerate/cruise/decelerate/stop
cycle, applicable to faking a `geometry_msgs/Twist` value on a
mobile-base test rig.

## Safety model

- No `eval`, no `exec`, no attribute access, no imports, no
  user-defined functions. Every callable is drawn from a fixed set of
  functions defined by the language.
- The only names in scope are `t`, `_t`, a `for` loop's own variable,
  names declared with `var`, and fields of the message being built. No
  name outside the value currently being computed is accessible.
- `hz` is clamped to a 50Hz ceiling at compile time. `hz <= 0` is a
  compile error, not a runtime exception.
- Malformed scripts (invalid field names, incorrect array shape,
  mismatched positional fill) fail at `compile_script()` or the first
  `step()`, before any value is sent.

## Rationale

A UI for faking a ROS2 topic publish requires accepting a script from
untrusted input — e.g. a browser textarea — and executing it safely,
without `eval` and without exposing any value outside the one being
computed. `Faker` and `Mimesis` generate individual fake values, not a
schedule of values over time. JSON-Schema-driven fuzzers produce
structurally valid data without an intentional time-varying pattern.
General sandboxed interpreters (`RestrictedPython`, `asteval`, embedded
Lua) are safe but have no built-in notion of ticks, ramps, or `send
hz/dur`; using one requires building this scheduling layer separately.
signallang implements this scheduling layer directly, as an expression
sandbox plus a small set of statement primitives, compiled to a flat
instruction tape and executed one tick at a time by a caller-provided
clock.

## Development

```bash
pip install -e ".[test]"
pytest -v
```

`compiler.py` and `vm.py` do not import `time`; the VM is timing-free by
construction, and CI enforces this with a grep. CI also verifies that
`src/signallang` contains no reference to `rclpy` or `ros2` — this
package has no ROS dependency. A ROS2 adapter, where needed, is
implemented in the consuming project by wrapping `SchemaProvider` around
message reflection and driving `step()` from an `rclpy.Timer`.

Type-checking: `pip install -e ".[dev]" && mypy src/signallang`.

## License

Apache-2.0
