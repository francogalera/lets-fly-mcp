"""Airports, city codes, typed errors and the overnight rule, through the tools and without recorded pages."""
import contextlib
import io
import unittest
from datetime import date, datetime

from letsfly import airports, client, decode, models, tools

# Searches use fixed dates, so the clock stays where they are still in the future
TODAY = date(2026, 11, 1)


class KnownCases(unittest.TestCase):
    def setUp(self):
        self.real = client.fetch, client.sleep, models.today
        client.sleep, models.today = lambda seconds: None, lambda: TODAY

    def tearDown(self):
        client.fetch, client.sleep, models.today = self.real

    def test_airports_by_city(self):
        self.assertEqual(tools.find_airports("Cartagena")["airports"][0]["code"], "CTG")
        self.assertEqual([a["code"] for a in tools.find_airports("buenos aires")["airports"][:2]], ["EZE", "AEP"])
        self.assertEqual(tools.find_airports("SAEZ")["airports"][0]["code"], "EZE")

    def test_every_airport_of_a_city_comes_first(self):
        for query in ("London", "Londres", "LON"):
            self.assertEqual([a["code"] for a in tools.find_airports(query)["airports"]],
                             ["LHR", "LGW", "STN", "LTN", "LCY"], query)
        cities = {"Nueva York": ["JFK", "LGA", "EWR"], "San Pablo": ["GRU", "CGH"], "Tokio": ["NRT", "HND"],
                  "Roma": ["FCO", "CIA"], "Pekín": ["PEK", "PKX"], "Moscú": ["SVO", "DME", "VKO"],
                  "Ciudad de México": ["MEX", "NLU"], "BUE": ["EZE", "AEP"]}
        for query, codes in cities.items():
            self.assertEqual([a["code"] for a in tools.find_airports(query, limit=len(codes))["airports"]], codes, query)

    def test_former_codes_lead_to_the_current_airport(self):
        self.assertEqual(airports.resolve("PBI,KPBI"), ["DJT", "DJT"])
        for query in ("PBI", "KPBI", "Palm Beach"):
            self.assertEqual(tools.find_airports(query)["airports"][0]["code"], "DJT", query)

    def test_city_codes_name_their_airports(self):
        error = tools.search_flights("BUE", "MIA", "2026-12-10")["error"]
        self.assertEqual((error["type"], error["param"], error["hint"]), ("city_code", "origin", "EZE,AEP"))
        error = tools.cheapest_dates("MIA", "LON", "2026-12-01", "2026-12-03")["error"]
        self.assertEqual((error["type"], error["param"], error["hint"]),
                         ("city_code", "destination", "LHR,LGW,STN,LTN,LCY,SEN"))
        self.assertEqual(tools.search_flights("RIO", "MIA", "2026-12-10")["error"]["hint"], "GIG,SDU")
        self.assertEqual(tools.search_flights("STO", "MIA", "2026-12-10")["error"]["hint"], "ARN,BMA,NYO,VST")
        self.assertFalse(set(airports.CITY_CODES) & set(airports.AIRPORTS))

    def test_errors_are_typed_objects(self):
        error = tools.search_flights("XXX", "MIA", "2026-12-10")["error"]
        self.assertEqual((error["type"], error["param"]), ("unknown_airport", "origin"))
        self.assertIn("find_airports", error["hint"])
        typo = tools.search_flights("EZE", "MIA", "2026-12-32")["error"]
        self.assertEqual((typo["type"], typo["param"]), ("invalid_input", "date"))
        self.assertTrue(typo["cause"].startswith("ValueError: "))
        long = tools.cheapest_dates("EZE", "MIA", "2026-12-01", "2027-03-31")["error"]
        self.assertEqual((long["type"], long["param"]), ("invalid_request", "end_date"))
        self.assertIn("93 dates or fewer", long["hint"])
        client.fetch = lambda url: "<html></html>"
        self.assertEqual(tools.search_flights("EZE", "MIA", "2026-12-10")["error"]["type"], "google_page")

    def test_a_bug_comes_back_as_internal_with_its_traceback(self):
        client.fetch = lambda url: {}[url]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            error = tools.search_flights("EZE", "MIA", "2026-12-10")["error"]
        self.assertEqual(error["type"], "internal")
        self.assertTrue(error["message"].startswith("KeyError: "))
        self.assertIn("Traceback", stderr.getvalue())

    def test_a_layover_that_starts_after_midnight_is_overnight(self):
        self.assertTrue(decode._overnight(datetime(2026, 12, 11, 0, 25), datetime(2026, 12, 11, 18, 40)))
        self.assertTrue(decode._overnight(datetime(2026, 12, 10, 23, 5), datetime(2026, 12, 11, 0, 20)))
        self.assertFalse(decode._overnight(datetime(2026, 12, 11, 5, 0), datetime(2026, 12, 11, 18, 40)))


if __name__ == "__main__":
    unittest.main()
