from signallang import compile_file


def test_compile_file_reads_and_compiles_a_signal_file(tmp_path):
    script_path = tmp_path / "demo.signal"
    script_path.write_text("data = 5;\nsend hz 1 dur 1t;")

    run = compile_file(script_path).new_run()
    result = run.step()

    assert result.value["data"] == 5.0


def test_compile_file_accepts_a_plain_string_path_too(tmp_path):
    script_path = tmp_path / "demo.signal"
    script_path.write_text("data = 7;\nsend hz 1 dur 1t;")

    run = compile_file(str(script_path)).new_run()
    result = run.step()

    assert result.value["data"] == 7.0
