"""Flight search (one way, or round trip expanding the cheapest outbounds) and the cheapest-date sweep."""
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

from letsfly import decode, models, page, tfs
from letsfly.client import SearchError, SearchUnreadable

POOL = ThreadPoolExecutor(max_workers=10, thread_name_prefix="letsfly")
TOP_N = 5
MAX_DATES = 93
# Pages without ds:1, before any date loaded, that abandon a sweep: a blocked client fails every date alike
BLOCKED_PAGES_LIMIT = 5
NO_PAYLOAD = "the results page had no ds:1 data: Google changed the page, or sent a consent or blocking page"
CONSENT_HINT = ". From an EU/EEA IP Google may serve its consent page, which has no results"


def flights(search):
    """One way: itineraries, cheapest first. Round trip: (outbound, return) pairs for the 5 cheapest outbounds.
    Also returns how many of those outbounds lost their returns because the page failed to load."""
    outbounds = _fetch(search)
    if not outbounds or len(search.dates) == 1:
        return outbounds or [], 0
    expanded = list(POOL.map(lambda outbound: _returns(search, outbound), outbounds[:TOP_N]))
    errors = [backs for _, backs in expanded if isinstance(backs, SearchError)]
    if len(errors) == len(expanded):
        raise errors[0]
    pairs = [(outbound, back) for outbound, backs in expanded if not isinstance(backs, SearchError)
             for back in backs or []]
    return pairs, len(errors)


def _returns(search, outbound):
    try:
        return outbound, _fetch(search, outbound)
    except SearchError as e:
        return outbound, e


def _fetch(search, outbound=None):
    data = page.fetch_ds1(tfs.search_page(search, search.dates, outbound))
    if data is None:
        raise SearchUnreadable(NO_PAYLOAD[0].upper() + NO_PAYLOAD[1:])
    try:
        rows = [row for index in (2, 3) if isinstance(data[index], list) for row in data[index][0]]
    except (IndexError, TypeError) as e:
        raise SearchUnreadable(f"The ds:1 data has no flight rows at index 2 or 3: {e}") from e
    found, reasons = [], []
    for row in rows:
        try:
            found.append(decode.itinerary(row))
        except (AttributeError, KeyError, ValueError, TypeError) as e:
            if (reason := f"{type(e).__name__}: {e}") not in reasons and len(reasons) < 3:
                reasons.append(reason)
    if rows and not found:
        raise SearchUnreadable(f"None of the {len(rows)} flight rows could be read, so Google probably changed their "
                              f"layout. For example: {'; '.join(reasons)}")
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
        raise models.ValidationError(
            f"The range has {len(days)} dates and the limit is {MAX_DATES}: each date costs a whole Google page",
            "end_date", f"Split the range into searches of {MAX_DATES} dates or fewer")
    health = _Health(threading.Lock())
    outcomes = list(POOL.map(lambda day: _price_day(search, day, trip_days, health), days))
    return _collect(outcomes, len(days), health.skipped)


def _price_day(search, day, trip_days, health):
    travel = [day] + ([day + timedelta(days=trip_days)] if trip_days else [])
    if day < models.today() - timedelta(days=1):
        return _Day(day.isoformat(), attempted=False)
    with health.lock:
        if not health.loaded and health.blocked >= BLOCKED_PAGES_LIMIT:
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
    error = SearchUnreadable if blocked or tripped else SearchError
    why = "; ".join(reasons)
    if not results and (everything_failed or tripped):
        if everything_failed:
            message = f"None of the {total} dates loaded. Why: {why}"
        else:
            message = f"No price for any of the {total} dates. Why: {why or 'the dates that loaded had no flights'}"
        if tripped:
            message += (f". Stopped after {len(attempted)} of {total} dates, {skipped} skipped: when no page at all "
                        "loads, every date fails the same way and each one costs several requests")
        raise error(message + (CONSENT_HINT if blocked or tripped else ""))
    if not results and failed and len(failed) >= loaded:
        raise (SearchUnreadable if blocked else SearchError)(
            f"No price for any of the {total} dates, and {len(failed)} of the {len(attempted)} tried did not load: "
            f"the {loaded} that loaded are too few to say there are no flights. Why: {why}")
    return results, [day.day for day in outcomes if day.failure]
