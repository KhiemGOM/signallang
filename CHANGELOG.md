# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
