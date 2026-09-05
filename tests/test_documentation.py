import ast
import asyncio
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

from signallang import DictSchemaProvider, compile_script

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SIGNALS = re.findall(r"```signal\n(.*?)\n```", README, re.DOTALL)


@pytest.mark.parametrize("source", SIGNALS)
def test_readme_signal_examples_compile_and_step(source):
    compile_script(source).new_run(seed=42).collect(20)


def test_readme_python_examples_are_valid_python():
    for source in re.findall(r"```python\n(.*?)\n```", README, re.DOTALL):
        ast.parse(source)


@pytest.mark.parametrize("name", ["stdout_signal", "websocket_signal", "schema_signal"])
def test_example_scripts_produce_valid_messages(name):
    tree = ast.parse((ROOT / "examples" / f"{name}.py").read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SCRIPT" for target in node.targets)
    )
    source = ast.literal_eval(assignment.value)
    schema = None
    if name == "schema_signal":
        schema = DictSchemaProvider(
            {"header": {"frame_id": "", "stamp": 0.0}, "linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "angular_drift": 0.0},
            strict=True,
        )
    assert len(compile_script(source, schema).new_run(seed=42).collect(5)) == 5


def test_websocket_disconnect_does_not_stop_healthy_clients(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace())
    spec = importlib.util.spec_from_file_location("signal_example", ROOT / "examples" / "websocket_signal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Client:
        def __init__(self, fail=False):
            self.fail, self.messages = fail, []

        async def send(self, value):
            if self.fail:
                raise ConnectionError("disconnected")
            self.messages.append(value)

    good, bad = Client(), Client(True)
    module.clients.update((good, bad))
    asyncio.run(module.broadcast({"data": 1}))
    asyncio.run(module.broadcast({"data": 2}))
    assert len(good.messages) == 2
    assert bad not in module.clients


def test_core_imports_remain_framework_and_clock_independent():
    for path in (ROOT / "src" / "signallang").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(name.split(".")[0] in ("rclpy", "ros2") for name in modules)
            if path.name != "realtime.py":
                assert "time" not in modules and "asyncio" not in modules
