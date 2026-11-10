"""The proxy's budget routes over a socket pair, with the trace, the clock and the shared file faked: the routes and
their limits, then what it counts per egress IP."""
import contextlib
import importlib.util
import io
import json
import os
import socket
import tempfile
import unittest
from datetime import datetime, timezone

PROXY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docker", "proxy.py")
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc).timestamp()
IP, OTHER_IP = "203.0.113.7", "198.51.100.9"


class Base(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("proxy", PROXY)
        self.proxy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.proxy)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = os.path.join(tmp.name, "budget.log")
        self.now, self.ip, self.traces = NOW, IP, 0
        self.proxy.SESSION, self.proxy.TRACE_HOST = "flights-1-1", "meta.example"
        self.proxy.BUDGET_FILE = self.file
        self.proxy.clock = lambda: self.now
        self.proxy.fetch_trace = self.fake_trace

    def fake_trace(self):
        self.traces += 1
        if self.ip is None:
            raise OSError("no route")
        return f"fl=1\nh=meta.example\nip={self.ip}\nts=1\n"

    def write(self, *lines, tail=""):
        with open(self.file, "w") as f:
            f.write("".join(f"{line}\n" for line in lines) + tail)

    def raw(self, data):
        """Sends one request to handle() and returns (status line, headers, body, stdout lines)."""
        a, b = socket.socketpair()
        a.sendall(data)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.proxy.handle(b)
        got = b""
        while chunk := a.recv(65536):
            got += chunk
        a.close()
        head, body = got.split(b"\r\n\r\n", 1)
        first, *rest = head.decode().split("\r\n")
        headers = dict(line.split(": ", 1) for line in rest)
        return first, headers, body, [" ".join(line.split()[1:]) for line in out.getvalue().splitlines()]

    def call(self, method, path):
        """Status line, JSON body and stdout lines of one budget request."""
        first, _, body, out = self.raw(f"{method} {path} HTTP/1.1\r\nHost: proxy:3128\r\n\r\n".encode())
        return first, json.loads(body), out


class ProxyRoutes(Base):
    def test_get_traces_and_answers_the_count(self):
        first, headers, body, out = self.raw(b"GET /budget HTTP/1.1\r\nHost: proxy:3128\r\n\r\n")
        self.assertEqual(first, "HTTP/1.1 200 OK")
        self.assertEqual(json.loads(body), {"used_last_hour": 0})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Content-Length"], str(len(body)))
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(out, ["TRACE meta.example:443 flights-1-1"])

    def test_spend_annotates_and_counts_it(self):
        self.assertEqual(
            self.call("POST", "/budget/spend"),
            ("HTTP/1.1 200 OK", {"used_last_hour": 1}, ["TRACE meta.example:443 flights-1-1", "spend 203.0.113.7"]),
        )

    def test_block_annotates_and_answers_it(self):
        first, body, out = self.call("POST", "/budget/block")
        self.assertEqual(body, {"used_last_hour": 0, "blocked_at": "2026-11-10T12:00:00Z"})
        self.assertEqual(out[-1], "block 203.0.113.7")

    def test_budget_line_has_three_fields(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.proxy.budget("spend")
        self.assertIn("BUDGET spend 203.0.113.7\n", out.getvalue())

    def test_anything_else_is_403_and_never_traced(self):
        for request, logged in [
            (b"GET /budget/x HTTP/1.1\r\n\r\n", "BLOCK ? flights-1-1"),
            (b"GET /budget/spend HTTP/1.1\r\n\r\n", "BLOCK ? flights-1-1"),
            (b"POST /budget HTTP/1.1\r\n\r\n", "BLOCK ? flights-1-1"),
            (b"GET http://proxy:3128/budget HTTP/1.1\r\n\r\n", "BLOCK proxy:3128 flights-1-1"),
            (b"GET / HTTP/1.1\r\n\r\n", "BLOCK ? flights-1-1"),
        ]:
            with self.subTest(request):
                first, headers, body, out = self.raw(request)
                self.assertEqual((first, body, out), ("HTTP/1.1 403 Forbidden", b"", [logged]))
        self.assertEqual(self.traces, 0)

    def test_failed_trace_is_502_and_annotates_nothing(self):
        self.ip = None
        first, body, out = self.call("POST", "/budget/spend")
        self.assertEqual((first, out), ("HTTP/1.1 502 Bad Gateway", ["TRACE meta.example:443 flights-1-1"]))
        self.ip = IP
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 0})

    def test_one_annotation_per_second_per_kind(self):
        self.call("POST", "/budget/spend")
        self.now = NOW + 0.5
        self.assertEqual(
            self.call("POST", "/budget/block")[:2],
            ("HTTP/1.1 200 OK", {"used_last_hour": 1, "blocked_at": "2026-11-10T12:00:00Z"}),
        )
        self.now = NOW + 0.7
        self.assertEqual(
            self.call("POST", "/budget/spend"),
            ("HTTP/1.1 429 Too Many Requests", {"error": "too many annotations"}, []),
        )
        self.now = NOW + 1.2
        first, _, out = self.call("POST", "/budget/block")
        self.assertEqual((first, out), ("HTTP/1.1 429 Too Many Requests", []))
        self.assertEqual(self.call("POST", "/budget/spend")[:2], ("HTTP/1.1 200 OK", {"used_last_hour": 2}))

    def test_block_accepted_after_the_spends_hit_their_limit(self):
        for i in range(30):
            self.now = NOW + 70 * i
            self.call("POST", "/budget/spend")
        self.now = NOW + 2100
        self.assertEqual(self.call("POST", "/budget/spend")[0], "HTTP/1.1 429 Too Many Requests")
        self.now = NOW + 2105
        self.assertEqual(
            self.call("POST", "/budget/block"),
            (
                "HTTP/1.1 200 OK",
                {"used_last_hour": 30, "blocked_at": "2026-11-10T12:35:05Z"},
                ["TRACE meta.example:443 flights-1-1", "block 203.0.113.7"],
            ),
        )

    def test_thirty_annotations_per_rolling_hour(self):
        for i in range(30):
            self.now = NOW + 60 * i
            self.assertEqual(self.call("POST", "/budget/spend")[0], "HTTP/1.1 200 OK")
        self.now = NOW + 1800
        self.assertEqual(self.call("POST", "/budget/spend")[0], "HTTP/1.1 429 Too Many Requests")
        self.now = NOW + 3600
        self.assertEqual(self.call("POST", "/budget/spend")[:2], ("HTTP/1.1 200 OK", {"used_last_hour": 30}))


class ProxyCount(Base):
    def test_own_line_counts_once_before_and_after_the_file_has_it(self):
        self.call("POST", "/budget/spend")
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 1})
        self.write("2026-11-10T12:00:00Z spend 203.0.113.7 flights-1-1")
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 1})

    def test_other_sessions_count_per_ip_and_an_ip_change_resets(self):
        self.write(
            "2026-11-10T11:10:00Z spend 203.0.113.7 flights-2-2",
            "2026-11-10T11:20:00Z spend 203.0.113.7 flights-3-3",
            "2026-11-10T11:30:00Z spend 198.51.100.9 flights-2-2",
        )
        self.call("POST", "/budget/spend")
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 3})
        self.ip, self.now = OTHER_IP, NOW + 61
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 1})

    def test_old_and_broken_lines_are_ignored(self):
        self.write(
            "2026-11-10T10:59:59Z spend 203.0.113.7 flights-2-2",
            "2026-11-10T11:00:01Z spend 203.0.113.7 flights-2-2",
            "2026-11-10T11:30:00Z spend 203.0.113.7",
            "2026-11-10T11:30:00Z spend 203.0.113.999 flights-2-2",
            "2026-13-10T11:30:00Z spend 203.0.113.7 flights-2-2",
            "2026-11-10T11:30:00Z spent 203.0.113.7 flights-2-2",
            "2026-11-10T11:30:00Z spend 203.0.113.7 session-2-2",
            "garbage",
            tail="2026-11-10T11:30:00Z spend 203.0.113.7 flights-2-2",
        )
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 1})

    def test_missing_file_counts_nothing(self):
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 0})

    def test_latest_block_within_13_hours(self):
        self.write("2026-11-09T22:59:59Z block 203.0.113.7 flights-2-2")
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 0})
        self.write(
            "2026-11-09T22:59:59Z block 203.0.113.7 flights-2-2",
            "2026-11-09T23:00:01Z block 203.0.113.7 flights-2-2",
            "2026-11-09T23:30:00Z block 203.0.113.7 flights-3-3",
            "2026-11-10T02:00:00Z block 198.51.100.9 flights-3-3",
        )
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 0, "blocked_at": "2026-11-09T23:30:00Z"})

    def test_block_cleared_by_a_later_spend_from_the_same_ip(self):
        self.write(
            "2026-11-10T09:00:00Z block 203.0.113.7 flights-2-2",
            "2026-11-10T09:30:00Z spend 198.51.100.9 flights-3-3",
        )
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 0, "blocked_at": "2026-11-10T09:00:00Z"})
        self.write(
            "2026-11-10T09:00:00Z block 203.0.113.7 flights-2-2",
            "2026-11-10T09:30:00Z spend 203.0.113.7 flights-3-3",
        )
        self.assertEqual(self.call("GET", "/budget")[1], {"used_last_hour": 0})

    def test_own_spend_clears_a_block_from_the_file(self):
        self.write("2026-11-10T11:00:00Z block 203.0.113.7 flights-2-2")
        self.assertEqual(self.call("POST", "/budget/spend")[1], {"used_last_hour": 1})

    def test_trace_reused_under_60_seconds_and_measured_again_after(self):
        self.write("2026-11-10T11:30:00Z spend 198.51.100.9 flights-2-2")
        self.call("GET", "/budget")
        self.ip, self.now = OTHER_IP, NOW + 59
        self.assertEqual((self.call("GET", "/budget")[1], self.traces), ({"used_last_hour": 0}, 1))
        self.now = NOW + 60
        self.assertEqual((self.call("GET", "/budget")[1], self.traces), ({"used_last_hour": 1}, 2))

    def test_post_always_traces(self):
        self.call("GET", "/budget")
        self.now = NOW + 5
        self.call("POST", "/budget/spend")
        self.assertEqual(self.traces, 2)
