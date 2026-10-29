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


def fetch_ds1(url: str):
    """Fetch url and return its ds:1 data, trying the page up to 3 times; None if it never came."""
    for attempt in range(ATTEMPTS):
        data = ds1(client.fetch(url))
        if data is not None:
            return data
        if attempt + 1 < ATTEMPTS:
            client.sleep(BACKOFF[attempt])
    return None
