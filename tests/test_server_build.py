"""Relaunching must recognize code copied over an unchanged Git commit."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from payroll.server import build_id


class RunningVersion(unittest.TestCase):
    def test_code_changes_are_detected_without_a_new_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            (root / '.git' / 'HEAD').write_text('a' * 40)
            (root / 'payroll').mkdir()
            code = root / 'payroll' / 'exports.py'
            code.write_text('old code')
            with patch('payroll.server.APP_ROOT', root):
                before = build_id()
                self.assertEqual(before, build_id())
                code.write_text('fixed code')
                self.assertNotEqual(before, build_id())
                self.assertTrue(build_id().startswith('aaaaaaa-'))

    def test_downloaded_copy_without_git_still_has_a_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'web').mkdir()
            code = root / 'web' / 'app.js'
            code.write_text('old interface')
            with patch('payroll.server.APP_ROOT', root):
                before = build_id()
                self.assertTrue(before)
                code.write_text('fixed interface')
                self.assertNotEqual(before, build_id())
