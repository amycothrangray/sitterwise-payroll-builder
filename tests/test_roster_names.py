"""Matching a caregiver to their OnPay record when the names differ.

OnPay holds people under their legal name. Jay's OnPay record is Jamie
R Rivers. Married names, preferred names and middle initials all do this. If
the roster matched on the Sitterwise name alone, importing OnPay's employee
list would quietly make a second entry for the same person and then report
the first as missing from OnPay.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.roster import READY, RosterEntry, normalise_name              # noqa: E402
from payroll.store import Store                                            # noqa: E402


class TheRosterRemembersTheOnPayName(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "payroll.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_a_legal_name_is_kept_alongside_the_working_name(self):
        self.store.upsert_roster_entry(RosterEntry(
            caregiver_key="jay", display_name="Jay", status=READY,
            onpay_clock_user="LG100", onpay_name="Jamie R Rivers"))
        entry = self.store.roster()["jay"]
        self.assertEqual(entry.display_name, "Jay")
        self.assertEqual(entry.onpay_name, "Jamie R Rivers")

    def test_it_survives_being_read_back_and_written_again(self):
        self.store.upsert_roster_entry(RosterEntry(
            caregiver_key="jay", display_name="Jay", status=READY,
            onpay_name="Jamie R Rivers"))
        entry = self.store.roster()["jay"]
        entry.onpay_clock_user = "LG100"
        self.store.upsert_roster_entry(entry)
        self.assertEqual(self.store.roster()["jay"].onpay_name, "Jamie R Rivers")

    def test_the_name_is_absent_by_default(self):
        self.store.upsert_roster_entry(
            RosterEntry(caregiver_key="tess", display_name="Tess Okafor", status=READY))
        self.assertEqual(self.store.roster()["tess"].onpay_name, "")


class AnOlderDatabaseGetsTheNewColumn(unittest.TestCase):
    """CREATE TABLE IF NOT EXISTS leaves an existing table alone."""

    def test_a_database_from_before_this_change_still_opens(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "payroll.sqlite3"
            old = sqlite3.connect(path)
            old.execute("""CREATE TABLE roster (
                caregiver_key TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                status TEXT NOT NULL, onpay_clock_user TEXT DEFAULT '',
                onpay_employee_id TEXT DEFAULT '', note TEXT DEFAULT '',
                updated_at TEXT, source TEXT DEFAULT 'manual')""")
            old.execute("INSERT INTO roster VALUES ('jay','Jay','onpay_ready',"
                        "'LG100','','',NULL,'manual')")
            old.commit()
            old.close()

            store = Store(path)
            try:
                entry = store.roster()["jay"]
                self.assertEqual(entry.display_name, "Jay")
                self.assertEqual(entry.onpay_name, "")
                entry.onpay_name = "Jamie R Rivers"
                store.upsert_roster_entry(entry)
                self.assertEqual(store.roster()["jay"].onpay_name, "Jamie R Rivers")
            finally:
                store.close()

    def test_opening_twice_does_not_add_the_column_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "payroll.sqlite3"
            Store(path).close()
            store = Store(path)          # would raise if the ALTER ran again
            store.close()


class NamesAreComparedTheSameWayEverywhere(unittest.TestCase):
    def test_spacing_and_case_do_not_make_a_different_person(self):
        self.assertEqual(normalise_name("  Jamie   R Rivers "),
                         normalise_name("jamie r rivers"))

    def test_a_legal_name_and_a_working_name_are_different_keys(self):
        # Which is exactly why the roster has to record the link.
        self.assertNotEqual(normalise_name("Jay"),
                            normalise_name("Jamie R Rivers"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
