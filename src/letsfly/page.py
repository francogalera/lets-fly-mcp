"""Reads the results Google inlines in its search page, the AF_initDataCallback blob keyed ds:1."""
import json
import re

from letsfly import client

BLOB = re.compile(r"AF_initDataCallback\((\{.*?\})\);", re.S)
KEY = re.compile(r"key:\s*'([^']+)'")
DATA = re.compile(r"data:(.*?), sideChannel", re.S)
# About one page in sixty arrives without ds:1 and the same request works seconds later
ATTEMPTS = 3
BACKOFF = (0.5, 1.5)
# Google's own error comes in place of the results: a ds:1 blob whose data is an ErrorResponse with a gRPC status,
# closed by errorHasStatus instead of sideChannel. Status 3 (invalid argument) came for dates over everywhere, dates
# past its horizon and airline codes it does not know; status 13 (internal) came once on a page that loaded seconds later
ERROR_STATUS = re.compile(r'data:\[(\d+),null,\[\["type\.googleapis\.com/travel\.frontend\.flights\.ErrorResponse"')
INVALID_ARGUMENT = 3
REJECTED = ("Google rejected the search as invalid, as it does for a date already over, a date past its horizon of "
            "about 11 months, and an airline code it does not know")


def ds1_blob(text: str) -> str | None:
    """The raw {key: 'ds:1', ... sideChannel: {}} text of the first ds:1 blob that carries data."""
    for match in BLOB.finditer(text):
        blob = match.group(1)
        key = KEY.search(blob)
        if key and key.group(1) == "ds:1" and DATA.search(blob):
            return blob
    return None


def ds1(text: str):
    """The ds:1 data as Python lists, or None when the page has none or it is not valid JSON."""
    blob = ds1_blob(text)
    if blob is None:
        return None
    try:
        return json.loads(DATA.search(blob).group(1))
    except ValueError:
        return None


def error_status(text: str) -> int | None:
    """The status of the ErrorResponse Google sent as its ds:1, or None when the page has none."""
    for match in BLOB.finditer(text):
        blob = match.group(1)
        key = KEY.search(blob)
        if key and key.group(1) == "ds:1" and (status := ERROR_STATUS.search(blob)):
            return int(status.group(1))
    return None


def fetch_ds1(url: str):
    """Fetch url and return its ds:1 data, trying the page up to 3 times; None if it never came. Google refusing the
    request as invalid raises SearchRejected at once, since the same request gets the same answer; its other errors
    are tried again, and raise SearchUnreadable naming their status if they last."""
    statuses = []
    for attempt in range(ATTEMPTS):
        text = client.fetch(url)
        data = ds1(text)
        if data is not None:
            return data
        if (status := error_status(text)) == INVALID_ARGUMENT:
            raise client.SearchRejected(REJECTED, hint="Check the dates and the airline codes: retrying the same "
                                                       "search gets the same answer")
        if status is not None:
            statuses.append(status)
        if attempt + 1 < ATTEMPTS:
            client.sleep(BACKOFF[attempt])
    if statuses:
        codes = ", ".join(str(status) for status in sorted(set(statuses)))
        raise client.SearchUnreadable(f"Google answered with its own error, status {codes}, on {len(statuses)} of "
                                      f"{ATTEMPTS} tries", hint="Retry in a minute")
    return None
