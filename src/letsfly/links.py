"""Short links: the tools hand out http://127.0.0.1:18375/<id> instead of booking links of ~300 characters, which
the agent altered when copying them and which filled answers with several flights. The id is the hash of the url,
so an id can only open its own url.
The core prints "LINK <id> <url>" on stderr. On the host, `links.py record` appends each one to the file of its
session, and `links.py serve`, a service of the user, redirects each id to its url.
Standard library only, and Python 3.9 as macOS ships it: bin/flights also runs this file on the host."""
import base64
import hashlib
import http.server
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

# Fixed for good: links in old answers carry it. Outside the ephemeral ranges of Linux and macOS
PORT = 18375
BASE = f"http://127.0.0.1:{PORT}"
UNIT, LABEL = "flights-links.service", "lets-fly.links"
# bin/flights turns short links on; without it links stay long (the tests)
ON = os.environ.get("FLIGHTS_LINKS") == "1"
LINE = re.compile(r"LINK ([a-z2-7]{8}) (https://[^\s\"'<>\\]+)\n?")
ID = re.compile(r"/([a-z2-7]{8})")
_lock = threading.Lock()


def key(url):
    """8 base32 characters of the url's sha256: 40 bits, so thousands of links stay far from a collision."""
    return base64.b32encode(hashlib.sha256(url.encode()).digest()).decode()[:8].lower()


def short(url):
    """The short link for url, or url itself when links are off or it is not a plain https link."""
    if not ON or not LINE.fullmatch(f"LINK {key(url)} {url}"):
        return url
    with _lock:
        sys.stderr.write(f"LINK {key(url)} {url}\n")
        sys.stderr.flush()
    return f"{BASE}/{key(url)}"


def session_file(root, name, now=None):
    """root/YYYY/MM/DD/HHMMSS-name.tsv: one file per run of bin/flights, which for the MCP is a client's session."""
    now = time.localtime() if now is None else now
    name = re.sub(r"[^A-Za-z0-9-]", "_", name)
    return os.path.join(root, time.strftime("%Y/%m/%d", now), f"{time.strftime('%H%M%S', now)}-{name}.tsv")


def record(lines, path, passthrough):
    """Appends "<id>\\t<url>" for each LINK line whose id is the hash of its url, once per id, to path, which is made
    at the first link; every other line goes to passthrough."""
    seen, out = set(), None
    try:
        for line in lines:
            found = LINE.fullmatch(line)
            if not found or found.group(1) != key(found.group(2)):
                passthrough.write(line)
                passthrough.flush()
                continue
            if found.group(1) in seen:
                continue
            seen.add(found.group(1))
            if out is None:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                out = open(path, "a", encoding="utf-8")
            out.write(f"{found.group(1)}\t{found.group(2)}\n")
            out.flush()
    finally:
        if out is not None:
            out.close()


class Index:
    """Every id in the session files under root. Files only grow, so a refresh reads what each one gained."""

    def __init__(self, root):
        self.root, self.urls, self.offsets = root, {}, {}
        self.lock = threading.Lock()

    def get(self, id):
        with self.lock:
            if id not in self.urls:
                self.refresh()
            return self.urls.get(id)

    def refresh(self):
        for folder, _, files in os.walk(self.root):
            for file in files:
                if file.endswith(".tsv"):
                    self._read(os.path.join(folder, file))

    def _read(self, path):
        start = self.offsets.get(path, 0)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return
        end = data.rfind(b"\n") + 1  # a line still being written waits for the next refresh
        self.offsets[path] = start + end
        for line in data[:end].decode("utf-8", "replace").splitlines():
            id, _, url = line.partition("\t")
            if LINE.fullmatch(f"LINK {id} {url}") and id == key(url):
                self.urls[id] = url


class Redirect(http.server.BaseHTTPRequestHandler):
    """GET /<id> answers 302 to its url; anything else, or another Host (DNS rebinding), is a 404."""

    def do_GET(self):
        port = self.server.server_address[1]
        found = ID.fullmatch(self.path)
        url = None
        if found and self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}"):
            url = self.server.index.get(found.group(1))
        if url is None:
            self.send_error(404)
            return
        self.send_response(302)
        # Header values travel as latin-1, so anything beyond ASCII goes percent-encoded
        self.send_header("Location", urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~"))
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def server(root, port=PORT):
    """The link server on 127.0.0.1 only, with every session file already read."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Redirect)
    httpd.index = Index(root)
    httpd.index.refresh()
    return httpd


def ensure():
    """Starts the service when nothing answers on PORT. A link handed out meanwhile still opens once it runs."""
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.3).close()
        return
    except OSError:
        pass
    start = (["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"] if sys.platform == "darwin"
             else ["systemctl", "--user", "start", UNIT])
    try:
        started = subprocess.run(start, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        started = False
    if not started:
        print(f"flights: nothing serves the links on {BASE}; run install.sh", file=sys.stderr)


if __name__ == "__main__":
    command, args = (sys.argv[1:] or [""])[0], sys.argv[2:]
    if command == "record":  # record ROOT NAME, from the core's stderr
        record(sys.stdin, session_file(*args), sys.stderr)
    elif command == "serve":  # serve ROOT
        server(args[0]).serve_forever()
    elif command == "ensure":
        ensure()
    else:
        sys.exit("usage: links.py record ROOT NAME | serve ROOT | ensure")
