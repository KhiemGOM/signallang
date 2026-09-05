import pytest

from signallang import ScriptError, compile_script


def run_all(src, external_params=None, limit=10_000):
    run = compile_script(src).new_run(external_params=external_params)
    out = []
    for _ in range(limit):
        r = run.step()
        if r is None:
            return out, True  # halted
        out.append(r)
    return out, False


def test_host_supplied_value_is_readable_by_bare_name():
    results, _ = run_all(
        "extern ros_topic;\ndata = ros_topic;\nsend hz 1 dur 1t;",
        external_params={"ros_topic": "/cmd_vel"},
    )
    assert results[0].value["data"] == "/cmd_vel"


def test_default_used_when_host_does_not_supply_it():
    results, _ = run_all('extern ros_topic = "unknown";\ndata = ros_topic;\nsend hz 1 dur 1t;')
    assert results[0].value["data"] == "unknown"


def test_host_supplied_value_overrides_default():
    results, _ = run_all(
        'extern ros_topic = "unknown";\ndata = ros_topic;\nsend hz 1 dur 1t;',
        external_params={"ros_topic": "/cmd_vel"},
    )
    assert results[0].value["data"] == "/cmd_vel"


def test_missing_required_extern_raises_at_new_run_not_compile():
    compiled = compile_script("extern ros_topic;\ndata = ros_topic;\nsend hz 1 dur 1t;")
    with pytest.raises(ScriptError, match="ros_topic"):
        compiled.new_run()


def test_two_externs_together_matches_the_ros_topic_ros_schema_use_case():
    results, _ = run_all(
        "extern ros_topic;\nextern ros_schema;\ndata = ros_topic;\nsend hz 1 dur 1t;",
        external_params={"ros_topic": "/cmd_vel", "ros_schema": "geometry_msgs/msg/Twist"},
    )
    assert results[0].value["data"] == "/cmd_vel"


def test_externs_readable_by_host_independent_of_the_script():
    run = compile_script("extern ros_topic;\nsend hz 1 dur 1t;").new_run(external_params={"ros_topic": "/cmd_vel"})
    assert run.externs["ros_topic"] == "/cmd_vel"


def test_externs_dict_is_separate_from_vars_not_merged_in():
    run = compile_script("var x = 5;\nextern ros_topic;\nsend hz 1 dur 1t;").new_run(
        external_params={"ros_topic": "/cmd_vel"}
    )
    assert "ros_topic" not in run.vars
    assert "x" not in run.externs


def test_assigning_to_an_extern_is_a_compile_time_error():
    with pytest.raises(ScriptError, match="cannot assign"):
        compile_script('extern ros_topic;\nros_topic = "/other";\nsend;')


def test_extern_name_collides_with_var_declared_first():
    with pytest.raises(ScriptError, match="already in scope"):
        compile_script("var ros_topic = 5;\nextern ros_topic;\nsend;")


def test_var_name_collides_with_extern_declared_first():
    with pytest.raises(ScriptError, match="already in scope"):
        compile_script("extern ros_topic;\nvar ros_topic = 5;\nsend;")


def test_extern_usable_in_a_comparison():
    results, _ = run_all(
        'extern ros_topic;\ndata = terop(ros_topic == "/cmd_vel", true, false);\nsend hz 1 dur 1t;',
        external_params={"ros_topic": "/cmd_vel"},
    )
    assert results[0].value["data"] is True


def test_extern_readable_inside_a_live_block():
    results, _ = run_all(
        "extern base;\ndata = live { return base + t; };\nsend hz 1 dur 3t;",
        external_params={"base": 100.0},
    )
    assert [r.value["data"] for r in results] == [100.0, 101.0, 102.0]


def test_extraneous_host_supplied_keys_are_ignored():
    # the script only declares ros_topic - a host passing extra context
    # keys it doesn't use shouldn't be an error, since the same
    # external_params dict might reasonably be reused across scripts
    # that each only care about a subset of it.
    results, _ = run_all(
        "extern ros_topic;\ndata = ros_topic;\nsend hz 1 dur 1t;",
        external_params={"ros_topic": "/cmd_vel", "unused_key": 123},
    )
    assert results[0].value["data"] == "/cmd_vel"
