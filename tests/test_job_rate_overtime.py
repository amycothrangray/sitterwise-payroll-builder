"""Job-rate overtime examples, independently calculated from hours and rates."""
import csv
import io
import unittest
from decimal import Decimal

from payroll import exports
from payroll.rules import Rules, DEFAULT_RULES_PATH
from tests import test_sitterwise_policy as policy


class JobRateOvertime(unittest.TestCase):
    row = policy.SitterwisePolicy.row
    run_rows = policy.SitterwisePolicy.run_rows

    def setUp(self):
        self.rules = Rules.load(DEFAULT_RULES_PATH)

    def test_mixed_week_pays_overtime_at_the_28_dollar_job_rate(self):
        rows = [self.row('9.5', day=3, rate='28', lifesaver='0', tip='50'),
                self.row('4', day=4, rate='28', lifesaver='0', tip='50'),
                self.row('6.5', day=5, lifesaver='0'), self.row('4', day=6, lifesaver='0')]
        run, entry = self.run_rows(rows)
        c = run.caregivers[0]
        # 13.5*28 + 10.5*23 + 1.5*14 + 100 tips = 740.50.
        self.assertEqual(c.ot_premium, Decimal('21.00'))
        self.assertEqual(c.total_paid, Decimal('740.50'))
        pay_rows = exports.onpay_pay_rows(c, '123', exports.load_onpay_mapping())
        ot = next(r for r in pay_rows if r['id'] == '17')
        self.assertEqual(ot['cash'], Decimal('21.00'))
        self.assertTrue(ot['treat_as_cash'])
        self.assertIsNone(ot['hours'])
        self.assertIsNone(ot['rate'])
        self.assertIn('1.50 hrs x $14.0000 premium', ot['note'])
        text, skipped = exports.onpay_import_csv(run, {entry.caregiver_key: entry})
        self.assertFalse(skipped)
        imported = sum((Decimal(r['cash_amount']) if r['cash_amount'] else
                        Decimal(r['hours']) * Decimal(r['rate'])
                        for r in csv.DictReader(io.StringIO(text))), Decimal('0'))
        self.assertEqual(imported, Decimal('740.50'))

    def test_lower_job_rate_keeps_the_higher_weighted_premium(self):
        run, _ = self.run_rows([self.row('9', lifesaver='0'),
                                self.row('7', day=4, rate='28', lifesaver='0')])
        # (9*23 + 7*28)/16 = 25.1875; 1*half = 12.59375 -> 12.59.
        self.assertEqual(run.caregivers[0].ot_premium, Decimal('12.59'))

    def test_job_order_comes_from_times_not_csv_row_order(self):
        early = self.row('5', lifesaver='0')
        late = self.row('4', rate='28', lifesaver='0')
        late.update({'Start Time': '13:00', 'End Time': '17:00'})
        for rows in ([late, early], [early, late]):
            run, _ = self.run_rows(rows)
            self.assertEqual(run.caregivers[0].ot_premium, Decimal('14.00'))
            self.assertEqual(run.caregivers[0].weeks[0].premium_segments[0]['booking_id'],
                             str(late['Booking ID']))

    def test_daily_overtime_can_span_two_rates(self):
        early = self.row('9', lifesaver='0')
        late = self.row('2', rate='28', lifesaver='0')
        late.update({'Start Time': '17:00', 'End Time': '19:00'})
        run, _ = self.run_rows([early, late])
        c = run.caregivers[0]
        # Actual wages 263/11; one OT hour at that floor, two at $28.
        # Guarantee for the second job is excluded: 263/11/2 + 2*14 = 39.95.
        self.assertEqual(c.ot_premium, Decimal('39.95'))
        self.assertEqual(c.guarantee_pay, Decimal('56.00'))

    def test_double_time_follows_the_job_rate_and_exports_as_cash(self):
        run, _ = self.run_rows([self.row('13', rate='28', lifesaver='0'),
                                self.row('8', day=4, lifesaver='0')])
        c = run.caregivers[0]
        self.assertEqual(c.ot_premium, Decimal('56.00'))
        self.assertEqual(c.dt_premium, Decimal('28.00'))
        row = next(r for r in exports.onpay_pay_rows(c, '123', exports.load_onpay_mapping())
                   if r['id'] == '121')
        self.assertEqual(row['cash'], Decimal('28.00'))
        self.assertIn('1.00 hrs x $28.0000 premium', row['note'])
        self.assertTrue(row['treat_as_cash'])

    def test_weekly_overtime_uses_the_jobs_after_40_regular_hours(self):
        self.rules.data['overtime']['weekly_overtime']['enabled'] = True
        rows = [self.row('8', day=d, lifesaver='0') for d in range(3, 8)]
        rows.append(self.row('5', day=8, rate='28', lifesaver='0'))
        run, _ = self.run_rows(rows)
        c = run.caregivers[0]
        self.assertEqual(c.ot_hours, Decimal('5.00'))
        self.assertEqual(c.ot_premium, Decimal('70.00'))
        self.assertEqual(c.weeks[0].premium_segments[0]['kind'], 'weekly_overtime')

    def test_daily_overtime_is_not_counted_again_as_weekly_overtime(self):
        self.rules.data['overtime']['weekly_overtime']['enabled'] = True
        rows = [self.row('9', day=3, rate='28', lifesaver='0')]
        rows.extend(self.row('8', day=d, lifesaver='0') for d in range(4, 8))
        rows.append(self.row('5', day=8, rate='28', lifesaver='0'))
        run, _ = self.run_rows(rows)
        c = run.caregivers[0]
        self.assertEqual(c.ot_hours, Decimal('6.00'))
        self.assertEqual(c.ot_premium, Decimal('84.00'))

    def test_seventh_day_uses_job_rate_for_overtime_and_double_time(self):
        rows = [self.row('1', day=d, lifesaver='0') for d in range(3, 9)]
        rows.append(self.row('9', day=9, rate='28', lifesaver='0'))
        run, _ = self.run_rows(rows)
        c = run.caregivers[0]
        self.assertEqual(c.ot_premium, Decimal('112.00'))
        self.assertEqual(c.dt_premium, Decimal('28.00'))

    def test_lifesaver_premium_is_added_after_job_rate_premium(self):
        run, _ = self.run_rows([self.row('9', rate='28'),
                                self.row('8', day=4, lifesaver='0')])
        # Bonus regular hours = 16; 15/16*1.5 = 1.40625. Base OT = 14.
        self.assertEqual(run.caregivers[0].ot_premium, Decimal('15.41'))

    def test_each_week_gets_its_own_rate_floor(self):
        run, _ = self.run_rows([self.row('9', rate='28', lifesaver='0'),
                                self.row('9', day=10, lifesaver='0')], end=16)
        self.assertEqual(run.caregivers[0].ot_premium, Decimal('25.50'))

    def test_old_snapshot_keeps_19_36_premium(self):
        self.rules.data['overtime']['regular_rate_method'] = 'weighted_average'
        run, _ = self.run_rows([self.row('9.5', rate='28', lifesaver='0'),
                                self.row('4', day=4, rate='28', lifesaver='0'),
                                self.row('6.5', day=5, lifesaver='0'),
                                self.row('4', day=6, lifesaver='0')])
        c = run.caregivers[0]
        self.assertEqual(c.ot_premium, Decimal('19.36'))
        self.assertEqual(c.weeks[0].premium_segments, [])


if __name__ == '__main__':
    unittest.main()
