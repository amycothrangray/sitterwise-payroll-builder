import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import date
import zipfile

from payroll.paths import data_directory
from payroll.roster import RosterEntry, READY
from payroll.server import _same_data_on
from payroll.store import Store
from payroll.transfer import create_archive, restore_archive


class PayrollHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = Store(self.root / "old-mac" / "payroll.sqlite3")
        self.new = Store(self.root / "new-mac" / "payroll.sqlite3")
        self.source = self.old.path.parent / "uploads" / "bookings.csv"
        self.source.parent.mkdir()
        self.source.write_text("Booking ID,Client Name\n123,Test family\n")
        for name in ("rules.json", "onpay_mapping.json"):
            (self.old.path.parent / name).write_text('{"version":"saved"}')
        self.run = self.old.create_run("Test week", date(2026, 9, 7), date(2026, 9, 13),
                                       {"version": "test"}, self.source.name, "test-hash", str(self.source))
        self.old.upsert_roster_entry(RosterEntry("test worker", "Test Worker", READY, "123"))
        self.old.add_recurring({"person_name": "Test Worker", "caregiver_key": "test worker",
                                "amount": "150", "frequency": "weekly", "starts_on": "2026-09-07",
                                "first_amount": "225"})
        self.old.add_note({"kind": "other", "detail": "Keep this note", "caregiver_name": "Test Worker"})
        self.old.set_entered(self.run, "test worker", True)
        self.old.finalize_run(self.run, ["123"], {"total_paid": "225.00"})

    def tearDown(self):
        self.old.close(); self.new.close(); self.tmp.cleanup()

    def test_history_works_after_original_mac_is_gone(self):
        raw = create_archive(self.old)
        self.assertEqual(self.old.get_run(self.run)["source_path"], str(self.source))
        self.source.unlink()
        manifest = restore_archive(raw, self.new)
        record = self.new.get_run(self.run)
        self.assertEqual(record["status"], "finalized")
        self.assertFalse(Path(record["source_path"]).is_absolute())
        self.assertTrue((self.new.path.parent / record["source_path"]).is_file())
        self.assertEqual(self.old.previously_paid(), self.new.previously_paid())
        self.assertEqual(self.new.list_recurring(), self.old.list_recurring())
        self.assertEqual(self.new.entered_map(self.run), self.old.entered_map(self.run))
        self.assertEqual(self.new.list_notes(), self.old.list_notes())
        self.assertEqual(self.new.roster(), self.old.roster())
        self.assertEqual(manifest["counts"]["runs"], 1)
        self.assertEqual((self.new.path.parent / "rules.json").read_text(), '{"version":"saved"}')

    def test_a_second_restore_cannot_overwrite_payroll(self):
        raw = create_archive(self.old)
        restore_archive(raw, self.new)
        with self.assertRaisesRegex(ValueError, "already has payroll"):
            restore_archive(raw, self.new)
        self.assertEqual(len(self.new.list_runs()), 1)

    def test_missing_export_prevents_incomplete_handoff(self):
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            create_archive(self.old)

    def test_corrupt_history_leaves_destination_empty(self):
        raw = create_archive(self.old)
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, "w") as dest:
            for item in source.infolist():
                data = source.read(item.filename)
                dest.writestr(item.filename, b"damaged" if item.filename == "payroll.sqlite3" else data)
        with self.assertRaisesRegex(ValueError, "verification"):
            restore_archive(out.getvalue(), self.new)
        self.assertEqual(self.new.list_runs(), [])
        self.assertFalse((self.new.path.parent / "uploads").exists())

    def test_archive_cannot_write_outside_destination(self):
        raw = io.BytesIO(create_archive(self.old))
        with zipfile.ZipFile(raw, "a") as archive:
            archive.writestr("../outside.txt", "bad")
        with self.assertRaisesRegex(ValueError, "unexpected path"):
            restore_archive(raw.getvalue(), self.new)
        self.assertFalse((self.root / "outside.txt").exists())


class PackagedAppIsolation(unittest.TestCase):
    def test_packaged_data_survives_replacing_the_application(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.frozen", True, create=True), patch("pathlib.Path.home", return_value=Path("/Users/test")):
            self.assertEqual(data_directory(), Path("/Users/test/Library/Application Support/Sitterwise Payroll"))

    def test_second_data_store_does_not_replace_running_payroll(self):
        state = json.dumps({"data_path": "/Users/first/payroll.sqlite3"}).encode()
        with patch("payroll.server.urllib.request.urlopen", return_value=io.BytesIO(state)):
            self.assertFalse(_same_data_on(8756, Path("/Users/second/payroll.sqlite3")))

    def test_old_app_without_data_identity_is_left_alone(self):
        with patch("payroll.server.urllib.request.urlopen", return_value=io.BytesIO(b'{"build":"old"}')):
            self.assertFalse(_same_data_on(8756, Path("/Users/second/payroll.sqlite3")))
