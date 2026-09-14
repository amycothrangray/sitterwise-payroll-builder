"""Sitterwise's caregiver number, used as the OnPay Clock User.

Sitterwise gives every caregiver a number and puts it on every booking. It is
unique, it is already there, and nothing has to be invented or kept in step -
so it is the number OnPay knows them by too. A caregiver is then one number
in both systems, and somebody starting next week needs no payroll setup at
all.

It also settles identity. Matching people by name is what made two Maria
Brants dangerous; two numbers under one name is now a stop, not a guess.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import inspect
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll.engine import calculate_caregiver                          # noqa: E402
from payroll.importer import _whole_number                              # noqa: E402
from payroll.model import Job                                           # noqa: E402
from payroll.roster import RosterEntry, clock_users_from_sitterwise     # noqa: E402
from payroll.rules import Rules                                         # noqa: E402
from payroll.validate import _check_caregiver                           # noqa: E402

FIELDS = inspect.signature(Job.__init__).parameters


def _empty(annotation):
    text = str(annotation)
    if "Decimal" in text:
        return Decimal("0")
    if "bool" in text:
        return False
    if "int" in text:
        return 0
    return ""


def booking(caregiver_id, day, name="Maria Brant", hours=6):
    kw = {n: _empty(p.annotation) for n, p in FIELDS.items()
          if n != "self" and p.default is inspect.Parameter.empty}
    kw.update(booking_id=f"b{day}", caregiver_name=name, caregiver_id=caregiver_id,
              client_name="Cameron", status="completed",
              start=datetime(2026, 9, day, 9), end=datetime(2026, 9, day, 9 + hours),
              hours_worked=Decimal(hours))
    job = Job(**kw)
    job.rate = Decimal("23")
    job.hours_paid = Decimal(hours)
    job.straight_pay = Decimal(hours) * 23
    return job


def payroll_for(*jobs):
    return calculate_caregiver("Maria Brant", "maria brant", list(jobs), Rules.load())


class TheNumberComesOffTheBookings(unittest.TestCase):
    def test_a_caregiver_carries_their_sitterwise_number(self):
        self.assertEqual(payroll_for(booking("121", 1), booking("121", 2)).caregiver_id, "121")

    def test_an_export_without_the_column_is_not_an_error(self):
        """Older exports predate it. Payroll still runs."""
        run = payroll_for(booking("", 1), booking("", 2))
        self.assertEqual(run.caregiver_id, "")
        self.assertEqual(run.caregiver_id_disagrees, [])

    def test_a_spreadsheet_number_does_not_arrive_as_a_decimal(self):
        """openpyxl hands back 121 as 121.0, and 121.0 matches nothing in OnPay."""
        self.assertEqual(_whole_number(121.0), "121")
        self.assertEqual(_whole_number(121), "121")
        self.assertEqual(_whole_number(" 121 "), "121")
        self.assertEqual(_whole_number(None), "")


class TwoPeopleUnderOneName(unittest.TestCase):
    """Their hours get pooled and their overtime worked out across both."""

    def test_two_numbers_under_one_name_stops_the_payroll(self):
        run = payroll_for(booking("121", 1), booking("149", 2))
        stops = [f for f in _check_caregiver(run, {}, Rules.load())
                 if f.code == "caregiver_id_disagrees"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].level, "stop")

    def test_it_names_both_numbers(self):
        run = payroll_for(booking("121", 1), booking("149", 2))
        detail = next(f for f in _check_caregiver(run, {}, Rules.load())
                      if f.code == "caregiver_id_disagrees").detail
        self.assertIn("121", detail)
        self.assertIn("149", detail)

    def test_one_number_says_nothing(self):
        run = payroll_for(booking("121", 1), booking("121", 2))
        self.assertFalse([f for f in _check_caregiver(run, {}, Rules.load())
                          if f.code == "caregiver_id_disagrees"])


class MovingTheRosterOntoTheSitterwiseNumber(unittest.TestCase):
    def roster(self, *entries):
        return {e.caregiver_key: e for e in entries}

    def test_a_caregiver_with_no_number_gets_theirs(self):
        roster = self.roster(RosterEntry("a", "Abigail Currie"))
        report = clock_users_from_sitterwise(roster, {"a": "53"})
        self.assertEqual(roster["a"].onpay_clock_user, "53")
        self.assertEqual([r["name"] for r in report["setting"]], ["Abigail Currie"])

    def test_a_number_that_changes_is_reported_because_onpay_must_follow(self):
        roster = self.roster(RosterEntry("a", "Abigail Currie", onpay_clock_user="1001"))
        report = clock_users_from_sitterwise(roster, {"a": "53"})
        self.assertEqual(report["changing"], [{"name": "Abigail Currie",
                                               "was": "1001", "now": "53"}])

    def test_a_number_already_right_is_left_alone(self):
        roster = self.roster(RosterEntry("c", "Cara Lin", onpay_clock_user="121"))
        report = clock_users_from_sitterwise(roster, {"c": "121"})
        self.assertEqual(report["changing"], [])
        self.assertEqual(report["setting"], [])
        self.assertEqual([r["name"] for r in report["already"]], ["Cara Lin"])

    def test_somebody_with_no_sitterwise_number_keeps_what_they_have(self):
        roster = self.roster(RosterEntry("e", "Eve Marsh", onpay_clock_user="1005"))
        report = clock_users_from_sitterwise(roster, {})
        self.assertEqual(roster["e"].onpay_clock_user, "1005")
        self.assertEqual(report["no_id"], ["Eve Marsh"])

    def test_the_numbers_it_hands_back_are_the_ones_to_retype(self):
        roster = self.roster(RosterEntry("a", "Abigail Currie", onpay_clock_user="1001"),
                             RosterEntry("d", "Dana Reyes"))
        report = clock_users_from_sitterwise(roster, {"a": "53", "d": "149"})
        retype = {r["name"]: r["now"] for r in report["setting"] + report["changing"]}
        self.assertEqual(retype, {"Abigail Currie": "53", "Dana Reyes": "149"})


if __name__ == "__main__":
    unittest.main()


class ConfirmingSomebodyIsSetUpInOnPay(unittest.TestCase):
    """The app adds a caregiver to the roster the first time it sees them work
    and marks them "setup incomplete" - which means nobody has told it yet,
    not that they are missing from OnPay. Only a person can close that gap: a
    Clock User on a booking does not prove OnPay holds it."""

    def setUp(self):
        from payroll.roster import READY, SETUP_INCOMPLETE, confirm_setup
        self.READY, self.SETUP_INCOMPLETE, self.confirm = READY, SETUP_INCOMPLETE, confirm_setup

    def auto(self, key, name):
        return RosterEntry(key, name, status=self.SETUP_INCOMPLETE,
                           source="added_automatically",
                           note="Added automatically - confirm their OnPay setup")

    def test_it_confirms_the_ones_the_app_added_itself(self):
        roster = {"a": self.auto("a", "Abigail Currie")}
        changed = self.confirm(roster, ["a"])
        self.assertEqual([e.display_name for e in changed], ["Abigail Currie"])
        self.assertEqual(roster["a"].status, self.READY)

    def test_the_apps_own_placeholder_note_goes_with_it(self):
        roster = {"a": self.auto("a", "Abigail Currie")}
        self.confirm(roster, ["a"])
        self.assertEqual(roster["a"].note, "")

    def test_a_status_somebody_set_by_hand_is_untouched(self):
        """Marking somebody "not in OnPay" is a deliberate act and blocks
        payroll. Confirming a batch must never quietly undo it."""
        from payroll.roster import NOT_IN_ONPAY
        roster = {"j": RosterEntry("j", "June Salter", status=NOT_IN_ONPAY, source="manual")}
        self.assertEqual(self.confirm(roster, ["j"]), [])
        self.assertEqual(roster["j"].status, NOT_IN_ONPAY)

    def test_only_the_caregivers_asked_about_are_touched(self):
        roster = {"a": self.auto("a", "Abigail Currie"), "z": self.auto("z", "Zara Quinn")}
        self.confirm(roster, ["a"])
        self.assertEqual(roster["z"].status, self.SETUP_INCOMPLETE)

    def test_confirming_twice_changes_nothing_the_second_time(self):
        roster = {"a": self.auto("a", "Abigail Currie")}
        self.confirm(roster, ["a"])
        self.assertEqual(self.confirm(roster, ["a"]), [])
