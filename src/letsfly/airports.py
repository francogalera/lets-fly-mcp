"""Known airports (OurAirports, public domain): reading the codes the user types, and finding airports by code,
city, name or country."""
import csv
import unicodedata
from pathlib import Path

from letsfly.models import ParseError

with open(Path(__file__).parent / "data" / "airports.csv", newline="", encoding="utf-8") as f:
    AIRPORTS = {row["iata"]: row for row in csv.DictReader(f)}
ICAO = {row["icao"]: code for code, row in AIRPORTS.items() if row["icao"]}
# Codes an airport used to have, e.g. PBI and KPBI for DJT since 2026
ALIASES = {alias: code for code, row in AIRPORTS.items() for alias in row["aliases"].split()}

# Cities served by several airports, and short aliases (sf, la, nyc); from fli's city table
CITIES = {
    "new york": ["JFK", "LGA", "EWR"], "nyc": ["JFK", "LGA", "EWR"], "chicago": ["ORD", "MDW"],
    "washington": ["IAD", "DCA", "BWI"], "washington dc": ["IAD", "DCA", "BWI"],
    "london": ["LHR", "LGW", "STN", "LTN", "LCY"], "paris": ["CDG", "ORY"], "tokyo": ["NRT", "HND"],
    "osaka": ["KIX", "ITM"], "seoul": ["ICN", "GMP"], "beijing": ["PEK", "PKX"], "shanghai": ["PVG", "SHA"],
    "bangkok": ["BKK", "DMK"], "istanbul": ["IST", "SAW"], "moscow": ["SVO", "DME", "VKO"], "milan": ["MXP", "LIN"],
    "rome": ["FCO", "CIA"], "sao paulo": ["GRU", "CGH"], "rio de janeiro": ["GIG", "SDU"], "rio": ["GIG", "SDU"],
    "toronto": ["YYZ", "YTZ"], "buenos aires": ["EZE", "AEP"], "dubai": ["DXB", "DWC"], "taipei": ["TPE", "TSA"],
    "san francisco": ["SFO", "OAK", "SJC"], "sf": ["SFO", "OAK", "SJC"], "bay area": ["SFO", "OAK", "SJC"],
    "los angeles": ["LAX", "BUR", "SNA", "ONT", "LGB"], "la": ["LAX", "BUR", "SNA", "ONT", "LGB"],
    "dallas": ["DFW", "DAL"], "houston": ["IAH", "HOU"], "miami": ["MIA", "FLL"],
}


def resolve(codes: str) -> list[str]:
    """IATA codes for "EZE,AEP" or "SAEZ"; blank entries are ignored."""
    found = [_resolve_one(code.strip()) for code in codes.split(",") if code.strip()]
    if not found:
        raise ParseError(f"No valid airport codes found in: '{codes}'")
    return found


def _resolve_one(code):
    upper = code.upper()
    if upper in ALIASES:
        return ALIASES[upper]
    if len(upper) == 4 and upper.isalpha():
        if upper not in ICAO:
            raise ParseError(f"Unknown ICAO code: '{code}'")
        return ICAO[upper]
    if upper not in AIRPORTS:
        raise ParseError(f"Invalid airport code: '{code}'")
    return upper


def search(query: str, limit: int = 5) -> list[dict]:
    """Airports best first: exact IATA, ICAO or former code, a multi-airport city, then city, name and country
    matches, where airports with scheduled service and bigger ones go before closer text matches."""
    q = _plain(query)
    if not q:
        return []
    scores = {}
    if q.upper() in AIRPORTS:
        scores[q.upper()] = 6
    if q.upper() in ICAO:
        scores.setdefault(ICAO[q.upper()], 5)
    if q.upper() in ALIASES:
        scores.setdefault(ALIASES[q.upper()], 5)
    for rank, code in enumerate(CITIES.get(q, [])):
        scores.setdefault(code, 4 - rank / 100)
    for code, city, name, country in _INDEX:
        if city == q:
            scores.setdefault(code, 3)
        elif city.startswith(q) or name.startswith(q):
            scores.setdefault(code, 2)
        elif q in city or q in name or q == country:
            scores.setdefault(code, 1)
    ranked = sorted(scores, key=lambda c: (-(scores[c] if scores[c] >= 4 else 0), -int(AIRPORTS[c]["scheduled"]),
                                           -int(AIRPORTS[c]["size"]), -scores[c], c))
    return [{"code": c, "name": AIRPORTS[c]["name"], "city": AIRPORTS[c]["city"], "country": AIRPORTS[c]["country"]}
            for c in ranked[:limit]]


def _plain(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


_INDEX = [(code, _plain(row["city"]), _plain(row["name"]), _plain(row["country"])) for code, row in AIRPORTS.items()]
