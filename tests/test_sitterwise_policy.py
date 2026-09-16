"""Owner-confirmed import policy and hand-calculated incentive overtime."""
import csv
import io
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from payroll import exports
from payroll.roster import READY, RosterEntry
from payroll.rules import Rules
from payroll.run import build_run
from tests.fixtures.make_fixture import booking


class SitterwisePolicy(unittest.TestCase):
    def setUp(self):
        self.rules = Rules.load()
        self.rules.data['reimbursements']['mileage'].update(
            use_exported_amount_only=True, review_details=False)
        self.rules.data['bonuses'].update(
            overtime_method='california_flat_sum', review_duplicate_columns=True)

    def row(self, hours='9', day=3, lifesaver='15', bonus='0', rate='23', **kw):
        row = booking('Example Caregiver', date(2026, 8, day), '08:00', hours,
                      rate, lifesaver=lifesaver, bonus=bonus, **kw)
        row['Caregiver ID'] = '123'
        return row

    def run_rows(self, rows, end=9):
        entry = RosterEntry('example caregiver', 'Example Caregiver', READY,
                            onpay_clock_user='')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bookings.csv'
            with path.open('w', newline='') as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            run = build_run(path, self.rules, date(2026, 8, 3), date(2026, 8, end),
                            roster={entry.caregiver_key: entry})
        return run, entry

    def test_caregiver_id_is_used_when_clock_column_is_blank(self):
        run, entry = self.run_rows([self.row('4', lifesaver='0')])
        text, skipped = exports.onpay_import_csv(run, {entry.caregiver_key: entry})
        self.assertFalse(skipped)
        self.assertEqual({r['emp_num'] for r in csv.DictReader(io.StringIO(text))}, {'123'})

    def test_mileage_amount_does_not_depend_on_distance_rate_or_approval(self):
        row = self.row('4', lifesaver='0', reimbursement='56.04', mileage_amount='41.04')
        row.update({'Round Trip Miles': 'not available', 'Payable Miles': 'unknown',
                    'Mileage Approved Miles': '', 'Mileage Approval Status': 'pending',
                    'Reimbursement Description': 'Parking and mileage'})
        run, _ = self.run_rows([row])
        c = run.caregivers[0]
        self.assertEqual(c.mileage_amount, Decimal('41.04'))
        self.assertEqual(c.other_reimbursement, Decimal('15.00'))
        self.assertEqual(c.total_paid, Decimal('148.04'))
        self.assertFalse(any(f.code.startswith('mileage_') for f in run.findings))
        self.assertIsNone(c.jobs[0].mileage_miles)

    def test_mileage_in_reimbursement_description_uses_the_dollar_amount(self):
        row = self.row('4', lifesaver='0', reimbursement='41.04',
                       mileage_amount='0', reimbursement_note='mileage')
        run, _ = self.run_rows([row])
        self.assertEqual(run.caregivers[0].mileage_amount, Decimal('41.04'))
        self.assertEqual(run.caregivers[0].reimbursements, Decimal('41.04'))

    def test_miles_alone_do_not_invent_a_reimbursement(self):
        row = self.row('4', lifesaver='0', round_trip_miles='500',
                       payable_miles='460', mileage_amount='0')
        run, _ = self.run_rows([row])
        self.assertEqual(run.caregivers[0].reimbursements, Decimal('0.00'))

    def test_unlabelled_reimbursement_is_still_paid_without_guessing_miles(self):
        run, _ = self.run_rows([self.row('4', lifesaver='0', reimbursement='41.04')])
        c = run.caregivers[0]
        self.assertEqual(c.mileage_amount, Decimal('0.00'))
        self.assertEqual(c.other_reimbursement, Decimal('41.04'))

    def test_one_lifesaver_bonus_with_daily_overtime(self):
        # 9 x 23 + 1 x 11.50 + 15 + (15 / 8 x 1.5 x 1) = 236.31.
        run, entry = self.run_rows([self.row()])
        c = run.caregivers[0]
        self.assertEqual(c.total_paid, Decimal('236.31'))
        self.assertEqual(c.ot_premium, Decimal('14.31'))
        self.assertEqual(c.weeks[0].bonus_ot_premium, Decimal('2.81'))
        self.assertNotIn('bonus_with_overtime', {f.code for f in run.findings})
        rows = exports.onpay_pay_rows(c, '123', exports.load_onpay_mapping())
        self.assertTrue(any('Lifesaver incentive overtime $2.81' in r['note'] for r in rows))
        text, skipped = exports.onpay_import_csv(run, {entry.caregiver_key: entry})
        self.assertFalse(skipped)
        imported = sum((Decimal(r['cash_amount']) if r['cash_amount'] else
                        Decimal(r['hours'] or 0) * Decimal(r['rate'] or 0)
                        for r in csv.DictReader(io.StringIO(text))), Decimal('0'))
        self.assertEqual(imported, c.total_paid)

    def test_double_time_uses_full_bonus_multiplier(self):
        # Base: 13 x 23 + 4 x 11.50 + 1 x 23 = 368.
        # Bonus: 15 + (15 / 8 x 1.5 x 4) + (15 / 8 x 2 x 1) = 30.
        run, _ = self.run_rows([self.row('13')])
        c = run.caregivers[0]
        self.assertEqual(c.total_paid, Decimal('398.00'))
        self.assertEqual(c.weeks[0].bonus_ot_premium, Decimal('11.25'))
        self.assertEqual(c.weeks[0].bonus_dt_premium, Decimal('3.75'))

    def test_guarantee_hours_do_not_dilute_the_bonus_rate(self):
        run, _ = self.run_rows([self.row('2.5', lifesaver='0'), self.row(day=4)])
        c = run.caregivers[0]
        self.assertEqual(c.guarantee_hours, Decimal('1.50'))
        self.assertEqual(c.weeks[0].bonus_regular_hours, Decimal('10.50'))
        self.assertEqual(c.weeks[0].bonus_ot_premium, Decimal('2.14'))

    def test_weekly_overtime_is_counted_once(self):
        self.rules.data['overtime']['weekly_overtime']['enabled'] = True
        rows = [self.row('8', day=d, lifesaver='0') for d in range(3, 8)]
        rows.append(self.row('5', day=8))
        run, _ = self.run_rows(rows)
        c = run.caregivers[0]
        self.assertEqual(c.ot_hours, Decimal('5.00'))
        self.assertEqual(c.weeks[0].bonus_regular_hours, Decimal('40.00'))
        self.assertEqual(c.weeks[0].bonus_ot_premium, Decimal('2.81'))

    def test_bonus_is_not_spread_to_another_week(self):
        run, _ = self.run_rows([self.row(), self.row(day=10, lifesaver='0')], end=16)
        self.assertEqual([w.bonus_ot_premium for w in run.caregivers[0].weeks],
                         [Decimal('2.81'), Decimal('0')])

    def test_two_bonus_columns_block_the_export_until_resolved(self):
        run, entry = self.run_rows([self.row(bonus='15')])
        self.assertIn('bonus_columns_overlap', {f.code for f in run.findings})
        _, skipped = exports.onpay_import_csv(run, {entry.caregiver_key: entry})
        self.assertIn('Example Caregiver', skipped)

    def test_no_overtime_adds_no_bonus_premium(self):
        run, _ = self.run_rows([self.row('3.5')])
        c = run.caregivers[0]
        self.assertEqual(c.total_paid, Decimal('107.00'))
        self.assertEqual(c.premium_pay, Decimal('0.00'))

    def test_mixed_wage_rates_and_flat_bonus_are_calculated_separately(self):
        first = self.row('5', rate='28', lifesaver='0')
        second = self.row('4')
        second['Start Time'] = '13:00'
        second['End Time'] = '17:00'
        run, _ = self.run_rows([first, second])
        c = run.caregivers[0]
        # Wages 232 / 9 = 25.7778; half-time 12.89 + bonus premium 2.81.
        self.assertEqual(c.ot_premium, Decimal('15.70'))
        self.assertEqual(c.total_paid, Decimal('262.70'))

    def test_bonus_without_regular_hours_is_blocked_not_divided_by_zero(self):
        self.rules.data['overtime']['daily_overtime']['threshold_hours'] = 0
        run, _ = self.run_rows([self.row()])
        self.assertIn('bonus_regular_hours_missing', {f.code for f in run.findings})

    def test_old_rule_snapshots_keep_their_original_bonus_calculation(self):
        self.rules.data['bonuses'].pop('overtime_method')
        run, _ = self.run_rows([self.row()])
        self.assertEqual(run.caregivers[0].total_paid, Decimal('233.50'))


if __name__ == '__main__':
    unittest.main()
