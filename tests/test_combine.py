"""Joining two monthly exports for one pay week.

Sitterwise exports a month at a time and payroll runs Monday to Sunday, so
a pay week lands across a month end every few months - the week of Monday
31 August 2026 needs one day from August and six from September.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.combine import combine_exports                              # noqa: E402
from payroll.importer import COLUMN_MAP, _squash, read_workbook          # noqa: E402
from payroll.rules import Rules                                          # noqa: E402
from payroll.run import build_run                                        # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "test-payroll.xlsx"


def as_day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


class SplittingAnExportAndPuttingItBack(unittest.TestCase):
    """The strongest thing to check: two halves must pay what the whole did."""

    @classmethod
    def setUpClass(cls):
        cls.header, cls.rows, _ = read_workbook(FIXTURE)
        cls.date_column = next(c for c in cls.header
                               if COLUMN_MAP.get(_squash(c)) == "start_date")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.work = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name, body):
        path = self.work / name
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.header, extrasaction="ignore")
            writer.writeheader()
            for row in body:
                writer.writerow({
                    c: ("" if row.get(c) is None else
                        row[c].isoformat(sep=" ") if isinstance(row.get(c), datetime)
                        else row[c])
                    for c in self.header})
        return path

    def split(self, cut: date):
        early = [r for r in self.rows if as_day(r[self.date_column]) < cut]
        late = [r for r in self.rows if as_day(r[self.date_column]) >= cut]
        return self.write("august.csv", early), self.write("september.csv", late)

    def test_the_two_halves_pay_exactly_what_the_whole_paid(self):
        first, second = self.split(date(2026, 8, 6))
        joined = combine_exports([first, second], self.work)
        rules = Rules.load()
        whole = build_run(FIXTURE, rules, date(2026, 8, 1), date(2026, 8, 15))
        rejoined = build_run(joined.path, rules, date(2026, 8, 1), date(2026, 8, 15))
        self.assertEqual(
            {c.key: c.total_paid for c in whole.caregivers},
            {c.key: c.total_paid for c in rejoined.caregivers})

    def test_no_booking_is_lost(self):
        first, second = self.split(date(2026, 8, 6))
        joined = combine_exports([first, second], self.work)
        self.assertEqual(joined.total_rows, len(self.rows))

    def test_a_booking_appearing_twice_in_one_export_is_kept_twice(self):
        # The fixture contains a booking recorded twice on purpose, and the
        # payroll check reports it. Removing it here would hide the problem
        # and quietly drop that caregiver's pay - which is what the first
        # version of this did, for $138.
        first, second = self.split(date(2026, 8, 6))
        joined = combine_exports([first, second], self.work)
        rules = Rules.load()
        rejoined = build_run(joined.path, rules, date(2026, 8, 1), date(2026, 8, 15))
        titles = " ".join(f.title for f in rejoined.findings)
        self.assertIn("appears", titles)

    def test_it_says_where_each_row_came_from(self):
        first, second = self.split(date(2026, 8, 6))
        joined = combine_exports([first, second], self.work)
        self.assertEqual(len(joined.parts), 2)
        self.assertEqual(sum(p.used for p in joined.parts), joined.total_rows)
        for part in joined.parts:
            self.assertTrue(part.first_day and part.last_day)

    def test_the_names_shown_are_the_ones_off_the_computer(self):
        # Uploads are stored with a content hash in front so two files with
        # the same name cannot collide. Nobody should have to read that.
        stored = self.work / "0123456789abcdef-bookingsAugust2026.csv"
        first, _ = self.split(date(2026, 8, 6))
        stored.write_bytes(first.read_bytes())
        joined = combine_exports([stored], self.work)
        self.assertEqual(joined.parts[0].name, "bookingsAugust2026.csv")


class WhenTheSameBookingIsInBothMonths(unittest.TestCase):
    HEADER = ["Booking ID", "Caregiver Name", "Start Date", "Start Time",
              "End Date", "End Time", "Paid to Caregiver", "Status"]

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.work = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name, rows):
        path = self.work / name
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.HEADER)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def row(self, booking, day, paid="92.00"):
        return {"Booking ID": booking, "Caregiver Name": "Ada Whitlow",
                "Start Date": day, "Start Time": "09:00",
                "End Date": day, "End Time": "13:00",
                "Paid to Caregiver": paid, "Status": "completed"}

    def test_it_is_counted_once(self):
        first = self.write("aug.csv", [self.row("100", "2026-08-31")])
        second = self.write("sep.csv", [self.row("100", "2026-08-31"),
                                        self.row("101", "2026-09-01")])
        joined = combine_exports([first, second], self.work)
        self.assertEqual(joined.total_rows, 2)
        self.assertEqual(joined.seen_twice, ["100"])

    def test_a_booking_that_changed_between_exports_is_reported(self):
        first = self.write("aug.csv", [self.row("100", "2026-08-31", paid="92.00")])
        second = self.write("sep.csv", [self.row("100", "2026-08-31", paid="115.00")])
        joined = combine_exports([first, second], self.work)
        self.assertEqual(len(joined.disagreements), 1)
        self.assertIn("Paid to Caregiver", joined.disagreements[0]["columns"])

    def test_the_newer_export_wins_a_disagreement(self):
        first = self.write("aug.csv", [self.row("100", "2026-08-31", paid="92.00")])
        second = self.write("sep.csv", [self.row("100", "2026-08-31", paid="115.00")])
        joined = combine_exports([first, second], self.work)
        with open(joined.path, encoding="utf-8-sig") as fh:
            written = list(csv.DictReader(fh))
        self.assertEqual(written[0]["Paid to Caregiver"], "115.00")
        self.assertEqual(joined.disagreements[0]["kept_from"], "sep.csv")

    def test_a_file_with_no_booking_id_column_is_named_not_silently_skipped(self):
        path = self.work / "wrong.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Something", "Else"])
            writer.writerow(["a", "b"])
        good = self.write("aug.csv", [self.row("100", "2026-08-31")])
        joined = combine_exports([good, path], self.work)
        self.assertTrue(joined.problems)
        self.assertIn("wrong.csv", joined.problems[0])

    def test_columns_only_one_file_has_are_kept(self):
        first = self.write("aug.csv", [self.row("100", "2026-08-31")])
        extra = self.work / "sep.csv"
        with open(extra, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=[*self.HEADER, "Round Trip Miles"])
            writer.writeheader()
            writer.writerow({**self.row("101", "2026-09-01"), "Round Trip Miles": "64"})
        joined = combine_exports([first, extra], self.work)
        with open(joined.path, encoding="utf-8-sig") as fh:
            written = list(csv.DictReader(fh))
        self.assertIn("Round Trip Miles", written[0])
        self.assertEqual(written[1]["Round Trip Miles"], "64")


if __name__ == "__main__":
    unittest.main(verbosity=2)
