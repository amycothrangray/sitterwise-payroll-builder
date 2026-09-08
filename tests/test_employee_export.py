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
                            SETUP_INCOMPLETE, RosterEntry, assign_clock_users,
                            merge_import, parse_onpay_employee_export)

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

    def test_two_columns_of_names_and_clock_users_are_enough(self):
        """All the roster actually needs. Someone reading the numbers out of
        OnPay by hand should not have to pad the file out to be understood."""
        path = self.dir / "clock-users.csv"
        path.write_text("Name,Clock User\nTess Okafor,1042\nNia Vance,1043\n",
                        encoding="utf-8")
        entries, _ = parse_onpay_employee_export(path)
        self.assertEqual([(e.display_name, e.onpay_clock_user) for e in entries],
                         [("Tess Okafor", "1042"), ("Nia Vance", "1043")])
        self.assertEqual(entries[0].status, READY)

    def test_a_quoted_last_comma_first_name_is_turned_around(self):
        path = self.dir / "commas.csv"
        path.write_text('Name,Clock User\n"Okafor, Tess",1042\n', encoding="utf-8")
        entries, _ = parse_onpay_employee_export(path)
        self.assertEqual(entries[0].display_name, "Tess Okafor")
        self.assertEqual(entries[0].onpay_clock_user, "1042")

    def test_an_unquoted_comma_shifts_the_columns_and_is_refused(self):
        """Without the quotes the name splits in two and every column after
        it moves across, so the Clock User read for somebody would be half of
        their own name. Paying on that is worse than not importing."""
        path = self.dir / "shifted.csv"
        path.write_text("Name,Clock User\nOkafor, Tess,1042\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            parse_onpay_employee_export(path)
        self.assertIn("quotes", str(caught.exception))

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

    def test_a_later_import_of_clock_users_keeps_the_rates(self):
        """The employee export carries rates and no Clock Users; a list of
        Clock Users carries no rates. Two imports have to add up, not take
        turns rubbing each other out."""
        changed, _ = merge_import(
            self.roster(RosterEntry("tess okafor", "Tess Okafor", onpay_rate="23",
                                    onpay_pay_type="Hourly", onpay_pay_frequency="Weekly")),
            [RosterEntry("tess okafor", "Tess Okafor", onpay_clock_user="1042")])
        entry = changed[0]
        self.assertEqual(entry.onpay_clock_user, "1042")
        self.assertEqual((entry.onpay_rate, entry.onpay_pay_type), ("23", "Hourly"))

    def test_a_changed_rate_does_come_through(self):
        """Filling blanks is not the same as ignoring what OnPay says. A rate
        that is actually in the file wins."""
        changed, _ = merge_import(
            self.roster(RosterEntry("tess okafor", "Tess Okafor", onpay_rate="20")),
            [RosterEntry("tess okafor", "Tess Okafor", onpay_rate="23")])
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


class HandingOutClockUsers(unittest.TestCase):
    """OnPay confirmed on 8 September 2026 that the payroll import identifies
    people by Clock User, that it is required, and that there is no bulk
    import - each one is typed into a profile by hand. So the numbers are
    ours to choose, and the app has to hold the same ones it hands over."""

    def roster(self, *entries):
        return {e.caregiver_key: e for e in entries}

    def test_everybody_without_one_gets_a_number(self):
        roster = self.roster(RosterEntry("a", "Abigail Currie"),
                             RosterEntry("b", "Beth Jones"))
        assign_clock_users(roster)
        self.assertEqual([roster["a"].onpay_clock_user, roster["b"].onpay_clock_user],
                         ["1001", "1002"])

    def test_a_number_somebody_already_has_is_left_alone(self):
        roster = self.roster(RosterEntry("a", "Abigail Currie", onpay_clock_user="77"))
        assign_clock_users(roster)
        self.assertEqual(roster["a"].onpay_clock_user, "77")

    def test_a_number_in_use_is_never_given_to_a_second_person(self):
        """Two caregivers on one Clock User puts one person's hours on the
        other one's pay."""
        roster = self.roster(RosterEntry("a", "Abigail Currie"),
                             RosterEntry("c", "Cara Lin", onpay_clock_user="1002"),
                             RosterEntry("d", "Dana Reyes"),
                             RosterEntry("e", "Eve Marsh"))
        assign_clock_users(roster)
        numbers = [e.onpay_clock_user for e in roster.values()]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertNotIn("1002", [roster["a"].onpay_clock_user,
                                  roster["d"].onpay_clock_user,
                                  roster["e"].onpay_clock_user])

    def test_running_it_twice_changes_nothing(self):
        roster = self.roster(RosterEntry("a", "Abigail Currie"),
                             RosterEntry("b", "Beth Jones"))
        assign_clock_users(roster)
        before = {k: e.onpay_clock_user for k, e in roster.items()}
        self.assertEqual(assign_clock_users(roster), [])
        self.assertEqual({k: e.onpay_clock_user for k, e in roster.items()}, before)

    def test_it_reports_only_the_people_it_changed(self):
        roster = self.roster(RosterEntry("a", "Abigail Currie", onpay_clock_user="1001"),
                             RosterEntry("b", "Beth Jones"))
        self.assertEqual([e.display_name for e in assign_clock_users(roster)],
                         ["Beth Jones"])

    def test_the_numbers_follow_the_order_the_roster_is_shown_in(self):
        roster = self.roster(RosterEntry("z", "Zara Quinn"),
                             RosterEntry("a", "Abigail Currie"))
        assign_clock_users(roster)
        self.assertLess(int(roster["a"].onpay_clock_user),
                        int(roster["z"].onpay_clock_user))

    def test_a_number_belonging_to_somebody_who_left_is_not_recycled(self):
        roster = self.roster(RosterEntry("gone", "Tess Okafor", status=SETUP_INCOMPLETE,
                                         onpay_clock_user="1001"),
                             RosterEntry("new", "Nia Vance"))
        assign_clock_users(roster)
        self.assertNotEqual(roster["new"].onpay_clock_user, "1001")
