"""Tests for Sitterwise payroll.

Every test states the sum it expects in the comment above it, so the numbers
can be checked by hand without reading any code. They run against
tests/fixtures/test-payroll.xlsx, a deliberately awkward payroll built by
tests/fixtures/make_fixture.py.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.engine import Adjustment, week_start_for                    # noqa: E402
from payroll.importer import import_export                                # noqa: E402
from payroll.roster import NOT_IN_ONPAY, READY, RosterEntry               # noqa: E402
from payroll.rules import Rules                                           # noqa: E402
from payroll.run import build_run, suggest_period                         # noqa: E402
from payroll.store import Store                                           # noqa: E402
from payroll import exports                                               # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "test-payroll.xlsx"
START, END = date(2026, 8, 1), date(2026, 8, 15)


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class PayrollCase(unittest.TestCase):
    """Shared setup: one import, one run, everyone ready in OnPay."""

    rules_overrides: dict = {}
    not_in_onpay: set = set()
    adjustments: list = []

    @classmethod
    def setUpClass(cls):
        if not FIXTURE.exists():
            from tests.fixtures.make_fixture import write
            write(FIXTURE)

    def setUp(self):
        self.rules = Rules.load()
        for path, value in self.rules_overrides.items():
            target = self.rules.data
            *parents, leaf = path.split(".")
            for part in parents:
                target = target[part]
            target[leaf] = value
        self.result = import_export(FIXTURE, self.rules)
        self.roster = {
            j.caregiver_key: RosterEntry(
                j.caregiver_key, j.display_name,
                NOT_IN_ONPAY if j.display_name in self.not_in_onpay else READY,
                onpay_clock_user=f"SW{j.booking_id}", source="onpay_import")
            for j in self.result.jobs if j.caregiver_key
        }
        self.payroll = build_run(FIXTURE, self.rules, START, END, roster=self.roster,
                             adjustments=self.adjustments, import_result=self.result)

    # -- helpers --------------------------------------------------------
    def person(self, name):
        found = next((c for c in self.payroll.caregivers if c.name == name), None)
        self.assertIsNotNone(found, f"{name} is not in this payroll")
        return found

    def codes(self, name=None):
        return {f.code for f in self.payroll.findings
                if name is None or f.caregiver_name == name}


# =====================================================================
# reading the export
# =====================================================================
class TestImport(PayrollCase):

    def test_hours_come_from_the_clock_not_the_hours_column(self):
        # Rosa's three jobs are 5 hours each: 15 hours in total.
        self.assertEqual(self.person("Rosa Delgado").hours_worked, money("15.00"))

    def test_only_completed_and_paid_jobs_are_paid(self):
        statuses = {j.status for j in self.payroll.period_jobs}
        self.assertEqual(statuses, {"completed", "paid"})

    def test_cancelled_and_unclosed_jobs_are_left_out_but_explained(self):
        reasons = self.payroll.reconciliation.exclusions
        self.assertIn("Status is cancelled", reasons)
        self.assertIn("Worked but never closed out in Sitterwise", reasons)

    def test_jobs_outside_the_pay_period_are_not_included(self):
        for job in self.payroll.period_jobs:
            self.assertTrue(START <= job.workday <= END, job.booking_id)

    def test_the_pay_period_is_suggested_not_assumed(self):
        start, end, note = suggest_period(self.result)
        self.assertIsInstance(note, str)
        self.assertTrue(note, "the app should always say how it chose the period")


# =====================================================================
# the rates
# =====================================================================
class TestRates(PayrollCase):

    def test_caregiver_with_only_regular_rate_jobs(self):
        # Rosa Delgado: 15 hrs x $23 = $345.00
        rosa = self.person("Rosa Delgado")
        self.assertEqual(len(rosa.tiers), 1)
        self.assertEqual(rosa.tiers[0].key, "standard")
        self.assertEqual(rosa.tier_hours("standard"), money("15.00"))
        self.assertEqual(rosa.straight_pay, money("345.00"))
        self.assertEqual(rosa.total_paid, money("345.00"))
        self.assertFalse(rosa.uses_multiple_rates)

    def test_caregiver_with_only_three_to_four_child_jobs(self):
        # Ivy Chen: 12 hrs x $28 = $336.00
        ivy = self.person("Ivy Chen")
        self.assertEqual(ivy.tier_hours("three_to_four"), money("12.00"))
        self.assertEqual(ivy.tier_hours("standard"), money("0"))
        self.assertEqual(ivy.total_paid, money("336.00"))

    def test_caregiver_with_both_rates(self):
        # Mona Patel: 6 hrs x $23 = $138.00, plus 4 hrs x $28 = $112.00 -> $250.00
        mona = self.person("Mona Patel")
        self.assertEqual(mona.tier_hours("standard"), money("6.00"))
        self.assertEqual(mona.tier_hours("three_to_four"), money("4.00"))
        self.assertEqual(mona.tier_pay("standard"), money("138.00"))
        self.assertEqual(mona.tier_pay("three_to_four"), money("112.00"))
        self.assertEqual(mona.total_paid, money("250.00"))
        self.assertTrue(mona.uses_multiple_rates)

    def test_pay_that_matches_no_known_rate_blocks_payroll(self):
        # June Salter was paid $99.99 for 4 hours, which is neither $23 nor $28.
        self.assertIn("rate_not_recognised", self.codes("June Salter"))
        blocked = [f for f in self.payroll.findings
                   if f.caregiver_name == "June Salter" and f.level == "stop"]
        self.assertTrue(blocked)

    def test_the_app_says_out_loud_that_it_guessed_the_rate(self):
        # There is no children count in the export, so every tier is inferred.
        self.assertIn("tier_inferred", self.codes())
        for job in self.payroll.period_jobs:
            if job.tier_key in ("standard", "three_to_four"):
                self.assertTrue(job.rate_basis.startswith("inferred_from_pay"))


# =====================================================================
# the four-hour minimum
# =====================================================================
class TestMinimumBooking(PayrollCase):

    def test_short_job_is_topped_up_to_four_hours(self):
        # Belle Cruz worked 2.5 hrs and was paid for 4.
        #   worked:    2.5 x $23 = $57.50
        #   guarantee: 1.5 x $23 = $34.50
        #   total                 = $92.00
        belle = self.person("Belle Cruz")
        self.assertEqual(belle.hours_worked, money("2.50"))
        self.assertEqual(belle.guarantee_hours, money("1.50"))
        self.assertEqual(belle.straight_pay, money("57.50"))
        self.assertEqual(belle.guarantee_pay, money("34.50"))
        self.assertEqual(belle.total_paid, money("92.00"))

    def test_guarantee_hours_do_not_count_toward_overtime(self):
        # Only hours actually worked can trigger overtime.
        belle = self.person("Belle Cruz")
        week = belle.weeks[0]
        self.assertEqual(week.hours_worked, money("2.50"))
        self.assertEqual(belle.ot_hours, money("0"))


# =====================================================================
# overtime
# =====================================================================
class TestOvertime(PayrollCase):

    def test_daily_overtime_at_a_single_rate(self):
        # Dana Reyes: 10 hrs on Aug 3 and 4 hrs on Aug 6 = 14 hrs x $23 = $322.00
        #   overtime: 2 hrs over 8 on Aug 3
        #   premium:  2 x 0.5 x $23 = $23.00
        #   total:    $345.00
        dana = self.person("Dana Reyes")
        self.assertEqual(dana.hours_worked, money("14.00"))
        self.assertEqual(dana.straight_pay, money("322.00"))
        self.assertEqual(dana.ot_hours, money("2.00"))
        self.assertEqual(dana.ot_premium, money("23.00"))
        self.assertEqual(dana.total_paid, money("345.00"))

    def test_overtime_across_two_rates_uses_the_weighted_average(self):
        # Tess Okafor, all on Aug 4:
        #   5 hrs x $23 = $115.00
        #   5 hrs x $28 = $140.00
        #   straight    = $255.00 over 10 hrs -> regular rate $25.50
        #   overtime    = 2 hrs over 8
        #   premium     = 2 x 0.5 x $25.50 = $25.50
        #   total       = $280.50
        tess = self.person("Tess Okafor")
        self.assertEqual(tess.straight_pay, money("255.00"))
        self.assertEqual(tess.weeks[0].regular_rate, Decimal("25.5000"))
        self.assertEqual(tess.ot_hours, money("2.00"))
        self.assertEqual(tess.ot_premium, money("25.50"))
        self.assertEqual(tess.total_paid, money("280.50"))
        self.assertIn("mixed_rate_overtime", self.codes("Tess Okafor"))

    def test_double_time_past_twelve_hours(self):
        # Priya Raman: 13 hrs on Aug 5 at $23
        #   straight:    13 x $23 = $299.00
        #   overtime:    4 hrs (8 to 12)  -> 4 x 0.5 x $23 = $46.00
        #   double time: 1 hr (past 12)   -> 1 x 1.0 x $23 = $23.00
        #   total:       $368.00
        priya = self.person("Priya Raman")
        self.assertEqual(priya.straight_pay, money("299.00"))
        self.assertEqual(priya.ot_hours, money("4.00"))
        self.assertEqual(priya.ot_premium, money("46.00"))
        self.assertEqual(priya.dt_hours, money("1.00"))
        self.assertEqual(priya.dt_premium, money("23.00"))
        self.assertEqual(priya.total_paid, money("368.00"))
        self.assertIn("double_time", self.codes("Priya Raman"))

    def test_seventh_consecutive_day(self):
        # Ruth Ozeki worked Mon Aug 3 to Sun Aug 9, 4 hrs a day = 28 hrs x $23 = $644.00
        #   Aug 9 is her seventh day in a row, so all 4 of its hours are at 1.5x
        #   premium: 4 x 0.5 x $23 = $46.00
        #   total:   $690.00
        ruth = self.person("Ruth Ozeki")
        self.assertEqual(ruth.hours_worked, money("28.00"))
        self.assertEqual(ruth.straight_pay, money("644.00"))
        self.assertEqual(ruth.ot_hours, money("4.00"))
        self.assertEqual(ruth.ot_premium, money("46.00"))
        self.assertEqual(ruth.total_paid, money("690.00"))
        seventh = [d for w in ruth.weeks for d in w.days if d.is_seventh_consecutive_day]
        self.assertEqual([d.day for d in seventh], [date(2026, 8, 9)])

    def test_weekly_threshold_is_warned_about_even_when_it_is_switched_off(self):
        # Cass Moreau: Mon Aug 3 to Sat Aug 8, 7 hrs a day = 42 hrs in one pay
        # week, never over 8 in a day.
        # Weekly overtime is off, so no premium - but the app must say so.
        cass = self.person("Cass Moreau")
        self.assertEqual(cass.hours_worked, money("42.00"))
        self.assertEqual(cass.ot_hours, money("0"))
        self.assertEqual(cass.total_paid, money("966.00"))
        self.assertIn("weekly_threshold_not_paid", self.codes("Cass Moreau"))

    def test_a_bonus_next_to_overtime_is_flagged_not_guessed_at(self):
        # Lena Voss: 9 hrs at $23 = $207.00, 1 hr overtime = $11.50,
        # plus a $15 lifesaver bonus -> $233.50
        lena = self.person("Lena Voss")
        self.assertEqual(lena.ot_hours, money("1.00"))
        self.assertEqual(lena.ot_premium, money("11.50"))
        self.assertEqual(lena.bonus, money("15.00"))
        self.assertEqual(lena.total_paid, money("233.50"))
        self.assertIn("bonus_with_overtime", self.codes("Lena Voss"))

    def test_workweek_boundary(self):
        # Sitterwise pays Monday to Sunday, so Aug 3 (a Monday) starts its own
        # week and Aug 9 (the Sunday) is the last day of it.
        self.assertEqual(week_start_for(date(2026, 8, 3), 0), date(2026, 8, 3))
        self.assertEqual(week_start_for(date(2026, 8, 9), 0), date(2026, 8, 3))
        self.assertEqual(week_start_for(date(2026, 8, 10), 0), date(2026, 8, 10))
        self.assertEqual(self.rules.workweek_start_index, 0)

    def test_a_monday_workweek_is_not_treated_as_no_setting(self):
        # Monday is index 0, which is falsy. Code that fell back with `or`
        # silently rebuilt every week as a Sunday week regardless of the
        # setting, which quietly broke weekly overtime and the seventh-day
        # rule. Ruth's seven days must land in one week, not split across two.
        ruth = self.person("Ruth Ozeki")
        self.assertEqual(len(ruth.weeks), 1)
        self.assertEqual(ruth.weeks[0].week_start, date(2026, 8, 3))
        self.assertEqual(ruth.weeks[0].week_end, date(2026, 8, 9))


class TestPersonalAttendantRules(PayrollCase):
    """The same payroll under 9/45 rules, changed only in the settings file."""

    rules_overrides = {
        "overtime.daily_overtime": {"enabled": True, "threshold_hours": 9, "multiplier": 1.5},
        "overtime.daily_double_time": {"enabled": False, "threshold_hours": 12, "multiplier": 2.0},
    }

    def test_daily_overtime_starts_at_nine_hours(self):
        # Dana Reyes' 10-hour day now yields 1 hour of overtime, not 2.
        dana = self.person("Dana Reyes")
        self.assertEqual(dana.ot_hours, money("1.00"))
        self.assertEqual(dana.ot_premium, money("11.50"))
        self.assertEqual(dana.total_paid, money("333.50"))

    def test_no_double_time(self):
        # Priya's 13-hour day: 4 hours of overtime, nothing at double time.
        priya = self.person("Priya Raman")
        self.assertEqual(priya.dt_hours, money("0"))
        self.assertEqual(priya.dt_premium, money("0"))
        self.assertEqual(priya.ot_hours, money("4.00"))
        self.assertEqual(priya.total_paid, money("345.00"))

    def test_nothing_else_had_to_change(self):
        # The rates, minimum and reimbursements are untouched by the switch.
        self.assertEqual(self.person("Rosa Delgado").total_paid, money("345.00"))
        self.assertEqual(self.person("Belle Cruz").total_paid, money("92.00"))


# =====================================================================
# tips, mileage and reimbursements
# =====================================================================
class TestExtraPayments(PayrollCase):

    def test_tip_is_kept_separate_from_wages(self):
        # Nina Alvarez: 4 hrs x $23 = $92.00, plus a $75 tip = $167.00
        nina = self.person("Nina Alvarez")
        self.assertEqual(nina.straight_pay, money("92.00"))
        self.assertEqual(nina.tips, money("75.00"))
        self.assertEqual(nina.taxable_earnings, money("167.00"))
        self.assertEqual(nina.reimbursements, money("0"))

    def test_tips_never_raise_the_overtime_rate(self):
        # A tip is the customer's money, not payment for hours worked.
        nina = self.person("Nina Alvarez")
        self.assertEqual(nina.weeks[0].regular_rate, Decimal("23.0000"))

    def test_mileage_on_a_care_com_job(self):
        # Gwen Mabry: 5 hrs x $23 = $115.00 wages, plus 40 miles at $0.76 = $30.40
        gwen = self.person("Gwen Mabry")
        self.assertEqual(gwen.mileage_miles, Decimal("40"))
        self.assertEqual(gwen.mileage_amount, money("30.40"))
        self.assertEqual(gwen.taxable_earnings, money("115.00"))
        self.assertEqual(gwen.reimbursements, money("30.40"))
        self.assertEqual(gwen.total_paid, money("145.40"))

    def test_mileage_is_not_taxable_and_is_not_in_the_regular_rate(self):
        gwen = self.person("Gwen Mabry")
        self.assertEqual(gwen.weeks[0].regular_rate, Decimal("23.0000"))
        self.assertNotIn(gwen.mileage_amount, [gwen.taxable_earnings])

    def test_a_reimbursement_that_is_not_mileage(self):
        # Sofia Bright: $22.50 is not a whole number of miles at any rate.
        sofia = self.person("Sofia Bright")
        self.assertEqual(sofia.mileage_amount, money("0"))
        self.assertEqual(sofia.other_reimbursement, money("22.50"))
        self.assertEqual(sofia.total_paid, money("114.50"))
        self.assertIn("reimbursements_no_description", self.codes())

    def test_both_a_tip_and_a_reimbursement(self):
        # Hana Kimura: 6 hrs x $28 = $168.00, $50 tip, 54 miles = $41.04
        hana = self.person("Hana Kimura")
        self.assertEqual(hana.straight_pay, money("168.00"))
        self.assertEqual(hana.tips, money("50.00"))
        self.assertEqual(hana.mileage_amount, money("41.04"))
        self.assertEqual(hana.taxable_earnings, money("218.00"))
        self.assertEqual(hana.reimbursements, money("41.04"))
        self.assertEqual(hana.total_paid, money("259.04"))

    def test_mileage_on_a_job_that_does_not_qualify_is_not_paid_as_mileage(self):
        # Faye Nakamura claimed exactly 40 miles - on a Babysitter job.
        # Mileage is Care.com only, so it must not go through as mileage.
        faye = self.person("Faye Nakamura")
        self.assertEqual(faye.mileage_amount, money("0"))
        self.assertEqual(faye.other_reimbursement, money("30.40"))
        self.assertIn("mileage_not_allowed", self.codes("Faye Nakamura"))

    def test_a_care_com_claim_under_forty_miles_is_flagged(self):
        nadia = self.person("Nadia Okoro")
        self.assertEqual(nadia.mileage_amount, money("0"))
        self.assertIn("mileage_under_minimum", self.codes("Nadia Okoro"))

    def test_mileage_larger_than_the_commission_is_flagged(self):
        # Della Cruz: 100 miles = $76.00 on a job Sitterwise made $48.00 on.
        self.assertIn("mileage_exceeds_commission", self.codes("Della Cruz"))

    def test_the_mileage_rate_follows_the_date_of_the_job(self):
        # The IRS rate changed mid-2026: $0.725 to June 30, $0.76 from July 1.
        self.assertEqual(self.rules.mileage_rate_for(date(2026, 3, 10)), Decimal("0.7250"))
        self.assertEqual(self.rules.mileage_rate_for(date(2026, 8, 10)), Decimal("0.7600"))
        march = next(j for j in self.result.jobs if j.workday == date(2026, 3, 10))
        self.assertEqual(march.mileage_miles, Decimal("40"))   # $29.00 / $0.725


# =====================================================================
# the payroll check
# =====================================================================
class TestMileageClaimLimits(PayrollCase):
    """The same payroll with a claim cap and a form threshold switched on."""

    rules_overrides = {
        "reimbursements.mileage": {
            "detect_from_reimbursement": True,
            "whole_mile_tolerance": 0.005,
            "minimum_miles": 40,
            "eligible_service_types": ["Corporate (Invoiced)"],
            "maximum_claimable_miles": 50,
            "form_required_above_miles": 50,
            "rates_by_effective_date": [
                {"effective": "2026-01-01", "rate": 0.725},
                {"effective": "2026-07-01", "rate": 0.76},
            ],
        },
    }

    def test_a_claim_over_the_cap_is_flagged(self):
        # Hana Kimura claimed 54 miles; the cap here is 50.
        self.assertIn("mileage_over_cap", self.codes("Hana Kimura"))

    def test_a_claim_under_the_cap_is_not_flagged(self):
        # Gwen Mabry claimed 40 miles.
        self.assertNotIn("mileage_over_cap", self.codes("Gwen Mabry"))

    def test_a_long_claim_is_told_to_have_a_form(self):
        self.assertIn("mileage_needs_form", self.codes("Della Cruz"))

    def test_the_claim_is_still_paid_in_full(self):
        # Flagging is not the same as refusing. Amy decides.
        hana = self.person("Hana Kimura")
        self.assertEqual(hana.mileage_amount, money("41.04"))

    def test_no_limits_means_no_flags(self):
        # The shipped default has both limits switched off.
        plain = Rules.load()
        self.assertIsNone(plain.maximum_claimable_miles)
        self.assertIsNone(plain.form_required_above_miles)


class TestChecks(PayrollCase):

    not_in_onpay = {"Opal Grant"}

    def test_a_duplicated_booking_blocks_payroll(self):
        self.assertIn("duplicate_booking", self.codes("Cleo Barnes"))

    def test_overlapping_shifts_block_payroll(self):
        self.assertIn("overlapping_shifts", self.codes("Ada Whitlow"))

    def test_a_job_with_no_caregiver_blocks_payroll(self):
        self.assertIn("missing_caregiver", self.codes())

    def test_a_caregiver_not_set_up_in_onpay_blocks_payroll(self):
        self.assertIn("not_in_onpay", self.codes("Opal Grant"))
        self.assertFalse(self.payroll.summary["can_finalize"])

    def test_test_data_is_caught(self):
        self.assertIn("test_booking", self.codes())

    def test_systemic_gaps_are_raised_once_not_per_job(self):
        # Otherwise a gap in the export drowns out the findings that need action.
        for code in ("tier_inferred", "reimbursements_no_description"):
            matching = [f for f in self.payroll.findings if f.code == code]
            self.assertEqual(len(matching), 1, f"{code} should be raised exactly once")

    def test_every_caregiver_lands_in_exactly_one_state(self):
        summary = self.payroll.summary
        self.assertEqual(
            summary["ready"] + summary["needs_review"] + summary["blocked"],
            len(self.payroll.caregivers))

    def test_findings_are_written_in_plain_english(self):
        for finding in self.payroll.findings:
            self.assertTrue(finding.title and finding.detail, finding.code)
            for jargon in ("exception reconciliation", "compensation classification",
                           "traceback", "None", "null"):
                self.assertNotIn(jargon, finding.title)


# =====================================================================
# proving nothing went missing
# =====================================================================
class TestReconciliation(PayrollCase):

    def test_every_job_is_either_paid_or_explained(self):
        recon = self.payroll.reconciliation
        self.assertEqual(recon.jobs_in_period, recon.jobs_paid + recon.jobs_excluded)
        self.assertEqual(recon.jobs_accounted_for, recon.jobs_paid)
        self.assertTrue(recon.balances)

    def test_the_app_agrees_with_sitterwise_on_every_job(self):
        # The fixture is priced the way Sitterwise prices things, except for
        # June Salter, whose pay matches no known rate.
        unexplained = [d for d in self.payroll.reconciliation.pay_differences
                       if d["caregiver"] != "June Salter"]
        self.assertEqual(unexplained, [])

    def test_the_totals_are_the_sum_of_the_caregivers(self):
        totals = self.payroll.totals()
        by_hand = sum((c.total_paid for c in self.payroll.caregivers), Decimal("0"))
        self.assertEqual(money(totals["total_paid"]), money(by_hand))

    def test_reimbursements_are_kept_out_of_taxable_pay(self):
        totals = self.payroll.totals()
        self.assertEqual(
            money(totals["total_paid"]),
            money(Decimal(totals["taxable_earnings"]) + Decimal(totals["reimbursements"])))


# =====================================================================
# manual corrections
# =====================================================================
class TestManualAdjustments(PayrollCase):

    def test_a_corrected_tip_shows_up_and_is_marked(self):
        # Vera Lund earned $115.00. A $40 cash tip is added by hand.
        before = self.person("Vera Lund")
        self.assertEqual(before.total_paid, money("115.00"))

        adjustment = Adjustment(
            id="t1", caregiver_key="vera lund", kind="tip", booking_id="95500",
            original_value="0.00", new_value="40.00",
            reason="Family confirmed a cash tip", created_at="2026-08-16T10:00:00",
        )
        after = build_run(FIXTURE, self.rules, START, END, roster=self.roster,
                          adjustments=[adjustment], import_result=self.result)
        vera = next(c for c in after.caregivers if c.name == "Vera Lund")

        self.assertEqual(vera.tips, money("40.00"))
        self.assertEqual(vera.total_paid, money("155.00"))
        self.assertEqual(len(vera.adjustments), 1)
        self.assertIn("manual_adjustment",
                      {f.code for f in after.findings if f.caregiver_name == "Vera Lund"})

    def test_the_original_value_is_never_lost(self):
        adjustment = Adjustment(
            id="t2", caregiver_key="rosa delgado", kind="hours", booking_id="90001",
            original_value="5.00", new_value="6.00", reason="Stayed an extra hour",
            created_at="2026-08-16T10:00:00")
        after = build_run(FIXTURE, self.rules, START, END, roster=self.roster,
                          adjustments=[adjustment], import_result=self.result)
        rosa = next(c for c in after.caregivers if c.name == "Rosa Delgado")
        self.assertEqual(rosa.adjustments[0].original_value, "5.00")
        self.assertEqual(rosa.hours_worked, money("16.00"))
        self.assertEqual(rosa.total_paid, money("368.00"))     # 16 x $23
        job = next(j for j in rosa.jobs if j.booking_id == "90001")
        self.assertTrue(any("Manual adjustment" in n for n in job.import_notes))

    def test_the_imported_data_is_never_changed(self):
        adjustment = Adjustment(
            id="t3", caregiver_key="rosa delgado", kind="hours", booking_id="90001",
            original_value="5.00", new_value="6.00", reason="test",
            created_at="2026-08-16T10:00:00")
        build_run(FIXTURE, self.rules, START, END, roster=self.roster,
                  adjustments=[adjustment], import_result=self.result)
        untouched = next(j for j in self.result.jobs if j.booking_id == "90001")
        self.assertEqual(untouched.hours_worked, money("5.00"))


# =====================================================================
# history and locking
# =====================================================================
class TestStoreAndHistory(PayrollCase):

    def test_a_finished_payroll_cannot_pay_the_same_job_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.sqlite3")
            run_id = store.create_run("Aug 1-15, 2026", START, END, self.rules.snapshot(),
                                      FIXTURE.name, "abc", str(FIXTURE))
            store.finalize_run(run_id, [j.booking_id for j in self.payroll.period_jobs], {})

            again = build_run(FIXTURE, self.rules, START, END, roster=self.roster,
                              previously_paid=store.previously_paid(), import_result=self.result)
            already = [f for f in again.findings if f.code == "already_paid"]
            self.assertTrue(already)
            self.assertEqual(already[0].level, "stop")
            store.close()

    def test_unlocking_is_deliberate_and_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.sqlite3")
            run_id = store.create_run("Aug 1-15, 2026", START, END, self.rules.snapshot(),
                                      FIXTURE.name, "abc", str(FIXTURE))
            store.finalize_run(run_id, ["90001"], {})
            self.assertEqual(store.get_run(run_id)["status"], "finalized")
            store.unlock_run(run_id, "wrong pay period")
            self.assertEqual(store.get_run(run_id)["status"], "open")
            self.assertEqual(store.previously_paid(), {})
            actions = [e["action"] for e in store.audit_trail()]
            self.assertIn("run_unlocked", actions)
            reasons = [e["detail"] for e in store.audit_trail() if e["action"] == "run_unlocked"]
            self.assertEqual(reasons, ["wrong pay period"])
            store.close()

    def test_a_finished_payroll_keeps_the_rules_it_was_run_with(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "t.sqlite3")
            run_id = store.create_run("Aug 1-15, 2026", START, END, self.rules.snapshot(),
                                      FIXTURE.name, "abc", str(FIXTURE))
            snapshot = Rules.from_snapshot(
                __import__("json").loads(store.get_run(run_id)["rules_snapshot"]))
            self.assertEqual(snapshot.daily_ot_threshold, self.rules.daily_ot_threshold)
            store.close()


# =====================================================================
# exports
# =====================================================================
class TestExports(PayrollCase):

    def test_every_export_is_produced_and_has_content(self):
        for item in exports.all_exports(self.payroll, self.roster):
            self.assertTrue(item["content"].strip(), item["key"])
            self.assertTrue(item["filename"].endswith(".csv"))

    def test_the_onpay_grid_has_a_row_for_every_caregiver(self):
        csv_text = exports.onpay_entry_csv(self.payroll, self.roster)
        self.assertEqual(len(csv_text.strip().splitlines()) - 1, len(self.payroll.caregivers))

    def test_the_summary_carries_the_reconciliation(self):
        text = exports.payroll_summary_csv(self.payroll)
        self.assertIn("Jobs accounted for in payroll", text)
        self.assertIn("Expected total employee payments", text)

    def test_the_onpay_import_file_leaves_nobody_out_silently(self):
        roster = dict(self.roster)
        first = next(iter(roster))
        roster[first] = RosterEntry(first, roster[first].display_name, READY,
                                    onpay_clock_user="")
        _, skipped = exports.onpay_import_csv(self.payroll, roster)
        self.assertIn(roster[first].display_name, skipped)

    def test_the_detail_export_shows_where_each_rate_came_from(self):
        text = exports.payroll_detail_csv(self.payroll)
        self.assertIn("Worked out from the amount paid", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
