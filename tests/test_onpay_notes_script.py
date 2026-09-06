"""The script Lissa runs to put the notes on OnPay's payroll lines.

Caregivers see these notes on their pay stubs. OnPay's CSV import has no
column for them and OnPay has no API that writes payroll, so they are pasted
in one at a time - and the script's job is to make that a click and a paste
rather than something to read off a screen and retype.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import onpay_notes                                                     # noqa: E402
from payroll.store import Store                                        # noqa: E402


class WhereTheWalkthroughGotTo(unittest.TestCase):
    """Forty notes is long enough that an interruption must not cost the lot."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "payroll.sqlite3")
        self.progress = onpay_notes.Progress(self.store, "run123")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_nothing_done_yet_starts_at_the_beginning(self):
        self.assertEqual(self.progress.done, 0)

    def test_it_remembers_how_far_you_got(self):
        self.progress.save(25)
        self.assertEqual(onpay_notes.Progress(self.store, "run123").done, 25)

    def test_each_payroll_keeps_its_own_place(self):
        self.progress.save(25)
        other = onpay_notes.Progress(self.store, "run456")
        self.assertEqual(other.done, 0)
        other.save(3)
        self.assertEqual(self.progress.done, 25)

    def test_finishing_clears_it(self):
        self.progress.save(40)
        self.progress.clear()
        self.assertEqual(self.progress.done, 0)

    def test_a_damaged_progress_file_is_not_a_crash(self):
        # Losing your place is a nuisance; a traceback mid-payroll is worse.
        self.progress.path.write_text("this is not json", encoding="utf-8")
        self.assertEqual(self.progress.done, 0)

    def test_it_is_kept_beside_the_history_not_inside_it(self):
        self.progress.save(5)
        self.assertTrue(self.progress.path.exists())
        self.assertNotEqual(self.progress.path, Path(self.store.path))


class ReadingAPayLineOutLoud(unittest.TestCase):
    def test_an_hourly_line_shows_the_rate_to_the_cent(self):
        # The file keeps four decimal places; a person reading it does not
        # need to see $23.0000.
        from decimal import Decimal
        text = onpay_notes.describe({
            "hours": Decimal("8.00"), "rate": Decimal("23.0000"),
            "amount": Decimal("184.00")})
        self.assertIn("$23.00", text)
        self.assertNotIn("23.0000", text)

    def test_a_flat_line_shows_only_the_amount(self):
        from decimal import Decimal
        text = onpay_notes.describe({
            "hours": None, "rate": None, "amount": Decimal("76.00")})
        self.assertEqual(text, "$76.00")


class PickingThePayrollToWorkOn(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "payroll.sqlite3")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_no_payrolls_at_all_is_not_a_crash(self):
        self.assertIsNone(onpay_notes.latest_run(self.store))


if __name__ == "__main__":
    unittest.main(verbosity=2)
