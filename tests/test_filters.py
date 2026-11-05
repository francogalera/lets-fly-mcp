"""What search_flights asks Google for when airlines or windows are set, and how it reads the answer, on pages written by hand."""
import contextlib
import io
import threading
import unittest

from fake_pages import directions, leg, page, reference, row
from letsfly import audit, client, decode, models, tfs, tools
from letsfly.models import Search
from test_decode import BACK, GLUED, NIGHT, REGIONAL, TODAY

OUT, RETURN = "2026-12-10", "2026-12-20"


class Pages(unittest.TestCase):
    def setUp(self):
        self.real = client.fetch, client.sleep, models.today
        client.sleep, models.today = lambda seconds: None, lambda: TODAY

    def tearDown(self):
        client.fetch, client.sleep, models.today = self.real


class FiltersInTheUrl(unittest.TestCase):
    def test_airlines_go_in_both_directions_and_in_order(self):
        search = Search(["EZE"], ["MIA"], [OUT, RETURN], airlines={"DL", "AA"})
        out, back = directions(tfs.search_page(search, search.dates))
        self.assertEqual((out[6], back[6]), ([b"AA", b"DL"], [b"AA", b"DL"]))

    def test_each_direction_carries_its_own_window(self):
        search = Search(["EZE"], ["MIA"], [OUT, RETURN], window=(0, 12), return_window=(18, 23))
        out, back = directions(tfs.search_page(search, search.dates))
        self.assertEqual((out[8], out[9], back[8], back[9]), ([0], [12], [18], [23]))

    def test_a_window_stays_in_its_direction(self):
        search = Search(["EZE"], ["MIA"], [OUT, RETURN], window=(6, 12))
        out, back = directions(tfs.search_page(search, search.dates))
        self.assertEqual((out[8], out[9]), ([6], [12]))
        self.assertFalse({8, 9} & back.keys())

    def test_the_filters_stay_on_the_page_that_pins_the_outbound(self):
        search = Search(["EZE"], ["MIA"], [OUT, RETURN], airlines={"AA"}, window=(6, 12))
        outbound = decode.itinerary(REGIONAL)
        out, back = directions(tfs.search_page(search, search.dates, outbound))
        self.assertEqual((len(out[4]), out[6], out[8], back[6]), (2, [b"AA"], [6], [b"AA"]))

    def test_without_filters_the_url_does_not_change(self):
        search = Search(["EZE"], ["MIA"], [OUT], currency="USD")
        self.assertEqual(tfs.search_page(search, search.dates),
                         "https://www.google.com/travel/flights?tfs=CBwQAhoeEgoyMDI2LTEyLTEwagcIARIDRVpFcgcIARIDTUlBQAFIAXABmAEC"
                         "&hl=en&gl=US&curr=USD")

    def test_a_booking_link_carries_no_filter(self):
        direction = directions(tfs.booking_link([decode.itinerary(REGIONAL)], 1, 1, "USD"))[0]
        self.assertFalse({6, 8, 9} & direction.keys())


class ReturnWindowParam(Pages):
    def test_it_needs_a_return_date(self):
        error = tools.search_flights("EZE", "MIA", OUT, return_window="18-23")["error"]
        self.assertEqual((error["type"], error["param"]), ("invalid_request", "return_window"))

    def test_each_window_names_its_own_parameter(self):
        out = tools.search_flights("EZE", "MIA", OUT, RETURN, return_window="tarde")["error"]
        self.assertEqual((out["type"], out["param"]), ("invalid_input", "return_window"))
        out = tools.search_flights("EZE", "MIA", OUT, RETURN, departure_window="tarde")["error"]
        self.assertEqual((out["type"], out["param"]), ("invalid_input", "departure_window"))
        out = tools.search_flights("EZE", "MIA", OUT, RETURN, return_window="6-0")["error"]
        self.assertEqual((out["type"], out["param"]), ("invalid_request", "return_window"))

    def test_it_reaches_the_second_direction_with_reversed_hours_swapped(self):
        urls = []

        def fetch(url):
            urls.append(url)
            return page([])
        client.fetch = fetch
        tools.search_flights("EZE", "MIA", OUT, RETURN, return_window="23-18")
        filtered = [directions(url)[1] for url in urls if 8 in directions(url)[1]]
        self.assertEqual((filtered[0][8], filtered[0][9]), ([18], [23]))


def round_trip(outbounds, returns):
    """A fetch that answers the page with a pinned outbound with returns, and any other with outbounds."""
    return lambda url: page(returns if 4 in directions(url)[0] else outbounds)


class WindowPerDirection(Pages):
    def test_a_return_outside_the_departure_window_stays(self):
        client.fetch = round_trip([NIGHT], [BACK])
        rows = tools.search_flights("EZE", "BOS", OUT, RETURN, departure_window="19-20")["flights"]
        self.assertEqual([(r["out"]["depart"], r["back"]["depart"]) for r in rows],
                         [("2026-12-10 19:40", "2026-12-20 21:00")])

    def test_a_return_outside_the_return_window_goes_when_google_ignores_the_field(self):
        client.fetch = round_trip([NIGHT], [BACK])
        self.assertEqual(tools.search_flights("EZE", "BOS", OUT, RETURN, return_window="6-12")["flights"], [])

    def test_a_return_inside_the_return_window_stays(self):
        client.fetch = round_trip([NIGHT], [BACK])
        self.assertEqual(tools.search_flights("EZE", "BOS", OUT, RETURN, return_window="21-22")["count"], 1)

    def test_an_outbound_outside_its_window_goes_when_google_ignores_the_field(self):
        client.fetch = round_trip([NIGHT, GLUED], [BACK])
        rows = tools.search_flights("EZE", "BOS", OUT, RETURN, departure_window="5-6")["flights"]
        self.assertEqual([r["out"]["depart"] for r in rows], ["2026-12-10 05:50"])

    def test_the_return_window_does_not_touch_the_outbound(self):
        client.fetch = round_trip([NIGHT], [BACK])
        rows = tools.search_flights("EZE", "BOS", OUT, RETURN, return_window="20-21")["flights"]
        self.assertEqual([r["out"]["depart"] for r in rows], ["2026-12-10 19:40"])


def filtered(url):
    return any({6, 8, 9} & direction.keys() for direction in directions(url))


def without_price(priced_row):
    priced_row[1][0] = []
    return priced_row


class ReferenceWithFilters(Pages):
    REFERENCE = reference(3, 720, 700, 650, 900)
    EXPECTED = {"lowest": 720, "typical": 700, "low": 650, "high": 900, "history": None}

    def pages(self, url):
        return page([REGIONAL]) if filtered(url) else page([REGIONAL], self.REFERENCE)

    def test_it_comes_from_the_page_without_filters(self):
        client.fetch = self.pages
        for filters in ({"airlines": ["AA"]}, {"departure_window": "22-23"}):
            with self.subTest(filters):
                out = tools.search_flights("EZE", "BOS", OUT, **filters)
                self.assertEqual((out["count"], out["price_reference"]), (1, self.EXPECTED))

    def test_a_round_trip_with_a_return_window_keeps_it(self):
        client.fetch = lambda url: page([BACK]) if 4 in directions(url)[0] else self.pages(url)
        out = tools.search_flights("EZE", "BOS", OUT, RETURN, return_window="21-22")
        self.assertEqual((out["count"], out["price_reference"]), (1, self.EXPECTED))

    def test_the_rows_stay_when_that_page_fails(self):
        client.fetch = lambda url: page([REGIONAL]) if filtered(url) else "<html></html>"
        out = tools.search_flights("EZE", "BOS", OUT, airlines=["AA"])
        self.assertEqual(out["count"], 1)
        self.assertNotIn("price_reference", out)

    def test_the_rows_stay_when_google_refuses_that_page(self):
        def fetch(url):
            if filtered(url):
                return page([REGIONAL])
            raise client.SearchRefused("refused")
        client.fetch = fetch
        out = tools.search_flights("EZE", "BOS", OUT, airlines=["AA"])
        self.assertEqual(out["count"], 1)
        self.assertNotIn("price_reference", out)

    def test_without_filters_one_page_is_enough(self):
        urls = []

        def fetch(url):
            urls.append(url)
            return page([REGIONAL], self.REFERENCE)
        client.fetch = fetch
        out = tools.search_flights("EZE", "BOS", OUT)
        self.assertEqual((len(urls), out["price_reference"]), (1, self.EXPECTED))


class LostByTheFilter(Pages):
    def test_an_emptied_page_takes_the_rows_of_the_page_without_filters_that_fit(self):
        partner = row([leg("BA175", "EZE", "BOS", "2026-12-10 09:00", "2026-12-10 21:00", "British Airways",
                           sold_as=("AA6936",))], 950, 720)
        client.fetch = lambda url: page([]) if filtered(url) else page([GLUED, REGIONAL, partner])
        out = tools.search_flights("EZE", "BOS", OUT, airlines=["AA"])
        self.assertEqual([r["out"]["flights"] for r in out["flights"]], ["AA900 AA4357"])
        self.assertEqual([r["out"]["flights"] for r in out["codeshares"]], ["BA175"])
        self.assertTrue(out["flights"][0]["price_other_seller"] and out["codeshares"][0]["price_other_seller"])

    def test_those_rows_also_respect_the_window(self):
        client.fetch = lambda url: page([]) if filtered(url) else page([NIGHT, GLUED])
        rows = tools.search_flights("EZE", "BOS", OUT, departure_window="5-6")["flights"]
        self.assertEqual([r["out"]["depart"] for r in rows], ["2026-12-10 05:50"])
        self.assertNotIn("price_other_seller", rows[0])

    def test_a_row_without_a_price_takes_the_one_of_the_same_flight_and_its_place(self):
        cheap, dear = jfk_lax("SQ25", "08:00", 1235), jfk_lax("SQ1", "09:00", 1300)
        client.fetch = lambda url: page([without_price(jfk_lax("SQ25", "08:00")), dear]) if filtered(url) else page([cheap])
        rows = tools.search_flights("JFK", "LAX", OUT, airlines=["SQ"])["flights"]
        self.assertEqual([(r["out"]["flights"], r["price"], r.get("price_other_seller")) for r in rows],
                         [("SQ25", 1235, True), ("SQ1", 1300, None)])

    def test_a_shared_first_leg_does_not_lend_its_price(self):
        def trip(second, price):
            return row([leg("SQ1", "FRA", "MUC", "2026-12-10 07:00", "2026-12-10 08:00", "Singapore"),
                        leg(second, "MUC", "SIN", "2026-12-10 10:00", "2026-12-11 06:00", "Singapore")], price, 900)

        def rows_with(plain):
            client.fetch = lambda url: page([without_price(trip("SQ327", 1))]) if filtered(url) else page([plain])
            return tools.search_flights("FRA", "SIN", OUT, airlines=["SQ"])["flights"]
        self.assertEqual(rows_with(trip("SQ9", 500))[0]["price"], None)
        self.assertEqual(rows_with(trip("SQ327", 700))[0]["price"], 700)

    def test_a_return_with_its_own_price_is_not_another_sellers(self):
        client.fetch = lambda url: (page([BACK]) if 4 in directions(url)[0]
                                    else page([without_price(jfk_lax("AA9", "09:00"))]) if filtered(url)
                                    else page([jfk_lax("AA9", "09:00", 900)]))
        row_out = tools.search_flights("JFK", "LAX", OUT, RETURN, airlines=["AA"])["flights"][0]
        self.assertEqual((row_out["price"], row_out.get("price_other_seller")), (1530, None))

    def test_a_return_without_a_price_keeps_the_outbounds_warning(self):
        client.fetch = lambda url: (page([without_price(row([leg("AA953", "BOS", "EZE", "2026-12-20 21:00", "2026-12-21 10:30", "American")], 1, 690))])
                                    if 4 in directions(url)[0]
                                    else page([without_price(jfk_lax("AA9", "09:00"))]) if filtered(url)
                                    else page([jfk_lax("AA9", "09:00", 900)]))
        row_out = tools.search_flights("JFK", "LAX", OUT, RETURN, airlines=["AA"])["flights"][0]
        self.assertEqual((row_out["price"], row_out["price_estimated"], row_out["price_other_seller"]), (900, True, True))

    def test_a_price_it_gets_back_counts_for_which_outbounds_expand(self):
        priced = [jfk_lax(f"AA{n}", f"0{n}:00", 1000 + n) for n in range(1, 6)]
        pinned, lock = [], threading.Lock()

        def fetch(url):
            first = directions(url)[0]
            if 4 in first:
                with lock:
                    pinned.append(first[4][0])
                return page([BACK])
            if 6 in first:
                return page([*priced, without_price(jfk_lax("AA9", "09:00"))])
            return page([jfk_lax("AA9", "09:00", 900)])
        client.fetch = fetch
        tools.search_flights("JFK", "LAX", OUT, RETURN, airlines=["AA"])
        self.assertEqual(len(pinned), 5)
        self.assertEqual(len([one for one in pinned if b"AA" in one and one.endswith(b"9")]), 1)

    def test_a_flight_the_other_page_does_not_have_stays_without_a_price(self):
        client.fetch = lambda url: page([without_price(jfk_lax("SQ25", "08:00"))]) if filtered(url) else page([jfk_lax("DL2", "08:00")])
        rows = tools.search_flights("JFK", "LAX", OUT, airlines=["SQ"])["flights"]
        self.assertEqual([(r["out"]["flights"], r["price"], r.get("price_other_seller")) for r in rows], [("SQ25", None, None)])

    def test_a_price_the_filtered_page_has_is_not_replaced(self):
        client.fetch = lambda url: page([jfk_lax("AA1", "08:00", 1010)]) if filtered(url) else page([jfk_lax("AA1", "08:00", 1002)])
        found = tools.search_flights("JFK", "LAX", OUT, airlines=["AA"])["flights"][0]
        self.assertEqual((found["price"], found.get("price_other_seller")), (1010, None))

    def test_an_empty_page_without_filters_changes_nothing(self):
        client.fetch = lambda url: page([jfk_lax("AA1", "08:00", 1010)]) if filtered(url) else "<html></html>"
        self.assertEqual(tools.search_flights("JFK", "LAX", OUT, airlines=["AA"])["count"], 1)


class UnknownAirlineCode(Pages):
    def test_the_error_names_airlines_when_only_the_filtered_page_is_empty(self):
        client.fetch = lambda url: "<html></html>" if filtered(url) else page([REGIONAL])
        error = tools.search_flights("EZE", "BOS", OUT, airlines=["ZZ"])["error"]
        self.assertEqual((error["type"], error["param"]), ("invalid_request", "airlines"))
        self.assertIn("IATA", error["hint"])

    def test_google_refusing_the_filtered_page_is_not_blamed_on_airlines(self):
        def fetch(url):
            if filtered(url):
                raise client.SearchRefused("refused")
            return page([REGIONAL])
        client.fetch = fetch
        error = tools.search_flights("EZE", "BOS", OUT, airlines=["AA"])["error"]
        self.assertEqual(error["type"], "google_http")
        self.assertNotIn("param", error)

    def test_a_blocked_client_still_gets_the_page_error(self):
        client.fetch = lambda url: "<html></html>"
        error = tools.search_flights("EZE", "BOS", OUT, airlines=["AA"])["error"]
        self.assertEqual(error["type"], "google_page")

    def test_a_window_alone_is_not_blamed_on_airlines(self):
        client.fetch = lambda url: "<html></html>" if filtered(url) else page([REGIONAL])
        error = tools.search_flights("EZE", "BOS", OUT, departure_window="6-12")["error"]
        self.assertEqual(error["type"], "google_page")


def british(flight, price, sold_as=("AA6936",), departs="2026-12-10 09:35"):
    return row([leg(flight, "LHR", "JFK", departs, "2026-12-10 12:35", "British Airways", sold_as=sold_as)], price, 480)


def american(flight, price, departs="2026-12-10 10:00"):
    return row([leg(flight, "LHR", "JFK", departs, "2026-12-10 13:00", "American")], price, 480)


class SoldAs(Pages):
    def test_it_reads_one_code_or_several(self):
        self.assertEqual(decode.itinerary(british("BA175", 1010)).legs[0].sold_as, {"AA": "AA6936"})
        self.assertEqual(decode.itinerary(british("BA173", 1010, ("AA6934", "IB3559"))).legs[0].sold_as,
                         {"AA": "AA6934", "IB": "IB3559"})
        self.assertEqual(decode.itinerary(british("BA171", 1010, ())).legs[0].sold_as, {})

    def test_a_block_it_cannot_read_does_not_fail_the_row(self):
        for block in ("x", 7, [None], ["AA"], [[None, "1"]], [["", "1"]], [["AA"]], [["AA", 6936]]):
            with self.subTest(block):
                bad = british("BA175", 1010)
                bad[0][2][0][15] = block
                self.assertEqual(decode.itinerary(bad).legs[0].sold_as, {})

    def test_a_row_the_airline_does_not_sell_goes(self):
        client.fetch = lambda url: page([british("BA175", 1010, ("IB3559",))])
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"])
        self.assertEqual(out["flights"], [])
        self.assertNotIn("codeshares", out)


class CodesharesInResult(Pages):
    def test_a_partner_flight_comes_apart_with_the_number_asked_for(self):
        client.fetch = lambda url: page([british("BA175", 1000), american("AA103", 1100)])
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"])
        self.assertEqual((out["count"], [r["out"]["flights"] for r in out["flights"]]), (1, ["AA103"]))
        self.assertNotIn("sold_as", out["flights"][0]["out"])
        shared = out["codeshares"][0]
        self.assertEqual((shared["airlines"], shared["out"]["flights"], shared["out"]["sold_as"]),
                         ("British Airways", "BA175", {"BA175": "AA6936"}))

    def test_only_a_partner_flight_leaves_flights_empty(self):
        client.fetch = lambda url: page([british("BA175", 1000)])
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"])
        self.assertEqual((out["count"], out["flights"], len(out["codeshares"])), (0, [], 1))

    def test_sold_as_names_only_the_airlines_asked_for(self):
        client.fetch = lambda url: page([british("BA173", 1000, ("AA6934", "IB3559", "AY5473"))])
        shared = tools.search_flights("LHR", "JFK", OUT, airlines=["AY", "AA"])["codeshares"][0]
        self.assertEqual(shared["out"]["sold_as"], {"BA173": "AA6934 AY5473"})

    def test_a_row_with_a_partner_leg_comes_apart(self):
        mixed = row([leg("BA472", "LHR", "MAD", "2026-12-10 07:00", "2026-12-10 10:30", "British Airways"),
                     leg("AA67", "MAD", "JFK", "2026-12-10 12:00", "2026-12-10 15:00", "American")], 988, 780)
        client.fetch = lambda url: page([mixed])
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"])
        self.assertEqual(out["flights"], [])
        self.assertEqual([r["out"]["flights"] for r in out["codeshares"]], ["BA472 AA67"])

    def test_a_direction_the_airlines_sell_carries_no_sold_as(self):
        back = row([leg("IB800", "JFK", "LHR", "2026-12-20 19:00", "2026-12-21 07:00", "Iberia", sold_as=("AA6937",))],
                   1300, 480)
        client.fetch = round_trip([british("BA175", 1000)], [back])
        shared = tools.search_flights("LHR", "JFK", OUT, RETURN, airlines=["AA", "BA"])["codeshares"][0]
        self.assertNotIn("sold_as", shared["out"])
        self.assertEqual(shared["back"]["sold_as"], {"IB800": "AA6937"})

    def test_without_airlines_nothing_comes_apart(self):
        client.fetch = lambda url: page([british("BA175", 1000), american("AA103", 1100)])
        out = tools.search_flights("LHR", "JFK", OUT)
        self.assertEqual(out["count"], 2)
        self.assertFalse({"codeshares", "codeshares_total"} & out.keys())
        self.assertFalse([r for r in out["flights"] if "sold_as" in r["out"]])

    def test_a_return_of_a_partner_sends_the_pair_apart(self):
        back = row([leg("BA176", "JFK", "LHR", "2026-12-20 19:00", "2026-12-21 07:00", "British Airways",
                        sold_as=("AA6937",))], 1300, 480)
        own = row([leg("AA100", "JFK", "LHR", "2026-12-20 18:00", "2026-12-21 06:00", "American")], 1400, 480)
        client.fetch = round_trip([american("AA103", 1100)], [back, own])
        out = tools.search_flights("LHR", "JFK", OUT, RETURN, airlines=["AA"])
        self.assertEqual([r["back"]["flights"] for r in out["flights"]], ["AA100"])
        shared = out["codeshares"][0]
        self.assertEqual((shared["out"]["flights"], shared["back"]["flights"], shared["back"]["sold_as"]),
                         ("AA103", "BA176", {"BA176": "AA6937"}))
        self.assertNotIn("sold_as", shared["out"])

    def test_a_cheaper_partner_flight_does_not_take_the_place_of_one_the_airline_sells(self):
        cheap = [british(f"BA{n}", 500 + n, departs=f"2026-12-10 0{n}:00") for n in range(1, 7)]
        pinned, lock = [], threading.Lock()

        def fetch(url):
            first = directions(url)[0]
            if 4 not in first:
                return page([*cheap, american("AA103", 1100)])
            with lock:
                pinned.append(first[4][0])
            return page([BACK])
        client.fetch = fetch
        out = tools.search_flights("LHR", "JFK", OUT, RETURN, airlines=["AA"])
        self.assertEqual(len(pinned), 5)
        self.assertEqual(len([leg for leg in pinned if b"AA" in leg and b"103" in leg]), 1)
        self.assertEqual(len(out["codeshares"]), 3)

    def test_codeshares_stop_at_3_and_say_how_many_there_were(self):
        partners = [british(f"BA{n}", 900 + n, departs=f"2026-12-10 0{n}:00") for n in range(5, 0, -1)]
        client.fetch = lambda url: page(partners)
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"], limit=20)
        self.assertEqual([r["price"] for r in out["codeshares"]], [901, 902, 903])
        self.assertEqual(out["codeshares_total"], 5)

    def test_codeshares_keep_the_order_of_flights_on_a_round_trip(self):
        def back(flight, price):
            return row([leg(flight, "JFK", "LHR", "2026-12-20 08:00", "2026-12-20 20:00", "American")], price, 480)

        def fetch(url):
            first = directions(url)[0]
            if 4 not in first:
                return page([british("BA1", 500), british("BA2", 600, departs="2026-12-10 11:00")])
            return page([back("AA1", 700), back("AA2", 710), back("AA3", 720)] if b"BA" in first[4][0] and b"1" == first[4][0][-1:]
                        else [back("AA5", 1500)])
        client.fetch = fetch
        out = tools.search_flights("LHR", "JFK", OUT, RETURN, airlines=["AA"], limit=20)
        self.assertEqual([r["price"] for r in out["codeshares"]], [700, 1500, 710])
        self.assertEqual(out["codeshares_total"], 4)

    def test_limit_and_links_count_in_each_list(self):
        own = [american(f"AA{n}", 1000 + n, f"2026-12-10 1{n}:00") for n in range(3)]
        partners = [british(f"BA{n}", 900 + n, departs=f"2026-12-10 0{n}:00") for n in range(3)]
        client.fetch = lambda url: page(own + partners)
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"], limit=2)
        self.assertEqual((len(out["flights"]), len(out["codeshares"])), (2, 2))
        for rows in (out["flights"], out["codeshares"]):
            self.assertTrue(all(r.get("link") for r in rows))
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"], limit=3)
        self.assertEqual([bool(r.get("link")) for r in out["codeshares"]], [True, True, False])
        out = tools.search_flights("LHR", "JFK", OUT, airlines=["AA"], limit=3, all_links=True)
        self.assertEqual([bool(r.get("link")) for r in out["codeshares"]], [True, True, True])


def jfk_lax(flight, departs, price=200, sold_as=()):
    return row([leg(flight, "JFK", "LAX", f"2026-12-10 {departs}", "2026-12-10 23:50", flight[:2], sold_as=sold_as)], price, 300)


def lax_jfk(flight, departs):
    return row([leg(flight, "LAX", "JFK", f"2026-12-20 {departs}", "2026-12-20 23:50", flight[:2])], 400, 300)


MORNING, EVENING, PARTNER = jfk_lax("AA1", "08:00", 100), jfk_lax("DL2", "20:00"), jfk_lax("BA3", "21:00", sold_as=("AA6936",))
BACK_MORNING, BACK_EVENING = lax_jfk("AA4", "07:00"), lax_jfk("AA5", "19:00")


def honoring(url, fields=(6, 8)):
    """Google as measured: each field in fields filters its direction, and any other is ignored."""
    first, second = (directions(url) + [{}])[:2]
    if 4 in first:
        return page([BACK_EVENING] if 8 in fields and 8 in second else [BACK_MORNING, BACK_EVENING])
    rows = [MORNING, EVENING, PARTNER]
    if 6 in fields and 6 in first:
        rows = [MORNING, PARTNER]
    if 8 in fields and 8 in first:
        rows = [MORNING]
    return page(rows)


class FiltersReachGoogle(Pages):
    def run_check(self, fetch):
        client.fetch = fetch
        audit.failures.clear()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            audit.filters_reach_google()
        return list(audit.failures), output.getvalue()

    def setUp(self):
        super().setUp()
        self.addCleanup(audit.failures.clear)

    def test_it_passes_when_google_honors_every_field(self):
        failed, output = self.run_check(honoring)
        self.assertEqual(failed, [])
        self.assertIn("2 of 3 rows outside without it, 0 of 1 with it", output)
        self.assertNotIn("inconclusive", output)

    def test_it_fails_on_every_filter_when_google_ignores_the_fields(self):
        failed, _ = self.run_check(lambda url: honoring(url, ()))
        self.assertEqual(failed, ["departure_window", "airlines", "return_window"])

    def test_it_names_only_the_field_google_stopped_honoring(self):
        self.assertEqual(self.run_check(lambda url: honoring(url, (8,)))[0], ["airlines"])
        self.assertEqual(self.run_check(lambda url: honoring(url, (6,)))[0], ["departure_window", "return_window"])

    def test_a_refused_page_fails_its_own_check_and_the_others_still_run(self):
        def fetch(url):
            if 8 in directions(url)[0]:
                raise client.SearchRefused("refused")
            return honoring(url)
        failed, output = self.run_check(fetch)
        self.assertEqual(failed, ["departure_window"])
        self.assertIn("SearchRefused: refused", output)
        self.assertEqual(output.count("  ok  "), 2)

    def test_main_can_run_only_this_check(self):
        client.fetch = honoring
        audit.failures.clear()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as ended:
            audit.main("filters")
        self.assertEqual((ended.exception.code, output.getvalue().count("  ok  ")), (0, 3))

    def test_it_fails_when_the_filter_empties_a_page_that_had_rows_inside(self):
        def emptied(url):
            return page([]) if any({6, 8} & direction.keys() for direction in directions(url)) else honoring(url)
        self.assertEqual(self.run_check(emptied)[0], ["departure_window", "airlines", "return_window"])

    def test_nothing_outside_without_the_filter_proves_nothing_and_does_not_fail(self):
        failed, output = self.run_check(lambda url: page([BACK_EVENING]) if 4 in directions(url)[0] else page([MORNING]))
        self.assertEqual(failed, [])
        self.assertEqual(output.count("inconclusive: 0 of 1 rows outside without it, 0 of 1 with it"), 3)

    def test_a_blocked_client_fails_instead_of_looking_inconclusive(self):
        failed, output = self.run_check(lambda url: "<html></html>")
        self.assertEqual(failed, ["departure_window", "airlines", "return_window"])
        self.assertNotIn("inconclusive", output)


if __name__ == "__main__":
    unittest.main()
