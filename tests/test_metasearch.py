"""The metasearch on answers written by hand: the search flow and its stop rule, the decoder, blocks, the cache and
the budget and the proxy's shared count, the dates over GraphQL, and the two tools."""
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
        self.real = metasearch._session, metasearch.sleep, models.now, metasearch._proxy
        self.clock = [NOW]
        self.proxied, self.proxy_says = [], {}
        metasearch._proxy = self.proxy
        vars(metasearch._local).pop("spend", None)
        self.slept = []
        metasearch.sleep = self.slept.append
        models.now = lambda: self.clock[0]
        metasearch._cache.clear()
        metasearch._spent.clear()
        metasearch._graphed.clear()
        metasearch._blocked[0] = None
        self.serve(fake.Session())

    def tearDown(self):
        metasearch._session, metasearch.sleep, models.now, metasearch._proxy = self.real
        vars(metasearch._local).pop("spend", None)
        metasearch._cache.clear()
        metasearch._spent.clear()
        metasearch._graphed.clear()
        metasearch._blocked[0] = None

    def serve(self, session):
        self.session = session
        metasearch._session = lambda: session

    def paths(self):
        return [path for path, _ in self.session.calls]

    def searched(self):
        return next(params for path, params in self.session.calls if path == "/flights/search")

    def proxy(self, method, path):
        self.proxied.append((method, path))
        return self.proxy_says.get(path)


class Search(Base):
    def test_key_has_one_leg_per_direction_without_leading_zeros(self):
        self.assertEqual(metasearch.key(TRIP), "EZE-MAD.14-4-2027.MAD-EZE.28-4-2027")

    def test_it_polls_meta_every_3_s_until_final_then_pulls_once(self):
        result, cost = metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual((cost, result["complete"]), (1, True))
        self.assertEqual(self.paths(), ["/graphql-cdn", "/flights/search"] + ["/flights/pull/s1/meta"] * 3
                         + ["/flights/rpull/s1"])
        self.assertEqual(self.slept, [3, 3, 3])
        self.assertEqual(self.session.calls[-1][1], {"v": "final"})
        self.assertEqual(self.searched(), {"s": "EZE-MAD.14-4-2027.MAD-EZE.28-4-2027", "countryCode": "AR",
                                           "numAdults": 1, "currency": "USD"})

    def test_without_final_in_30_s_it_pulls_what_there_is_and_says_so(self):
        self.serve(fake.Session(versions=(4,)))
        result, _ = metasearch.search(TRIP, 1, "ECONOMY")
        self.assertFalse(result["complete"])
        self.assertEqual(self.paths().count("/flights/pull/s1/meta"), 10)
        self.assertEqual(len(result["offers"]), 3)

    def test_a_cabin_other_than_economy_goes_in_the_search(self):
        metasearch.search(TRIP, 2, "BUSINESS")
        self.assertEqual((self.searched()["cabinClass"], self.searched()["numAdults"]), ("Business", 2))

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
        self.assertEqual(metasearch.budget(), {"used_last_hour": 1, "streak_started_at": "2026-11-10T12:00:00Z",
                                               "streak_seconds": 0, "safe_streak_seconds": 240, "shared": False})
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
        self.assertEqual(self.paths(), ["/graphql-cdn", "/flights/search"])
        with self.assertRaises(metasearch.MetasearchBlocked):
            metasearch.search([("AEP", "MDZ", "2027-03-05")], 1, "ECONOMY")
        self.assertEqual(self.paths(), ["/graphql-cdn", "/flights/search"])

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


# The results page's query as the web sent it on 2026-11-12 (one way), and with the round trip's part as its code adds it
ONE_WAY_QUERY = (
    "query ($iso: String!, $iata: String!, $iata2: String!, $iata3: String!, $dep2: String!, $dep3: String!, $route: String) "
    "{ alliances { id name airlines { iata } }, arrival2:airport (iata: $iata2, iso: $iso) { iata hardcoded_city "
    "hardcoded_country country_code latitud longitud region { id } city { id name slug slug_name country { id name } } }, "
    "arrival3:airport (iata: $iata3, iso: $iso) { city { id name } }, departure2:airport (iata: $dep2, iso: $iso) { city "
    "{ id } }, departure3:airport (iata: $dep3, iso: $iso) { city { id } }, providerIata{ provider iata provider_iata_id }, "
    "compareUnit (iata: $iata, country: $iso, route: $route) { provider_name provider_img search_url disallowed_trip_type } }")
ROUND_TRIP_QUERY = ONE_WAY_QUERY.replace(
    "$route: String) { alliances",
    "$route: String, $currency: String!, $arr: String!, $outbound: String!, $inbound: String) { statsLast90(currency: "
    "$currency, iso: $iso, departure: $iata, arrival: $arr, outbound: $outbound, inbound: $inbound) { avg_price created } "
    "alliances")
HISTORY = [{"avg_price": 1056.4583, "created": "2026-11-07"}, {"avg_price": 937.71, "created": "2026-08-25"},
           {"avg_price": None, "created": "2026-09-01"}]


class PageQuery(Base):
    """Before each search, the results page's GraphQL query, as the web sends it."""

    def test_a_one_way_sends_the_webs_query_before_the_search(self):
        self.serve(fake.Session(result=fake.ONE_WAY))
        result, _ = metasearch.search([("AEP", "MDZ", "2027-03-05")], 1, "ECONOMY")
        self.assertEqual(self.paths()[:2], ["/graphql-cdn", "/flights/search"])
        self.assertEqual(self.session.calls[0][1], {
            "query": ONE_WAY_QUERY,
            "variables": '{"iso":"AR","iata":"AEP","iata2":"","iata3":"","dep2":"","dep3":"","route":"AEP-MDZ",'
                         '"arr":"MDZ","outbound":"2027-03-05","currency":"USD"}'})
        self.assertEqual(self.session.sent[0], ({"device": "desktop"}, 5))
        self.assertEqual(self.session.sent[1], (None, 60))
        self.assertIsNone(result["history"])

    def test_a_round_trip_asks_for_the_90_day_history_with_the_return(self):
        self.serve(fake.Session(graph={"statsLast90": HISTORY}))
        result, _ = metasearch.search(TRIP, 1, "ECONOMY", round_trip=True)
        self.assertEqual(self.session.calls[0][1], {
            "query": ROUND_TRIP_QUERY,
            "variables": '{"iso":"AR","iata":"EZE","iata2":"","iata3":"","dep2":"","dep3":"","route":"EZE-MAD",'
                         '"arr":"MAD","outbound":"2027-04-14","inbound":"2027-04-28","currency":"USD"}'})
        self.assertEqual(result["history"], [("2026-08-25", 937.71), ("2026-11-07", 1056.46)])

    def test_a_multi_city_trip_names_its_second_and_third_legs(self):
        legs = [("EZE", "MAD", "2027-05-03"), ("MAD", "BCN", "2027-05-10"), ("BCN", "FCO", "2027-05-14"),
                ("FCO", "EZE", "2027-05-20")]
        self.serve(fake.Session(graph={"statsLast90": HISTORY}))
        result, _ = metasearch.search(legs, 1, "ECONOMY")
        self.assertEqual(self.session.calls[0][1], {
            "query": ONE_WAY_QUERY,
            "variables": '{"iso":"AR","iata":"EZE","iata2":"BCN","iata3":"FCO","dep2":"MAD","dep3":"BCN",'
                         '"route":"EZE-MAD","arr":"MAD","outbound":"2027-05-03","inbound":"2027-05-10","currency":"USD"}'})
        self.assertIsNone(result["history"])

    def test_two_legs_back_and_forth_not_asked_as_a_round_trip_go_as_multi_city(self):
        metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual(self.session.calls[0][1]["query"], ONE_WAY_QUERY)
        self.assertIn('"iata2":"EZE","iata3":"","dep2":"MAD"', self.session.calls[0][1]["variables"])

    def test_a_cached_search_sends_no_query(self):
        metasearch.search(TRIP, 1, "ECONOMY", round_trip=True)
        self.assertEqual(metasearch.search(TRIP, 1, "ECONOMY", round_trip=True)[1], 0)
        self.assertEqual(self.paths().count("/graphql-cdn"), 1)

    def test_a_failing_query_leaves_the_search_whole(self):
        for answer in (fake.Response(status=502, text=""), fake.Response(status=400, text="{}"),
                       fake.Response(text="<html>"), fake.Response({"data": None, "errors": [{"message": "x"}]})):
            with self.subTest(answer.text):
                metasearch._cache.clear()
                self.serve(fake.Session(override={"/graphql-cdn": answer}))
                result, cost = metasearch.search(TRIP, 1, "ECONOMY", round_trip=True)
                self.assertEqual((cost, len(result["offers"]), result["history"]), (1, 3, None))
                self.assertEqual(self.paths()[:2], ["/graphql-cdn", "/flights/search"])

    def test_a_block_or_a_challenge_on_the_query_sends_no_search(self):
        challenge = fake.Response(status=403, headers={"cf-mitigated": "challenge"}, text="<html>Just a moment...")
        for answer, error in ((challenge, metasearch.MetasearchChallenge),
                              (fake.Response(status=403, text=""), metasearch.MetasearchBlocked)):
            with self.subTest(error.kind):
                self.serve(fake.Session(override={"/graphql-cdn": answer}))
                with self.assertRaises(error):
                    metasearch.search(TRIP, 1, "ECONOMY")
                self.assertEqual(self.paths(), ["/graphql-cdn"])


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

    def test_graphql_goes_by_get_with_the_device_header_and_costs_no_search(self):
        metasearch.months("BUE", "MAD", True)
        self.assertEqual(metasearch.budget()["used_last_hour"], 0)
        self.assertTrue(self.query().startswith("query { r: cheapFlightsMonthsFeed("))

    def test_a_refused_or_unreadable_query_is_metasearch_page(self):
        refused = fake.Response({"errors": [{"message": "Cannot query field"}]}, status=400)
        for answer in (refused, fake.Response({"data": None, "errors": [{"message": "x"}]}), fake.Response({"data": {}})):
            with self.subTest(answer.text):
                self.serve(fake.Session(graph={"cheapFlightsMonthsFeed": answer}))
                with self.assertRaises(metasearch.MetasearchUnreadable):
                    metasearch.months("BUE", "MAD", True)

    def test_after_a_block_the_dates_ask_nothing_either(self):
        metasearch._blocked[0] = NOW
        with self.assertRaises(metasearch.MetasearchBlocked):
            metasearch.months("BUE", "MAD", True)
        self.assertEqual(self.session.calls, [])


class FlightsTool(Base):
    def run_tool(self, **arguments):
        return tools.metasearch_flights(**({"origin": "EZE", "destination": "MAD", "date": "2027-04-14",
                                            "return_date": "2027-04-28"} | arguments))

    def test_rows_go_by_price_with_one_seller_bags_fees_and_links(self):
        out = self.run_tool()
        glued, operated, nonstop = out["flights"]
        self.assertEqual([r["price"] for r in out["flights"]], [800.25, 950.0, 1100.0])
        self.assertEqual((nonstop["seller"], nonstop["bags"]), ("Iberia", {"carry_on": 1, "checked": 1}))
        self.assertFalse({"sellers", "sellers_total"} & nonstop.keys())
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
        self.assertEqual((out["cost"], out["budget"]), (1, {"used_last_hour": 1, "streak_started_at": "2026-11-10T12:00:00Z",
                                                            "streak_seconds": 0, "safe_streak_seconds": 240, "shared": False}))

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
        self.assertIn("spends 1 more search", error["hint"])

    def test_the_90_day_history_is_economy_only(self):
        self.serve(fake.Session(graph={"statsLast90": [{"avg_price": 900, "created": "2026-09-01"}]}))
        self.assertNotIn("price_reference", self.run_tool(cabin="BUSINESS"))
        self.assertIn("statsLast90(", self.session.calls[0][1]["query"])
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

    def test_a_round_trip_carries_the_90_day_history_and_a_one_way_does_not(self):
        self.serve(fake.Session(graph={"statsLast90": [{"avg_price": 900, "created": "2026-09-01"},
                                                       {"avg_price": 1000, "created": "2026-11-09"},
                                                       {"avg_price": 950, "created": "2026-10-01"}]}))
        self.assertEqual(self.run_tool()["price_reference"], {"from": "2026-09-01", "to": "2026-11-09", "days": 3,
                                                              "latest": 1000, "low": 900, "high": 1000})
        self.serve(fake.Session(result=fake.ONE_WAY, graph={"statsLast90": [{"avg_price": 90, "created": "2026-09-01"}]}))
        out = tools.metasearch_flights("AEP", "MDZ", "2027-03-05")
        self.assertNotIn("price_reference", out)
        self.assertNotIn("statsLast90", self.session.calls[0][1]["query"])

    def test_a_failing_history_leaves_the_search_whole(self):
        self.serve(fake.Session(graph={"statsLast90": fake.Response(status=400, text="{}")}))
        out = self.run_tool()
        self.assertNotIn("price_reference", out)
        self.assertEqual(out["count"], 3)

    def test_a_city_code_and_a_multi_city_trip_go_in_the_key(self):
        tools.metasearch_flights("BUE", "MAD", "2027-05-03", next_legs=[{"origin": "BCN", "destination": "BUE",
                                                                          "date": "2027-05-17"}])
        self.assertEqual(self.searched()["s"], "BUE-MAD.3-5-2027.BCN-BUE.17-5-2027")

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


class SharedBudget(Base):
    SPEND, READ, BLOCK = ("POST", "/budget/spend"), ("GET", "/budget"), ("POST", "/budget/block")

    def run_tool(self, **arguments):
        return tools.metasearch_flights(**({"origin": "EZE", "destination": "MAD", "date": "2027-04-14",
                                            "return_date": "2027-04-28"} | arguments))

    def test_a_new_search_annotates_its_spend_once_and_the_budget_is_the_proxys_count(self):
        self.proxy_says = {"/budget/spend": {"used_last_hour": 4}}
        out = self.run_tool()
        self.assertEqual(out["budget"], {"used_last_hour": 4, "streak_seconds": 0, "safe_streak_seconds": 240, "shared": True})
        self.assertEqual(self.proxied, [self.SPEND])

    def test_a_cached_search_reads_the_count_once(self):
        self.proxy_says = {"/budget/spend": {"used_last_hour": 4}, "/budget": {"used_last_hour": 6}}
        self.run_tool()
        out = self.run_tool(max_stops="NON_STOP")
        self.assertEqual((out["cost"], out["budget"]["used_last_hour"]), (0, 6))
        self.assertEqual(self.proxied, [self.SPEND, self.READ])

    def test_a_poll_failing_after_the_search_started_still_annotates_the_spend(self):
        self.serve(fake.Session(override={"/flights/pull/": fake.Response(status=502, text="")}))
        with self.assertRaises(metasearch.MetasearchRefused):
            metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual(self.proxied, [self.SPEND])

    def test_a_spend_kept_by_a_failed_search_does_not_leak_into_the_next_budget(self):
        self.proxy_says = {"/budget/spend": {"used_last_hour": 4}, "/budget": {"used_last_hour": 7}}
        metasearch.search(TRIP, 1, "ECONOMY")
        self.serve(fake.Session(override={"/flights/pull/": fake.Response(status=502, text="")}))
        with self.assertRaises(metasearch.MetasearchRefused):
            metasearch.search(TRIP, 2, "ECONOMY")
        self.assertEqual(metasearch.search(TRIP, 1, "ECONOMY")[1], 0)
        self.assertEqual(metasearch.budget()["used_last_hour"], 7)

    def test_a_403_or_429_annotates_the_block_once(self):
        for status in (403, 429):
            with self.subTest(status):
                metasearch._blocked[0], self.proxied = None, []
                self.serve(fake.Session(override={"/flights/search": fake.Response(status=status, text="")}))
                for legs in (TRIP, [("AEP", "MDZ", "2027-03-05")]):
                    with self.assertRaises(metasearch.MetasearchBlocked):
                        metasearch.search(legs, 1, "ECONOMY")
                self.assertEqual(self.proxied, [self.BLOCK])

    def test_a_challenge_annotates_nothing(self):
        challenge = fake.Response(status=403, headers={"cf-mitigated": "challenge"}, text="<html>Just a moment...")
        self.serve(fake.Session(override={"/flights/search": challenge}))
        with self.assertRaises(metasearch.MetasearchChallenge):
            metasearch.search(TRIP, 1, "ECONOMY")
        self.assertEqual(self.proxied, [])

    def test_without_the_proxy_the_budget_is_this_sessions_count(self):
        metasearch.search(TRIP, 1, "ECONOMY")
        metasearch.search(TRIP, 2, "ECONOMY")
        self.assertEqual(metasearch.budget(), {"used_last_hour": 2, "streak_started_at": "2026-11-10T12:00:00Z",
                                               "streak_seconds": 0, "safe_streak_seconds": 240, "shared": False})
        self.assertEqual(self.proxied, [self.SPEND, self.SPEND])


    def test_a_live_block_of_the_ip_passes_through_and_warns(self):
        self.proxy_says = {"/budget": {"used_last_hour": 2, "blocked_at": "2026-11-10T09:00:00Z"}}
        out = metasearch.budget()
        self.assertEqual((out["used_last_hour"], out["shared"], out["blocked_at"]), (2, True, "2026-11-10T09:00:00Z"))
        self.assertIn("blocked this IP at 2026-11-10T09:00:00Z", out["warning"])
        self.assertIn("Ask the user", out["warning"])


class StreakBudget(Base):
    """GraphQL that reaches the API ends the streak; the budget warns at 240 s of it."""

    def answer(self, cache):
        return fake.Response({"data": {"r": []}}, headers={"cf-cache-status": cache} if cache else {})

    def test_a_query_that_reached_the_api_is_annotated_before_the_search(self):
        for cache in ("MISS", "EXPIRED", "REVALIDATED", "BYPASS", "DYNAMIC"):
            with self.subTest(cache):
                metasearch._cache.clear()
                self.proxied = []
                self.serve(fake.Session(override={"/graphql-cdn": self.answer(cache)}))
                metasearch.search([("AEP", "MDZ", "2027-03-05")], 1, "ECONOMY")
                self.assertEqual(self.proxied, [("POST", "/budget/graphql"), ("POST", "/budget/spend")])

    def test_a_query_the_cdn_answered_is_not(self):
        for cache in ("HIT", "STALE", "UPDATING", None):
            with self.subTest(cache):
                metasearch._cache.clear()
                self.proxied = []
                self.serve(fake.Session(override={"/graphql-cdn": self.answer(cache)}))
                metasearch.search([("AEP", "MDZ", "2027-03-05")], 1, "ECONOMY")
                self.assertEqual(self.proxied, [("POST", "/budget/spend")])

    def test_the_dates_queries_are_annotated_too(self):
        self.serve(fake.Session(override={"/graphql-cdn": self.answer("MISS")}))
        metasearch.months("BUE", "MAD", True)
        self.assertEqual(self.proxied, [("POST", "/budget/graphql")])

    def test_without_the_proxy_each_query_that_reached_the_api_ends_the_streak(self):
        for cache, started, seconds in (("MISS", "2026-11-10T12:03:20Z", 0), ("HIT", "2026-11-10T12:00:00Z", 200)):
            with self.subTest(cache):
                metasearch._cache.clear()
                metasearch._spent.clear()
                metasearch._graphed.clear()
                self.serve(fake.Session(override={"/graphql-cdn": self.answer(cache)}))
                self.clock[0] = NOW
                metasearch.search([("AEP", "MDZ", "2027-03-05")], 1, "ECONOMY")
                self.clock[0] = NOW + timedelta(seconds=200)
                metasearch.search([("AEP", "MDZ", "2027-03-06")], 1, "ECONOMY")
                out = metasearch.budget()
                self.assertEqual((out["streak_started_at"], out["streak_seconds"], out["shared"]), (started, seconds, False))

    def test_without_the_proxy_a_pause_of_390_s_ends_it(self):
        metasearch._spent.extend([NOW - timedelta(seconds=500), NOW - timedelta(seconds=110)])
        self.assertEqual(metasearch.budget()["streak_started_at"], "2026-11-10T11:58:10Z")
        metasearch._spent[:] = [NOW - timedelta(seconds=499), NOW - timedelta(seconds=110)]
        self.assertEqual(metasearch.budget()["streak_started_at"], "2026-11-10T11:51:41Z")
        metasearch._spent[:] = [NOW - timedelta(seconds=390)]
        out = metasearch.budget()
        self.assertEqual((out["streak_seconds"], "streak_started_at" in out), (0, False))

    def test_it_warns_at_240_s_with_the_time_left_and_not_at_239(self):
        streak = {"used_last_hour": 9, "streak_started_at": "2026-11-10T11:56:01Z", "streak_seconds": 239,
                  "streak_resets_in": 380}
        self.proxy_says = {"/budget": streak}
        self.assertNotIn("warning", metasearch.budget())
        self.proxy_says = {"/budget": streak | {"streak_started_at": "2026-11-10T11:56:00Z", "streak_seconds": 240}}
        out = metasearch.budget()
        self.assertEqual((out["streak_started_at"], out["streak_seconds"], out["safe_streak_seconds"], out["shared"]),
                         ("2026-11-10T11:56:00Z", 240, 240, True))
        self.assertEqual(out["warning"], "This IP has searched the metasearch for 240 s in a row, and another search may "
                                         "get the IP blocked for hours; the streak ends after 380 s with no search. Ask "
                                         "the user before another one")

    def test_the_streak_kept_from_the_spend_counts_the_polls_after_it(self):
        self.proxy_says = {"/budget/spend": {"used_last_hour": 9, "streak_started_at": "2026-11-10T11:56:20Z",
                                             "streak_seconds": 220, "streak_resets_in": 390}}
        self.serve(fake.Session(versions=(4,)))
        metasearch.sleep = lambda seconds: self.clock.__setitem__(0, self.clock[0] + timedelta(seconds=seconds))
        metasearch.search(TRIP, 1, "ECONOMY")
        out = metasearch.budget()
        self.assertEqual((out["streak_started_at"], out["streak_seconds"]), ("2026-11-10T11:56:20Z", 250))
        self.assertIn("the streak ends after 360 s with no search", out["warning"])

    def test_a_kept_streak_older_than_390_s_has_ended(self):
        self.proxy_says = {"/budget/spend": {"used_last_hour": 9, "streak_started_at": "2026-11-10T11:56:20Z",
                                             "streak_seconds": 220, "streak_resets_in": 390}}
        self.serve(fake.Session(versions=(4,)))
        metasearch.sleep = lambda seconds: self.clock.__setitem__(0, self.clock[0] + timedelta(seconds=39))
        metasearch.search(TRIP, 1, "ECONOMY")
        out = metasearch.budget()
        self.assertEqual((out["streak_seconds"], "streak_started_at" in out, "warning" in out), (0, False, False))

    def test_without_the_proxy_it_warns_for_this_session(self):
        metasearch._spent.extend([NOW - timedelta(seconds=245), NOW - timedelta(seconds=10)])
        out = metasearch.budget()
        self.assertEqual((out["streak_seconds"], out["shared"]), (245, False))
        self.assertTrue(out["warning"].startswith("This session has searched the metasearch for 245 s in a row"))
        self.assertIn("the streak ends after 380 s with no search", out["warning"])


class SharedBudgetClient(unittest.TestCase):
    """The real client against a connection written by hand: anything but a 200 with a count is None."""

    def ask(self, status=200, body=b'{"used_last_hour": 3}', fails=None):
        made = []

        class Connection:
            def __init__(self, host, port, timeout):
                made.append((host, port, timeout))

            def request(self, method, path):
                if fails:
                    raise fails
                made.append((method, path))

            def getresponse(self):
                return mock.Mock(status=status, read=lambda: body)

            def close(self):
                made.append("closed")

        with mock.patch("http.client.HTTPConnection", Connection):
            return metasearch._proxy("POST", "/budget/spend"), made

    def test_it_posts_to_the_proxy_itself_and_reads_the_count(self):
        self.assertEqual(self.ask(), ({"used_last_hour": 3}, [("proxy", 3128, 10), ("POST", "/budget/spend"), "closed"]))
        self.assertEqual(self.ask(body=b'{"used_last_hour": 3, "blocked_at": "2026-11-10T09:00:00Z"}')[0],
                         {"used_last_hour": 3, "blocked_at": "2026-11-10T09:00:00Z"})

    def test_a_refusal_an_unreadable_answer_or_no_connection_is_none(self):
        for status, body, fails in ((429, b'{"used_last_hour": 3}', None), (502, b"", None), (403, b"", None),
                                    (200, b"<html>", None), (200, b'{"used": 3}', None),
                                    (200, b'{"used_last_hour": true}', None), (200, b"", ConnectionRefusedError()),
                                    (200, b'{"used_last_hour": 3, "streak_seconds": "9"}', None),
                                    (200, b'{"used_last_hour": 3, "streak_started_at": "2026-11-10T09:00:00Z"}', None),
                                    (200, b'{"used_last_hour": 3, "streak_started_at": 1, "streak_seconds": 9, '
                                          b'"streak_resets_in": 9}', None)):
            with self.subTest(f"{status} {body!r} {fails!r}"):
                self.assertIsNone(self.ask(status, body, fails)[0])


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
        self.proxy_says = {"/budget/spend": {"used_last_hour": 1}}
        printed = self.terminal("meta", "EZE", "MAD", "2027-04-14", "2027-04-28", "--limit", "1")
        self.assertTrue(printed.startswith("USD 800  JetSMART, Air Europa  from Agency B  (separate tickets)"))
        self.assertTrue(printed.rstrip().endswith("Cost: 1 search; streak of 0 s, of a safe 240 s"))
        self.assertEqual(json.loads(self.terminal("meta", "EZE", "MAD", "2027-04-14", "--json"))["cost"], 1)

    def test_the_terminal_says_when_the_count_is_this_sessions_only_and_prints_the_warning(self):
        self.serve(fake.Session())
        metasearch._spent.append(NOW - timedelta(seconds=250))
        printed = self.terminal("meta", "EZE", "MAD", "2027-04-14", "2027-04-28", "--limit", "1").rstrip().splitlines()
        self.assertEqual(printed[-2], "Cost: 1 search; streak of 250 s, of a safe 240 s (this session only: the proxy "
                                      "could not count the others)")
        self.assertTrue(printed[-1].startswith("Warning: This session has searched the metasearch for 250 s in a row"))


if __name__ == "__main__":
    unittest.main()
