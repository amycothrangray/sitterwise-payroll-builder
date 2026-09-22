"""Payday metadata persists without changing wages, tips or paid history."""
import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from payroll import exports, statements, transfer
from payroll.payday import checked_pay_date, suggested_pay_date
from payroll.roster import READY, RosterEntry
from payroll.rules import Rules, DEFAULT_RULES_PATH
from payroll.server import ApiError, Handler, load_run, run_payload
from payroll.store import SCHEMA, Store
from tests.fixtures.make_fixture import booking

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'tests/fixtures/test-payroll.xlsx'
START, END = date(2026, 8, 3), date(2026, 8, 9)


class Payday(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'payroll.sqlite3')
        self.rules = Rules.load(DEFAULT_RULES_PATH)
        source = self.root / 'bookings.csv'
        rows = [booking('Test Caregiver', START, '08:00', '9.5', '28', tip='50'),
                booking('Test Caregiver', date(2026,8,5), '08:00', '3', '23'),
                booking('Second Caregiver', START, '08:00', '4', '23')]
        with source.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        self.run_id = self.store.create_run('Test week', START, END,
            self.rules.snapshot(), source.name, 'fixture', str(source))
        for c in load_run(self.store, self.run_id)[1].caregivers:
            self.store.upsert_roster_entry(RosterEntry(c.key, c.name, READY,
                onpay_clock_user=c.caregiver_id), quiet=True)
        self.handler = object.__new__(Handler)
        self.handler.store = self.store
        self.handler._json = lambda data: data

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def save(self, value, run_id=None):
        self.handler._json_body = lambda: {'pay_date': value}
        return self.handler._api_post(f'/api/runs/{run_id or self.run_id}/pay-date')

    def snapshot(self):
        tables = ('paid_bookings', 'adjustments', 'tip_payments', 'tip_updates',
                  'entry_progress', 'notes', 'recurring_pay')
        return {name: [tuple(r) for r in self.store.db.execute('SELECT * FROM '+name)]
                for name in tables}

    def test_next_friday_across_month_year_and_leap_boundaries(self):
        for end, payday in [('2026-09-20','2026-09-25'), ('2026-12-27','2027-01-01'),
                            ('2028-02-27','2028-03-03'), ('2026-09-25','2026-10-02')]:
            self.assertEqual(suggested_pay_date(date.fromisoformat(end)).isoformat(), payday)
        self.assertEqual(self.store.get_run(self.run_id)['pay_date'], '2026-08-14')
        self.assertEqual(checked_pay_date('2026-08-13', END), date(2026,8,13))

    def test_saved_date_survives_reopen_and_never_changes_csv_or_money(self):
        _, before, roster = load_run(self.store, self.run_id)
        csv_before = exports.onpay_import_csv(before, roster)
        history = self.snapshot()
        self.assertEqual(self.save('2026-08-13'), {'ok': True})
        self.store.close(); self.store = Store(self.root / 'payroll.sqlite3')
        _, after, roster = load_run(self.store, self.run_id)
        self.assertEqual(after.pay_date, date(2026,8,13))
        self.assertEqual(run_payload(self.store,self.run_id)['run']['pay_date'], '2026-08-13')
        self.assertEqual(after.totals(), before.totals())
        self.assertEqual(exports.onpay_import_csv(after, roster), csv_before)
        self.assertEqual(self.snapshot(), history)

    def test_invalid_date_is_rejected_without_mutating_any_record(self):
        before = list(self.store.db.iterdump())
        for value in ['', None, 123, [], {}, '2026-02-30', '2026-8-14',
                      '2026-08-08', '2026-08-14T12:00:00', '<script>']:
            with self.subTest(value=value), self.assertRaises(ApiError): self.save(value)
            self.assertEqual(list(self.store.db.iterdump()), before)
        with self.assertRaisesRegex(ApiError, 'no longer exists'): self.save('2026-08-14', 'abcdef')

    def test_finished_record_allows_date_only_and_noop_has_no_audit_noise(self):
        _, run, _ = load_run(self.store, self.run_id)
        self.store.finalize_run(self.run_id, [j.booking_id for j in run.period_jobs], run.totals())
        old = self.store.get_run(self.run_id); history = self.snapshot()
        self.save('2026-08-13')
        new = self.store.get_run(self.run_id)
        self.assertEqual({k:v for k,v in old.items() if k!='pay_date'},
                         {k:v for k,v in new.items() if k!='pay_date'})
        self.assertEqual(self.snapshot(), history)
        once = list(self.store.db.iterdump()); self.save('2026-08-13')
        self.assertEqual(list(self.store.db.iterdump()), once)

    def test_old_database_upgrade_does_not_invent_historical_paydays(self):
        old_path = self.root/'old.sqlite3'
        old = sqlite3.connect(old_path)
        try:
            old.executescript(SCHEMA.replace(",\n    pay_date TEXT", ""))
            old.execute("INSERT INTO runs(id,label,period_start,period_end,status,created_at,"
                        "source_filename,source_sha256,source_path,rules_snapshot,rules_version) "
                        "VALUES ('abc','Old week','2026-08-03','2026-08-09','finalized','old',"
                        "'file.xlsx','sha',?,'{}','1')", (str(FIXTURE),))
            old.commit()
        finally: old.close()
        upgraded = Store(old_path)
        try:
            self.assertIsNone(upgraded.get_run('abc')['pay_date'])
            upgraded.set_pay_date('abc','2026-08-14')
            self.assertEqual(upgraded.get_run('abc')['status'], 'finalized')
        finally: upgraded.close()

    def test_date_survives_private_history_transfer(self):
        self.save('2026-08-13')
        for name in ('rules.json','onpay_mapping.json'):
            (self.root/name).write_bytes((ROOT/name).read_bytes())
        other = Store(self.root/'new/payroll.sqlite3')
        try:
            transfer.restore_archive(transfer.create_archive(self.store), other)
            self.assertEqual(load_run(other,self.run_id)[1].pay_date,date(2026,8,13))
        finally: other.close()

    def test_pdf_archive_task_and_filenames_agree_on_date(self):
        from pypdf import PdfReader
        self.save('2026-08-13')
        _, run, roster = load_run(self.store,self.run_id)
        handoff = exports.onpay_notes_handoff(run,roster)
        self.assertIn('Pay date: 2026-08-13', handoff)
        with zipfile.ZipFile(io.BytesIO(statements.create_archive(run,roster))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['pay_date'], '2026-08-13')
            for c in manifest['caregivers']:
                self.assertIn(c['filename'], handoff)
                reader = PdfReader(io.BytesIO(archive.read(c['filename'])))
                for page in reader.pages:
                    self.assertIn('Pay date Aug 13, 2026', page.extract_text())
        c = run.caregivers[0]
        original = statements.filename_for(statements.statement_data(run,c))
        run.pay_date = date(2026,8,14)
        self.assertNotEqual(original, statements.filename_for(statements.statement_data(run,c)))
        run.pay_date = None
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(
            statements.render_pdf(statements.statement_data(run,c)))).pages)
        self.assertNotIn('Pay date', text)
        self.assertIn('No pay date is recorded', exports.onpay_notes_handoff(run,roster))
