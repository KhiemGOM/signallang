#!/usr/bin/env python3
"""Publish a schema-shaped, nested message to stdout as NDJSON - the
pieces stdout_signal.py's single flat field doesn't show: a
schema_provider, a nested json {} object, and a persisted accumulator.

DictSchemaProvider here is a plain-dict test double, not a real ROS
type generator (see README's "Development" section for how a real
adapter wraps message reflection instead). With it, fields never
explicitly assigned - linear.y, linear.z below - still publish at
their schema zero value, because the message starts fully defaulted
before the first instruction runs.

Run it:
    pip install signallang
    python3 examples/schema_signal.py
"""
import json

from signallang import DictSchemaProvider, compile_script, run_realtime

SCHEMA = DictSchemaProvider(
    {
        "header": {"frame_id": "", "stamp": 0.0},
        "linear": {"x": 0.0, "y": 0.0, "z": 0.0},
        "angular_drift": 0.0,
    }
)

SCRIPT = """
header = live { return json { frame_id: "base_link", stamp: t }; };
linear.x = linear!(0, 1.0, 5s);
angular_drift = brown_motion!(0, 0.01);
send hz 5 dur inf;
"""


def main():
    compiled = compile_script(SCRIPT, schema_provider=SCHEMA)
    run_realtime(compiled, on_send=lambda msg: print(json.dumps(msg), flush=True))


if __name__ == "__main__":
    main()
