# patternlang

A small, safe expression language for describing time-varying synthetic
data — no `eval`/`exec`, no user-defined functions, nothing that reaches
outside the value it's computing.

It was designed to drive a ROS2 "fake publisher" (make a topic emit
synthetic sensor/status data for testing a subscriber in isolation), but
nothing in the language itself is ROS-specific — it's just a restricted
arithmetic and boolean expression evaluator, usable anywhere you want a
non-technical, un-Google-able way to describe "this value, but changing
over time."

## What's here

This repo currently ships the **expression layer** — arithmetic,
comparisons, `and`/`or`/`not`, a small function whitelist, and `terop`
(a named ternary, not `? :`). A statement-level layer on top of it
(`var`, `if {} else {}`, `repeat`/`for`, `live` blocks with an automatic
per-binding timer, `send hz/dur`) is designed but not yet built — see
[Roadmap](#roadmap).

```python
from patternlang import evaluate

evaluate("sin(t)", {"t": 1.57})
# 0.9999996829318346

evaluate("terop(t < 4.5 and battery > 0, 1, 0)", {"t": 2.0, "battery": 80.0})
# 1.0
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

The full design — `msg`, static vs. `live` field assignment, `var`
locals, real `if {} else {}` blocks, `repeat`/`for i in A..B`, `timer()`
/ `latching_timer()` / the automatic per-`live`-block `_t`, `send hz X
dur Y` (including tick-count durations), positional array fill against a
schema, and the `default` placeholder — is written up in full but not
yet implemented on top of this expression layer. That's the next phase.
