"""Loading and reading the payroll rules file.

Every payroll rule Amy might want to change lives in rules.json, never in
code. A payroll run stores a copy of the rules it used, so reopening an old
run shows the rules that were in force then rather than today's.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .money import money, hours, rate as to_rate

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "rules.json"

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


class RulesError(ValueError):
    """The rules file says something the app cannot act on."""


class Rules:
    def __init__(self, data: dict):
        self.data = data
        self._validate()

    # -- construction ---------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Rules":
        path = Path(path) if path else DEFAULT_RULES_PATH
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "Rules":
        return cls(copy.deepcopy(snapshot))

    def snapshot(self) -> dict:
        return copy.deepcopy(self.data)

    # -- validation -----------------------------------------------------
    def _validate(self) -> None:
        tiers = self.data.get("pay_rates", {}).get("tiers")
        if not tiers:
            raise RulesError("rules.json has no pay rate tiers - payroll cannot be calculated")
        seen = set()
        for tier in tiers:
            for field in ("key", "label", "rate"):
                if field not in tier:
                    raise RulesError(f"pay rate tier is missing '{field}': {tier}")
            if tier["key"] in seen:
                raise RulesError(f"two pay rate tiers share the key '{tier['key']}'")
            seen.add(tier["key"])
            if Decimal(str(tier["rate"])) <= 0:
                raise RulesError(f"pay rate for '{tier['key']}' must be more than zero")
        if self.workweek_start_index is None:
            raise RulesError(
                "overtime.workweek_start_day must be a day name such as 'sunday'"
            )
        mileage = self.data.get("reimbursements", {}).get("mileage", {})
        for entry in mileage.get("rates_by_effective_date", []):
            date.fromisoformat(entry["effective"])  # raises if malformed

    @property
    def version(self) -> str:
        return str(self.data.get("version", "unversioned"))

    # -- pay rates ------------------------------------------------------
    @property
    def tiers(self) -> list[dict]:
        return self.data["pay_rates"]["tiers"]

    def tier(self, key: str) -> dict | None:
        return next((t for t in self.tiers if t["key"] == key), None)

    def tier_label(self, key: str) -> str:
        tier = self.tier(key)
        return tier["label"] if tier else "Unknown rate"

    def tier_rate(self, key: str) -> Decimal:
        tier = self.tier(key)
        return to_rate(tier["rate"]) if tier else to_rate(0)

    @property
    def rate_match_tolerance(self) -> Decimal:
        return money(self.data["pay_rates"].get("rate_match_tolerance", 0.02))

    # -- minimum booking ------------------------------------------------
    @property
    def minimum_enabled(self) -> bool:
        return bool(self.data.get("minimum_booking", {}).get("enabled", False))

    @property
    def minimum_hours(self) -> Decimal:
        return hours(self.data.get("minimum_booking", {}).get("minimum_hours", 0))

    @property
    def guarantee_counts_toward_overtime(self) -> bool:
        return bool(self.data.get("minimum_booking", {}).get("counts_toward_overtime", False))

    @property
    def guarantee_counts_toward_regular_rate(self) -> bool:
        return bool(self.data.get("minimum_booking", {}).get("counts_toward_regular_rate", False))

    # -- overtime -------------------------------------------------------
    @property
    def overtime(self) -> dict:
        return self.data.get("overtime", {})

    def _ot_block(self, name: str) -> dict:
        return self.overtime.get(name, {})

    @property
    def daily_ot_enabled(self) -> bool:
        return bool(self._ot_block("daily_overtime").get("enabled", False))

    @property
    def daily_ot_threshold(self) -> Decimal:
        return hours(self._ot_block("daily_overtime").get("threshold_hours", 8))

    @property
    def daily_ot_multiplier(self) -> Decimal:
        return to_rate(self._ot_block("daily_overtime").get("multiplier", 1.5))

    @property
    def daily_dt_enabled(self) -> bool:
        return bool(self._ot_block("daily_double_time").get("enabled", False))

    @property
    def daily_dt_threshold(self) -> Decimal:
        return hours(self._ot_block("daily_double_time").get("threshold_hours", 12))

    @property
    def daily_dt_multiplier(self) -> Decimal:
        return to_rate(self._ot_block("daily_double_time").get("multiplier", 2.0))

    @property
    def weekly_ot_enabled(self) -> bool:
        return bool(self._ot_block("weekly_overtime").get("enabled", False))

    @property
    def weekly_ot_threshold(self) -> Decimal:
        return hours(self._ot_block("weekly_overtime").get("threshold_hours", 40))

    @property
    def weekly_ot_multiplier(self) -> Decimal:
        return to_rate(self._ot_block("weekly_overtime").get("multiplier", 1.5))

    @property
    def warn_when_weekly_ot_disabled(self) -> bool:
        return bool(self._ot_block("weekly_overtime").get("warn_when_disabled", True))

    @property
    def seventh_day_enabled(self) -> bool:
        return bool(self._ot_block("seventh_consecutive_day").get("enabled", False))

    @property
    def seventh_day_straight_hours(self) -> Decimal:
        return hours(self._ot_block("seventh_consecutive_day").get("straight_hours", 8))

    @property
    def seventh_day_multiplier(self) -> Decimal:
        return to_rate(self._ot_block("seventh_consecutive_day").get("multiplier", 1.5))

    @property
    def seventh_day_beyond_multiplier(self) -> Decimal:
        return to_rate(self._ot_block("seventh_consecutive_day").get("beyond_multiplier", 2.0))

    @property
    def regular_rate_method(self) -> str:
        return self.overtime.get("regular_rate_method", "weighted_average")

    @property
    def overnight_attribution(self) -> str:
        return self.overtime.get("overnight_attribution", "shift_start_day")

    @property
    def workweek_start_index(self) -> int | None:
        return _WEEKDAYS.get(str(self.overtime.get("workweek_start_day", "sunday")).lower())

    @property
    def workweek_start_confirmed(self) -> bool:
        return bool(self.overtime.get("workweek_start_confirmed", False))

    # -- other pay categories -------------------------------------------
    @property
    def bonus_in_regular_rate(self) -> bool:
        return bool(self.data.get("bonuses", {}).get("include_in_regular_rate", False))

    @property
    def warn_bonus_with_overtime(self) -> bool:
        return bool(self.data.get("bonuses", {}).get("warn_when_bonus_and_overtime_coincide", True))

    @property
    def tips_in_regular_rate(self) -> bool:
        return bool(self.data.get("tips", {}).get("include_in_regular_rate", False))

    # -- mileage --------------------------------------------------------
    @property
    def _mileage(self) -> dict:
        return self.data.get("reimbursements", {}).get("mileage", {})

    @property
    def detect_mileage(self) -> bool:
        return bool(self._mileage.get("detect_from_reimbursement", False))

    @property
    def whole_mile_tolerance(self) -> Decimal:
        return Decimal(str(self._mileage.get("whole_mile_tolerance", 0.005)))

    @property
    def minimum_miles(self) -> Decimal:
        return Decimal(str(self._mileage.get("minimum_miles", 1)))

    @property
    def mileage_eligible_service_types(self) -> set[str]:
        """Job types mileage may be claimed on. Empty means any job."""
        return set(self._mileage.get("eligible_service_types", []))

    def mileage_allowed_on(self, service_type: str) -> bool:
        eligible = self.mileage_eligible_service_types
        return not eligible or service_type in eligible

    def mileage_rate_for(self, on: date) -> Decimal:
        """The mileage rate in force on a given date.

        The IRS changed the rate mid-2026, so this is a table, not a constant.
        """
        entries = sorted(
            self._mileage.get("rates_by_effective_date", []),
            key=lambda e: e["effective"],
        )
        chosen = None
        for entry in entries:
            if date.fromisoformat(entry["effective"]) <= on:
                chosen = entry
        return to_rate(chosen["rate"]) if chosen else to_rate(0)

    # -- statuses -------------------------------------------------------
    @property
    def payable_statuses(self) -> set[str]:
        return {s.lower() for s in self.data.get("payable_statuses", {}).get("pay", [])}

    @property
    def never_pay_statuses(self) -> set[str]:
        return {s.lower() for s in self.data.get("payable_statuses", {}).get("never_pay", [])}

    @property
    def flag_if_past_dated_statuses(self) -> set[str]:
        return {s.lower() for s in self.data.get("payable_statuses", {}).get("flag_if_past_dated", [])}

    @property
    def known_service_types(self) -> set[str]:
        return set(self.data.get("service_types", {}).get("known", []))

    # -- validation thresholds ------------------------------------------
    @property
    def validation(self) -> dict:
        return self.data.get("validation", {})

    def v(self, key: str, default):
        return self.validation.get(key, default)
