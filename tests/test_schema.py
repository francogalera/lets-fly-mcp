"""The outputSchema of each tool: a description on every field, and the output of the 4 tools and every error fit it.
A client that checks results, as MCP asks, rejects a result that breaks the schema."""
import json
import typing
import unittest
from unittest import mock

from fake_pages import days, leg, page, reference, row
import fake_metasearch
from letsfly import client, errors, mcp, metasearch, models, promos, tools
from test_decode import BACK, GLUED, NIGHT, NOW, REGIONAL
from test_mcp import converse

TOOLS = ("search_flights", "cheapest_dates", "find_airports", "promociones-aereas", "metasearch_flights",
         "metasearch_dates")
FEED = ("<rss><channel><item><title>Paquetes a Cancún desde Buenos Aires por U$D 1.234 por persona en base doble</title>"
        "<link>https://promociones-aereas.com.ar/cancun-schema-pa-p.html</link>"
        "<pubDate>Sat, 31 Oct 2026 16:30:00 +0000</pubDate></item>"
        "<item><title>Hotel</title><link>https://promociones-aereas.com.ar/alojamientos/schema.html</link></item>"
        "</channel></rss>")
POST = ("<p>Los precios se encontraban disponibles cuando se armó el post el 31/10/2026 a las 1:30</p>"
        '<div id="post-content-container"><p>Paquete con vuelo y hotel en base doble, con desayuno incluido.</p>'
        '<div class="bg-blue rounded-lg">Precio final</div>'
        '<a href="https://pkg.test/p?hotel_name=H&meal_plans=AI&d=2027-03-01,2027-03-08">Ver este Paquete</a>'
        '<a href="https://pkg.test/o?base=triple">Ver opciones en base triple</a>'
        "<ul><li><strong>Aerolínea:</strong> Aerolíneas Argentinas</li></ul>"
        "<table class='promos-tablas'><tr><td style='display:none;'>2027-03-01</td>"
        "<td style='display:none;'>2027-03-08</td><td class='dias'>7</td><td class='escala'>Directo</td>"
        "<td><a href='https://search.test/r?to=CUN&x=1'>ver</a></td></tr></table>También te puede interesar</div>")


def fields(schema, path=""):
    """Every property of schema, nested ones included, as (path, its schema)."""
    for name, sub in schema.get("properties", {}).items():
        yield f"{path}.{name}".lstrip("."), sub
        yield from fields(sub, f"{path}.{name}")
    for branch in schema.get("anyOf", []):
        yield from fields(branch, path)
    if "items" in schema:
        yield from fields(schema["items"], f"{path}[]")
    if isinstance(schema.get("additionalProperties"), dict):
        yield from fields(schema["additionalProperties"], f"{path}{{}}")


def kinds(cls):
    return [cls] + [sub for child in cls.__subclasses__() for sub in kinds(child)]


class OutputSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        listed = converse({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0]["result"]["tools"]
        cls.schemas = {t["name"]: t["outputSchema"] for t in listed if t["name"] in TOOLS}

    def setUp(self):
        self.real = client.fetch, client.sleep, models.now
        client.sleep, models.now = lambda seconds: None, lambda: NOW

    def tearDown(self):
        client.fetch, client.sleep, models.now = self.real

    def fits(self, tool, out):
        self.assertEqual(mcp.problems(json.loads(json.dumps(out)), self.schemas[tool]), [])

    def test_every_field_has_a_description(self):
        self.assertEqual(sorted(self.schemas), sorted(TOOLS))
        paths = {name: dict(fields(schema)) for name, schema in self.schemas.items()}
        self.assertIn("flights[].out.layovers[].overnight", paths["search_flights"])
        self.assertIn("price_reference.history.weekly", paths["search_flights"])
        self.assertIn("deals[].details.dates[].to", paths["promociones-aereas"])
        self.assertIn("error.hint", paths["find_airports"])
        for name, found in paths.items():
            for path, schema in found.items():
                with self.subTest(f"{name} {path}"):
                    self.assertTrue(schema.get("description"))

    def test_flights_fit(self):
        client.fetch = lambda url: page([REGIONAL, NIGHT, GLUED])
        self.fits("search_flights", tools.search_flights("EZE", "BOS", "2026-12-10", all_links=True))
        outbounds = iter([[REGIONAL, NIGHT, GLUED]])
        client.fetch = lambda url: page(next(outbounds, [BACK]))
        out = tools.search_flights("EZE", "BOS", "2026-12-10", "2026-12-20")
        self.assertEqual(len(out["flights"]), 3)
        self.fits("search_flights", out)

    def test_reference_fits(self):
        full = reference(2, 48, 62, 40, 145, [[1_788_472_800_000, 44], [1_788_559_200_000, 45]])
        partial = [2, [None, 48], None, None, None, None, 1, None, None, None, None]
        for block in (full, reference(3, 1111, 1036, 980, 1200), partial):
            client.fetch = lambda url, block=block: page([REGIONAL], block)
            out = tools.search_flights("EZE", "BOS", "2026-12-10")
            self.assertIn("price_reference", out)
            self.fits("search_flights", out)

    def test_dates_fit(self):
        def fetch(url):
            if "2026-12-11" in days(url):
                raise TimeoutError("simulated")
            return page([REGIONAL, NIGHT])
        client.fetch = fetch
        out = tools.cheapest_dates("EZE", "BOS", "2026-12-10", "2026-12-12", trip_days=7)
        self.assertEqual((out["count"], out["missing_dates"]), (2, ["2026-12-11"]))
        self.fits("cheapest_dates", out)
        client.fetch = lambda url: page([])
        out = tools.cheapest_dates("EZE", "BOS", "2026-12-10", "2026-12-12")
        self.assertIn("note", out)
        self.fits("cheapest_dates", out)

    def test_metasearch_flights_fit(self):
        session = fake_metasearch.Session(graph={"statsLast90": [{"avg_price": 900, "created": "2026-09-01"}]})
        with mock.patch.object(metasearch, "_session", lambda: session), mock.patch.object(metasearch, "sleep", lambda s: None):
            metasearch._cache.clear()
            self.fits("metasearch_flights", tools.metasearch_flights("EZE", "MAD", "2026-12-10", "2026-12-20"))
            session.result = fake_metasearch.ONE_WAY
            self.fits("metasearch_flights", tools.metasearch_flights("AEP", "MDZ", "2026-12-10", limit=1))
            metasearch._cache.clear()

    def test_metasearch_dates_fit(self):
        session = fake_metasearch.Session(graph={"calendarFlightPrices": [{"price": 550, "calendardate": "2026-12-11",
                                                                           "isLowprice": True}]})
        with mock.patch.object(metasearch, "_session", lambda: session):
            self.fits("metasearch_dates", tools.metasearch_dates("BUE", "MAD", month="2026-12"))
            self.fits("metasearch_dates", tools.metasearch_dates("BUE", "MAD"))

    def test_airports_fit(self):
        self.fits("find_airports", tools.find_airports("Londres"))

    def test_deals_fit(self):
        with mock.patch.object(promos, "_get", lambda url: FEED if "feed" in url else POST):
            out = tools.promociones_aereas("cancun")
            scan = tools.promociones_aereas(details=False)
        package, hotel = out["deals"]
        self.assertEqual(sorted(package["details"]),
                         ["badges", "dates", "expired", "featured", "fields", "notes", "other_occupancy", "prices_seen_at"])
        self.assertEqual(hotel["details"]["error"]["type"], "blog_forbidden")
        self.fits("promociones-aereas", out)
        self.fits("promociones-aereas", scan)

    def test_every_error_fits_every_tool(self):
        found = kinds(errors.FlightsError)
        self.assertEqual({cls.kind for cls in found}, set(typing.get_args(errors.Kind)))
        for error in [cls("message", "param", "hint") for cls in found] + [KeyError("bug")]:
            for tool in TOOLS:
                with self.subTest(f"{tool} {error!r}"):
                    self.fits(tool, {"error": errors.payload(error)})

    def test_codeshares_fit_even_with_flights_empty(self):
        partner = row([leg("BA175", "LHR", "JFK", "2026-12-10 09:35", "2026-12-10 12:35", "British Airways",
                           sold_as=("AA6936",))], 1010, 480)
        client.fetch = lambda url: page([partner])
        out = tools.search_flights("LHR", "JFK", "2026-12-10", airlines=["AA"])
        self.assertEqual((out["flights"], out["codeshares"][0]["out"]["sold_as"]), ([], {"BA175": "AA6936"}))
        self.fits("search_flights", out)
        rows = self.schemas["search_flights"]["anyOf"][0]["properties"]["flights"]["items"]
        self.assertEqual(mcp.problems(json.loads(json.dumps(out["codeshares"][0])), rows), [])

    def test_a_result_that_breaks_the_schema_is_caught(self):
        client.fetch = lambda url: page([REGIONAL])
        out = tools.search_flights("EZE", "BOS", "2026-12-10")
        out["flights"][0]["price"] = "980"
        del out["flights"][0]["out"]["route"]
        self.assertEqual([path for path, _ in mcp.problems(out, self.schemas["search_flights"])],
                         ["flights[0].price", "flights[0].out.route"])


if __name__ == "__main__":
    unittest.main()
