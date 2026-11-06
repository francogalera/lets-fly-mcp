"""The flights MCP: four tools over Google Flights and promociones-aereas.com.ar, and the shape of each answer.
Runs over stdio, so nothing here may print: stdout belongs to the protocol."""
from typing import Annotated, Literal, NotRequired, TypedDict

from letsfly import airports, links, params, promos, search
from letsfly.errors import Failure
from letsfly.mcp import Range, serve, tool
from letsfly.tfs import booking_link

# ChatGPT reads only the first 512 characters and Codex caps plugin servers at 1,000 bytes, so the core goes first
INSTRUCTIONS = (
    "Never state a price, time, airline or link that is not in a tool result of this session. Copy each link "
    "exactly as returned: never build, edit, shorten or join links. Fixed dates: search_flights on them. Flexible "
    "dates: cheapest_dates first, then search_flights on the best dates. An empty result means this search found "
    "nothing, not that no flight exists: try nearby dates or airports and say so. A tool error carries a hint: "
    "follow it and retry.\n"
    "Use only airport codes find_airports returned or a flight tool accepted. Call promociones-aereas only when the "
    "user asks for deals or packages and names an origin in Argentina, with no flight tool: it reads a blog at about "
    "5 s per request, so list with details=false first, then read at most 3 deals. Answer in the language of the "
    "request, best option first, with its price, bags, route, times and link."
)

Airports = Annotated[str, "IATA code, or several separated by commas (EZE,AEP)"]
Day = Annotated[str, "Date YYYY-MM-DD"]
Stops = Literal["ANY", "NON_STOP", "ONE_STOP", "TWO_PLUS_STOPS"]
Currency = Annotated[str, "Currency of every price"]
Link = Annotated[str, f"Short local link ({links.BASE}/<id>) that opens the real page"]
# Extras next to what was asked: enough to show a cheaper partner without doubling the reply
CODESHARE_ROWS = 3


class Layover(TypedDict):
    airport: Annotated[str, "Where the connection is, IATA code"]
    minutes: Annotated[int, "From landing to the next departure"]
    overnight: NotRequired[Annotated[bool, "Present when the layover touches 00:00 to 05:00"]]
    change_of_airport: NotRequired[Annotated[bool, "Present when the next leg leaves from another airport"]]


class Direction(TypedDict):
    route: Annotated[str, "Airports in order, EZE-GRU-NRT; AEP/EZE lands at one and leaves from the other"]
    flights: Annotated[str, "Flight numbers in order, separated by spaces"]
    depart: Annotated[str, "Local departure time, YYYY-MM-DD HH:MM"]
    arrive: Annotated[str, "Local arrival time, YYYY-MM-DD HH:MM"]
    hours: Annotated[float, "Total duration"]
    layovers: Annotated[list[Layover], "One per connection, in order"]
    operated_by: Annotated[dict[str, str] | None, "Flight to the company that flies it, when not the seller"]
    sold_as: NotRequired[Annotated[dict[str, str], "In codeshares: flight to the number the airlines asked for sell it under"]]


class Bags(TypedDict):
    carry_on: Annotated[int | None, "Carry-on bags included; null when Google does not say"]
    checked: Annotated[int | None, "Checked bags included; null when Google does not say"]


class Flight(TypedDict):
    price: Annotated[float | None, "Fare for all adults; on a round trip, for this outbound and return pair"]
    airlines: Annotated[str, "Airlines selling the legs"]
    bags: Annotated[Bags, "Bags the fare includes"]
    out: Annotated[Direction, "Outbound"]
    back: NotRequired[Annotated[Direction, "Return, on a round trip"]]
    price_estimated: NotRequired[Annotated[bool, "The pair had no quote, so price is the outbound's"]]
    price_other_seller: NotRequired[Annotated[bool, "Google gave no price for airlines: this is the flight's price without that filter, maybe another seller's"]]
    self_transfer: NotRequired[Annotated[bool, "Separate tickets: a missed connection is the traveller's problem"]]
    link: NotRequired[Annotated[str, "Short local link to this row's booking page on Google Flights"]]


PriceHistory = TypedDict("PriceHistory", {
    "from": Annotated[str, "Date of the first price in weekly, YYYY-MM-DD"],
    "weekly": Annotated[list[float], "Lowest fare every 7 days, oldest first; the last one is the latest day"],
    "min": Annotated[float, "Lowest fare of the whole daily series, at most about 2 months"],
    "max": Annotated[float, "Highest fare of the whole daily series"],
})


class PriceReference(TypedDict):
    lowest: Annotated[float | None, "Lowest fare of the unfiltered first page, whatever airlines and the windows say; a round trip row can come below it"]
    typical: Annotated[float | None, "What Google considers a typical fare for this search"]
    low: Annotated[float | None, "Bottom of the typical range"]
    high: Annotated[float | None, "Top of the typical range"]
    history: Annotated[PriceHistory | None, "How lowest moved; null when Google gives none, as with over 1 adult or in first class"]


class FlightsResult(TypedDict):
    currency: Currency
    count: Annotated[int, "Rows in flights"]
    flights: Annotated[list[Flight], "The cheapest of each distinct outbound first, then the rest, cheapest first"]
    codeshares: NotRequired[Annotated[list[dict], "Rows like those of flights and in their order, outside what airlines asked for: up to 3 with a partner's flight they also sell"]]
    codeshares_total: NotRequired[Annotated[int, "How many such rows the search found, before keeping up to 3"]]
    note: NotRequired[Annotated[str, "Why some round trips are missing"]]
    price_reference: NotRequired[Annotated[PriceReference, "Google's reference; missing if it gives none"]]


DateRow = TypedDict("DateRow", {
    "date": Annotated[str, "Departure date, YYYY-MM-DD"],
    "return": NotRequired[Annotated[str, "Return date, with trip_days"]],
    "price": Annotated[float, "Lowest fare for all adults that day; with trip_days, for the round trip"],
})


class DatesResult(TypedDict):
    currency: Currency
    count: Annotated[int, "Rows in dates"]
    dates: Annotated[list[DateRow], "Cheapest first"]
    missing_dates: NotRequired[Annotated[list[str], "Dates whose page failed: unknown, not without flights"]]
    note: NotRequired[Annotated[str, "Every page loaded and none had fares"]]


class Airport(TypedDict):
    code: Annotated[str, "IATA code, what the flight tools take"]
    name: Annotated[str, "Airport name"]
    city: Annotated[str, "City it serves"]
    country: Annotated[str, "Country"]


class AirportsResult(TypedDict):
    airports: Annotated[list[Airport], "Best match first"]


class Featured(TypedDict):
    hotel: Annotated[str, "Hotel name"]
    out: Annotated[str | None, "Departure date, YYYY-MM-DD"]
    back: Annotated[str | None, "Return date, YYYY-MM-DD"]
    meal_plan: Annotated[str, "Meal plan, as the package link names it"]
    link: Link


class DealDate(TypedDict):
    out: Annotated[str, "Departure date, YYYY-MM-DD"]
    back: Annotated[str, "Return date, YYYY-MM-DD"]
    to: NotRequired[Annotated[str, "Destination airport, when the link names one"]]
    days: Annotated[int | None, "Length of stay in days"]
    direct: Annotated[bool, "The blog lists it as a direct flight"]
    link: Link


class Details(TypedDict):
    prices_seen_at: NotRequired[Annotated[str, "When the blog saw the prices, Argentina time"]]
    expired: Annotated[bool, "The blog marks the post as expired"]
    notes: Annotated[list[str], "The post's opening paragraphs"]
    badges: NotRequired[Annotated[list[str], "Labels the blog puts on the post"]]
    featured: NotRequired[Annotated[list[Featured], "Hotels a package features"]]
    other_occupancy: NotRequired[Annotated[dict[str, str], "Room occupancy, named as on the blog, to the link of its dates"]]
    fields: NotRequired[Annotated[dict[str, str], "The post's labelled facts: airline, baggage, payment terms"]]
    dates_total: NotRequired[Annotated[int, "How many dates the post had, when more than the 60 listed"]]
    dates: Annotated[list[DealDate], "Dates on sale, each with its own link and no price"]


class Deal(TypedDict):
    published: Annotated[str | None, "When the post went up, YYYY-MM-DD HH:MM Argentina time"]
    type: Annotated[Literal["package", "flight", "hotel", "car", "activity", "note"], "What the deal sells"]
    title: Annotated[str, "The post's title"]
    price_from: Annotated[float | None, "The title's starting price: the lowest when the post was written"]
    currency: Annotated[Literal["USD", "ARS"] | None, "Currency of price_from"]
    basis: NotRequired[Annotated[str, "What price_from covers: per person, per night"]]
    origin: NotRequired[Annotated[str, "Departure city the title names"]]
    url: Annotated[str, "The post"]
    details: NotRequired[Annotated[Details | Failure, "The post read in full, or the error that kept it from it"]]


class DealsResult(TypedDict):
    source: Annotated[str, "The blog"]
    count: Annotated[int, "Deals returned"]
    deals: Annotated[list[Deal], "Newest first"]


def _flight(leg):
    return leg.flight_number if leg.flight_number.startswith(leg.airline) else f"{leg.airline}{leg.flight_number}"


def _route(legs):
    """EZE-GRU-NRT, with an airport change shown as AEP/EZE where a leg leaves from another airport."""
    stops = [legs[0].origin]
    for leg, after in zip(legs, legs[1:] + [None]):
        stops.append(leg.destination + (f"/{after.origin}" if after and after.origin != leg.destination else ""))
    return "-".join(stops)


def _direction(itinerary, shared_with=frozenset()):
    legs = itinerary.legs
    operated = {_flight(leg): leg.operator for leg in legs
                if leg.operator and not leg.operator.lower().startswith(leg.airline_name.lower())}
    sold_as = {_flight(leg): " ".join(leg.sold_as[code] for code in sorted(shared_with & leg.sold_as.keys()))
               for leg in legs if leg.airline not in shared_with and shared_with & leg.sold_as.keys()}
    return {
        "route": _route(legs),
        "flights": " ".join(_flight(leg) for leg in legs),
        "depart": legs[0].departs.strftime("%Y-%m-%d %H:%M"),
        "arrive": legs[-1].arrives.strftime("%Y-%m-%d %H:%M"),
        "hours": round(itinerary.minutes / 60, 1),
        "layovers": [{"airport": x.airport, "minutes": x.minutes} | ({"overnight": True} if x.overnight else {})
                     | ({"change_of_airport": True} if x.change_of_airport else {}) for x in itinerary.layovers],
        "operated_by": operated or None,
    } | ({"sold_as": sold_as} if sold_as else {})


def _flight_rows(trips, query, currency, limit, all_links, shared_with=frozenset()):
    rows = []
    for index, trip in enumerate(_varied(sorted(trips, key=lambda f: _priced(f).price or float("inf")))[:limit]):
        outbound, back = trip if isinstance(trip, tuple) else (trip, None)
        fare = _priced(trip)
        names = [leg.airline_name for direction in (outbound, back) if direction for leg in direction.legs]
        row = {"price": fare.price, "airlines": ", ".join(dict.fromkeys(names)),
               "bags": {"carry_on": fare.carry_on, "checked": fare.checked}, "out": _direction(outbound, shared_with)}
        if back and back.price is None and outbound.price is not None:
            row["price_estimated"] = True
        if fare.price_other_seller:
            row["price_other_seller"] = True
        if back:
            row["back"] = _direction(back, shared_with)
        if outbound.self_transfer or (back and back.self_transfer):
            row["self_transfer"] = True
        if all_links or index < 2:
            row["link"] = links.short(booking_link([outbound, back] if back else [outbound], query.seat, query.adults,
                                                   currency))
        rows.append(row)
    return rows


@tool()
def search_flights(
    origin: Airports,
    destination: Airports,
    date: Day,
    return_date: Annotated[str | None, "Return date YYYY-MM-DD; without it, one way"] = None,
    max_stops: Stops = "ANY",
    airlines: Annotated[list[str] | None, "Only these airlines, IATA codes (AR, DM); partners' flights they also sell come apart in codeshares"] = None,
    departure_window: Annotated[str | None, "Hours the outbound may leave, for example 6-20, up to 20:59; the return is not filtered"] = None,
    return_window: Annotated[str | None, "Hours the return may leave, for example 18-23, up to 23:59; needs return_date"] = None,
    adults: Annotated[int, Range(1, 9)] = 1,
    cabin: Literal["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"] = "ECONOMY",
    currency: str = "USD",
    limit: Annotated[int, Range(1, 20)] = 5,
    all_links: Annotated[bool, "Booking link on every row; by default only the first 2 rows carry one"] = False,
) -> FlightsResult:
    """Flights on one date, or round trips with return_date: the cheapest of each distinct outbound first, then the
    rest, cheapest first. price is for all adults; on a round trip, for the pair (price_estimated: the outbound's
    price stands in; price_other_seller: maybe not the airline's, say so). null bags: Google does not say. self_transfer: separate tickets. route AEP/EZE and
    change_of_airport: the next leg leaves from another airport. overnight: touches 00:00 to 05:00. operated_by: who
    flies a leg when not the seller. Each window filters one direction. codeshares: up to 3 of codeshares_total rows with
    a partner's flight the airlines also sell (number in sold_as), outside the request: always mention them, apart.
    price_reference covers all airlines and hours, only max_stops and cabin narrow it: lowest (a pair can beat it),
    typical, range low to high, in price's terms; history: weekly lowest since from, daily min and max."""
    query = params.flight_search(origin, destination, date, return_date, max_stops, airlines, departure_window,
                                 return_window, adults, cabin, currency)
    found, lost, reference = search.flights(query)
    asked, shared = search.split(found, query.airlines)
    rows = _flight_rows(asked, query, currency, limit, all_links)
    out = {"currency": currency, "count": len(rows), "flights": rows}
    if shared:
        out["codeshares"] = _flight_rows(shared, query, currency, min(limit, CODESHARE_ROWS), all_links, query.airlines)
        out["codeshares_total"] = len(shared)
    if lost:
        out["note"] = f"The return page failed to load for {lost} of the outbounds, so their round trips are missing"
    if reference:
        out["price_reference"] = reference
    return out


def _varied(trips):
    """The cheapest trip of each distinct outbound first, then the rest, keeping price order within each."""
    seen, first, rest = set(), [], []
    for trip in trips:
        outbound = trip[0] if isinstance(trip, tuple) else trip
        key = tuple((leg.airline, leg.flight_number, leg.departs) for leg in outbound.legs)
        (rest if key in seen else first).append(trip)
        seen.add(key)
    return first + rest


def _priced(trip):
    """The itinerary that carries the fare: on a round trip, the return quoted for that outbound."""
    if not isinstance(trip, tuple):
        return trip
    outbound, back = trip
    return back if back.price is not None else outbound


@tool()
def cheapest_dates(
    origin: Airports,
    destination: Airports,
    start_date: Day,
    end_date: Day,
    trip_days: Annotated[int | None, Range(1), "Length of stay in days; without it the search is one way"] = None,
    adults: Annotated[int, Range(1, 9)] = 1,
    max_stops: Stops = "ANY",
    currency: str = "USD",
    limit: Annotated[int, Range(1, 60)] = 10,
) -> DatesResult:
    """Lowest price per departure date, cheapest first; one Google page per date, 93 dates at most. price: lowest
    fare for all adults, for the round trip with trip_days. missing_dates: their page failed, so they are unknown,
    not empty. note: every page loaded and none had fares."""
    query, days = params.date_search(origin, destination, start_date, end_date, trip_days, adults, max_stops,
                                     currency)
    found, missing = search.dates(query, days, trip_days)
    rows = [{"date": day, **({"return": back} if back else {}), "price": price}
            for day, back, price in sorted(found, key=lambda d: d[2])[:limit]]
    out = {"currency": currency, "count": len(rows), "dates": rows}
    if missing:
        out["missing_dates"] = missing
    elif not rows:
        out["note"] = "Every page loaded and Google has no fares for these dates"
    return out


@tool()
def find_airports(
    query: Annotated[str, "IATA or ICAO code, city (also in Spanish: Londres), airport name or country"],
    limit: Annotated[int, Range(1, 20)] = 5,
) -> AirportsResult:
    """Airports by IATA or ICAO code, city (Spanish names too: Londres), airport name or country, best match first.
    A city with several airports, by name or by IATA city code (BUE, LON), lists all of them first. The flight tools
    take those airport codes, never a city code."""
    return {"airports": airports.search(query, limit)}


@tool("promociones-aereas")
def promociones_aereas(
    query: Annotated[str, "What to look for, in Spanish like the blog: destination, airline or origin (punta cana, paquetes brasil). Empty: the latest posts"] = "",
    type: Literal["any", "package", "flight", "hotel", "car", "activity"] = "any",
    page: Annotated[int, Range(1, 10), "Feed page, 15 posts per page, newest first"] = 1,
    limit: Annotated[int, Range(1, 15), "How many deals to return, each with its details (about 5 s per deal)"] = 5,
    details: Annotated[bool, "By default every deal nests its details: dates with links, payment terms, airline, baggage, "
                             "featured hotels and when the blog saw the price. false only for a quick scan of many titles"] = True,
    month: Annotated[str | None, "Only departure dates in that month, YYYY-MM"] = None,
    stay_days: Annotated[int | None, Range(1, 60), "Only dates with that length of stay in days, ±1 (each date carries days to tell the exact ones)"] = None,
) -> DealsResult:
    """Deals on promociones-aereas.com.ar, an Argentine blog (flights, packages, hotels), newest first, with details.
    Call it only when the user asks for deals or packages and names an origin in Argentina: flight searches don't need
    it. price_from is the title's starting price: the lowest when the post was written (prices_seen_at), not per date;
    currency can be ARS. details: the dates on sale, each with its own link and to (destination airport) when known,
    the post's fields (airline, baggage, payment), featured hotels and notes, or an error object if the post was
    unreadable. A package lists the dates of the room occupancy its price is based on, other_occupancy links the rest,
    and a post with more than 60 dates gives dates_total. Dates carry no price: check a flight deal with search_flights
    only if the user asks."""
    return promos.search(query, type, page, limit, details, month, stay_days)


if __name__ == "__main__":
    serve(INSTRUCTIONS)
