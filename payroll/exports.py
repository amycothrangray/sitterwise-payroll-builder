"""The files Amy can take out of the app.

Five reports plus an OnPay import file. All of them are plain CSV so they
open in Excel or Google Sheets without anything special.

Every report is built from the same PayrollRun, so the numbers cannot drift
between one export and another.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .engine import CaregiverPayroll
from .roster import RosterEntry
from .run import PayrollRun

ZERO = Decimal("0")

MAPPING_PATH = Path(__file__).resolve().parent.parent / "onpay_mapping.json"


def _writer():
    buffer = io.StringIO(newline="")
    return buffer, csv.writer(buffer, lineterminator="\n")


def _safe(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in label).strip("-")


# --- 1. payroll detail: every job, every number -----------------------------

def payroll_detail_csv(run: PayrollRun) -> str:
    buffer, out = _writer()
    out.writerow([
        "Caregiver", "Booking ID", "Date", "Start", "End", "Client", "Job type",
        "Where", "Hours worked", "Pay tier", "Rate", "Straight pay",
        "Minimum applied", "Guarantee hours", "Guarantee pay",
        "Tip", "Mileage miles", "Mileage $", "Other reimbursement $",
        "Bonus", "Sitterwise said", "How the rate was worked out", "Notes",
    ])
    for caregiver in run.caregivers:
        for job in sorted(caregiver.jobs, key=lambda j: (j.workday or date.min, j.booking_id)):
            out.writerow([
                caregiver.name, job.booking_id,
                job.workday.isoformat() if job.workday else "",
                f"{job.start:%H:%M}" if job.start else "",
                f"{job.end:%H:%M}" if job.end else "",
                job.client_name, job.service_type,
                job.hotel or job.location_type,
                job.hours_worked, job.tier_label, job.rate, job.straight_pay,
                "yes" if job.minimum_applied else "", job.guarantee_hours, job.guarantee_pay,
                job.tip, job.mileage_miles or "", job.mileage_amount,
                job.other_reimbursement, job.bonus + job.lifesaver_bonus,
                job.paid_to_caregiver, _basis(job.rate_basis),
                " | ".join(job.import_notes),
            ])
    return buffer.getvalue()


def _basis(basis: str) -> str:
    return {
        "stated_in_export": "Rate came from the export",
        "inferred_from_pay:worked_hours": "Worked out from the amount paid",
        "inferred_from_pay:exported_hours": "Worked out from the amount paid, using Sitterwise's hours",
        "manual_adjustment": "Set by hand",
        "unmatched": "Could not be matched to a rate",
        "none": "No pay recorded",
    }.get(basis, basis)


# --- 2. the OnPay entry grid ------------------------------------------------

def onpay_entry_csv(run: PayrollRun, roster: dict[str, RosterEntry],
                    entered: dict[str, bool] | None = None) -> str:
    entered = entered or {}
    tier_keys = [t["key"] for t in run.totals()["tiers"]]
    buffer, out = _writer()
    header = ["Caregiver", "OnPay Clock User"]
    header += [f"{run.rules.tier_label(k)} hours" for k in tier_keys]
    header += ["Minimum hours", "Overtime hours", "Overtime $", "Double time hours",
               "Double time $", "Tips", "Bonus", "Mileage", "Other reimbursement",
               "Taxable earnings", "Total being paid", "Entered in OnPay"]
    out.writerow(header)
    for caregiver in run.caregivers:
        entry = roster.get(caregiver.key)
        row = [caregiver.name, entry.onpay_clock_user if entry else ""]
        row += [caregiver.tier_hours(k) or "" for k in tier_keys]
        row += [
            caregiver.guarantee_hours or "",
            caregiver.ot_hours or "", caregiver.ot_premium or "",
            caregiver.dt_hours or "", caregiver.dt_premium or "",
            caregiver.tips or "", caregiver.bonus or "",
            caregiver.mileage_amount or "", caregiver.other_reimbursement or "",
            caregiver.taxable_earnings, caregiver.total_paid,
            "yes" if entered.get(caregiver.key) else "",
        ]
        out.writerow(row)
    return buffer.getvalue()


# --- 3. summary -------------------------------------------------------------

def payroll_summary_csv(run: PayrollRun) -> str:
    totals = run.totals()
    summary = run.summary
    recon = run.reconciliation
    buffer, out = _writer()
    out.writerow(["Sitterwise payroll summary"])
    out.writerow(["Pay period", run.label])
    out.writerow(["Rules version", run.rules.version])
    out.writerow(["Source file", run.import_result.source_filename])
    out.writerow([])
    out.writerow(["Caregivers", totals["caregivers"]])
    out.writerow(["Jobs paid", totals["jobs"]])
    out.writerow([])
    out.writerow(["Hours"])
    for tier in totals["tiers"]:
        out.writerow([f"  {tier['label']} hours", tier["hours"]])
    out.writerow(["  Minimum-guarantee hours (paid, not worked)", totals["guarantee_hours"]])
    out.writerow(["  Overtime hours", totals["ot_hours"]])
    out.writerow(["  Double time hours", totals["dt_hours"]])
    out.writerow(["  Total hours worked", totals["hours_worked"]])
    out.writerow([])
    out.writerow(["Money"])
    for tier in totals["tiers"]:
        out.writerow([f"  {tier['label']} wages", tier["pay"]])
    out.writerow(["  Minimum-guarantee pay", totals["guarantee_pay"]])
    out.writerow(["  Overtime premium", totals["ot_premium"]])
    out.writerow(["  Double time premium", totals["dt_premium"]])
    out.writerow(["  Tips", totals["tips"]])
    out.writerow(["  Bonuses", totals["bonus"]])
    out.writerow(["  Taxable earnings", totals["taxable_earnings"]])
    out.writerow([])
    out.writerow(["  Mileage", totals["mileage_amount"],
                  f"{totals['mileage_miles']} miles"])
    out.writerow(["  Other reimbursements", totals["other_reimbursement"]])
    out.writerow(["  Reimbursements (not taxable)", totals["reimbursements"]])
    out.writerow([])
    out.writerow(["Expected total employee payments", totals["total_paid"]])
    out.writerow([])
    out.writerow(["Proof nothing went missing"])
    out.writerow(["  Jobs in this period in the export", recon.jobs_in_period])
    out.writerow(["  Jobs paid", recon.jobs_paid])
    out.writerow(["  Jobs accounted for in payroll", recon.jobs_accounted_for])
    for reason, count in recon.exclusions.items():
        out.writerow([f"  Left out - {reason}", count])
    out.writerow(["  Everything balances", "yes" if recon.balances else "NO - look into this"])
    out.writerow([])
    out.writerow(["Payroll check"])
    out.writerow(["  Ready", summary["ready"]])
    out.writerow(["  Need review", summary["needs_review"]])
    out.writerow(["  Cannot be finalised", summary["blocked"]])
    return buffer.getvalue()


# --- 4. exceptions ----------------------------------------------------------

def exceptions_csv(run: PayrollRun) -> str:
    levels = {"stop": "Cannot be finalised", "review": "Needs review", "note": "Worth knowing"}
    buffer, out = _writer()
    out.writerow(["How serious", "Caregiver", "What it is", "Detail",
                  "What to do", "Bookings"])
    for finding in run.findings:
        out.writerow([
            levels.get(finding.level, finding.level), finding.caregiver_name,
            finding.title, finding.detail, finding.what_to_do,
            " ".join(finding.booking_ids[:40]),
        ])
    return buffer.getvalue()


# --- 5. caregiver detail ----------------------------------------------------

def caregiver_detail_csv(run: PayrollRun, roster: dict[str, RosterEntry]) -> str:
    buffer, out = _writer()
    out.writerow(["Sitterwise caregiver detail", run.label])
    out.writerow([])
    for caregiver in run.caregivers:
        entry = roster.get(caregiver.key)
        out.writerow([caregiver.name.upper(),
                      entry.status_label if entry else "Not on the roster"])
        for tier in caregiver.tiers:
            out.writerow(["", tier.label,
                          f"{tier.hours} hrs x ${tier.rate:.2f}", tier.pay])
        if caregiver.guarantee_hours:
            out.writerow(["", "4-hour minimum top-up",
                          f"{caregiver.guarantee_hours} hrs", caregiver.guarantee_pay])
        for week in caregiver.weeks:
            if week.ot_hours or week.dt_hours or week.weekly_ot_hours:
                out.writerow(["", f"Week of {week.week_start:%b %-d}",
                              week.regular_rate_explanation])
                for day in week.days:
                    if day.ot_hours or day.dt_hours:
                        out.writerow(["", "", f"{day.day:%b %-d}", day.explanation,
                                      " ".join(day.booking_ids)])
        if caregiver.ot_hours:
            out.writerow(["", "Overtime", f"{caregiver.ot_hours} hrs", caregiver.ot_premium])
        if caregiver.dt_hours:
            out.writerow(["", "Double time", f"{caregiver.dt_hours} hrs", caregiver.dt_premium])
        if caregiver.tips:
            out.writerow(["", "Tips", "", caregiver.tips])
        if caregiver.bonus:
            out.writerow(["", "Bonuses", "", caregiver.bonus])
        if caregiver.mileage_amount:
            out.writerow(["", "Mileage", f"{caregiver.mileage_miles} miles",
                          caregiver.mileage_amount])
        if caregiver.other_reimbursement:
            out.writerow(["", "Other reimbursement", "", caregiver.other_reimbursement])
        for adj in caregiver.adjustments:
            out.writerow(["", "MANUAL ADJUSTMENT",
                          f"{adj.kind}: {adj.original_value} -> {adj.new_value}",
                          adj.reason, adj.created_at])
        out.writerow(["", "Taxable earnings", "", caregiver.taxable_earnings])
        out.writerow(["", "Reimbursements", "", caregiver.reimbursements])
        out.writerow(["", "TOTAL BEING PAID", "", caregiver.total_paid])
        out.writerow([])
    return buffer.getvalue()


# --- 6. the OnPay import file -----------------------------------------------

def load_onpay_mapping(path: Path | str | None = None) -> dict:
    with open(Path(path) if path else MAPPING_PATH, encoding="utf-8") as fh:
        return json.load(fh)


ONPAY_HEADER = ["type", "id", "emp_num", "hours", "rate", "treat_as_cash",
                "cash_amount", "ob3_qualified_ot"]
ONPAY_TYPE_PAY_ITEM = "1"
_Q4 = Decimal("0.0001")
_Q2 = Decimal("0.01")


def _q(value: Decimal, places: Decimal) -> Decimal:
    return Decimal(value).quantize(places, rounding=ROUND_HALF_UP)


def onpay_pay_rows(caregiver: CaregiverPayroll, emp_num: str,
                   mapping: dict) -> list[dict]:
    """The OnPay pay-item rows for one person.

    OnPay takes one row per pay item and allows only one row for pay item 1
    and one for pay item 2 per employee. That single rule decides the shape
    of everything below.

    Somebody on one rate gets the ordinary presentation: regular hours at
    their rate, overtime at time and a half. Somebody who worked two rates
    in the week cannot have both on pay item 1, so each rate keeps its own
    row at its real rate and the overtime row carries only the premium -
    half the weighted regular rate - because the straight-time part is
    already in the rate rows above. Both come to the same money; the second
    just does not put a blended rate on the wage statement in place of the
    rates actually worked.
    """
    ids = mapping.get("pay_ids", {})
    tier_ids = mapping.get("tier_pay_ids", {})
    places = Decimal(1).scaleb(-int(mapping.get("rate_decimals", 4)))
    rows: list[dict] = []

    def hourly(pay_id, hours, rate, ob3=None):
        if hours and rate:
            rows.append({"id": str(pay_id), "hours": _q(hours, _Q2),
                         "rate": _q(rate, places), "cash": None, "ob3": ob3})

    def cash(pay_id, amount, treat_as_cash=True):
        if amount:
            rows.append({"id": str(pay_id), "hours": None, "rate": None,
                         "cash": _q(amount, _Q2), "ob3": None,
                         "treat_as_cash": treat_as_cash})

    # Hours, kept per rate tier, with the four-hour minimum folded into the
    # tier that earned it. Guarantee pay is always the guarantee hours at
    # that tier's rate, so the arithmetic still comes out exactly.
    buckets: dict[str, dict] = {}
    for job in caregiver.jobs:
        paid = job.hours_worked + job.guarantee_hours
        if paid <= 0:
            continue
        bucket = buckets.setdefault(job.tier_key, {"hours": ZERO, "rate": job.rate})
        bucket["hours"] += paid
        if job.rate:
            bucket["rate"] = job.rate

    worked = caregiver.hours_worked
    regular_rate = (caregiver.straight_pay / worked) if worked else ZERO
    ot, dt = caregiver.ot_hours, caregiver.dt_hours

    if len(buckets) == 1:
        key, bucket = next(iter(buckets.items()))
        hourly(tier_ids.get(key, ids.get("regular", 1)),
               bucket["hours"] - ot - dt, bucket["rate"])
        hourly(ids.get("overtime", 2), ot, regular_rate * Decimal("1.5"), ob3=ot)
        hourly(ids.get("double_overtime", 22), dt, regular_rate * 2, ob3=dt)
    elif buckets:
        for key, bucket in sorted(buckets.items(), key=lambda kv: -kv[1]["rate"]):
            hourly(tier_ids.get(key, ids.get("regular", 1)),
                   bucket["hours"], bucket["rate"])
        # Premium only: the straight time is already in the rows above.
        hourly(ids.get("overtime", 2), ot, regular_rate * Decimal("0.5"), ob3=ot)
        hourly(ids.get("double_overtime", 22), dt, regular_rate, ob3=dt)

    # Salary and other flat pay. A salaried person has no bookings behind
    # them, so their pay goes on pay item 1 as a cash amount with no hours
    # and no rate, the way OnPay's own template writes it.
    salary = ZERO
    other_taxable = ZERO
    for adj in caregiver.adjustments:
        if adj.booking_id or not adj.taxable:
            continue
        amount = Decimal(str(adj.new_value or 0))
        if adj.kind == "recurring_pay":
            salary += amount
        else:
            other_taxable += amount
    if salary:
        rows.append({"id": str(ids.get("regular", 1)), "hours": None, "rate": None,
                     "cash": _q(salary, _Q2), "ob3": None, "treat_as_cash": False})

    cash(ids.get("bonus", 7), _q(caregiver.bonus + other_taxable, _Q2))
    cash(ids.get("tips", 208), caregiver.tips)
    cash(ids.get("reimbursement", 107), caregiver.reimbursements)

    for row in rows:
        row["emp_num"] = emp_num
    return rows


def onpay_row_total(row: dict) -> Decimal:
    if row["cash"] is not None:
        return _q(row["cash"], _Q2)
    return _q(row["hours"] * row["rate"], _Q2)


def onpay_import_check(run: PayrollRun, roster: dict[str, RosterEntry],
                       mapping: dict | None = None) -> list[dict]:
    """Anything about the import file worth saying out loud before it is used.

    Two things can go wrong quietly. OnPay rejects a file that has an
    employee twice on pay item 1 or 2, so that is checked rather than
    discovered on upload. And a rate has to be rounded to fit the file, so
    what OnPay will actually pay is added back up and compared with what
    this app worked out - a penny apart is still worth seeing.
    """
    mapping = mapping or load_onpay_mapping()
    statuses = run.summary["statuses"]
    problems = []
    for caregiver in run.caregivers:
        entry = roster.get(caregiver.key)
        emp = entry.onpay_clock_user if entry else ""
        if statuses.get(caregiver.key) == "blocked":
            problems.append({
                "caregiver": caregiver.name,
                "problem": "is left out of the file until the payroll check on "
                           "them is sorted out",
            })
            continue
        if not emp:
            problems.append({
                "caregiver": caregiver.name,
                "problem": "has no Clock User in the roster, so OnPay would not "
                           "know who they are - enter them by hand",
            })
            continue
        rows = onpay_pay_rows(caregiver, emp, mapping)
        seen: dict[str, int] = {}
        for row in rows:
            seen[row["id"]] = seen.get(row["id"], 0) + 1
        for pay_id in ("1", "2"):
            if seen.get(pay_id, 0) > 1:
                problems.append({
                    "caregiver": caregiver.name,
                    "problem": f"would be in the file twice on pay item {pay_id}, "
                               "which OnPay does not allow",
                })
        total = sum((onpay_row_total(r) for r in rows), ZERO)
        if _q(total, _Q2) != _q(caregiver.total_paid, _Q2):
            problems.append({
                "caregiver": caregiver.name,
                "problem": f"the file comes to ${_q(total, _Q2)} but this payroll "
                           f"says ${_q(caregiver.total_paid, _Q2)}",
            })
    return problems


def onpay_import_csv(run: PayrollRun, roster: dict[str, RosterEntry],
                     mapping: dict | None = None) -> tuple[str, list[str]]:
    """Build the file OnPay's CSV importer takes.

    Returns the CSV plus a list of anyone left out, so the app can say who
    still has to be entered by hand rather than silently dropping them.
    """
    mapping = mapping or load_onpay_mapping()
    skip_without_id = mapping.get("skip_rows_without_identifier", True)

    buffer, out = _writer()
    if mapping.get("include_header", True):
        out.writerow(ONPAY_HEADER)

    # Somebody the payroll check has stopped does not belong in a file that
    # OnPay will act on. June Salter's rate could not be worked out, so the
    # only rate available is one divided out of the amount paid - writing
    # that into payroll would quietly pay a number nobody agreed to.
    statuses = run.summary["statuses"]

    skipped: list[str] = []
    for caregiver in run.caregivers:
        entry = roster.get(caregiver.key)
        emp = entry.onpay_clock_user if entry else ""
        if statuses.get(caregiver.key) == "blocked":
            skipped.append(caregiver.name)
            continue
        if not emp:
            if skip_without_id:
                skipped.append(caregiver.name)
                continue
            emp = ""
        for row in onpay_pay_rows(caregiver, emp, mapping):
            treat = row.get("treat_as_cash")
            out.writerow([
                ONPAY_TYPE_PAY_ITEM,
                row["id"],
                row["emp_num"],
                _plain(row["hours"]),
                _plain(row["rate"]),
                "1" if (treat is True) else "",
                _plain(row["cash"]),
                _plain(row["ob3"]),
            ])
    return buffer.getvalue(), skipped


def _plain(value) -> str:
    """A number the way OnPay wants it: no currency, no padding, no zeroes."""
    if value in (None, ""):
        return ""
    if isinstance(value, Decimal):
        if value == 0:
            return ""
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    return str(value)


def _render(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, Decimal):
        return "" if value == 0 else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _onpay_values(run: PayrollRun, caregiver: CaregiverPayroll,
                  entry: RosterEntry | None) -> dict:
    parts = caregiver.name.split()
    values = {
        "name": caregiver.name,
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "onpay_clock_user": entry.onpay_clock_user if entry else "",
        "onpay_employee_id": entry.onpay_employee_id if entry else "",
        "guarantee_hours": caregiver.guarantee_hours,
        "guarantee_pay": caregiver.guarantee_pay,
        "ot_hours": caregiver.ot_hours,
        "ot_premium": caregiver.ot_premium,
        "dt_hours": caregiver.dt_hours,
        "dt_premium": caregiver.dt_premium,
        "premium_pay": caregiver.premium_pay,
        "tips": caregiver.tips,
        "bonus": caregiver.bonus,
        "mileage_miles": caregiver.mileage_miles,
        "mileage_amount": caregiver.mileage_amount,
        "other_reimbursement": caregiver.other_reimbursement,
        "reimbursement_total": caregiver.reimbursements,
        "taxable_earnings": caregiver.taxable_earnings,
        "total_paid": caregiver.total_paid,
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "blank": "",
    }
    for tier in run.rules.tiers:
        values[f"hours_{tier['key']}"] = caregiver.tier_hours(tier["key"])
        values[f"pay_{tier['key']}"] = caregiver.tier_pay(tier["key"])
    return values


# --- what the app offers on the exports screen ------------------------------

def all_exports(run: PayrollRun, roster: dict[str, RosterEntry],
                entered: dict[str, bool] | None = None) -> list[dict]:
    stamp = _safe(run.label)
    onpay_csv, skipped = onpay_import_csv(run, roster)
    return [
        {"key": "detail", "name": "Payroll detail",
         "description": "Every job, with the hours, rate and pay behind it.",
         "filename": f"payroll-detail-{stamp}.csv", "content": payroll_detail_csv(run)},
        {"key": "onpay_entry", "name": "OnPay entry sheet",
         "description": "One row per caregiver, in the categories you type into OnPay.",
         "filename": f"onpay-entry-{stamp}.csv",
         "content": onpay_entry_csv(run, roster, entered)},
        {"key": "summary", "name": "Payroll summary",
         "description": "The totals, and the proof that nothing went missing.",
         "filename": f"payroll-summary-{stamp}.csv", "content": payroll_summary_csv(run)},
        {"key": "exceptions", "name": "Things needing attention",
         "description": "Everything the payroll check found, and what to do about it.",
         "filename": f"payroll-exceptions-{stamp}.csv", "content": exceptions_csv(run)},
        {"key": "caregiver", "name": "Caregiver detail",
         "description": "A readable breakdown per caregiver, with the overtime working shown.",
         "filename": f"caregiver-detail-{stamp}.csv",
         "content": caregiver_detail_csv(run, roster)},
        {"key": "onpay_import", "name": "OnPay import file",
         "description": ("Upload this straight into OnPay. One row per pay item, in "
                         "the format OnPay specified."
                         + (f" {len(skipped)} not in it - see below."
                            if skipped else " Everybody is in it.")),
         "filename": f"onpay-import-{stamp}.csv", "content": onpay_csv,
         "skipped": skipped,
         "problems": onpay_import_check(run, roster)},
    ]
