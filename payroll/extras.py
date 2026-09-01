"""Two things a bookings export cannot tell you.

**Payroll notes** replace the "Payroll Odds & Ends" spreadsheet. Somebody
notices something during the week - a bonus owed, a caregiver who never
checked out, a Trustline fee to reimburse - and writes it down. The old
sheet then relied on a person reading it back, doing the arithmetic and
retyping it into OnPay. Here the note carries its own numbers, and the
payroll run it belongs to applies it as an ordinary adjustment, so it lands
in the same audit trail as every other manual change.

**Recurring pay** covers people paid for work that never appears in a
booking at all: a monthly salary, admin hours, phone days, training. Each
entry becomes its own payroll line on the periods it is due.

Nothing here invents money. A note only ever does what its author typed.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .engine import ZERO, Adjustment, CaregiverPayroll
from .money import money
from .roster import normalise_name

# Notes that change the payroll on their own, and what each one means.
#
# Only hours and rate touch a booking, because only those are corrections to
# what the export said. Everything else is money added alongside the work -
# a bonus is not a different opinion about a job, it is extra pay - so it
# rides at the caregiver level where it cannot silently overwrite an
# imported figure. That also keeps it out of the overtime regular rate,
# which is where a bonus does not belong.
APPLIES_ITSELF = {
    "bonus":         {"label": "Bonus",              "taxable": True,  "level": "caregiver"},
    "cancellation":  {"label": "Cancellation pay",   "taxable": True,  "level": "caregiver"},
    "extra_pay":     {"label": "Extra pay",          "taxable": True,  "level": "caregiver"},
    "dock":          {"label": "Reduce pay",         "taxable": True,  "level": "caregiver"},
    "reimbursement": {"label": "Reimbursement",      "taxable": False, "level": "caregiver"},
    "mileage":       {"label": "Mileage",            "taxable": False, "level": "caregiver"},
    "hours":         {"label": "Hours correction",   "taxable": True,  "level": "booking"},
    "rate":          {"label": "Rate correction",    "taxable": True,  "level": "booking"},
}

# Notes that are shown but never applied by the app, because acting on them
# takes a judgement the app should not make on its own.
NEEDS_A_PERSON = {
    "exclude":  "Do not pay this - already paid another way",
    "check":    "Pay by paper check, not direct deposit",
    "other":    "Just a note",
}

NOTE_KINDS = {**{k: v["label"] for k, v in APPLIES_ITSELF.items()}, **NEEDS_A_PERSON}


def note_label(kind: str) -> str:
    return NOTE_KINDS.get(kind, kind)


def amount_of(value) -> Decimal:
    try:
        return money(Decimal(str(value or "0")))
    except (InvalidOperation, ValueError):
        return ZERO


def note_problem(note: dict) -> str:
    """Why this note cannot be applied yet, in plain English. '' if it can."""
    kind = note.get("kind", "")
    if kind in NEEDS_A_PERSON:
        return ""
    if kind not in APPLIES_ITSELF:
        return f"'{kind}' is not something this app knows how to apply."
    spec = APPLIES_ITSELF[kind]
    if not note.get("caregiver_key"):
        return "No caregiver on this note, so there is nobody to pay."
    if spec["level"] == "booking" and not note.get("booking_id"):
        return "A booking number is needed to correct hours or a rate."
    if amount_of(note.get("amount")) == ZERO:
        return "No amount on this note."
    return ""


def note_to_adjustment(note: dict) -> Adjustment:
    """Turn a note into the adjustment that carries it into the payroll.

    The reason text is written so that somebody reading the audit trail a
    year later can see this came from a note, who wrote it and when.
    """
    kind = note["kind"]
    spec = APPLIES_ITSELF[kind]
    amount = amount_of(note.get("amount"))
    if kind == "dock":
        amount = -abs(amount)

    written = (note.get("created_at") or "")[:10]
    author = note.get("created_by") or ""
    trail = f"Payroll note{f' from {author}' if author else ''}{f', {written}' if written else ''}"
    detail = (note.get("detail") or "").strip()
    reason = f"{trail}: {detail}" if detail else trail

    if spec["level"] == "booking":
        return Adjustment(
            id="", caregiver_key=note["caregiver_key"], kind=kind,
            booking_id=note.get("booking_id", ""),
            original_value=note.get("original_value", ""),
            new_value=str(amount), reason=reason, taxable=spec["taxable"],
        )
    return Adjustment(
        id="", caregiver_key=note["caregiver_key"], kind=kind, booking_id="",
        original_value="0.00", new_value=str(amount), reason=reason,
        taxable=spec["taxable"],
    )


# -- recurring and non-booking pay ---------------------------------------

def first_monday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7)


def _months_touched(start: date, end: date) -> list[tuple[int, int]]:
    months, cursor = [], date(start.year, start.month, 1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        days = monthrange(cursor.year, cursor.month)[1]
        cursor = cursor + timedelta(days=days)
        cursor = date(cursor.year, cursor.month, 1)
    return months


def is_due(entry: dict, period_start: date, period_end: date) -> bool:
    """Does this entry get paid on the payroll covering these dates?

    Weekly lands on every payroll. Monthly lands on the one payroll whose
    dates contain the first Monday of the month - Sitterwise pays Monday to
    Sunday, so exactly one week a month qualifies and nobody gets paid twice
    when a week straddles a month end.
    """
    if not entry.get("active", 1):
        return False
    frequency = entry.get("frequency", "monthly")
    if frequency == "weekly":
        return True
    if frequency == "monthly":
        if entry.get("schedule", "first_monday") != "first_monday":
            return False
        return any(period_start <= first_monday(y, m) <= period_end
                   for y, m in _months_touched(period_start, period_end))
    if frequency == "one_off":
        return False
    return False


def due_in_period(entries: list[dict], period_start: date, period_end: date) -> list[dict]:
    return [e for e in entries if is_due(e, period_start, period_end)]


def recurring_payroll(entry: dict, period_start: date, period_end: date) -> CaregiverPayroll:
    """A payroll line for somebody with no bookings this period.

    Built as an ordinary CaregiverPayroll carrying a single caregiver-level
    adjustment, so it flows into the OnPay screen, the exports and the
    reconciliation exactly like everybody else - with no hours, no overtime
    and no tiers, because there is no booked work behind it.
    """
    amount = amount_of(entry.get("amount"))
    frequency = entry.get("frequency", "monthly")
    label = {"weekly": "weekly", "monthly": "monthly", "one_off": "one-off"}.get(
        frequency, frequency)
    detail = (entry.get("note") or "").strip()
    reason = f"Set up as {label} pay in Settings"
    if detail:
        reason += f" - {detail}"

    adjustment = Adjustment(
        id="", caregiver_key=entry["caregiver_key"], kind="recurring_pay",
        booking_id="", original_value="0.00", new_value=str(amount),
        reason=reason, taxable=bool(entry.get("taxable", 1)),
    )
    taxable = amount if adjustment.taxable else ZERO
    nontaxable = ZERO if adjustment.taxable else amount

    return CaregiverPayroll(
        key=entry["caregiver_key"], name=entry["person_name"], jobs=[], weeks=[],
        tiers=[], guarantee_hours=ZERO, guarantee_pay=ZERO, ot_hours=ZERO,
        ot_premium=ZERO, dt_hours=ZERO, dt_premium=ZERO, tips=ZERO, bonus=ZERO,
        mileage_miles=ZERO, mileage_amount=ZERO, other_reimbursement=ZERO,
        adjustments=[adjustment], adjustment_taxable_total=taxable,
        adjustment_nontaxable_total=nontaxable, uses_multiple_rates=False,
    )


def key_for(person_name: str) -> str:
    return normalise_name(person_name)
