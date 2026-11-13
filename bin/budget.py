#!/usr/bin/env python3
"""The metasearch budget file the proxies read, ~/.local/state/flights/metasearch-budget.log: one line per spend,
block or GraphQL that reached the origin, "YYYY-MM-DDTHH:MM:SSZ KIND IP SESSION". Only the host writes it. Python 3.9
and the standard library only.
Usage: budget.py split SESSION BUDGET_FILE | trim BUDGET_FILE"""
import calendar
import ipaddress
import os
import sys
import time

KINDS = ("spend", "block", "graphql")
LIMIT = 64 * 1024
KEEP = 24 * 3600
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def budget_line(line, session, now):
    """The file line for a proxy's "BUDGET KIND IP", or None when it does not fit."""
    fields = line.split()
    if len(fields) != 3 or fields[0] != "BUDGET" or fields[1] not in KINDS:
        return None
    try:
        ip = ipaddress.ip_address(fields[2])
    except ValueError:
        return None
    return f"{time.strftime(STAMP, time.gmtime(now))} {fields[1]} {ip} {session}\n"


def split(lines, session, path, passthrough, clock=time.time):
    """Appends each fitting BUDGET line to path, with the host's time and this session, drops the rest of them and
    LISTEN, and passes every other line through as it comes. It never stops reading: the proxy's output has no other
    reader."""
    broken = False
    for line in lines:
        if line.startswith("LISTEN"):
            continue
        if line.split()[:1] != ["BUDGET"]:
            passthrough.write(line)
            passthrough.flush()
            continue
        entry = budget_line(line, session, clock())
        if entry is None or broken:
            continue
        try:
            with open(path, "a", encoding="utf-8") as out:
                out.write(entry)
        except OSError as e:
            broken = True
            passthrough.write(f"flights: the metasearch budget cannot be kept in {path}: {e}\n")
            passthrough.flush()


def recent(line, now):
    """Whether a file line was written in the last 24 h."""
    try:
        written = calendar.timegm(time.strptime(line.split(" ", 1)[0], STAMP))
    except ValueError:
        return False
    return now - written <= KEEP


def trim(path, clock=time.time):
    """Over 64 KB, keeps the lines of the last 24 h in place: the inode every running proxy has mounted stays, and a
    line appended meanwhile lands after the kept ones."""
    if os.path.getsize(path) <= LIMIT:
        return
    now = clock()
    with open(path, encoding="utf-8", errors="replace") as f:
        kept = [line for line in f if line.endswith("\n") and recent(line, now)]
    os.truncate(path, 0)
    with open(path, "a", encoding="utf-8") as out:
        out.write("".join(kept))


if __name__ == "__main__":
    command, args = (sys.argv[1:] or [""])[0], sys.argv[2:]
    if command == "split" and len(args) == 2:
        for stream in (sys.stdin, sys.stdout):
            stream.reconfigure(encoding="utf-8", errors="surrogateescape")
        split(iter(sys.stdin.readline, ""), args[0], args[1], sys.stdout)
    elif command == "trim" and len(args) == 1:
        trim(args[0])
    else:
        sys.exit("usage: budget.py split SESSION BUDGET_FILE | trim BUDGET_FILE")
