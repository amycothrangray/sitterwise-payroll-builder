"""Reading a Sitterwise bookings export.

Three rules govern this module:

  1. The source file is never modified. Everything it says is preserved
     verbatim on the Job alongside whatever the app works out.
  2. Hours come from Start/End, never from the 'Total Hours' column, which
     disagrees with the clock on a meaningful number of rows and is zero on
     every booking that was not closed out.
  3. Anything the app has to infer - the pay tier, whether a reimbursement
     was mileage - is recorded as an inference so it can be shown as one.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .model import ImportResult, Job
from .money import hours as to_hours, is_blank, money, rate as to_rate
from .rules import Rules

# Column name in the export -> attribute we keep it under. Matching is done
# on a squashed lowercase form so a renamed or reordered export still lands.
COLUMN_MAP = {
    "bookingid": "booking_id",
    "ulid": "ulid",
    "clientname": "client_name",
    "clientemail": "client_email",
    "clientphone": "client_phone",
    "servicetype": "service_type",
    "locationtype": "location_type",
    "hotel": "hotel",
    "address": "address",
    "startdate": "start_date",
    "starttime": "start_time",
    "enddate": "end_date",
    "endtime": "end_time",
    "totalhours": "total_hours",
    "caregivername": "caregiver_name",
    "status": "status",
    "paymentstatus": "payment_status",
    "chargetoclient": "charge_to_client",
    "paidtocaregiver": "paid_to_caregiver",
    "sitterwisecut": "sitterwise_cut",
    "reimbursement": "reimbursement",
    "tip": "tip",
    "bonus": "bonus",
    "totalamount": "total_amount",
    "createdat": "created_at",
    "adminnotes": "admin_notes",
    "lifesaverbonus": "lifesaver_bonus",
    # fields Sitterwise added in the August 2026 export
    "carecomjobnumber": "care_com_job_number",
    "hoursworked": "hours_worked_stated",
    "hoursbilled": "hours_billed_stated",
    "minimumapplied": "minimum_applied_stated",
    "onpayclockuser": "onpay_clock_user",
    "reimbursementdescription2": "other_reimbursement_description",
    "roundtripmiles": "round_trip_miles",
    "mileageapprovedmiles": "mileage_approved_miles",
    "mileageapprovalstatus": "mileage_approval_status",
    "payablemiles": "payable_miles",
    "hourlyrate": "pay_rate",
    # tolerated aliases for fields Sitterwise may add later
    "caregiverid": "caregiver_id",
    "childrencount": "children_count",
    "children": "children_count",
    "numberofchildren": "children_count",
    "payrate": "pay_rate",
    "caregiverhourlyrate": "pay_rate",
    "mileagemiles": "mileage_miles",
    "miles": "mileage_miles",
    "mileagerate": "mileage_rate",
    "mileageamount": "mileage_amount",
    "otherreimbursement": "other_reimbursement",
    "otherreimbursementdescription": "other_reimbursement_description",
    "reimbursementdescription": "other_reimbursement_description",
}

REQUIRED = ["booking_id", "caregiver_name", "start_date", "start_time",
            "end_date", "end_time", "status", "paid_to_caregiver"]

# Sitterwise stopped exporting Total Hours once it started exporting Hours
# Worked. Either is accepted as the cross-check against the clock.


def _squash(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _parse_datetime(day, clock) -> datetime | None:
    """Turn the export's separate date and time strings into a datetime."""
    if is_blank(day):
        return None
    if isinstance(day, datetime):
        base = day.date()
    elif isinstance(day, date):
        base = day
    else:
        base = date.fromisoformat(str(day).strip()[:10])
    if is_blank(clock):
        return datetime.combine(base, datetime.min.time())
    if isinstance(clock, datetime):
        return datetime.combine(base, clock.time())
    text = str(clock).strip()
    for pattern in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.combine(base, datetime.strptime(text, pattern).time())
        except ValueError:
            continue
    raise ValueError(f"cannot read the time {clock!r}")


def read_workbook(path: Path | str) -> tuple[list[str], list[dict], str]:
    """Return (header, rows-as-dicts, sha256) from an .xlsx or .csv export."""
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.suffix.lower() == ".csv":
        import csv
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            rows = list(reader)
        header = rows[0] if rows else []
        body = [dict(zip(header, r)) for r in rows[1:] if any(str(c).strip() for c in r)]
        return header, body, digest

    import openpyxl
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = workbook.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    workbook.close()
    if not rows:
        return [], [], digest
    header = [str(h) if h is not None else "" for h in rows[0]]
    body = [
        dict(zip(header, row))
        for row in rows[1:]
        if any(cell is not None and str(cell).strip() != "" for cell in row)
    ]
    return header, body, digest


def import_export(path: Path | str, rules: Rules) -> ImportResult:
    header, raw_rows, digest = read_workbook(path)

    mapped, unmapped = {}, []
    for column in header:
        key = COLUMN_MAP.get(_squash(column))
        if key:
            mapped[key] = column
        elif str(column).strip():
            unmapped.append(column)
    missing = [name for name in REQUIRED if name not in mapped]

    jobs: list[Job] = []
    errors: list[dict] = []
    today = date.today()

    for index, row in enumerate(raw_rows, start=2):
        def cell(key):
            column = mapped.get(key)
            return row.get(column) if column else None

        try:
            job = _build_job(index, cell, rules, today)
        except Exception as exc:  # a single bad row must not stop payroll
            errors.append({
                "row": index,
                "booking_id": str(cell("booking_id") or ""),
                "problem": str(exc),
            })
            continue
        jobs.append(job)

    dates = [j.workday for j in jobs if j.workday]
    return ImportResult(
        jobs=jobs,
        source_filename=Path(path).name,
        source_sha256=digest,
        row_count=len(raw_rows),
        header=header,
        unmapped_columns=unmapped,
        missing_columns=missing,
        parse_errors=errors,
        min_date=min(dates) if dates else None,
        max_date=max(dates) if dates else None,
    )


def _build_job(row_number, cell, rules: Rules, today: date) -> Job:
    start = _parse_datetime(cell("start_date"), cell("start_time"))
    end = _parse_datetime(cell("end_date"), cell("end_time"))

    raw_tip = cell("tip")
    raw_reimb = cell("reimbursement")
    raw_total_hours = cell("hours_worked_stated")
    if is_blank(raw_total_hours):
        raw_total_hours = cell("total_hours")

    job = Job(
        row_number=row_number,
        booking_id=str(cell("booking_id") or "").strip(),
        ulid=str(cell("ulid") or "").strip(),
        caregiver_name=str(cell("caregiver_name") or "").strip(),
        client_name=str(cell("client_name") or "").strip(),
        service_type=str(cell("service_type") or "").strip(),
        location_type=str(cell("location_type") or "").strip(),
        hotel=str(cell("hotel") or "").strip(),
        status=str(cell("status") or "").strip().lower(),
        payment_status=str(cell("payment_status") or "").strip().lower(),
        admin_notes=str(cell("admin_notes") or "").strip(),
        start=start,
        end=end,
        hours_exported=None if is_blank(raw_total_hours) else to_hours(raw_total_hours),
        paid_to_caregiver=money(cell("paid_to_caregiver")),
        charge_to_client=money(cell("charge_to_client")),
        sitterwise_cut=money(cell("sitterwise_cut")),
        tip=money(raw_tip),
        tip_was_blank=is_blank(raw_tip),
        reimbursement=money(raw_reimb),
        reimbursement_was_blank=is_blank(raw_reimb),
        reimbursement_description=str(
            cell("other_reimbursement_description") or "").strip(),
        bonus=money(cell("bonus")),
        lifesaver_bonus=money(cell("lifesaver_bonus")),
    )

    _set_hours_and_workday(job, rules)
    _set_payability(job, rules, today)
    _set_rate(job, cell, rules)
    _set_pay(job, rules, cell)
    _split_reimbursement(job, cell, rules)
    return job


def _set_hours_and_workday(job: Job, rules: Rules) -> None:
    if job.start and job.end:
        span = (job.end - job.start).total_seconds() / 3600
        job.hours_worked = to_hours(span)
        if job.hours_worked <= 0:
            job.import_notes.append(
                "The end time is not after the start time, so this job has no hours."
            )
    else:
        job.import_notes.append("This job is missing a start or end time.")

    if job.start:
        job.workday = job.start.date()
        if rules.overnight_attribution == "shift_start_day" and job.end and job.end.date() != job.start.date():
            job.import_notes.append(
                "This shift runs past midnight. All of its hours are counted on "
                f"{job.start.date():%b %-d}, the day it started."
            )

    if job.hours_exported is not None and job.hours_worked > 0:
        gap = abs(job.hours_exported - job.hours_worked)
        if gap > to_hours(rules.v("hours_mismatch_tolerance", 0.01)):
            job.import_notes.append(
                f"Sitterwise says {job.hours_exported} hours but the start and end "
                f"times work out to {job.hours_worked}. The app used the clock."
            )


def _set_payability(job: Job, rules: Rules, today: date) -> None:
    if job.status in rules.payable_statuses:
        job.is_payable = True
        return
    job.is_payable = False
    if job.status in rules.never_pay_statuses:
        job.exclusion_reason = f"Status is {job.status}"
    elif job.status in rules.flag_if_past_dated_statuses:
        if job.workday and job.workday < today:
            job.exclusion_reason = "Worked but never closed out in Sitterwise"
        else:
            job.exclusion_reason = "Still upcoming"
    else:
        job.exclusion_reason = f"Status is {job.status}, which is not a paid status"


def _set_rate(job: Job, cell, rules: Rules) -> None:
    """Work out which pay tier applied.

    If Sitterwise ever starts exporting the rate or the children count, we use
    that. Until then the tier is worked backwards out of 'Paid to Caregiver',
    and we say so.
    """
    stated_rate = cell("pay_rate")
    if not is_blank(stated_rate):
        rate_value = to_rate(stated_rate)
        for tier in rules.tiers:
            if abs(rate_value - to_rate(tier["rate"])) <= rules.rate_match_tolerance:
                _apply_tier(job, tier, "stated_in_export")
                return
        job.rate = rate_value
        job.tier_key, job.tier_label, job.rate_basis = "other", "Other rate", "stated_in_export"
        job.import_notes.append(
            f"Sitterwise gave a rate of ${rate_value} which is not one of the "
            "rates set up in Settings."
        )
        return

    if job.paid_to_caregiver <= 0:
        job.rate_basis = "none"
        if job.is_payable:
            job.import_notes.append(
                "This job has no pay recorded, so the app cannot tell which rate applied."
            )
        return

    bases: list[tuple[Decimal, str]] = []
    if job.hours_worked > 0:
        bases.append((job.hours_worked, "worked_hours"))
    if job.hours_exported and job.hours_exported > 0 and job.hours_exported != job.hours_worked:
        bases.append((job.hours_exported, "exported_hours"))

    for basis_hours, basis_name in bases:
        payable_hours = basis_hours
        if rules.minimum_enabled and payable_hours < rules.minimum_hours:
            payable_hours = rules.minimum_hours
        implied = job.paid_to_caregiver / payable_hours
        for tier in rules.tiers:
            if abs(implied - Decimal(str(tier["rate"]))) <= rules.rate_match_tolerance:
                _apply_tier(job, tier, f"inferred_from_pay:{basis_name}")
                if basis_name == "exported_hours":
                    job.import_notes.append(
                        "The pay only makes sense against Sitterwise's own hours figure, "
                        "not the start and end times. Worth a look."
                    )
                return

    job.rate_basis = "unmatched"
    job.rate = to_rate(job.paid_to_caregiver / max(job.hours_worked, Decimal("0.01")))
    job.import_notes.append(
        f"The pay of {job.paid_to_caregiver} over {job.hours_worked} hours does not "
        "match any rate in Settings, so the app could not tell which tier this was."
    )


def _apply_tier(job: Job, tier: dict, basis: str) -> None:
    job.tier_key = tier["key"]
    job.tier_label = tier["label"]
    job.rate = to_rate(tier["rate"])
    job.rate_basis = basis


def _set_pay(job: Job, rules: Rules, cell=None) -> None:
    """Work out what the caregiver is paid for, as opposed to what they worked.

    Sitterwise now exports Hours Billed and a Minimum Applied flag. When they
    are there we use them rather than reapplying the rule ourselves - the
    platform is the authority on what it decided to pay.
    """
    stated_billed = cell("hours_billed_stated") if cell else None
    stated_minimum = cell("minimum_applied_stated") if cell else None

    if not is_blank(stated_billed):
        job.hours_paid = to_hours(stated_billed)
        job.minimum_applied = str(stated_minimum).strip().lower() in ("true", "yes", "1")
        if job.hours_paid < job.hours_worked:
            job.import_notes.append(
                f"Sitterwise says this job is billed at {job.hours_paid} hours but the clock "
                f"shows {job.hours_worked} worked. The app paid the billed figure."
            )
    else:
        job.hours_paid = job.hours_worked
        if rules.minimum_enabled and 0 < job.hours_worked < rules.minimum_hours:
            job.hours_paid = rules.minimum_hours
            job.minimum_applied = True
    job.guarantee_hours = to_hours(max(Decimal("0"), job.hours_paid - job.hours_worked))
    job.straight_pay = money(job.hours_worked * job.rate)
    job.guarantee_pay = money(job.guarantee_hours * job.rate)

    if job.minimum_applied:
        job.import_notes.append(
            f"Worked {job.hours_worked} hours but paid for {job.hours_paid} because of "
            f"the {rules.minimum_hours}-hour minimum. The extra "
            f"{job.guarantee_hours} hours are guarantee pay and do not count toward overtime."
        )


def _stated_mileage(job: Job, cell, rules: Rules) -> bool:
    """Use Sitterwise's own mileage columns when they carry a real figure.

    The columns arrived in the August 2026 export but are zero on every row so
    far, so a zero is treated as "nothing recorded" rather than "no mileage" -
    otherwise a reimbursement that plainly is mileage would be hidden by an
    empty column.
    """
    amount = cell("mileage_amount")
    payable = cell("payable_miles")
    round_trip = cell("round_trip_miles")
    if is_blank(amount) or money(amount) <= 0:
        return False

    rate_used = rules.mileage_rate_for(job.workday or date.today())
    stated_rate = cell("mileage_rate")
    if not is_blank(stated_rate) and to_rate(stated_rate) > 0:
        rate_used = to_rate(stated_rate)

    job.mileage_amount = money(amount)
    job.mileage_rate = rate_used
    job.mileage_payable_miles = Decimal(str(payable)) if not is_blank(payable) else None
    job.mileage_miles = (Decimal(str(round_trip)) if not is_blank(round_trip)
                         else (job.mileage_payable_miles + rules.deduct_first_miles
                               if job.mileage_payable_miles is not None else None))
    if job.mileage_payable_miles is not None:
        job.mileage_policy_amount = money(job.mileage_payable_miles * rate_used)
    job.other_reimbursement = money(max(Decimal("0"), job.reimbursement - job.mileage_amount))
    job.mileage_from_export = not is_blank(round_trip)
    job.import_notes.append(
        f"Mileage came from Sitterwise: {job.mileage_payable_miles} payable miles, "
        f"${job.mileage_amount}. No guessing needed."
    )
    return True


def _split_reimbursement(job: Job, cell, rules: Rules) -> None:
    """Separate mileage from every other kind of reimbursement.

    Sitterwise has one untyped Reimbursement column, so mileage has to be
    recognised by arithmetic: a reimbursement that divides into a whole number
    of miles at the rate in force that day is almost certainly mileage. This
    whole function disappears the day Sitterwise stores miles properly.
    """
    if _stated_mileage(job, cell, rules):
        return

    stated_miles = cell("mileage_miles")
    if not is_blank(stated_miles):
        miles = Decimal(str(stated_miles))
        stated_rate = cell("mileage_rate")
        rate_used = to_rate(stated_rate) if not is_blank(stated_rate) else rules.mileage_rate_for(
            job.workday or date.today()
        )
        job.mileage_miles = miles
        job.mileage_rate = rate_used
        stated_amount = cell("mileage_amount")
        job.mileage_amount = money(stated_amount) if not is_blank(stated_amount) else money(miles * rate_used)
        job.other_reimbursement = money(cell("other_reimbursement")) if not is_blank(
            cell("other_reimbursement")
        ) else money(job.reimbursement - job.mileage_amount)
        if job.other_reimbursement < 0:
            job.other_reimbursement = money(0)
        return

    if job.reimbursement <= 0:
        return

    if not rules.detect_mileage:
        job.other_reimbursement = job.reimbursement
        return

    rate_used = rules.mileage_rate_for(job.workday or date.today())
    eligible = rules.mileage_allowed_on(job.service_type)
    if rate_used > 0:
        miles = job.reimbursement / rate_used
        nearest = miles.quantize(Decimal("1"))
        looks_like_mileage = abs(miles - nearest) <= rules.whole_mile_tolerance

        # What the figure stands for depends on whether the 40-mile deduction
        # was taken off before it was entered. Work out the round trip either
        # way, because eligibility is judged on the whole drive.
        if rules.mileage_amount_is_whole_trip:
            round_trip, payable = nearest, rules.payable_miles(nearest)
        else:
            payable = nearest
            round_trip = nearest + rules.deduct_first_miles

        if looks_like_mileage and round_trip >= rules.minimum_miles and eligible:
            job.mileage_miles = round_trip
            job.mileage_payable_miles = payable
            job.mileage_rate = rate_used
            job.mileage_amount = job.reimbursement
            job.mileage_policy_amount = (payable * rate_used).quantize(Decimal("0.01"))
            job.import_notes.append(
                f"Treated as mileage: ${job.reimbursement} is exactly {nearest} miles at "
                f"${rate_used} a mile"
                + (f", read as a {round_trip}-mile round trip, of which {payable} are payable."
                   if rules.deduct_first_miles else ".")
                + " Sitterwise has no mileage field, so the app worked this out from the amount."
            )
            return
        if looks_like_mileage and not eligible:
            job.other_reimbursement = job.reimbursement
            job.mileage_rejected_reason = "service_type"
            job.import_notes.append(
                f"${job.reimbursement} is exactly {nearest} miles at ${rate_used} a mile, but "
                f"this is a {job.service_type} job and mileage is only paid on "
                f"{' or '.join(sorted(rules.mileage_eligible_service_types))} jobs. NOT treated "
                "as mileage."
            )
            return
        if looks_like_mileage and round_trip < rules.minimum_miles:
            job.other_reimbursement = job.reimbursement
            job.mileage_rejected_reason = "under_minimum"
            job.import_notes.append(
                f"${job.reimbursement} works out to a {round_trip}-mile round trip, under the "
                f"{rules.minimum_miles} miles a mileage claim needs. NOT treated as mileage."
            )
            return

    job.other_reimbursement = job.reimbursement
    job.import_notes.append(
        f"Treated as an expense reimbursement, not mileage: ${job.reimbursement} is not a "
        f"whole number of miles at ${rate_used} a mile. Sitterwise records no description."
    )
