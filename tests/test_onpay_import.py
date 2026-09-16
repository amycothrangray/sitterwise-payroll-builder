"""The file OnPay's CSV importer actually takes.

OnPay sent the specification on 4 September 2026: one row per pay item,
eight fixed columns, numeric pay types, and only one row per employee for
pay item 1 and one for pay item 2. That last rule is what shapes the file.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import dataclasses
import io
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

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
        rules = Rules.load(Path(__file__).parent / "fixtures" / "rules-2026-08.json")
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

    # -- overtime never goes near OnPay's own overtime items ----------------

    def test_pay_items_2_and_22_are_never_used(self):
        """OnPay recomputes anything on them, whatever rate we send.

        The register for 7-13 September 2026 came back with every one of
        these rows relabelled "Overtime Weighted" at a rate OnPay invented -
        Olivia Doyle was sent 4 hours at $34.50 and paid $48.56 an hour.
        Eleven people, $466.99 overpaid.
        """
        used = sorted({r["id"] for r in self.rows if r["id"] in ("2", "22")})
        self.assertEqual(used, [], "OnPay will recalculate these rows")

    def test_one_rate_keeps_every_hour_at_the_rate_worked(self):
        # Dana Reyes: 14 hours at $23, two of them overtime.
        rows = {r["id"]: r for r in self.rows_for("Dana Reyes")}
        self.assertEqual(Decimal(rows["1"]["hours"]), 14)
        self.assertEqual(Decimal(rows["1"]["rate"]), Decimal("23"))

    def test_one_rate_overtime_premium_is_money_on_its_own_item(self):
        premium = str(self.mapping["pay_ids"]["overtime_premium"])
        rows = {r["id"]: r for r in self.rows_for("Dana Reyes")}
        # Two hours of overtime at $23 - the premium alone is $11.50 an hour.
        self.assertEqual(Decimal(rows[premium]["cash_amount"]), Decimal("23.00"))
        self.assertEqual(rows[premium]["hours"], "")
        self.assertEqual(rows[premium]["rate"], "")

    def test_double_time_premium_gets_its_own_pay_type(self):
        wanted = str(self.mapping["pay_ids"]["double_overtime_premium"])
        rows = {r["id"]: r for r in self.rows_for("Priya Raman")}
        self.assertIn(wanted, rows)
        self.assertEqual(Decimal(rows[wanted]["cash_amount"]),
                         self.person("Priya Raman").dt_premium)

    def test_non_hourly_premiums_use_treat_as_cash(self):
        # OnPay rejected the Sep 7–13 file with: "Must use treat-as-cash
        # for pay type 17". Both premium items are Non-Hourly in OnPay.
        ids = {str(self.mapping["pay_ids"][key]) for key in
               ("overtime_premium", "double_overtime_premium")}
        rows = [r for r in self.rows if r["id"] in ids]
        self.assertEqual({r["id"] for r in rows}, ids)
        for row in rows:
            self.assertEqual(row["treat_as_cash"], "1", row)
            self.assertEqual(row["hours"], "")
            self.assertEqual(row["rate"], "")
            self.assertGreater(Decimal(row["cash_amount"]), 0)

    def test_hourly_rows_do_not_use_treat_as_cash(self):
        for row in self.rows:
            if row["hours"]:
                self.assertEqual(row["treat_as_cash"], "", row)

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
        premium = str(self.mapping["pay_ids"]["overtime_premium"])
        rows = {r["id"]: r for r in self.rows_for("Tess Okafor")}
        # Weighted regular rate is $25.50, so the premium alone is $12.75.
        self.assertEqual(Decimal(rows[premium]["cash_amount"]),
                         self.person("Tess Okafor").ot_premium)
        self.assertEqual(rows[premium]["hours"], "", "hours would count twice")

    def test_a_two_rate_week_still_comes_to_the_right_money(self):
        rows = self.rows_for("Tess Okafor")
        total = sum((money(Decimal(r["hours"]) * Decimal(r["rate"])) if r["hours"]
                     else Decimal(r["cash_amount"])
                     for r in rows), Decimal("0"))
        self.assertEqual(total, self.person("Tess Okafor").total_paid)

    def test_the_hours_in_the_file_are_the_hours_being_paid_for(self):
        """A premium carried as hours would count the same hour twice.

        Angela Hanson worked 26.75 hours in the week of 7 September 2026 and
        OnPay's wage statement said 37.50, because the premium rows put her
        overtime hours in a second time.
        """
        for caregiver in self.payroll.caregivers:
            if caregiver.name in self.skipped or not caregiver.jobs:
                continue
            filed = sum((Decimal(r["hours"]) for r in self.rows_for(caregiver.name)
                         if r["hours"]), Decimal("0"))
            self.assertEqual(
                filed, caregiver.hours_worked + caregiver.guarantee_hours,
                caregiver.name)

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
        # Check the rows that actually carry the premiums; items 2 and 22
        # are deliberately absent, so checking them would test nothing.
        for name in ("Dana Reyes", "Priya Raman", "Tess Okafor"):
            person = self.person(name)
            rows = {r["id"]: r for r in self.rows_for(name)}
            for key, hours in (("overtime_premium", person.ot_hours),
                               ("double_overtime_premium", person.dt_hours)):
                if hours:
                    row = rows[str(self.mapping["pay_ids"][key])]
                    self.assertEqual(Decimal(row["ob3_qualified_ot"]), hours)

    def test_an_empty_roster_number_falls_back_to_the_sitterwise_one(self):
        """Sitterwise's caregiver number is the Clock User, so a blank on the
        roster is not a gap - there is nothing to fill in by hand."""
        roster = dict(self.roster)
        victim = self.person("Tess Okafor")
        roster[victim.key] = RosterEntry(victim.key, victim.name, READY,
                                         onpay_clock_user="")
        text, skipped = exports.onpay_import_csv(self.payroll, roster)
        self.assertNotIn(victim.name, skipped)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertIn(victim.caregiver_id,
                      [r["emp_num"] for r in rows])

    def test_a_number_on_the_roster_beats_the_sitterwise_one(self):
        """Where OnPay genuinely holds somebody under something else, the
        roster is where that goes, and payroll must not quietly disagree."""
        roster = dict(self.roster)
        victim = self.person("Tess Okafor")
        roster[victim.key] = RosterEntry(victim.key, victim.name, READY,
                                         onpay_clock_user="7777")
        text, _ = exports.onpay_import_csv(self.payroll, roster)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertIn("7777", [r["emp_num"] for r in rows])
        self.assertNotIn(victim.caregiver_id, [r["emp_num"] for r in rows])

    def test_nobody_is_dropped_silently_when_there_is_no_number_anywhere(self):
        """An export from before the Caregiver ID column, and a blank roster.
        Then there really is nothing, and they have to be named."""
        roster = dict(self.roster)
        victim = self.person("Tess Okafor")
        roster[victim.key] = RosterEntry(victim.key, victim.name, READY,
                                         onpay_clock_user="")
        nameless = dataclasses.replace(victim, caregiver_id="")
        payroll = dataclasses.replace(
            self.payroll,
            caregivers=[nameless if c.key == victim.key else c
                        for c in self.payroll.caregivers])
        _, skipped = exports.onpay_import_csv(payroll, roster)
        self.assertIn(victim.name, skipped)

    def test_export_summary_counts_only_the_rows_in_the_upload(self):
        item = exports.all_exports(self.payroll, self.roster)[0]
        # This fixture contains a caregiver with an unresolved rate. A partial
        # file must not look like a complete, ready-to-import payroll.
        self.assertTrue(item["download_blocked"])
        self.assertEqual(item["summary"]["people"], len({r["emp_num"] for r in self.rows}))
        self.assertEqual(item["summary"]["rows"], len(self.rows))
        self.assertEqual(Decimal(item["summary"]["hours"]),
                         sum((Decimal(r["hours"] or 0) for r in self.rows), Decimal(0)))

    def test_already_paid_run_cannot_download_an_empty_upload(self):
        duplicate = build_run(FIXTURE, Rules.load(Path(__file__).parent / "fixtures" / "rules-2026-08.json"), WEEK_START, WEEK_END,
                              roster=self.roster,
                              previously_paid={j.booking_id: "Original payroll" for c in self.payroll.caregivers
                                               for j in c.jobs})
        item = exports.all_exports(duplicate, self.roster)[0]
        self.assertEqual(item["summary"]["rows"], 0)
        self.assertTrue(item["download_blocked"])
        self.assertIn("History", item["problems"][0]["problem"])

    def test_shared_clock_users_block_the_upload(self):
        roster = dict(self.roster)
        a, b = self.person("Dana Reyes"), self.person("Tess Okafor")
        roster[b.key] = dataclasses.replace(roster[b.key],
                                            onpay_clock_user=roster[a.key].onpay_clock_user)
        item = exports.all_exports(self.payroll, roster)[0]
        self.assertTrue(item["download_blocked"])
        self.assertTrue(any("shares Clock User" in p["problem"] for p in item["problems"]))

    def test_invalid_mapping_blocks_download_but_keeps_reports_available(self):
        mapping = {**self.mapping, "pay_ids": {
            **self.mapping["pay_ids"], "overtime_premium": 2}}
        with patch.object(exports, "load_onpay_mapping", return_value=mapping):
            listing = exports.all_exports(self.payroll, self.roster)
        self.assertTrue(listing[0]["download_blocked"])
        self.assertTrue(all(not item.get("download_blocked") for item in listing[1:]))

    def test_download_endpoint_enforces_the_block(self):
        from payroll.server import ApiError, Handler
        handler = object.__new__(Handler)
        handler.store = Mock()
        with patch("payroll.server.load_run", return_value=(None, self.payroll, self.roster)), \
             patch.object(exports, "all_exports", return_value=[{
                 "key": "onpay_import", "download_blocked": True,
                 "problems": [{"problem": "This file has no pay rows.", "blocking": True}],
             }]):
            with self.assertRaisesRegex(ApiError, "no pay rows"):
                handler._export("test", "onpay_import")
        handler.store.log.assert_not_called()


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

    def test_shipped_double_time_item_leaves_prior_pay_adjustment_alone(self):
        shipped = exports.load_onpay_mapping(exports.MAPPING_PATH)
        self.assertEqual(shipped["pay_ids"]["double_overtime_premium"], 121)
        self.assertEqual(shipped["pay_item_names"]["121"], "Double Time Premium")

    def test_premiums_cannot_use_onpays_recalculated_overtime_items(self):
        for key in ("overtime_premium", "double_overtime_premium"):
            for pay_id in (2, 22):
                broken = {**self.mapping,
                          "pay_ids": {**self.mapping["pay_ids"], key: pay_id}}
                self.assertTrue(exports.onpay_mapping_problems(broken), (key, pay_id))

    def test_premiums_cannot_share_an_item_with_other_pay(self):
        for other in ("regular", "bonus", "tips", "reimbursement", "overtime_premium"):
            broken = {**self.mapping, "pay_ids": {
                **self.mapping["pay_ids"],
                "double_overtime_premium": self.mapping["pay_ids"][other]}}
            self.assertTrue(exports.onpay_mapping_problems(broken), other)

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
        rules = Rules.load(Path(__file__).parent / "fixtures" / "rules-2026-08.json")
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

    def test_the_premium_row_says_what_it_is_made_of(self):
        """The row is just money, so the hours and rate live in the note."""
        premium = str(self.mapping["pay_ids"]["overtime_premium"])
        note = next(l["note"] for l in self.lines("Dana Reyes") if l["id"] == premium)
        self.assertRegex(note, r"2\.00 hrs x \$11\.5")

    def test_the_overtime_line_says_which_day_it_fell_on(self):
        note = next(l["note"] for l in self.lines("Dana Reyes")
                    if l["id"] == str(self.mapping["pay_ids"]["overtime_premium"]))
        self.assertRegex(note, r"[A-Z][a-z]{2} \d")

    def test_a_mileage_note_states_the_reimbursement_amount(self):
        for caregiver in self.payroll.caregivers:
            if caregiver.mileage_amount and caregiver.name not in ("June Salter",):
                lines = [l for l in self.lines(caregiver.name) if l["id"] == "107"]
                if not lines:
                    continue
                self.assertIn("mileage", lines[0]["note"])
                self.assertIn(f"${caregiver.mileage_amount:.2f}", lines[0]["note"])
                self.assertNotIn("mi paid", lines[0]["note"])
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
