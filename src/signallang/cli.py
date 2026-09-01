"""`signallang` command-line entry point - a thin wrapper for two things a
host developer reaches for before writing any Python: "does this script
even compile" and "what does it actually send". Not a REPL, not a
debugger - compile_script()/compile_file() and ScriptRun remain the real
API; this just saves a throwaway harness for the common case.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .errors import ScriptError
from .expr import ExprError
from .vm import DEFAULT_STEP_INSTRUCTION_BUDGET, compile_file


def _format_error(path: str, source: str, err: Exception) -> str:
    pos = getattr(err, "pos", None)
    if pos is None:
        return f"{path}: error: {err}"
    # ScriptError.__str__ already appends "(at position N)" to the raw
    # message when pos is set - drop that here so it isn't printed twice
    # alongside the friendlier line:col + caret this adds.
    message = str(err).removesuffix(f" (at position {pos})")
    line = source.count("\n", 0, pos) + 1
    line_start = source.rfind("\n", 0, pos) + 1
    col = pos - line_start + 1
    source_line = source.splitlines()[line - 1] if line - 1 < len(source.splitlines()) else ""
    caret = " " * (col - 1) + "^"
    return f"{path}:{line}:{col}: error: {message}\n    {source_line}\n    {caret}"


def _parse_ext_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_externs(pairs: list) -> dict:
    externs = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"error: --ext expects KEY=VALUE, got {pair!r}")
        key, _, raw_value = pair.partition("=")
        externs[key] = _parse_ext_value(raw_value)
    return externs


def _cmd_validate(args: argparse.Namespace) -> int:
    source = args.file.read_text(encoding="utf-8")
    try:
        compile_file(args.file)
    except (ScriptError, ExprError) as err:
        print(_format_error(str(args.file), source, err), file=sys.stderr)
        return 1
    print(f"{args.file}: OK")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    source = args.file.read_text(encoding="utf-8")
    try:
        compiled = compile_file(args.file)
        run = compiled.new_run(
            external_params=_parse_externs(args.ext),
            step_instruction_budget=args.step_instruction_budget,
        )
        for _ in range(args.ticks):
            result = run.step()
            if result is None:
                break
            if result.sent:
                print(json.dumps(result.value))
    except (ScriptError, ExprError) as err:
        print(_format_error(str(args.file), source, err), file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signallang", description="Compile and run .signal scripts.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="compile a script and report success or a source-located error")
    validate.add_argument("file", type=pathlib.Path)
    validate.set_defaults(func=_cmd_validate)

    run = sub.add_parser("run", help="compile a script and print each sent message as a JSON line")
    run.add_argument("file", type=pathlib.Path)
    run.add_argument("--ticks", type=int, default=20, help="max step() calls (default: 20)")
    run.add_argument(
        "--ext",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="supply an extern's value (VALUE parsed as JSON when possible); repeatable",
    )
    run.add_argument(
        "--step-instruction-budget",
        type=int,
        default=DEFAULT_STEP_INSTRUCTION_BUDGET,
        help=f"max instructions per step() before an infinite-loop error (default: {DEFAULT_STEP_INSTRUCTION_BUDGET})",
    )
    run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
