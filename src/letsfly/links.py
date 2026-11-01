"""Short links: the tools hand out file:///.../flight-links/<id>.html instead of booking links of ~300 characters,
which the agent altered when copying them. The id is the hash of the url, so a page can only hold its own url.
Standard library only: bin/flights also runs this file on the host, to write the pages from the core's stderr."""
import base64
import hashlib
import html
import os
import re
import sys
import threading
import urllib.parse

# The host folder for the pages, which bin/flights passes to the core; without it links stay long (the tests)
DIR = os.environ.get("FLIGHTS_LINKS")
LINE = re.compile(r"LINK ([a-z2-7]{8}) (https://[^\s\"'<>\\]+)\n?")
PAGE = ('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={0}">'
        '<title>flights</title><a href="{0}">{0}</a>\n')
_lock = threading.Lock()


def key(url):
    """8 base32 characters of the url's sha256: 40 bits, so thousands of links stay far from a collision."""
    return base64.b32encode(hashlib.sha256(url.encode()).digest()).decode()[:8].lower()


def short(url):
    """The short link for url, or url itself when there is no folder for pages or it is not a plain https link."""
    if not DIR or not LINE.fullmatch(f"LINK {key(url)} {url}"):
        return url
    with _lock:
        sys.stderr.write(f"LINK {key(url)} {url}\n")
        sys.stderr.flush()
    return "file://" + urllib.parse.quote(f"{DIR}/{key(url)}.html")


def write_pages(lines, folder, passthrough):
    """Writes the page of each LINK line whose id is the hash of its url; every other line goes to passthrough."""
    os.makedirs(folder, exist_ok=True)
    for line in lines:
        found = LINE.fullmatch(line)
        if not found or found.group(1) != key(found.group(2)):
            passthrough.write(line)
            passthrough.flush()
            continue
        path = os.path.join(folder, f"{found.group(1)}.html")
        with open(f"{path}.tmp", "w", encoding="utf-8") as f:
            f.write(PAGE.format(html.escape(found.group(2), quote=True)))
        os.replace(f"{path}.tmp", path)


if __name__ == "__main__":
    write_pages(sys.stdin, sys.argv[1], sys.stderr)
