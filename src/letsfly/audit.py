"""Live data checks against Google through the proxy: pair prices, date sweeps, a burst of requests and the filters."""
import html
import re
import sys
import threading
import time
from dataclasses import replace
from datetime import date, timedelta

from letsfly import client, models, page, search, tfs, tools
from letsfly.models import Search

ROUND_TRIPS = [("EZE", "MIA"), ("LGA", "MCO"), ("EZE", "SCL")]
SWEEPS = [("EZE", "MIA", None), ("AEP", "BRC", None), ("EZE", "MIA", 10)]
BURST = [("EZE", "MAD"), ("COR", "GRU"), ("EZE", "CUN"), ("EZE", "LIM"), ("MDZ", "SCL")]
NO_RESULTS = {error.kind for error in (search.NoFlights, search.NoReturnFlights, search.NoRoundTripFare,
                                       search.FilteredOut)}
QUOTE = re.compile(r"From ([\d,]+) US dollars round trip total\..*?Leaves .+? at (\d+):(\d+)\W*([AP]M) on .+? "
                   r"arrives at .+? at (\d+):(\d+)\W*([AP]M)")
failures = []


def check(name, ok, detail):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {detail}", flush=True)
    if not ok:
        failures.append(name)


def day(offset):
    """A date offset days from today, on the clock the tools validate against."""
    return (models.today() + timedelta(days=offset)).isoformat()


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
            best = tools.search_flights(origin, destination, row["date"], row.get("return"), limit=1).get("flights", [])
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
        out = tools.search_flights(origin, destination, day(75), day(89), limit=5)
        # A search without flights loaded fine: only a page that failed counts against the burst
        errors += "error" in out and out["error"]["type"] not in NO_RESULTS
    check("burst of 3 sweeps and 5 round trips", not errors and not missing,
          f"{errors} errors, {missing} missing dates in {time.monotonic() - start:.0f} s")


def filters_reach_google():
    """Google honors each filter, read before search._keep, which would hide a field it stopped honoring. JFK-LAX has
    flights from morning to night and several airlines, so the page without a filter nearly always shows rows outside."""
    route, out, back = (["JFK"], ["LAX"]), day(45), day(55)
    cases = (("departure_window", Search(*route, [out], window=(6, 12)), lambda i: 6 <= _hour(i) <= 12),
             ("airlines", Search(*route, [out], airlines={"AA"}),
              lambda i: any(leg.airline == "AA" or "AA" in leg.sold_as for leg in i.legs)),
             ("return_window", Search(*route, [out, back], return_window=(18, 23)), lambda i: 18 <= _hour(i) <= 23))
    for name, query, inside in cases:
        try:
            _honored(name, query, inside)
        except client.SearchError as e:
            check(name, False, f"{type(e).__name__}: {e}")


def _honored(name, query, inside):
    outbound = None
    if len(query.dates) == 2:  # Google lists returns only once an outbound is pinned
        priced = [i for i in _rows(_without_filters(query)) if i.price]
        if not priced:
            return check(name, False, "no outbound to pin on the page without filters")
        outbound = min(priced, key=lambda i: i.price)
    plain, rows = _rows(_without_filters(query), outbound), _rows(query, outbound)
    outside_plain, outside = sum(not inside(i) for i in plain), sum(not inside(i) for i in rows)
    detail = f"{outside_plain} of {len(plain)} rows outside without it, {outside} of {len(rows)} with it"
    if plain and not outside_plain:
        check(name, True, f"inconclusive: {detail}")
    else:
        check(name, plain and not outside and (rows or outside_plain == len(plain)), detail)


def _rows(query, outbound=None):
    return search._decode_all(page.fetch_ds1(tfs.search_page(query, query.dates, outbound)))


def _without_filters(query):
    return replace(query, airlines=set(), window=None, return_window=None)


def _hour(itinerary):
    return itinerary.legs[0].departs.hour


def _clock(hour, minute, half):
    return f"{int(hour) % 12 + (12 if half == 'PM' else 0):02d}:{minute}"


def main(only=None):
    print(f"flights audit, {date.today()}")
    for step in (filters_reach_google,) if only == "filters" else (pair_prices, dates_match_search, burst, filters_reach_google):
        step()
    print("All good." if not failures else f"{len(failures)} checks failed.")
    sys.exit(1 if failures else 0)
