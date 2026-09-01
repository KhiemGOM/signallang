import json

import pytest

from signallang.cli import main


def test_validate_reports_ok_for_a_valid_script(tmp_path, capsys):
    script = tmp_path / "demo.signal"
    script.write_text("data = 5;\nsend hz 1 dur 1t;")

    code = main(["validate", str(script)])

    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_validate_reports_a_located_error_with_a_caret(tmp_path, capsys):
    script = tmp_path / "demo.signal"
    script.write_text("foobar_not_a_keyword;\nsend hz 1 dur 1t;")

    code = main(["validate", str(script)])
    err = capsys.readouterr().err

    assert code == 1
    assert "1:21" in err
    assert "^" in err
    assert "(at position" not in err  # not printed twice alongside line:col


def test_run_prints_one_json_line_per_sent_message(tmp_path, capsys):
    script = tmp_path / "demo.signal"
    script.write_text("data = 5;\nsend hz 1 dur 3t;")

    code = main(["run", str(script)])
    lines = capsys.readouterr().out.strip().splitlines()

    assert code == 0
    assert [json.loads(line) for line in lines] == [{"data": 5.0}] * 3


def test_run_stops_after_the_requested_tick_count(tmp_path, capsys):
    script = tmp_path / "demo.signal"
    script.write_text("var x = 0;\nrepeat {\n x = x + 1;\n data = x;\n send hz 1 dur 1t;\n}")

    code = main(["run", str(script), "--ticks", "3"])
    lines = capsys.readouterr().out.strip().splitlines()

    assert code == 0
    assert len(lines) == 3


def test_run_accepts_ext_overrides_parsed_as_json(tmp_path, capsys):
    script = tmp_path / "demo.signal"
    script.write_text('extern topic = "unknown";\ndata = topic;\nsend hz 1 dur 1t;')

    code = main(["run", str(script), "--ext", "topic=/cmd_vel"])
    out = capsys.readouterr().out

    assert code == 0
    assert json.loads(out.strip()) == {"data": "/cmd_vel"}


def test_run_reports_the_infinite_loop_budget_error(tmp_path, capsys):
    script = tmp_path / "demo.signal"
    script.write_text("var x = 0;\nrepeat {\n x = x + 1;\n}")

    code = main(["run", str(script), "--step-instruction-budget", "500"])
    err = capsys.readouterr().err

    assert code == 1
    assert "step_instruction_budget" in err


def test_no_command_exits_nonzero_instead_of_crashing(capsys):
    with pytest.raises(SystemExit):
        main([])
