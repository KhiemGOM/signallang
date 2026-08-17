# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- `config.retries = 5;`, where `config` is a `var` holding an object,
  silently did the wrong thing: `_parse_path_stmt` only ever checked a
  *single-segment* path against declared vars, so a multi-segment path
  starting with a var name fell straight through to the message-field
  write path instead - writing an unrelated top-level field named
  "config" into the published message, while the actual `config` var
  was never touched at all. No error, no warning, just a wrong result
  and a phantom field in the output. Fixed by checking whether the
  *leading* name is a declared var, independent of how many accessors
  follow: `name(.ident | [expr])+ = expr;` now mutates that var's own
  array/object value in place when the leading name is a var, and still
  writes a message field when it isn't - both forms share the same
  syntax, disambiguated only by that one check, never by anything
  different in the grammar. Also adds bracket assignment (`arr[0] =
  5;`), not just dot assignment, and rejects `[...]` assignment outright
  on an actual message-field path (fields are addressed by name, never
  by index).

### Added
- With a `schema_provider`, the message now starts fully defaulted -
  every field at its schema zero value, before the first instruction
  runs. A bare `send;` with no field assignments at all sends a
  complete, schema-shaped message; a partial assignment overwrites only
  that field. The tree-building logic already existed
  (`SchemaProvider.default_at([])` already recursed the whole schema
  for an explicit `msg = default;`); this applies it automatically at
  run start instead of requiring that statement. No schema still starts
  empty, unchanged.
- `msg` - reads back the message as built so far (the same value `send`
  would currently emit) through the same `.`/`[...]` access as any
  other object value: `msg.header.frame_id`, `msg["temperature"]`.
  Reflects schema defaults if present, and anything statically written,
  but not a field still driven by an unresolved `live` binding (adding
  that would mean a live field whose own expression reads `msg` would
  need to resolve itself to build the very value it's asking for).
  Reserved, so it can never collide with a `var`; each read is an
  independent deep copy, so a `var` that captures part of it is never
  later affected by an unrelated field write reusing the same nested
  dict in place.
- Bare-name sugar for a top-level `msg` field: `angular` reads
  `msg.angular`, but only when nothing else already claims that name -
  a `var`, `t`/`_t`, a timer, or a built-in function/constant name all
  win outright and silently, with no ambiguity error. A `var` declared
  after the field was written takes the bare name over from that point
  on; the field stays reachable, only now by writing `msg.angular`
  explicitly. Never applies to a nested field (`header.frame_id`),
  since two different paths could share a leaf name (`linear.x`,
  `angular.x`), which would make a bare name genuinely ambiguous rather
  than merely shadowed.

### Fixed
- A `var` or `for` loop variable could silently shadow anything: an
  outer `var`, an enclosing `for` loop's own variable, even a built-in
  function or constant name (`var linear = 5;` used to just work). None
  of these are actually harmless - a `for` loop's variable is mutated
  throughout the loop's execution, not just given an initial value, so
  two bindings sharing a name silently corrupted each other while the
  loop ran (confirmed: an outer `var i` gets overwritten by a same-named
  `for i`, and nested `for i { for i { ... } }` corrupts the *outer*
  loop's own iteration count, ending it early). Now a compile-time
  error, everywhere a new binding is introduced (`var`, `for`), against
  everything already in scope, including the built-in function/constant
  namespace (`expr.py`'s new `RESERVED_NAMES`, exported alongside the
  existing `TIME_SHAPED_FUNCTIONS`). Sequential, non-overlapping `for`
  loops may still reuse a name freely - this is about overlapping
  scope, not the identifier.

### Added
- Arrays (`[expr, expr, ...]`) and objects (`json { key: expr, ... }`)
  as first-class values - assignable to a `var`, nestable inside each
  other, passable to `terop`. `arr[i]` and `obj["key"]`/`obj.key` index
  into them, chainable (`arr[0].header.frame_id`). `==`/`!=` compare
  them structurally for free via Python's own equality; every other
  operator (arithmetic, ordering, the numeric postfixes) rejects an
  array/object operand the same way it already rejected a string.
- A script never branches on whether a `schema_provider` exists - that
  choice belongs to the integration layer. `json {}` needs no schema
  either way (its own keys already name its fields). A bare array
  literal written directly as a field/`send` value is interpreted
  against whatever schema is present: fills fields by position if one
  is given, or is simply published as a plain array value if not -
  previously this was a hard error with no schema. `default` is the one
  exception and always requires a schema, inside an array or not, since
  there is no field to ask a missing schema for a zero value at.
- New signal-shape builtins - plain, pure functions of an explicit
  elapsed-time argument, same as `sin`/`cos`: `square(t, low, high,
  period)`, `triangle(t, low, high, period)`, `sawtooth(t, low, high,
  period)`, `sinusoidal_wave(t, amplitude, period)`, `damped_wave(t,
  amplitude, decay, period)` (a decaying sinusoid - the natural response
  shape of an underdamped 2nd-order system like an RLC circuit), and
  `noise(mean, stddev)` (one Gaussian random draw).
- Postfix result transforms `.scale(k)` (multiply) and `.add(k)`/`.bias(k)`
  (add - two names, one operation), general operators on any numeric
  value, chainable with each other and with `.s`/`.m`/`.ms` in any order:
  `_t.s.scale(2).add(1)`.
- `.shift(offset)`, recognized anywhere within the postfix chain that
  follows a function call's closing `)`: subtracts `offset` from the
  call's first argument before the call is made, then the call proceeds
  normally (once, or once per tick if wrapped in `live`/`!`, decided
  independently). Position-independent relative to `.scale`/`.add`/
  `.bias` in the same chain - `f(...).shift(k).scale(2)` and
  `f(...).scale(2).shift(k)` are equivalent, and multiple `.shift(...)`
  calls accumulate - since the whole chain is scanned before the call is
  made rather than applied left to right as it's encountered. Works on
  both bare and bang calls: `square(5, 0, 1, 10s).shift(3s)` and
  `square!(0, 1, 10s).shift(3s)`.
- `name!(args)` - live-call sugar for any function, as the whole
  assignment right-hand side: `sin!(t)` desugars to `live { return
  sin(t); };`. For the fixed set of time-shaped builtins (`linear`,
  `square`, `triangle`, `sawtooth`, `damped_wave`, `sinusoidal_wave`), `!`
  also injects `_t` as the leading argument so the call keeps its
  ergonomic shape -
  `linear!(20, 30, 10s)`, not `linear!(_t, 20, 30, 10s)`.
- `field = live <expr>;` - a one-line shorthand for `live { return
  <expr>; };`, for any live expression that isn't a single bang-callable
  function (`data = live t * 2 + 1;`).

### Changed
- **Breaking:** `linear(from, to, dur)` is no longer implicit magic sugar
  - it's an ordinary function, `linear(t, from, to, dur)`, taking elapsed
  time as an explicit first argument like any other function. Previously
  it was the *only* function name the parser silently auto-wrapped in a
  live block, which meant nothing about a call site told you whether it
  was live or a plain one-shot call - you had to already know `linear`
  was special. Now **nothing** is implicitly live; `live` (the keyword,
  the shorthand, or `!`) is the only thing that ever means "re-evaluates
  every tick," with zero hardcoded exceptions. Existing scripts using
  bare `linear(a, b, dur)` need `linear!(a, b, dur)` instead.
- Strings are now first-class expression values, not just an
  assignment-only literal: `+` concatenation, `==`/`!=`/`< <= > >=`
  comparison (ordering is lexicographic, requires both sides to be
  strings), and `and`/`or`/`not`/`terop` truthiness (non-empty = truthy).
  `frame_id = frame + "_link";` and `if frame == "map" { ... }` both work
  now. Mixing a string with a number in arithmetic, ordering, or the
  `.s`/`.m`/`.ms` unit view is a clear `ExprError`, never a silent
  coercion or a raw Python `TypeError`.

### Changed
- `StringLit` is gone from the AST - a bare string literal is just an
  `ExprSpan` whose text happens to be a quoted atom, evaluated by the
  same expression engine as everything else. No behavior change for
  existing scripts, only an internal simplification enabled by strings
  becoming real expression values.

### Fixed
- Two latent truthiness bugs, unreachable until strings could exist:
  `if`'s `JumpIfFalse` and a `live` block's `if` both checked
  `cond == 0.0` / `cond != 0.0` directly, which is wrong for a string (a
  str is never `==`/`!=` a float, so every string condition - even an
  empty one - would have taken the same branch regardless of truthiness).
  Both now go through a single `is_truthy()` helper.

## [0.1.1] — 2026-08-17

### Added
- `examples/stdout_signal.py` — NDJSON-to-stdout adapter, proving output is
  language-agnostic (any consumer, not just Python).
- `examples/websocket_signal.py` — broadcasts ticks to WebSocket clients,
  driven from an `asyncio` event loop instead of `run_realtime()`'s thread.
- `mypy` wired into CI (`dev` extra); the whole package now type-checks
  clean.
- Tag-triggered PyPI publishing via Trusted Publishing (OIDC) — no token
  stored anywhere.

### Fixed
- Package never actually ran on Python 3.9 despite claiming
  `requires-python = ">=3.9"` since 0.1.0 — `X | None` (PEP 604) only
  evaluates at runtime on 3.10+. Fixed with `from __future__ import
  annotations` across the six affected modules; caught by finally running
  CI for real, not by a user report.
- Two `float | None` narrowing gaps in `vm.py`'s `_send_is_done` (caught
  by adding `mypy`, not a reported bug — `dur_value` is always non-`None`
  for `dur_kind in ("tick", "wall")`, now asserted explicitly).

## [0.1.0] — 2026-08-16

Initial release.

### Added
- Expression engine: arithmetic, comparisons, `and`/`or`/`not`, `terop`,
  a fixed function whitelist, postfix `.s`/`.m`/`.ms` unit view.
- Statement language: `var`, `if`/`else if`/`else`, `repeat`/`repeat N`,
  `for i in A..B`, `live { }` blocks with an automatic per-binding `_t`,
  `timer()`/`latching_timer()`/`.reset()`, string literals, `default`,
  positional array fill.
- Compiler: lowers the AST to a flat instruction tape
  (`SetVar`/`SetField`/`Send`/`Jump`/`JumpIfFalse`/`CreateTimer`/`ResetTimer`).
- `ScriptRun.step()`: a stepped VM — no `sleep`, no clock, time is counted
  not waited for.
- `run_realtime()`: opt-in blocking real-time driver, the only module
  that imports `time`.
- `SchemaProvider` protocol + `DictSchemaProvider` test double, for
  `default` and positional-fill support against an arbitrary message
  schema.
- 112 tests, zero runtime dependencies, PEP 561 `py.typed` marker.
