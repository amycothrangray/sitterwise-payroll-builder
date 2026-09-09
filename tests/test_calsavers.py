"""Reading the CalSavers contributions out of an OnPay register.

Money withheld for CalSavers has to be sent to the CalSavers portal within
seven days of the pay date, typed in by hand. The figures come off OnPay's
payroll register - and the register's run totals sit at the end of the
document, inside what looks like the last person's section. Read carelessly,
the last caregiver appears to have a deduction equal to everybody's put
together: a contribution paid for somebody who has none, and a total twice
what it should be. That very nearly happened on 9 September 2026.

The registers below are made up. A real one carries every employee's pay.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.calsavers import read_contributions                        # noqa: E402


def person(name, net, deduction=None, check_date="9/11/2026"):
    lines = [name, "Employee No.:", "Check No. DD900001", "Run ID 572",
             f"Check Date {check_date}", "Period 8/31/2026 - 9/6/2026",
             f"Net Pay ${net}", "Regular $23.000 4.000 $92.00"]
    if deduction is not None:
        lines.append(f"CalSavers ${deduction}")
    return lines


def run_summary(deductions):
    """The totals OnPay puts at the end, which land inside the last section."""
    return ["Earnings", "$12,043.60", "Benefits", "$0.00",
            "Deductions", f"${deductions}", "Net", "$10,327.62"]


def register(*blocks, deductions="28.52", summary=True):
    lines = []
    for block in blocks:
        lines += block
    if summary:
        lines += run_summary(deductions)
    return "\n".join(lines)


class ReadingARegister(unittest.TestCase):
    def test_it_finds_who_has_a_contribution(self):
        text = register(person("Tess Okafor", "77.33", "6.44"),
                        person("Nia Vance", "148.83", "14.72"),
                        person("Ada Whitlow", "76.41", "7.36"))
        got = read_contributions(text)
        self.assertEqual([(p.name, str(p.amount)) for p in got.people],
                         [("Tess Okafor", "6.44"), ("Nia Vance", "14.72"),
                          ("Ada Whitlow", "7.36")])
        self.assertEqual(got.total, Decimal("28.52"))
        self.assertTrue(got.ok)

    def test_somebody_without_one_is_left_out(self):
        text = register(person("Tess Okafor", "77.33", "6.44"),
                        person("Cara Lin", "500.00"),
                        person("Nia Vance", "148.83", "14.72"),
                        person("Ada Whitlow", "76.41", "7.36"))
        got = read_contributions(text)
        self.assertNotIn("Cara Lin", [p.name for p in got.people])

    def test_the_run_total_is_not_mistaken_for_the_last_persons_deduction(self):
        """The trap. The summary lands inside the last section, so the last
        caregiver looks like they have everybody's deductions put together."""
        text = register(person("Tess Okafor", "77.33", "6.44"),
                        person("Nia Vance", "148.83", "14.72"),
                        person("Ada Whitlow", "76.41", "7.36"),
                        person("Mika Bell", "170.71"))     # has none
        got = read_contributions(text)
        self.assertNotIn("Mika Bell", [p.name for p in got.people])
        self.assertEqual(len(got.people), 3)
        self.assertEqual(got.total, Decimal("28.52"))

    def test_one_person_whose_contribution_is_the_whole_run(self):
        text = register(person("Tess Okafor", "77.33", "28.52"))
        got = read_contributions(text)
        self.assertEqual([(p.name, str(p.amount)) for p in got.people],
                         [("Tess Okafor", "28.52")])
        self.assertEqual(got.total, Decimal("28.52"))

    def test_the_pay_date_and_period_come_across(self):
        got = read_contributions(register(person("Tess Okafor", "77.33", "28.52")))
        self.assertEqual(got.pay_date, "9/11/2026")
        self.assertEqual(got.period, "8/31/2026 - 9/6/2026")

    def test_a_deduction_under_another_name_is_still_found(self):
        text = register(person("Tess Okafor", "77.33", "28.52"))
        text = text.replace("CalSavers $", "CalSavers Roth $")
        self.assertEqual(len(read_contributions(text).people), 1)


class WhenItDoesNotAddUp(unittest.TestCase):
    """A list that does not reconcile is worse than no list - it would be
    typed into a retirement account."""

    def test_amounts_that_do_not_match_the_run_total_are_refused(self):
        text = register(person("Tess Okafor", "77.33", "6.44"),
                        person("Nia Vance", "148.83", "14.72"),
                        deductions="28.52")
        got = read_contributions(text)
        self.assertEqual(got.people, [])
        self.assertFalse(got.ok)
        self.assertTrue(any("does not add up" in p for p in got.problems))

    def test_it_says_both_figures_so_the_gap_can_be_found(self):
        text = register(person("Tess Okafor", "77.33", "6.44"), deductions="28.52")
        problem = read_contributions(text).problems[0]
        self.assertIn("6.44", problem)
        self.assertIn("28.52", problem)

    def test_a_register_with_no_run_total_is_refused(self):
        text = register(person("Tess Okafor", "77.33", "6.44"), summary=False)
        got = read_contributions(text)
        self.assertEqual(got.people, [])
        self.assertTrue(any("nothing to check" in p for p in got.problems))

    def test_something_that_is_not_a_register_is_refused(self):
        got = read_contributions("Just some words.\nNothing to do with payroll.")
        self.assertEqual(got.people, [])
        self.assertTrue(any("does not look like" in p for p in got.problems))

    def test_a_run_where_nobody_contributes_is_not_an_error(self):
        text = register(person("Tess Okafor", "77.33"),
                        person("Nia Vance", "148.83"), deductions="0.00")
        got = read_contributions(text)
        self.assertEqual(got.people, [])
        self.assertEqual(got.problems, [])


if __name__ == "__main__":
    unittest.main()
