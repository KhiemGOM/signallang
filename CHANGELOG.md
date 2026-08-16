# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
