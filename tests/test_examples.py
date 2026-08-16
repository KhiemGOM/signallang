"""End-to-end re-verification of all five worked examples from the Publish
Pattern Language artifact, against hand-computed expected values."""

import pytest

from signallang import compile_script


def collect(src, n):
    run = compile_script(src).new_run()
    out = []
    for _ in range(n):
        r = run.step()
        out.append(r)
        if r is None:
            break
    return out


def test_example1_temperature_drift():
    src = "temperature = linear(20, 30, 10s);\nsend hz 2 dur inf;"
    results = collect(src, 40)
    # hz=2 -> 0.5s per tick; ramp 20->30 over 10s = 20 ticks to reach 30
    assert results[0].value["temperature"] == pytest.approx(20.0)
    assert results[10].value["temperature"] == pytest.approx(25.0)  # t=5s, halfway
    assert results[19].value["temperature"] == pytest.approx(29.5)  # t=9.5s
    assert results[20].value["temperature"] == pytest.approx(30.0)  # t=10s, reached
    assert results[39].value["temperature"] == pytest.approx(30.0)  # holds forever


def test_example2_battery_low_warning():
    src = "data = false;\nsend dur 15s;\ndata = true;\nsend dur inf;"
    run = compile_script(src).new_run()
    results = []
    for _ in range(752):
        results.append(run.step())
    # unspecified hz defaults to MAX_HZ (50.0) -> 15s * 50Hz = 750 ticks of False
    assert all(r.value["data"] == 0.0 for r in results[:750])
    assert all(r.value["data"] == 1.0 for r in results[750:])


def test_example3_lidar_sweep_exactly_seven_then_stops():
    src = "for i in 0..7 {\n    data = i * 30;\n    send hz 1 dur 1t;\n}"
    results = collect(src, 10)
    values = [r.value["data"] for r in results if r is not None]
    assert values == [0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0]
    assert results[7] is None  # halted for good, no trailing infinite send


def test_example4_heartbeat_counter_unbounded():
    src = "for i in 0..inf {\n    data = i;\n    send hz 1 dur 1t;\n}"
    results = collect(src, 500)
    assert [r.value["data"] for r in results[:5]] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert results[499].value["data"] == 499.0


def test_example5_motion_ramp_cycle_restarts_each_lap():
    src = """repeat {
    linear.x = linear(0, 1.0, 3s);
    send hz 10 dur 3s;

    linear.x = 1.0;
    send hz 10 dur 5s;

    linear.x = linear(1.0, 0, 3s);
    send hz 10 dur 3s;

    linear.x = 0.0;
    send hz 10 dur 2s;
}"""
    # one lap = 3+5+3+2 = 13s at hz10 = 130 ticks (indices 0..129).
    # Each tick reports the value BEFORE that tick's own time-advance, so a
    # wall-clock `dur 3s` ramp at hz10 (30 ticks, period 0.1s) tops out at
    # _t=2.9 on its last tick (0.967), not exactly 1.0 - the clean 1.0 comes
    # from the *next* statement's static assignment, one tick later.
    results = collect(src, 131)
    xs = [round(r.value["linear"]["x"], 3) for r in results]
    assert xs[0] == 0.0  # ramp-up starts
    assert xs[29] == pytest.approx(0.967, abs=1e-3)  # last ramp-up tick (_t=2.9)
    assert xs[30] == 1.0  # first hold tick - now a static 1.0, ramp phase over
    assert xs[79] == 1.0  # last hold tick
    assert xs[80] == 1.0  # first ramp-down tick - fresh live binding, _t=0
    assert xs[109] == pytest.approx(0.033, abs=1e-3)  # last ramp-down tick (_t=2.9)
    assert xs[110] == 0.0  # first hold-at-0 tick - static 0.0
    assert xs[129] == 0.0  # last tick of the lap
    assert xs[130] == 0.0  # next lap's ramp restarts cleanly from 0
