"""Weekly reminders persist, reset, and never change or silently drop pay."""
from datetime import date
from pathlib import Path
import tempfile
import unittest

from payroll import checklist
from payroll.roster import RosterEntry, READY
from payroll.rules import Rules
from payroll.run import build_run
from payroll.server import Handler, load_run, run_payload, waiting_notes, ApiError
from payroll.store import Store
from payroll.transfer import create_archive, restore_archive

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT/'tests/fixtures/test-payroll.xlsx'
START, END = date(2026,8,3), date(2026,8,9)


class WeeklyReminders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.store = Store(self.root/'payroll.sqlite3'); self.rules = Rules.load()
        result = build_run(FIXTURE,self.rules,START,END)
        self.result = result.import_result
        for c in result.caregivers:
            self.store.upsert_roster_entry(RosterEntry(c.key,c.name,READY,c.caregiver_id),quiet=True)
        self.run_id = self.draft()
        for name in ('rules.json','onpay_mapping.json'):
            (self.root/name).write_bytes((ROOT/name).read_bytes())
        self.handler = object.__new__(Handler); self.handler.store = self.store
        self.handler._json = lambda data: data

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def draft(self, start=START, end=END):
        return self.store.create_run('Test week',start,end,self.rules.snapshot(),FIXTURE.name,
                                     self.result.source_sha256,str(FIXTURE))

    def items(self, run_id=None):
        record,run,_ = load_run(self.store,run_id or self.run_id)
        return checklist.items_for(self.store,record,run,waiting_notes(self.store,record))

    def check(self, key, value=True):
        self.handler._json_body = lambda: dict(key=key,checked=value)
        return self.handler._api_post(f'/api/runs/{self.run_id}/checklist')

    def test_always_reminds_about_external_odds_ends_and_preserves_pay(self):
        before = load_run(self.store,self.run_id)[1].totals()
        self.assertEqual([i['key'] for i in self.items()],['odds_ends'])
        self.check('odds_ends')
        self.assertTrue(self.items()[0]['checked'])
        self.assertEqual(load_run(self.store,self.run_id)[1].totals(),before)
        self.store.close(); self.store = Store(self.root/'payroll.sqlite3')
        self.assertTrue(self.items()[0]['checked'])
        self.assertFalse(self.items(self.draft(date(2026,8,10),date(2026,8,16)))[0]['checked'])

    def test_due_weekly_people_use_correct_first_and_usual_amounts(self):
        one = self.store.add_recurring(dict(person_name='Test Coordinator',caregiver_key='test coordinator',
            amount='450',frequency='weekly',starts_on='2026-08-03'))
        two = self.store.add_recurring(dict(person_name='Test Administrator',caregiver_key='test administrator',
            amount='150',frequency='weekly',starts_on='2026-08-03',first_amount='225'))
        self.assertEqual(len(self.items()),3)
        self.assertTrue(any('$225.00' in i['title'] for i in self.items()))
        self.check('recurring:'+one)
        self.store.update_recurring(one,dict(amount='460'))
        self.assertFalse(next(i for i in self.items() if i['key']=='recurring:'+one)['checked'])
        upcoming = self.items(self.draft(date(2026,8,10),date(2026,8,16)))
        self.assertTrue(any('$150.00' in i['title'] for i in upcoming))

    def test_new_odds_entry_reopens_review_and_cannot_be_checked_away(self):
        self.check('odds_ends')
        self.store.add_note(dict(kind='other',detail='Check external adjustment',applies_to='next'))
        self.assertFalse(self.items()[0]['checked'])
        with self.assertRaisesRegex(ApiError,'resolve'): self.check('odds_ends')

    def test_missing_booking_payment_is_never_marked_applied_and_lost(self):
        note = self.store.add_note(dict(kind='extra_pay',caregiver_key='no bookings',caregiver_name='No Bookings',
                                       amount='40',detail='Training',applies_to='next'))
        reply = self.handler._apply_notes(self.run_id)
        self.assertEqual(reply['applied'],0)
        self.assertIn('No bookings',reply['skipped'][0]['why'])
        self.assertEqual(self.store.list_notes('open')[0]['id'],note)
        self.assertIn('Handle this entry in OnPay',run_payload(self.store,self.run_id)['waiting_notes'][0]['problem'])

    def test_unknown_reminder_is_rejected_and_finished_run_is_locked(self):
        with self.assertRaises(ApiError): self.check('missing')
        self.store.finalize_run(self.run_id,[],{})
        with self.assertRaises(ApiError): self.check('odds_ends')

    def test_eligible_odds_entry_adds_pay_once_and_clears_waiting_list(self):
        run = load_run(self.store,self.run_id)[1]
        person = run.caregivers[0]
        self.store.add_note(dict(kind='extra_pay',caregiver_key=person.key,caregiver_name=person.name,
                                 amount='40',detail='Extra work',applies_to='next'))
        self.assertEqual(self.handler._apply_notes(self.run_id)['applied'],1)
        self.assertEqual(self.handler._apply_notes(self.run_id)['applied'],0)
        updated = load_run(self.store,self.run_id)[1]
        self.assertEqual(sum(c.total_paid for c in updated.caregivers),sum(c.total_paid for c in run.caregivers)+40)
        self.assertEqual(run_payload(self.store,self.run_id)['waiting_notes'],[])
        self.check('odds_ends')
        self.assertTrue(self.items()[0]['checked'])

    def test_finalize_requires_weekly_review(self):
        with self.assertRaisesRegex(ApiError,'checklist'):
            self.handler._api_post(f'/api/runs/{self.run_id}/finalize')
        self.assertEqual(self.store.get_run(self.run_id)['status'],'open')

    def test_review_survives_private_backup_restore(self):
        self.check('odds_ends')
        other = Store(self.root/'new/payroll.sqlite3')
        try:
            restore_archive(create_archive(self.store),other)
            record,run,_ = load_run(other,self.run_id)
            self.assertTrue(checklist.items_for(other,record,run,[])[0]['checked'])
        finally: other.close()
