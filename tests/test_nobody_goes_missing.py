"""Synthetic regression: a past booking still marked confirmed must be reviewed.

The operator needs its caregiver, date and amount before closing payroll.
"""
from __future__ import annotations

import inspect
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.model import Job                                            # noqa: E402
from payroll.rules import Rules                                          # noqa: E402
from payroll.validate import STOP, _check_unclosed_jobs                  # noqa: E402

FIELDS = inspect.signature(Job.__init__).parameters


def _empty(annotation):
    text = str(annotation)
    if "Decimal" in text:
        return Decimal("0")
    if "bool" in text:
        return False
    if "int" in text:
        return 0
    return ""


def booking(booking_id, name, day, status="confirmed", hours="4.00", pay="92.00"):
    kw = {n: _empty(p.annotation) for n, p in FIELDS.items()
          if n != "self" and p.default is inspect.Parameter.empty}
    kw.update(booking_id=booking_id, caregiver_name=name, client_name="Example family",
              start=datetime(day.year, day.month, day.day, 9),
              end=datetime(day.year, day.month, day.day, 13))
    job = Job(**kw)
    job.status = status
    job.workday = day
    job.hours_worked = Decimal(hours)
    job.hours_paid = Decimal(hours)
    job.paid_to_caregiver = Decimal(pay)
    return job


YESTERDAY = date.today() - timedelta(days=1)
WEEK_START = YESTERDAY - timedelta(days=6)


def check(jobs, start=None, end=None):
    return _check_unclosed_jobs(jobs, start or WEEK_START, end or YESTERDAY, Rules.load())


class APastJobStillMarkedConfirmed(unittest.TestCase):
    def test_stops_payroll(self):
        found = check([booking("sample-123", "Casey Sample", YESTERDAY)])
        self.assertEqual([f.level for f in found], [STOP])

    def test_says_who_it_is(self):
        found = check([booking("sample-123", "Casey Sample", YESTERDAY)])
        self.assertIn("Casey Sample", found[0].title)
        self.assertEqual(found[0].booking_ids, ["sample-123"])

    def test_says_how_much_they_would_lose(self):
        found = check([booking("sample-123", "Casey Sample", YESTERDAY)])
        self.assertIn("4.00 hours", found[0].detail)
        self.assertIn("$92.00", found[0].detail)

    def test_says_what_to_do_about_it(self):
        found = check([booking("sample-123", "Casey Sample", YESTERDAY)])
        self.assertIn("completed", found[0].what_to_do)


class OnePersonPerFinding(unittest.TestCase):
    def test_two_people_are_two_stops(self):
        found = check([booking("1", "Casey Sample", YESTERDAY),
                       booking("2", "Anna Lucero", YESTERDAY)])
        self.assertEqual(len(found), 2)
        self.assertEqual({f.caregiver_name for f in found},
                         {"Casey Sample", "Anna Lucero"})

    def test_two_bookings_for_one_person_are_added_up(self):
        found = check([booking("1", "Casey Sample", YESTERDAY),
                       booking("2", "Casey Sample", YESTERDAY - timedelta(days=1))])
        self.assertEqual(len(found), 1)
        self.assertIn("8.00 hours", found[0].detail)
        self.assertIn("$184.00", found[0].detail)
        self.assertEqual(sorted(found[0].booking_ids), ["1", "2"])


class WhatItLeavesAlone(unittest.TestCase):
    def test_a_completed_job_is_fine(self):
        self.assertEqual(check([booking("1", "Tess Okafor", YESTERDAY,
                                        status="completed")]), [])

    def test_a_cancelled_job_is_fine(self):
        self.assertEqual(check([booking("1", "Tess Okafor", YESTERDAY,
                                        status="cancelled")]), [])

    def test_a_booking_still_in_the_future_is_fine(self):
        """Confirmed and upcoming is just the schedule, not a problem."""
        later = date.today() + timedelta(days=5)
        self.assertEqual(check([booking("1", "Tess Okafor", later)],
                               start=date.today(), end=later + timedelta(days=1)), [])

    def test_a_booking_outside_the_pay_period_is_fine(self):
        old = WEEK_START - timedelta(days=30)
        self.assertEqual(check([booking("1", "Tess Okafor", old)]), [])

    def test_nothing_to_say_when_there_is_no_period(self):
        self.assertEqual(
            _check_unclosed_jobs([booking("1", "Tess Okafor", YESTERDAY)],
                                 None, None, Rules.load()), [])


if __name__ == "__main__":
    unittest.main()
