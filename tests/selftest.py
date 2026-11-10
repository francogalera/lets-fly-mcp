#!/usr/bin/env python3
"""flights selftest: the core image end to end with the real isolation. Searches through the
terminal and the MCP, live replies against their output schema, the proxy, and nothing left behind or let out.
Usage: selftest.py PATH_TO_bin/flights"""
import datetime
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from letsfly.mcp import problems  # noqa: E402  stdlib only, so it runs on the host

LAUNCHER = sys.argv[1]
IMAGE = subprocess.run([LAUNCHER, "image"], capture_output=True, text=True, check=True).stdout.strip()
LOG = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "flights", "egress.log")
DAY = datetime.date.today() + datetime.timedelta(days=45)
DATE, RETURN, RANGE_END = (DAY + datetime.timedelta(days=n) for n in (0, 10, 4))
DATE, RETURN, RANGE_END = DATE.isoformat(), RETURN.isoformat(), RANGE_END.isoformat()
ROUTE = ("EZE", "GRU")
# What the proxy lets through, read from the launcher's ALLOW; the test itself causes the BLOCK
with open(LAUNCHER) as f:
    ALLOWED = re.search(r"^ALLOW=(\S+)$", f.read(), re.M).group(1).split(",")
EXPECTED_EGRESS = {f"ALLOW {host}:443" for host in ALLOWED} | {"BLOCK example.com:443"}
# An external limit, not a broken build: the metasearch can refuse a shared IP, as CI runners have
LIMITS = {"metasearch_blocked", "metasearch_challenge"}
failures, pids = [], []


def check(name, ok, detail=""):
    # flush: python -I ignores PYTHONUNBUFFERED, and a run killed for hanging would leave an empty log in CI
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{f': {detail}' if detail else ''}", flush=True)
    if not ok:
        failures.append(name)


def problem(data):
    """The message of a tool's error object, or why there is nothing to show."""
    return data["error"]["message"] if "error" in data else "no results"


def fits(name, schema, res):
    """A live reply against the outputSchema the server published: real data can hold a null the schema forbids."""
    found = problems(res.get("structuredContent"), schema or {})
    check(f"MCP: {name} fits its output schema", bool(schema) and not found, "; ".join(m for _, m in found[:3]))


def page_ok(link):
    """A short link answers on this machine with a redirect to a Google Flights booking page."""
    found = re.fullmatch(r"http://127\.0\.0\.1:(\d+)(/[a-z2-7]{8})", link or "")
    if not found:
        return False
    time.sleep(0.5)  # the link is recorded from the core's stderr, which can trail its reply by a moment
    conn = http.client.HTTPConnection("127.0.0.1", int(found.group(1)), timeout=5)
    try:
        conn.request("GET", found.group(2))
        res = conn.getresponse()
        return res.status == 302 and res.getheader("Location", "").startswith(
            "https://www.google.com/travel/flights/booking?")
    except OSError:
        return False
    finally:
        conn.close()


def leftovers(pid):
    found = []
    for cmd in (["ps", "-a"], ["network", "ls"]):
        r = subprocess.run(["docker", *cmd, "-q", "--filter", f"label=flights.pid={pid}"], capture_output=True, text=True)
        found += r.stdout.split()
    return found


class Mcp:
    def __init__(self):
        self.p = subprocess.Popen([LAUNCHER, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        pids.append(self.p.pid)
        self.n = 0

    def call(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        line = self.p.stdout.readline()
        if not line.startswith("{"):
            raise RuntimeError(f"MCP stdout is not JSON-RPC: {line[:120]!r}")
        return json.loads(line)

    def start(self):
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "flights-selftest", "version": "1"}})
        self.call("notifications/initialized", notify=True)


def live_filters(mcp, schema):
    """Filters against Google: the page without them leaves Copa out of EZE-MIA, so its rows prove the code got there."""
    def search(**arguments):
        res = mcp.call("tools/call", {"name": "search_flights", "arguments": arguments})["result"]
        return res, res.get("structuredContent") or json.loads(res["content"][0]["text"])

    def typical(data):
        return data.get("price_reference", {}).get("typical")

    def flies(rows, code):
        return any(number.startswith(code) for r in rows for number in r["out"]["flights"].split())

    _, plain = search(origin="EZE", destination="MIA", date=DATE, limit=20)
    res, copa = search(origin="EZE", destination="MIA", date=DATE, airlines=["CM"])
    rows = copa.get("flights", [])
    check("MCP: airlines reach Google, with a carrier the page without them leaves out", flies(rows, "CM"),
          problem(copa) if not rows else f"{len(rows)} flights" if not flies(plain.get("flights", []), "CM")
          else "inconclusive: the page without airlines already has CM")
    check("MCP: price_reference does not change with airlines", typical(copa) and typical(copa) == typical(plain),
          f"typical {typical(plain)} and {typical(copa)}")
    fits("search_flights with airlines", schema, res)

    def hour(text):
        return int(text[11:13])
    res, timed = search(origin="JFK", destination="LAX", date=DATE, return_date=RETURN, departure_window="6-12",
                        return_window="18-23", limit=20)
    rows = timed.get("flights", [])
    check("MCP: departure_window and return_window, each on its direction, with price_reference",
          bool(rows) and typical(timed) and all(6 <= hour(r["out"]["depart"]) <= 12 and 18 <= hour(r["back"]["depart"]) <= 23
                                                for r in rows), f"{len(rows)} options" if rows else problem(timed))
    fits("search_flights with windows", schema, res)

    res, american = search(origin="LHR", destination="JFK", date=DATE, airlines=["AA"])
    shared = american.get("codeshares", [])
    check("MCP: codeshares come apart, with the number of the airline asked for",
          bool(shared) and all(any(n.startswith("AA") for n in [*r["out"]["flights"].split(), *r["out"].get("sold_as", {}).values()])
                               for r in shared),
          f"{len(american.get('flights', []))} flights and {len(shared)} of {american.get('codeshares_total')} codeshares"
          if shared else problem(american))
    fits("search_flights with codeshares", schema, res)
    found = [m for r in shared for _, m in problems(r, schema["anyOf"][0]["properties"]["flights"]["items"])]
    check("MCP: codeshares rows have the shape of flights rows", not found, "; ".join(found[:3]))


def live_metasearch(mcp, schemas):
    """One expensive search, the same one again from the cache, and the free dates."""
    def call(name, arguments):
        res = mcp.call("tools/call", {"name": name, "arguments": arguments})["result"]
        return res, res.get("structuredContent") or json.loads(res["content"][0]["text"])

    trip = {"origin": ROUTE[0], "destination": ROUTE[1], "date": DATE, "return_date": RETURN}
    res, data = call("metasearch_flights", trip)
    kind = data.get("error", {}).get("type")
    if kind in LIMITS:
        print(f"  skip MCP: metasearch_flights, {kind}: {problem(data)}", flush=True)
        return
    rows = data.get("flights", [])
    check("MCP: metasearch_flights round trip, with sellers, spending 1 search",
          bool(rows) and all(r.get("sellers") and "back" in r for r in rows) and data.get("cost") == 1
          and data.get("budget", {}).get("used_last_hour", 0) >= 1,
          f"{len(rows)} options from USD {rows[0]['price']:.0f}, {rows[0]['sellers'][0]['name']}" if rows else problem(data))
    fits("metasearch_flights", schemas.get("metasearch_flights"), res)
    _, again = call("metasearch_flights", trip | {"max_stops": "ONE_STOP"})
    check("MCP: metasearch_flights again within 15 minutes costs nothing", again.get("cost") == 0,
          f"cost {again.get('cost')}" if "cost" in again else problem(again))
    res, data = call("metasearch_dates", {"origin": ROUTE[0], "destination": ROUTE[1]})
    check("MCP: metasearch_dates per month, free", "prices" in data and "cost" not in data,
          f"{data.get('count')} months" if "prices" in data else problem(data))
    fits("metasearch_dates", schemas.get("metasearch_dates"), res)


def google_honors_filters():
    """Runs inside the core because over the MCP search._keep hides a field Google stopped honoring."""
    audit = subprocess.Popen([LAUNCHER, "audit", "filters"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pids.append(audit.pid)
    out, err = audit.communicate(timeout=180)
    lines = [found for found in map(re.compile(r"  (ok  |FAIL) ([^:]+): (.*)").fullmatch, out.splitlines()) if found]
    for found in lines:
        check(f"Google honors {found[2]}", found[1] != "FAIL", found[3])
    # A crash can come after some checks already printed ok
    if not lines or (audit.returncode and not any(found[1] == "FAIL" for found in lines)):
        check("Google honors the filters", False, (err or out).strip()[-200:])


def probe_network(pid):
    nets = subprocess.run(["docker", "network", "ls", "-q", "--filter", f"label=flights.pid={pid}"],
                          capture_output=True, text=True).stdout.split()
    if not nets:
        return None
    code = r'''
import socket
def proxy(target):
    s = socket.create_connection(("proxy", 3128), timeout=10)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    return s.recv(64).split(b" ")[1].decode()
def direct():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=5)
        return "connects"
    except OSError:
        return "no network"
print(proxy("www.google.com:443"), proxy("example.com:443"), direct())
'''
    r = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", nets[0], "--cap-drop=ALL",
                        "--security-opt=no-new-privileges", "--entrypoint", "/usr/local/bin/python3", IMAGE,
                        "-I", "-c", code], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or r.stderr.strip()[-200:]


def main():
    print(f"flights selftest: {IMAGE}, {ROUTE[0]}-{ROUTE[1]} {DATE}")
    log_start = os.path.getsize(LOG) if os.path.exists(LOG) else 0

    cli = subprocess.Popen([LAUNCHER, "search", *ROUTE, DATE, "--json"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pids.append(cli.pid)
    out, err = cli.communicate(timeout=180)
    try:
        flights = [f for f in json.loads(out).get("flights", []) if f.get("price")]
        check("terminal search", bool(flights), f"{len(flights)} flights, from USD {min(f['price'] for f in flights):.0f}"
              if flights else "no results")
    except ValueError:
        check("terminal search", False, (err or out).strip()[-200:])
    check("the terminal leaves no containers or networks", not leftovers(cli.pid))
    google_honors_filters()

    mcp = Mcp()
    try:
        mcp.start()
        schemas = {t["name"]: t.get("outputSchema") for t in mcp.call("tools/list")["result"]["tools"]}
        need = {"search_flights", "cheapest_dates", "find_airports", "promociones-aereas", "metasearch_flights",
                "metasearch_dates"}
        check("MCP: clean output and the 6 tools", set(schemas) == need, ", ".join(sorted(schemas)))
        res = mcp.call("tools/call", {"name": "search_flights", "arguments": {
            "origin": ROUTE[0], "destination": ROUTE[1], "date": DATE, "return_date": RETURN}})["result"]
        text = res["content"][0]["text"]
        data = res.get("structuredContent") or json.loads(text)
        rows = data.get("flights", [])
        check("MCP: search_flights round trip, links on the first 2 rows",
              bool(rows) and all("back" in r for r in rows) and [i for i, r in enumerate(rows) if r.get("link")] == [0, 1],
              f"{len(rows)} options, from USD {rows[0]['price']:.0f}" if rows else problem(data))
        check("MCP: search_flights with layovers and operator per direction",
              bool(rows) and all({"layovers", "operated_by"} <= r[d].keys() for r in rows for d in ("out", "back")))
        check("MCP: booking links are short and the link server redirects them to Google",
              bool(rows) and page_ok(rows[0].get("link")), (rows[0].get("link") or "no link") if rows else "no results")
        check("MCP: short reply (under 4000 characters)", len(text) < 4000, f"{len(text)} characters")
        fits("search_flights", schemas.get("search_flights"), res)
        live_filters(mcp, schemas.get("search_flights"))
        res = mcp.call("tools/call", {"name": "cheapest_dates", "arguments": {
            "origin": ROUTE[0], "destination": ROUTE[1], "start_date": DATE, "end_date": RANGE_END, "trip_days": 10}})["result"]
        data = res.get("structuredContent") or json.loads(res["content"][0]["text"])
        check("MCP: cheapest_dates", bool(data.get("dates")), f"{len(data.get('dates', []))} dates" if data.get("dates")
              else problem(data))
        fits("cheapest_dates", schemas.get("cheapest_dates"), res)
        live_metasearch(mcp, schemas)
        res = mcp.call("tools/call", {"name": "promociones-aereas", "arguments": {"limit": 5, "details": False}})["result"]
        text = res["content"][0]["text"]
        deals = json.loads(text).get("deals", [])
        check("MCP: promociones-aereas, short list", bool(deals) and all(d.get("url") for d in deals) and len(text) < 4000,
              f"{len(deals)} deals, {len(text)} characters" if deals else text[:200])
        fits("promociones-aereas", schemas.get("promociones-aereas"), res)
        res = mcp.call("tools/call", {"name": "promociones-aereas", "arguments": {"type": "package", "limit": 3}})["result"]
        text = res["content"][0]["text"]
        # The newest package can be a coupon, a real post with no dates
        det = next((d["details"] for d in json.loads(text).get("deals", []) if d.get("details", {}).get("dates")), {})
        check("MCP: promociones-aereas with details by default (keeps 5 s between requests)", bool(det.get("prices_seen_at") and det.get("dates")),
              f"{len(det.get('dates', []))} dates, prices from {det.get('prices_seen_at')}" if det else text[:200])
        fits("promociones-aereas with details", schemas.get("promociones-aereas"), res)
        verdict = probe_network(mcp.p.pid)
        check("network: google passes, example.com 403, no direct connection",
              verdict == "200 403 no network", verdict or "could not find the session network")
    except Exception as e:  # noqa: BLE001
        check("MCP", False, f"{type(e).__name__}: {e}")
    mcp.p.stdin.close()
    mcp.p.wait(timeout=60)
    time.sleep(1)
    check("MCP: closing stdin leaves no containers or networks", not leftovers(mcp.p.pid))

    # How Claude Code stops a server on Linux and macOS: SIGINT, SIGTERM 100 ms later, SIGKILL 400 ms after that
    term = Mcp()
    try:
        term.start()
        term.call("tools/call", {"name": "promociones-aereas", "arguments": {"limit": 1, "details": False}})
        start = time.monotonic()
        for sig, grace in ((signal.SIGINT, 0.1), (signal.SIGTERM, 0.4), (signal.SIGKILL, 10)):
            term.p.send_signal(sig)
            try:
                term.p.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                pass
        took = time.monotonic() - start
        check("MCP: closed like Claude Code, it ends before SIGKILL", took < 0.5, f"{took:.3f} s")
        deadline = time.monotonic() + 10
        while leftovers(term.p.pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        check("MCP: closed like Claude Code, it leaves no containers or networks", not leftovers(term.p.pid))
    except Exception as e:  # noqa: BLE001
        term.p.kill()
        check("MCP: closed like Claude Code", False, f"{type(e).__name__}: {e}")

    # The log is written while each session runs; only this test's sessions count
    with open(LOG) as f:
        f.seek(log_start)
        ours = [line.split() for line in f if any(f" flights-{pid}-" in line for pid in pids)]
    check("network: the log kept the session closed like Claude Code",
          any(session.startswith(f"flights-{term.p.pid}-") for *_, session in ours))
    # The proxy connects upstream only after logging ALLOW, so an ERROR line is never new egress
    unexpected = sorted({f"{v} {t}" for _, v, t, _ in ours if v != "ERROR"} - EXPECTED_EGRESS)
    check("network: the core tried to go nowhere else", not unexpected, ", ".join(unexpected))

    print("All good." if not failures else f"{len(failures)} checks failed.")
    sys.exit(1 if failures else 0)


main()
