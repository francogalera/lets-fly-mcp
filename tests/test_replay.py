import json
import os
import unittest

from replay import harness

HERE = os.path.dirname(os.path.abspath(__file__))


class Replay(unittest.TestCase):
    def test_tools_match_reference(self):
        harness.replay(harness.load(f"{HERE}/replay/fixtures.json.gz"))
        got = harness.run_all()
        harness.restore()
        with open(f"{HERE}/replay/reference.json") as f:
            want = json.load(f)
        self.assertEqual(sorted(got), sorted(want))
        for name in want:
            with self.subTest(name):
                self.assertEqual(got[name], want[name])


if __name__ == "__main__":
    unittest.main()
