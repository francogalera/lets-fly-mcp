"""Checks what the tools receive and builds the Search, with fli's rules and error messages; each error names
the tool parameter at fault."""
import re
from datetime import datetime, timedelta

from letsfly import airports, models
from letsfly.models import ParseError, Search, ValidationError

STOPS = {"ANY": 0, "NON_STOP": 1, "ONE_STOP": 2, "TWO_PLUS_STOPS": 3}
SEATS = {"ECONOMY": 1, "PREMIUM_ECONOMY": 2, "BUSINESS": 3, "FIRST": 4}
# A range start further back than this moves to today; only a reversed range can start that far back
MAX_PAST_FROM_DAYS = 6


def flight_search(origin, destination, date, return_date, max_stops, airlines, window, adults, cabin, currency) -> Search:
    """The Search for search_flights; raises the errors of airports and of this module in fli's order."""
    origins, destinations = airports.resolve(origin, "origin"), airports.resolve(destination, "destination")
    codes = airline_codes(airlines)
    hours = departure_window(window)
    dates = [_segment_date(date, origins, destinations, "date")]
    if return_date:
        dates.append(_segment_date(return_date, destinations, origins, "return_date"))
        if dates[1] < dates[0]:
            raise ValidationError(f"input: Value error, Return date ({dates[1]}) cannot be before departure date "
                                  f"({dates[0]})", "return_date")
    return Search(origins, destinations, dates, STOPS[max_stops], SEATS[cabin], adults, codes, hours, parse_currency(currency))


def date_search(origin, destination, start_date, end_date, trip_days, adults, max_stops, currency):
    """The Search and the days to sweep for cheapest_dates, after fli's swap and clamp of the range."""
    origins, destinations = airports.resolve(origin, "origin"), airports.resolve(destination, "destination")
    first = _segment_date(start_date, origins, destinations, "start_date")
    dates = [first]
    if trip_days:
        dates.append(_segment_date(_shift(first, trip_days), destinations, origins, "trip_days"))
    start, end = _day(start_date), _day(end_date, "to_date", "end_date")
    if start > end:
        start, end = end, start
    if end < models.today() - timedelta(days=1):
        raise ValidationError("to_date: Value error, To date cannot be in the past", "end_date")
    if (models.today() - start).days > MAX_PAST_FROM_DAYS:
        start = min(models.today(), end)
    search = Search(origins, destinations, dates, STOPS[max_stops], 1, adults, currency=parse_currency(currency))
    return search, [start + timedelta(days=n) for n in range((end - start).days + 1)]


def airline_codes(codes):
    if not codes:
        return set()
    tokens = [token.strip().upper() for item in codes for token in re.split(r"[,\s]+", item) if token.strip()]
    if not tokens:
        raise ParseError(f"No valid airline codes found in: {codes!r}", "airlines")
    for code in tokens:
        if not re.fullmatch(r"[A-Z0-9]{2}", code):
            raise ParseError(f"Invalid airline code: '{code}'", "airlines")
    return set(tokens)


def departure_window(window):
    """(earliest, latest) departure hour from "6-20"; reversed hours are swapped."""
    if not window:
        return None
    try:
        parts = window.split("-")
        if len(parts) != 2:
            raise ValueError
        earliest, latest = int(parts[0].strip()), int(parts[1].strip())
        if not (0 <= earliest <= 23 and 0 <= latest <= 23):
            raise ValueError
    except ValueError:
        raise ParseError(f"Invalid time range format: '{window}'. Expected 'HH-HH' (e.g., '6-20')",
                         "departure_window") from None
    if latest == 0:
        raise ValidationError("latest_departure: Input should be greater than 0", "departure_window")
    return min(earliest, latest), max(earliest, latest)


def parse_currency(currency):
    if not currency:
        return None
    code = currency.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ParseError(f"Invalid currency code: '{currency}'. Expected a 3-letter ISO 4217 code.", "currency")
    return code


def _segment_date(text, origins, destinations, param):
    """The date of one direction, zero-padded; a typo raises ParseError, from the ValueError of strptime."""
    try:
        normalized = datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise ParseError(f"Invalid date: '{text}'. Expected YYYY-MM-DD", param) from e
    # Validation measures against yesterday in UTC: somewhere on Earth it is still that day
    if _day(normalized) < models.today() - timedelta(days=1):
        raise ValidationError("travel_date: Value error, Travel date cannot be in the past", param)
    if origins[0] == destinations[0]:
        raise ValidationError("input: Value error, Departure and arrival airports must be different", "destination")
    return normalized


def _shift(day, days):
    return (_day(day) + timedelta(days=days)).strftime("%Y-%m-%d")


def _day(text, field=None, param=None):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as e:
        if field is None:
            raise
        raise ValidationError(f"{field}: Value error, {e}", param) from e
