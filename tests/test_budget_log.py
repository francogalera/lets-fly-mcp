"""bin/budget.py: the host rebuilds each BUDGET line of the proxy with its own time and session, and trims in place."""
import importlib.util
import io
import os
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
SPEC = importlib.util.spec_from_file_location("budget", os.path.join(BIN, "budget.py"))
budget = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(budget)

# 2026-11-10T12:00:00Z
NOON = 1794312000


class BudgetLogTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "metasearch-budget.log")

    def split(self, *lines):
        out = io.StringIO()
        budget.split(iter(lines), "flights-42-7", self.path, out, clock=lambda: NOON)
        return out.getvalue()

    def written(self):
        if not os.path.exists(self.path):
            return ""
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_spend_and_block_get_the_host_time_and_the_session(self):
        self.split("BUDGET spend 203.0.113.9\n", "BUDGET block 2001:db8::1\n")
        self.assertEqual(
            self.written(),
            "2026-11-10T12:00:00Z spend 203.0.113.9 flights-42-7\n"
            "2026-11-10T12:00:00Z block 2001:db8::1 flights-42-7\n",
        )

    def test_lines_that_do_not_fit_are_dropped(self):
        out = self.split(
            "BUDGET refund 203.0.113.9\n",
            "BUDGET spend 203.0.113.999\n",
            "BUDGET spend not-an-ip\n",
            "BUDGET spend 203.0.113.9 flights-1-1\n",
            "BUDGET spend\n",
            "LISTEN 3128\n",
        )
        self.assertEqual((self.written(), out), ("", ""))

    def test_egress_lines_pass_through(self):
        lines = (
            "2026-11-10T12:00:00Z ALLOW www.google.com:443 flights-42-7\n",
            "2026-11-10T12:00:01Z TRACE api.turismocity.com:443 flights-42-7\n",
        )
        self.assertEqual(self.split(*lines), "".join(lines))
        self.assertEqual(self.written(), "")

    def test_each_line_is_handled_as_it_arrives(self):
        seen = []

        def lines():
            yield "BUDGET spend 203.0.113.9\n"
            seen.append(self.written())
            yield "2026-11-10T12:00:00Z ALLOW www.google.com:443 flights-42-7\n"
            seen.append(out.getvalue())

        out = io.StringIO()
        budget.split(lines(), "flights-42-7", self.path, out, clock=lambda: NOON)
        self.assertEqual(
            seen,
            [
                "2026-11-10T12:00:00Z spend 203.0.113.9 flights-42-7\n",
                "2026-11-10T12:00:00Z ALLOW www.google.com:443 flights-42-7\n",
            ],
        )

    def fill(self, old, new):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(old + new)
        return os.stat(self.path).st_ino

    def test_trim_keeps_the_last_24_hours_and_the_inode(self):
        old = "2026-11-09T11:59:59Z spend 203.0.113.9 flights-1-1\n" * 2000
        new = "2026-11-09T12:00:00Z block 203.0.113.9 flights-2-2\n2026-11-10T11:00:00Z spend 2001:db8::1 flights-3-3\n"
        inode = self.fill(old, new)
        budget.trim(self.path, clock=lambda: NOON)
        self.assertEqual((self.written(), os.stat(self.path).st_ino), (new, inode))

    def test_trim_leaves_a_file_under_64_kb(self):
        old = "2026-11-01T00:00:00Z spend 203.0.113.9 flights-1-1\n"
        self.fill(old, "")
        budget.trim(self.path, clock=lambda: NOON)
        self.assertEqual(self.written(), old)


if __name__ == "__main__":
    unittest.main()
