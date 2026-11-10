"""The flight metasearch: one search asks every seller at once and returns all their itineraries. The API limits
searches per IP and answers more with a block of hours, so a search is expensive, while its polls, its pull and its
GraphQL (dates and price history) are free. The host is named here and in bin/flights only.
Flow, as the web client runs it: search, meta every EVERY s until version is "final", one pull of the whole result."""
import base64
import binascii
import random
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta

from letsfly import airports, links, models
from letsfly.decode import _overnight
from letsfly.errors import FlightsError
from letsfly.models import Itinerary, Layover, Leg, ParseError

API = "https://api.turismocity.com"
WEB = "https://www.turismocity.com.ar"
PROFILE = "safari2601"
TIMEOUT = 60
EVERY = 3  # the web polls meta every 0.5 s
POLLS = 10  # 30 s: final came within 21 s in every search of the spike
CACHE_FOR = timedelta(minutes=15)
SAFE_PER_HOUR = 10
CABINS = {"ECONOMY": "Economy", "PREMIUM_ECONOMY": "PremiumEconomy", "BUSINESS": "Business", "FIRST": "First"}
BLOCKED_HINT = "Usually lasts hours: go on with search_flights and do not retry"

sleep = time.sleep
_local = threading.local()
_searching = threading.Lock()
_cache: dict = {}
_spent: list[datetime] = []
_blocked: list[datetime | None] = [None]


class MetasearchError(FlightsError):
    kind = "metasearch"


class MetasearchTimeout(MetasearchError):
    kind = "metasearch_timeout"


class MetasearchUnreachable(MetasearchError):
    kind = "metasearch_connection"


class MetasearchCertificate(MetasearchUnreachable):
    kind = "metasearch_certificate"


class MetasearchRefused(MetasearchError):
    kind = "metasearch_http"


class MetasearchUnreadable(MetasearchError):
    """An answer arrived but could not be read, or GraphQL refused the query: the API changed."""
    kind = "metasearch_page"


class MetasearchBlocked(MetasearchError):
    """The API's own limit: it refuses this IP for hours."""
    kind = "metasearch_blocked"


class MetasearchChallenge(MetasearchError):
    """Cloudflare asked for a browser check."""
    kind = "metasearch_challenge"


@dataclass
class Offer:
    """One itinerary of the result: one Itinerary per leg of the trip, priced for all adults by its cheapest seller."""
    directions: list[Itinerary]
    price: float
    sellers: list[tuple[str, float]]  # every seller that prices it, cheapest first
    carry_on: int | None
    checked: int | None
    bag_fee: float | str | None  # "included", the fee in the result's currency, or None when the seller gives none
    changes: float | str | None
    refunds: float | str | None
    link: str | None


def code(text, param):
    """One airport, or an IATA city code, which the metasearch opens into its airports (BUE: EZE and AEP)."""
    if "," in text:
        raise ParseError(f"'{text}' has several codes: give one airport or city code (BUE takes EZE and AEP)", param)
    upper = text.strip().upper()
    return upper if upper in airports.CITY_CODES else airports.resolve(text, param)[0]


def same_place(a, b):
    """Two codes of one city: the same code, a city code and one of its airports, or two airports of one city."""
    return a == b or _city(a) == _city(b)


def _city(code):
    return next((city for city, (_, members) in airports.METROS.items() if code == city or code in members), code)


def key(legs):
    """The search key: EZE-MAD.14-4-2027.MAD-EZE.28-4-2027, one origin-destination.D-M-YYYY per leg."""
    return ".".join(f"{o}-{d}.{int(day[8:])}-{int(day[5:7])}-{day[:4]}" for o, d, day in legs)


def search(legs, adults, cabin):
    """The result's offers and the search's cost: 0 when the same search ran in the last CACHE_FOR, else 1.
    complete is False when meta did not reach final within POLLS: the slowest sellers are missing. An empty result
    is not kept: the metasearch sometimes answers final with no itineraries for minutes, for a trip it sells."""
    k = (key(legs), adults, cabin)
    with _searching:
        hit = _cache.get(k)
        if hit and models.now() - hit[0] < CACHE_FOR:
            return hit[1], 0
        params = {"s": k[0], "countryCode": "AR", "numAdults": adults, "currency": "USD"}
        if cabin != "ECONOMY":
            params["cabinClass"] = CABINS[cabin]
        started = _get("/flights/search", params)
        _spent.append(models.now())
        sid = started.get("searchId") if isinstance(started, dict) else None
        if not sid:
            raise MetasearchUnreadable("The search started without an id")
        final = False
        for _ in range(POLLS):
            sleep(EVERY)
            if (_get(f"/flights/pull/{sid}/meta", {"rand": random.random()}) or {}).get("version") == "final":
                final = True
                break
        body = unpack(_get(f"/flights/rpull/{sid}", {"v": "final"}))
        result = {"offers": offers(body), "matrix": body.get("matrix") or [], "complete": final}
        if result["offers"]:
            _cache[k] = (models.now(), result)
        return result, 1


def budget():
    """What this session spent: searches in the last hour, against the most that never got an IP blocked."""
    hour_ago = models.now() - timedelta(hours=1)
    return {"used_last_hour": sum(1 for t in _spent if t > hour_ago), "safe_per_hour": SAFE_PER_HOUR}


def calendar(origin, destination, month, round_trip, outbound=None):
    """Lowest fare seen per day of month (YYYY-MM): departures, or with outbound the returns for that departure, as
    (YYYY-MM-DD, price, low) by date; low is the metasearch's mark for the cheapest days. Free: GraphQL."""
    year, number = month.split("-")
    extra = f', outbound: "{outbound}"' if outbound else ""
    rows = _graph(f'calendarFlightPrices(iso: "AR", departure: "{origin}", arrival: "{destination}", year: "{year}", '
                  f'month: "{int(number)}", triptype: "{"roundtrip" if round_trip else "oneway"}", currency: "USD"'
                  f'{extra}) {{ price calendardate isLowprice }}')
    return sorted((r["calendardate"], round(r["price"], 2), bool(r.get("isLowprice"))) for r in rows
                  if _amount(r.get("price")) and r.get("calendardate"))


def months(origin, destination, round_trip):
    """Lowest fare seen per month for the next 12, as (YYYY-MM, price) by month. Free: GraphQL."""
    one_way = "" if round_trip else ", oneWay: true"
    rows = _graph(f'cheapFlightsMonthsFeed(iso: "AR", currency: "USD", departureIata: "{origin}", arrivalIata: '
                  f'"{destination}"{one_way}, version: "V2") {{ minprice month year }}')
    return sorted((f"{r['year']:04d}-{r['month']:02d}", round(r["minprice"], 2)) for r in rows
                  if _amount(r.get("minprice")) and r.get("year") and r.get("month"))


def history(origin, destination, outbound, inbound):
    """Average fare per day of the last 90 days for this round trip, as (YYYY-MM-DD, price) by day. Free: GraphQL.
    Only round trips have it, and only some routes: none for one ways in 12 of 12 routes tried."""
    rows = _graph(f'statsLast90(currency: "USD", iso: "AR", departure: "{origin}", arrival: "{destination}", '
                  f'outbound: "{outbound}", inbound: "{inbound}") {{ avg_price created }}')
    return sorted((r["created"], round(r["avg_price"], 2)) for r in rows if _amount(r.get("avg_price")) and r.get("created"))


def _graph(field):
    """The rows of one GraphQL field, aliased r. The query goes in the URL, as the web client sends it, so the CDN can
    keep it a minute; every value in it was validated before."""
    data = _get("/graphql-cdn", {"query": f"query {{ r: {field} }}"}, {"device": "desktop"})
    rows = (data.get("data") or {}).get("r") if isinstance(data, dict) else None
    if not isinstance(rows, list) or data.get("errors"):
        raise MetasearchUnreadable("The metasearch's dates answer could not be read")
    return rows


def unpack(data):
    """The web client's decoder. An object whose sorted keys were not seen yet comes literal and registers the next
    template id (1, 2...); a later one with the same keys comes as [id, values...], several in a row in one list. A
    list that starts with a number carries a leading 0 so it is not read as a template."""
    templates = {}

    def number(x):
        return isinstance(x, int | float) and not isinstance(x, bool)

    def packed(x):
        return isinstance(x, list) and len(x) > 0 and number(x[0]) and x[0] != 0

    def read(y):
        if isinstance(y, dict):
            names = sorted(y)
            if names:
                templates[len(templates) + 1] = names
            return {name: read(y[name]) for name in names}
        if not isinstance(y, list) or not y:
            return [] if isinstance(y, list) else y
        if packed(y):
            names = templates[y[0]]
            rows = [{name: read(y[i * len(names) + j + 1]) for j, name in enumerate(names)}
                    for i in range((len(y) - 1) // len(names))]
            return rows if len(rows) > 1 else rows[0]
        out = []
        for item in y[1:] if y[0] == 0 and number(y[0]) else y:
            value = read(item)
            if packed(item) and isinstance(value, list):
                out.extend(value)
            else:
                out.append(value)
        return out

    return read(data)


def offers(body):
    """Every itinerary that has a price, as an Offer."""
    names = (body.get("dictionary") or {}).get("airline") or {}
    found = []
    for it in body.get("itineraries") or []:
        # bestPrice is the web's pick and now and then a provider sells the same itinerary for a little less: the
        # cheapest offer gives the row its price, link, bags and fees, bestPrice winning a tie
        quoted = [p for p in [it.get("bestPrice")] + list(it.get("providers") or [])
                  if isinstance(p, dict) and _amount((p.get("price") or {}).get("value"))]
        if not quoted:
            continue
        best = min(quoted, key=lambda p: p["price"]["value"])
        price, fares = best["price"]["value"], best["price"]
        sellers = {}
        for p in quoted:
            name, value = p.get("providerName") or "?", round(p["price"]["value"], 2)
            if value < sellers.get(name, float("inf")):
                sellers[name] = value
        found.append(Offer(
            directions=[_direction(seg, i, best, names) for i, seg in enumerate(it["segments"])],
            price=round(price, 2), sellers=sorted(sellers.items(), key=lambda s: s[1]),
            carry_on=_all(best.get("cabinBaggageIncluded")), checked=_all(best.get("baggageIncluded")),
            bag_fee=_fee(best.get("hasBaggageIncluded"), fares.get("baggageIncluded")),
            changes=_fee(best.get("hasChangesIncluded"), fares.get("changes")),
            refunds=_fee(best.get("hasRefundsIncluded"), fares.get("refunds")),
            link=_link(best.get("base64"))))
    return found


def _direction(segment, index, best, names):
    flights = segment["flights"]
    legs = [Leg(f["airline"], names.get(f["airline"], f["airline"]), str(f["flightNumber"]), f["departure"],
                f["arrival"], _time(f["departureTime"]), _time(f["arrivalTime"]),
                names.get(f["operatingAirline"], f["operatingAirline"]) if f.get("operatingAirline") else None)
            for f in flights]
    layovers = [Layover(a.destination, int((b.departs - a.arrives).total_seconds() // 60),
                        _overnight(a.arrives, b.departs), a.destination != b.origin) for a, b in zip(legs, legs[1:])]
    return Itinerary(legs, None, int(segment["duration"]), layovers, any(f.get("selfTransfer") for f in flights),
                     _all(_at(best.get("cabinBaggageIncluded"), index)), _all(_at(best.get("baggageIncluded"), index)))


def _time(text):
    return datetime.strptime(text[:16], "%Y-%m-%d %H:%M")


def _all(flags):
    """1 when every flag (nested per leg and flight) is true, 0 when any is false, None when there are none."""
    values = [v for v in _flat(flags) if isinstance(v, bool)]
    return None if not values else int(all(values))


def _flat(x):
    if isinstance(x, list):
        for item in x:
            yield from _flat(item)
    elif x is not None:
        yield x


def _at(seq, index):
    return seq[index] if isinstance(seq, list) and len(seq) > index else None


def _fee(included, amount):
    return "included" if included else (round(amount, 2) if _amount(amount) else None)


def _amount(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _link(encoded):
    """The seller's page: base64 with its % escaped; relative ones hang from the web. A POST redirect is no link."""
    if not encoded:
        return None
    raw = urllib.parse.unquote(encoded)
    try:
        url = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    if url.startswith("/flights/postredir"):
        return None
    url = WEB + url if url.startswith("/") else url
    return links.short(url) if url.startswith("https://") else None


def _get(path, params, headers=None):
    """GET one API path and return its JSON; a block, a challenge or a refusal raises its own error. After a block,
    this session asks nothing more: the API refuses the whole IP for hours, and another try only prolongs it."""
    if _blocked[0]:
        raise MetasearchBlocked(f"The metasearch blocked this IP at {_blocked[0]:%H:%M} UTC", hint=BLOCKED_HINT)
    try:
        response = _session().get(f"{API}{path}", params=params, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        raise _wrap(e) from e
    if response.headers.get("cf-mitigated"):
        raise MetasearchChallenge("Cloudflare asked for a browser check, which this client cannot pass",
                                  hint="Go on with search_flights")
    if response.status_code in (403, 429):
        _blocked[0] = models.now()
        raise MetasearchBlocked(f"The metasearch refused this IP (HTTP {response.status_code}): its search limit",
                                hint=BLOCKED_HINT)
    if response.status_code == 400 and path == "/graphql-cdn":
        raise MetasearchUnreadable("The metasearch refused the dates query: its API changed")
    if response.status_code != 200:
        raise MetasearchRefused(f"The metasearch answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as e:
        raise MetasearchUnreadable("The metasearch answered something that is not JSON") from e


def _session():
    if not hasattr(_local, "session"):
        from curl_cffi import requests  # ~100 ms to import, only paid on the first request

        _local.session = requests.Session(impersonate=PROFILE)
    return _local.session


def _wrap(e):
    from curl_cffi.requests import exceptions as curl

    if isinstance(e, curl.CertificateVerifyError) or (isinstance(e, curl.ConnectionError) and getattr(e, "code", None) == 60):
        return MetasearchCertificate("The TLS certificate of the metasearch did not verify")
    if isinstance(e, curl.Timeout):
        return MetasearchTimeout(f"The metasearch did not answer within {TIMEOUT} s; try again in a moment")
    if isinstance(e, curl.ConnectionError):
        return MetasearchUnreachable("No connection to the metasearch: the network, DNS or the proxy is down")
    return MetasearchError(f"The request to the metasearch failed: {e.__class__.__name__}")
