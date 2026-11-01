"""promociones-aereas.com.ar: the deals it publishes (RSS search feed) and the details of each post (its HTML)."""
import html
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime

from letsfly import errors, links

SITE = "https://promociones-aereas.com.ar"
USER_AGENT = "flights-mcp/1 (personal use)"
GAP = 5
MAX_DATES = 60
KINDS = {"pa": "package", "rt": "flight", "ow": "flight", "ho": "hotel", "au": "car", "ac": "activity"}
_last = [0.0]
_lock = threading.Lock()
_cache: dict = {}
AR_TIME = timezone(timedelta(hours=-3))  # Argentina has no daylight saving time
BOILERPLATE = re.compile(r"^(Les dejo|Dejamos opciones|¡Recib|Descarg|Subscrib|Continuar a la nota)", re.I)
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
# Links decode only references that end in ";" (&amp;, &#038;): the blog writes some with a raw &currency=, which
# html.unescape reads as the legacy entity &curren (¤) while a browser leaves it alone inside an attribute
REFERENCE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);")


def _get(url):
    """One request to the blog, at least GAP seconds after the previous one, retried once if the connection drops."""
    with _lock:
        for attempt in (1, 2):
            wait = _last[0] + GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                raise errors.BlogError(f"The blog answered HTTP {e.code} for {url}") from e
            except (ConnectionError, TimeoutError, urllib.error.URLError) as e:
                if attempt == 2:
                    raise errors.BlogError(f"Could not reach the blog for {url}, twice") from e
            finally:
                _last[0] = time.monotonic()


def _plain(fragment):
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", EMOJI.sub("", text)).strip()


def _href(raw):
    return REFERENCE.sub(lambda m: html.unescape(m.group()), raw)


def _price(title):
    m = re.search(r"(U\$D|US\$|USD|AR\$|\$)\s*([\d.]+(?:,\d+)?)", title)
    if not m:
        return None, None
    amount = float(m.group(2).replace(".", "").replace(",", "."))
    return amount, "ARS" if m.group(1) in ("AR$", "$") else "USD"


def _item(item):
    title = _plain(item.findtext("title") or "")
    url = item.findtext("link") or ""
    kind = re.search(r"-([a-z]{2})-p(?:-\d+)?\.html$", url)
    price, currency = _price(title)
    basis = re.search(r"(por persona en base \w+|por noche|la noche)", title, re.I)
    origin = re.search(r"[Dd]esde (?!U\$D|US\$|USD|AR\$)((?:[A-ZÁÉÍÓÚ][\wáéíóúñ.]*\s?){1,3})", title)
    published = parsedate_to_datetime(item.findtext("pubDate")) if item.findtext("pubDate") else None
    return {
        "published": published.astimezone(AR_TIME).strftime("%Y-%m-%d %H:%M") if published else None,
        "type": KINDS.get(kind.group(1), "note") if kind else "note",
        "title": title[:200],
        "price_from": price, "currency": currency,
        **({"basis": basis.group(1).lower()} if basis else {}),
        **({"origin": origin.group(1).strip().rstrip(".")} if origin else {}),
        "url": url,
    }


def _details(url, month, stay_days):
    if "/alojamientos/" in url:
        raise errors.BlogForbidden("The site's robots.txt asks not to read /alojamientos/")
    if url not in _cache:
        _cache[url] = _get(url)
    page = _cache[url]
    out = {}
    snap = re.search(r"Los precios se encontraban disponibles cuando se armó el post el ([\d/]+) a las ([\d:]+)", page)
    if snap:
        out["prices_seen_at"] = f"{snap.group(1)} {snap.group(2)}"
    expired = re.search(r"const isExpiredPost\s*=\s*'([^']*)'", page)
    out["expired"] = bool(expired and expired.group(1))
    start = page.find('id="post-content-container"')
    body = page[start:page.find("También te puede interesar", start)] if start >= 0 else ""
    first_table = body.find("<table")
    intro = body[:first_table] if first_table >= 0 else body
    notes = [_plain(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", intro, re.S)]
    out["notes"] = [n for n in notes if len(n) > 25 and not BOILERPLATE.match(n)][:6]
    badges = [_plain(b) for b in re.findall(r'<div class="bg-[^"]*rounded-lg[^"]*">([^<]{2,60})</div>', intro)]
    if badges:
        out["badges"] = list(dict.fromkeys(badges))
    for a in re.findall(r"<a\s[^>]*>.*?</a>", intro, re.S):
        href = re.search(r"href=[\"']([^\"']+)[\"']", a)
        if not href:
            continue
        link = _href(href.group(1))
        if "Ver este Paquete" in a:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
            when = re.findall(r"\d{4}-\d{2}-\d{2}", link)
            out.setdefault("featured", []).append({
                "hotel": q.get("hotel_name", [""])[0], "out": when[0] if when else None,
                "back": when[1] if len(when) > 1 else None, "meal_plan": q.get("meal_plans", [""])[0],
                "link": links.short(link)})
        occupancy = re.search(r"Ver opciones en base (\w+)", a)
        if occupancy:
            out.setdefault("other_occupancy", {})[occupancy.group(1).lower()] = links.short(link)
    for li in re.findall(r"<li[^>]*>\s*<strong>([^<:]+):?</strong>(.*?)</li>", body, re.S):
        out.setdefault("fields", {})[_plain(li[0])] = _plain(li[1])
    dates = {}
    for table in re.findall(r"<table[^>]*promos-tablas.*?</table>", body, re.S):
        for row in re.findall(r"<tr.*?</tr>", table, re.S):
            iso = re.findall(r"<td style='display:none;'>(\d{4}-\d{2}-\d{2})</td>", row)
            link = re.search(r"href='(https://[^']+)'", row)
            if len(iso) < 2 or not link or "currency=ARS" in link.group(1):
                continue
            go, back = iso[0], iso[1]
            if month and not go.startswith(month):
                continue
            days = re.search(r"class='dias[^']*'>(\d+)<", row)
            if stay_days and not (days and abs(int(days.group(1)) - stay_days) <= 1):
                continue
            stops = re.search(r"class='escala[^']*'>([^<]+)<", row)
            href = _href(link.group(1))
            to = _destination(href)
            # A package lists its dates once per room occupancy, a table each: the first one, the occupancy the
            # title's price is based on, keeps the date
            dates.setdefault((go, back, to), {
                "out": go, "back": back, **({"to": to} if to else {}), "days": int(days.group(1)) if days else None,
                "direct": bool(stops and "directo" in stops.group(1).lower()), "link": href})
    # With stay_days, exact stays go first, so a cut drops the ±1 ones
    exact_first = (lambda d: abs((d["days"] or 0) - stay_days)) if stay_days else (lambda d: 0)
    dates = sorted(dates.values(), key=lambda d: (exact_first(d), d["out"], d["back"]))
    # Some posts list many destinations and hundreds of dates: cut them and report the total
    if len(dates) > MAX_DATES:
        out["dates_total"] = len(dates)
    out["dates"] = [d | {"link": links.short(d["link"])} for d in dates[:MAX_DATES]]
    return out


def _destination(href):
    """The airport a flight link searches to: Despegar names it in the path, Travel Services in to=."""
    found = (re.search(r"/flights/results/(?:roundtrip|oneway)/[A-Z]{3}/([A-Z]{3})/", href)
             or re.search(r"[?&]to=([A-Z]{3})(?:&|$)", href))
    return found.group(1) if found else None


def search(query, type, page, limit, details, month, stay_days):
    path = f"/page/{page}/" if page > 1 else "/"
    url = f"{SITE}{path}?" + urllib.parse.urlencode({"s": query, "feed": "rss2"}) if query else f"{SITE}/feed" + (f"?paged={page}" if page > 1 else "")
    text = _get(url)
    try:
        feed = ET.fromstring(text)
    except ET.ParseError as e:
        raise errors.BlogUnreadable(f"The blog's feed is not valid XML ({url})") from e
    items = [_item(i) for i in feed.findall("./channel/item")]
    items = [i for i in items if type == "any" or i["type"] == type][:limit]
    if details:
        for item in items:
            try:
                item["details"] = _details(item["url"], month, stay_days)
                _fix_seen_at(item)
            except Exception as e:  # noqa: BLE001 - one deal that fails keeps the others
                item["details"] = errors.result(e)
    return {"source": SITE, "count": len(items), "deals": items}


def _fix_seen_at(item):
    """The blog writes the time on a 12-hour clock without am/pm ("1:30" in a post published at 13:30)."""
    seen = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4}) (\d{1,2}):(\d{2})", item["details"].get("prices_seen_at", ""))
    if not seen:
        return
    d, m, y, hour, minute = (int(x) for x in seen.groups())
    published = item.get("published") or ""
    if published.startswith(f"{y:04d}-{m:02d}-{d:02d}") and hour < 12 and hour + 12 <= int(published[11:13]) + 1:
        hour += 12
    item["details"]["prices_seen_at"] = f"{y:04d}-{m:02d}-{d:02d} {hour:02d}:{minute:02d}"
