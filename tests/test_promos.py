"""The blog client: spacing between requests across threads, and one retry when a connection drops."""
import threading
import time
import unittest
from unittest import mock

from letsfly import errors, promos, tools


class Response:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.text.encode()


class Requests(unittest.TestCase):
    def setUp(self):
        self.gap, promos.GAP, promos._last[0] = promos.GAP, 0.3, 0.0

    def tearDown(self):
        promos.GAP = self.gap

    def test_concurrent_calls_keep_the_gap(self):
        starts = []

        def urlopen(request, timeout):
            starts.append(time.monotonic())
            return Response("ok")
        with mock.patch("urllib.request.urlopen", urlopen):
            threads = [threading.Thread(target=promos._get, args=(f"https://example.test/{i}",)) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        gaps = [b - a for a, b in zip(sorted(starts), sorted(starts)[1:])]
        self.assertTrue(all(g >= 0.29 for g in gaps), gaps)

    def test_a_dropped_connection_is_retried_once(self):
        answers = [ConnectionResetError("reset"), Response("second")]

        def urlopen(request, timeout):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(promos._get("https://example.test/feed"), "second")

    def test_a_second_drop_is_raised(self):
        def urlopen(request, timeout):
            raise ConnectionResetError("reset")
        with mock.patch("urllib.request.urlopen", urlopen), self.assertRaises(errors.BlogError) as caught:
            promos._get("https://example.test/feed")
        self.assertIsInstance(caught.exception.__cause__, ConnectionResetError)

    def test_blog_errors_are_typed(self):
        with mock.patch.object(promos, "_get", lambda url: "<html>not a feed"):
            self.assertEqual(tools.promociones_aereas()["error"]["type"], "blog_unreadable")
        feed = ("<rss><channel><item><title>Hotel</title><link>https://promociones-aereas.com.ar/alojamientos/x.html"
                "</link></item></channel></rss>")
        with mock.patch.object(promos, "_get", lambda url: feed):
            deal = tools.promociones_aereas()["deals"][0]
        self.assertEqual(deal["details"]["error"]["type"], "blog_forbidden")


class Details(unittest.TestCase):
    def test_links_keep_a_raw_currency_parameter(self):
        rows = "".join(
            f"<tr><td style='display:none;'>2026-11-{day}</td><td style='display:none;'>2026-12-{day}</td>"
            f"<td><a href='https://search.test/r?dates=2026-11-{day},2026-12-{day}{amp}currency=USD{amp}utm=1'>ver</a></td></tr>"
            for day, amp in (("24", "&"), ("25", "&amp;"), ("26", "&#038;")))
        page = ('<div id="post-content-container"><a href="https://pkg.test/p?hotel_name=H&currency=USD">Ver este Paquete</a>'
                f"<table class='promos-tablas'>{rows}</table>También te puede interesar</div>")
        with mock.patch.object(promos, "_get", lambda url: page):
            out = promos._details("https://promociones-aereas.com.ar/currency-test-pa-p.html", None, None)
        self.assertEqual([d["link"] for d in out["dates"]],
                         [f"https://search.test/r?dates=2026-11-{day},2026-12-{day}&currency=USD&utm=1" for day in (24, 25, 26)])
        self.assertEqual(out["featured"][0]["link"], "https://pkg.test/p?hotel_name=H&currency=USD")

    def test_every_date_carries_its_own_link(self):
        def table(link, dates):
            return "<table class='promos-tablas'>" + "".join(
                f"<tr><td style='display:none;'>{a}</td><td style='display:none;'>{b}</td>"
                f"<td><a href='{link.format(a=a, b=b)}'>ver</a></td></tr>" for a, b in dates) + "</table>"
        flights = ("https://www.despegar.com.ar/shop/flights/results/roundtrip/BUE/{to}/{{a}}/{{b}}/1/0/0?clt_n=r",
                   "https://search.travelservices.com.ar/flights/results?dates={{a}}%2C{{b}}&from=BUE&to={to}&type=RT")
        package = "https://www.despegar.com.ar/trip/start/FH/CIT_982/CIT_5963/{{a}}/{{b}}/CIT_5963/{{a}}/{{b}}/{people}?x=1"
        pairs = [("2027-03-01", "2027-03-11"), ("2027-03-02", "2027-03-12")]
        page = ('<div id="post-content-container">' + table(flights[0].format(to="MAD"), pairs)
                + table(flights[1].format(to="FLN"), pairs[:1]) + table(package.format(people=2), pairs)
                + table(package.format(people=3), pairs) + "</div>")
        with mock.patch.object(promos, "_get", lambda url: page):
            out = promos._details("https://promociones-aereas.com.ar/links-test-pa-p.html", None, None)
        self.assertNotIn("link_templates", out)
        got = [(d["out"], d.get("to"), d["link"].rsplit("/", 1)[-1]) for d in out["dates"]]
        self.assertEqual(sorted(got, key=str), sorted([
            ("2027-03-01", "MAD", "0?clt_n=r"), ("2027-03-02", "MAD", "0?clt_n=r"),
            ("2027-03-01", "FLN", "results?dates=2027-03-01%2C2027-03-11&from=BUE&to=FLN&type=RT"),
            ("2027-03-01", None, "2?x=1"), ("2027-03-02", None, "2?x=1")], key=str))


if __name__ == "__main__":
    unittest.main()
