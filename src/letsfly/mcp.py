"""Minimal MCP server over stdio: initialize, ping, tools/list and tools/call, one protocol version.

Each tool is a function registered with @tool. Its schema comes from the type hints: Annotated carries
the description (a str) and the bounds (Range). Arguments are checked against that schema before the
call, calls run in parallel, and a lock keeps two replies from interleaving on stdout.
"""
import inspect
import json
import sys
import threading
import types
import typing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

PROTOCOL = "2025-06-18"
READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
_tools = {}
_out = threading.Lock()


@dataclass(frozen=True)
class Range:
    ge: int | None = None
    le: int | None = None


def tool(name=None):
    def register(fn):
        _tools[name or fn.__name__] = (fn, _input_schema(fn))
        return fn
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


def _schema(hint):
    meta = []
    if typing.get_origin(hint) is typing.Annotated:
        hint, *meta = typing.get_args(hint)
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        schema = {"anyOf": [_schema(inner[0]), {"type": "null"}]}
    elif origin is typing.Literal:
        schema = {"type": "string", "enum": list(args)}
    elif origin is list:
        schema = {"type": "array", "items": _schema(args[0])}
    else:
        schema = {"type": {str: "string", int: "integer", bool: "boolean", float: "number"}[hint]}
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


def _invalid(value, schema, name):
    if "anyOf" in schema:
        return None if value is None else _invalid(value, schema["anyOf"][0], name)
    kind = schema["type"]
    ok = {"string": isinstance(value, str), "boolean": isinstance(value, bool),
          "integer": isinstance(value, int) and not isinstance(value, bool),
          "number": isinstance(value, int | float) and not isinstance(value, bool),
          "array": isinstance(value, list)}[kind]
    if not ok:
        return f"{name}: expected {kind}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{name}: must be one of {', '.join(schema['enum'])}"
    if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
        return f"{name}: out of range ({schema.get('minimum')} to {schema.get('maximum')})"
    if kind == "array":
        return next((e for i, v in enumerate(value) if (e := _invalid(v, schema["items"], f"{name}[{i}]"))), None)
    return None


def _call(params):
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in _tools:
        return _text(f"Unknown tool {name}", error=True)
    fn, schema = _tools[name]
    problems = [f"{k}: unknown parameter" for k in args if k not in schema["properties"]]
    problems += [f"{k}: missing" for k in schema["required"] if k not in args]
    problems += [e for k, v in args.items() if k in schema["properties"]
                 and (e := _invalid(v, schema["properties"][k], k))]
    if problems:
        return _text("Invalid arguments: " + "; ".join(problems), error=True)
    try:
        result = fn(**args)
    except Exception as e:  # noqa: BLE001 - the agent gets the error as the tool result
        return _text(f"{type(e).__name__}: {e}", error=True)
    return {**_text(json.dumps(result, ensure_ascii=False, separators=(",", ":"))), "structuredContent": result}


def _text(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": "flights", "version": "1"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": inspect.getdoc(fn) or "", "inputSchema": schema,
                           "outputSchema": {"type": "object", "additionalProperties": True},
                           "annotations": READ_ONLY} for n, (fn, schema) in _tools.items()]}
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


def serve():
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
