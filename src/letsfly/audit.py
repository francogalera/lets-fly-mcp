"""Live data checks against Google through the proxy: pair prices, date sweeps and a burst of requests."""
import html
import re
import sys
import threading
import time
from datetime import date, timedelta

from letsfly import client, tools

ROUND_TRIPS = [("EZE", "MIA"), ("LGA", "MCO"), ("EZE", "SCL")]
SWEEPS = [("EZE", "MIA", None), ("AEP", "BRC", None), ("EZE", "MIA", 10)]
BURST = [("EZE", "MAD"), ("COR", "GRU"), ("EZE", "CUN"), ("EZE", "LIM"), ("MDZ", "SCL")]
QUOTE = re.compile(r"From ([\d,]+) US dollars round trip total\..*?Leaves .+? at (\d+):(\d+)\W*([AP]M) on .+? "
                   r"arrives at .+? at (\d+):(\d+)\W*([AP]M)")
failures = []


def check(name, ok, detail):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {detail}", flush=True)
    if not ok:
        failures.append(name)


def day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


def pair_prices():
    """Every priced round-trip row costs what Google quotes as the round trip total for that pair."""
    labels, lock, fetch = [], threading.Lock(), client.fetch

    def recording(url):
        text = fetch(url)
        with lock:
            labels.extend(html.unescape(x) for x in re.findall(r'aria-label="(From [^"]*)"', text))
        return text
    client.fetch, matched, priced = recording, 0, 0
    try:
        for origin, destination in ROUND_TRIPS:
            labels.clear()
            rows = tools.search_flights(origin, destination, day(60), day(67), limit=20).get("flights", [])
            quotes = {(float(m[1].replace(",", "")), _clock(m[2], m[3], m[4]), _clock(m[5], m[6], m[7]))
                      for m in filter(None, map(QUOTE.search, labels))}
            for row in rows:
                if row["price"] is not None:
                    priced += 1
                    matched += (row["price"], row["back"]["depart"][11:], row["back"]["arrive"][11:]) in quotes
    finally:
        client.fetch = fetch
    check("round-trip rows priced as Google's round trip total", priced and matched == priced, f"{matched}/{priced}")


def dates_match_search():
    """The cheapest price per date is the cheapest option a search for that date returns."""
    agree = total = 0
    for origin, destination, trip in SWEEPS:
        rows = tools.cheapest_dates(origin, destination, day(60), day(66), trip_days=trip, limit=60).get("dates", [])
        for row in rows[:2]:
            best = tools.search_flights(origin, destination, row["date"], row.get("return"), limit=1)["flights"]
            total += 1
            agree += bool(best) and best[0]["price"] == row["price"]
    check("cheapest_dates equals the cheapest search_flights row", total and agree == total, f"{agree}/{total}")


def burst():
    """Back-to-back sweeps and round trips load without errors or missing dates."""
    start, errors, missing = time.monotonic(), 0, 0
    for origin, destination, trip in SWEEPS:
        out = tools.cheapest_dates(origin, destination, day(75), day(88), trip_days=trip, limit=60)
        errors += "error" in out
        missing += len(out.get("missing_dates", []))
    for origin, destination in BURST:
        errors += "error" in tools.search_flights(origin, destination, day(75), day(89), limit=5)
    check("burst of 3 sweeps and 5 round trips", not errors and not missing,
          f"{errors} errors, {missing} missing dates in {time.monotonic() - start:.0f} s")


def _clock(hour, minute, half):
    return f"{int(hour) % 12 + (12 if half == 'PM' else 0):02d}:{minute}"


def main():
    print(f"flights audit, {date.today()}")
    for step in (pair_prices, dates_match_search, burst):
        step()
    print("All good." if not failures else f"{len(failures)} checks failed.")
    sys.exit(1 if failures else 0)
