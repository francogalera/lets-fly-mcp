"""Short links http://127.0.0.1:18375/<id> in place of ~300-character booking links, which the agent altered when
copying. The id is the url's hash. The core prints "LINK <id> <url>" on stderr; on the host, `links.py record` keeps
them per session and `links.py serve` redirects. Python 3.9 and the standard library: the host runs it too."""
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
# Surrogates are bytes that were not UTF-8, which no url holds
LINE = re.compile(r"LINK ([a-z2-7]{8}) (https://[^\s\"'<>\\\ud800-\udfff]+)\n?")
ID = re.compile(r"/([a-z2-7]{8})")
_lock = threading.Lock()


def key(url):
    """8 base32 characters of the url's sha256: 40 bits, so thousands of links stay far from a collision."""
    return base64.b32encode(hashlib.sha256(url.encode()).digest()).decode()[:8].lower()


def valid(id, url):
    """A plain https url, and id is its hash."""
    return bool(LINE.fullmatch(f"LINK {id} {url}")) and id == key(url)


def short(url):
    """The short link for url, or url itself when links are off or it is not a plain https link."""
    if not ON or not valid(key(url), url):
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
    """Appends "<id>\\t<url>" for each valid LINK line, once per id, to path, which is made at the first link; every
    other line goes to passthrough. It never stops reading: the core's stderr has no other reader."""
    seen, out, broken = set(), None, False
    try:
        for line in lines:
            found = LINE.fullmatch(line)
            if not found or not valid(*found.groups()):
                passthrough.write(line)
                passthrough.flush()
                continue
            if found.group(1) in seen or broken:
                continue
            seen.add(found.group(1))
            try:
                if out is None:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    out = open(path, "a", encoding="utf-8")
                out.write(f"{found.group(1)}\t{found.group(2)}\n")
                out.flush()
            except OSError as e:
                broken = True
                passthrough.write(f"flights: links of this session cannot be kept in {path}: {e}\n")
                passthrough.flush()
    finally:
        if out is not None:
            try:
                out.close()
            except OSError:
                pass


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
            if os.stat(path).st_size == start:  # what an unknown id costs: a stat per file, not a read
                return
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return
        end = data.rfind(b"\n") + 1  # a line still being written waits for the next refresh
        self.offsets[path] = start + end
        for line in data[:end].decode("utf-8", "replace").splitlines():
            id, _, url = line.partition("\t")
            if valid(id, url):
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


def answers():
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.3).close()
        return True
    except OSError:
        return False


def ensure(wait=2.0):
    """Starts the service when nothing answers on PORT, and says so when it still does not: a link handed out
    meanwhile is kept, and opens once the server runs."""
    if answers():
        return
    env = dict(os.environ)
    # missing: the exit code when the service is not installed (launchctl's 113 is from its docs, not a Mac)
    if sys.platform == "darwin":
        start, missing = ["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"], 113
    else:
        start, missing = ["systemctl", "--user", "start", UNIT], 5
        # Clients other than Claude Code start servers with a short environment, and systemctl needs the user bus
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        r = subprocess.run(start, capture_output=True, text=True, timeout=10, env=env)
        why = (r.stderr.strip() or f"{start[0]} exited {r.returncode}") if r.returncode else ""
        if r.returncode == missing:
            why += "; run flights update"
    except (OSError, subprocess.TimeoutExpired) as e:
        why = str(e)
    # A start that returns 0 only means the process was launched, not that it serves
    deadline = time.monotonic() + (0 if why else wait)
    while not why and not answers():
        if time.monotonic() > deadline:
            why = f"{UNIT if start[0] == 'systemctl' else LABEL} started, but nothing answers"
        else:
            time.sleep(0.1)
    if why:
        print(f"flights: links open only once {BASE} answers: {why}", file=sys.stderr)


if __name__ == "__main__":
    command, args = (sys.argv[1:] or [""])[0], sys.argv[2:]
    if command == "record":  # record ROOT NAME, from the core's stderr, which is UTF-8 whatever the host's locale
        for stream in (sys.stdin, sys.stderr):
            stream.reconfigure(encoding="utf-8", errors="surrogateescape")
        record(sys.stdin, session_file(*args), sys.stderr)
    elif command == "serve":  # serve ROOT
        server(args[0]).serve_forever()
    elif command == "ensure":
        ensure()
    else:
        sys.exit("usage: links.py record ROOT NAME | serve ROOT | ensure")
