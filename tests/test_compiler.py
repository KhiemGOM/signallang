import pytest

from signallang.compiler import Jump, JumpIfFalse, SendInstr, SetVar, compile_program
from signallang.errors import ScriptError
from signallang.parser import parse


def test_if_else_lowers_to_jumps():
    instrs = compile_program(parse("if t < 1 {\n data = 1;\n} else {\n data = 2;\n}"))
    kinds = [type(i).__name__ for i in instrs]
    assert kinds[0] == "JumpIfFalse"
    assert "Jump" in kinds  # the then-branch's jump-over-else


def test_repeat_n_lowers_identically_to_equivalent_for():
    a = compile_program(parse("repeat 5 {\n data = 1;\n send;\n}"))
    b = compile_program(parse("for i in 0..5 {\n data = 1;\n send;\n}"))
    # same instruction *shape* (types + relative structure), different
    # synthesized loop-variable name only.
    assert [type(i).__name__ for i in a] == [type(i).__name__ for i in b]


def test_for_inf_end_compiles_unconditional_backedge():
    instrs = compile_program(parse("for i in 0..inf {\n data = i;\n send hz 1 dur 1t;\n}"))
    kinds = [type(i).__name__ for i in instrs]
    assert "JumpIfFalse" not in kinds
    assert kinds.count("Jump") == 1


def test_linear_desugars_to_live_block_with_created_timer():
    instrs = compile_program(parse("temperature = linear!(20, 30, 10s);\nsend;"))
    kinds = [type(i).__name__ for i in instrs]
    assert kinds[0] == "CreateTimer"
    assert kinds[1] == "SetField"


def test_send_variants_compile_to_one_send_instruction_each():
    for src, expected_kind, expected_val in [
        ("send hz 10 dur 4.5s;", "wall", 4.5),
        ("send hz 1 dur 10t;", "tick", 10.0),
        ("send;", "inf", None),
    ]:
        instrs = compile_program(parse(src))
        sends = [i for i in instrs if isinstance(i, SendInstr)]
        assert len(sends) == 1
        assert sends[0].dur_kind == expected_kind
        assert sends[0].dur_value == expected_val


def test_dur_inf_inside_infinite_repeat_before_end_is_a_compile_error():
    with pytest.raises(ScriptError):
        compile_program(parse("repeat {\n data = 1;\n send dur inf;\n data = 2;\n send dur 1s;\n}"))


def test_dur_inf_as_last_statement_of_infinite_repeat_is_fine():
    # harmless (equivalent to just ending there) - only a *non-last* dur-inf
    # send makes later statements truly unreachable.
    compile_program(parse("repeat {\n data = 1;\n send dur 1s;\n data = 2;\n send dur inf;\n}"))


def test_dur_inf_inside_conditional_branch_of_infinite_loop_is_allowed():
    # only ONE branch is unreachable-after, not the whole loop - must not
    # be flagged by the same check that catches the unconditional case.
    compile_program(
        parse(
            "repeat {\n"
            " if t > 100 {\n"
            "  send dur inf;\n"
            " }\n"
            " data = 1;\n"
            " send dur 1s;\n"
            "}"
        )
    )


def test_hz_inf_clamps_to_max_hz():
    instrs = compile_program(parse("send hz inf dur 1s;"), max_hz=25.0)
    sends = [i for i in instrs if isinstance(i, SendInstr)]
    assert sends[0].hz == 25.0


def test_explicit_hz_above_max_is_clamped_too():
    instrs = compile_program(parse("send hz 999 dur 1s;"), max_hz=25.0)
    sends = [i for i in instrs if isinstance(i, SendInstr)]
    assert sends[0].hz == 25.0


def test_send_value_sugar_compiles_setfield_then_send():
    instrs = compile_program(parse("send [1, 2, 3];"))
    kinds = [type(i).__name__ for i in instrs]
    assert kinds == ["SetField", "SendInstr"]
    assert instrs[0].path == []


def test_hz_zero_is_a_compile_error_not_a_zero_division_crash():
    # left unvalidated, this reaches 1.0/hz deep inside the VM/driver as a
    # raw ZeroDivisionError instead of a clear authoring-mistake message.
    with pytest.raises(ScriptError):
        compile_program(parse("send hz 0;"))


def test_hz_negative_is_rejected():
    # `_parse_number` has no unary-minus handling, so this already fails
    # at parse time ("expected a number") rather than exercising the new
    # compile-time hz<=0 check - still worth locking in as "rejected
    # either way, never reaches the VM."
    with pytest.raises(ScriptError):
        parse("send hz -5;")
