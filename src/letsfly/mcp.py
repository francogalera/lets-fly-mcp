"""Minimal MCP server over stdio, one protocol version. Schemas come from type hints: Annotated carries the
description (a str) and bounds (Range). A client may check results against outputSchema, so output objects allow
extra fields and every tool's schema also takes the error object @tool returns instead of raising."""
import functools
import inspect
import json
import re
import sys
import threading
import types
import typing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from letsfly import errors

PROTOCOL = "2025-06-18"
READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
TYPES = {str: "string", int: "integer", bool: "boolean", float: "number", type(None): "null", dict: "object"}
# What a call spends beyond time, said at the end of the tool's description so the agent plans with it
COSTS = {"expensive": "EXPENSIVE: each call spends 1 search of an hourly budget per IP; the reply carries cost and "
                      "budget. Never call it twice for the same search."}
_tools = {}
_instructions = None
_out = threading.Lock()


@dataclass(frozen=True)
class Range:
    ge: int | None = None
    le: int | None = None


def tool(name=None, cost=None):
    """Registers fn as a tool; cost (a key of COSTS) adds its line to the description."""
    def register(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - the agent gets the error as an object instead of an exception
                return errors.result(e)
        description = (inspect.getdoc(fn) or "") + (f"\n{COSTS[cost]}" if cost else "")
        _tools[name or fn.__name__] = (call, _input_schema(fn), _output_schema(fn), description)
        return call
    return register


def _input_schema(fn):
    hints = typing.get_type_hints(fn, include_extras=True)
    props, required = {}, []
    for name, param in inspect.signature(fn).parameters.items():
        props[name] = _schema(hints[name])
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            props[name]["default"] = param.default
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _output_schema(fn):
    returns = typing.get_type_hints(fn, include_extras=True)["return"]
    return {"type": "object", "anyOf": [_schema(returns), _schema(errors.Failure)]}


def _schema(hint):
    meta = []
    while typing.get_origin(hint) in (typing.Annotated, typing.NotRequired, typing.Required):
        hint, *extra = typing.get_args(hint)
        meta += extra
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        schema = {"anyOf": [_schema(a) for a in args]}
    elif origin is typing.Literal:
        schema = {"type": "string", "enum": list(args)}
    elif origin is list:
        schema = {"type": "array", "items": _schema(args[0])}
    elif origin is dict:
        schema = {"type": "object", "additionalProperties": _schema(args[1])}
    elif typing.is_typeddict(hint):
        fields = typing.get_type_hints(hint, include_extras=True)
        schema = {"type": "object", "properties": {k: _schema(v) for k, v in fields.items()},
                  "required": [k for k in fields if k in hint.__required_keys__], "additionalProperties": True}
    else:
        schema = {"type": TYPES[hint]}
    for m in meta:
        if isinstance(m, str):
            schema["description"] = m
        elif isinstance(m, Range):
            target = schema["anyOf"][0] if "anyOf" in schema else schema
            if m.ge is not None:
                target["minimum"] = m.ge
            if m.le is not None:
                target["maximum"] = m.le
    return schema


def problems(value, schema, path=""):
    """Where value breaks schema, as (path, message) pairs; empty when it fits."""
    kind = schema.get("type")
    if kind and not _is(value, kind):
        return [(path, f"{path or 'value'}: expected {kind}")]
    if "anyOf" in schema:
        return _closest(value, schema["anyOf"], path)
    if "enum" in schema and value not in schema["enum"]:
        return [(path, f"{path}: must be one of {', '.join(schema['enum'])}")]
    if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
        return [(path, f"{path}: out of range ({schema.get('minimum')} to {schema.get('maximum')})")]
    if kind == "array":
        return [p for i, v in enumerate(value) for p in problems(v, schema["items"], f"{path}[{i}]")]
    if kind != "object":
        return []
    props, extra = schema.get("properties", {}), schema.get("additionalProperties", True)
    at = {k: f"{path}.{k}" if path else k for k in [*value, *schema.get("required", [])]}
    found = [(at[k], f"{at[k]}: unknown parameter") for k in value if k not in props and extra is False]
    found += [(at[k], f"{at[k]}: missing") for k in schema.get("required", []) if k not in value]
    for k, v in value.items():
        rule = props.get(k, extra if isinstance(extra, dict) else None)
        found += problems(v, rule, at[k]) if rule else []
    return found


def _closest(value, branches, path):
    """The problems of the branch that fits, else of the one sharing most keys with value, then with fewest."""
    keys = set(value) if isinstance(value, dict) else set()
    found = [(problems(value, b, path), len(keys & set(b.get("properties", {})))) for b in branches]
    return min(found, key=lambda f: (bool(f[0]), -f[1], len(f[0])))[0]


def _is(value, kind):
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, {"string": str, "boolean": bool, "integer": int, "number": int | float, "array": list,
                              "object": dict, "null": type(None)}[kind])


def _call(params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in _tools:
        return _reply(errors.result(errors.UnknownTool(f"Unknown tool {name}", "name")))
    fn, schema, *_ = _tools[name]
    found = problems(args, schema)
    if found:
        message = "Invalid arguments: " + "; ".join(text for _, text in found)
        return _reply(errors.result(errors.InvalidArguments(message, re.split(r"[.\[]", found[0][0])[0])))
    return _reply(fn(**args))


def _reply(result):
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": "error" in result}


def _handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": "flights", "version": "1"}, **({"instructions": _instructions} if _instructions else {})}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": description, "inputSchema": inputs, "outputSchema": outputs,
                           "annotations": READ_ONLY}
                          for n, (_, inputs, outputs, description) in _tools.items()]}
    if method == "tools/call":
        return _call(params)
    raise LookupError(method)


def _respond(msg):
    try:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": _handle(msg)}
    except LookupError as e:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"Unknown method: {e}"}}
    except Exception as e:  # noqa: BLE001 - without a reply the client waits forever
        reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
    _write(reply)


def _write(reply):
    line = json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _out:
        sys.stdout.write(line)
        sys.stdout.flush()


def serve(instructions=None):
    global _instructions
    _instructions = instructions
    with ThreadPoolExecutor(max_workers=8) as pool:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}})
                continue
            if "id" in msg and "method" in msg:
                pool.submit(_respond, msg)
