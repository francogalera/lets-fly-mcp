"""Every decoded row against the text Google shows for it (its aria-label), on every recorded page."""
import html
import os
import re
import unittest

from letsfly import decode, page
from replay import harness

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = "January February March April May June July August September October November December".split()


def label_facts(label):
    facts = {"price": float(re.search(r"From ([\d,]+) ", label).group(1).replace(",", ""))}
    stops = re.search(r"(Nonstop|(\d+) stops?) flight with", label)
    facts["stops"] = 0 if stops.group(1) == "Nonstop" else int(stops.group(2))
    when = re.search(r"Leaves .+? at (\d+):(\d+)\W*([AP]M) on \w+, (\w+) (\d+) and arrives at .+? at (\d+):(\d+)\W*([AP]M) on "
                     r"\w+, (\w+) (\d+)", label)
    facts["departs"] = (int(when[1]) % 12 + (12 if when[3] == "PM" else 0), int(when[2]))
    facts["arrives"] = (int(when[6]) % 12 + (12 if when[8] == "PM" else 0), int(when[7]))
    facts["arrival_day"] = (MONTHS.index(when[9]) + 1, int(when[10]))
    duration = re.search(r"Total duration (?:(\d+) hr)? ?(?:(\d+) min)?", label)
    facts["minutes"] = int(duration[1] or 0) * 60 + int(duration[2] or 0)
    operator = re.search(r"Operated by (.+?)\. Leaves", label)
    facts["operator"] = operator.group(1) if operator else None
    bags = re.search(r"(\d+) carry-on bags? included\. (\d+) checked bags? included", label)
    if bags:
        facts["carry_on"], facts["checked"] = int(bags[1]), int(bags[2])
    elif "include overhead bin access" in label:
        facts["carry_on"] = 0
    return facts


def rows(blob):
    data = page.ds1(f"<script>AF_initDataCallback({blob});</script>")
    return [decode.itinerary(row) for index in (2, 3) if data[index] for row in data[index][0]]


class Labels(unittest.TestCase):
    def test_every_priced_row_matches_its_label(self):
        """Pages recorded without labels are skipped; on the others every priced row must have its label."""
        fx = harness.load(f"{HERE}/replay/fixtures.json.gz")
        checked = 0
        for url, recorded in fx["pages"].items():
            labels = [label_facts(html.unescape(text)) for text in recorded["labels"]]
            for trip in rows(recorded["blob"]) if labels else []:
                if trip.price is None:
                    continue
                first, last = trip.legs[0], trip.legs[-1]
                times = ((first.departs.hour, first.departs.minute), (last.arrives.hour, last.arrives.minute))
                same_times = [f for f in labels if (f["departs"], f["arrives"]) == times]
                with self.subTest(url=url[-30:], flight=f"{first.airline}{first.flight_number}"):
                    self.assertTrue(same_times, "no label with this row's times")
                    self.assertIn(trip.price, [f["price"] for f in same_times])
                    facts = next(f for f in same_times if f["price"] == trip.price)
                    self.assertEqual(len(trip.legs) - 1, facts["stops"])
                    self.assertEqual(trip.minutes, facts["minutes"])
                    self.assertEqual((last.arrives.month, last.arrives.day), facts["arrival_day"])
                    operators = ", ".join(leg.operator for leg in trip.legs if leg.operator)
                    self.assertEqual(operators or None, facts["operator"])
                    for bag in ("carry_on", "checked"):
                        if bag in facts:
                            self.assertEqual(getattr(trip, bag), facts[bag])
                checked += 1
        self.assertGreater(checked, 300)


if __name__ == "__main__":
    unittest.main()
