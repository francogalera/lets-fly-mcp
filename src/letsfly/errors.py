"""The error object every tool returns instead of raising. The search's own kinds live next to their code
(models, client); Kind lists them all because a client may check results against it."""
import sys
import traceback
from typing import Annotated, Literal, NotRequired, TypedDict

Kind = Literal["internal", "invalid_arguments", "unknown_tool", "unknown_airport", "city_code", "invalid_input",
               "invalid_request", "google", "google_timeout", "google_connection", "google_certificate", "google_http",
               "google_page", "blog_unavailable", "blog_forbidden", "blog_unreadable"]


class Error(TypedDict):
    type: Annotated[Kind, "What failed; internal is a bug in the tool"]
    message: Annotated[str, "What happened"]
    param: NotRequired[Annotated[str, "The tool parameter at fault"]]
    hint: NotRequired[Annotated[str, "How to retry; for city_code, the airport codes to search instead"]]
    cause: NotRequired[Annotated[str, "The exception underneath, class and message"]]


class Failure(TypedDict):
    error: Annotated[Error, "What the tool returns instead of its result when it fails"]


class FlightsError(Exception):
    kind = "internal"

    def __init__(self, message, param=None, hint=None):
        super().__init__(message)
        self.param, self.hint = param, hint


class UnknownAirport(FlightsError, ValueError):
    kind = "unknown_airport"


class CityCode(FlightsError, ValueError):
    """An IATA city code (BUE, LON): it names several airports, and Google wants one of them."""
    kind = "city_code"


class BlogError(FlightsError):
    kind = "blog_unavailable"


class BlogForbidden(BlogError):
    kind = "blog_forbidden"


class BlogUnreadable(BlogError):
    kind = "blog_unreadable"


class UnknownTool(FlightsError):
    kind = "unknown_tool"


class InvalidArguments(FlightsError):
    kind = "invalid_arguments"


def payload(e: Exception) -> Error:
    """The error object: an exception that is not a FlightsError is a bug, and keeps its class name as internal."""
    ours = isinstance(e, FlightsError)
    out = {"type": e.kind if ours else "internal", "message": (str(e) if ours else f"{type(e).__name__}: {e}")[:400]}
    if ours and e.param:
        out["param"] = e.param
    if ours and e.hint:
        out["hint"] = e.hint
    if e.__cause__ is not None:
        out["cause"] = f"{type(e.__cause__).__name__}: {e.__cause__}"[:200]
    return out


def result(e: Exception) -> Failure:
    """What a tool returns instead of raising. An internal error also prints its traceback on stderr, which the
    MCP protocol leaves free, so the bug can be traced."""
    out = {"error": payload(e)}
    if out["error"]["type"] == "internal":
        traceback.print_exception(e, file=sys.stderr)
    return out
