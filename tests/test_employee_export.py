"""Reading OnPay's Employee Detail export.

This is the file Amy gets out of OnPay when she asks for the employee list,
and it is not shaped the way the app first assumed. The columns start on the
fourth row, under a title block. Names are split across First, Middle and
Last. Everybody the company has ever hired is on it, not the handful working
this week. People who have left are on a second tab. And it carries no Clock
User at all - which is the one number the payroll import file needs, so an
import of this file has to say so instead of quietly marking people ready.

No real employee data is in here. The export carries names, addresses and
social security numbers for several hundred people, so the fixtures below
are made up to the same shape.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.roster import (DIRECT_DEPOSIT_INCOMPLETE, READY,               # noqa: E402
                            SETUP_INCOMPLETE, RosterEntry, merge_import,
                            parse_onpay_employee_export)

COLUMNS = ["Last Name", "First Name", "Middle Name", "Employee Number", "Social",
           "Hire Date", "Termination Date", "Type", "Rate", "Pay Frequency"]

# Rows 1-3 of the real export: the report's name, the company, and when it
# was run. The column names are on row 4.
PREAMBLE = [["Employee Detail"] + [""] * 9,
            ["Sitterwise, Inc.", "", "", "Id: ", "20364", "Date:", "9/7/2026", "", "", ""],
            [""] * 10]


def person(last, first, middle="", **kw):
    row = {c: "" for c in COLUMNS}
    row.update({"Last Name": last, "First Name": first, "Middle Name": middle,
                "Type": "Hourly", "Rate": "23", "Pay Frequency": "Weekly"})
    row.update(kw)
    return [row[c] for c in COLUMNS]


def write_export(tmp: Path, active, inactive=(), name="employees.xlsx") -> Path:
    import openpyxl
    book = openpyxl.Workbook()
    for index, (title, people) in enumerate((("Active", active), ("Inactive", inactive))):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.title = title
        for row in PREAMBLE + [COLUMNS] + list(people):
            sheet.append(row)
    path = tmp / name
    book.save(path)
    return path


class TheEmployeeDetailExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, active, inactive=()):
        entries, problems = parse_onpay_employee_export(
            write_export(self.dir, active, inactive))
        return {e.caregiver_key: e for e in entries}, problems

    def test_the_columns_are_found_under_the_title_block(self):
        people, _ = self.read([person("Acosta", "Danielle", "G")])
        self.assertIn("danielle acosta", people)

    def test_a_first_and_last_name_become_the_name_shown(self):
        people, _ = self.read([person("Acosta", "Danielle", "G")])
        self.assertEqual(people["danielle acosta"].display_name, "Danielle Acosta")

    def test_the_middle_name_is_kept_as_onpays_name_not_as_the_name_shown(self):
        """The name shown stays the one Sitterwise uses; OnPay's fuller
        version is what a later import matches them on."""
        people, _ = self.read([person("Acosta", "Danielle", "G")])
        self.assertEqual(people["danielle acosta"].display_name, "Danielle Acosta")
        self.assertEqual(people["danielle acosta"].onpay_name, "Danielle G Acosta")

    def test_no_middle_name_means_nothing_to_record(self):
        people, _ = self.read([person("Okafor", "Tess")])
        self.assertEqual(people["tess okafor"].onpay_name, "")

    def test_lissa_is_recognised_on_a_second_import(self):
        """Her roster entry says Lissa Gray; OnPay says Elisabeth R Gray."""
        people, _ = self.read([person("Gray", "Elisabeth", "R", Type="Salary",
                                      Rate="7400", **{"Pay Frequency": "Monthly"})])
        changed, report = merge_import(
            {"lissa gray": RosterEntry("lissa gray", "Lissa Gray",
                                       onpay_name="Elisabeth R Gray")},
            list(people.values()))
        self.assertEqual(report["linked"], 1)
        self.assertEqual(changed[0].display_name, "Lissa Gray")
        self.assertEqual(changed[0].onpay_rate, "7400")

    def test_the_onpay_rate_comes_across(self):
        people, _ = self.read([person("Acosta", "Danielle", Rate="23")])
        entry = people["danielle acosta"]
        self.assertEqual(entry.onpay_rate, "23")
        self.assertEqual(entry.onpay_pay_type, "Hourly")
        self.assertEqual(entry.onpay_pay_frequency, "Weekly")

    def test_a_rate_is_kept_exactly_as_exported(self):
        """A rate is money. Nothing on the way in may round it."""
        people, _ = self.read([person("Vance", "Nia", Rate="23.755")])
        self.assertEqual(people["nia vance"].onpay_rate, "23.755")

    def test_a_salary_is_read_as_a_salary(self):
        people, _ = self.read([person("Gray", "Elisabeth", "R", Type="Salary",
                                      Rate="7400", **{"Pay Frequency": "Monthly"})])
        entry = people["elisabeth gray"]
        self.assertEqual((entry.onpay_pay_type, entry.onpay_pay_frequency),
                         ("Salary", "Monthly"))


class PeopleWhoAreNoLongerHere(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, active, inactive=()):
        entries, problems = parse_onpay_employee_export(
            write_export(self.dir, active, inactive))
        return {e.caregiver_key: e for e in entries}, problems

    def test_the_inactive_tab_is_read_too(self):
        people, _ = self.read([], [person("Okafor", "Tess")])
        self.assertIn("tess okafor", people)

    def test_somebody_on_the_inactive_tab_is_never_ready_to_pay(self):
        people, _ = self.read([], [person("Okafor", "Tess")])
        self.assertNotEqual(people["tess okafor"].status, READY)
        self.assertIn("no longer employed", people["tess okafor"].note)

    def test_a_termination_date_says_the_same_thing(self):
        people, _ = self.read([person("Okafor", "Tess",
                                      **{"Termination Date": "2026-03-01"})])
        self.assertNotEqual(people["tess okafor"].status, READY)

    def test_a_rehire_is_one_person_and_the_live_record_wins(self):
        """A closed record and a live one under one name is somebody who came
        back, not two people. Paying them off the closed record would pay a
        rate nobody is on any more."""
        people, problems = self.read([person("Gutierrez", "Jessica", Rate="15")],
                                     [person("Gutierrez", "Jessica", "A", Rate="7.25")])
        self.assertEqual(people["jessica gutierrez"].onpay_rate, "15")
        self.assertIn("closed record", people["jessica gutierrez"].note)
        self.assertFalse([p for p in problems if "match them up by hand" in p])


class TwoPeopleWithTheSameName(unittest.TestCase):
    """Sitterwise really does have two Maria Brants, on different rates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, active):
        entries, problems = parse_onpay_employee_export(write_export(self.dir, active))
        return {e.caregiver_key: e for e in entries}, problems

    def test_neither_of_them_is_imported(self):
        people, _ = self.read([person("Brant", "Maria", Rate="20"),
                               person("Brant", "Maria", Rate="23")])
        self.assertNotIn("maria brant", people)

    def test_the_name_is_reported_rather_than_silently_dropped(self):
        _, problems = self.read([person("Brant", "Maria", Rate="20"),
                                 person("Brant", "Maria", Rate="23")])
        self.assertTrue(any("Maria Brant" in p for p in problems))

    def test_everybody_else_still_comes_through(self):
        people, _ = self.read([person("Brant", "Maria", Rate="20"),
                               person("Brant", "Maria", Rate="23"),
                               person("Okafor", "Tess")])
        self.assertIn("tess okafor", people)


class WithoutAClockUser(unittest.TestCase):
    """The number the payroll import file identifies people by. This export
    has no column for it, and OnPay's Employee Number is empty for everybody,
    so an import of it cannot make anyone ready to pay."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nobody_is_marked_ready(self):
        entries, _ = parse_onpay_employee_export(
            write_export(self.dir, [person("Okafor", "Tess")]))
        self.assertNotEqual(entries[0].status, READY)

    def test_it_says_why(self):
        _, problems = parse_onpay_employee_export(
            write_export(self.dir, [person("Okafor", "Tess")]))
        self.assertTrue(any("Clock User" in p for p in problems))

    def test_an_export_that_does_carry_one_marks_people_ready(self):
        path = self.dir / "with-clock.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["First Name", "Last Name", "Clock User",
                             "Direct Deposit", "Rate"])
            writer.writerow(["Tess", "Okafor", "1042", "Yes", "23"])
        entries, problems = parse_onpay_employee_export(path)
        self.assertEqual(entries[0].status, READY)
        self.assertEqual(entries[0].onpay_clock_user, "1042")
        self.assertFalse([p for p in problems if "Clock User" in p])

    def test_no_direct_deposit_column_is_still_reported(self):
        _, problems = parse_onpay_employee_export(
            write_export(self.dir, [person("Okafor", "Tess")]))
        self.assertTrue(any("direct deposit" in p for p in problems))

    def test_a_file_with_no_recognisable_columns_is_refused(self):
        path = self.dir / "wrong.csv"
        path.write_text("total,amount\n10,20\n", encoding="utf-8")
        entries, problems = parse_onpay_employee_export(path)
        self.assertEqual(entries, [])
        self.assertTrue(problems)


class FoldingTheExportIntoTheRosterHere(unittest.TestCase):
    """The roster is who Sitterwise pays. The export is everybody OnPay has
    on file - 625 people against the sixty-odd who worked this month."""

    def roster(self, *entries):
        return {e.caregiver_key: e for e in entries}

    def test_somebody_who_has_never_worked_here_is_not_added(self):
        changed, report = merge_import(
            self.roster(RosterEntry("tess okafor", "Tess Okafor")),
            [RosterEntry("tess okafor", "Tess Okafor", onpay_rate="23"),
             RosterEntry("nia vance", "Nia Vance", onpay_rate="20")])
        self.assertEqual([e.caregiver_key for e in changed], ["tess okafor"])
        self.assertEqual(report["in_onpay_only"], 1)

    def test_a_clock_user_typed_in_by_hand_survives_an_import_without_one(self):
        """This export has no Clock User column. Importing it must not undo
        the number Amy typed in, which is what the payroll file pays on."""
        changed, _ = merge_import(
            self.roster(RosterEntry("tess okafor", "Tess Okafor", status=READY,
                                    onpay_clock_user="1042")),
            [RosterEntry("tess okafor", "Tess Okafor",
                         status=SETUP_INCOMPLETE, onpay_rate="23")])
        self.assertEqual(changed[0].onpay_clock_user, "1042")
        self.assertEqual(changed[0].status, READY)

    def test_it_still_brings_the_rate_across(self):
        changed, _ = merge_import(
            self.roster(RosterEntry("tess okafor", "Tess Okafor", status=READY,
                                    onpay_clock_user="1042")),
            [RosterEntry("tess okafor", "Tess Okafor", onpay_rate="23",
                         onpay_pay_type="Hourly")])
        self.assertEqual(changed[0].onpay_rate, "23")

    def test_a_note_somebody_wrote_is_left_alone(self):
        changed, _ = merge_import(
            self.roster(RosterEntry("tess okafor", "Tess Okafor",
                                    note="Ask her about the Tuesday job")),
            [RosterEntry("tess okafor", "Tess Okafor", note="OnPay has them as Tess A Okafor.")])
        self.assertEqual(changed[0].note, "Ask her about the Tuesday job")

    def test_the_apps_own_placeholder_note_is_replaced(self):
        changed, _ = merge_import(
            self.roster(RosterEntry("tess okafor", "Tess Okafor",
                                    source="added_automatically",
                                    note="Added automatically - confirm their OnPay setup")),
            [RosterEntry("tess okafor", "Tess Okafor", note="OnPay has them as Tess A Okafor.")])
        self.assertEqual(changed[0].note, "OnPay has them as Tess A Okafor.")

    def test_lissa_is_matched_under_her_legal_name(self):
        changed, report = merge_import(
            self.roster(RosterEntry("lissa gray", "Lissa Gray",
                                    onpay_name="Elisabeth R Gray")),
            [RosterEntry("elisabeth r gray", "Elisabeth R Gray",
                         onpay_rate="7400", onpay_pay_type="Salary")])
        self.assertEqual(report["linked"], 1)
        self.assertEqual(report["in_onpay_only"], 0)
        self.assertEqual(changed[0].display_name, "Lissa Gray")
        self.assertEqual(changed[0].onpay_name, "Elisabeth R Gray")
        self.assertEqual(changed[0].onpay_rate, "7400")

    def test_somebody_the_export_never_mentions_is_named_back(self):
        _, report = merge_import(
            self.roster(RosterEntry("june salter", "June Salter")),
            [RosterEntry("tess okafor", "Tess Okafor")])
        self.assertEqual(report["not_in_onpay_file"], ["June Salter"])


if __name__ == "__main__":
    unittest.main()
