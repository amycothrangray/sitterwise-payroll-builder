"""Putting a whole payroll run together, and proving it adds up.

A run is: one export file, one pay period, one set of rules, plus whatever
manual adjustments and OnPay-entry ticks Amy has made. Everything else is
worked out from those.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from . import extras
from .engine import Adjustment, CaregiverPayroll, calculate_caregiver
from .importer import import_export
from .model import ImportResult, Job
from .money import hours as to_hours, money
from .roster import RosterEntry
from .rules import Rules
from .validate import Finding, run_checks, summarise

ZERO = Decimal("0")


# --- working out the pay period --------------------------------------------

def suggest_period(result: ImportResult, rules: Rules | None = None,
                   today: date | None = None) -> tuple[date, date, str]:
    """Work out which pay period this export is probably for.

    Sitterwise pays weekly, Monday to Sunday, so the app suggests the most
    recent complete week that actually has jobs in it. It always shows the
    suggestion back for confirmation - an export is a whole calendar month and
    cannot say which week you mean.
    """
    if not result.min_date or not result.max_date:
        stamp = today or date.today()
        return stamp, stamp, "This file has no dates the app could read."

    lo, hi = result.min_date, result.max_date
    start_index = (rules.workweek_start_index if rules else None)
    if start_index is None:
        start_index = 0
    weekly = (rules.pay_period_type if rules else "weekly") == "weekly"

    if not weekly:
        if (hi - lo).days > 20:
            return (date(hi.year, hi.month, 1), date(hi.year, hi.month, 15),
                    f"This export covers {lo:%b %-d} to {hi:%b %-d} - a whole month, not a pay "
                    f"period. The app has suggested the first half of {hi:%B}.")
        return lo, hi, f"Taken from the dates in the file: {lo:%b %-d} to {hi:%b %-d}."

    weeks = weeks_in(result, start_index)
    if not weeks:
        return lo, hi, f"This export covers {lo:%b %-d} to {hi:%b %-d}."

    stamp = today or date.today()
    complete = [w for w in weeks if w[1] < stamp]
    chosen = complete[-1] if complete else weeks[-1]
    day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday"][start_index]
    note = (f"This export covers {lo:%b %-d} to {hi:%b %-d}. Sitterwise pays weekly, "
            f"{day_name} to {['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][start_index]}, "
            f"so the app has suggested the most recent complete week with jobs in it. "
            "Pick a different one if that is not the run you are doing.")
    return chosen[0], chosen[1], note


def weeks_in(result: ImportResult, start_index: int) -> list[tuple[date, date]]:
    """Every pay week the export has jobs in, oldest first."""
    from .engine import week_start_for
    starts = sorted({week_start_for(j.workday, start_index)
                     for j in result.jobs if j.workday and j.is_payable})
    return [(s, s + timedelta(days=6)) for s in starts]


def half_month_periods(year: int, month: int) -> list[tuple[date, date, str]]:
    first = date(year, month, 1)
    mid = date(year, month, 15)
    next_month = date(year + (month == 12), (month % 12) + 1, 1)
    last = next_month - timedelta(days=1)
    return [
        (first, mid, f"{first:%b %-d}-{mid:%-d}, {first:%Y}"),
        (mid + timedelta(days=1), last, f"{mid + timedelta(days=1):%b %-d}-{last:%-d}, {last:%Y}"),
    ]


def period_label(start: date, end: date) -> str:
    if start.year == end.year and start.month == end.month:
        return f"{start:%b %-d}-{end:%-d}, {start:%Y}"
    if start.year == end.year:
        return f"{start:%b %-d} - {end:%b %-d}, {start:%Y}"
    return f"{start:%b %-d, %Y} - {end:%b %-d, %Y}"


# --- the run ---------------------------------------------------------------

@dataclass
class Reconciliation:
    """Proof that nothing fell out between Sitterwise and payroll."""
    source_rows: int
    jobs_in_file: int
    jobs_in_period: int
    jobs_paid: int
    jobs_excluded: int
    exclusions: dict
    jobs_accounted_for: int
    balances: bool
    exported_pay_total: Decimal
    app_straight_total: Decimal
    pay_difference: Decimal
    pay_differences: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_rows": self.source_rows,
            "jobs_in_file": self.jobs_in_file,
            "jobs_in_period": self.jobs_in_period,
            "jobs_paid": self.jobs_paid,
            "jobs_excluded": self.jobs_excluded,
            "exclusions": self.exclusions,
            "jobs_accounted_for": self.jobs_accounted_for,
            "balances": self.balances,
            "exported_pay_total": str(self.exported_pay_total),
            "app_straight_total": str(self.app_straight_total),
            "pay_difference": str(self.pay_difference),
            "pay_differences": self.pay_differences,
        }


@dataclass
class PayrollRun:
    period_start: date
    period_end: date
    rules: Rules
    import_result: ImportResult
    caregivers: list[CaregiverPayroll]
    findings: list[Finding]
    reconciliation: Reconciliation
    period_jobs: list[Job]
    excluded_jobs: list[Job]

    @property
    def label(self) -> str:
        return period_label(self.period_start, self.period_end)

    @property
    def summary(self) -> dict:
        return summarise(self.caregivers, self.findings)

    def totals(self) -> dict:
        def add(attr):
            return sum((getattr(c, attr) for c in self.caregivers), ZERO)

        tier_totals = {}
        for caregiver in self.caregivers:
            for tier in caregiver.tiers:
                bucket = tier_totals.setdefault(
                    tier.key, {"label": tier.label, "rate": tier.rate,
                               "hours": ZERO, "pay": ZERO})
                bucket["hours"] = to_hours(bucket["hours"] + tier.hours)
                bucket["pay"] = money(bucket["pay"] + tier.pay)

        return {
            "caregivers": len(self.caregivers),
            "jobs": len(self.period_jobs),
            "tiers": [
                {"key": key, "label": v["label"], "rate": str(v["rate"]),
                 "hours": str(v["hours"]), "pay": str(v["pay"])}
                for key, v in sorted(tier_totals.items(), key=lambda kv: -kv[1]["rate"])
            ],
            "hours_worked": str(to_hours(add("hours_worked"))),
            "straight_pay": str(money(add("straight_pay"))),
            "guarantee_hours": str(to_hours(add("guarantee_hours"))),
            "guarantee_pay": str(money(add("guarantee_pay"))),
            "ot_hours": str(to_hours(add("ot_hours"))),
            "ot_premium": str(money(add("ot_premium"))),
            "dt_hours": str(to_hours(add("dt_hours"))),
            "dt_premium": str(money(add("dt_premium"))),
            "premium_pay": str(money(add("premium_pay"))),
            "tips": str(money(add("tips"))),
            "bonus": str(money(add("bonus"))),
            "mileage_miles": str(add("mileage_miles")),
            "mileage_amount": str(money(add("mileage_amount"))),
            "other_reimbursement": str(money(add("other_reimbursement"))),
            "taxable_earnings": str(money(add("taxable_earnings"))),
            "reimbursements": str(money(add("reimbursements"))),
            "total_paid": str(money(add("total_paid"))),
        }


def build_run(path, rules: Rules, period_start: date, period_end: date,
              roster: dict[str, RosterEntry] | None = None,
              adjustments: list[Adjustment] | None = None,
              previously_paid: dict[str, str] | None = None,
              import_result: ImportResult | None = None,
              recurring: list[dict] | None = None) -> PayrollRun:
    result = import_result or import_export(path, rules)
    roster = roster or {}
    adjustments = adjustments or []
    recurring = recurring or []

    in_period = [j for j in result.jobs
                 if j.workday and period_start <= j.workday <= period_end]
    payable = [j for j in in_period if j.is_payable]
    excluded = [j for j in in_period if not j.is_payable]

    by_caregiver: dict[str, list[Job]] = defaultdict(list)
    for job in payable:
        by_caregiver[job.caregiver_key].append(job)

    adjustments_by_caregiver: dict[str, list[Adjustment]] = defaultdict(list)
    for adj in adjustments:
        adjustments_by_caregiver[adj.caregiver_key].append(adj)

    # Recurring pay for people the export cannot know about. Somebody who
    # also worked bookings this week gets it folded into their own payroll so
    # they end up with one payment, not two; somebody with no bookings at all
    # gets a line of their own.
    due = extras.due_in_period(recurring, period_start, period_end)
    standalone = []
    for entry in due:
        line = extras.recurring_payroll(entry, period_start, period_end)
        if entry["caregiver_key"] in by_caregiver:
            adjustments_by_caregiver[entry["caregiver_key"]].extend(line.adjustments)
        else:
            standalone.append(line)

    caregivers = []
    for key, jobs in by_caregiver.items():
        name = next((j.display_name for j in jobs if j.display_name), "")
        caregivers.append(
            calculate_caregiver(name, key, jobs, rules, adjustments_by_caregiver.get(key, [])))
    caregivers.extend(standalone)
    caregivers.sort(key=lambda c: (c.name == "", c.name.lower()))

    findings = run_checks(
        caregivers, result.jobs, payable, roster, rules,
        previously_paid=previously_paid,
        period_start=period_start, period_end=period_end,
        period_all_jobs=in_period,
    )

    reconciliation = _reconcile(result, in_period, payable, excluded, caregivers)

    return PayrollRun(
        period_start=period_start,
        period_end=period_end,
        rules=rules,
        import_result=result,
        caregivers=caregivers,
        findings=findings,
        reconciliation=reconciliation,
        period_jobs=payable,
        excluded_jobs=excluded,
    )


def _reconcile(result: ImportResult, in_period, payable, excluded,
               caregivers: list[CaregiverPayroll]) -> Reconciliation:
    exclusions: dict[str, int] = defaultdict(int)
    for job in excluded:
        exclusions[job.exclusion_reason or "Not a paid status"] += 1

    accounted = sum(len(c.jobs) for c in caregivers)

    exported_total = money(sum((j.paid_to_caregiver for j in payable), ZERO))
    app_total = money(sum(
        (c.straight_pay + c.guarantee_pay for c in caregivers), ZERO))

    differences = []
    for job in payable:
        expected = job.expected_pay
        if abs(expected - job.paid_to_caregiver) > Decimal("0.01"):
            differences.append({
                "booking_id": job.booking_id,
                "caregiver": job.display_name,
                "date": job.workday.isoformat() if job.workday else "",
                "sitterwise_says": str(job.paid_to_caregiver),
                "app_calculates": str(expected),
                "difference": str(money(expected - job.paid_to_caregiver)),
                "why": "; ".join(job.import_notes) or "Recalculated from hours and rate.",
            })

    return Reconciliation(
        source_rows=result.row_count,
        jobs_in_file=len(result.jobs),
        jobs_in_period=len(in_period),
        jobs_paid=len(payable),
        jobs_excluded=len(excluded),
        exclusions=dict(exclusions),
        jobs_accounted_for=accounted,
        balances=accounted == len(payable) and len(in_period) == len(payable) + len(excluded),
        exported_pay_total=exported_total,
        app_straight_total=app_total,
        pay_difference=money(app_total - exported_total),
        pay_differences=differences,
    )
