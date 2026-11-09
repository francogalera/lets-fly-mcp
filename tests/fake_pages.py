"""Search result pages written by hand: ds:1 rows that carry only the positions the decode reads."""
import base64
import json
import re
from datetime import datetime


def leg(flight, origin, destination, departs, arrives, name, operator=None, sold_as=()):
    """One leg: flight is the airline code and number (AA4357), times are local, YYYY-MM-DD HH:MM; sold_as holds the
    flights of the other airlines that sell it."""
    up, down = datetime.fromisoformat(departs), datetime.fromisoformat(arrives)
    fl = [None] * 23
    fl[2], fl[3], fl[6] = operator, origin, destination
    fl[8], fl[10], fl[11] = [up.hour, up.minute], [down.hour, down.minute], 60
    fl[20], fl[21] = [up.year, up.month, up.day], [down.year, down.month, down.day]
    fl[15] = [[other[:2], other[2:], None, other[:2]] for other in sold_as] or None
    fl[22] = [flight[:2], flight[2:], None, name]
    return fl


def row(legs, price, minutes, carry_on=None, checked=None, self_transfer=False):
    detail = [None] * 13
    detail[2], detail[9], detail[12] = legs, minutes, self_transfer or None
    return [detail, [[price]], None, None, [None] * 6 + [[checked, carry_on]]]


def reference(level, lowest, typical, low, high, points=None):
    """The block Google puts at data[5]; points are [timestamp_ms, price] pairs."""
    money = [[None, n] for n in (lowest, typical, typical - lowest, low, high)]
    return [level, *money, 1, None, None, None, [points] if points else None, None, "London"]


def page(rows, price_reference=None, price_range=None):
    """A results page. price_range is data[7][0], (lowest, highest) of everything Google has before its filters: real
    pages carry it whenever Google has itineraries, rows or not."""
    data = [None, None, [rows], None] + ([None, price_reference] if price_reference else [])
    if price_range:
        data += [None] * (7 - len(data)) + [[[[None, price_range[0]], [None, price_range[1]]], None]]
    return f"<script>AF_initDataCallback({{key: 'ds:1', hash: '1', data:{json.dumps(data)}, sideChannel: {{}}}});</script>"


def rejected(status=3):
    """Google's own error as it arrives live, a ds:1 ErrorResponse in place of the results: status 3 refuses the
    request as invalid, 13 is an internal error that passes."""
    return (f"<script>AF_initDataCallback({{key: 'ds:1',  data:[{status},null,[["
            "\"type.googleapis.com/travel.frontend.flights.ErrorResponse\","
            "[[null,[[1794112037986556,112791372,892324636],null,null,null,null,[[0]]],0,\"a\",\"b\"],3]]]],"
            "errorHasStatus: true,});</script>")


def directions(url):
    """The fields of each direction inside a results URL, read from its tfs without letsfly: [{field: [values]}]."""
    tfs = re.search(r"tfs=([^&]+)", url).group(1)
    found = []
    for field, value in _fields(base64.urlsafe_b64decode(tfs + "=" * (-len(tfs) % 4))):
        if field == 3:
            direction = {}
            for inner, item in _fields(value):
                direction.setdefault(inner, []).append(item)
            found.append(direction)
    return found


def _fields(raw):
    at = 0
    while at < len(raw):
        tag, at = _varint(raw, at)
        if tag & 7 == 0:
            value, at = _varint(raw, at)
        else:
            size, at = _varint(raw, at)
            value, at = raw[at:at + size], at + size
        yield tag >> 3, value


def _varint(raw, at):
    value = shift = 0
    while True:
        byte = raw[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


def days(url):
    """The travel dates inside a results URL, read from its base64 tfs protobuf."""
    tfs = re.search(r"tfs=([^&]+)", url).group(1)
    raw = base64.urlsafe_b64decode(tfs + "=" * (-len(tfs) % 4))
    return {day.decode() for day in re.findall(rb"\d{4}-\d{2}-\d{2}", raw)}
