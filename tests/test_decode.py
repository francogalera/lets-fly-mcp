"""The decode on ds:1 rows written by hand: what each row carries comes out of search_flights as is."""
import unittest
from datetime import date

from fake_pages import leg, page, row
from letsfly import client, models, tools

TODAY = date(2026, 11, 1)
REGIONAL = row([leg("AA900", "EZE", "JFK", "2026-12-10 22:00", "2026-12-11 06:30", "American"),
                leg("AA4357", "JFK", "BOS", "2026-12-11 08:15", "2026-12-11 09:40", "American",
                    "Republic Airways as American Eagle")], 980, 820, carry_on=1, checked=0)
NIGHT = row([leg("AR1300", "EZE", "JFK", "2026-12-10 19:40", "2026-12-11 04:10", "Aerolíneas Argentinas"),
             leg("DL2100", "LGA", "BOS", "2026-12-11 07:00", "2026-12-11 08:20", "Delta")], 845, 880, carry_on=1)
GLUED = row([leg("JA709", "EZE", "SCL", "2026-12-10 05:50", "2026-12-10 07:05", "JetSMART"),
             leg("LA2502", "SCL", "BOS", "2026-12-10 10:00", "2026-12-10 20:30", "LATAM", "LATAM Airlines Chile")],
            720, 1000, carry_on=0, self_transfer=True)
BACK = row([leg("AA953", "BOS", "EZE", "2026-12-20 21:00", "2026-12-21 10:30", "American")], 1530, 690)


class Decode(unittest.TestCase):
    def setUp(self):
        self.real = client.fetch, models.today
        models.today = lambda: TODAY

    def tearDown(self):
        client.fetch, models.today = self.real

    def test_each_row_keeps_its_price_operator_bags_and_layovers(self):
        client.fetch = lambda url: page([REGIONAL, NIGHT, GLUED])
        glued, night, regional = tools.search_flights("EZE", "BOS", "2026-12-10")["flights"]
        self.assertEqual([glued["price"], night["price"], regional["price"]], [720.0, 845.0, 980.0])

        self.assertEqual(regional["out"]["operated_by"], {"AA4357": "Republic Airways as American Eagle"})
        self.assertEqual(regional["bags"], {"carry_on": 1, "checked": 0})
        self.assertEqual(regional["out"]["layovers"], [{"airport": "JFK", "minutes": 105}])
        self.assertEqual(regional["out"]["hours"], 13.7)

        self.assertEqual(night["out"]["route"], "EZE-JFK/LGA-BOS")
        self.assertEqual(night["out"]["layovers"],
                         [{"airport": "JFK", "minutes": 170, "overnight": True, "change_of_airport": True}])
        self.assertEqual(night["bags"], {"carry_on": 1, "checked": None})
        self.assertIsNone(night["out"]["operated_by"])

        self.assertIs(glued["self_transfer"], True)
        self.assertIsNone(glued["out"]["operated_by"], "LATAM Airlines Chile is LATAM itself")
        self.assertEqual(glued["bags"], {"carry_on": 0, "checked": None})
        self.assertNotIn("self_transfer", night)
        self.assertNotIn("self_transfer", regional)

    def test_a_round_trip_carries_the_price_of_the_pair(self):
        outbounds = iter([[REGIONAL, NIGHT, GLUED]])
        client.fetch = lambda url: page(next(outbounds, [BACK]))
        rows = tools.search_flights("EZE", "BOS", "2026-12-10", "2026-12-20")["flights"]
        self.assertEqual([(r["out"]["flights"], r["price"]) for r in rows],
                         [("JA709 LA2502", 1530.0), ("AR1300 DL2100", 1530.0), ("AA900 AA4357", 1530.0)])
        self.assertEqual(rows[0]["back"]["route"], "BOS-EZE")
        self.assertIs(rows[0]["self_transfer"], True)
        self.assertNotIn("price_estimated", rows[0])


if __name__ == "__main__":
    unittest.main()
