"""The simple workflow must preserve identities, all pay, and paycheck notes."""
import dataclasses
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from payroll import exports
from payroll.roster import (READY, MATCHED, UNCHECKED, SETUP_INCOMPLETE,
                            NOT_IN_ONPAY, RosterEntry, merge_import,
                            parse_onpay_employee_export)
from payroll.rules import Rules
from payroll.run import build_run
from payroll.server import Handler
from payroll.store import Store

FIXTURE = Path(__file__).parent / 'fixtures' / 'test-payroll.xlsx'
START, END = date(2026, 8, 3), date(2026, 8, 9)


class AccurateRosterStatus(unittest.TestCase):
    def test_employee_list_without_clock_users_confirms_existence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'employees.csv'
            path.write_text('First Name,Last Name,Status\nTest,Worker,Active\n')
            entries, _ = parse_onpay_employee_export(path)
        self.assertEqual(entries[0].status, MATCHED)
        self.assertFalse(entries[0].needs_attention)
        self.assertFalse(entries[0].is_blocking)

    def test_an_inactive_employee_is_not_made_ready_by_an_old_clock_user(self):
        previous = RosterEntry('test worker', 'Test Worker', READY, onpay_clock_user='18')
        current = RosterEntry('test worker', 'Test Worker', SETUP_INCOMPLETE,
                              note='Marked as no longer employed in OnPay.')
        changed, _ = merge_import({previous.caregiver_key: previous}, [current])
        self.assertEqual(changed[0].status, SETUP_INCOMPLETE)
        self.assertEqual(changed[0].onpay_clock_user, '18')

    def test_only_automatically_created_unknown_statuses_are_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'payroll.sqlite3'
            store = Store(path)
            for key, status, source in [('unknown', SETUP_INCOMPLETE, 'added_automatically'),
                                        ('missing', NOT_IN_ONPAY, 'manual'),
                                        ('inactive', SETUP_INCOMPLETE, 'onpay_import')]:
                store.upsert_roster_entry(RosterEntry(key, key, status, source=source))
            store.close()
            store = Store(path)
            self.assertEqual(store.roster()['unknown'].status, UNCHECKED)
            self.assertEqual(store.roster()['missing'].status, NOT_IN_ONPAY)
            self.assertEqual(store.roster()['inactive'].status, SETUP_INCOMPLETE)
            store.close()


class CompletePayrollAndNotes(unittest.TestCase):
    def setUp(self):
        self.rules = Rules.load()
        self.recurring = dict(person_name='Test Administrator', caregiver_key='test administrator',
                              amount='150.00', first_amount='225.00', starts_on='2026-08-03',
                              frequency='weekly', active=1, taxable=1,
                              note='Operator: type a different first-week amount in OnPay')
        preliminary = build_run(FIXTURE, self.rules, START, END, recurring=[self.recurring])
        self.roster = {c.key: RosterEntry(c.key, c.name, READY, onpay_clock_user=str(i+900))
                       for i, c in enumerate(preliminary.caregivers)}
        self.run = build_run(FIXTURE, self.rules, START, END,
                             roster=self.roster, recurring=[self.recurring])

    def test_handoff_covers_every_person_once_and_keeps_operator_notes_private(self):
        text = exports.onpay_notes_handoff(self.run, self.roster)
        people = json.loads(text[text.index('{\n  "paychecks"'):])['paychecks']
        self.assertEqual(len(people), len(self.run.caregivers))
        self.assertEqual(len({p['caregiver'] for p in people}), len(people))
        admin = next(p for p in people if p['caregiver'] == 'Test Administrator')
        self.assertEqual(admin['expected_gross_including_reimbursements'], '225.00')
        self.assertEqual(admin['paycheck_memo'], 'Recurring pay: $225.00')
        self.assertNotIn('Operator:', text)
        self.assertIn('reopen or reload', text)
        self.assertIn('payroll remains unsubmitted', text)
        self.assertIn(str(self.run.totals()['total_paid']), text)

    def test_usual_weekly_amount_is_in_the_next_week_handoff(self):
        run = build_run(FIXTURE, self.rules, date(2026, 8, 10), date(2026, 8, 16),
                        roster=self.roster, recurring=[self.recurring])
        text = exports.onpay_notes_handoff(run, self.roster)
        self.assertIn('Recurring pay: $150.00', text)
        self.assertNotIn('225.00', text)

    def test_a_missing_employee_identifier_blocks_the_complete_download(self):
        caregiver = next(c for c in self.run.caregivers if c.name == 'Test Administrator')
        self.roster[caregiver.key].onpay_clock_user = ''
        item = exports.all_exports(self.run, self.roster)[0]
        self.assertTrue(item['download_blocked'])
        self.assertIn(caregiver.name, item['skipped'])
        self.assertTrue(any(p.get('blocking') and p['caregiver'] == caregiver.name
                            for p in item['problems']))


class ReuploadingAWeek(unittest.TestCase):
    def test_finished_payroll_wins_over_a_stray_duplicate_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'history.sqlite3')
            result = build_run(FIXTURE, Rules.load(), START, END).import_result
            finished = store.create_run('Test week', START, END, Rules.load().snapshot(),
                                         FIXTURE.name, result.source_sha256, str(FIXTURE))
            store.finalize_run(finished, ['test-booking'], {})
            store.create_run('Duplicate', START, END, Rules.load().snapshot(),
                             FIXTURE.name, result.source_sha256, str(FIXTURE))
            handler = Mock(store=store)
            handler._json.side_effect = lambda data: data
            answer = Handler._create_run(handler, dict(source_path=str(FIXTURE),
                                        period_start=START.isoformat(), period_end=END.isoformat()))
            self.assertEqual(answer['run_id'], finished)
            self.assertTrue(answer['finalized'])
            self.assertEqual(len(store.list_runs()), 2)
            self.assertIn('test-booking', store.previously_paid())
            store.close()
