"""The shapes a search works with: what is asked (Search), what comes back (Itinerary) and the clock."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from letsfly.errors import FlightsError


class ParseError(FlightsError, ValueError):
    """An airline, window, currency or date the user typed that cannot be read."""
    kind = "invalid_input"


class ValidationError(FlightsError, ValueError):
    """A request that reads fine but cannot be searched; the message starts with the field at fault."""
    kind = "invalid_request"


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return now().date()


def earliest_day() -> date:
    """The earliest date still running somewhere: the last time zone, UTC-12, ends each day 12 hours after UTC. Google
    refuses a date that is over everywhere."""
    return (now() - timedelta(hours=12)).date()


@dataclass
class Leg:
    airline: str
    airline_name: str
    flight_number: str
    origin: str
    destination: str
    departs: datetime
    arrives: datetime
    operator: str | None
    sold_as: dict[str, str] = field(default_factory=dict)


@dataclass
class Layover:
    airport: str
    minutes: int
    overnight: bool
    change_of_airport: bool


@dataclass
class Itinerary:
    legs: list[Leg]
    price: float | None
    minutes: int
    layovers: list[Layover]
    self_transfer: bool
    carry_on: int | None
    checked: int | None
    price_other_seller: bool = False


@dataclass
class Search:
    origins: list[str]
    destinations: list[str]
    dates: list[str]
    stops: int = 0
    seat: int = 1
    adults: int = 1
    airlines: set[str] = field(default_factory=set)
    window: tuple[int, int] | None = None
    currency: str | None = None
    return_window: tuple[int, int] | None = None
