"""Turns Google's ds:1 data into what a search returns: one result row into an Itinerary, and the price reference."""
from datetime import datetime, time, timedelta, timezone

from letsfly.models import Itinerary, Layover, Leg

NIGHT_ENDS = time(5)
WEEK = 7
TO_ORIGIN_DAY = timedelta(hours=13)
# A malformed row raises, and plain indexing raises on purpose: the exception type decides whether the row is
# skipped or the whole search fails (an IndexError only gets past the flight search, not the date sweep)


def itinerary(row) -> Itinerary:
    detail = row[0]
    price = _price(row)
    legs = [_leg(fl) for fl in detail[2] or []]
    minutes = detail[9]
    if price is not None and not price >= 0:
        raise ValueError(f"price is not a non-negative number: {price}")
    _check_positive(minutes, "duration")
    layovers = [Layover(a.destination, max(int((b.departs - a.arrives).total_seconds() // 60), 0),
                        _overnight(a.arrives, b.departs), a.destination != b.origin) for a, b in zip(legs, legs[1:])]
    checked, carry_on = _bags(row)
    return Itinerary(legs, price, minutes, layovers, _at(detail, 12) is True, carry_on, checked)


def price_reference(data) -> dict | None:
    """Google's reference for the page, or None. Unlike a row, a block that cannot be read never fails the search.

    data[5] on the first page, in the page's currency, for all adults and on a round trip for the pair. Each amount
    comes as [null, n].
    [0] level, which the page words as 1 low, 2 to 4 typical, 5 high: below [4], inside [4] to [5], above [5].
    [1] lowest fare of this page. Follows max_stops. A round trip pair is quoted on its return page and can come below it.
    With airlines in the tfs every amount describes those airlines, and an hour window or two airlines empty the block.
    [2] typical fare. [3] typical minus lowest, positive when cheap, rounded on its own: can differ by 1.
    [4] and [5] the typical range, low and high.
    [6] the cabin: 1 economy, 3 business, 4 first. Premium economy and some first class searches come without the block.
    [10] [[[timestamp_ms, lowest that day], ...]], null with over 1 adult and in first class, daily up to today: at
    most 62 days, fewer on far dates, and how far varies by route.
    Each timestamp is midnight at the origin, so adding 13 hours lands on its day from UTC-10 to UTC+13: New Zealand
    in summer and Tonga fit, Pago Pago and Niue (UTC-11) and UTC+14 come out a day off.
    [7] to [9] null, [11] opaque and [12] the destination city, not read.
    Return pages (with the outbound pinned) carry null here."""
    block = _at(data, 5)
    if not isinstance(block, list):
        return None
    lowest, typical, low, high = (_amount(_at(_at(block, index), 1)) for index in (1, 2, 4, 5))
    history = _history(_at(_at(block, 10), 0))
    if not (lowest or typical or low or high or history):
        return None
    return {"lowest": lowest, "typical": typical, "low": low, "high": high, "history": history}


def _history(points):
    """One price a week counted back from the latest day, with the extremes of the whole series."""
    if not isinstance(points, list):
        return None
    daily = [(_amount(_at(point, 0)), _amount(_at(point, 1))) for point in points]
    daily = [(stamp, price) for stamp, price in daily if stamp and price]
    if not daily:
        return None
    weekly = daily[::-WEEK][::-1]
    try:
        start = datetime.fromtimestamp(weekly[0][0] / 1000, timezone.utc) + TO_ORIGIN_DAY
    except (OverflowError, OSError, ValueError):
        return None
    prices = [price for _, price in daily]
    return {"from": start.date().isoformat(), "weekly": [price for _, price in weekly],
            "min": min(prices), "max": max(prices)}


def _amount(value):
    usable = isinstance(value, int | float) and not isinstance(value, bool) and 0 < value < 1e308
    return float(value) if usable else None


def _overnight(arrives, departs):
    """The layover touches the night: it crosses midnight, or starts before 05:00 (EZE 00:25 to 18:40)."""
    return arrives.date() != departs.date() or arrives.time() < NIGHT_ENDS


def _leg(fl) -> Leg:
    info = fl[22] or []
    airline = _at(info, 0)
    number = _at(info, 1)
    name = _at(info, 3)
    leg = Leg(
        airline=airline,
        airline_name=name if isinstance(name, str) and name else airline,
        flight_number=number if isinstance(number, str) and number else "",
        origin=_airport(fl[3]),
        destination=_airport(fl[6]),
        departs=_datetime(fl[20], fl[8]),
        arrives=_datetime(fl[21], fl[10]),
        operator=fl[2] if isinstance(fl[2], str) and fl[2] else None,
        sold_as=_sold_as(_at(fl, 15)),
    )
    if not isinstance(airline, str) or not airline:
        raise ValueError(f"missing airline: {airline!r}")
    _check_positive(fl[11], "leg duration")
    return leg


def _sold_as(entries):
    """{code: flight} of the other airlines that sell this flight; a block that cannot be read never fails the row."""
    found = {}
    for entry in entries if isinstance(entries, list) else []:
        code, number = _at(entry, 0), _at(entry, 1)
        if isinstance(code, str) and code and isinstance(number, str) and number:
            found[code] = f"{code}{number}"
    return found


def _price(row):
    block = _at(row, 1)
    if not isinstance(block, list):
        raise ValueError("no price block")
    try:
        head = block[0]
        if not isinstance(head, list):
            raise ValueError("the price is not in a list")
        if not head:
            return None
        raw = head[-1]
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ValueError(f"the price is not a number: {raw!r}")
        return float(raw)
    except (IndexError, TypeError) as e:
        raise ValueError(f"unreadable price: {e}") from e


def _bags(row):
    """Bags included in the fare, (checked, carry_on); None where Google does not say (it gives no checked count
    outside the US)."""
    bags = _at(_at(row, 4), 6)
    bags = bags if isinstance(bags, list) and len(bags) >= 2 else [None, None]
    return tuple(n if isinstance(n, int) and not isinstance(n, bool) else None for n in bags[:2])


def _airport(code):
    if not isinstance(code, str) or not code:
        raise ValueError(f"missing airport: {code!r}")
    return code


def _datetime(day, time):
    if not any(x is not None for x in day) or not any(x is not None for x in time):
        raise ValueError("a leg without its date or time")
    return datetime(*(x or 0 for x in day), *(x or 0 for x in time))


def _check_positive(value, what):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{what} is not a positive integer: {value!r}")


def _at(seq, index):
    return seq[index] if isinstance(seq, list) and 0 <= index < len(seq) else None
