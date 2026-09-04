"""The file OnPay's CSV importer actually takes.

OnPay sent the specification on 4 September 2026: one row per pay item,
eight fixed columns, numeric pay types, and only one row per employee for
pay item 1 and one for pay item 2. That last rule is what shapes the file.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import io
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll import exports                                              # noqa: E402
from payroll.roster import READY, RosterEntry                            # noqa: E402
from payroll.rules import Rules                                          # noqa: E402
from payroll.run import build_run                                        # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "test-payroll.xlsx"
WEEK_START, WEEK_END = date(2026, 8, 3), date(2026, 8, 9)
LISSA = {"person_name": "Lissa Trevino", "caregiver_key": "lissa trevino",
         "amount": "1500.00", "frequency": "monthly", "schedule": "first_monday",
         "taxable": 1, "active": 1, "note": "Monthly salary"}


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class OnPayImportFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rules = Rules.load()
        first = build_run(FIXTURE, rules, WEEK_START, WEEK_END, recurring=[LISSA])
        cls.roster = {
            c.key: RosterEntry(c.key, c.name or "Unnamed", READY,
                               onpay_clock_user=f"SW{i:03d}")
            for i, c in enumerate(first.caregivers, 1)}
        cls.payroll = build_run(FIXTURE, rules, WEEK_START, WEEK_END,
                            roster=cls.roster, recurring=[LISSA])
        cls.mapping = exports.load_onpay_mapping()
        cls.csv_text, cls.skipped = exports.onpay_import_csv(cls.payroll, cls.roster)
        cls.rows = list(csv.DictReader(io.StringIO(cls.csv_text)))

    def person(self, name):
        return next(c for c in self.payroll.caregivers if c.name == name)

    def rows_for(self, name):
        clock = self.roster[self.person(name).key].onpay_clock_user
        return [r for r in self.rows if r["emp_num"] == clock]

    # -- the shape OnPay demands -------------------------------------------

    def test_the_columns_are_the_ones_onpay_specified(self):
        self.assertEqual(self.csv_text.splitlines()[0].split(","),
                         ["type", "id", "emp_num", "hours", "rate", "treat_as_cash",
                          "cash_amount", "ob3_qualified_ot"])

    def test_every_row_is_a_pay_item(self):
        self.assertTrue(self.rows)
        for row in self.rows:
            self.assertEqual(row["type"], "1")

    def test_nobody_appears_twice_on_pay_item_1_or_2(self):
        seen: dict[tuple[str, str], int] = {}
        for row in self.rows:
            if row["id"] in ("1", "2"):
                key = (row["emp_num"], row["id"])
                seen[key] = seen.get(key, 0) + 1
        repeated = [k for k, n in seen.items() if n > 1]
        self.assertEqual(repeated, [], "OnPay rejects a file with these in it")

    # -- the money has to be the same money --------------------------------

    def test_what_onpay_will_pay_equals_what_the_app_worked_out(self):
        filed = Decimal("0")
        for row in self.rows:
            if row["cash_amount"]:
                filed += Decimal(row["cash_amount"])
            elif row["hours"] and row["rate"]:
                filed += money(Decimal(row["hours"]) * Decimal(row["rate"]))
        expected = sum(
            (c.total_paid for c in self.payroll.caregivers
             if c.name not in self.skipped
             and self.payroll.summary["statuses"].get(c.key) != "blocked"),
            Decimal("0"))
        self.assertEqual(filed, money(expected))

    def test_the_app_reports_no_problems_with_its_own_file(self):
        real = [p for p in exports.onpay_import_check(self.payroll, self.roster)
                if "left out" not in p["problem"] and "Clock User" not in p["problem"]]
        self.assertEqual(real, [])

    # -- one rate: the ordinary presentation --------------------------------

    def test_one_rate_pays_overtime_at_time_and_a_half(self):
        # Dana Reyes: 14 hours at $23, two of them overtime.
        rows = {r["id"]: r for r in self.rows_for("Dana Reyes")}
        self.assertEqual(Decimal(rows["1"]["hours"]), 12)
        self.assertEqual(Decimal(rows["1"]["rate"]), Decimal("23"))
        self.assertEqual(Decimal(rows["2"]["hours"]), 2)
        self.assertEqual(Decimal(rows["2"]["rate"]), Decimal("34.5"))
        total = sum((money(Decimal(r["hours"]) * Decimal(r["rate"]))
                     for r in rows.values() if r["hours"]), Decimal("0"))
        self.assertEqual(total, self.person("Dana Reyes").total_paid)

    def test_double_time_gets_its_own_pay_type(self):
        rows = {r["id"]: r for r in self.rows_for("Priya Raman")}
        self.assertIn("22", rows)
        self.assertEqual(Decimal(rows["22"]["rate"]), Decimal("46"))

    def test_the_four_hour_minimum_rides_in_the_regular_row(self):
        # Belle Cruz worked 2.5 hours and is paid 4.
        rows = {r["id"]: r for r in self.rows_for("Belle Cruz")}
        self.assertEqual(Decimal(rows["1"]["hours"]), 4)
        self.assertEqual(money(Decimal(rows["1"]["hours"]) * Decimal(rows["1"]["rate"])),
                         self.person("Belle Cruz").total_paid)

    # -- two rates: real rates kept, premium-only overtime ------------------

    def test_two_rates_are_never_blended_into_one_invented_rate(self):
        rows = self.rows_for("Tess Okafor")
        rates = {Decimal(r["rate"]) for r in rows if r["rate"] and r["id"] != "2"}
        self.assertEqual(rates, {Decimal("28"), Decimal("23")})
        self.assertNotIn(Decimal("25.5"), rates, "that rate was never worked")

    def test_the_higher_tier_gets_its_own_hourly_pay_type(self):
        ids = {r["id"] for r in self.rows_for("Tess Okafor")}
        self.assertIn(str(self.mapping["tier_pay_ids"]["three_to_four"]), ids)

    def test_two_rate_overtime_carries_only_the_premium(self):
        rows = {r["id"]: r for r in self.rows_for("Tess Okafor")}
        # Weighted regular rate is $25.50, so the premium alone is $12.75.
        self.assertEqual(Decimal(rows["2"]["rate"]), Decimal("12.75"))

    def test_a_two_rate_week_still_comes_to_the_right_money(self):
        total = sum(
            (money(Decimal(r["hours"]) * Decimal(r["rate"]))
             for r in self.rows_for("Tess Okafor") if r["hours"]),
            Decimal("0"))
        self.assertEqual(total, self.person("Tess Okafor").total_paid)

    # -- the flat-money rows ------------------------------------------------

    def test_salary_is_a_cash_amount_with_no_hours_and_no_rate(self):
        rows = self.rows_for("Lissa Trevino")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], "1")
        self.assertEqual(row["hours"], "")
        self.assertEqual(row["rate"], "")
        self.assertEqual(Decimal(row["cash_amount"]), Decimal("1500"))

    def test_reimbursements_go_on_the_non_taxable_pay_type(self):
        wanted = str(self.mapping["pay_ids"]["reimbursement"])
        for caregiver in self.payroll.caregivers:
            if caregiver.reimbursements and caregiver.name not in self.skipped:
                rows = {r["id"]: r for r in self.rows_for(caregiver.name)}
                self.assertIn(wanted, rows, caregiver.name)
                self.assertEqual(Decimal(rows[wanted]["cash_amount"]),
                                 caregiver.reimbursements)
                self.assertEqual(rows[wanted]["treat_as_cash"], "1")

    def test_tips_use_the_type_onpay_confirmed(self):
        self.assertEqual(self.mapping["pay_ids"]["tips"], 208)

    # -- what never reaches OnPay -------------------------------------------

    def test_a_caregiver_the_check_stopped_is_left_out(self):
        self.assertIn("June Salter", self.skipped)

    def test_a_rate_the_app_could_not_work_out_never_reaches_payroll(self):
        # June Salter's only available rate is one divided out of the amount
        # paid. Writing that into OnPay would pay a rate nobody agreed to.
        self.assertNotIn("24.9975", self.csv_text)

    def test_ob3_carries_all_overtime_hours(self):
        for row in self.rows:
            if row["id"] == "2":
                self.assertEqual(row["ob3_qualified_ot"], row["hours"])
            if row["id"] == "22":
                self.assertEqual(row["ob3_qualified_ot"], row["hours"])

    def test_somebody_with_no_clock_user_is_named_rather_than_dropped(self):
        roster = dict(self.roster)
        victim = self.person("Tess Okafor")
        roster[victim.key] = RosterEntry(victim.key, victim.name, READY,
                                         onpay_clock_user="")
        _, skipped = exports.onpay_import_csv(self.payroll, roster)
        self.assertIn(victim.name, skipped)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ThePayItemMapping(unittest.TestCase):
    """OnPay's pay items are identified by internal id, not by their name.

    'Custom 1' is id 4 and 'Custom 4' is id 119, which is exactly the sort of
    thing that gets renamed in the wrong place.
    """

    def setUp(self):
        self.mapping = exports.load_onpay_mapping()
        self.tiers = {k: v for k, v in self.mapping["tier_pay_ids"].items()
                      if not k.startswith("_")}

    def test_the_live_mapping_is_sound(self):
        self.assertEqual(exports.onpay_mapping_problems(self.mapping), [])

    def test_the_standard_rate_is_onpay_pay_item_1(self):
        self.assertEqual(self.tiers["standard"], 1)

    def test_the_higher_tier_points_at_the_item_that_was_renamed(self):
        # Amy renamed OnPay's "Custom 4", whose internal id is 119.
        self.assertEqual(self.tiers["three_to_four"], 119)

    def test_no_two_tiers_share_a_pay_item(self):
        ids = list(self.tiers.values())
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_tier_never_lands_on_a_flat_money_pay_item(self):
        flat = {self.mapping["pay_ids"][k] for k in ("bonus", "tips", "reimbursement")}
        for name, pay_id in self.tiers.items():
            self.assertNotIn(pay_id, flat, name)

    def test_two_tiers_on_one_item_is_reported_not_shipped(self):
        broken = dict(self.mapping)
        broken["tier_pay_ids"] = {"standard": 1, "three_to_four": 1}
        problems = exports.onpay_mapping_problems(broken)
        self.assertTrue(problems)
        self.assertIn("OnPay rejects", problems[0])

    def test_the_standard_rate_being_moved_off_item_1_is_reported(self):
        broken = dict(self.mapping)
        broken["tier_pay_ids"] = {"standard": 119, "three_to_four": 1}
        self.assertTrue(exports.onpay_mapping_problems(broken))


class NotesForThePayLines(unittest.TestCase):
    """Ethan typed job dates and family names onto each OnPay payroll line so
    a caregiver could see what she was being paid for. OnPay's import file has
    no column for that, so the app works out the wording and it gets typed in.
    """

    @classmethod
    def setUpClass(cls):
        rules = Rules.load()
        first = build_run(FIXTURE, rules, WEEK_START, WEEK_END, recurring=[LISSA])
        cls.roster = {
            c.key: RosterEntry(c.key, c.name or "Unnamed", READY,
                               onpay_clock_user=f"SW{i:03d}")
            for i, c in enumerate(first.caregivers, 1)}
        cls.payroll = build_run(FIXTURE, rules, WEEK_START, WEEK_END,
                                roster=cls.roster, recurring=[LISSA])
        cls.mapping = exports.load_onpay_mapping()

    def lines(self, name):
        person = next(c for c in self.payroll.caregivers if c.name == name)
        return exports.onpay_pay_rows(
            person, self.roster[person.key].onpay_clock_user, self.mapping)

    def test_the_import_file_has_no_column_for_a_note(self):
        # If OnPay ever adds one, this is the test that should fail.
        self.assertNotIn("note", exports.ONPAY_HEADER)

    def test_a_note_never_reaches_the_import_file(self):
        text, _ = exports.onpay_import_csv(self.payroll, self.roster)
        for row in csv.reader(io.StringIO(text)):
            self.assertLessEqual(len(row), len(exports.ONPAY_HEADER))

    def test_the_hours_line_names_the_days_and_families(self):
        note = next(l["note"] for l in self.lines("Dana Reyes") if l["id"] == "1")
        self.assertTrue(note)
        self.assertRegex(note, r"[A-Z][a-z]{2} \d")

    def test_the_overtime_line_says_which_day_it_fell_on(self):
        note = next(l["note"] for l in self.lines("Dana Reyes") if l["id"] == "2")
        self.assertRegex(note, r"[A-Z][a-z]{2} \d")

    def test_a_mileage_note_says_the_miles_being_paid_for(self):
        for caregiver in self.payroll.caregivers:
            if caregiver.mileage_amount and caregiver.name not in ("June Salter",):
                lines = [l for l in self.lines(caregiver.name) if l["id"] == "107"]
                if not lines:
                    continue
                self.assertIn("mileage", lines[0]["note"])
                self.assertIn("paid", lines[0]["note"],
                              "payable miles, not the round trip")
                return
        self.skipTest("no mileage in this week")

    def test_a_salary_note_is_the_human_half_not_the_audit_wording(self):
        note = self.lines(LISSA["person_name"])[0]["note"]
        self.assertEqual(note, "Monthly salary")
        self.assertNotIn("Settings", note)

    def test_every_line_carries_a_note_field_even_when_empty(self):
        for caregiver in self.payroll.caregivers:
            for line in self.lines(caregiver.name):
                self.assertIn("note", line)

    def test_the_lines_sheet_lists_a_row_per_pay_line(self):
        text = exports.onpay_lines_csv(self.payroll, self.roster)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertTrue(rows)
        self.assertIn("Note to type in OnPay", rows[0])
        self.assertTrue(any(r["Note to type in OnPay"] for r in rows))

    def test_the_lines_sheet_leaves_out_anyone_the_check_stopped(self):
        text = exports.onpay_lines_csv(self.payroll, self.roster)
        self.assertNotIn("June Salter", text)


class TheReimbursementDescriptionIsKept(unittest.TestCase):
    """Sitterwise records what a reimbursement was for. The importer mapped
    that column and then dropped it, so it never reached anything."""

    def test_a_job_has_somewhere_to_put_it(self):
        import dataclasses
        from payroll.model import Job
        names = {f.name for f in dataclasses.fields(Job)}
        self.assertIn("reimbursement_description", names)
