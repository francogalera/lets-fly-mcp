"""The only door to Google: one curl_cffi session per thread posing as Chrome, 10 requests per second, 3 tries."""
import threading
import time
from urllib.parse import urlparse

from letsfly.errors import FlightsError

RATE = 10
ATTEMPTS = 3
TIMEOUT = 60
# A pre-accepted consent cookie: EU/EEA IPs otherwise get Google's consent page, which has no results
SOCS = "CAISNQgQEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjQwMzE3LjA5X3AwGgJlbiADGgYIgLC_rwY"

sleep = time.sleep
_local = threading.local()
_next_start = [0.0]
_spacing = threading.Lock()


class SearchError(FlightsError):
    kind = "google"


class SearchTimeout(SearchError):
    kind = "google_timeout"


class SearchUnreachable(SearchError):
    kind = "google_connection"


class SearchCertificate(SearchUnreachable):
    kind = "google_certificate"


class SearchRefused(SearchError):
    kind = "google_http"


class SearchUnreadable(SearchError):
    """A page arrived but could not be read."""
    kind = "google_page"


class SearchRejected(SearchError):
    """Google answered and refused the request as invalid, so the same request gets the same answer."""
    kind = "google_rejected"


def fetch(url: str) -> str:
    """GET a Google Flights page and return its HTML; retries twice, waiting 1 s and then 2 s."""
    for attempt in range(ATTEMPTS):
        try:
            response = _get(url)
            break
        except SearchCertificate:
            raise
        except SearchError:
            if attempt + 1 == ATTEMPTS:
                raise
            sleep(2 ** attempt)
    return response.text


def _get(url):
    _wait_turn()
    try:
        response = _session().get(url, impersonate="chrome", allow_redirects=True, timeout=TIMEOUT)
        response.raise_for_status()
        return response
    except Exception as e:
        raise _wrap(url, e) from e


def _session():
    if not hasattr(_local, "session"):
        from curl_cffi import requests  # ~100 ms to import, only paid on the first request

        session = requests.Session()
        session.headers.update({"content-type": "application/x-www-form-urlencoded;charset=UTF-8"})
        session.cookies.set("SOCS", SOCS, domain=".google.com")
        _local.session = session
    return _local.session


def _wait_turn():
    """Spaces requests 1/RATE apart across every thread, so no second sees more than RATE of them."""
    with _spacing:
        now = time.monotonic()
        start = max(now, _next_start[0])
        _next_start[0] = start + 1 / RATE
    time.sleep(start - now)


def _wrap(url, e):
    from curl_cffi.requests import exceptions as curl

    host = urlparse(url).hostname or url
    if isinstance(e, curl.CertificateVerifyError) or (isinstance(e, curl.ConnectionError) and getattr(e, "code", None) == 60):
        return SearchCertificate(f"The TLS certificate of {host} did not verify")
    if isinstance(e, curl.Timeout):
        return SearchTimeout(f"{host} did not answer within {TIMEOUT} s; try again in a moment")
    if isinstance(e, curl.ConnectionError):
        return SearchUnreachable(f"No connection to {host}: the network, DNS or the proxy is down")
    if isinstance(e, curl.HTTPError):
        status = getattr(getattr(e, "response", None), "status_code", None)
        suffix = f" (HTTP {status})" if status else ""
        return SearchRefused(f"{host} answered with an error{suffix}: it may be limiting or blocking our requests")
    return SearchError(f"The request to {host} failed: {e.__class__.__name__}")
