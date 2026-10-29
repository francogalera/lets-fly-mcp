"""Records real Google Flights and blog pages, and replays the 4 tools against those recordings.

There are three seams: letsfly.client.fetch (the only way out to Google), letsfly.models.today (the
clock date validation reads) and letsfly.promos._get (the blog). For Google, each URL keeps its raw
ds:1 blob and the aria-label of every row; for the blog, the text of each request.
    python -I harness.py record    runs against the network and prints OUTPUT per scenario and FIXTURES
    python -I harness.py replay    runs offline on /tmp/t/fixtures.json.gz and prints OUTPUT
"""
import base64
import gzip
import html
import json
import re
import sys
import threading
from datetime import date

from letsfly import client, models, page, promos, tools

OW = [("AEP", "BRC", "2026-12-10"), ("EZE", "SCL", "2026-12-10"), ("EZE", "SCL", "2026-11-01"),
      ("EZE", "NRT", "2027-07-08"), ("EZE,AEP", "NRT,HND", "2027-07-08"), ("LGA", "MCO", "2026-12-10"),
      ("COR", "MIA", "2026-12-10"), ("MDZ", "MIA", "2026-12-10"), ("ROS", "MAD", "2026-12-10"),
      ("BRC", "SCL", "2026-12-10"), ("COR", "MAD", "2026-12-10"), ("LHR", "BCN", "2026-12-10"),
      ("JFK", "BOS", "2026-12-10"), ("DFW", "TYR", "2026-12-10"), ("DAL", "HOU", "2026-12-10")]
RT = [("EZE", "MIA", "2026-12-10", "2026-12-20"), ("LGA", "MCO", "2026-12-10", "2026-12-15"),
      ("EZE", "SCL", "2026-12-10", "2026-12-17"), ("EZE", "NRT", "2027-07-08", "2027-07-22")]
SCENARIOS = (
    [(f"ow {o}-{d} {day}", "search_flights", {"origin": o, "destination": d, "date": day, "limit": 20})
     for o, d, day in OW]
    + [(f"rt {o}-{d} {a}/{b}", "search_flights",
        {"origin": o, "destination": d, "date": a, "return_date": b, "limit": 20}) for o, d, a, b in RT]
    + [("dates EZE-MIA one way", "cheapest_dates",
        {"origin": "EZE", "destination": "MIA", "start_date": "2026-12-01", "end_date": "2026-12-07", "limit": 60}),
       ("dates EZE,AEP-NRT,HND 14 days", "cheapest_dates",
        {"origin": "EZE,AEP", "destination": "NRT,HND", "start_date": "2027-07-01", "end_date": "2027-07-07",
         "trip_days": 14, "limit": 60}),
       ("dates EZE-KIX no fares", "cheapest_dates",
        {"origin": "EZE", "destination": "KIX", "start_date": "2027-07-01", "end_date": "2027-07-03",
         "trip_days": 14}),
       ("dates 121-day range", "cheapest_dates",
        {"origin": "EZE", "destination": "MIA", "start_date": "2026-12-01", "end_date": "2027-03-31"}),
       ("ow invalid airport", "search_flights", {"origin": "XXX", "destination": "MIA", "date": "2026-12-10"})]
    + [(f"airports {q}", "find_airports", {"query": q}) for q in ("KIX", "Cartagena", "CTG", "Ezeiza", "SBGR")]
    + [("promos japon 2027-07", "promociones-aereas", {"query": "japon", "month": "2027-07", "limit": 2}),
       ("promos latest without details", "promociones-aereas", {"details": False, "limit": 5})]
)
REAL = (client.fetch, client.sleep, promos._get, models.today)


def restore():
    """Puts back the real network, blog and clock after replay or record."""
    client.fetch, client.sleep, promos._get, models.today = REAL


FUNCS = {"search_flights": tools.search_flights, "cheapest_dates": tools.cheapest_dates,
         "find_airports": tools.find_airports, "promociones-aereas": tools.promociones_aereas}


def run_all():
    outs = {}
    for name, tool, args in SCENARIOS:
        try:
            outs[name] = FUNCS[tool](**args)
        except Exception as e:  # noqa: BLE001
            outs[name] = {"exception": f"{type(e).__name__}: {e}"[:300]}
    return json.loads(json.dumps(outs, ensure_ascii=False))


def replay(fx):
    """Makes letsfly answer from the recordings in fx, offline."""
    def fetch(url):
        if url not in fx["pages"]:
            raise RuntimeError(f"no recording for {url}")
        blob = fx["pages"][url]["blob"]
        return f"<script>AF_initDataCallback({blob});</script>" if blob else "<html></html>"

    def pa_get(url):
        if url not in fx["blog"]:
            raise RuntimeError(f"no recording for {url}")
        return fx["blog"][url]

    client.fetch, client.sleep, promos._get = fetch, lambda s: None, pa_get
    models.today = lambda: date.fromisoformat(fx["today"])


def record():
    fx, lock = {"today": models.today().isoformat(), "pages": {}, "blog": {}}, threading.Lock()
    real_fetch, real_pa_get = client.fetch, promos._get

    def fetch(url):
        text = real_fetch(url)
        labels = [html.unescape(x) for x in re.findall(r'aria-label="(From [^"]*)"', text)]
        with lock:
            fx["pages"][url] = {"status": 200, "blob": page.ds1_blob(text), "labels": labels}
        return text

    def pa_get(url):
        fx["blog"][url] = real_pa_get(url)
        return fx["blog"][url]

    client.fetch, promos._get = fetch, pa_get
    models.today = lambda: date.fromisoformat(fx["today"])
    return fx


def load(path):
    with gzip.open(path) as f:
        return json.loads(f.read())


if __name__ == "__main__":
    if sys.argv[1] == "record":
        fx = record()
    else:
        replay(load("/tmp/t/fixtures.json.gz"))
    for name, out in run_all().items():
        print("OUTPUT " + json.dumps({"name": name, "output": out}, ensure_ascii=False, sort_keys=True), flush=True)
    if sys.argv[1] == "record":
        data = gzip.compress(json.dumps(fx, sort_keys=True).encode(), 9, mtime=0)
        print("FIXTURES " + base64.b64encode(data).decode())
