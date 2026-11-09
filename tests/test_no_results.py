"""search_flights without rows says why, and Google's refusals and horizon read as what they are (LET-23), on pages
written by hand after the ones captured live on 2026-11-08."""
import contextlib
import io
import threading
import unittest

from fake_pages import days, directions, page, rejected
from letsfly import audit, client, tools
from test_decode import BACK, GLUED, NIGHT, REGIONAL
from test_filters import OUT, RETURN, Pages, filtered, round_trip

# data[7][0] of EZE-MIA, live: Google has itineraries from 393 to 1277
RANGE = (393, 1277)


def timeout():
    raise client.SearchTimeout("simulated")


def error_of(out):
    return out["error"]["type"], out["error"].get("param")


class Rejected(Pages):
    def test_a_refusal_is_google_rejected_after_a_single_fetch(self):
        urls = []
        client.fetch = lambda url: urls.append(url) or rejected()
        error = tools.search_flights("EZE", "MIA", OUT)["error"]
        self.assertEqual((error["type"], len(urls)), ("google_rejected", 1))

    def test_an_internal_error_from_google_is_tried_again(self):
        answers = [rejected(13), page([NIGHT])]
        client.fetch = lambda url: answers.pop(0)
        self.assertEqual((tools.search_flights("EZE", "JFK", OUT)["count"], answers), (1, []))

    def test_an_internal_error_that_lasts_is_google_page_with_its_status(self):
        client.fetch = lambda url: rejected(13)
        error = tools.search_flights("EZE", "JFK", OUT)["error"]
        self.assertEqual((error["type"], error["message"]),
                         ("google_page", "Google answered with its own error, status 13, on 3 of 3 tries"))

    def test_the_message_counts_only_the_tries_that_carried_the_error(self):
        answers = [rejected(13), rejected(13), "<html>nothing</html>"]
        client.fetch = lambda url: answers.pop(0)
        self.assertEqual(tools.search_flights("EZE", "JFK", OUT)["error"]["message"],
                         "Google answered with its own error, status 13, on 2 of 3 tries")

    def test_a_filtered_page_that_never_loads_is_not_blamed_on_a_valid_airline(self):
        for bad in (rejected(13), "<html>nothing</html>"):
            with self.subTest(bad[:40]):
                client.fetch = lambda url, bad=bad: bad if filtered(url) else page([REGIONAL])
                error = tools.search_flights("EZE", "JFK", OUT, airlines=["AA"])["error"]
                self.assertEqual((error["type"], "param" in error), ("google_page", False))

    def test_a_sweep_stops_on_google_errors_as_on_blocked_pages_and_says_no_consent(self):
        lock, fetched = threading.Lock(), []

        def fetch(url):
            with lock:
                fetched.append(url)
            return rejected(13)
        client.fetch = fetch
        error = tools.cheapest_dates("EZE", "MIA", "2026-12-01", "2026-12-30")["error"]
        # A date starts only while fewer than 5 are blocked, and 10 run at once: at most 4 + 10 dates of 3 tries each
        self.assertLessEqual(len(fetched), 14 * 3)
        self.assertEqual((error["type"], "skipped" in error["message"], "EU/EEA" in error["message"]),
                         ("google_page", True, False))

    def test_a_short_sweep_with_google_errors_on_every_page_is_a_page_error(self):
        client.fetch = lambda url: rejected(13)
        self.assertEqual(tools.cheapest_dates("EZE", "MIA", "2026-12-01", "2026-12-03")["error"]["type"], "google_page")

    def test_the_consent_hint_needs_a_blocked_or_stopped_sweep(self):
        def fetch(url):
            if days(url) == {"2026-12-01"}:
                return "<html>nothing</html>"
            raise client.SearchTimeout("simulated")
        client.fetch = fetch
        error = tools.cheapest_dates("EZE", "MIA", "2026-12-01", "2026-12-03")["error"]
        self.assertEqual((error["type"], "EU/EEA" in error["message"]), ("google", False))


class Horizon(Pages):
    """Pages sets today to 2026-11-01, so 2027-09-26 is day 329, the last one Google searches."""

    def setUp(self):
        super().setUp()
        self.urls = []
        client.fetch = lambda url: self.urls.append(url) or page([NIGHT])

    def error(self, out):
        return out["error"]["type"], out["error"]["param"], out["error"]["hint"], len(self.urls)

    def test_the_last_day_is_searched(self):
        self.assertEqual(tools.search_flights("EZE", "JFK", "2027-09-26")["count"], 1)

    def test_a_date_over_in_every_time_zone_fails_without_a_page(self):
        error = tools.search_flights("EZE", "JFK", "2026-10-30")["error"]
        self.assertEqual((error["type"], error["param"], len(self.urls)), ("invalid_request", "date", 0))

    def test_a_date_past_it_fails_without_a_page(self):
        self.assertEqual(self.error(tools.search_flights("EZE", "JFK", "2027-09-27")),
                         ("invalid_request", "date", "Search on 2027-09-26 or earlier", 0))

    def test_a_return_past_it_names_the_return(self):
        self.assertEqual(self.error(tools.search_flights("EZE", "JFK", "2027-09-20", "2027-09-27"))[:2],
                         ("invalid_request", "return_date"))

    def test_a_range_that_crosses_it_fails_without_a_page(self):
        self.assertEqual(self.error(tools.cheapest_dates("EZE", "JFK", "2027-09-20", "2027-09-27")),
                         ("invalid_request", "end_date", "End the range on 2027-09-26 or earlier", 0))

    def test_a_stay_that_crosses_it_moves_the_end_back(self):
        self.assertEqual(self.error(tools.cheapest_dates("EZE", "JFK", "2027-09-10", "2027-09-20", trip_days=7)),
                         ("invalid_request", "end_date", "End the range on 2027-09-19 or earlier", 0))

    def test_a_stay_too_long_for_the_start_asks_for_an_earlier_start_or_a_shorter_stay(self):
        self.assertEqual(self.error(tools.cheapest_dates("EZE", "JFK", "2027-09-20", "2027-09-21", trip_days=10)),
                         ("invalid_request", "trip_days", "Start the range by 2027-09-16, or shorten trip_days", 0))


class UnreadableRow(Pages):
    def test_a_row_too_short_to_read_is_skipped_and_the_others_stay(self):
        short = [[None, "a detail with two fields"], [[1]], None, None, None]
        client.fetch = lambda url: page([NIGHT, short, REGIONAL])
        rows = tools.search_flights("EZE", "JFK", OUT)["flights"]
        self.assertEqual(sorted(r["out"]["flights"] for r in rows), ["AA900 AA4357", "AR1300 DL2100"])


class FirstPage(Pages):
    def test_a_page_without_itineraries_is_no_flights_on_the_date(self):
        client.fetch = lambda url: page([])
        self.assertEqual(error_of(tools.search_flights("EZE", "MIA", OUT)), ("no_flights", "date"))

    def test_a_page_the_stops_emptied_is_filtered_out(self):
        client.fetch = lambda url: page([], price_range=RANGE)
        self.assertEqual(error_of(tools.search_flights("EZE", "NRT", OUT, max_stops="NON_STOP")),
                         ("filtered_out", "max_stops"))

    def test_rows_of_other_airlines_on_both_pages_are_filtered_out(self):
        client.fetch = lambda url: page([NIGHT, GLUED])
        self.assertEqual(error_of(tools.search_flights("EZE", "JFK", OUT, airlines=["AA"])),
                         ("filtered_out", "airlines"))

    def test_two_filters_are_both_named_and_neither_is_the_param(self):
        client.fetch = lambda url: page([]) if filtered(url) else page([NIGHT])
        error = tools.search_flights("EZE", "JFK", OUT, airlines=["AA"], departure_window="2-3")["error"]
        self.assertEqual((error["type"], "param" in error, error["message"]),
                         ("filtered_out", False, "Google has flights for this search, but none match airlines, "
                                                 "departure_window"))

    def test_the_unfiltered_page_failing_fails_the_search(self):
        client.fetch = lambda url: page([]) if filtered(url) else timeout()
        self.assertEqual(error_of(tools.search_flights("EZE", "JFK", OUT, airlines=["AA"]))[0], "google_timeout")

    def test_the_hint_names_the_cabin_the_adults_and_a_date_over_at_the_origin(self):
        client.fetch = lambda url: page([])
        hint = tools.search_flights("EZE", "MIA", "2026-10-31", cabin="BUSINESS", adults=3)["error"]["hint"]
        self.assertEqual(hint, "Try nearby dates with cheapest_dates, or nearby airports, or another cabin, or fewer "
                               "adults: the seats may be sold out. The date may already be over at the origin")


class RoundTrip(Pages):
    """The round trip page has no itineraries; each one-way page answers by its date."""

    def search(self, out, back, dates=(OUT, RETURN)):
        self.urls = []

        def fetch(url):
            self.urls.append(url)
            if len(directions(url)) == 2:
                return page([])
            return out() if days(url) == {dates[0]} else back()
        client.fetch = fetch
        return tools.search_flights("EZE", "JFK", *dates)

    def test_no_outbound_is_no_flights(self):
        out = self.search(lambda: page([]), lambda: page([BACK]))
        self.assertEqual(error_of(out), ("no_flights", "date"))

    def test_no_return_is_no_return_flights(self):
        out = self.search(lambda: page([NIGHT]), lambda: page([]))
        self.assertEqual(error_of(out), ("no_return_flights", "return_date"))

    def test_both_directions_without_a_fare_cost_two_more_pages(self):
        out = self.search(lambda: page([NIGHT]), lambda: page([BACK]))
        self.assertEqual((error_of(out), len(self.urls)), (("no_round_trip_fare", None), 3))

    def test_a_same_day_round_trip_warns_the_return_may_leave_first(self):
        out = self.search(lambda: page([NIGHT]), lambda: page([NIGHT]), (OUT, OUT))
        self.assertTrue(out["error"]["hint"].endswith("On the same day the return may leave before the outbound lands"))

    def test_a_one_way_page_failing_fails_the_search(self):
        out = self.search(timeout, lambda: page([BACK]))
        self.assertEqual(error_of(out)[0], "google_timeout")


def failing_first_return(then):
    """A fetch with two outbounds whose first return page fails and the other answers then."""
    lock, seen = threading.Lock(), []

    def fetch(url):
        if 4 not in directions(url)[0]:
            return page([NIGHT, REGIONAL])
        with lock:
            seen.append(url)
            first = len(seen) == 1
        return timeout() if first else then
    return fetch


class Returns(Pages):
    def test_no_return_on_any_page_is_no_return_flights(self):
        client.fetch = round_trip([NIGHT, REGIONAL], [])
        self.assertEqual(error_of(tools.search_flights("EZE", "JFK", OUT, RETURN)), ("no_return_flights", "return_date"))

    def test_returns_the_return_window_emptied_are_filtered_out(self):
        client.fetch = lambda url: page([], price_range=RANGE) if 4 in directions(url)[0] else page([NIGHT, REGIONAL])
        self.assertEqual(error_of(tools.search_flights("EZE", "JFK", OUT, RETURN, return_window="1-2")),
                         ("filtered_out", "return_window"))

    def test_a_failed_return_page_with_the_rest_empty_fails_the_search(self):
        client.fetch = failing_first_return(page([]))
        self.assertEqual(error_of(tools.search_flights("EZE", "JFK", OUT, RETURN))[0], "google_timeout")

    def test_the_lost_return_note_stays_when_other_returns_came(self):
        client.fetch = failing_first_return(page([BACK]))
        out = tools.search_flights("EZE", "JFK", OUT, RETURN)
        self.assertEqual((out["count"], out["note"]),
                         (1, "The return page failed to load for 1 of the outbounds, so their round trips are missing"))


class Audit(Pages):
    """flights audit compares live searches, which can now come back as an error without flights."""

    def run_check(self, check):
        audit.failures.clear()
        self.addCleanup(audit.failures.clear)
        with contextlib.redirect_stdout(io.StringIO()):
            check()
        return list(audit.failures)

    def test_its_dates_follow_the_clock_the_tools_validate_against(self):
        self.assertEqual(audit.day(0), "2026-11-01")

    def test_a_search_without_flights_is_a_disagreement_and_not_a_crash(self):
        client.fetch = lambda url: page([]) if 4 in directions(url)[0] else page([NIGHT])
        self.assertEqual(self.run_check(audit.dates_match_search),
                         ["cheapest_dates equals the cheapest search_flights row"])

    def test_a_search_without_flights_is_not_a_burst_error(self):
        client.fetch = lambda url: page([])
        self.assertEqual(self.run_check(audit.burst), [])


if __name__ == "__main__":
    unittest.main()
