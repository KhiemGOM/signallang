#!/usr/bin/env python3
"""Pipe synthetic signal values to stdout as newline-delimited JSON (NDJSON).

No ROS, no framework - just plain text on stdout, which means the
consuming side isn't Python-specific either. Any program that can read a
line and parse JSON works, including a few lines of C++:

    std::string line;
    while (std::getline(std::cin, line)) {
        auto msg = nlohmann::json::parse(line);
        std::cout << "temperature=" << msg["temperature"] << "\n";
    }

Run it:
    pip install signallang
    python3 examples/stdout_signal.py
    python3 examples/stdout_signal.py | your_consumer
"""
import json

from signallang import compile_script, run_realtime

SCRIPT = """
temperature = linear!(20, 30, 10s);
jitter = noise!(0, 0.1);
send hz 2 dur inf;
"""


def main():
    compiled = compile_script(SCRIPT)
    run_realtime(compiled, on_send=lambda msg: print(json.dumps(msg), flush=True))


if __name__ == "__main__":
    main()
