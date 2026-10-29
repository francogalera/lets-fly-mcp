"""The flights MCP: small tools over Google Flights and promociones-aereas.com.ar, with short answers for the agent.

Flight tools run on letsfly's own core (params, search, airports) and only shape its output: one row per
option. Runs over stdio: nothing may print, stdout belongs to the protocol.
"""
from typing import Annotated, Literal

from letsfly import airports, params, promos, search
from letsfly.mcp import Range, serve, tool
from letsfly.tfs import booking_link

Airports = Annotated[str, "IATA code, or several separated by commas (EZE,AEP)"]
Day = Annotated[str, "Date YYYY-MM-DD"]
Stops = Literal["ANY", "NON_STOP", "ONE_STOP", "TWO_PLUS_STOPS"]


def _flight(leg):
    return leg.flight_number if leg.flight_number.startswith(leg.airline) else f"{leg.airline}{leg.flight_number}"


def _route(legs):
    """EZE-GRU-NRT, with an airport change shown as AEP/EZE where a leg leaves from another airport."""
    stops = [legs[0].origin]
    for leg, after in zip(legs, legs[1:] + [None]):
        stops.append(leg.destination + (f"/{after.origin}" if after and after.origin != leg.destination else ""))
    return "-".join(stops)


def _direction(itinerary):
    legs = itinerary.legs
    operated = {_flight(leg): leg.operator for leg in legs
                if leg.operator and not leg.operator.lower().startswith(leg.airline_name.lower())}
    return {
        "route": _route(legs),
        "flights": " ".join(_flight(leg) for leg in legs),
        "depart": legs[0].departs.strftime("%Y-%m-%d %H:%M"),
        "arrive": legs[-1].arrives.strftime("%Y-%m-%d %H:%M"),
        "hours": round(itinerary.minutes / 60, 1),
        "layovers": [{"airport": x.airport, "minutes": x.minutes} | ({"overnight": True} if x.overnight else {})
                     | ({"change_of_airport": True} if x.change_of_airport else {}) for x in itinerary.layovers],
        "operated_by": operated or None,
    }


@tool()
def search_flights(
    origin: Airports,
    destination: Airports,
    date: Day,
    return_date: Annotated[str | None, "Return date YYYY-MM-DD; without it, one way"] = None,
    max_stops: Stops = "ANY",
    airlines: Annotated[list[str] | None, "Only these airlines, IATA codes (AR, DM)"] = None,
    departure_window: Annotated[str | None, "Departure window in hours, for example 6-20"] = None,
    adults: Annotated[int, Range(1, 9)] = 1,
    cabin: Literal["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"] = "ECONOMY",
    currency: str = "USD",
    limit: Annotated[int, Range(1, 20)] = 5,
    all_links: Annotated[bool, "Booking link on every row; by default only the first 2 rows carry one"] = False,
) -> dict:
    """Flights on one date: first the cheapest option of each distinct outbound, then the rest, cheapest first. The price is the fare for all adults and, on a round trip, the
    total Google quotes for that outbound and return pair. bags is what the fare includes: carry_on and
    checked counts, null where Google does not say. self_transfer means the itinerary glues separate
    tickets (a missed connection is the traveller's problem). Each direction carries layovers (airport,
    minutes, overnight: true when the layover spans the night, change_of_airport: true when the next leg
    leaves from another airport, also shown in route as AEP/EZE) and operated_by: flight -> the carrier
    that flies it when it is another company than the airline selling it, or null when there is none."""
    try:
        query = params.flight_search(origin, destination, date, return_date, max_stops, airlines, departure_window,
                                     adults, cabin, currency)
        found, lost = search.flights(query)
    except Exception as e:  # noqa: BLE001 - the agent gets the error instead of an exception
        return {"error": f"{type(e).__name__}: {e}"[:400]}

    rows = []
    for index, trip in enumerate(_varied(sorted(found, key=lambda f: _priced(f).price or float("inf")))[:limit]):
        outbound, back = trip if isinstance(trip, tuple) else (trip, None)
        fare = _priced(trip)
        names = [leg.airline_name for direction in (outbound, back) if direction for leg in direction.legs]
        row = {"price": fare.price, "airlines": ", ".join(dict.fromkeys(names)),
               "bags": {"carry_on": fare.carry_on, "checked": fare.checked}, "out": _direction(outbound)}
        if back and back.price is None and outbound.price is not None:
            row["price_estimated"] = True
        if back:
            row["back"] = _direction(back)
        if outbound.self_transfer or (back and back.self_transfer):
            row["self_transfer"] = True
        if all_links or index < 2:
            row["link"] = booking_link([outbound, back] if back else [outbound], query.seat, query.adults, currency)
        rows.append(row)
    out = {"currency": currency, "count": len(rows), "flights": rows}
    if lost:
        out["note"] = f"The return page failed to load for {lost} of the outbounds, so their round trips are missing"
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
) -> dict:
    """Lowest price per departure date in a range, cheapest first. missing_dates lists the dates whose page
    failed to load, so they are unknown rather than without flights."""
    try:
        query, days = params.date_search(origin, destination, start_date, end_date, trip_days, adults, max_stops,
                                         currency)
        found, missing = search.dates(query, days, trip_days)
    except Exception as e:  # noqa: BLE001 - the agent gets the error instead of an exception
        return {"error": f"{type(e).__name__}: {e}"[:400]}
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
    query: Annotated[str, "IATA or ICAO code, city, airport name or country"],
    limit: Annotated[int, Range(1, 20)] = 5,
) -> dict:
    """Finds airports by code, city, name or country: code, name, city and country of each, best match first."""
    return {"airports": airports.search(query, limit)}


@tool("promociones-aereas")
def promociones_aereas(
    query: Annotated[str, "What to look for: destination, airline or origin (punta cana, paquetes brasil). Empty: the latest posts"] = "",
    type: Literal["any", "package", "flight", "hotel", "car", "activity"] = "any",
    page: Annotated[int, Range(1, 10), "Feed page, 15 posts per page, newest first"] = 1,
    limit: Annotated[int, Range(1, 15), "How many deals to return, each with its details (about 20 s per deal)"] = 5,
    details: Annotated[bool, "By default every deal nests its details: dates with links, payment terms, airline, baggage, "
                             "featured hotels and when the blog saw the price. false only for a quick scan of many titles"] = True,
    month: Annotated[str | None, "Only departure dates in that month, YYYY-MM"] = None,
    stay_days: Annotated[int | None, Range(1, 60), "Only dates with that length of stay in days, ±1 (each date carries days to tell the exact ones)"] = None,
) -> dict:
    """Deals published on promociones-aereas.com.ar (flights, packages, hotels), newest first, each with
    its details nested. price_from is the title's "a partir de": the lowest price the blog saw when it
    wrote the post, not a price per date. Detail dates carry no price; for each one, link_templates[link]
    with {out} and {back} filled in gives the Despegar link. For the real price of a flight on those
    dates, use search_flights."""
    return promos.search(query, type, page, limit, details, month, stay_days)


if __name__ == "__main__":
    serve()
