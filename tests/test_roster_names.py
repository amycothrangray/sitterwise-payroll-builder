"""Matching a caregiver to their OnPay record when the names differ.

OnPay holds people under their legal name. Lissa's OnPay record is Elisabeth
R Gray. Married names, preferred names and middle initials all do this. If
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
            caregiver_key="lissa", display_name="Lissa", status=READY,
            onpay_clock_user="LG100", onpay_name="Elisabeth R Gray"))
        entry = self.store.roster()["lissa"]
        self.assertEqual(entry.display_name, "Lissa")
        self.assertEqual(entry.onpay_name, "Elisabeth R Gray")

    def test_it_survives_being_read_back_and_written_again(self):
        self.store.upsert_roster_entry(RosterEntry(
            caregiver_key="lissa", display_name="Lissa", status=READY,
            onpay_name="Elisabeth R Gray"))
        entry = self.store.roster()["lissa"]
        entry.onpay_clock_user = "LG100"
        self.store.upsert_roster_entry(entry)
        self.assertEqual(self.store.roster()["lissa"].onpay_name, "Elisabeth R Gray")

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
            old.execute("INSERT INTO roster VALUES ('lissa','Lissa','onpay_ready',"
                        "'LG100','','',NULL,'manual')")
            old.commit()
            old.close()

            store = Store(path)
            try:
                entry = store.roster()["lissa"]
                self.assertEqual(entry.display_name, "Lissa")
                self.assertEqual(entry.onpay_name, "")
                entry.onpay_name = "Elisabeth R Gray"
                store.upsert_roster_entry(entry)
                self.assertEqual(store.roster()["lissa"].onpay_name, "Elisabeth R Gray")
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
        self.assertEqual(normalise_name("  Elisabeth   R Gray "),
                         normalise_name("elisabeth r gray"))

    def test_a_legal_name_and_a_working_name_are_different_keys(self):
        # Which is exactly why the roster has to record the link.
        self.assertNotEqual(normalise_name("Lissa"),
                            normalise_name("Elisabeth R Gray"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
