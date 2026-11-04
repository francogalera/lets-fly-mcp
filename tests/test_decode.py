"""The decode on ds:1 rows written by hand: what each row carries comes out of search_flights as is."""
import unittest
from datetime import date

from fake_pages import leg, page, reference, row
from letsfly import client, decode, models, tools

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
DAY_MS = 86_400_000
MADRID_SEP_4 = 1_788_472_800_000  # Midnight in Madrid, 22:00 UTC the day before
BUENOS_AIRES_SEP_4 = 1_788_490_800_000  # Midnight in Buenos Aires, 03:00 UTC the same day
HONOLULU_SEP_4 = 1_788_516_000_000  # Midnight in Honolulu, 10:00 UTC the same day, the westernmost that holds
AUCKLAND_SUMMER_SEP_4 = 1_788_433_200_000  # Midnight at UTC+13, 11:00 UTC the day before, the easternmost that holds


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


class Reference(unittest.TestCase):
    def test_history_is_one_price_a_week_ending_at_the_latest_day(self):
        prices = [100 + day for day in range(61)]
        prices[5], prices[6] = 200, 50  # Neither falls on a weekly sample, and both still set max and min
        points = [[MADRID_SEP_4 + day * DAY_MS, price] for day, price in enumerate(prices)]
        history = decode.price_reference([None] * 5 + [reference(2, 48, 62, 40, 145, points)])["history"]
        self.assertEqual(history, {"from": "2026-09-08", "weekly": [104, 111, 118, 125, 132, 139, 146, 153, 160],
                                   "min": 50, "max": 200})

    def test_the_day_is_the_origin_s_on_either_side_of_utc(self):
        for stamp in (MADRID_SEP_4, BUENOS_AIRES_SEP_4, HONOLULU_SEP_4, AUCKLAND_SUMMER_SEP_4):
            history = decode.price_reference([None] * 5 + [reference(2, 48, 62, 40, 145, [[stamp, 44]])])["history"]
            self.assertEqual(history, {"from": "2026-09-04", "weekly": [44], "min": 44, "max": 44})

    def test_every_amount_is_a_float(self):
        out = decode.price_reference([None] * 5 + [reference(2, 48, 62, 40, 145, [[MADRID_SEP_4, 44]])])
        history = out["history"]
        amounts = [out["lowest"], out["typical"], out["low"], out["high"], *history["weekly"], history["min"], history["max"]]
        self.assertEqual({type(amount) for amount in amounts}, {float})

    def test_a_page_without_the_block_has_no_reference(self):
        for data in ([None] * 5 + [None], [None] * 5 + [[]]):
            with self.subTest(data):
                self.assertIsNone(decode.price_reference(data))

    def test_an_unusable_number_is_null_never_zero(self):
        for bad in (0, -5, True, "48", float("inf"), 10 ** 400):
            with self.subTest(bad=repr(bad)[:10]):
                block = [2, [None, bad], [None, bad], None, [None, bad], [None, bad], 1, None, None, None, None]
                self.assertIsNone(decode.price_reference([None] * 5 + [block]))
        block[2] = [None, 62]
        self.assertEqual(decode.price_reference([None] * 5 + [block]),
                         {"lowest": None, "typical": 62, "low": None, "high": None, "history": None})

    def test_a_bad_history_does_not_sink_the_reference(self):
        for points in (["junk", ["x", 5], [MADRID_SEP_4, None], [MADRID_SEP_4, 0]], [[10 ** 30, 44]], [[10 ** 400, 44]]):
            with self.subTest(points):
                block = reference(2, 48, 62, 40, 145)
                block[10] = [points]
                self.assertEqual(decode.price_reference([None] * 5 + [block])["typical"], 62)
                self.assertIsNone(decode.price_reference([None] * 5 + [block])["history"])


class ReferenceInResult(unittest.TestCase):
    REFERENCE = reference(3, 720, 700, 650, 900, [[BUENOS_AIRES_SEP_4, 700]])
    EXPECTED = {"lowest": 720, "typical": 700, "low": 650, "high": 900,
                "history": {"from": "2026-09-04", "weekly": [700], "min": 700, "max": 700}}

    def setUp(self):
        self.real = client.fetch, models.today
        models.today = lambda: TODAY

    def tearDown(self):
        client.fetch, models.today = self.real

    def test_one_way(self):
        client.fetch = lambda url: page([GLUED], self.REFERENCE)
        self.assertEqual(tools.search_flights("EZE", "BOS", "2026-12-10")["price_reference"], self.EXPECTED)

    def test_a_round_trip_takes_it_from_the_first_page(self):
        outbounds = iter([page([GLUED], self.REFERENCE)])
        client.fetch = lambda url: next(outbounds, page([BACK]))
        out = tools.search_flights("EZE", "BOS", "2026-12-10", "2026-12-20")
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["price_reference"], self.EXPECTED)

    def test_the_field_is_missing_when_google_does_not_give_it(self):
        client.fetch = lambda url: page([GLUED])
        self.assertNotIn("price_reference", tools.search_flights("EZE", "BOS", "2026-12-10"))

    def test_it_stays_when_our_own_filters_leave_no_rows(self):
        client.fetch = lambda url: page([GLUED], self.REFERENCE)
        out = tools.search_flights("EZE", "BOS", "2026-12-10", airlines=["AA"])
        self.assertEqual((out["count"], out["price_reference"]), (0, self.EXPECTED))


if __name__ == "__main__":
    unittest.main()
