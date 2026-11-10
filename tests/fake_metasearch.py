"""Metasearch answers written by hand: the fields the module reads, in the API's shape, and a fake session that serves
them. pull bodies go out packed, as the API sends them, with pack(), which mirrors the web client's format."""
import base64
import json
import urllib.parse


def flight(code, origin, destination, departs, arrives, operator=None, self_transfer=False):
    """code is airline and number (IB6844); times are local, YYYY-MM-DD HH:MM."""
    return {"airline": code[:2], "flightNumber": code[2:], "departure": origin, "arrival": destination,
            "departureTime": f"{departs}:00", "arrivalTime": f"{arrives}:00", "operatingAirline": operator,
            "selfTransfer": self_transfer}


def segment(flights, minutes):
    return {"flights": flights, "duration": minutes, "stops": len(flights) - 1}


def link(url):
    """As the API encodes a seller's link: base64 with its = escaped."""
    return urllib.parse.quote(base64.b64encode(url.encode()).decode())


def itinerary(segments, sellers, carry_on=True, checked=False, fees=None, included=(), url="https://seller.test/book"):
    """sellers: (name, total price) pairs; the first is the best price. carry_on and checked hold for every flight.
    fees: costs the best price quotes (baggageIncluded, changes, refunds); included: what its fare includes."""
    bags = [[carry_on] * len(s["flights"]) for s in segments]
    checks = [[checked] * len(s["flights"]) for s in segments]
    name, price = sellers[0]
    best = {"price": {"value": price, "currency": "USD", **(fees or {})}, "providerName": name,
            "cabinBaggageIncluded": bags, "baggageIncluded": checks, "base64": link(url) if url else None,
            "hasBaggageIncluded": "bags" in included, "hasChangesIncluded": "changes" in included,
            "hasRefundsIncluded": "refunds" in included}
    providers = [{"providerName": n, "price": {"value": p, "currency": "USD"}} for n, p in sellers]
    return {"segments": segments, "bestPrice": best, "providers": providers}


def body(itineraries, matrix=None, names=None):
    out = {"itineraries": itineraries, "totalItineraries": len(itineraries),
           "dictionary": {"airline": names or {"IB": "Iberia", "JA": "JetSMART", "UX": "Air Europa", "LA": "LATAM"}}}
    if itineraries:
        out["matrix"] = matrix if matrix is not None else []
    return out


NONSTOP = itinerary([segment([flight("IB6844", "EZE", "MAD", "2027-04-14 23:55", "2027-04-15 16:15")], 740),
                     segment([flight("IB6843", "MAD", "EZE", "2027-04-28 23:59", "2027-04-29 08:35")], 756)],
                    [("Iberia", 1100.0), ("Agency A", 1150.5), ("Iberia", 1180.0)], carry_on=True, checked=True,
                    fees={"changes": 150.0}, included=("bags", "refunds"))
GLUED = itinerary([segment([flight("JA3070", "EZE", "GRU", "2027-04-14 19:00", "2027-04-14 21:50", self_transfer=True),
                            flight("UX26", "GRU", "MAD", "2027-04-15 01:30", "2027-04-15 16:40")], 1180),
                   segment([flight("UX25", "MAD", "GRU", "2027-04-28 12:10", "2027-04-28 19:20"),
                            flight("JA3071", "GRU", "EZE", "2027-04-28 22:40", "2027-04-29 01:35", self_transfer=True)],
                           1045)],
                  [("Agency B", 800.25)], carry_on=False, fees={"baggageIncluded": 120.0, "changes": 95.5,
                                                                 "refunds": 0},
                  url="/flights/getredir?u=x")
OPERATED = itinerary([segment([flight("LA8023", "AEP", "SCL", "2027-04-14 06:00", "2027-04-14 08:10", operator="LA"),
                               flight("LA704", "SCL", "MAD", "2027-04-14 11:00", "2027-04-15 05:40")], 1180),
                      segment([flight("LA705", "MAD", "SCL", "2027-04-28 13:00", "2027-04-28 21:00"),
                               flight("LA2502", "SCL", "AEP", "2027-04-28 23:00", "2027-04-29 01:50")], 1130)],
                     [("Agency C", 950.0)], url="/flights/postredir?u=y")
ROUND_TRIP = body([NONSTOP, GLUED, OPERATED], matrix=[
    {"id": "UX", "name": "Air Europa", "lowestprice": 800.25, "stops": [None, {"price": 800.25, "stops": 1}, None, None]},
    {"id": "IB", "name": "Iberia", "lowestprice": 1100.0, "stops": [{"price": 1100.0, "stops": 0}, None, None, None]}])
ONE_WAY = body([itinerary([segment([flight("JA3078", "AEP", "MDZ", "2027-03-05 20:00", "2027-03-05 21:50")], 110)],
                          [("JetSMART", 82.13)])])
EMPTY = body([])


def pack(data):
    """The API's packed format: the first object with a set of keys goes literal and takes the next template id, later
    ones go as [id, values...]; a list that starts with a number gets a leading 0."""
    templates = {}

    def enc(x):
        if isinstance(x, dict):
            names = tuple(sorted(x))
            if not names:
                return {}
            if names in templates:
                return [templates[names], *[enc(x[n]) for n in names]]
            templates[names] = len(templates) + 1
            return {n: enc(x[n]) for n in names}
        if isinstance(x, list):
            items = [enc(i) for i in x]
            number = items and isinstance(items[0], int | float) and not isinstance(items[0], bool)
            return [0, *items] if number else items
        return x

    return enc(data)


class Response:
    def __init__(self, data=None, status=200, headers=None, text=None):
        self.status_code, self.headers = status, headers or {}
        self.text = text if text is not None else json.dumps(data)

    def json(self):
        return json.loads(self.text)


class Session:
    """Serves search, meta (versions in order, the last one repeats), the packed pull, and GraphQL by field name.
    Every request lands in calls as (path, params)."""

    def __init__(self, result=ROUND_TRIP, versions=(3, 5, "final"), graph=None, override=None):
        self.result, self.versions, self.graph, self.override = result, list(versions), graph or {}, override or {}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        path = urllib.parse.urlsplit(url).path
        self.calls.append((path, params or {}))
        for prefix, response in self.override.items():
            if path.startswith(prefix):
                return response
        if path == "/flights/search":
            return Response({"searchId": "s1", "version": 0})
        if path.endswith("/meta"):
            version = self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]
            return Response({"version": version, "meta": {}})
        if path.startswith("/flights/rpull/"):
            return Response(pack(self.result))
        if path == "/graphql-cdn":
            query = params["query"]
            for field, rows in self.graph.items():
                if field in query:
                    return rows if isinstance(rows, Response) else Response({"data": {"r": rows}})
            return Response({"data": {"r": []}})
        return Response(status=404, text="")
