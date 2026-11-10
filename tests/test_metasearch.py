"""The metasearch on answers written by hand: the search flow and its stop rule, the decoder, blocks, the cache and
the budget, the dates over GraphQL, and the two tools."""
import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import fake_metasearch as fake
from letsfly import __main__, metasearch, models, tools

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
TRIP = [("EZE", "MAD", "2027-04-14"), ("MAD", "EZE", "2027-04-28")]


class Base(unittest.TestCase):
    def setUp(self):
        self.real = metasearch._session, metasearch.sleep, models.now
        self.clock = [NOW]
        self.slept = []
        metasearch.sleep = self.slept.append
        models.now = lambda: self.clock[0]
        metasearch._cache.clear()
        metasearch._spent.clear()
        metasearch._blocked[0] = None
        self.serve(fake.Session())

    def tearDown(self):
        metasearch._session, metasearch.sleep, models.now = self.real
        metasearch._cache.clear()
        metasearch._spent.clear()
        metasearch._blocked[0] = None

    def serve(self, session):
        self.session = session
        metasearch._session = lambda: session

    def paths(self):
        return [path for path, _ in self.session.calls]


class Search(Base):
    def test_key_has_one_leg_per_direction_without_leading_zeros(self):
        self.assertEqual(metasearch.key(TRIP), "EZE-MAD.14-4-2027.MAD-EZE.28-4-2027")

    def test_it_polls_meta_every_3_s_until_final_then_pulls_once(self):
        result, cost = metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual((cost, result["complete"]), (1, True))
        self.assertEqual(self.paths(), ["/flights/search"] + ["/flights/pull/s1/meta"] * 3 + ["/flights/rpull/s1"])
        self.assertEqual(self.slept, [3, 3, 3])
        self.assertEqual(self.session.calls[-1][1], {"v": "final"})
        self.assertEqual(self.session.calls[0][1], {"s": "EZE-MAD.14-4-2027.MAD-EZE.28-4-2027", "countryCode": "AR",
                                                    "numAdults": 1, "currency": "USD"})

    def test_without_final_in_30_s_it_pulls_what_there_is_and_says_so(self):
        self.serve(fake.Session(versions=(4,)))
        result, _ = metasearch.search(TRIP, 1, "ECONOMY")
        self.assertFalse(result["complete"])
        self.assertEqual(self.paths().count("/flights/pull/s1/meta"), 10)
        self.assertEqual(len(result["offers"]), 3)

    def test_a_cabin_other_than_economy_goes_in_the_search(self):
        metasearch.search(TRIP, 2, "BUSINESS")
        self.assertEqual((self.session.calls[0][1]["cabinClass"], self.session.calls[0][1]["numAdults"]), ("Business", 2))

    def test_the_decoder_reads_templates_lists_of_them_and_leading_zeros(self):
        packed = [{"a": 1, "b": {"n": [0, 4, 5]}}, [1, 2, [2, [0, 6]], 3, [2, []]], [0, 7, 8], [], {}]
        self.assertEqual(metasearch.unpack(packed), [
            {"a": 1, "b": {"n": [4, 5]}}, {"a": 2, "b": {"n": [6]}}, {"a": 3, "b": {"n": []}}, [7, 8], [], {}])
        self.assertEqual(metasearch.unpack(fake.pack(fake.ROUND_TRIP)), fake.ROUND_TRIP)

    def test_each_offer_keeps_its_total_price_sellers_bags_fees_and_link(self):
        nonstop, glued, operated = metasearch.search(TRIP, 1, "ECONOMY")[0]["offers"]
        self.assertEqual((nonstop.price, nonstop.sellers), (1100.0, [("Iberia", 1100.0), ("Agency A", 1150.5)]))
        self.assertEqual((nonstop.carry_on, nonstop.checked), (1, 1))
        self.assertEqual((nonstop.bag_fee, nonstop.changes, nonstop.refunds), ("included", 150.0, "included"))
        self.assertEqual(nonstop.link, "https://seller.test/book")
        self.assertEqual((glued.carry_on, glued.checked, glued.bag_fee, glued.refunds), (0, 0, 120.0, None))
        self.assertEqual(glued.link, metasearch.WEB + "/flights/getredir?u=x")
        self.assertIsNone(operated.link)

    def test_each_direction_keeps_legs_layovers_and_separate_tickets(self):
        _, glued, operated = metasearch.search(TRIP, 1, "ECONOMY")[0]["offers"]
        out, back = glued.directions
        self.assertEqual([(leg.airline_name, leg.flight_number) for leg in out.legs], [("JetSMART", "3070"), ("Air Europa", "26")])
        self.assertEqual((out.minutes, out.self_transfer), (1180, True))
        stop = out.layovers[0]
        self.assertEqual((stop.airport, stop.minutes, stop.overnight, stop.change_of_airport), ("GRU", 220, True, False))
        self.assertEqual(back.layovers[0].minutes, 200)
        self.assertEqual(operated.directions[0].legs[0].operator, "LATAM")
        self.assertEqual(operated.directions[0].legs[0].departs, datetime(2027, 4, 14, 6, 0))

    def test_an_empty_result_has_no_offers_and_is_not_kept(self):
        self.serve(fake.Session(result=fake.EMPTY, versions=("final",)))
        result, _ = metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual((result["offers"], result["matrix"], result["complete"]), ([], [], True))
        self.session.result = fake.ROUND_TRIP
        result, cost = metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual((cost, len(result["offers"])), (1, 3))
        self.assertEqual(self.paths().count("/flights/search"), 2)

    def test_a_seller_cheaper_than_the_best_price_carries_the_row(self):
        cheaper = {"providerName": "Agency Y", "price": {"value": 990.0, "currency": "USD", "changes": 50.0},
                   "base64": fake.link("https://y.test/book"), "cabinBaggageIncluded": [[False]],
                   "baggageIncluded": [[True]], "hasBaggageIncluded": True}
        trip = fake.itinerary([fake.segment([fake.flight("JA3078", "AEP", "MDZ", "2027-03-05 20:00", "2027-03-05 21:50")],
                                            110)], [("Agency X", 1000.0)], carry_on=True, checked=False,
                              fees={"changes": 80.0})
        trip["providers"].append(cheaper)
        self.serve(fake.Session(result=fake.body([trip])))
        offer, = metasearch.search([("AEP", "MDZ", "2027-03-05")], 1, "ECONOMY")[0]["offers"]
        self.assertEqual((offer.price, offer.sellers), (990.0, [("Agency Y", 990.0), ("Agency X", 1000.0)]))
        self.assertEqual((offer.link, offer.carry_on, offer.checked, offer.bag_fee, offer.changes),
                         ("https://y.test/book", 0, 1, "included", 50.0))

    def test_the_same_search_within_15_minutes_costs_nothing_and_asks_nothing(self):
        metasearch.search(TRIP, 1, "ECONOMY")
        asked = len(self.session.calls)
        self.clock[0] = NOW + timedelta(minutes=14)
        self.assertEqual(metasearch.search(TRIP, 1, "ECONOMY")[1], 0)
        self.assertEqual(len(self.session.calls), asked)
        self.assertEqual(metasearch.search(TRIP, 2, "ECONOMY")[1], 1)
        self.clock[0] = NOW + timedelta(minutes=16)
        self.assertEqual(metasearch.search(TRIP, 1, "ECONOMY")[1], 1)

    def test_the_budget_counts_real_searches_of_the_last_hour(self):
        metasearch.search(TRIP, 1, "ECONOMY")
        metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual(metasearch.budget(), {"used_last_hour": 1, "safe_per_hour": 10})
        self.clock[0] = NOW + timedelta(minutes=30)
        metasearch.search(TRIP, 2, "ECONOMY")
        self.assertEqual(metasearch.budget()["used_last_hour"], 2)
        self.clock[0] = NOW + timedelta(minutes=61)
        self.assertEqual(metasearch.budget()["used_last_hour"], 1)

    def test_a_403_blocks_this_session_and_nothing_is_asked_again(self):
        self.serve(fake.Session(override={"/flights/search": fake.Response(status=403, text="")}))
        with self.assertRaises(metasearch.MetasearchBlocked) as caught:
            metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual(caught.exception.hint, metasearch.BLOCKED_HINT)
        self.assertEqual(self.paths(), ["/flights/search"])
        with self.assertRaises(metasearch.MetasearchBlocked):
            metasearch.search([("AEP", "MDZ", "2027-03-05")], 1, "ECONOMY")
        self.assertEqual(self.paths(), ["/flights/search"])

    def test_a_block_in_the_middle_of_the_polls_also_stops_the_session(self):
        self.serve(fake.Session(override={"/flights/pull/": fake.Response(status=403, text="")}))
        with self.assertRaises(metasearch.MetasearchBlocked):
            metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual(metasearch.budget()["used_last_hour"], 1)

    def test_a_challenge_is_its_own_error_and_does_not_block_the_session(self):
        challenge = fake.Response(status=403, headers={"cf-mitigated": "challenge"}, text="<html>Just a moment...")
        self.serve(fake.Session(override={"/flights/search": challenge}))
        with self.assertRaises(metasearch.MetasearchChallenge):
            metasearch.search(TRIP, 1, "ECONOMY")
        self.assertIsNone(metasearch._blocked[0])

    def test_other_answers_are_typed(self):
        for response, error in ((fake.Response(status=502, text=""), metasearch.MetasearchRefused),
                                (fake.Response(text="<html>"), metasearch.MetasearchUnreadable),
                                (fake.Response({"version": 0}), metasearch.MetasearchUnreadable)):
            with self.subTest(error.kind):
                self.serve(fake.Session(override={"/flights/search": response}))
                with self.assertRaises(error):
                    metasearch.search(TRIP, 1, "ECONOMY")

    def test_a_code_is_one_airport_or_a_city_code(self):
        self.assertEqual([metasearch.code(c, "origin") for c in ("eze", "BUE", "NYC", "SAEZ")], ["EZE", "BUE", "NYC", "EZE"])
        with self.assertRaises(models.ParseError) as caught:
            metasearch.code("EZE,AEP", "origin")
        self.assertEqual(caught.exception.param, "origin")


class GraphQL(Base):
    def query(self):
        path, params = self.session.calls[-1]
        self.assertEqual(path, "/graphql-cdn")
        return params["query"]

    def test_the_calendar_gives_each_day_by_date_and_asks_for_the_month_without_zeros(self):
        self.serve(fake.Session(graph={"calendarFlightPrices": [
            {"price": 655, "calendardate": "2027-04-26", "isLowprice": False},
            {"price": 550, "calendardate": "2027-04-11", "isLowprice": True}, {"price": None, "calendardate": "2027-04-12"}]}))
        self.assertEqual(metasearch.calendar("EZE", "MAD", "2027-04", False),
                         [("2027-04-11", 550, True), ("2027-04-26", 655, False)])
        query = self.query()
        self.assertIn('month: "4"', query)
        self.assertIn('triptype: "oneway"', query)
        self.assertNotIn("outbound", query)

    def test_the_calendar_with_an_outbound_gives_the_returns_for_it(self):
        self.serve(fake.Session(graph={"calendarFlightPrices": [{"price": 929, "calendardate": "2027-06-23"}]}))
        self.assertEqual(metasearch.calendar("BUE", "MAD", "2027-06", True, "2027-06-09"), [("2027-06-23", 929, False)])
        self.assertIn('triptype: "roundtrip", currency: "USD", outbound: "2027-06-09"', self.query())

    def test_months_come_in_order_and_one_way_says_so(self):
        self.serve(fake.Session(graph={"cheapFlightsMonthsFeed": [{"minprice": 804, "month": 2, "year": 2027},
                                                                  {"minprice": 845, "month": 11, "year": 2026}]}))
        self.assertEqual(metasearch.months("BUE", "MAD", True), [("2026-11", 845), ("2027-02", 804)])
        self.assertNotIn("oneWay", self.query())
        metasearch.months("BUE", "MAD", False)
        self.assertIn("oneWay: true", self.query())

    def test_the_history_comes_by_day(self):
        self.serve(fake.Session(graph={"statsLast90": [{"avg_price": 1056.4583, "created": "2026-11-07"},
                                                       {"avg_price": 937.71, "created": "2026-08-25"}]}))
        self.assertEqual(metasearch.history("EZE", "MAD", "2027-04-14", "2027-04-28"),
                         [("2026-08-25", 937.71), ("2026-11-07", 1056.46)])
        self.assertIn('outbound: "2027-04-14", inbound: "2027-04-28"', self.query())

    def test_graphql_goes_by_get_with_the_device_header_and_costs_no_search(self):
        metasearch.months("BUE", "MAD", True)
        self.assertEqual(metasearch.budget()["used_last_hour"], 0)
        self.assertTrue(self.query().startswith("query { r: cheapFlightsMonthsFeed("))

    def test_a_refused_or_unreadable_query_is_metasearch_page(self):
        refused = fake.Response({"errors": [{"message": "Cannot query field"}]}, status=400)
        for answer in (refused, fake.Response({"data": None, "errors": [{"message": "x"}]}), fake.Response({"data": {}})):
            with self.subTest(answer.text):
                self.serve(fake.Session(graph={"statsLast90": answer}))
                with self.assertRaises(metasearch.MetasearchUnreadable):
                    metasearch.history("EZE", "MAD", "2027-04-14", "2027-04-28")

    def test_after_a_block_the_dates_ask_nothing_either(self):
        metasearch._blocked[0] = NOW
        with self.assertRaises(metasearch.MetasearchBlocked):
            metasearch.months("BUE", "MAD", True)
        self.assertEqual(self.session.calls, [])


class FlightsTool(Base):
    def run_tool(self, **arguments):
        return tools.metasearch_flights(**({"origin": "EZE", "destination": "MAD", "date": "2027-04-14",
                                            "return_date": "2027-04-28"} | arguments))

    def test_rows_go_by_price_with_their_sellers_bags_fees_and_links(self):
        out = self.run_tool()
        glued, operated, nonstop = out["flights"]
        self.assertEqual([r["price"] for r in out["flights"]], [800.25, 950.0, 1100.0])
        self.assertEqual(nonstop["sellers"], [{"name": "Iberia", "price": 1100.0}, {"name": "Agency A", "price": 1150.5}])
        self.assertEqual((nonstop["sellers_total"], nonstop["bags"]), (2, {"carry_on": 1, "checked": 1}))
        self.assertEqual((nonstop["bag_fee"], nonstop["changes"], nonstop["refunds"]), ("included", 150.0, "included"))
        self.assertEqual((glued["bag_fee"], glued["changes"]), (120.0, 95.5))
        self.assertNotIn("refunds", glued)
        self.assertFalse({"bag_fee", "changes", "refunds"} & operated.keys())
        self.assertEqual(nonstop["link"], "https://seller.test/book")
        self.assertNotIn("link", operated)
        self.assertTrue(glued["self_transfer"])
        self.assertNotIn("self_transfer", nonstop)
        self.assertEqual((glued["out"]["route"], glued["out"]["flights"], glued["back"]["route"]),
                         ("EZE-GRU-MAD", "JA3070 UX26", "MAD-GRU-EZE"))
        self.assertEqual(glued["out"]["layovers"], [{"airport": "GRU", "minutes": 220, "overnight": True}])
        self.assertEqual((out["cost"], out["budget"]), (1, {"used_last_hour": 1, "safe_per_hour": 10}))

    def test_the_summary_points_to_each_cheapest_and_the_fastest(self):
        out = self.run_tool()
        self.assertEqual({k: v for k, v in out["summary"].items() if k != "by_airline"},
                         {"cheapest_nonstop": 2, "cheapest_single_ticket": 1, "cheapest_with_carry_on": 1,
                          "cheapest_with_checked_bag": 2, "fastest": 2})
        self.assertEqual(out["summary"]["by_airline"], [{"airline": "Air Europa", "lowest": 800.25, "one_stop": 800.25},
                                                        {"airline": "Iberia", "lowest": 1100.0, "nonstop": 1100.0}])

    def test_the_summary_keeps_the_5_cheapest_airlines(self):
        matrix = [{"id": code, "name": code, "lowestprice": price, "stops": [{"price": price, "stops": 0}]}
                  for code, price in (("G1", 700), ("G2", 300), ("G3", 500), ("G4", 900), ("G5", 400), ("G6", 600),
                                      ("G7", 800))]
        self.serve(fake.Session(result=fake.body([fake.NONSTOP], matrix=matrix)))
        self.assertEqual([a["airline"] for a in self.run_tool()["summary"]["by_airline"]], ["G2", "G5", "G3", "G6", "G1"])

    def test_what_the_summary_points_to_comes_after_the_limit(self):
        out = self.run_tool(limit=1)
        self.assertEqual([r["price"] for r in out["flights"]], [800.25, 950.0, 1100.0])
        self.assertEqual((out["count"], out["summary"]["cheapest_nonstop"]), (3, 2))

    def test_filters_apply_to_the_whole_result(self):
        self.assertEqual([r["price"] for r in self.run_tool(max_stops="NON_STOP")["flights"]], [1100.0])
        self.assertEqual([r["price"] for r in self.run_tool(airlines=["LA"])["flights"]], [950.0])
        self.assertEqual([r["price"] for r in self.run_tool(departure_window="20-23")["flights"]], [1100.0])
        self.assertEqual([r["price"] for r in self.run_tool(return_window="12-13")["flights"]], [800.25, 950.0])
        self.assertEqual(metasearch.budget()["used_last_hour"], 1)

    def test_filters_that_leave_nothing_say_which(self):
        out = self.run_tool(airlines=["AA"])
        self.assertEqual((out["error"]["type"], out["error"]["param"]), ("filtered_out", "airlines"))
        self.assertIn("costs nothing", out["error"]["hint"])

    def test_an_empty_result_is_no_flights_and_says_it_can_be_spurious(self):
        self.serve(fake.Session(result=fake.EMPTY, versions=("final",)))
        error = self.run_tool()["error"]
        self.assertEqual(error["type"], "no_flights")
        self.assertIn("sometimes answers empty", error["hint"])

    def test_the_90_day_history_is_economy_only(self):
        self.serve(fake.Session(graph={"statsLast90": [{"avg_price": 900, "created": "2026-09-01"}]}))
        self.assertNotIn("price_reference", self.run_tool(cabin="BUSINESS"))
        self.assertNotIn("/graphql-cdn", self.paths())
        self.assertIn("price_reference", self.run_tool(adults=2))

    def test_two_codes_of_one_city_are_refused_before_any_search(self):
        for origin, destination in (("BUE", "AEP"), ("EZE", "AEP"), ("NYC", "JFK"), ("JFK", "EWR")):
            with self.subTest(f"{origin}-{destination}"):
                error = tools.metasearch_flights(origin, destination, "2027-04-14")["error"]
                self.assertEqual((error["type"], error["param"]), ("invalid_request", "destination"))
        error = tools.metasearch_flights("EZE", "MAD", "2027-05-03", next_legs=[
            {"origin": "MAD", "destination": "MAD", "date": "2027-05-10"}])["error"]
        self.assertEqual(error["param"], "next_legs")
        self.assertEqual(self.session.calls, [])

    def test_a_round_trip_carries_the_90_day_history_and_a_one_way_does_not_ask(self):
        self.serve(fake.Session(graph={"statsLast90": [{"avg_price": 900, "created": "2026-09-01"},
                                                       {"avg_price": 1000, "created": "2026-11-09"},
                                                       {"avg_price": 950, "created": "2026-10-01"}]}))
        self.assertEqual(self.run_tool()["price_reference"], {"from": "2026-09-01", "to": "2026-11-09", "days": 3,
                                                              "latest": 1000, "low": 900, "high": 1000})
        self.serve(fake.Session(result=fake.ONE_WAY))
        out = tools.metasearch_flights("AEP", "MDZ", "2027-03-05")
        self.assertNotIn("price_reference", out)
        self.assertNotIn("/graphql-cdn", self.paths())

    def test_a_failing_history_leaves_the_search_whole(self):
        self.serve(fake.Session(graph={"statsLast90": fake.Response(status=400, text="{}")}))
        out = self.run_tool()
        self.assertNotIn("price_reference", out)
        self.assertEqual(out["count"], 3)

    def test_a_city_code_and_a_multi_city_trip_go_in_the_key(self):
        tools.metasearch_flights("BUE", "MAD", "2027-05-03", next_legs=[{"origin": "BCN", "destination": "BUE",
                                                                          "date": "2027-05-17"}])
        self.assertEqual(self.session.calls[0][1]["s"], "BUE-MAD.3-5-2027.BCN-BUE.17-5-2027")

    def test_a_trip_is_checked_before_any_search(self):
        for arguments, param in (({"next_legs": [{"origin": "BCN", "destination": "EZE", "date": "2027-05-17"}]}, "next_legs"),
                                 ({"return_date": "2027-04-10"}, "return_date"),
                                 ({"origin": "EZE,AEP"}, "origin"),
                                 ({"return_date": None, "return_window": "6-9"}, "return_window")):
            with self.subTest(param):
                self.assertEqual(self.run_tool(**arguments)["error"]["param"], param)
        self.assertEqual(self.session.calls, [])

    def test_the_same_search_again_costs_nothing_even_with_other_filters(self):
        self.run_tool()
        out = self.run_tool(max_stops="NON_STOP")
        self.assertEqual((out["cost"], out["budget"]["used_last_hour"], out["count"]), (0, 1, 1))

    def test_an_incomplete_search_says_so(self):
        self.serve(fake.Session(versions=(4,)))
        self.assertIn("had not answered", self.run_tool()["note"])

    def test_a_block_comes_back_as_its_error_object(self):
        self.serve(fake.Session(override={"/flights/search": fake.Response(status=403, text="")}))
        error = self.run_tool()["error"]
        self.assertEqual((error["type"], error["hint"]), ("metasearch_blocked", metasearch.BLOCKED_HINT))


CALENDAR = [{"price": 655, "calendardate": "2027-04-26", "isLowprice": False},
            {"price": 550, "calendardate": "2027-04-11", "isLowprice": True}]
MONTHS = [{"minprice": 804, "month": 2, "year": 2027}, {"minprice": 845, "month": 11, "year": 2026}]


class DatesToolAndTerminal(Base):
    def setUp(self):
        super().setUp()
        self.serve(fake.Session(graph={"calendarFlightPrices": CALENDAR, "cheapFlightsMonthsFeed": MONTHS}))

    def test_without_a_month_it_gives_12_months_and_spends_nothing(self):
        out = tools.metasearch_dates("BUE", "MAD")
        self.assertEqual((out["kind"], out["prices"]), ("months", [{"month": "2026-11", "price": 845},
                                                                    {"month": "2027-02", "price": 804}]))
        self.assertEqual(out["seen"], tools.SEEN)
        self.assertNotIn("cost", out)
        self.assertEqual(metasearch.budget()["used_last_hour"], 0)
        tools.metasearch_dates("BUE", "MAD", one_way=True)
        self.assertIn("oneWay: true", self.session.calls[-1][1]["query"])

    def test_a_month_gives_its_days_and_marks_the_cheapest(self):
        out = tools.metasearch_dates("EZE", "MAD", month="2027-04", one_way=True)
        self.assertEqual((out["kind"], out["prices"]), ("departures", [{"date": "2027-04-11", "price": 550, "low": True},
                                                                        {"date": "2027-04-26", "price": 655}]))

    def test_an_outbound_gives_the_returns_of_its_month_unless_another_is_named(self):
        self.assertEqual(tools.metasearch_dates("BUE", "MAD", outbound="2027-04-14")["kind"], "returns")
        self.assertIn('month: "4", triptype: "roundtrip", currency: "USD", outbound: "2027-04-14"',
                      self.session.calls[-1][1]["query"])
        tools.metasearch_dates("BUE", "MAD", month="2027-05", outbound="2027-04-14")
        self.assertIn('month: "5"', self.session.calls[-1][1]["query"])

    def test_wrong_input_is_refused_before_asking(self):
        for arguments, kind, param in (({"month": "2027-4"}, "invalid_input", "month"),
                                       ({"month": "2026-10"}, "invalid_request", "month"),
                                       ({"destination": "EZE"}, "invalid_request", "destination"),
                                       ({"outbound": "2027-04-14", "one_way": True}, "invalid_request", "outbound"),
                                       ({"destination": "EZE,AEP"}, "invalid_input", "destination")):
            with self.subTest(param):
                error = tools.metasearch_dates(**({"origin": "BUE", "destination": "MAD"} | arguments))["error"]
                self.assertEqual((error["type"], error["param"]), (kind, param))
        self.assertEqual(self.session.calls, [])

    def test_nothing_seen_says_so(self):
        self.serve(fake.Session())
        out = tools.metasearch_dates("BUE", "MAD", month="2027-04")
        self.assertEqual((out["count"], out["note"]), (0, "The metasearch saw no fares for this route and period"))

    def terminal(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["flights", *argv]), contextlib.redirect_stdout(out):
            __main__.main()
        return out.getvalue()

    def test_the_terminal_runs_both_and_says_what_a_search_cost(self):
        printed = self.terminal("meta-dates", "BUE", "MAD", "--month", "2027-04").splitlines()
        self.assertEqual(printed[:2], ["2027-04-11  USD 550  low", "2027-04-26  USD 655"])
        self.serve(fake.Session())
        printed = self.terminal("meta", "EZE", "MAD", "2027-04-14", "2027-04-28", "--limit", "1")
        self.assertTrue(printed.startswith("USD 800  JetSMART, Air Europa  from Agency B  (separate tickets)"))
        self.assertTrue(printed.rstrip().endswith("Cost: 1 search; 1 of 10 in the last hour"))
        self.assertEqual(json.loads(self.terminal("meta", "EZE", "MAD", "2027-04-14", "--json"))["cost"], 1)


if __name__ == "__main__":
    unittest.main()
