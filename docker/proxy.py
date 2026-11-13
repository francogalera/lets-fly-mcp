#!/usr/bin/env python3
"""Egress proxy, the core's only way out: HTTPS to the allowed hosts only, each attempt logged to stdout as
`UTC-datetime ALLOW|BLOCK|ERROR|TRACE host:port session`. Also answers the metasearch budget routes over plain HTTP,
from the shared file plus its own annotations, each printed as `BUDGET KIND IP` for bin/flights. Exits when
<SESSION>-core leaves its network. Usage: proxy.py PORT HOST[,HOST...] SESSION TRACE_HOST"""
import calendar
import ipaddress
import json
import os
import re
import select
import socket
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

PORT, SESSION, TRACE_HOST, ALLOW = 0, "", "", set()
BUDGET_FILE = "/budget.log"
HOUR, BLOCK_TTL, TRACE_TTL, PER_HOUR = 3600, 13 * 3600, 60, 240
RESET = 390
STAMP = "%Y-%m-%dT%H:%M:%SZ"
LINE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (spend|block|graphql) (\S+) (flights-\d+-\d+)")
ROUTES = {("GET", "/budget"): None, ("POST", "/budget/spend"): "spend", ("POST", "/budget/block"): "block",
          ("POST", "/budget/graphql"): "graphql"}
lock = threading.Lock()
budget_lock = threading.Lock()
clock = time.time
own = []
measured = [None, 0.0]


def log(verdict, target):
    with lock:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {verdict} {target} {SESSION}", flush=True)


def fetch_trace():
    """The body of the metasearch's trace page, seen from this proxy's egress."""
    with urllib.request.urlopen(f"https://{TRACE_HOST}/cdn-cgi/trace", timeout=5) as r:
        return r.read().decode("utf-8", "replace")


def trace():
    """The egress IP as the metasearch sees it, normalized so file lines compare to it, or None if the trace fails."""
    log("TRACE", f"{TRACE_HOST}:443")
    try:
        for line in fetch_trace().splitlines():
            if line.startswith("ip="):
                return str(ipaddress.ip_address(line[3:].strip()))
    except Exception:  # noqa: BLE001 - any failure is a failed trace
        pass
    return None


def file_lines(ip):
    """The (time, kind) of other sessions' lines for this IP: our own count from memory, and a last line with no
    newline is still being written."""
    try:
        with open(BUDGET_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")[:-1]
    except OSError:
        return []
    found = []
    for line in lines:
        m = LINE.fullmatch(line)
        if not m or m[4] == SESSION:
            continue
        try:
            t = calendar.timegm(time.strptime(m[1], STAMP))
            if str(ipaddress.ip_address(m[3])) == ip:
                found.append((t, m[2]))
        except ValueError:
            continue
    return found


def status(ip, now):
    """The reply body for this IP: its spends in the last hour, its running streak, and its latest block unless a spend
    came after it."""
    lines = file_lines(ip) + [(t, kind) for t, kind, i in own if i == ip]
    spends = [t for t, kind in lines if kind == "spend"]
    body = {"used_last_hour": sum(1 for t in spends if t > now - HOUR)}
    first, last = streak(lines, now)
    if first is not None:
        body |= {"streak_started_at": time.strftime(STAMP, time.gmtime(first)), "streak_seconds": int(now - first),
                 "streak_resets_in": int(RESET - (now - last))}
    blocks = [t for t, kind in lines if kind == "block" and t > now - BLOCK_TTL]
    if blocks and not any(t > max(blocks) for t in spends):
        body["blocked_at"] = time.strftime(STAMP, time.gmtime(max(blocks)))
    return body


def streak(lines, now):
    """The (first, last) search of the running streak, searches with no GraphQL that reached the origin between them
    and no pause of RESET s, or (None, None)."""
    first = last = None
    for t, kind in sorted(lines, key=lambda line: (int(line[0]), line[1] != "graphql", line[0])):
        if kind == "graphql":
            first = last = None
        elif kind == "spend":
            if last is None or t - last >= RESET:
                first = t
            last = t
    return (first, last) if last is not None and now - last < RESET else (None, None)


def budget(kind):
    """The (status, body) of one budget route: a GET when kind is None, else an annotation, rate limited per kind so a
    looping core cannot flood the shared file nor crowd out its block."""
    with budget_lock:
        now = clock()
        if kind:
            recent = [t for t, k, _ in own if k == kind and t > now - HOUR]
            if len(recent) >= PER_HOUR or recent and max(recent) > now - 1:
                return "429 Too Many Requests", {"error": "too many annotations"}
        ip = measured[0] if not kind and measured[0] and now - measured[1] < TRACE_TTL else None
        if not ip:
            ip = trace()
            if not ip:
                return "502 Bad Gateway", {"error": "trace failed"}
            measured[:] = [ip, now]
        if kind:
            own.append((now, kind, ip))
            with lock:
                print(f"BUDGET {kind} {ip}", flush=True)
        return "200 OK", status(ip, now)


def reply(client, code, body):
    data = json.dumps(body).encode()
    head = f"HTTP/1.1 {code}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
    client.sendall(head.encode() + b"Connection: close\r\n\r\n" + data)


def relay(a, b):
    socks = [a, b]
    while True:
        r, _, x = select.select(socks, [], socks, 120)
        if x or not r:
            return
        for s in r:
            data = s.recv(65536)
            if not data:
                return
            (b if s is a else a).sendall(data)


def handle(client):
    upstream = None
    try:
        client.settimeout(30)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk or len(data) > 65536:
                return
            data += chunk
        head, rest = data.split(b"\r\n\r\n", 1)
        method, target, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
        if (method, target) in ROUTES:
            reply(client, *budget(ROUTES[method, target]))
            return
        if method != "CONNECT":
            # Plain HTTP: log only the host, the URL may carry the whole search
            log("BLOCK", urlsplit(target).netloc or "?")
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        host, _, port = target.rpartition(":")
        if host.lower() not in ALLOW or port != "443":
            log("BLOCK", target)
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        log("ALLOW", target)
        upstream = socket.create_connection((host, 443), timeout=30)
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        if rest:
            upstream.sendall(rest)
        client.settimeout(None)
        upstream.settimeout(None)
        relay(client, upstream)
    except Exception as e:  # noqa: BLE001 - log it and drop the connection
        log("ERROR", type(e).__name__)
    finally:
        for s in (client, upstream):
            if s:
                s.close()


def follow(core):
    """Exits once the core has come and gone: on a signal the launcher returns at once and leaves this to us."""
    seen, misses, start = False, 0, time.monotonic()
    while True:
        time.sleep(0.1)
        try:
            socket.gethostbyname(core)
            seen, misses = True, 0
        except OSError:
            misses += 1
        if seen and misses >= 3 or not seen and time.monotonic() - start > 60:
            os._exit(0)


def main():
    global PORT, SESSION, TRACE_HOST, ALLOW
    PORT, SESSION, TRACE_HOST = int(sys.argv[1]), sys.argv[3], sys.argv[4]
    ALLOW = {h.strip().lower() for h in sys.argv[2].split(",") if h.strip()}
    threading.Thread(target=follow, args=(f"{SESSION}-core",), daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(64)
    print("LISTEN", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
