"""Known airports (OurAirports, public domain): reading the codes the user types, and finding airports by code,
city, name or country."""
import csv
import unicodedata
from pathlib import Path

from letsfly.errors import CityCode, UnknownAirport
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
    "dallas": ["DFW", "DAL"], "houston": ["IAH", "HOU"], "miami": ["MIA", "FLL"], "mexico city": ["MEX", "NLU"],
}
# Spanish names of the cities searched most, to the name CITIES uses
SPANISH = {"londres": "london", "nueva york": "new york", "san pablo": "sao paulo", "tokio": "tokyo", "roma": "rome",
           "pekin": "beijing", "moscu": "moscow", "ciudad de mexico": "mexico city", "estambul": "istanbul",
           "seul": "seoul"}
# IATA city codes, which Google does not take: each names its city, and CITIES gives its airports when it has them.
# bin/airports.py keeps the same codes out of the aliases (BER and QSF there are also airports, so not here)
CITY_CODES = {
    "BAK": "baku", "BHZ": "belo horizonte", "BJS": "beijing", "BUE": "buenos aires", "BUH": "bucharest",
    "CAS": "casablanca", "CHI": "chicago", "DTT": "detroit", "EAP": "basel", "JKT": "jakarta", "LON": "london",
    "MIL": "milan", "MOW": "moscow", "NYC": "new york", "OSA": "osaka", "PAR": "paris", "REK": "reykjavik",
    "RIO": "rio de janeiro", "ROM": "rome", "SAO": "sao paulo", "SEL": "seoul", "SPK": "sapporo", "STO": "stockholm",
    "TYO": "tokyo", "WAS": "washington", "YEA": "edmonton", "YMQ": "montreal", "YTO": "toronto", "QDF": "dallas",
    "QHO": "houston", "QLA": "los angeles", "QMI": "miami",
}
LOOK_UP = "find_airports finds the code by city or airport name"


def resolve(codes: str, param: str | None = None) -> list[str]:
    """IATA codes for "EZE,AEP" or "SAEZ"; blank entries are ignored. param names the argument in the error."""
    found = [_resolve_one(code.strip(), param) for code in codes.split(",") if code.strip()]
    if not found:
        raise ParseError(f"No valid airport codes found in: '{codes}'", param)
    return found


def _resolve_one(code, param):
    upper = code.upper()
    if upper in ALIASES:
        return ALIASES[upper]
    if len(upper) == 4 and upper.isalpha():
        if upper not in ICAO:
            raise UnknownAirport(f"Unknown ICAO code: '{code}'", param, LOOK_UP)
        return ICAO[upper]
    if upper in CITY_CODES and upper not in AIRPORTS:
        city = CITY_CODES[upper]
        hint = ",".join(CITIES[city]) if city in CITIES else f"find_airports('{city}') lists its airports"
        raise CityCode(f"'{code}' is the city code of {city.title()}, not an airport: search its airports", param, hint)
    if upper not in AIRPORTS:
        raise UnknownAirport(f"Invalid airport code: '{code}'", param, LOOK_UP)
    return upper


def search(query: str, limit: int = 5) -> list[dict]:
    """Airports best first: exact IATA, ICAO or former code, a multi-airport city, then city, name and country
    matches, where airports with scheduled service and bigger ones go before closer text matches. A Spanish city
    name (londres) or a city code (LON) reads as the city."""
    q = _plain(query)
    q = CITY_CODES.get(q.upper()) or SPANISH.get(q, q)
    if not q:
        return []
    scores = {}
    if q.upper() in AIRPORTS:
        scores[q.upper()] = 6
    if q.upper() in ICAO:
        scores.setdefault(ICAO[q.upper()], 5)
    if q.upper() in ALIASES:
        scores.setdefault(ALIASES[q.upper()], 5)
    members = {code: rank for rank, code in enumerate(CITIES.get(q, []))}
    for code in members:
        scores.setdefault(code, 4)
    for code, city, name, country in _INDEX:
        if city == q:
            scores.setdefault(code, 3)
        elif city.startswith(q) or name.startswith(q):
            scores.setdefault(code, 2)
        elif q in city or q in name or q == country:
            scores.setdefault(code, 1)
    # Exact codes and every airport of the city first, in the order CITIES gives them
    ranked = sorted(scores, key=lambda c: (-(scores[c] if scores[c] >= 4 else 0), members.get(c, 0),
                                           -int(AIRPORTS[c]["scheduled"]), -int(AIRPORTS[c]["size"]), -scores[c], c))
    return [{"code": c, "name": AIRPORTS[c]["name"], "city": AIRPORTS[c]["city"], "country": AIRPORTS[c]["country"]}
            for c in ranked[:limit]]


def _plain(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


_INDEX = [(code, _plain(row["city"]), _plain(row["name"]), _plain(row["country"])) for code, row in AIRPORTS.items()]
