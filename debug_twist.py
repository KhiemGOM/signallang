import sys, traceback
sys.path.insert(0, "/home/khiemgom/patternlang/src")
sys.path.insert(0, "/home/khiemgom/robohome_ws/src/dashboard")
import os
os.environ.setdefault("AMENT_PREFIX_PATH", "")

from patternlang import compile_script

# reimplement just enough of RosSchemaProvider inline (avoid needing full ROS env for this debug)
from rosidl_runtime_py.utilities import get_message
import re

_ARRAY_SUFFIX_RE = re.compile(r"^(.*)\[\d*\]$")
_SEQUENCE_RE = re.compile(r"^sequence<(.+?)(?:,\s*\d+)?>$")
_MSG_TYPE_RE = re.compile(r"^[a-zA-Z0-9_]+/[A-Z][a-zA-Z0-9_]*$")

def _unwrap(field_type):
    m = _SEQUENCE_RE.match(field_type)
    if m:
        return m.group(1), "[]"
    m = _ARRAY_SUFFIX_RE.match(field_type)
    if m:
        return m.group(1), "[]"
    return field_type, ""

class RosSchemaProvider:
    def __init__(self, type_str):
        self._root_type = type_str

    def _resolve_class(self, path):
        msg_cls = get_message(self._root_type)
        for seg in path:
            field_types = msg_cls.get_fields_and_field_types()
            if seg not in field_types:
                raise KeyError(f"no field '{seg}' on {msg_cls.__name__}")
            inner, suffix = _unwrap(field_types[seg])
            if suffix or not _MSG_TYPE_RE.match(inner):
                raise KeyError(f"'{seg}' is not a nested message field")
            pkg, cls_name = inner.split("/")
            msg_cls = get_message(f"{pkg}/msg/{cls_name}")
        return msg_cls

    def fields_at(self, path):
        return list(self._resolve_class(path).get_fields_and_field_types().keys())

    def default_at(self, path):
        raise NotImplementedError

src = "send [[1,2,3],[4,5,6]] hz 1 dur 10s;"
schema = RosSchemaProvider("geometry_msgs/msg/Twist")
try:
    compiled = compile_script(src, schema_provider=schema, max_hz=50.0)
    run = compiled.new_run()
    r = run.step()
    print("OK:", r)
except Exception as e:
    print("EXC TYPE:", type(e).__name__)
    print("EXC MSG:", e)
    traceback.print_exc()
