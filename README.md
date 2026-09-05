<img src="assets/banner.svg" alt="signallang — script the signal, scrap the loop" width="100%" />

# signallang

A small, framework-independent language for publishing synthetic signals:
structured messages whose values change on a schedule. Use it to mock sensor
feeds, drive UI demos, exercise message consumers, and feed simulators.

Here, “signal” means a scheduled message, not audio or DSP processing. The
library has **zero runtime dependencies**. Its restricted expression language
has no Python evaluation, imports, arbitrary attribute access, or ambient host
namespace. See [Safety and limits](#safety-and-limits) for the boundaries.

```python
from signallang import compile_script

compiled = compile_script("""
    temperature = linear!(20, 30, 10s);
    send hz 2 dur inf;
""")
run = compiled.new_run(seed=42)
first = run.step()
assert first.value == {"temperature": 20.0}
assert first.timestamp == 0.0
assert first.delay == 0.5
assert run.step().value == {"temperature": 20.5}
```

## Install and CLI

```bash
pip install signallang
signallang validate demo.signal
signallang run demo.signal --ticks 5
signallang run demo.signal --ticks 5 --trace --seed 42 --ext rate=10
```

`validate` checks statement and expression syntax and function names/arities,
including expressions in branches that will not execute. It never runs the
script. Runtime-dependent errors, such as a missing field, invalid host rate,
or division by a computed zero, are checked during execution. Diagnostics
include a source location when available; expanded macro errors identify the
call site.

`run` advances simulated time as fast as it can and prints one JSON message per
sent tick. It does **not** wait in real time. `--trace` also prints wait events,
with `timestamp`, `sequence`, `delay`, `sent`, and `value`. `--ticks` counts all
steps, including waits, and must be positive. `--ext KEY=VALUE` can repeat;
values parse as JSON when possible. Additional options: `--max-hz`,
`--step-instruction-budget`, and `--operation-budget`.

## Runtime and host integration

The compiler lowers statements to a flat instruction tape and compiles
expressions into reusable syntax trees. A run owns its variables, timers,
message, persistent live state, and random generator. The VM reads no clock,
sleeps nowhere, and creates no threads or tasks.

`step()` executes until a send or wait, returning a `StepResult`, or returns
`None` when the script finishes. A result contains:

| Attribute | Meaning |
| --- | --- |
| `value` | Message dictionary, or `None` for a wait |
| `sent` | Whether there is a message to publish |
| `hz` | Reciprocal of the interval until the next step |
| `delay` | That interval in seconds (`1 / hz`) |
| `timestamp` | Simulated time at the start of this step |
| `sequence` | Zero-based step number, including waits |

A message dictionary is always the publishing envelope. `send 5` produces
`{"data": 5}`; `send [1, 2]` without a schema produces `{"data": [1, 2]}`;
`send json {x: 1}` produces `{"x": 1}`.

```python
from signallang import compile_file, run_realtime

compiled = compile_file("demo.signal")  # .signal is a convention, not required
run_realtime(compiled, print, max_ticks=10, seed=42)
```

`run_realtime(compiled_or_run, on_send, ...)` blocks and paces against cumulative
monotonic deadlines, including time spent evaluating and delivering messages.
`run_async(...)` is its async counterpart; callbacks may be synchronous or
awaitable. Both skip callbacks for waits, deliver one callback at a time, and
propagate callback failures. Awaiting delivery provides backpressure without
building an unbounded queue.

Both drivers accept `max_ticks`, a `cancelled()` predicate, and `late_policy`:
`"delay"` rebases after overruns (default), `"catch_up"` keeps the original
deadlines, and `"error"` raises on an overrun. Cancellation predicates are
checked between ticks and at most 100 ms apart during sleeps; they cannot
interrupt a running synchronous callback. Async task cancellation propagates.
Drivers forward other options to `new_run()` for a compiled script. When given
an existing run, configure it beforehand instead of passing run options again.

A host with its own scheduler should call `step()` directly, publish only when
`result.sent`, and schedule the next call after `result.delay`. It can use
`run.collect(ticks)` for a bounded offline trace, or iterate over a finite run.
An infinite script also yields an infinite iterator; bound collection explicitly.

[Examples](examples/) demonstrate stdout, WebSocket broadcasting with disconnect
handling, and schema-shaped messages.

## Language reference

### Values, variables, and message fields

Statements end with semicolons. Both `//` and `#` begin line comments outside
strings. Identifiers use letters, digits, and underscores. Keywords, builtins,
`t`, `_t`, and internal variable names beginning `__` cannot be declared as vars.

```signal
var frame = "map";
count = 5;
ratio = 5.0;
valid = true;
header.frame_id = frame + "_link";
send hz 2 dur 1t;
```

`var name = expression;` declares a variable; `name = expression;` reassigns a
previously declared variable, otherwise it writes a message field. Dotted paths
create nested objects. Use `msg.name` to explicitly address a message field when
a variable uses the same name. A timer or variable cannot redeclare an existing
variable in overlapping statement scope. A `for` counter exists only for its
loop; sequential loops can reuse the name.

Primitive values are Int, Float, Bool, and String, plus arrays and objects.
Whole-number syntax produces Int; decimal or scientific notation produces
Float. Bool is distinct from numbers for arithmetic and numeric functions.
Int arithmetic stays Int except `/`, which always returns Float. `floor`,
`ceil`, and `floordiv` return Int. Strings support concatenation and
lexicographic ordering; quotes currently have no escape syntax. `null` is not
a language value, and host values containing `None` are rejected.

Container assignment has **value semantics**: assigning an array or object to a
variable, field, or indexed slot captures a copy. Later mutation does not modify
another assignment or a previously returned message.

```signal
var config = json {points: [1, 2, json {x: 0}]};
config.points[2].x = 42;
config.extra.enabled = true;
result = config.points[2].x;
send dur 1t;
```

Arrays use `[a, b]`; objects use `json {key: value}` with identifier or quoted
keys. Duplicate object keys are rejected. Bracket and dot reads chain freely.
Array indices must be whole numbers within bounds; negative indices do not
wrap. Missing reads raise an error. Missing intermediate objects in dotted
variable assignment are created; bracket walks require existing keys.
Message fields are assigned by dotted name, not bracket index.

`msg` reads a snapshot of the statically assembled message, including schema
defaults. Live bindings are overlaid only when sending, so `msg` does not resolve
them: a live field may still have its earlier static/default value there, or
be absent. A bare name also reads a top-level message field when no variable,
timer, or builtin already claims it. Nested fields need an explicit path.

### Expressions and durations

Precedence from loosest to tightest: `or`, `and`, `not`, comparison, `+ -`,
`* / %`, unary `+ -`, then calls/indexing/postfix access. Comparisons do not
chain: write `a > 1 and a < 5`. `and` and `or` short-circuit and return Bool.
Truthiness follows ordinary empty/nonempty and zero/nonzero rules.
`==`/`!=` use structural Python value equality, including numeric equality;
ordering requires two numbers or two strings.

Durations are numeric seconds. `10s`, `3m`, and `500ms` produce Float values.
Arithmetic, variables, host parameters, containers, and live locals compose
normally; there is no separate compile-time Duration type. APIs and time-shaped
functions interpret bare numeric duration arguments as seconds and validate
positive durations where required. Tick units (`Nt`) belong only to send duration
syntax, not ordinary expressions or wait.

```signal
extern base_period = 500ms;
var period = base_period * 2;
value = square!(0, 1, period);
send hz 4 dur 4t;
```

| Expression feature | Meaning |
| --- | --- |
| `pi`, `e`, `true`, `false` | Constants |
| `sin cos abs sqrt floor ceil min max floordiv` | Numeric functions; min/max also accept two or more strings |
| `terop(condition, then, otherwise)` | Eager conditional: both branches evaluate and must have the same type category; Int/Float form one category |
| `.s`, `.m`, `.ms` | View numeric seconds in the requested unit |
| `.scale(k)` | Multiply the result by k |
| `.add(k)`, `.bias(k)` | Add k |
| `.shift(offset)` | Subtract offset from a function call's first argument |

Postfix transforms chain. `.shift` requires a call with at least one argument;
all shifts in that call's initial chain apply before the call, regardless of
where they appear relative to result transforms. For example,
`square(5, 0, 1, 10s).scale(2).shift(1s)` is equivalent to
`square(4, 0, 1, 10s).scale(2)`. Numeric transforms reject strings/Bool/containers.
Object properties take priority over numeric unit names on an object value.

### Control flow and scheduling

```signal
for i in 0..3 {
    if i < 2 { status = "warming"; }
    else { status = "ready"; }
    count = i;
    send hz 2 dur 1t;
}
wait 500ms;
repeat 2 { send hz 4 dur 1t; }
```

`if`/`else if`/`else`, `repeat N`, `repeat { ... }`, and `for name in start..end`
compile to jumps, not eager unrolling. Repeat counts are nonnegative whole
numbers; range bounds are whole numbers and the upper bound is exclusive.
Bounds are captured when entering the loop. `for i in 0..inf` and an uncounted
repeat are unbounded. A zero repeat count or empty range does nothing.

**Bare `send;` sends indefinitely**, at the configured maximum rate. Use
`send dur 1t;` to send once. Value, `hz`, and `dur` modifiers may appear in any
order. The default rate ceiling is 50 Hz; change it with
`compile_script(source, max_hz=200)`. Requested rates above the ceiling clamp to
it; zero, negative, and non-finite rates are errors.

| Form | Meaning |
| --- | --- |
| `send hz 2 dur inf;` | Publish indefinitely at 2 Hz |
| `send hz 10 dur 4.5s;` | Publish on 10 Hz ticks until at least 4.5 simulated seconds elapse |
| `send hz 1 dur 10t;` | Exactly 10 ticks; counts must be positive whole numbers |
| `send 5 dur 1t;` | Publish a scalar in the data envelope once |
| `wait 500ms;` | Advance time without publishing |
| `send hz (rate) dur (length);` | Schedule expressions; length is seconds |
| `send hz (rate) dur (count)t;` | Computed tick count |
| `wait (base_period * 2);` | Computed gap in seconds |

Parentheses delimit computed send modifiers to keep them unambiguous with the
optional sent value. Schedule expressions resolve once on entering the send,
not on every tick; a loop re-entry evaluates them again. Wait accepts a numeric
literal, variable, or parenthesized expression. Every duration must be positive.

Wall-duration sends use whole ticks: at 2 Hz, 750 ms takes two ticks and advances
one second. Values are evaluated before time advances; a 1-second ramp sent at
2 Hz for exactly 1 second emits its values at 0 and 0.5 seconds. Follow it with
a hold or another tick to emit its endpoint. Waits are not clamped by max_hz.

### Static and live evaluation

`field = expression;` evaluates once when reached, even for `sin(t)` or a random
function. `field = live expression;` evaluates each sent tick. `field = name!(args)`
is shorthand for a live call, only as the entire right-hand side. Time-shaped
functions automatically receive the binding's `_t` as their first argument.
Other bang calls retain exactly the arguments written.

```signal
counter = live {
    static value = 0;
    value = value + 1;
    var label = "tick";
    return json {label: label, count: value};
};
send hz 2 dur 3t;
```

A live block supports local var declarations/reassignment, top-level static
locals, conditionals, and a final return. It cannot write message fields or outer
variables, send, wait, create/reset timers, or call statement macros. Locals reset
on each sent tick; statics persist. Static initializers execute sequentially when
the binding is assigned, can read earlier statics and `_t`, and reset when the
binding is assigned again. A local may shadow an outer value in its own scope.

The most recent write owns overlapping paths: replacing an object or the entire
message removes old child live bindings; writing a child also cancels a live
ancestor. Independent sibling bindings remain active. Returned messages are
snapshots, and caller mutation cannot affect later sends.

`t` counts simulated seconds for the entire run, starting at zero and advancing
on sends and waits. `_t` is private to each live binding and latches on first
actual read. Reassigning that binding recreates its timer.

```signal
var eager = timer();
var latched = latching_timer();
wait 1s;
elapsed = latched.s;  // first actual read: zero
age = eager.s;        // one second
latched.reset();
send dur 1t;
```

`timer()` starts at creation; `latching_timer()` starts on first actual read.
Reset re-zeros the eager timer or unlatches the latching timer. Timer reads are
ordinary Float seconds; only a timer declaration supports reset. `t` cannot reset.

### Signal shapes and randomness

| Function | Behavior |
| --- | --- |
| `linear(t, a, b, dur)` | Ramp a to b, then hold b; negative t extrapolates |
| `square(t, low, high, period)` | Low for the first half, high for the second |
| `triangle(t, low, high, period)` | Low to high to low |
| `sawtooth(t, low, high, period)` | Low to high, then reset |
| `sinusoidal_wave(t, amplitude, period)` | Sinusoidal value |
| `damped_wave(t, amplitude, decay, period)` | Sinusoid with exponential amplitude decay |
| `pulse(t, low, high, period, duty)` | High for the first duty fraction; duty in [0, 1] |
| `exponential(t, initial, rate)` | Exponential growth/decay |
| `polynomial(t, a0, a1, ...)` | Polynomial in t; no coefficients gives zero |
| `keyframes(t, points, mode, repeat)` | Piecewise interpolation through [seconds, value] points |

All functions in this table inject `_t` when called with `!`. Periods and ramp
durations must be positive; rate/decay are numeric rate constants.

Keyframe times must strictly increase. Mode defaults to `"linear"`; `"hold"`
keeps the previous value until the next point. Repeat defaults to false and
requires at least two points when true. Non-repeating curves clamp before/after
the endpoints. Repeating curves wrap at the final point's time.

```signal
value = keyframes!([[0s, 0], [1s, 10], [2s, 10], [3s, 0]], "linear", true);
send hz 2 dur 8t;
```

| Function | Result and constraints |
| --- | --- |
| `random()` | Float in [0, 1) |
| `noise(mean, stddev)` | Gaussian Float; nonnegative stddev |
| `uniform(low, high)` | Uniform Float; low <= high |
| `discrete_uniform(low, high)` | Inclusive Int; whole-number bounds |
| `poisson(lam)` | Int; rate in (0, 100000], stable additive sampling |
| `binomial(n, p)` | Int; whole n in [0, 1000000], p in [0, 1] |
| `rand_walk!(low, high)` | Accumulated discrete_uniform steps, starting from zero |
| `brown_motion!(mean, stddev)` | Accumulated Gaussian increments, starting from zero |

Each run has its own random generator. `seed(number_or_string);` reseeds that
run; `new_run(seed=42)` seeds it from the host. Interleaving other runs does not
change its sequence or Python's global random state. Sequences are reproducible
for the same implementation/version; sampler implementation changes can change
them. Standalone `evaluate()` retains Python's random module by default or takes
an explicit `rng=random.Random(...)` instance.

Random-walk increments are per sent tick, not scaled by elapsed seconds. Changing
hz changes increments per second. The accumulator forms exist only with `!`;
`.scale`, `.add`, `.bias`, and unit views transform their accumulated result.
They do not support `.shift`, because their result is an accumulator, not a
function of time. Use time-shaped functions when phase shifting is needed.

### Macros and extern parameters

```signal
func ramp(field, start, finish, length) {
    var beginning = start;
    field = linear!(beginning, finish, length);
}
ramp(temperature, 20, 30, 2s);
send hz 2 dur 4t;
```

A `func` is a statement macro expanded inline, not a callable runtime function.
Arguments are atomic identifiers/dotted paths, numbers, strings, or duration
literals; computed expressions are not macro arguments. Local variable renaming
preserves field names and object keys. Recursive expansion and excessive total
expansion are rejected. Unused macro bodies are templates and are validated when
expanded. Macro calls are not allowed in live blocks.

```signal
extern topic = "/demo";
extern rate = 2;
extern length = 1s;
channel = topic;
value = linear!(0, 10, length);
send hz (rate) dur (length);
```

An extern without a default must be supplied via `new_run(external_params=...)`.
Extra host keys are ignored. Extern values are validated and copied on entry;
reading a container gives a copy, so script aliases cannot mutate the host input
or the run's extern namespace. Direct script assignment to an extern is rejected.
The host can inspect resolved values in `run.externs`.

### Schemas

Without a schema, the message starts empty. With one, it starts at schema
defaults. `default` and positional array filling use the host's SchemaProvider;
`json {}` carries its own field names. A top-level array literal in field/send
position is positional fill for a schema object; at an array-typed schema field
it is an ordinary array. Without a schema it is always an ordinary array.

```python
from signallang import DictSchemaProvider, compile_script

schema = DictSchemaProvider({"level": 0, "ratio": 0.0}, strict=True)
compiled = compile_script("send [3, 0.5] dur 1t;", schema_provider=schema)
assert compiled.new_run().step().value == {"level": 3, "ratio": 0.5}
```

`SchemaProvider` requires `fields_at(path)` and `default_at(path)`. A separate
`TypedSchemaProvider` protocol adds `type_at(path)`; the runtime detects that
method optionally. Both static and live leaf writes use it. No numeric conversion
is implicit: an Int field rejects Float and a Float field rejects Int.

`DictSchemaProvider(..., strict=True)` additionally validates unknown/missing
keys, whole objects, and array elements recursively. A nonempty array default
uses its first element as an element template; an empty one accepts any supported
element type. With strict=False (default), whole sub-object contents and unknown
leaf names remain permissive, but an object cannot replace a typed scalar leaf.
Custom providers can implement `validate_at(path, value)` for additional rules.
Schema defaults and constructor inputs are defensively copied.

## Safety and limits

The grammar exposes only fixed builtins and plain values. There is no arbitrary
Python evaluation or object attribute access; dot access reads dictionary keys
or a fixed postfix operator. Externs accept only bounded plain Int/Float/Bool/
String/list/dict trees, with string object keys. Custom objects, cycles, None,
non-finite numbers, and oversized values are rejected.

Two independent limits apply to each step: `step_instruction_budget` (default
100,000 instant instructions) catches non-yielding loops; `operation_budget`
(default 1,000,000 work units) covers expression nodes, copying, and costly random
sampling, including live expressions. Both are configurable positive integers.
A runtime error halts that run; start a new run to retry after correcting inputs.

Additional limits: 200,000 source characters, bounded expression/statement
nesting (up to 64 levels, with AST structure also counted), 100,000 nodes in an
expanded value tree, 1,000,000 characters per string, and 4,096 bits per integer.
Macros have a separate 200,000-character total expansion ceiling. Extremely
small/large schedules that exceed floating-point clock precision fail explicitly.

These are deterministic resource guardrails, **not process isolation or a
hard wall-clock deadline**. Host callbacks and custom schema providers are
trusted Python code outside those guarantees. max_hz caps simulated publish
rate, not how fast a caller can invoke step(). Use an isolated process when an
application needs enforceable CPU/memory isolation from untrusted workloads.

## Development

```bash
pip install -e ".[test,dev]"
pytest -q
mypy src/signallang
ruff check src/signallang tests examples
python -m build
```

Tests include cross-feature regressions, generated bounded inputs, sampler
statistics, executable README examples, and integration checks. CI exercises
supported Python versions and ensures the core stays independent of clocks and
message frameworks. The [changelog](CHANGELOG.md) records intentional beta
language changes and [AUDIT.md](AUDIT.md) records the original audit and its
resolution status. Framework-specific schema reflection belongs in adapters.

## License

Apache-2.0
