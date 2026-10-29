"""Short links: the core hands out file:// pages named after the hash of each link, and the host writes them."""
import contextlib
import html
import io
import os
import re
import tempfile
import unittest
from unittest import mock

from letsfly import links

URL = "https://www.google.com/travel/flights/booking?tfs=CBwQAhoeEgoyMDI2LTEyLTEw&curr=USD&hl=en"


class Links(unittest.TestCase):
    def test_without_a_folder_links_stay_long(self):
        with mock.patch.object(links, "DIR", None):
            self.assertEqual(links.short(URL), URL)

    def test_a_short_link_names_a_page_that_redirects_to_the_link(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(links, "DIR", folder):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                first, again = links.short(URL), links.short(URL)
            self.assertEqual(first, again)
            self.assertRegex(first, rf"^file://{re.escape(folder)}/[a-z2-7]{{8}}\.html$")
            self.assertEqual(stderr.getvalue(), f"LINK {links.key(URL)} {URL}\n" * 2)
            passthrough = io.StringIO()
            links.write_pages(io.StringIO(stderr.getvalue() + "Traceback (most recent call last):\n"), folder, passthrough)
            with open(first[len("file://"):]) as f:
                page = f.read()
            self.assertEqual(html.unescape(re.search(r'url=([^"]+)"', page).group(1)), URL)
            self.assertIn(f'href="{html.escape(URL)}"', page)
            self.assertEqual(passthrough.getvalue(), "Traceback (most recent call last):\n")

    def test_the_host_writes_only_pages_named_after_their_link(self):
        evil = "https://evil.test/x"
        with tempfile.TemporaryDirectory() as folder:
            passthrough = io.StringIO()
            lines = [f"LINK aaaaaaaa {evil}\n",  # an id that is not the hash of its url
                     f"LINK {links.key('javascript:alert(1)')} javascript:alert(1)\n",
                     f"LINK {links.key(evil + chr(34))} {evil}\"><script>\n"]
            links.write_pages(io.StringIO("".join(lines)), folder, passthrough)
            self.assertEqual(os.listdir(folder), [])
            self.assertEqual(passthrough.getvalue(), "".join(lines))

    def test_links_that_are_not_plain_https_stay_long(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(links, "DIR", folder):
            for url in ("http://example.test/x", "https://example.test/a b", "https://example.test/\"x"):
                self.assertEqual(links.short(url), url)


if __name__ == "__main__":
    unittest.main()
