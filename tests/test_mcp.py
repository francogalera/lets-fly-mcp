"""The MCP server over a fake stdio: handshake, schema, validation and calls that overlap."""
import io
import json
import sys
import time
import unittest
from typing import Annotated

from letsfly import mcp

@mcp.tool("test_wait")
def wait(seconds: Annotated[float, "How long to wait"], tag: Annotated[str, mcp.Range(), "Echoed back"] = "") -> dict:
    """Waits, then echoes the tag."""
    time.sleep(seconds)
    return {"tag": tag}


def converse(*messages):
    stdin, stdout = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), io.StringIO()
    try:
        mcp.serve()
        return [json.loads(line) for line in sys.stdout.getvalue().splitlines()]
    finally:
        sys.stdin, sys.stdout = stdin, stdout


def call(id, name, arguments):
    return {"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


class Server(unittest.TestCase):
    def test_handshake_and_list(self):
        replies = converse({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                           {"jsonrpc": "2.0", "method": "notifications/initialized"},
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                           {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                           {"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
        by_id = {r["id"]: r for r in replies}
        self.assertEqual(len(replies), 4)
        self.assertEqual(by_id[1]["result"]["protocolVersion"], mcp.PROTOCOL)
        tool = next(t for t in by_id[2]["result"]["tools"] if t["name"] == "test_wait")
        self.assertEqual(tool["inputSchema"]["required"], ["seconds"])
        self.assertEqual(tool["inputSchema"]["properties"]["seconds"], {"type": "number", "description": "How long to wait"})
        self.assertEqual(by_id[3]["result"], {})
        self.assertEqual(by_id[4]["error"]["code"], -32601)

    def test_calls_overlap_and_replies_stay_whole(self):
        replies = converse(call(1, "test_wait", {"seconds": 0.5, "tag": "slow"}), call(2, "test_wait", {"seconds": 0, "tag": "fast"}))
        self.assertEqual([r["id"] for r in replies], [2, 1])
        self.assertEqual(replies[1]["result"]["structuredContent"], {"tag": "slow"})
        self.assertEqual(json.loads(replies[1]["result"]["content"][0]["text"]), {"tag": "slow"})

    def test_invalid_arguments_are_a_tool_error(self):
        reply = converse(call(1, "test_wait", {"seconds": "soon", "extra": 1}))[0]["result"]
        self.assertTrue(reply["isError"])
        self.assertIn("seconds: expected number", reply["content"][0]["text"])
        self.assertIn("extra: unknown parameter", reply["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
