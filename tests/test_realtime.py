from patternlang import compile_script
from patternlang.realtime import run_realtime


def test_run_realtime_calls_on_send_once_per_tick_in_order(monkeypatch):
    monkeypatch.setattr("patternlang.realtime.time.sleep", lambda _: None)
    monkeypatch.setattr("patternlang.realtime.time.monotonic", lambda: 0.0)

    compiled = compile_script("for i in 0..5 {\n data = i;\n send hz 1 dur 1t;\n}")
    received = []
    run_realtime(compiled, on_send=lambda v: received.append(v["data"]))

    assert received == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_run_realtime_stops_when_step_returns_none(monkeypatch):
    monkeypatch.setattr("patternlang.realtime.time.sleep", lambda _: None)
    monkeypatch.setattr("patternlang.realtime.time.monotonic", lambda: 0.0)

    compiled = compile_script("send hz 10 dur 3t;")
    calls = []
    run_realtime(compiled, on_send=lambda v: calls.append(v))

    assert len(calls) == 3
