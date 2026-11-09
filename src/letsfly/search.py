"""Flight search (one way, or round trip expanding the cheapest outbounds) and the cheapest-date sweep."""
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from letsfly import decode, models, page, params, tfs
from letsfly.client import SearchError, SearchRejected, SearchUnreadable
from letsfly.errors import FlightsError

POOL = ThreadPoolExecutor(max_workers=10, thread_name_prefix="letsfly")
TOP_N = 5
MAX_DATES = 93
# Pages without ds:1, before any date loaded, that abandon a sweep: a blocked client fails every date alike
BLOCKED_PAGES_LIMIT = 5
NO_PAYLOAD = "the results page had no ds:1 data: Google changed the page, or sent a consent or blocking page"
CONSENT_HINT = ". From an EU/EEA IP Google may serve its consent page, which has no results"
# The fields of a Search that filter Google's itineraries, with the tool parameter that sets each
FILTERS = {"airlines": "airlines", "window": "departure_window", "return_window": "return_window", "stops": "max_stops"}
CABINS = {seat: name for name, seat in params.SEATS.items()}


class NoFlights(FlightsError):
    kind = "no_flights"


class NoReturnFlights(FlightsError):
    kind = "no_return_flights"


class NoRoundTripFare(FlightsError):
    kind = "no_round_trip_fare"


class FilteredOut(FlightsError):
    kind = "filtered_out"


def flights(search):
    """One way: itineraries, cheapest first. Round trip: (outbound, return) pairs for 5 outbounds, the cheapest,
    with those the airlines asked for sell first. Without any, raises why: NoFlights, NoReturnFlights, NoRoundTripFare,
    FilteredOut, or the error of a page that failed.
    Also returns how many of those outbounds lost their returns because the page failed to load, and Google's price
    reference, which only a first page carries: an unfiltered one, since airlines change it and a window empties it."""
    unfiltered = replace(search, airlines=set(), window=None, return_window=None)
    plain = POOL.submit(_page, unfiltered) if unfiltered != search else None
    try:
        data = _page(search)
    except SearchRejected:
        # Google refuses a code it does not know, while the page without airlines loads. A page that never loads says
        # nothing about the codes, so it stays a page error
        if search.airlines and _loaded(plain) is not None:
            raise models.ValidationError("Google does not know at least one of these airline codes", "airlines",
                                         "Check the IATA codes and retry, or search without airlines") from None
        raise
    reference = decode.price_reference(_loaded(plain) if plain else data)
    outbounds, pages = _rows(data, search), [data]
    if plain:
        # Without rows of its own the search rests on the unfiltered page, so that page failing fails it
        pages.append(_loaded(plain) if outbounds else plain.result())
        outbounds = _completed(outbounds, pages[-1], search)
    if not outbounds:
        raise _nothing(search, pages)
    if len(search.dates) == 1:
        return outbounds, 0, reference
    # Google mixes both kinds by price, and a cheap codeshare must not take the place of a flight the airline sells
    picked = sorted(outbounds, key=lambda outbound: not _sold_by(outbound, search.airlines))[:TOP_N]
    expanded = list(POOL.map(lambda outbound: _returns(search, outbound), picked))
    errors = [backs for _, backs, _ in expanded if isinstance(backs, SearchError)]
    pairs = [(outbound, back) for outbound, backs, _ in expanded if not isinstance(backs, SearchError)
             for back in backs or []]
    if not pairs:
        # A failed page may have held the returns, so only pages that loaded can say there are none
        if errors:
            raise errors[0]
        exist = any(exist for _, _, exist in expanded)
        raise exist and _filtered_out(search, ("airlines", "return_window", "stops")) or _no_return(search)
    return pairs, len(errors), reference


def _nothing(search, pages):
    """Why the first page left no outbound. Google reports whether it has itineraries before its filters; on a round
    trip without any, one page per direction tells no flights, no return and no round trip fare apart."""
    if any(decode.itineraries_exist(data) for data in pages) and (error := _filtered_out(search, FILTERS)):
        return error
    if len(search.dates) == 1:
        return _no_flights(search)
    back = replace(search, origins=search.destinations, destinations=search.origins, dates=search.dates[1:],
                   window=search.return_window, return_window=None)
    out, back = POOL.map(_page, (replace(search, dates=search.dates[:1], return_window=None), back))
    if not decode.itineraries_exist(out):
        return _no_flights(search)
    if not decode.itineraries_exist(back):
        return _no_return(search)
    same_day = ". On the same day the return may leave before the outbound lands" if len(set(search.dates)) == 1 else ""
    return NoRoundTripFare(f"Each direction has flights, but Google offers no round trip fare for {search.dates[0]} "
                           f"and {search.dates[1]}", None, "Search each direction as a one way with search_flights; "
                           f"the two are separate tickets{same_day}")


def _filtered_out(search, fields):
    """FilteredOut naming the filters set among fields, or None when none is set."""
    names = [FILTERS[field] for field in fields if getattr(search, field)]
    if not names:
        return None
    return FilteredOut(f"Google has flights for this search, but none match {', '.join(names)}",
                       names[0] if len(names) == 1 else None,
                       f"Widen or drop {' or '.join(names)} and search again, and say which one left nothing")


def _no_flights(search):
    day = search.dates[0]
    hint = "Try nearby dates with cheapest_dates, or nearby airports" + _also(search)
    if models.today() > datetime.strptime(day, "%Y-%m-%d").date():
        hint += ". The date may already be over at the origin"
    return NoFlights(f"Google has no flights from {_codes(search.origins)} to {_codes(search.destinations)} on "
                     f"{day}{_for(search)}", "date", hint)


def _no_return(search):
    return NoReturnFlights(f"Google has flights out on {search.dates[0]}, but none back from "
                           f"{_codes(search.destinations)} to {_codes(search.origins)} on {search.dates[1]}"
                           f"{_for(search)}", "return_date",
                           "Try another return_date, or nearby airports for the return" + _also(search))


def _for(search):
    return (f" in {CABINS[search.seat]}" if search.seat != 1 else "") + (
        f" for {search.adults} adults" if search.adults > 1 else "")


def _also(search):
    return (", or another cabin" if search.seat != 1 else "") + (
        ", or fewer adults: the seats may be sold out" if search.adults > 1 else "")


def _codes(airports):
    return ",".join(airports)


def _completed(rows, plain, search):
    """Google's filter can lose rows or prices the page without it has, as an airline and a cabin together can. That
    page does not look at who sells, so with airlines its price can be another seller's."""
    kept = [itinerary for itinerary in _decode_all(plain) if _keep(itinerary, search)]
    prices = {_flight_numbers(itinerary): itinerary.price for itinerary in kept}
    from_plain = not rows
    rows = rows or kept
    for itinerary in rows:
        filled = itinerary.price is None
        if filled:
            itinerary.price = prices.get(_flight_numbers(itinerary))
        itinerary.price_other_seller = bool(search.airlines) and itinerary.price is not None and (filled or from_plain)
    return _cheapest_first(rows) or None


def _flight_numbers(itinerary):
    return tuple((leg.airline, leg.flight_number) for leg in itinerary.legs)


def _cheapest_first(itineraries):
    return sorted(itineraries, key=lambda itinerary: (itinerary.price is None, itinerary.price))


def split(found, airlines):
    """(trips those airlines sell on every leg, the rest)."""
    asked, shared = [], []
    for trip in found:
        directions = trip if isinstance(trip, tuple) else (trip,)
        (asked if all(_sold_by(direction, airlines) for direction in directions) else shared).append(trip)
    return asked, shared


def _sold_by(itinerary, airlines):
    return not airlines or all(leg.airline in airlines for leg in itinerary.legs)


def _loaded(future):
    """The page of a future, or None: a reference that does not load must not sink the search."""
    try:
        return future.result()
    except SearchError:
        return None


def _returns(search, outbound):
    """(outbound, its returns or the error that kept them, whether Google has returns before its filters)."""
    try:
        data = _page(search, outbound)
        return outbound, _rows(data, replace(search, window=search.return_window)), decode.itineraries_exist(data)
    except SearchError as e:
        return outbound, e, False


def _page(search, outbound=None):
    data = page.fetch_ds1(tfs.search_page(search, search.dates, outbound))
    if data is None:
        raise SearchUnreadable(NO_PAYLOAD[0].upper() + NO_PAYLOAD[1:])
    return data


def _rows(data, search):
    try:
        rows = [row for index in (2, 3) if isinstance(data[index], list) for row in data[index][0]]
    except (IndexError, TypeError) as e:
        raise SearchUnreadable(f"The ds:1 data has no flight rows at index 2 or 3: {e}") from e
    found, reasons = [], []
    for row in rows:
        try:
            found.append(decode.itinerary(row))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
            if (reason := f"{type(e).__name__}: {e}") not in reasons and len(reasons) < 3:
                reasons.append(reason)
    if rows and not found:
        raise SearchUnreadable(f"None of the {len(rows)} flight rows could be read, so Google probably changed their "
                              f"layout. For example: {'; '.join(reasons)}")
    return _cheapest_first(itinerary for itinerary in found if _keep(itinerary, search)) or None


def _keep(itinerary, search):
    """Both filters travel in the tfs. This repeats them because a field Google stopped honoring would otherwise pass
    every row as filtered. An airline counts on a leg it sells, with its own code or as a codeshare."""
    if search.airlines and not any(leg.airline in search.airlines or search.airlines & leg.sold_as.keys()
                                   for leg in itinerary.legs):
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
    # Its page never loaded: no ds:1, or Google's own error on every try
    blocked: bool = False


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
    if day < models.earliest_day():
        return _Day(day.isoformat(), attempted=False)
    with health.lock:
        if not health.loaded and health.blocked >= BLOCKED_PAGES_LIMIT:
            health.skipped += 1
            return _Day(day.isoformat(), attempted=False, failure="skipped after repeated blocked pages")
    url = tfs.search_page(search, [d.strftime("%Y-%m-%d") for d in travel])
    try:
        try:
            data = page.fetch_ds1(url)
        except SearchUnreadable as e:
            # Google's own error on every try blocks a sweep as much as a page without ds:1
            with health.lock:
                health.blocked += 1
            return _Day(day.isoformat(), failure=f"{type(e).__name__}: {e}", blocked=True)
        if data is None:
            with health.lock:
                health.blocked += 1
            return _Day(day.isoformat(), failure=NO_PAYLOAD, blocked=True)
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
    blocked = bool(failed) and all(day.blocked for day in failed)
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
        # The consent page is one of the pages without ds:1, so the hint needs some of those
        no_payload = any(day.failure == NO_PAYLOAD for day in failed)
        raise error(message + (CONSENT_HINT if (blocked or tripped) and no_payload else ""))
    if not results and failed and len(failed) >= loaded:
        raise (SearchUnreadable if blocked else SearchError)(
            f"No price for any of the {total} dates, and {len(failed)} of the {len(attempted)} tried did not load: "
            f"the {loaded} that loaded are too few to say there are no flights. Why: {why}")
    return results, [day.day for day in outcomes if day.failure]
