"""PDF money must agree with the engine and each private file must stand alone."""
import copy
import hashlib
import io
import json
import unittest
import zipfile
from decimal import Decimal
from unittest.mock import patch

from payroll import exports, statements
from payroll.engine import Adjustment
from payroll.rules import Rules, DEFAULT_RULES_PATH
from payroll.server import Handler, ApiError
from tests import test_sitterwise_policy as policy


class Statements(unittest.TestCase):
    row = policy.SitterwisePolicy.row
    run_rows = policy.SitterwisePolicy.run_rows

    def setUp(self):
        self.rules = Rules.load(DEFAULT_RULES_PATH)

    def example(self):
        return self.run_rows([self.row('9.5',rate='28',lifesaver='0',tip='50'),
            self.row('4',day=4,rate='28',lifesaver='0',tip='50'),
            self.row('6.5',day=5,lifesaver='0'),self.row('4',day=6,lifesaver='0')])

    def text(self, pdf):
        from pypdf import PdfReader
        return '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)

    def test_job_rate_breakdown_and_short_locator_match_csv(self):
        run, entry = self.example(); c=run.caregivers[0]
        data=statements.statement_data(run,c)
        self.assertEqual([j['total'] for j in data['jobs']],['337.00','162.00','149.50','92.00'])
        self.assertEqual(data['total'],'740.50')
        self.assertEqual(Decimal(data['jobs'][0]['premiums'][0]['rate']),Decimal('14'))
        original=exports.onpay_import_csv(run,{c.key:entry})
        pdf=statements.render_pdf(data); text=self.text(pdf)
        self.assertTrue(pdf.startswith(b'%PDF-')); self.assertIn('$740.50',text)
        self.assertIn('14.0000',text); self.assertIn('3-4',text)
        self.assertIn('before taxes',text.lower())
        self.assertEqual(original,exports.onpay_import_csv(run,{c.key:entry}))
        task=exports.onpay_notes_handoff(run,{c.key:entry})
        payload=json.loads(task[task.index('{\n  "paychecks"'):])['paychecks'][0]
        self.assertEqual(payload['pdf_filename'],statements.filename_for(data))
        self.assertEqual(payload['paycheck_memo'],statements.MEMO)
        self.assertIn('Employee Viewable',task);self.assertIn('Never use Company Documents',task)
        self.assertIn('payroll remains unsubmitted',task)

    def test_minimum_bonus_reimbursements_late_tip_and_private_adjustments(self):
        run,_=self.run_rows([self.row('2.5',lifesaver='15',tip='20',mileage_amount='12.34',reimbursement='9')])
        c=run.caregivers[0]
        c.late_tips=[dict(booking_id='old123',workday='2026-08-02',client_name='Previous Family',amount='25.00')]
        c.tips+=Decimal('25')
        c.adjustments=[Adjustment('a',c.key,'recurring_pay',new_value='150',reason='SECRET OPERATOR NOTE'),
                       Adjustment('b',c.key,'adjustment',new_value='-10',reason='PRIVATE'),
                       Adjustment('c',c.key,'reimbursement',new_value='5',taxable=False)]
        c.adjustment_taxable_total=Decimal('140');c.adjustment_nontaxable_total=Decimal('5')
        data=statements.statement_data(run,c)
        self.assertEqual(data['jobs'][0]['hours'],'2.50');self.assertEqual(data['jobs'][0]['minimum_hours'],'1.50')
        self.assertEqual(data['total'],str(c.total_paid))
        self.assertEqual(sum(Decimal(j['total']) for j in data['jobs'])+sum(Decimal(j['amount']) for j in data['extras']),c.total_paid)
        text=self.text(statements.render_pdf(data))
        for expected in ('Minimum top-up','Late tip','Scheduled pay','$150.00','Reimbursement adjustment'):
            self.assertIn(expected,text)
        self.assertNotIn('SECRET',text);self.assertNotIn('PRIVATE',text)

    def test_legacy_saved_rules_keep_original_weighted_amount(self):
        self.rules.data['overtime']['regular_rate_method']='weighted_average'
        run,_=self.example(); data=statements.statement_data(run,run.caregivers[0])
        self.assertEqual(data['ot'],'19.36');self.assertEqual(data['total'],'738.86')
        self.assertTrue(any(e['amount']=='19.36' for e in data['extras']))
        self.assertTrue(all(not j['premiums'] for j in data['jobs']))
        self.assertIn('$738.86',self.text(statements.render_pdf(data)))

    def test_rounding_preserves_weekly_cents_and_bonus_is_separate(self):
        early=self.row('9',lifesaver='15'); late=self.row('2',rate='28',lifesaver='0')
        late.update({'Start Time':'17:00','End Time':'19:00'})
        run,_=self.run_rows([early,late]);c=run.caregivers[0];data=statements.statement_data(run,c)
        base=sum(Decimal(j['premium_total']) for j in data['jobs'])
        bonus=sum(Decimal(e['amount']) for e in data['extras'])
        self.assertEqual(base,Decimal('39.95'));self.assertEqual(base+bonus,c.ot_premium)
        self.assertEqual(statements._allocate([dict(hours='1',premium_rate='1.005')]*3,Decimal('3.02')),
                         [Decimal('1.01'),Decimal('1.01'),Decimal('1.00')])

    def test_double_time(self):
        run,_=self.run_rows([self.row('13',rate='28')]);c=run.caregivers[0]
        data=statements.statement_data(run,c)
        self.assertEqual([p['amount'] for p in data['jobs'][0]['premiums']],['56.00','28.00'])
        self.assertIn('Double-time',self.text(statements.render_pdf(data)))

    def test_multi_page_and_xml_escaped_client(self):
        run,_=self.run_rows([self.row('4',day=d,lifesaver='0') for d in range(3,24)],end=24)
        c=run.caregivers[0];c.jobs[0].client_name='A & B <img src="file:///etc/passwd"/> family'
        from pypdf import PdfReader
        pdf=statements.render_pdf(statements.statement_data(run,c));reader=PdfReader(io.BytesIO(pdf))
        self.assertGreater(len(reader.pages),1)
        for page in reader.pages:self.assertIn('PAYROLL BREAKDOWN',page.extract_text())
        text=self.text(pdf);self.assertIn('A & B',text);self.assertIn('$1,932.00',text)
        self.assertIn('Pay period totals',reader.pages[-1].extract_text())

    def test_archive_is_individual_and_manifest_hashes_match(self):
        run,entry=self.example();first=run.caregivers[0];second=copy.deepcopy(first)
        second.name='Other Worker';second.key='other';second.caregiver_id='456';run.caregivers.append(second)
        data=statements.create_archive(run,{first.key:entry})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest=json.loads(archive.read('manifest.json'))['caregivers']
            self.assertEqual(len(manifest),2);self.assertEqual(len(archive.namelist()),4)
            for person in manifest:
                pdf=archive.read(person['filename'])
                self.assertEqual(hashlib.sha256(pdf).hexdigest(),person['sha256'])
                text=self.text(pdf);self.assertIn(person['caregiver'],text)
                other='Other Worker' if person['caregiver']==first.name else first.name
                self.assertNotIn(other,text)

    def test_filename_blocks_path_and_changes_with_money(self):
        run,_=self.example();c=run.caregivers[0];c.name='../../Bad/Name\\Name'
        data=statements.statement_data(run,c);name=statements.filename_for(data)
        self.assertNotIn('/',name);self.assertNotIn('\\',name);self.assertNotIn('..',name)
        data['total']='999.00';self.assertNotEqual(name,statements.filename_for(data))

    def test_inconsistent_or_duplicate_details_fail_closed(self):
        run,_=self.example();c=run.caregivers[0];c.tips+=Decimal('1')
        with self.assertRaisesRegex(ValueError,'totals'):statements.statement_data(run,c)
        c.tips-=Decimal('1');c.jobs.append(c.jobs[0])
        with self.assertRaisesRegex(ValueError,'Duplicate'):statements.statement_data(run,c)
        # Metadata still loads so the operator can fix payroll checks.
        listing=exports.all_exports(run,{})
        self.assertTrue(next(e for e in listing if e['key']=='caregiver_pdfs')['download_blocked'])

    def test_binary_export_and_no_tip_freeze(self):
        run,entry=self.example();handler=object.__new__(Handler)
        from unittest.mock import Mock
        handler.store=Mock();handler.store.entered_map.return_value={};handler.store.list_notes.return_value=[]
        handler._send=Mock()
        record=dict(status='open',period_start='2026-08-03',period_end='2026-08-09')
        with patch('payroll.server.load_run',return_value=(record,run,{entry.caregiver_key:entry})):
            Handler._export(handler,'abc','caregiver_pdfs')
            args=handler._send.call_args.args
            self.assertEqual(args[2],'application/zip');self.assertTrue(args[1].startswith(b'PK'))
            handler.store.db.execute.assert_not_called()
            handler.store.list_notes.return_value=[dict(applies_to='next')]
            with self.assertRaisesRegex(ApiError,'Saved pay changes'):Handler._export(handler,'abc','caregiver_pdfs')
            handler.store.list_notes.return_value=[];record.update(status='finalized',totals_snapshot=json.dumps({'total_paid':'1.00'}))
            with self.assertRaisesRegex(ApiError,'no longer match'):Handler._export(handler,'abc','caregiver_pdfs')
            with self.assertRaisesRegex(ApiError,'finished'):Handler._export(handler,'abc','onpay_notes')
