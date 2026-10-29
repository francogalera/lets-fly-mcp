"""The cases the audit of 2026-10-28 found, through the tools, on the recorded pages."""
import base64
import os
import re
import unittest

from letsfly import airports, client, tools
from replay import harness

HERE = os.path.dirname(os.path.abspath(__file__))


class KnownCases(unittest.TestCase):
    def setUp(self):
        self.fx = harness.load(f"{HERE}/replay/fixtures.json.gz")
        harness.replay(self.fx)

    def tearDown(self):
        harness.restore()

    def flights(self, *args):
        return tools.search_flights(*args, limit=20)["flights"]

    def test_regional_operator(self):
        row = next(r for r in self.flights("JFK", "BOS", "2026-12-10") if r["out"]["flights"] == "AA4357")
        self.assertEqual(row["out"]["operated_by"], {"AA4357": "Republic Airways as American Eagle"})

    def test_bags_included(self):
        jsx = next(r for r in self.flights("DAL", "HOU", "2026-12-10") if r["airlines"] == "JSX")
        self.assertEqual(jsx["bags"], {"carry_on": 1, "checked": 2})
        jetsmart = next(r for r in self.flights("AEP", "BRC", "2026-12-10") if r["airlines"] == "JetSMART")
        self.assertEqual(jetsmart["bags"], {"carry_on": 0, "checked": None})

    def test_airport_change_is_visible(self):
        row = next(r for r in self.flights("MDZ", "MIA", "2026-12-10") if r["out"]["route"].startswith("MDZ-AEP"))
        self.assertEqual(row["out"]["route"], "MDZ-AEP/EZE-MIA")
        self.assertTrue(row["out"]["layovers"][0]["change_of_airport"])

    def test_round_trip_carries_the_pair_price(self):
        rows = self.flights("EZE", "NRT", "2027-07-08", "2027-07-22")
        nh = next(r for r in rows if r["back"]["flights"].startswith("NH805"))
        self.assertEqual(nh["price"], 2745.0)

    def test_rows_vary_by_outbound_and_only_two_carry_links(self):
        rows = self.flights("EZE", "MIA", "2026-12-10", "2026-12-20")
        outbounds = [r["out"]["flights"] + r["out"]["depart"] for r in rows]
        distinct = len(set(outbounds))
        self.assertEqual(len(set(outbounds[:distinct])), distinct)
        self.assertEqual([r["price"] for r in rows[:distinct]], sorted(r["price"] for r in rows[:distinct]))
        self.assertEqual([i for i, r in enumerate(rows) if "link" in r], [0, 1])
        every = tools.search_flights("EZE", "MIA", "2026-12-10", "2026-12-20", limit=20, all_links=True)["flights"]
        self.assertTrue(all("link" in r for r in every))

    def test_operator_of_the_same_company_is_hidden(self):
        rows = self.flights("EZE", "SCL", "2026-12-10")
        latam = [r for r in rows if r["airlines"] == "LATAM"]
        self.assertTrue(latam)
        self.assertTrue(all(r["out"]["operated_by"] is None for r in latam))

    def test_airports_by_city(self):
        self.assertEqual(tools.find_airports("Cartagena")["airports"][0]["code"], "CTG")
        self.assertEqual([a["code"] for a in tools.find_airports("buenos aires")["airports"][:2]], ["EZE", "AEP"])
        self.assertEqual(tools.find_airports("SAEZ")["airports"][0]["code"], "EZE")

    def test_former_codes_lead_to_the_current_airport(self):
        self.assertEqual(airports.resolve("PBI,KPBI"), ["DJT", "DJT"])
        for query in ("PBI", "KPBI", "Palm Beach"):
            self.assertEqual(tools.find_airports(query)["airports"][0]["code"], "DJT", query)

    def test_city_codes_are_not_one_of_their_airports(self):
        for code in ("BUE", "RIO", "MIL"):
            self.assertIn("Invalid airport code", tools.search_flights(code, "MIA", "2026-12-10")["error"])

    def test_a_failed_return_page_keeps_the_other_round_trips(self):
        recorded, seen = client.fetch, []
        client.fetch = lambda url: (seen.append(url), recorded(url))[1]
        whole = tools.search_flights("EZE", "MIA", "2026-12-10", "2026-12-20", limit=20)
        lost = seen[3]
        client.fetch = lambda url: "<html></html>" if url == lost else recorded(url)
        out = tools.search_flights("EZE", "MIA", "2026-12-10", "2026-12-20", limit=20)
        self.assertNotIn("error", out)
        self.assertIn("1 of the outbounds", out["note"])
        outbounds = [{r["out"]["flights"] + r["out"]["depart"] for r in o["flights"]} for o in (whole, out)]
        self.assertEqual(len(outbounds[1]), len(outbounds[0]) - 1)
        self.assertNotIn("note", whole)

    def test_dates_that_fail_are_listed(self):
        recorded = client.fetch
        failing = {"2026-12-03", "2026-12-05"}

        def fetch(url):
            if url_days(url) & failing:
                raise TimeoutError("simulated")
            return recorded(url)
        client.fetch = fetch
        out = tools.cheapest_dates("EZE", "MIA", "2026-12-01", "2026-12-07", limit=60)
        self.assertEqual(sorted(out["missing_dates"]), sorted(failing))
        self.assertEqual(out["count"], 5)

    def test_empty_sweep_says_so(self):
        out = tools.cheapest_dates("EZE", "KIX", "2027-07-01", "2027-07-03", trip_days=14)
        self.assertEqual(out["count"], 0)
        self.assertIn("note", out)
        self.assertNotIn("missing_dates", out)


def url_days(url):
    """The travel dates inside a search URL, read from its base64 tfs protobuf."""
    tfs = re.search(r"tfs=([^&]+)", url).group(1)
    raw = base64.urlsafe_b64decode(tfs + "=" * (-len(tfs) % 4))
    return {day.decode() for day in re.findall(rb"\d{4}-\d{2}-\d{2}", raw)}


if __name__ == "__main__":
    unittest.main()
