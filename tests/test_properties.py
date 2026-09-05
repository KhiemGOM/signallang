"""Bounded generated-input checks for the expression compiler and stepped VM."""

import operator

from hypothesis import given, settings
from hypothesis import strategies as st

from signallang import ScriptError, compile_script, evaluate

SETTINGS = settings(max_examples=200, deadline=None, database=None, derandomize=True)
integers = st.integers(-100, 100)
trees = st.recursive(
    integers.map(lambda value: (str(value), value)),
    lambda children: st.tuples(st.sampled_from(["+", "-", "*"]), children, children).map(
        lambda item: (
            f"({item[1][0]} {item[0]} {item[2][0]})",
            {"+": operator.add, "-": operator.sub, "*": operator.mul}[item[0]](item[1][1], item[2][1]),
        )
    ),
    max_leaves=15,
)


@SETTINGS
@given(trees)
def test_arithmetic_matches_independently_computed_tree(tree):
    source, expected = tree
    assert evaluate(source, {}) == expected


@SETTINGS
@given(st.text(alphabet='abcxyz0123456789+-*/%()[]{}.;,! \n"#', max_size=120))
def test_malformed_input_never_leaks_implementation_exceptions(source):
    try:
        run = compile_script(source).new_run(operation_budget=1000, step_instruction_budget=1000)
        run.collect(3)
    except ScriptError:
        pass


@SETTINGS
@given(st.integers(1, 50), st.integers(1, 30))
def test_finite_tick_schedule_yields_exact_count_and_time(rate, ticks):
    run = compile_script(f"data = live t; send hz {rate} dur {ticks}t;").new_run()
    results = run.collect(ticks + 1)
    assert len(results) == ticks
    assert [r.sequence for r in results] == list(range(ticks))
    assert abs(run.master_t - ticks / rate) < 1e-10
    assert all(a.timestamp < b.timestamp for a, b in zip(results, results[1:]))


@SETTINGS
@given(st.lists(st.integers(-100, 100), max_size=20))
def test_external_container_mutation_has_value_semantics(items):
    source = "extern input; var copy = input; copy.value = 9; send input dur 1t;"
    original = {"items": items}
    run = compile_script(source).new_run(external_params={"input": original})
    assert run.step().value == original == {"items": items}
