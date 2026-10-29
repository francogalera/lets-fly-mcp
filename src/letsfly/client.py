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


class SearchClientError(FlightsError):
    kind = "google"


class SearchTimeoutError(SearchClientError):
    kind = "google_timeout"


class SearchConnectionError(SearchClientError):
    kind = "google_connection"


class SearchCertificateError(SearchConnectionError):
    kind = "google_certificate"


class SearchHTTPError(SearchClientError):
    kind = "google_http"


class SearchParseError(SearchClientError):
    """A page arrived but could not be read."""
    kind = "google_page"


def fetch(url: str) -> str:
    """GET a Google Flights page and return its HTML; retries twice, waiting 1 s and then 2 s."""
    for attempt in range(ATTEMPTS):
        try:
            response = _get(url)
            break
        except SearchCertificateError:
            raise
        except SearchClientError:
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
        return SearchCertificateError(f"TLS certificate verification failed for Google Flights ({host}).")
    if isinstance(e, curl.Timeout):
        return SearchTimeoutError(
            f"Timed out talking to Google Flights ({host}). The service may be slow or unreachable from your "
            "network: check your connection and try again.")
    if isinstance(e, curl.ConnectionError):
        return SearchConnectionError(
            f"Could not reach Google Flights ({host}). Check your internet connection or DNS and try again.")
    if isinstance(e, curl.HTTPError):
        status = getattr(getattr(e, "response", None), "status_code", None)
        suffix = f" (HTTP {status})" if status else ""
        return SearchHTTPError(
            f"Google Flights returned an error response{suffix}. The request may be malformed, rate-limited, or blocked.")
    return SearchClientError(f"GET request to Google Flights ({host}) failed: {e.__class__.__name__}")
