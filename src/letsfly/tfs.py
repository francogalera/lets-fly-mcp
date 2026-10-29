"""The tfs parameter, a protobuf that names an itinerary in Google Flights URLs, and the two URLs built on it."""
import base64
from urllib.parse import quote

PAGE = "https://www.google.com/travel/flights"
BOOKING = "https://www.google.com/travel/flights/booking"
MAX_U64 = (1 << 64) - 1


def _varint(value):
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        if not value:
            return bytes(out + bytes([byte]))
        out.append(byte | 0x80)


def _number(field, value):
    return _varint(field << 3) + _varint(value)


def _bytes(field, payload):
    if isinstance(payload, str):
        payload = payload.encode()
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _segment(origins, destinations, day, legs=(), max_stops=None):
    """One direction of the trip: date, stop ceiling, pinned legs (as Leg) and the airports at each end."""
    body = _bytes(2, day)
    if max_stops is not None:
        body += _number(5, max_stops)
    for leg in legs:
        body += _bytes(4, _bytes(1, leg.origin) + _bytes(2, leg.departs.strftime("%Y-%m-%d")) + _bytes(3, leg.destination)
                       + _bytes(5, leg.airline) + _bytes(6, leg.flight_number))
    for code in origins:
        body += _bytes(13, _number(1, 1) + _bytes(2, code))
    for code in destinations:
        body += _bytes(14, _number(1, 1) + _bytes(2, code))
    return _bytes(3, body)


def _envelope(segments, one_way, adults, seat, pin=False):
    payload = _number(1, 28) + _number(2, 2) + segments
    for _ in range(adults or 1):
        payload += _number(8, 1)
    payload += _number(9, seat) + _number(14, 1)
    if pin:
        payload += _bytes(16, _number(1, MAX_U64))
    payload += _number(19, 2 if one_way else 1)
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def search_page(search, dates, outbound=None):
    """URL of the results page for search on dates; outbound pins the first direction to get its returns."""
    directions = [(search.origins, search.destinations), (search.destinations, search.origins)]
    segments = b""
    for index, day in enumerate(dates):
        legs = outbound.legs if outbound and index == 0 else ()
        # MaxStops.ANY is 0 and must leave the field out: 0 on the wire means non-stop
        segments += _segment(*directions[index], day, legs, search.stops - 1 if search.stops else None)
    tfs = _envelope(segments, len(dates) == 1, search.adults, search.seat)
    return f"{PAGE}?tfs={tfs}&hl=en&gl=US" + (f"&curr={search.currency}" if search.currency else "")


def booking_link(itineraries, seat, adults, currency):
    """Deep link to the booking page of one itinerary per direction."""
    try:
        segments = b"".join(_segment([i.legs[0].origin], [i.legs[-1].destination], i.legs[0].departs.strftime("%Y-%m-%d"),
                                     i.legs) for i in itineraries)
        url = f"{BOOKING}?tfs={_envelope(segments, len(itineraries) == 1, adults, seat, pin=True)}"
    except Exception:  # noqa: BLE001 - fli fell back to the plain search page on any failure
        url = PAGE
    return f"{url}{'&' if '?' in url else '?'}curr={quote(currency.upper(), safe='')}" if currency else url
