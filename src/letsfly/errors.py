"""The errors the tools return: a class per kind, each with the parameter at fault and a hint when there is one.

mcp.tool turns whatever a tool raises into {"error": payload(e)}, so the agent gets the same object from all 4
tools: type, message, param, hint and cause, the exception it was raised from. The kinds of the search itself
live next to their code (models.ParseError and ValidationError, client.SearchClientError and its family) and
derive from FlightsError; the rest are here.
"""
import sys
import traceback


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


def payload(e: Exception) -> dict:
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


def result(e: Exception) -> dict:
    """What a tool returns instead of raising. An internal error also prints its traceback on stderr, which the
    MCP protocol leaves free, so the bug can be traced."""
    out = {"error": payload(e)}
    if out["error"]["type"] == "internal":
        traceback.print_exception(e, file=sys.stderr)
    return out
