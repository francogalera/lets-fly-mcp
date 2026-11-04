"""Search result pages written by hand: ds:1 rows that carry only the positions the decode reads."""
import base64
import json
import re
from datetime import datetime


def leg(flight, origin, destination, departs, arrives, name, operator=None):
    """One leg: flight is the airline code and number (AA4357), times are local, YYYY-MM-DD HH:MM."""
    up, down = datetime.fromisoformat(departs), datetime.fromisoformat(arrives)
    fl = [None] * 23
    fl[2], fl[3], fl[6] = operator, origin, destination
    fl[8], fl[10], fl[11] = [up.hour, up.minute], [down.hour, down.minute], 60
    fl[20], fl[21] = [up.year, up.month, up.day], [down.year, down.month, down.day]
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


def page(rows, price_reference=None):
    data = json.dumps([None, None, [rows], None] + ([None, price_reference] if price_reference else []))
    return f"<script>AF_initDataCallback({{key: 'ds:1', hash: '1', data:{data}, sideChannel: {{}}}});</script>"


def days(url):
    """The travel dates inside a results URL, read from its base64 tfs protobuf."""
    tfs = re.search(r"tfs=([^&]+)", url).group(1)
    raw = base64.urlsafe_b64decode(tfs + "=" * (-len(tfs) % 4))
    return {day.decode() for day in re.findall(rb"\d{4}-\d{2}-\d{2}", raw)}
