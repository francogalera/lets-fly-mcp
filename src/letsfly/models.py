"""The shapes a search works with: what is asked (Search), what comes back (Itinerary) and the clock."""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone


class ParseError(ValueError):
    """An airport, airline, window or currency the user typed that cannot be read."""


class ValidationError(ValueError):
    """A request that reads fine but cannot be searched; the message starts with the field at fault."""


def today() -> date:
    return datetime.now(timezone.utc).date()


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
