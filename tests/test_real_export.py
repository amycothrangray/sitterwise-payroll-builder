"""A regression test against a real Sitterwise export.

Real exports have client names, emails and phone numbers in them, so they are
never committed. Drop one in tests/fixtures/real/ (which git ignores) or point
SITTERWISE_EXPORT at it, and these run. Otherwise they skip.

What they check is that the app's answers on real data stay put: if a change
moves a real payroll total, that should be a decision, not a surprise.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.importer import import_export           # noqa: E402
from payroll.roster import READY, RosterEntry        # noqa: E402
from payroll.rules import Rules                      # noqa: E402
from payroll.run import build_run                    # noqa: E402

REAL_DIR = Path(__file__).parent / "fixtures" / "real"


def find_export() -> Path | None:
    from_env = os.environ.get("SITTERWISE_EXPORT")
    if from_env and Path(from_env).exists():
        return Path(from_env)
    if REAL_DIR.exists():
        candidates = sorted(REAL_DIR.glob("*.xlsx"))
        if candidates:
            return candidates[0]
    return None


EXPORT = find_export()


@unittest.skipIf(EXPORT is None,
                 "no real export available - put one in tests/fixtures/real/")
class TestRealExport(unittest.TestCase):
    """These numbers were checked by hand against the August 2026 export."""

    @classmethod
    def setUpClass(cls):
        cls.rules = Rules.load()
        cls.result = import_export(EXPORT, cls.rules)
        cls.roster = {
            j.caregiver_key: RosterEntry(j.caregiver_key, j.display_name, READY,
                                         source="onpay_import")
            for j in cls.result.jobs if j.caregiver_key}
        cls.payroll = build_run(EXPORT, cls.rules, date(2026, 8, 1), date(2026, 8, 15),
                            roster=cls.roster, import_result=cls.result)

    def test_every_row_reads_without_error(self):
        self.assertEqual(self.result.parse_errors, [])
        self.assertEqual(self.result.missing_columns, [])
        self.assertEqual(self.result.unmapped_columns, [])

    def test_every_paid_job_matches_a_known_rate(self):
        unmatched = [j.booking_id for j in self.payroll.period_jobs
                     if j.tier_key not in ("standard", "three_to_four")]
        self.assertEqual(unmatched, [],
                         "a real job whose rate the app cannot work out")

    def test_the_payroll_balances(self):
        self.assertTrue(self.payroll.reconciliation.balances)
        self.assertEqual(self.payroll.reconciliation.jobs_accounted_for,
                         self.payroll.reconciliation.jobs_paid)

    def test_it_agrees_with_sitterwise_except_where_it_says_why(self):
        for difference in self.payroll.reconciliation.pay_differences:
            self.assertTrue(difference["why"],
                            f"booking {difference['booking_id']} differs with no explanation")

    def test_the_totals_have_not_moved(self):
        # Checked by hand against the August 2026 export, Aug 1-15:
        #   790.25 hrs at $23 = $18,175.75
        #   187.25 hrs at $28 =  $5,243.00
        #   3.25 guarantee hrs =    $83.50
        #   51.25 overtime hrs =   $618.41 premium
        #    1.25 double-time  =    $28.75 premium
        totals = self.payroll.totals()
        expected = {
            "hours_worked": "977.50",
            "straight_pay": "23418.75",
            "guarantee_pay": "83.50",
            "ot_hours": "51.25",
            "ot_premium": "618.41",
            "dt_hours": "1.25",
            "dt_premium": "28.75",
            "tips": "635.00",
            "total_paid": "25326.05",
        }
        for key, value in expected.items():
            self.assertEqual(Decimal(totals[key]), Decimal(value), key)

    def test_the_four_hour_minimum_is_recognised(self):
        topped_up = [j for j in self.payroll.period_jobs if j.minimum_applied]
        self.assertTrue(topped_up, "the 4-hour minimum should apply to some real jobs")
        for job in topped_up:
            self.assertLess(job.hours_worked, self.rules.minimum_hours)
            self.assertEqual(job.hours_paid, self.rules.minimum_hours)

    def test_mileage_is_only_found_on_care_com_jobs(self):
        for job in self.result.jobs:
            if job.mileage_miles:
                self.assertTrue(self.rules.mileage_allowed_on(job.service_type),
                                f"booking {job.booking_id} is a {job.service_type} job")
                self.assertGreaterEqual(Decimal(job.mileage_miles), self.rules.minimum_miles)


if __name__ == "__main__":
    unittest.main(verbosity=2)
