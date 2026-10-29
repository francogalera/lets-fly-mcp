"""The blog client: spacing between requests across threads, and one retry when a connection drops."""
import threading
import time
import unittest
from unittest import mock

from letsfly import promos


class Response:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.text.encode()


class Requests(unittest.TestCase):
    def setUp(self):
        self.gap, promos.GAP, promos._last[0] = promos.GAP, 0.3, 0.0

    def tearDown(self):
        promos.GAP = self.gap

    def test_concurrent_calls_keep_the_gap(self):
        starts = []

        def urlopen(request, timeout):
            starts.append(time.monotonic())
            return Response("ok")
        with mock.patch("urllib.request.urlopen", urlopen):
            threads = [threading.Thread(target=promos._get, args=(f"https://example.test/{i}",)) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        gaps = [b - a for a, b in zip(sorted(starts), sorted(starts)[1:])]
        self.assertTrue(all(g >= 0.29 for g in gaps), gaps)

    def test_a_dropped_connection_is_retried_once(self):
        answers = [ConnectionResetError("reset"), Response("second")]

        def urlopen(request, timeout):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(promos._get("https://example.test/feed"), "second")

    def test_a_second_drop_is_raised(self):
        def urlopen(request, timeout):
            raise ConnectionResetError("reset")
        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertRaises(ConnectionResetError, promos._get, "https://example.test/feed")


if __name__ == "__main__":
    unittest.main()
