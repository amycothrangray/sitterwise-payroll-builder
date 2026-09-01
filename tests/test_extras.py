"""Payroll notes and recurring pay.

Two things the bookings export cannot tell us: the notes somebody wrote down
during the week, and the people paid for work that was never a booking.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll import extras                                                # noqa: E402
from payroll.rules import Rules                                           # noqa: E402
from payroll.run import build_run                                         # noqa: E402
from payroll.store import Store                                           # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "test-payroll.xlsx"
# Aug 3 2026 is a Monday, and the first Monday of that month.
WEEK_START, WEEK_END = date(2026, 8, 3), date(2026, 8, 9)

LISSA = {"person_name": "Lissa Trevino", "caregiver_key": "lissa trevino",
         "amount": "1500.00", "frequency": "monthly", "schedule": "first_monday",
         "taxable": 1, "active": 1, "note": "Monthly salary"}


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class WhenAMonthlyPersonGetsPaid(unittest.TestCase):
    """Sitterwise pays weekly, so "monthly" has to mean one particular week."""

    def test_the_first_monday_is_always_a_monday_in_the_first_week(self):
        for year in (2025, 2026, 2027):
            for month in range(1, 13):
                day = extras.first_monday(year, month)
                self.assertEqual(day.weekday(), 0)
                self.assertLessEqual(day.day, 7)
                self.assertEqual((day.year, day.month), (year, month))

    def test_paid_exactly_twelve_times_a_year(self):
        paid, day = 0, date(2026, 1, 5)
        while day < date(2027, 1, 4):
            if extras.is_due(LISSA, day, day + timedelta(days=6)):
                paid += 1
            day += timedelta(days=7)
        self.assertEqual(paid, 12)

    def test_a_week_straddling_a_month_end_does_not_pay_twice(self):
        # Aug 31 - Sep 6 2026 touches two months. September's first Monday is
        # the 7th, so this week owes her nothing and the next one owes her.
        self.assertFalse(extras.is_due(LISSA, date(2026, 8, 31), date(2026, 9, 6)))
        self.assertTrue(extras.is_due(LISSA, date(2026, 9, 7), date(2026, 9, 13)))

    def test_the_second_week_of_a_month_never_pays_her(self):
        self.assertFalse(extras.is_due(LISSA, date(2026, 8, 10), date(2026, 8, 16)))

    def test_turning_somebody_off_stops_the_payments(self):
        off = dict(LISSA, active=0)
        self.assertFalse(extras.is_due(off, WEEK_START, WEEK_END))

    def test_weekly_lands_on_every_payroll(self):
        weekly = dict(LISSA, frequency="weekly")
        for start in (date(2026, 8, 3), date(2026, 8, 10), date(2026, 8, 17)):
            self.assertTrue(extras.is_due(weekly, start, start + timedelta(days=6)))


class ARecurringPayrollLine(unittest.TestCase):
    def setUp(self):
        self.rules = Rules.load()

    def test_a_salary_carries_no_hours_and_no_overtime(self):
        line = extras.recurring_payroll(LISSA, WEEK_START, WEEK_END)
        self.assertEqual(line.total_paid, money("1500.00"))
        self.assertEqual(line.taxable_earnings, money("1500.00"))
        self.assertEqual(line.hours_worked, 0)
        self.assertEqual(line.ot_hours, 0)
        self.assertEqual(line.dt_hours, 0)
        self.assertEqual(line.jobs, [])

    def test_it_says_on_the_record_where_the_money_came_from(self):
        line = extras.recurring_payroll(LISSA, WEEK_START, WEEK_END)
        reason = line.adjustments[0].reason
        self.assertIn("Settings", reason)
        self.assertIn("Monthly salary", reason)

    def test_somebody_with_no_bookings_still_appears_on_the_payroll(self):
        run = build_run(FIXTURE, self.rules, WEEK_START, WEEK_END, recurring=[LISSA])
        found = [c for c in run.caregivers if c.key == LISSA["caregiver_key"]]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].total_paid, money("1500.00"))

    def test_adding_a_salary_does_not_move_anybody_elses_pay(self):
        before = build_run(FIXTURE, self.rules, WEEK_START, WEEK_END)
        after = build_run(FIXTURE, self.rules, WEEK_START, WEEK_END, recurring=[LISSA])
        was = {c.key: c.total_paid for c in before.caregivers}
        now = {c.key: c.total_paid for c in after.caregivers
               if c.key != LISSA["caregiver_key"]}
        self.assertEqual(was, now)

    def test_somebody_who_also_worked_bookings_gets_one_payment_not_two(self):
        plain = build_run(FIXTURE, self.rules, WEEK_START, WEEK_END)
        worker = plain.caregivers[0]
        entry = dict(LISSA, person_name=worker.name, caregiver_key=worker.key,
                     amount="300.00", note="Admin work")
        run = build_run(FIXTURE, self.rules, WEEK_START, WEEK_END, recurring=[entry])
        rows = [c for c in run.caregivers if c.key == worker.key]
        self.assertEqual(len(rows), 1, "they should not appear twice")
        self.assertEqual(rows[0].total_paid, money(worker.total_paid + money("300.00")))

    def test_recurring_pay_does_not_create_overtime(self):
        plain = build_run(FIXTURE, self.rules, WEEK_START, WEEK_END)
        worker = plain.caregivers[0]
        entry = dict(LISSA, person_name=worker.name, caregiver_key=worker.key,
                     amount="900.00", note="Admin work")
        run = build_run(FIXTURE, self.rules, WEEK_START, WEEK_END, recurring=[entry])
        after = next(c for c in run.caregivers if c.key == worker.key)
        self.assertEqual(after.ot_hours, worker.ot_hours)
        self.assertEqual(after.ot_premium, worker.ot_premium)
        self.assertEqual(after.dt_premium, worker.dt_premium)

    def test_a_non_taxable_recurring_payment_stays_out_of_wages(self):
        entry = dict(LISSA, taxable=0, note="Phone stipend reimbursement")
        line = extras.recurring_payroll(entry, WEEK_START, WEEK_END)
        self.assertEqual(line.taxable_earnings, money("0.00"))
        self.assertEqual(line.reimbursements, money("1500.00"))


class TurningANoteIntoPay(unittest.TestCase):
    def test_a_bonus_is_taxable_and_sits_beside_the_work(self):
        note = {"kind": "bonus", "caregiver_key": "ada whitlow", "amount": "50",
                "detail": "Late cancellation", "created_at": "2026-08-04T10:00:00"}
        adj = extras.note_to_adjustment(note)
        self.assertTrue(adj.taxable)
        self.assertEqual(adj.booking_id, "", "a bonus must not overwrite a booking")
        self.assertEqual(Decimal(adj.new_value), money("50.00"))

    def test_a_reimbursement_is_not_taxable(self):
        note = {"kind": "reimbursement", "caregiver_key": "ada whitlow",
                "amount": "140", "detail": "Trustline"}
        self.assertFalse(extras.note_to_adjustment(note).taxable)

    def test_docking_pay_comes_out_negative_however_it_was_typed(self):
        for typed in ("45", "-45"):
            note = {"kind": "dock", "caregiver_key": "ada whitlow", "amount": typed,
                    "detail": "Docked"}
            self.assertEqual(Decimal(extras.note_to_adjustment(note).new_value),
                             money("-45.00"))

    def test_the_reason_says_it_came_from_a_note_and_who_wrote_it(self):
        note = {"kind": "bonus", "caregiver_key": "ada whitlow", "amount": "50",
                "detail": "Late cancellation", "created_at": "2026-08-04T10:00:00",
                "created_by": "Lissa"}
        reason = extras.note_to_adjustment(note).reason
        self.assertIn("Payroll note", reason)
        self.assertIn("Lissa", reason)
        self.assertIn("2026-08-04", reason)
        self.assertIn("Late cancellation", reason)

    def test_an_hours_correction_is_tied_to_its_booking(self):
        note = {"kind": "hours", "caregiver_key": "ada whitlow", "booking_id": "90001",
                "amount": "6", "detail": "Was really there 6 hours"}
        adj = extras.note_to_adjustment(note)
        self.assertEqual(adj.booking_id, "90001")


class NotesTheAppRefusesToGuessAt(unittest.TestCase):
    """A note is only applied when it says enough to be applied safely."""

    def test_a_note_with_nobody_on_it_is_not_applied(self):
        problem = extras.note_problem({"kind": "bonus", "amount": "50"})
        self.assertIn("nobody", problem.lower())

    def test_an_hours_correction_without_a_booking_is_not_applied(self):
        problem = extras.note_problem(
            {"kind": "hours", "caregiver_key": "ada whitlow", "amount": "6"})
        self.assertIn("booking number", problem.lower())

    def test_a_note_with_no_amount_is_not_applied(self):
        problem = extras.note_problem(
            {"kind": "bonus", "caregiver_key": "ada whitlow", "amount": ""})
        self.assertIn("amount", problem.lower())

    def test_a_good_note_has_no_complaint(self):
        self.assertEqual(extras.note_problem(
            {"kind": "bonus", "caregiver_key": "ada whitlow", "amount": "50"}), "")

    def test_judgement_calls_are_never_applied_automatically(self):
        for kind in ("exclude", "check", "other"):
            self.assertNotIn(kind, extras.APPLIES_ITSELF)


class NotesSurviveInTheStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "payroll.sqlite3")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_a_note_is_written_down_and_read_back(self):
        note_id = self.store.add_note(
            {"kind": "bonus", "caregiver_name": "Ada Whitlow",
             "caregiver_key": "ada whitlow", "amount": "50", "detail": "Late cancellation"})
        notes = self.store.list_notes("open")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["id"], note_id)
        self.assertEqual(notes[0]["detail"], "Late cancellation")

    def test_applying_a_note_records_which_payroll_took_it(self):
        note_id = self.store.add_note(
            {"kind": "bonus", "caregiver_key": "ada whitlow", "amount": "50"})
        self.store.mark_note_applied(note_id, "run123")
        note = self.store.get_note(note_id)
        self.assertEqual(note["status"], "applied")
        self.assertEqual(note["applied_run_id"], "run123")
        self.assertEqual(self.store.list_notes("open"), [])

    def test_a_reopened_note_is_waiting_again(self):
        note_id = self.store.add_note(
            {"kind": "bonus", "caregiver_key": "ada whitlow", "amount": "50"})
        self.store.mark_note_applied(note_id, "run123")
        self.store.reopen_note(note_id)
        self.assertEqual(len(self.store.list_notes("open")), 1)
        self.assertEqual(self.store.get_note(note_id)["applied_run_id"], "")

    def test_writing_a_note_leaves_a_trail(self):
        self.store.add_note({"kind": "bonus", "caregiver_name": "Ada Whitlow",
                             "caregiver_key": "ada whitlow", "amount": "50",
                             "detail": "Late cancellation"})
        actions = [e["action"] for e in self.store.audit_trail()]
        self.assertIn("note_added", actions)

    def test_recurring_pay_is_written_down_and_read_back(self):
        self.store.add_recurring(dict(LISSA))
        entries = self.store.list_recurring(active_only=True)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["person_name"], "Lissa Trevino")
        self.assertEqual(entries[0]["amount"], "1500.00")

    def test_switching_somebody_off_takes_them_out_of_future_payrolls(self):
        entry_id = self.store.add_recurring(dict(LISSA))
        self.store.update_recurring(entry_id, {"active": False})
        self.assertEqual(self.store.list_recurring(active_only=True), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
