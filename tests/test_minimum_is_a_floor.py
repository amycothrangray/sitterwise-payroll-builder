"""The four-hour minimum is a floor nothing gets under.

Sitterwise exports its own Hours Billed column, and the app used to take it
on trust - so a booking Sitterwise billed at 3.00 or 3.75 hours was paid at
that, with the guarantee skipped and nothing said about it. Three caregivers
were short on the week of 31 August 2026 before this was caught.

The minimum now applies to every booking somebody worked, whatever the
export says, and a disagreement with the export is reported rather than
quietly resolved either way.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import inspect
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.importer import _set_pay                                    # noqa: E402
from payroll.model import Job                                            # noqa: E402
from payroll.rules import Rules                                          # noqa: E402
from payroll.validate import _what_this_booking_paid                      # noqa: E402

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


def job_paid(worked, billed=None, minimum_flag="", rate="23"):
    """One job through the pay rules, as the importer would build it."""
    rules = Rules.load()
    kw = {name: _empty(p.annotation) for name, p in FIELDS.items()
          if name != "self" and p.default is inspect.Parameter.empty}
    kw.update(booking_id="b1", caregiver_name="Tess Okafor", client_name="Cameron",
              start=datetime(2026, 9, 1, 9), end=datetime(2026, 9, 1, 12),
              hours_worked=Decimal(worked))
    job = Job(**kw)
    job.rate = Decimal(rate)
    cell = None
    if billed is not None:
        cell = (lambda key: {"hours_billed_stated": billed,
                             "minimum_applied_stated": minimum_flag}.get(key))
    _set_pay(job, rules, cell)
    return job


class AShortBookingIsPaidTheMinimum(unittest.TestCase):
    def test_when_the_export_says_nothing(self):
        self.assertEqual(job_paid("3.00").hours_paid, Decimal("4.00"))

    def test_when_the_export_billed_it_short(self):
        """The case that underpaid three people."""
        job = job_paid("3.00", billed="3.00")
        self.assertEqual(job.hours_paid, Decimal("4.00"))
        self.assertEqual(job.guarantee_hours, Decimal("1.00"))

    def test_a_quarter_hour_short_counts_too(self):
        job = job_paid("3.75", billed="3.75")
        self.assertEqual(job.hours_paid, Decimal("4.00"))
        self.assertEqual(job.guarantee_hours, Decimal("0.25"))

    def test_paying_more_than_the_export_asked_for_is_said_out_loud(self):
        job = job_paid("3.75", billed="3.75")
        self.assertTrue(any("under the" in n for n in job.import_notes),
                        f"nothing explained the difference: {job.import_notes}")

    def test_the_guarantee_is_paid_at_the_job_rate(self):
        job = job_paid("3.00", billed="3.00", rate="23")
        self.assertEqual(job.guarantee_pay, Decimal("23.00"))
        self.assertEqual(job.straight_pay, Decimal("69.00"))

    def test_it_is_marked_as_the_minimum_so_the_wage_statement_says_so(self):
        self.assertTrue(job_paid("3.00", billed="3.00").minimum_applied)

    def test_the_export_flag_being_blank_does_not_lose_the_label(self):
        job = job_paid("3.00", billed="4.00", minimum_flag="")
        self.assertEqual(job.guarantee_hours, Decimal("1.00"))
        self.assertTrue(job.minimum_applied)


class WhatTheMinimumMustNotTouch(unittest.TestCase):
    def test_a_normal_length_job_is_untouched(self):
        job = job_paid("6.00", billed="6.00")
        self.assertEqual(job.hours_paid, Decimal("6.00"))
        self.assertEqual(job.guarantee_hours, Decimal("0.00"))
        self.assertFalse(job.minimum_applied)

    def test_a_job_exactly_on_the_minimum_gains_nothing(self):
        job = job_paid("4.00", billed="4.00")
        self.assertEqual(job.hours_paid, Decimal("4.00"))
        self.assertEqual(job.guarantee_hours, Decimal("0.00"))

    def test_a_cancellation_nobody_worked_is_not_topped_up(self):
        """A cancellation fee is not a short shift. Paying four hours for a
        booking nobody turned up to would invent money."""
        job = job_paid("0.00", billed="2.00")
        self.assertEqual(job.hours_paid, Decimal("2.00"))
        self.assertFalse(job.minimum_applied)

    def test_a_cancellation_with_no_pay_stays_at_nothing(self):
        job = job_paid("0.00", billed="0.00")
        self.assertEqual(job.hours_paid, Decimal("0.00"))

    def test_the_export_billing_under_the_clock_is_still_reported(self):
        job = job_paid("5.00", billed="4.00")
        self.assertEqual(job.hours_paid, Decimal("4.00"))
        self.assertTrue(any("billed at" in n for n in job.import_notes))


if __name__ == "__main__":
    unittest.main()


class SayingWhatABookingActuallyPaid(unittest.TestCase):
    """The hours-disagreement finding used to report only the worked hours at
    the rate and leave the minimum top-up off. It reads like the whole figure.
    On 9 September 2026 it was read that way, and a payroll was held up over
    three caregivers who were never short."""

    def test_a_topped_up_booking_reports_the_whole_amount(self):
        job = job_paid("3.00", rate="23")
        text = _what_this_booking_paid(job)
        self.assertIn("92.00", text)
        self.assertIn("minimum top-up", text)

    def test_it_still_shows_the_working(self):
        text = _what_this_booking_paid(job_paid("3.00", rate="23"))
        self.assertIn("69.00", text)      # the worked hours
        self.assertIn("23.00", text)      # the top-up

    def test_a_normal_booking_says_one_figure(self):
        text = _what_this_booking_paid(job_paid("6.00", rate="23"))
        self.assertIn("138.00", text)
        self.assertNotIn("top-up", text)

    def test_it_names_no_pronoun(self):
        for hours in ("3.00", "6.00"):
            text = _what_this_booking_paid(job_paid(hours, rate="23")).lower()
            for word in (" she ", " her ", " he ", " his "):
                self.assertNotIn(word, f" {text} ")
