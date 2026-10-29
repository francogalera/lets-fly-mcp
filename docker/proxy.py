#!/usr/bin/env python3
"""Egress proxy for flights: lets HTTPS through only to the allowed hosts and logs every attempt.

Runs in its own image with nothing third-party, and is the only way out of the internal network the
core lives on. Each attempt goes to stdout as `UTC-datetime ALLOW|BLOCK|ERROR host:port session`, and
bin/flights collects it with docker logs on shutdown.
Usage: proxy.py PORT HOST[,HOST...] SESSION
"""
import select
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

PORT, SESSION = int(sys.argv[1]), sys.argv[3]
ALLOW = {h.strip().lower() for h in sys.argv[2].split(",") if h.strip()}
lock = threading.Lock()


def log(verdict, target):
    with lock:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {verdict} {target} {SESSION}", flush=True)


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


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", PORT))
srv.listen(64)
print("LISTEN", flush=True)
while True:
    conn, _ = srv.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
