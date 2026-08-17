# Examples

Three small proofs that signallang isn't ROS-specific — the same
`compile_script()` → `step()` contract, handed to different real-time
hosts and script shapes.

## `stdout_signal.py`

Prints each tick as one line of JSON to stdout. The plainest possible
adapter: no ROS, no server, no Python-specific wire format on the
receiving end — any language that can read a line and parse JSON can
consume it (a five-line C++ reader works exactly as well as a Python one).
One `linear!` ramp, one `noise!` jitter field, no schema.

```bash
pip install signallang
python3 stdout_signal.py
```

## `schema_signal.py`

Same stdout adapter, a richer script: a `schema_provider`
(`DictSchemaProvider`) so unassigned fields (`linear.y`, `linear.z`)
still publish at their schema zero value, a nested `json {}` object
built fresh every tick inside a `live` block, and `brown_motion!` for a
persisted, accumulating drift field.

```bash
pip install signallang
python3 schema_signal.py
```

## `websocket_signal.py`

Broadcasts each tick to any connected WebSocket client, driven from an
`asyncio` event loop instead of `run_realtime()`'s blocking thread —
proof that `step()` slots into whatever clock the host already owns.

```bash
pip install signallang websockets
python3 websocket_signal.py
```

Then, from a browser console:

```js
const ws = new WebSocket("ws://localhost:8765");
ws.onmessage = (e) => console.log(e.data);
```
