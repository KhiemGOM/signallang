# signallang: repository index and general audit

Date: 2026-09-05. Scope: local source, README, changelog, examples, tests, and CI/release workflows. No library code was changed. This is a general engineering audit, not a complete security assessment.

## Assessment

The architecture fits the stated goal well: a small, framework-independent language for scheduled synthetic messages. A stepped VM, explicit simulated time, live bindings, schema adapters, and zero runtime dependencies are useful foundations. The strongest next investment is semantic consistency and reliable embedding before expanding the builtin catalog.

Validation performed: **467 existing tests passed on Python 3.12.14**. Additional small, bounded runtime probes reproduced the findings below. The declared Python 3.9–3.12 matrix was inspected but only 3.12 was run locally. No long-running hostile payloads, external services, or live publishing were exercised.

## Repository index

The core contains 11 Python files / 3,343 lines, tests contain 9 files / 2,658 lines, and examples contain 3 files / 141 lines. Counts include comments and blank lines.

| Area | Entry point | Responsibility |
| --- | --- | --- |
| Public API | [__init__.py](D:/Coding/Python/signallang/src/signallang/__init__.py) | Compilation, stepping, expression helpers, schemas, errors, real-time driver exports |
| Statement parsing | [parser.py](D:/Coding/Python/signallang/src/signallang/parser.py) | Scope bookkeeping, control flow, macros, live/bang syntax, durations |
| Expression execution | [expr.py](D:/Coding/Python/signallang/src/signallang/expr.py) | Recursive expression parser/evaluator, primitive types, builtins, random distributions |
| Syntax representation | [ast_nodes.py](D:/Coding/Python/signallang/src/signallang/ast_nodes.py), [span.py](D:/Coding/Python/signallang/src/signallang/span.py) | Statement nodes and source scanning; expression nodes retain text only |
| Lowering | [compiler.py](D:/Coding/Python/signallang/src/signallang/compiler.py) | Flat instruction tape, jumps, timers, send/wait instructions |
| Runtime | [vm.py](D:/Coding/Python/signallang/src/signallang/vm.py) | Run state, simulated time, variables, externs, live values, message assembly |
| Schema adapter | [schema.py](D:/Coding/Python/signallang/src/signallang/schema.py) | Defaults, positional fields, optional type checks |
| Real-time delivery | [realtime.py](D:/Coding/Python/signallang/src/signallang/realtime.py) | Blocking callback/sleep loop |
| CLI and diagnostics | [cli.py](D:/Coding/Python/signallang/src/signallang/cli.py), [errors.py](D:/Coding/Python/signallang/src/signallang/errors.py) | Validate/run commands and error formatting |
| Integrations | [examples](D:/Coding/Python/signallang/examples) | Stdout, WebSocket, and schema usage |
| Verification and release | [tests](D:/Coding/Python/signallang/tests), [test.yml](D:/Coding/Python/signallang/.github/workflows/test.yml), [release.yml](D:/Coding/Python/signallang/.github/workflows/release.yml) | Unit/integration coverage, Python matrix, mypy, package publishing |

Execution path: source → statement AST → flat instruction tape → ScriptRun.step() → StepResult → caller-controlled delivery. Expressions are parsed again when evaluated rather than compiled into reusable expression nodes.

## Priority findings

P1 means address before relying on the affected behavior for embedding or simulation accuracy. P2 means a correctness or usability gap worth addressing next. Findings are reproducible unless explicitly described as a code-inspection inference.

### 1. P1 — Extern values can mutate the host through aliases

Location: [vm.py:120](D:/Coding/Python/signallang/src/signallang/vm.py:120), [vm.py:281](D:/Coding/Python/signallang/src/signallang/vm.py:281).

Extern values are retained by reference. Direct assignment to an extern is rejected, but assigning a compound extern to a variable preserves its identity:

```text
extern config;
var alias = config;
alias.x = 9;
send dur 1t;
```

Supplying `{"config": {"x": 1}}` changes the caller's dictionary to `{"config": {"x": 9}}` after stepping. This contradicts the stated read-only contract and can couple multiple runs.

Recommendation: validate the supported external value tree, defensively copy host inputs, and define whether reads of extern containers yield copies or immutable values. Copying only at new_run protects the host but does not by itself make the run's extern values read-only.

### 2. P1 — The instruction budget does not bound work inside expressions

Location: [vm.py:132](D:/Coding/Python/signallang/src/signallang/vm.py:132), [expr.py:175](D:/Coding/Python/signallang/src/signallang/expr.py:175).

With `step_instruction_budget=1`, `data = binomial(1000, 0.5); send dur 1t;` performs 1,000 random draws and succeeds. The builtin's trial count has no upper limit. Larger counts can therefore monopolize a host thread despite the budget; this scaling conclusion follows from the loop implementation, not from running a huge payload. Live expression work also executes outside the instant-instruction counter.

Recommendation: apply an operation budget inside expression evaluation and expensive builtins, plus explicit input-size/depth/value-size limits. Describe the current protection as an instant-instruction loop guard rather than a complete resource sandbox. Rate limiting simulated messages does not limit CPU use when the host steps as fast as possible.

### 3. P1 — Poisson sampling is incorrect at large rates

Location: [expr.py:157](D:/Coding/Python/signallang/src/signallang/expr.py:157).

For `poisson(1000)`, 200 draws after `random.seed(123)` averaged **745.01**, rather than approximately 1,000. `exp(-lam)` and the accumulated product underflow; this is an accuracy failure, not merely the slowness mentioned in the code comment.

Recommendation: use a numerically stable large-rate method or explicitly reject rates outside the supported range. Add deterministic statistical checks with suitable tolerances across small and large rates, alongside finite-parameter validation.

### 4. P1 — Live values bypass schema type checks

Location: [vm.py:424](D:/Coding/Python/signallang/src/signallang/vm.py:424), [vm.py:447](D:/Coding/Python/signallang/src/signallang/vm.py:447).

With `DictSchemaProvider({"level": 0})`, `level = "wrong";` raises a type error, but `level = live "wrong"; send dur 1t;` publishes `{"level": "wrong"}`. Live message assembly never invokes the field type check.

Recommendation: validate live output before returning a sent result. Separately consider an optional strict schema mode for unknown fields, recursive object validation, and array element types. Whole-object validation is currently documented as incomplete, so that broader restriction would be a feature change rather than just a bug fix.

### 5. P1 — Latching timers start on unrelated expression evaluation

Location: [vm.py:231](D:/Coding/Python/signallang/src/signallang/vm.py:231).

```text
var lt = latching_timer();
unrelated = 123;
wait 2s;
elapsed = lt.s;
send dur 1t;
```

The first explicit read publishes `elapsed = 2.0`, where the documented first-read behavior implies `0.0`. Building any expression's scope resolves every named timer and therefore latches it early.

Recommendation: resolve timer identifiers on actual access, without reading every timer when constructing the scope.

### 6. P2 — Parent/message replacement leaves old child live bindings active

Location: [vm.py:350](D:/Coding/Python/signallang/src/signallang/vm.py:350), [vm.py:447](D:/Coding/Python/signallang/src/signallang/vm.py:447).

`header.x = live 1; send dur 1t; header = json {x: 9}; send dur 1t;` publishes `x = 1` twice. Likewise, after `x = live 1`, sending `json {y: 9}` still includes `x = 1`. Assignment removes only an exactly matching live path, allowing an old descendant to overwrite its replacement.

Recommendation: define ownership for overlapping live paths and clear affected bindings on subtree/whole-message replacement. Test both parent-over-child and child-over-parent assignments.

### 7. P2 — Seeded runs are not isolated or independently reproducible

Location: [vm.py:160](D:/Coding/Python/signallang/src/signallang/vm.py:160), [expr.py:128](D:/Coding/Python/signallang/src/signallang/expr.py:128).

All runs use Python's shared random module. A script's output changes when another run is interleaved, even when both use `seed(42)`. Seeding a script also resets random state used by unrelated host code. The README acknowledges shared state, but the resulting behavior weakens reproducible simulation and parallel test scenarios.

Recommendation: give each ScriptRun its own random.Random instance; route all distributions through it. An optional host seed/RNG parameter would make controlled tests easier.

### 8. P2 — Validation accepts malformed expressions; runtime errors lose source context

Location: [parser.py:283](D:/Coding/Python/signallang/src/signallang/parser.py:283), [ast_nodes.py:13](D:/Coding/Python/signallang/src/signallang/ast_nodes.py:13), [cli.py:53](D:/Coding/Python/signallang/src/signallang/cli.py:53).

`compile_script("data = 1 + ; send dur 1t;")` succeeds, so the validate command reports success. `send dur 1t; data = 1 + ; send dur 1t;` sends a message before failing on its second step. This contradicts the README's broad claim that malformed scripts fail before any value is sent.

ExprSpan retains only text, so runtime expression errors lack an original file position. Some errors also escape the CLI's handled exception types: `1 % 0` raises ZeroDivisionError, `1.2.3` raises ValueError, and `exponential(1000, 1, 1)` raises OverflowError.

Recommendation: parse expressions into an AST during compilation, retain source ranges and macro call-site information, validate syntax/function names/arity without execution, and normalize expected runtime failures into located language errors. Runtime-dependent failures will still require runtime checks; document that distinction.

### 9. P2 — Live local durations and static initialization do not compose

Location: [parser.py:856](D:/Coding/Python/signallang/src/signallang/parser.py:856), [vm.py:356](D:/Coding/Python/signallang/src/signallang/vm.py:356).

These accepted programs fail when stepped:

```text
data = live { var d = 2s; return linear(_t, 0, 1, d); };
data = live { static a = 1; static b = a + 1; return b; };
data = live { static a = _t; return a; };
```

The first loses local duration metadata when parsing restores the outer scope. The second evaluates initializers without earlier statics. The third evaluates initialization without the binding's timer scope. Separately, `var d = 2s; d = 7;` retains its duration classification despite the reassignment.

Recommendation: carry scope/type metadata with each compiled binding, initialize statics sequentially in that binding's environment, and define duration behavior across reassignment and branches.

### 10. P2 — Macro hygiene can rename published field names

Location: [parser.py:122](D:/Coding/Python/signallang/src/signallang/parser.py:122), [parser.py:719](D:/Coding/Python/signallang/src/signallang/parser.py:719).

```text
func build(field) { var x = 1; field = json {x: x}; }
build(out);
send dur 1t;
```

This publishes `{"out": {"__build_1_x": 1}}`. Text substitution cannot distinguish a local variable reference from an object key.

Recommendation: perform hygienic renaming on parsed identifiers with syntactic roles; preserve object keys and field-path segments.

### 11. P2 — Real-time pacing accumulates evaluation cost

Location: [realtime.py:15](D:/Coding/Python/signallang/src/signallang/realtime.py:15).

The clock measurement starts after step(), so VM evaluation time is added to each requested period. A deterministic fake-clock probe with a 20 ms step cost and 10 Hz schedule produced sends at 0.02, 0.14, and 0.26 seconds: 120 ms apart instead of 100 ms.

Recommendation: pace against cumulative monotonic deadlines and expose an explicit overrun policy. The convenience driver should also accept an existing ScriptRun or forward external_params and budget options; its current unconditional new_run() cannot run a script requiring host externs. Cancellation and an async helper would improve embedding.

### 12. P2 — Parameter and primitive-type validation is uneven

Location: [compiler.py:274](D:/Coding/Python/signallang/src/signallang/compiler.py:274), [expr.py:576](D:/Coding/Python/signallang/src/signallang/expr.py:576), [expr.py:912](D:/Coding/Python/signallang/src/signallang/expr.py:912).

Confirmed behavior: max_hz=0 reaches a raw division-by-zero error; `send dur 0t` sends one message; `send dur 1.5t` sends two. `+true` returns True and `sqrt(true)` returns 1.0 even though the language otherwise separates Bool from numeric operations. Boolean and/or evaluate their right-hand sides eagerly, so `false and (1 / 0)` raises; unlike eager terop, this behavior is not clearly documented.

Recommendation: specify and validate positive finite rates, duration/tick rounding, integer tick counts, budgets, builtin argument types, and logical evaluation rules. Distinguish intentional semantics from Python behavior inherited accidentally.

### 13. P2 — Documentation examples and integration edges need executable checks

Location: [README.md:148](D:/Coding/Python/signallang/README.md:148), [README.md:417](D:/Coding/Python/signallang/README.md:417), [websocket_signal.py:49](D:/Coding/Python/signallang/examples/websocket_signal.py:49).

Many DSL examples use `#` comments, but the parser supports `//` only; copying a commented example fails. The send reference describes bare `send;` as sending once while its default duration is infinite. Several output comments still show floats where the language now emits integers.

The WebSocket example also does not check result.sent and does not handle per-client send failures. Its current script contains no wait, so the first issue is latent until users adapt the example; a disconnected client's send exception can escape the gather call and stop the driver (code-inspection finding).

Recommendation: execute complete README snippets in tests, correct bare-send/value-wrapper documentation, and test the example driver with wait ticks and disconnecting clients.

## Additions most aligned with the goals

1. **Compile-time expression AST and diagnostics.** This addresses validation, source mapping, lazy identifier resolution, scope metadata, and repeated parsing overhead together. Preserve the small fixed expression grammar and clock-free VM.
2. **Run-owned randomness and trace metadata.** Include simulated timestamp, tick/sequence number, and time until the next step in results; offer an iterator or bounded trace collector. This makes scripted sensor feeds easy to replay, plot, and compare in tests.
3. **Host-parameterized schedules.** Allow validated extern/duration expressions in hz, send duration, and wait, with explicit evaluation timing. Currently host parameters cannot conveniently control the schedule itself.
4. **Keyframes or piecewise interpolation.** A compact list of time/value points would express startup, ramp, plateau, and shutdown scenarios more directly than long blocks of assignments and sends. Hold, linear interpolation, and repeat behavior would cover many practical scenarios.
5. **Optional strict schemas.** Add recursive validation, unknown-field handling, array element types, and a deliberate numeric conversion policy while retaining the simple adapter interface.
6. **Delivery controls.** Add deadline-based real-time/async drivers, cancellation, late-tick policy, and bounded delivery/backpressure options outside the VM.

## Verification priorities

The existing suite provides broad coverage of individual features. The reproduced gaps are mostly interactions: externs plus alias mutation, live values plus schemas, timers plus unrelated expressions, macros plus object keys, and multiple seeded runs.

Prioritize regression tests for those combinations, statistical sampler tests, executable documentation, and bounded property-based parser/runtime tests. Add benchmarks before optimizing: expressions currently reparse on every evaluation and scope construction deep-copies the message even for constant expressions. Check scaling with message size, live field count, and expressions per tick. Some older tests compare booleans/integers against numeric equivalents; use explicit type assertions where type preservation is the contract.

Suggested order: protect host state and computation limits; fix sampling, schema, timer, and binding correctness; introduce expression compilation and diagnostics; then add schedule ergonomics and delivery helpers.
