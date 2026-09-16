"""Exercise the local access boundary over HTTP using synthetic payroll data."""
import http.client
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from payroll import server
from payroll.security import access_token, checked_workbook, MAX_UPLOAD_BYTES
from payroll.store import Store

FIXTURE = Path(__file__).parent / 'fixtures' / 'test-payroll.xlsx'


class LocalAccess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'payroll.sqlite3')
        self.uploads = self.root / 'uploads'
        self.web = self.root / 'web'
        self.web.mkdir()
        (self.web / 'index.html').write_text('Payroll')
        self.patches = [patch.object(server, 'UPLOAD_DIR', self.uploads),
                        patch.object(server, 'WEB_ROOT', self.web)]
        for p in self.patches: p.start()
        class TestHandler(server.Handler):
            pass
        TestHandler.store = self.store
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        self.token = access_token(self.store.path)
        self.httpd.access_token = self.token
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.store.close()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def request(self, path='/api/state', method='GET', body=None, headers=None, auth=True):
        extra = {'Authorization': 'Bearer ' + self.token} if auth else {}
        extra.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        try:
            conn.request(method, path, body=body, headers=extra)
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_payroll_reads_writes_and_stop_require_local_key(self):
        for method, path in [('GET', '/api/state'), ('GET', '/api/history-transfer'),
                             ('POST', '/api/notes'), ('DELETE', '/api/notes/123'),
                             ('POST', '/api/stop')]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(path, method, auth=False)[0], 401)
        self.assertEqual(self.store.list_notes(), [])
        self.assertEqual(self.request()[0], 200)

    def test_wrong_key_cannot_read_payroll(self):
        self.assertEqual(self.request(headers={'Authorization':'Bearer wrong'})[0], 401)

    def test_rebinding_host_and_cross_origin_are_denied_even_with_key(self):
        for headers in [{'Host': 'attacker.example:'+str(self.port)},
                        {'Origin': 'https://attacker.example'}, {'Origin':'null'},
                        {'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(self.request(headers={'Origin':f'http://127.0.0.1:{self.port}'})[0], 200)

    def test_untrusted_site_cannot_frame_or_load_app(self):
        self.assertEqual(self.request('/', auth=False, headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        status, body, headers = self.request('/', auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_static_paths_cannot_escape_to_sibling_or_symlink(self):
        outside = self.root / 'web-private'; outside.mkdir()
        (outside / 'secret.txt').write_text('private')
        (self.web / 'link.txt').symlink_to(outside / 'secret.txt')
        for path in ['/../web-private/secret.txt','/%2e%2e/web-private/secret.txt','/link.txt']:
            self.assertEqual(self.request(path, auth=False)[0],404)

    def test_uploaded_paths_cannot_read_arbitrary_local_files(self):
        for source in [str(FIXTURE), '/etc/passwd']:
            data = json.dumps({'source_path':source,'period_start':'2026-08-03','period_end':'2026-08-09'})
            self.assertEqual(self.request('/api/runs','POST',data,{'Content-Type':'application/json'})[0],400)
        self.assertEqual(self.store.list_runs(), [])

    def test_uploaded_symlinks_cannot_read_outside_uploads(self):
        self.uploads.mkdir()
        (self.uploads / 'linked.xlsx').symlink_to(FIXTURE.resolve())
        handler = object.__new__(server.Handler)
        with self.assertRaises(server.ApiError):
            handler._uploaded_path(self.uploads / 'linked.xlsx')

    def test_authenticated_upload_create_and_download_still_work(self):
        status, body, _ = self.request('/api/upload','POST',FIXTURE.read_bytes(),{'X-Filename':'bookings.xlsx'})
        self.assertEqual(status,200,body)
        info = json.loads(body)
        data = json.dumps(dict(source_path=info['source_path'],period_start='2026-08-03',period_end='2026-08-09'))
        status, body, _ = self.request('/api/runs','POST',data,{'Content-Type':'application/json'})
        self.assertEqual(status,200,body)
        run = json.loads(body)['run_id']
        status, body, _ = self.request('/api/runs/'+run+'/exports')
        self.assertEqual(status,200,body)
        exports = json.loads(body)['exports']
        export = next(e for e in exports if not e.get('download_blocked'))
        status, body, headers = self.request('/api/runs/'+run+'/export/'+export['key'])
        self.assertEqual(status,200,body)
        self.assertTrue(body)
        self.assertIn('attachment;',headers['Content-Disposition'])
        self.assertEqual(Path(info['source_path']).stat().st_mode & 0o777,0o600)

    def test_negative_duplicate_chunked_and_oversized_lengths_are_rejected(self):
        for extra, expected in [('Content-Length: -1\r\n',400),
                                ('Content-Length: 0\r\nContent-Length: 1\r\n',400),
                                ('Content-Length: 0\r\nTransfer-Encoding: chunked\r\n',400),
                                (f'Content-Length: {MAX_UPLOAD_BYTES+1}\r\n',413)]:
            with socket.create_connection(('127.0.0.1',self.port),timeout=3) as conn:
                conn.sendall((f'POST /api/notes HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                              f'Authorization: Bearer {self.token}\r\n'+extra+'\r\n').encode())
                response = http.client.HTTPResponse(conn); response.begin()
                self.assertEqual(response.status,expected)


class PrivateFiles(unittest.TestCase):
    def test_key_is_owner_only_stable_and_not_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'payroll.sqlite3'
            token = access_token(db)
            self.assertEqual(access_token(db),token)
            path = Path(tmp) / '.payroll.sqlite3-access-token'
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            path.unlink(); outside=Path(tmp)/'outside'; outside.write_text('unchanged')
            path.symlink_to(outside)
            with self.assertRaises(OSError): access_token(db)
            self.assertEqual(outside.read_text(),'unchanged')

    def test_existing_database_permissions_are_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'payroll.sqlite3'; db.touch(); db.chmod(0o644)
            store = Store(db); store.close()
            self.assertEqual(db.stat().st_mode & 0o777,0o600)

    def test_workbook_size_is_checked_before_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'large.csv'; path.write_text('x')
            with patch('payroll.security.MAX_UPLOAD_BYTES',0):
                with self.assertRaisesRegex(ValueError,'too large'): checked_workbook(path)
            path=Path(tmp)/'expanded.xlsx'
            with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z: z.writestr('large.xml','a'*1024)
            with patch('payroll.security.MAX_WORKBOOK_BYTES',100):
                with self.assertRaisesRegex(ValueError,'expands'): checked_workbook(path)

    def test_xml_entities_cannot_expand(self):
        from openpyxl.xml.functions import fromstring
        with self.assertRaises(Exception):
            fromstring(b'<!DOCTYPE root [<!ENTITY x "private">]><root>&x;</root>')


class SpreadsheetReports(unittest.TestCase):
    def test_imported_formula_text_is_literal_but_amounts_stay_numeric(self):
        import csv
        from decimal import Decimal
        from payroll.exports import _writer
        buffer, writer = _writer()
        writer.writerow(['=HYPERLINK("https://example.invalid")', '@SUM(1)', '\t=1+1', '  =1+1', '-12.50', Decimal('-2.25'), "O'Connor"])
        row = next(csv.reader(io.StringIO(buffer.getvalue())))
        self.assertTrue(all(value.startswith("'") for value in row[:4]))
        self.assertEqual(row[4:], ['-12.50', '-2.25', "O'Connor"])

    def test_machine_import_writer_preserves_identifiers_exactly(self):
        import csv
        from payroll.exports import _writer
        buffer, writer = _writer(report=False)
        writer.writerow(['00123', '-2.25'])
        self.assertEqual(next(csv.reader(io.StringIO(buffer.getvalue()))), ['00123','-2.25'])
