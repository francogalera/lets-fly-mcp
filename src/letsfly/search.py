"""Flight search (one way, or round trip expanding the cheapest outbounds) and the cheapest-date sweep."""
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

from letsfly import decode, models, page, tfs
from letsfly.client import SearchClientError, SearchParseError

POOL = ThreadPoolExecutor(max_workers=10, thread_name_prefix="letsfly")
TOP_N = 5
MAX_DATES = 93
# Pages without ds:1, before any date loaded, that abandon a sweep: a blocked client fails every date alike
SWEEP_FAILURE_THRESHOLD = 5
NO_PAYLOAD = ("the search page carried no ds:1 payload: Google may have changed the page shape, or served a "
              "consent/blocked page instead")
CONSENT_HINT = ". From an EU/EEA IP Google may serve its consent page, which has no results"


def flights(search):
    """One way: itineraries, cheapest first. Round trip: (outbound, return) pairs for the 5 cheapest outbounds.
    Also returns how many of those outbounds lost their returns because the page failed to load."""
    outbounds = _fetch(search)
    if not outbounds or len(search.dates) == 1:
        return outbounds or [], 0
    expanded = list(POOL.map(lambda outbound: _returns(search, outbound), outbounds[:TOP_N]))
    errors = [backs for _, backs in expanded if isinstance(backs, SearchClientError)]
    if len(errors) == len(expanded):
        raise errors[0]
    pairs = [(outbound, back) for outbound, backs in expanded if not isinstance(backs, SearchClientError)
             for back in backs or []]
    return pairs, len(errors)


def _returns(search, outbound):
    try:
        return outbound, _fetch(search, outbound)
    except SearchClientError as e:
        return outbound, e


def _fetch(search, outbound=None):
    data = page.fetch_ds1(tfs.search_page(search, search.dates, outbound))
    if data is None:
        raise SearchParseError("Search page carried no ds:1 payload: Google may have changed the page shape, "
                               "or served a consent/blocked page instead.")
    try:
        rows = [row for index in (2, 3) if isinstance(data[index], list) for row in data[index][0]]
    except (IndexError, TypeError) as e:
        raise SearchParseError(f"Shopping response shape changed: no flights array at inner[2]/[3]: {e}") from e
    found, reasons = [], []
    for row in rows:
        try:
            found.append(decode.itinerary(row))
        except (AttributeError, KeyError, ValueError, TypeError) as e:
            if (reason := f"{type(e).__name__}: {e}") not in reasons and len(reasons) < 3:
                reasons.append(reason)
    if rows and not found:
        raise SearchParseError(f"Parsed 0/{len(rows)} flight rows: Google response shape may have changed "
                               f"(sample reasons: {'; '.join(reasons)})")
    found = [itinerary for itinerary in found if _keep(itinerary, search)]
    found.sort(key=lambda itinerary: (itinerary.price is None, itinerary.price))
    return found or None


def _keep(itinerary, search):
    """The filters Google's URL cannot carry: airlines and the departure window."""
    if search.airlines and not {leg.airline for leg in itinerary.legs} & search.airlines:
        return False
    if search.window is None:
        return True
    return bool(itinerary.legs) and search.window[0] <= itinerary.legs[0].departs.hour <= search.window[1]


@dataclass
class _Day:
    day: str
    attempted: bool = True
    failure: str | None = None
    price: tuple | None = None


@dataclass
class _Health:
    lock: threading.Lock
    blocked: int = 0
    loaded: bool = False
    skipped: int = 0


def dates(search, days, trip_days=None):
    """Cheapest price per departure day, one page each: ([(day, return day or None, price)], days that failed)."""
    if len(days) > MAX_DATES:
        raise ValueError(f"This date search covers {len(days)} dates, above the {MAX_DATES}-date limit. Google's search "
                         "page serves no calendar grid, so every date costs its own full page fetch. Narrow the range "
                         "(or run several smaller searches).")
    health = _Health(threading.Lock())
    outcomes = list(POOL.map(lambda day: _price_day(search, day, trip_days, health), days))
    return _collect(outcomes, len(days), health.skipped)


def _price_day(search, day, trip_days, health):
    travel = [day] + ([day + timedelta(days=trip_days)] if trip_days else [])
    if day < models.today() - timedelta(days=1):
        return _Day(day.isoformat(), attempted=False)
    with health.lock:
        if not health.loaded and health.blocked >= SWEEP_FAILURE_THRESHOLD:
            health.skipped += 1
            return _Day(day.isoformat(), attempted=False, failure="skipped after repeated blocked pages")
    url = tfs.search_page(search, [d.strftime("%Y-%m-%d") for d in travel])
    try:
        data = page.fetch_ds1(url)
        if data is None:
            with health.lock:
                health.blocked += 1
            return _Day(day.isoformat(), failure=NO_PAYLOAD)
        with health.lock:
            health.loaded = True
        prices = [itinerary.price for itinerary in _decode_all(data) if _keep(itinerary, search) and itinerary.price]
    except Exception as e:  # noqa: BLE001 - one bad date must not sink the sweep
        return _Day(day.isoformat(), failure=f"{type(e).__name__}: {e}")
    if not prices:
        return _Day(day.isoformat())
    return _Day(day.isoformat(), price=(travel[0].isoformat(), travel[1].isoformat() if trip_days else None, min(prices)))


def _decode_all(data):
    found = []
    if not isinstance(data, list):
        return found
    for index in (2, 3):
        block = data[index] if index < len(data) else None
        if not isinstance(block, list) or not block or not isinstance(block[0], list):
            continue
        for row in block[0]:
            try:
                found.append(decode.itinerary(row))
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                continue
    return found


def _collect(outcomes, total, skipped):
    """The priced days; raises when nothing priced and too many days never loaded to call it "no flights"."""
    results = [day.price for day in outcomes if day.price is not None]
    attempted = [day for day in outcomes if day.attempted]
    failed = [day for day in attempted if day.failure]
    loaded = len(attempted) - len(failed)
    reasons = list(dict.fromkeys(day.failure for day in failed))[:3]
    tripped = skipped > 0
    everything_failed = bool(attempted) and len(failed) == len(attempted)
    blocked = bool(failed) and all(day.failure == NO_PAYLOAD for day in failed)
    error = SearchParseError if blocked or tripped else SearchClientError
    if not results and (everything_failed or tripped):
        if everything_failed:
            message = f"Priced 0 of {total} dates: every date in the range failed. Reasons: {'; '.join(reasons)}"
        else:
            message = (f"Priced 0 of {total} dates: no date in the range could be priced. "
                       f"Reasons: {'; '.join(reasons) or 'no flights on the dates that loaded'}")
        if tripped:
            message += (f". Gave up after {len(attempted)} of {total} dates ({skipped} skipped): a sweep that has not "
                        "loaded a single page is failing for the same reason on every date, and each one costs several "
                        "requests")
        raise error(message + (CONSENT_HINT if blocked or tripped else ""))
    if not results and failed and len(failed) >= loaded:
        raise (SearchParseError if blocked else SearchClientError)(
            f"Priced 0 of {total} dates: {len(failed)} of the {len(attempted)} dates tried failed to load, so "
            f'"no flights" cannot be concluded from the {loaded} that did. Reasons: {"; ".join(reasons)}')
    return results, [day.day for day in outcomes if day.failure]
