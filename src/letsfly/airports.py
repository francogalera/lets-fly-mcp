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

# The airports with scheduled service of each IATA metropolitan area, main ones first, by its IATA city code.
# The last ones share their code with an airport, which the search takes, so they only group the city's airports.
METROS = {
    "BAK": ("baku", ["GYD"]), "BHZ": ("belo horizonte", ["CNF"]), "BJS": ("beijing", ["PEK", "PKX"]),
    "BUE": ("buenos aires", ["EZE", "AEP"]), "BUH": ("bucharest", ["OTP", "BBU"]), "CAS": ("casablanca", ["CMN"]),
    "CHI": ("chicago", ["ORD", "MDW"]), "DTT": ("detroit", ["DTW"]), "EAP": ("basel", ["BSL"]),
    "JKT": ("jakarta", ["CGK", "HLP"]), "LON": ("london", ["LHR", "LGW", "STN", "LTN", "LCY", "SEN"]),
    "MIL": ("milan", ["MXP", "LIN", "BGY"]), "MOW": ("moscow", ["SVO", "DME", "VKO", "ZIA"]),
    "NYC": ("new york", ["JFK", "LGA", "EWR"]), "OSA": ("osaka", ["KIX", "ITM"]), "PAR": ("paris", ["CDG", "ORY"]),
    "REK": ("reykjavik", ["KEF", "RKV"]), "RIO": ("rio de janeiro", ["GIG", "SDU"]), "ROM": ("rome", ["FCO", "CIA"]),
    "SAO": ("sao paulo", ["GRU", "CGH", "VCP"]), "SEL": ("seoul", ["ICN", "GMP"]), "SPK": ("sapporo", ["CTS", "OKD"]),
    "STO": ("stockholm", ["ARN", "BMA", "NYO", "VST"]), "TYO": ("tokyo", ["NRT", "HND"]),
    "WAS": ("washington", ["IAD", "DCA", "BWI"]), "YEA": ("edmonton", ["YEG"]), "YMQ": ("montreal", ["YUL"]),
    "YTO": ("toronto", ["YYZ", "YTZ"]), "QDF": ("dallas", ["DFW", "DAL"]), "QHO": ("houston", ["IAH", "HOU"]),
    "QLA": ("los angeles", ["LAX", "BUR", "SNA", "ONT", "LGB"]), "QMI": ("miami", ["MIA", "FLL"]),
    "BKK": ("bangkok", ["BKK", "DMK"]), "DXB": ("dubai", ["DXB", "DWC"]), "IST": ("istanbul", ["IST", "SAW"]),
    "MEX": ("mexico city", ["MEX", "NLU"]), "SFO": ("san francisco", ["SFO", "OAK", "SJC"]),
    "SHA": ("shanghai", ["PVG", "SHA"]), "TPE": ("taipei", ["TPE", "TSA"]),
}
CITIES = {city: codes for city, codes in METROS.values()}
# IATA city codes, which the search does not take; bin/airports.py keeps the same codes out of the aliases
CITY_CODES = {code: city for code, (city, _) in METROS.items() if code not in AIRPORTS}
# Spanish names of the cities searched most, to the name CITIES uses
SPANISH = {"londres": "london", "nueva york": "new york", "san pablo": "sao paulo", "tokio": "tokyo", "roma": "rome",
           "pekin": "beijing", "moscu": "moscow", "ciudad de mexico": "mexico city", "estambul": "istanbul",
           "seul": "seoul"}
LOOK_UP = "find_airports finds the code by city or airport name"


def resolve(codes: str, param: str | None = None) -> list[str]:
    """IATA codes for "EZE,AEP" or "SAEZ"; blank entries are ignored. param names the argument in the error."""
    found = [_resolve_one(code.strip(), param) for code in codes.split(",") if code.strip()]
    if not found:
        raise ParseError(f"No airport code in '{codes}'", param)
    return found


def _resolve_one(code, param):
    upper = code.upper()
    if upper in ALIASES:
        return ALIASES[upper]
    if len(upper) == 4 and upper.isalpha():
        if upper not in ICAO:
            raise UnknownAirport(f"No airport has the ICAO code '{code}'", param, LOOK_UP)
        return ICAO[upper]
    if upper in CITY_CODES and upper not in AIRPORTS:
        city = CITY_CODES[upper]
        raise CityCode(f"'{code}' is the city code of {city.title()}, not an airport: search its airports", param,
                       ",".join(CITIES[city]))
    if upper not in AIRPORTS:
        raise UnknownAirport(f"No airport has the code '{code}'", param, LOOK_UP)
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
