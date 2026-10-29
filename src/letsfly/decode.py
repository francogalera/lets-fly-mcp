"""Turns one result row of the ds:1 data into an Itinerary, reading positions as fli mapped them."""
from datetime import datetime, time

from letsfly.models import Itinerary, Layover, Leg

# A layover that crosses midnight or starts before this time is overnight
NIGHT_ENDS = time(5)
# A malformed row raises; plain indexing keeps fli's exception types, which decide whether the row is skipped
# or the whole search fails (an IndexError only gets past the flight search, not the date sweep)


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
    )
    if not isinstance(airline, str) or not airline:
        raise ValueError(f"missing airline: {airline!r}")
    _check_positive(fl[11], "leg duration")
    return leg


def _price(row):
    block = _at(row, 1)
    if not isinstance(block, list):
        raise ValueError("price block missing: skip row")
    try:
        head = block[0]
        if not isinstance(head, list):
            raise ValueError("price head is not a list")
        if not head:
            return None
        raw = head[-1]
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ValueError(f"price field is not numeric: {raw!r}")
        return float(raw)
    except (IndexError, TypeError) as e:
        raise ValueError(f"malformed price block: {e}") from e


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
        raise ValueError("Date and time arrays must contain at least one non-None value")
    return datetime(*(x or 0 for x in day), *(x or 0 for x in time))


def _check_positive(value, what):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{what} is not a positive integer: {value!r}")


def _at(seq, index):
    return seq[index] if isinstance(seq, list) and 0 <= index < len(seq) else None
