"""Late tips are additions to tips, never a second payment for old work."""
import csv
from datetime import date
from decimal import Decimal
import io
import hashlib
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
import zipfile
from unittest.mock import patch

from payroll import exports, late_tips
from payroll.engine import Adjustment
from payroll.importer import import_export
from payroll.roster import RosterEntry, READY
from payroll.rules import Rules
from payroll.run import build_run
from payroll.server import Handler, load_run, ApiError
from payroll.store import Store
from payroll.transfer import create_archive, restore_archive

ROOT=Path(__file__).resolve().parent.parent


class LateTips(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'payroll.sqlite3')
        self.rules=Rules.load(ROOT/'rules.json')
        self.roster={'sample caregiver':RosterEntry('sample caregiver','Sample Caregiver',READY,'9001')}
        self.store.upsert_roster_entry(self.roster['sample caregiver'],quiet=True)
        for name in ('rules.json','onpay_mapping.json'):
            (self.root/name).write_bytes((ROOT/name).read_bytes())
        self.counter=0

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def file(self, tip='0', day='2026-09-13', booking='test-old', identity='9001', extra=False):
        self.counter+=1
        path=self.root/f'bookings-{self.counter}.csv'
        fields=['Booking ID','Caregiver ID','Caregiver Name','Client Name','Start Date','End Date',
                'Start Time','End Time','Status','Hours Worked','Pay Rate','Paid to Caregiver','Tip','Service Type']
        row=[booking,identity,'Sample Caregiver','Example Family',day,day,'09:00','13:00','completed','4','23','92',tip,'Hotel']
        with path.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(fields);writer.writerow(row)
            if extra:
                writer.writerow(['test-new','9001','Sample Caregiver','Example Family','2026-09-15','2026-09-15',
                                 '09:00','13:00','completed','4','23','92','5','Hotel'])
        return path,import_export(path,self.rules)

    def draft(self,path,result,start='2026-09-14',end='2026-09-20'):
        return self.store.create_run('Synthetic week',date.fromisoformat(start),date.fromisoformat(end),
            self.rules.snapshot(),path.name,result.source_sha256,str(path))

    def payroll(self,run_id):
        return load_run(self.store,run_id)[1]

    def finish(self,run_id):
        run=self.payroll(run_id)
        self.store.finalize_run(run_id,[j.booking_id for j in run.period_jobs],run.totals(),late_tips.payments_for(run),run.late_tips)
        return run

    def original(self,tip='0',legacy=False,adjusted=None):
        path,result=self.file(tip)
        run_id=self.draft(path,result,'2026-09-07','2026-09-13')
        if adjusted is not None:
            self.store.add_adjustment(run_id,Adjustment('','sample caregiver','tip','test-old',tip,adjusted,'Correction'))
        if legacy:
            run=self.payroll(run_id)
            self.store.finalize_run(run_id,[j.booking_id for j in run.period_jobs],run.totals())
        else:self.finish(run_id)
        return run_id,path

    def observed_draft(self,tip='25',extra=False):
        path,result=self.file(tip,extra=extra)
        late_tips.observe_export(self.store,result)
        return self.draft(path,result)

    def test_sunday_tip_arriving_monday_is_paid_once_without_old_hours(self):
        old,_=self.original()
        following=self.observed_draft()
        run=self.payroll(following)
        self.assertEqual(run.totals()['tips'],'25.00')
        self.assertEqual(run.totals()['hours_worked'],'0.00')
        self.assertEqual(run.totals()['total_paid'],'25.00')
        self.assertTrue(run.reconciliation.balances)
        self.assertEqual(run.period_jobs,[])
        self.assertEqual(self.payroll(old).totals()['tips'],'0.00')
        self.finish(following)
        path,result=self.file('25')
        late_tips.observe_export(self.store,result)
        third=self.draft(path,result,'2026-09-21','2026-09-27')
        self.assertEqual(self.payroll(third).totals()['tips'],'0.00')
        self.assertEqual(self.payroll(following).totals()['tips'],'25.00')

    def test_only_unpaid_increase_is_added_and_later_increases_still_work(self):
        self.original('10')
        second=self.observed_draft('25')
        self.assertEqual(self.finish(second).totals()['tips'],'15.00')
        path,result=self.file('30');late_tips.observe_export(self.store,result)
        third=self.draft(path,result,'2026-09-21','2026-09-27')
        self.assertEqual(self.finish(third).totals()['tips'],'5.00')
        self.assertEqual(self.payroll(second).totals()['tips'],'15.00')

    def test_current_booking_and_late_tip_share_one_tips_item(self):
        self.original('10');second=self.observed_draft('25',extra=True)
        run=self.payroll(second)
        rows=exports.onpay_pay_rows(run.caregivers[0],'9001',exports.load_onpay_mapping())
        tips=[r for r in rows if r['id']=='208']
        self.assertEqual(len(tips),1);self.assertEqual(tips[0]['cash'],Decimal('20.00'))
        self.assertIn('Late tip for 2026-09-13',tips[0]['note'])
        from payroll.statements import statement_data
        self.assertTrue(any('test-old' in e['label'] for e in
                            statement_data(run,run.caregivers[0])['extras']))
        self.assertEqual(run.totals()['hours_worked'],'4.00')
        self.assertEqual(run.totals()['straight_pay'],'92.00')
        self.assertEqual(len(run.caregivers),1)
        self.assertTrue(run.reconciliation.balances)

    def test_legacy_history_uses_original_export_and_manual_tip_correction(self):
        old,_=self.original('10',legacy=True,adjusted='12')
        second=self.observed_draft('25')
        self.assertEqual(self.payroll(second).totals()['tips'],'13.00')
        self.assertEqual(self.payroll(old).totals()['tips'],'12.00')
        self.assertEqual(self.store.get_run(old)['tips_recorded'],1)

    def test_missing_or_changed_original_does_not_guess_zero(self):
        old,path=self.original('10',legacy=True)
        path.write_text('damaged')
        second=self.observed_draft('25',extra=True)
        run=self.payroll(second)
        self.assertFalse(run.summary['can_finalize'])
        item=next(e for e in exports.all_exports(run,self.roster) if e['key']=='onpay_import')
        self.assertTrue(item['download_blocked'])
        self.assertEqual(run.totals()['tips'],'5.00')

    def test_changed_caregiver_blocks_late_payment(self):
        self.original()
        path,result=self.file('25',identity='9002',extra=True)
        late_tips.observe_export(self.store,result)
        second=self.draft(path,result)
        run=self.payroll(second)
        self.assertFalse(run.summary['can_finalize'])
        self.assertEqual(run.late_tips,[])

    def test_two_drafts_cannot_claim_the_same_tip(self):
        self.original();second=self.observed_draft()
        self.assertEqual(self.payroll(second).totals()['tips'],'25.00')
        path,result=self.file('25');third=self.draft(path,result,'2026-09-21','2026-09-27')
        self.assertEqual(self.payroll(third).totals()['tips'],'0.00')
        self.store.delete_run(second)
        self.assertEqual(self.payroll(third).totals()['tips'],'25.00')

    def test_pending_tip_survives_a_new_month_export_without_old_booking(self):
        self.original();path,result=self.file('25');late_tips.observe_export(self.store,result)
        path,result=self.file('0',day='2026-10-01',booking='october-job')
        late_tips.observe_export(self.store,result)
        third=self.draft(path,result,'2026-09-28','2026-10-04')
        self.assertEqual(self.payroll(third).totals()['tips'],'25.00')

    def test_lower_total_never_deducts_an_already_paid_tip(self):
        self.original('25');second=self.observed_draft('10')
        self.assertEqual(self.payroll(second).totals()['tips'],'0.00')

    def test_repeated_import_preview_and_download_do_not_consume_tips(self):
        self.original();second=self.observed_draft()
        path,result=self.file('25')
        for _ in range(3):
            late_tips.observe_export(self.store,result)
            run=self.payroll(second)
            rows,_=exports.onpay_import_csv(run,self.roster)
            self.assertEqual(list(csv.DictReader(io.StringIO(rows)))[0]['cash_amount'],'25')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tip_payments WHERE run_id=?',(second,)).fetchone()[0],0)

    def test_export_freezes_late_tips_and_further_increase_waits(self):
        self.original();second=self.observed_draft()
        handler=object.__new__(Handler);handler.store=self.store;handler._send=lambda *args: None
        handler._export(second,'onpay_import')
        path,result=self.file('30');late_tips.observe_export(self.store,result)
        self.assertEqual(self.payroll(second).totals()['tips'],'25.00')
        self.finish(second)
        third=self.draft(path,result,'2026-09-21','2026-09-27')
        self.assertEqual(self.payroll(third).totals()['tips'],'5.00')

    def test_tip_only_week_can_be_created_from_an_upload(self):
        self.original();path,result=self.file('25')
        handler=object.__new__(Handler);handler.store=self.store;handler._json=lambda data:data
        with patch('payroll.server.UPLOAD_DIR',self.root):
            reply=handler._create_run(dict(source_path=str(path),period_start='2026-09-14',period_end='2026-09-20'))
        self.assertEqual(self.payroll(reply['run_id']).totals()['tips'],'25.00')

    def test_late_tip_history_survives_private_transfer(self):
        self.original('10');second=self.observed_draft('25');self.finish(second)
        raw=create_archive(self.store)
        other=Store(self.root/'new-mac/payroll.sqlite3')
        try:
            manifest=restore_archive(raw,other)
            self.assertEqual(manifest['version'],2)
            self.assertEqual(load_run(other,second)[1].totals()['tips'],'15.00')
            path,result=self.file('25');late_tips.observe_export(other,result)
            record=dict(id='future',status='open',period_start='2026-09-21',period_end='2026-09-27')
            self.assertEqual(late_tips.for_run(other,record)[0],[])
        finally:other.close()

    def test_unlocking_releases_payment_but_keeps_late_tip_with_its_run(self):
        old,_=self.original();second=self.observed_draft();self.finish(second)
        with self.assertRaisesRegex(ValueError,'later payroll'):self.store.unlock_run(old,'test')
        self.store.unlock_run(second,'test')
        self.assertEqual(self.payroll(second).totals()['tips'],'25.00')
        self.finish(second)
        self.assertEqual(self.payroll(second).totals()['tips'],'25.00')

    def test_tip_arriving_after_an_export_with_no_late_tips_waits(self):
        self.original()
        second=self.observed_draft('0',extra=True)
        handler=object.__new__(Handler);handler.store=self.store;handler._send=lambda *args: None
        handler._export(second,'onpay_import')
        path,result=self.file('25');late_tips.observe_export(self.store,result)
        self.assertEqual(self.finish(second).totals()['tips'],'5.00')
        third=self.draft(path,result,'2026-09-21','2026-09-27')
        self.assertEqual(self.payroll(third).totals()['tips'],'25.00')

    def test_unpaid_prior_booking_is_not_treated_as_late_tip(self):
        second=self.observed_draft('25',extra=True)
        self.assertEqual(self.payroll(second).totals()['tips'],'5.00')
        self.assertEqual(self.payroll(second).late_tips,[])

    def test_ambiguous_old_booking_blocks_export(self):
        self.original();path,result=self.file('25',extra=True)
        with path.open('a') as f:
            f.write(path.read_text().splitlines()[1]+'\n')
        result=import_export(path,self.rules);late_tips.observe_export(self.store,result)
        run=self.payroll(self.draft(path,result))
        self.assertFalse(run.summary['can_finalize'])
        self.assertTrue(exports.all_exports(run,self.roster)[0]['download_blocked'])

    def test_mismatched_historical_totals_are_not_assumed_paid(self):
        old,_=self.original('10',legacy=True)
        self.store.db.execute('UPDATE runs SET totals_snapshot=? WHERE id=?',
                              (json.dumps({'tips':'99.00'}),old))
        self.store.db.commit()
        run=self.payroll(self.observed_draft('25',extra=True))
        self.assertFalse(run.summary['can_finalize'])
        self.assertEqual(run.late_tips,[])

    def test_pending_tip_survives_transfer_before_payment(self):
        self.original();path,result=self.file('25');late_tips.observe_export(self.store,result)
        other=Store(self.root/'pending-mac/payroll.sqlite3')
        try:
            restore_archive(create_archive(self.store),other)
            record=dict(id='future',status='open',period_start='2026-09-14',period_end='2026-09-20')
            self.assertEqual(late_tips.for_run(other,record)[0][0]['amount'],'25.00')
        finally:other.close()

    def test_original_version_one_history_can_be_restored_and_backfilled(self):
        old,_=self.original('10',legacy=True)
        with zipfile.ZipFile(io.BytesIO(create_archive(self.store))) as archive:
            files={name:archive.read(name) for name in archive.namelist()}
        db_path=self.root/'version-one.sqlite3';db_path.write_bytes(files['payroll.sqlite3'])
        with sqlite3.connect(db_path) as db:
            for table in ('tip_payments','tip_updates'):db.execute(f'DROP TABLE {table}')
            for column in ('tips_recorded','late_tips_snapshot','tip_export_snapshot'):
                db.execute(f'ALTER TABLE runs DROP COLUMN {column}')
        files['payroll.sqlite3']=db_path.read_bytes()
        manifest=json.loads(files.pop('manifest.json'));manifest['version']=1
        for table in ('tip_payments','tip_updates'):manifest['counts'].pop(table)
        manifest['files']['payroll.sqlite3']=hashlib.sha256(files['payroll.sqlite3']).hexdigest()
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,data in files.items():archive.writestr(name,data)
            archive.writestr('manifest.json',json.dumps(manifest))
        other=Store(self.root/'legacy-mac/payroll.sqlite3')
        try:
            restore_archive(raw.getvalue(),other)
            path,result=self.file('25');late_tips.observe_export(other,result)
            record=dict(id='future',status='open',period_start='2026-09-14',period_end='2026-09-20')
            self.assertEqual(late_tips.for_run(other,record)[0][0]['amount'],'15.00')
            self.assertEqual(load_run(other,old)[1].totals()['tips'],'10.00')
        finally:other.close()
