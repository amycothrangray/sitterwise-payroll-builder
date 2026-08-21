"""Working out what each caregiver is owed.

The whole point of this module is that it can be checked by hand. Every
number it produces carries the arithmetic that made it, so a caregiver card
can show the sum rather than just the answer.

Order of operations, per caregiver:

  1. Split their jobs into workweeks.
  2. Within each workweek, work out the regular rate - the weighted average
     of everything they actually worked. For someone on a single rate this
     is just their rate.
  3. Within each workday, split hours into straight, overtime and double time.
  4. Pay the premium on top of straight time, at the regular rate.

Guarantee pay from the 4-hour minimum sits outside all of that: it is paid,
but it is not hours worked, so it neither triggers overtime nor moves the
regular rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .model import Job
from .money import hours as to_hours, money, rate as to_rate
from .rules import Rules

ZERO = Decimal("0")


@dataclass
class Adjustment:
    """A manual change Amy made. Never silently applied - always shown."""
    id: str
    caregiver_key: str
    kind: str                      # hours | rate | tip | mileage | reimbursement | adjustment
    booking_id: str = ""           # blank for a caregiver-level adjustment
    original_value: str = ""
    new_value: str = ""
    reason: str = ""
    created_at: str = ""
    taxable: bool = True

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class DayResult:
    day: date
    hours_worked: Decimal
    straight_hours: Decimal
    ot_hours: Decimal
    dt_hours: Decimal
    is_seventh_consecutive_day: bool
    booking_ids: list[str]
    explanation: str

    def to_dict(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "hours_worked": str(self.hours_worked),
            "straight_hours": str(self.straight_hours),
            "ot_hours": str(self.ot_hours),
            "dt_hours": str(self.dt_hours),
            "is_seventh_consecutive_day": self.is_seventh_consecutive_day,
            "booking_ids": self.booking_ids,
            "explanation": self.explanation,
        }


@dataclass
class WeekResult:
    week_start: date
    week_end: date
    hours_worked: Decimal
    straight_earnings: Decimal
    regular_rate: Decimal
    regular_rate_explanation: str
    days: list[DayResult]
    ot_hours: Decimal
    dt_hours: Decimal
    weekly_ot_hours: Decimal
    ot_premium: Decimal
    dt_premium: Decimal
    crossed_disabled_weekly_threshold: bool = False
    rates_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "week_start": self.week_start.isoformat(),
            "week_end": self.week_end.isoformat(),
            "hours_worked": str(self.hours_worked),
            "straight_earnings": str(self.straight_earnings),
            "regular_rate": str(self.regular_rate),
            "regular_rate_explanation": self.regular_rate_explanation,
            "days": [d.to_dict() for d in self.days],
            "ot_hours": str(self.ot_hours),
            "dt_hours": str(self.dt_hours),
            "weekly_ot_hours": str(self.weekly_ot_hours),
            "ot_premium": str(self.ot_premium),
            "dt_premium": str(self.dt_premium),
            "crossed_disabled_weekly_threshold": self.crossed_disabled_weekly_threshold,
            "rates_used": self.rates_used,
        }


@dataclass
class TierTotal:
    key: str
    label: str
    rate: Decimal
    hours: Decimal
    pay: Decimal

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "rate": str(self.rate),
                "hours": str(self.hours), "pay": str(self.pay)}


@dataclass
class CaregiverPayroll:
    key: str
    name: str
    jobs: list[Job]
    weeks: list[WeekResult]
    tiers: list[TierTotal]
    guarantee_hours: Decimal
    guarantee_pay: Decimal
    ot_hours: Decimal
    ot_premium: Decimal
    dt_hours: Decimal
    dt_premium: Decimal
    tips: Decimal
    bonus: Decimal
    mileage_miles: Decimal
    mileage_amount: Decimal
    other_reimbursement: Decimal
    adjustments: list[Adjustment]
    adjustment_taxable_total: Decimal
    adjustment_nontaxable_total: Decimal
    uses_multiple_rates: bool

    @property
    def hours_worked(self) -> Decimal:
        return to_hours(sum((t.hours for t in self.tiers), ZERO))

    @property
    def straight_pay(self) -> Decimal:
        return money(sum((t.pay for t in self.tiers), ZERO))

    @property
    def premium_pay(self) -> Decimal:
        return money(self.ot_premium + self.dt_premium)

    @property
    def taxable_earnings(self) -> Decimal:
        """Wages OnPay withholds against. Reimbursements are not in here."""
        return money(
            self.straight_pay + self.guarantee_pay + self.premium_pay
            + self.tips + self.bonus + self.adjustment_taxable_total
        )

    @property
    def reimbursements(self) -> Decimal:
        return money(self.mileage_amount + self.other_reimbursement
                     + self.adjustment_nontaxable_total)

    @property
    def total_paid(self) -> Decimal:
        return money(self.taxable_earnings + self.reimbursements)

    def tier_hours(self, key: str) -> Decimal:
        return next((t.hours for t in self.tiers if t.key == key), ZERO)

    def tier_pay(self, key: str) -> Decimal:
        return next((t.pay for t in self.tiers if t.key == key), ZERO)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "job_count": len(self.jobs),
            "booking_ids": [j.booking_id for j in self.jobs],
            "jobs": [j.to_dict() for j in self.jobs],
            "weeks": [w.to_dict() for w in self.weeks],
            "tiers": [t.to_dict() for t in self.tiers],
            "hours_worked": str(self.hours_worked),
            "straight_pay": str(self.straight_pay),
            "guarantee_hours": str(self.guarantee_hours),
            "guarantee_pay": str(self.guarantee_pay),
            "ot_hours": str(self.ot_hours),
            "ot_premium": str(self.ot_premium),
            "dt_hours": str(self.dt_hours),
            "dt_premium": str(self.dt_premium),
            "premium_pay": str(self.premium_pay),
            "tips": str(self.tips),
            "bonus": str(self.bonus),
            "mileage_miles": str(self.mileage_miles),
            "mileage_amount": str(self.mileage_amount),
            "other_reimbursement": str(self.other_reimbursement),
            "adjustments": [a.to_dict() for a in self.adjustments],
            "adjustment_taxable_total": str(self.adjustment_taxable_total),
            "adjustment_nontaxable_total": str(self.adjustment_nontaxable_total),
            "taxable_earnings": str(self.taxable_earnings),
            "reimbursements": str(self.reimbursements),
            "total_paid": str(self.total_paid),
            "uses_multiple_rates": self.uses_multiple_rates,
        }


# ---------------------------------------------------------------------------
# manual adjustments
# ---------------------------------------------------------------------------

def apply_adjustments(jobs: list[Job], adjustments: list[Adjustment]) -> list[Job]:
    """Return copies of the jobs with Amy's corrections applied.

    The imported jobs are left untouched; a corrected job carries a note
    saying what was changed and why, so the card can show 'Manual adjustment'
    next to it.
    """
    import copy as _copy

    by_booking: dict[str, list[Adjustment]] = {}
    for adj in adjustments:
        if adj.booking_id:
            by_booking.setdefault(adj.booking_id, []).append(adj)

    out = []
    for job in jobs:
        edits = by_booking.get(job.booking_id)
        if not edits:
            out.append(job)
            continue
        fixed = _copy.deepcopy(job)
        for adj in edits:
            _apply_one(fixed, adj)
        out.append(fixed)
    return out


def _apply_one(job: Job, adj: Adjustment) -> None:
    value = Decimal(str(adj.new_value or "0"))
    if adj.kind == "hours":
        job.hours_worked = to_hours(value)
    elif adj.kind == "rate":
        job.rate = to_rate(value)
        job.rate_basis = "manual_adjustment"
    elif adj.kind == "tip":
        job.tip = money(value)
        job.tip_was_blank = False
    elif adj.kind == "mileage":
        job.mileage_amount = money(value)
    elif adj.kind == "reimbursement":
        job.other_reimbursement = money(value)
    else:
        return
    job.import_notes.append(
        f"Manual adjustment - {adj.kind} changed from {adj.original_value} to "
        f"{adj.new_value}. Reason: {adj.reason or 'none given'}"
    )


def recompute_derived_pay(job: Job, rules: Rules) -> None:
    """Redo the minimum-hours and straight-pay maths after an adjustment."""
    job.hours_paid = job.hours_worked
    job.minimum_applied = False
    if rules.minimum_enabled and ZERO < job.hours_worked < rules.minimum_hours:
        job.hours_paid = rules.minimum_hours
        job.minimum_applied = True
    job.guarantee_hours = to_hours(job.hours_paid - job.hours_worked)
    job.straight_pay = money(job.hours_worked * job.rate)
    job.guarantee_pay = money(job.guarantee_hours * job.rate)


# ---------------------------------------------------------------------------
# workweeks
# ---------------------------------------------------------------------------

def week_start_for(day: date, start_index: int) -> date:
    """The first day of the workweek containing `day`.

    start_index follows Python's Monday=0 convention.
    """
    delta = (day.weekday() - start_index) % 7
    return day - timedelta(days=delta)


# ---------------------------------------------------------------------------
# the calculation
# ---------------------------------------------------------------------------

def calculate_caregiver(name: str, key: str, jobs: list[Job], rules: Rules,
                        adjustments: list[Adjustment] | None = None) -> CaregiverPayroll:
    adjustments = adjustments or []
    working = apply_adjustments(jobs, adjustments)
    for job in working:
        if any(a.booking_id == job.booking_id for a in adjustments):
            recompute_derived_pay(job, rules)

    weeks = _calculate_weeks(working, rules)

    tier_map: dict[str, TierTotal] = {}
    for job in working:
        if job.hours_worked <= 0:
            continue
        total = tier_map.get(job.tier_key)
        if total is None:
            total = TierTotal(job.tier_key, job.tier_label, job.rate, ZERO, ZERO)
            tier_map[job.tier_key] = total
        total.hours = to_hours(total.hours + job.hours_worked)
        total.pay = money(total.pay + job.straight_pay)
    tiers = sorted(tier_map.values(), key=lambda t: (-t.rate, t.key))

    caregiver_adjustments = [a for a in adjustments if not a.booking_id]
    taxable_adj = money(sum(
        (Decimal(str(a.new_value or 0)) for a in caregiver_adjustments if a.taxable), ZERO))
    nontaxable_adj = money(sum(
        (Decimal(str(a.new_value or 0)) for a in caregiver_adjustments if not a.taxable), ZERO))

    return CaregiverPayroll(
        key=key,
        name=name,
        jobs=working,
        weeks=weeks,
        tiers=tiers,
        guarantee_hours=to_hours(sum((j.guarantee_hours for j in working), ZERO)),
        guarantee_pay=money(sum((j.guarantee_pay for j in working), ZERO)),
        ot_hours=to_hours(sum((w.ot_hours + w.weekly_ot_hours for w in weeks), ZERO)),
        ot_premium=money(sum((w.ot_premium for w in weeks), ZERO)),
        dt_hours=to_hours(sum((w.dt_hours for w in weeks), ZERO)),
        dt_premium=money(sum((w.dt_premium for w in weeks), ZERO)),
        tips=money(sum((j.tip for j in working), ZERO)),
        bonus=money(sum((j.bonus + j.lifesaver_bonus for j in working), ZERO)),
        mileage_miles=sum((j.mileage_miles or ZERO for j in working), ZERO),
        mileage_amount=money(sum((j.mileage_amount for j in working), ZERO)),
        other_reimbursement=money(sum((j.other_reimbursement for j in working), ZERO)),
        adjustments=adjustments,
        adjustment_taxable_total=taxable_adj,
        adjustment_nontaxable_total=nontaxable_adj,
        uses_multiple_rates=len([t for t in tiers if t.hours > 0]) > 1,
    )


def _calculate_weeks(jobs: list[Job], rules: Rules) -> list[WeekResult]:
    # Not `or 6`: Monday is index 0, which is falsy, so that silently
    # rebuilt every week as a Sunday week no matter what the setting said.
    start_index = rules.workweek_start_index
    if start_index is None:
        start_index = 0
    by_week: dict[date, list[Job]] = {}
    for job in jobs:
        if not job.workday or job.hours_worked <= 0:
            continue
        by_week.setdefault(week_start_for(job.workday, start_index), []).append(job)

    results = []
    for week_start in sorted(by_week):
        results.append(_calculate_week(week_start, by_week[week_start], rules))
    return results


def _calculate_week(week_start: date, jobs: list[Job], rules: Rules) -> WeekResult:
    hours_worked = to_hours(sum((j.hours_worked for j in jobs), ZERO))
    straight_earnings = money(sum((j.straight_pay for j in jobs), ZERO))

    rate_bits = sorted({f"{j.hours_worked} hrs at ${j.rate:.2f}"
                        for j in jobs if j.hours_worked > 0})
    if hours_worked > 0:
        regular_rate = to_rate(straight_earnings / hours_worked)
    else:
        regular_rate = ZERO

    distinct_rates = {j.rate for j in jobs if j.hours_worked > 0}
    if len(distinct_rates) <= 1:
        explanation = f"One rate this week, so the regular rate is ${regular_rate:.2f} an hour."
    else:
        explanation = (
            f"Two rates this week, so overtime uses the weighted average: "
            f"${straight_earnings:,.2f} of straight-time pay divided by {hours_worked} hours "
            f"worked = ${regular_rate:.4f} an hour."
        )

    by_day: dict[date, list[Job]] = {}
    for job in jobs:
        by_day.setdefault(job.workday, []).append(job)

    seventh_days = _seventh_consecutive_days(sorted(by_day)) if rules.seventh_day_enabled else set()

    days, ot_hours, dt_hours = [], ZERO, ZERO
    for day in sorted(by_day):
        day_jobs = by_day[day]
        worked = to_hours(sum((j.hours_worked for j in day_jobs), ZERO))
        result = _split_day(day, worked, day in seventh_days, day_jobs, rules)
        days.append(result)
        ot_hours = to_hours(ot_hours + result.ot_hours)
        dt_hours = to_hours(dt_hours + result.dt_hours)

    weekly_ot_hours = ZERO
    crossed_disabled = False
    if hours_worked > rules.weekly_ot_threshold:
        overage = to_hours(hours_worked - rules.weekly_ot_threshold - ot_hours - dt_hours)
        if overage > 0:
            if rules.weekly_ot_enabled:
                weekly_ot_hours = overage
            else:
                crossed_disabled = True

    ot_factor = rules.daily_ot_multiplier - Decimal("1")
    dt_factor = rules.daily_dt_multiplier - Decimal("1")
    weekly_factor = rules.weekly_ot_multiplier - Decimal("1")

    ot_premium = money((ot_hours * ot_factor + weekly_ot_hours * weekly_factor) * regular_rate)
    dt_premium = money(dt_hours * dt_factor * regular_rate)

    return WeekResult(
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        hours_worked=hours_worked,
        straight_earnings=straight_earnings,
        regular_rate=regular_rate,
        regular_rate_explanation=explanation,
        days=days,
        ot_hours=ot_hours,
        dt_hours=dt_hours,
        weekly_ot_hours=weekly_ot_hours,
        ot_premium=ot_premium,
        dt_premium=dt_premium,
        crossed_disabled_weekly_threshold=crossed_disabled,
        rates_used=rate_bits,
    )


def _split_day(day: date, worked: Decimal, is_seventh: bool,
               day_jobs: list[Job], rules: Rules) -> DayResult:
    booking_ids = [j.booking_id for j in day_jobs]

    if is_seventh:
        straight_cap = rules.seventh_day_straight_hours
        ot = min(worked, straight_cap)
        dt = to_hours(max(ZERO, worked - straight_cap))
        explanation = (
            f"Seventh day in a row worked. The first {ot} hours are at time and a half"
            + (f" and the remaining {dt} at double time." if dt > 0 else ".")
        )
        return DayResult(day, worked, ZERO, to_hours(ot), dt, True, booking_ids, explanation)

    dt = ZERO
    if rules.daily_dt_enabled and worked > rules.daily_dt_threshold:
        dt = to_hours(worked - rules.daily_dt_threshold)

    ot = ZERO
    if rules.daily_ot_enabled:
        capped = min(worked, rules.daily_dt_threshold) if rules.daily_dt_enabled else worked
        if capped > rules.daily_ot_threshold:
            ot = to_hours(capped - rules.daily_ot_threshold)

    straight = to_hours(worked - ot - dt)

    if ot == ZERO and dt == ZERO:
        explanation = f"{worked} hours, all at the normal rate."
    elif dt == ZERO:
        explanation = (
            f"{worked} hours. The first {rules.daily_ot_threshold} are normal, "
            f"the next {ot} are overtime."
        )
    else:
        explanation = (
            f"{worked} hours. The first {rules.daily_ot_threshold} are normal, "
            f"{ot} are overtime, and {dt} past {rules.daily_dt_threshold} hours "
            "are double time."
        )
    return DayResult(day, worked, straight, ot, dt, False, booking_ids, explanation)


def _seventh_consecutive_days(days: list[date]) -> set[date]:
    """Days that are the 7th or later in an unbroken run of worked days."""
    flagged, run = set(), 0
    previous = None
    for day in days:
        run = run + 1 if previous and (day - previous).days == 1 else 1
        if run >= 7:
            flagged.add(day)
        previous = day
    return flagged
