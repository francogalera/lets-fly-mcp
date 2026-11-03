"""Short links: the core hands out http://127.0.0.1:18375/<id>, the host appends each link to its session's file, and
the link server redirects each id to its url."""
import contextlib
import http.client
import io
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from letsfly import links

URL = "https://www.google.com/travel/flights/booking?tfs=CBwQAhoeEgoyMDI2LTEyLTEw&curr=USD&hl=en"
DEAL = "https://promociones-aereas.com.ar/cancún-desde-buenos-aires.html"


def line(url):
    return f"LINK {links.key(url)} {url}\n"


class Short(unittest.TestCase):
    def test_links_stay_long_unless_bin_flights_turns_them_on(self):
        with mock.patch.object(links, "ON", False):
            self.assertEqual(links.short(URL), URL)

    def test_a_short_link_is_the_server_and_the_hash_of_the_url(self):
        stderr = io.StringIO()
        with mock.patch.object(links, "ON", True), contextlib.redirect_stderr(stderr):
            first, again = links.short(URL), links.short(URL)
        self.assertEqual(first, again)
        self.assertEqual(first, f"http://127.0.0.1:18375/{links.key(URL)}")
        self.assertEqual(stderr.getvalue(), line(URL) * 2)

    def test_links_that_are_not_plain_https_stay_long(self):
        with mock.patch.object(links, "ON", True):
            for url in ("http://example.test/x", "https://example.test/a b", "https://example.test/\"x"):
                self.assertEqual(links.short(url), url)


class Record(unittest.TestCase):
    def test_a_session_has_one_file_in_a_folder_per_day(self):
        now = time.strptime("2027-03-24 16:47:37", "%Y-%m-%d %H:%M:%S")
        self.assertEqual(links.session_file("/r", "mcp-123", now), "/r/2027/03/24/164737-mcp-123.tsv")
        self.assertEqual(links.session_file("/r", "../x y", now), "/r/2027/03/24/164737-___x_y.tsv")

    def test_each_link_is_appended_once_and_other_lines_pass_through(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "2027", "03", "24", "164737-mcp-1.tsv")
            passthrough = io.StringIO()
            links.record(io.StringIO(line(URL) + "Traceback (most recent call last):\n" + line(URL) + line(DEAL)),
                         path, passthrough)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), f"{links.key(URL)}\t{URL}\n{links.key(DEAL)}\t{DEAL}\n")
            self.assertEqual(passthrough.getvalue(), "Traceback (most recent call last):\n")

    def test_a_session_without_links_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as root:
            links.record(io.StringIO("nothing\n"), os.path.join(root, "2027", "x.tsv"), io.StringIO())
            self.assertEqual(os.listdir(root), [])

    def test_only_links_named_after_their_hash_are_recorded(self):
        evil = "https://evil.test/x"
        with tempfile.TemporaryDirectory() as root:
            passthrough = io.StringIO()
            lines = [f"LINK aaaaaaaa {evil}\n",  # an id that is not the hash of its url
                     f"LINK {links.key('javascript:alert(1)')} javascript:alert(1)\n",
                     f"LINK {links.key(evil + chr(34))} {evil}\"><script>\n"]
            links.record(io.StringIO("".join(lines)), os.path.join(root, "x.tsv"), passthrough)
            self.assertEqual(os.listdir(root), [])
            self.assertEqual(passthrough.getvalue(), "".join(lines))


class Server(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = folder.name
        self.session = os.path.join(self.root, "2027", "03", "24", "164737-mcp-1.tsv")
        links.record(io.StringIO(line(URL)), self.session, io.StringIO())
        with open(os.path.join(self.root, "old.html"), "w") as f:
            f.write("a page from before, which the server ignores")
        self.httpd = links.server(self.root, port=0)
        threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, host=None, method="GET"):
        port = self.httpd.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request(method, path, headers={"Host": host or f"127.0.0.1:{port}"})
            res = conn.getresponse()
            return res.status, res.getheader("Location"), res.read()
        finally:
            conn.close()

    def test_an_id_redirects_to_its_url(self):
        self.assertEqual(self.get(f"/{links.key(URL)}"), (302, URL, b""))
        self.assertEqual(self.get(f"/{links.key(URL)}", host=f"localhost:{self.httpd.server_address[1]}")[:2],
                         (302, URL))
        self.assertEqual(self.get(f"/{links.key(URL)}", method="HEAD"), (302, URL, b""))

    def test_a_link_recorded_after_the_server_started_opens_too(self):
        later = os.path.join(self.root, "2027", "03", "25", "090000-mcp-2.tsv")
        links.record(io.StringIO(line(DEAL)), later, io.StringIO())
        with open(self.session, "a", encoding="utf-8") as f:
            f.write(f"{links.key(URL + '&x=1')}\t{URL}&x=1\n")
        self.assertEqual(self.get(f"/{links.key(URL + '&x=1')}")[:2], (302, URL + "&x=1"))

    def test_a_url_beyond_ascii_goes_out_percent_encoded(self):
        links.record(io.StringIO(line(DEAL)), self.session, io.StringIO())
        self.assertEqual(self.get(f"/{links.key(DEAL)}")[:2],
                         (302, "https://promociones-aereas.com.ar/canc%C3%BAn-desde-buenos-aires.html"))

    def test_anything_else_is_not_found(self):
        port = self.httpd.server_address[1]
        for path, host in ((f"/{links.key('https://nowhere.test/')}", None), ("/", None), ("/favicon.ico", None),
                           (f"/{links.key(URL)}/", None), (f"/{links.key(URL)}?x", None), ("/old", None),
                           (f"/{links.key(URL)}", f"evil.test:{port}")):
            self.assertEqual(self.get(path, host)[:2], (404, None), (path, host))

    def test_a_line_still_being_written_waits(self):
        partial = os.path.join(self.root, "2027", "03", "26", "100000-mcp-3.tsv")
        os.makedirs(os.path.dirname(partial))
        with open(partial, "w", encoding="utf-8") as f:
            f.write(f"{links.key(DEAL)}\t{DEAL[:30]}")
        self.assertEqual(self.get(f"/{links.key(DEAL)}")[0], 404)
        with open(partial, "a", encoding="utf-8") as f:
            f.write(f"{DEAL[30:]}\n")
        self.assertEqual(self.get(f"/{links.key(DEAL)}")[0], 302)


if __name__ == "__main__":
    unittest.main()
