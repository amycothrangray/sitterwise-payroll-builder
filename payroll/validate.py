"""The payroll check.

Everything here answers one question: is there anything in this payroll that
could make us pay the wrong person, the wrong amount, or nothing at all?

Findings come in three strengths:

  stop    payroll cannot be finalised until this is dealt with
  review  Amy should look, but she can decide it is fine
  note    worth knowing, usually about the data rather than the money

Every finding is written in plain English, names the caregiver and the
bookings involved, and says what to do about it.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .engine import CaregiverPayroll
from .model import Job
from .roster import RosterEntry
from .rules import Rules

STOP = "stop"
REVIEW = "review"
NOTE = "note"

ZERO = Decimal("0")


@dataclass
class Finding:
    code: str
    level: str
    title: str
    detail: str
    what_to_do: str = ""
    caregiver_key: str = ""
    caregiver_name: str = ""
    booking_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "level": self.level,
            "title": self.title,
            "detail": self.detail,
            "what_to_do": self.what_to_do,
            "caregiver_key": self.caregiver_key,
            "caregiver_name": self.caregiver_name,
            "booking_ids": self.booking_ids,
        }


def run_checks(caregivers: list[CaregiverPayroll], all_jobs: list[Job],
               period_jobs: list[Job], roster: dict[str, RosterEntry],
               rules: Rules, previously_paid: dict[str, str] | None = None,
               period_start: date | None = None,
               period_end: date | None = None,
               period_all_jobs: list[Job] | None = None) -> list[Finding]:
    previously_paid = previously_paid or {}
    findings: list[Finding] = []

    findings += _check_duplicates(period_jobs)
    findings += _check_already_paid(period_jobs, previously_paid)
    findings += _check_test_data(period_all_jobs or period_jobs, rules)
    findings += _check_unknown_service_types(period_jobs, rules)
    findings += _check_unclosed_jobs(all_jobs, period_start, period_end, rules)
    findings += _check_settings(rules)

    for caregiver in caregivers:
        findings += _check_caregiver(caregiver, roster, rules)

    findings += _check_data_gaps(period_jobs, rules)
    findings += _check_unconfirmed_roster(caregivers, roster)
    findings += _summarise_overtime(caregivers)
    return _sorted(findings)


def _summarise_overtime(caregivers: list[CaregiverPayroll]) -> list[Finding]:
    """One note for all the overtime, not one per person.

    Overtime is already on every card and in the OnPay grid. Repeating it here
    once per caregiver pushed everything that needed action off the screen.
    """
    with_ot = [c for c in caregivers if c.ot_hours > 0 or c.dt_hours > 0]
    if not with_ot:
        return []
    hours = sum((c.ot_hours + c.dt_hours for c in with_ot), ZERO)
    premium = sum((c.ot_premium + c.dt_premium for c in with_ot), ZERO)
    listing = ", ".join(f"{c.name} {c.ot_hours + c.dt_hours}" for c in
                        sorted(with_ot, key=lambda c: -(c.ot_hours + c.dt_hours))[:8])
    return [Finding(
        "overtime_summary", NOTE,
        f"{len(with_ot)} caregivers worked overtime - {hours} hours, {premium} in premium pay",
        f"Most hours first: {listing}"
        + (" and others." if len(with_ot) > 8 else "."),
        "Open a caregiver's card to see exactly which days caused it.",
        booking_ids=[b for c in with_ot for b in _ot_bookings(c) + _dt_bookings(c)][:60],
    )]


def _check_unconfirmed_roster(caregivers: list[CaregiverPayroll],
                              roster: dict[str, RosterEntry]) -> list[Finding]:
    """Caregivers the app added to the roster itself and nobody has confirmed.

    Raised once rather than per person, because on a first run that would be
    every single caregiver and would drown out everything that matters.
    """
    unconfirmed = [c for c in caregivers
                   if (entry := roster.get(c.key)) and entry.source == "added_automatically"]
    if not unconfirmed:
        return []
    owed = sum((c.total_paid for c in unconfirmed), ZERO)
    names = sorted(c.name for c in unconfirmed if c.name)
    return [Finding(
        "roster_unconfirmed", REVIEW,
        f"{len(unconfirmed)} caregivers have not had their OnPay setup confirmed",
        f"The app added them to the roster itself when it saw them in the export, so it does "
        f"not yet know whether they can actually be paid. Between them they are owed {owed}. "
        + (", ".join(names[:6]) + (" and others." if len(names) > 6 else ".")),
        "Import your employee list from OnPay on the Roster screen - that sets everyone at "
        "once. Anyone genuinely not in OnPay can be marked so, which will then block payroll.",
        booking_ids=[j.booking_id for c in unconfirmed for j in c.jobs][:60],
    )]


def _sorted(findings: list[Finding]) -> list[Finding]:
    order = {STOP: 0, REVIEW: 1, NOTE: 2}
    return sorted(findings, key=lambda f: (order.get(f.level, 3), f.caregiver_name, f.code))


# --- file-wide checks ------------------------------------------------------

def _check_duplicates(jobs: list[Job]) -> list[Finding]:
    out = []
    counts = Counter(j.booking_id for j in jobs if j.booking_id)
    for booking_id, count in counts.items():
        if count > 1:
            job = next(j for j in jobs if j.booking_id == booking_id)
            out.append(Finding(
                "duplicate_booking", STOP,
                f"Booking {booking_id} appears {count} times",
                f"The same booking shows up {count} times in this export, which would "
                f"pay {job.display_name or 'someone'} more than once for one job.",
                "Check the export. If it really is one job, remove the extra rows and upload again.",
                job.caregiver_key, job.display_name, [booking_id],
            ))

    by_shift = defaultdict(list)
    for job in jobs:
        if job.start and job.end and job.caregiver_key:
            by_shift[(job.caregiver_key, job.start, job.end)].append(job.booking_id)
    for (key, start, _), ids in by_shift.items():
        if len(ids) > 1:
            name = next(j.display_name for j in jobs if j.caregiver_key == key)
            out.append(Finding(
                "duplicate_shift", STOP,
                f"{name} has the same shift booked twice",
                f"Bookings {' and '.join(ids)} are all on {start:%b %-d} at "
                f"{start:%-I:%M %p} for the same person.",
                "One of these is probably a mistake. Check Sitterwise before paying both.",
                key, name, ids,
            ))
    return out


def _check_already_paid(jobs: list[Job], previously_paid: dict[str, str]) -> list[Finding]:
    out = []
    for job in jobs:
        run_label = previously_paid.get(job.booking_id)
        if run_label:
            out.append(Finding(
                "already_paid", STOP,
                f"Booking {job.booking_id} was already paid",
                f"{job.display_name} was paid for this job in {run_label}. Paying it "
                "again would be a duplicate.",
                "Leave it out of this payroll, or unlock the earlier run if it was wrong.",
                job.caregiver_key, job.display_name, [job.booking_id],
            ))
    return out


def _looks_like_test_data(name: str, patterns: set[str]) -> bool:
    """True only when a name is made up entirely of test-ish words.

    Matching "test" anywhere in the text would flag a real client called
    Testa, or a family whose booking is named "Test Family" by an admin who
    meant something real. Requiring every word to be a test word keeps
    "Test Test" and "Demo Demo" while leaving real names alone.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", str(name or "").lower()) if w]
    return bool(words) and all(word in patterns for word in words)


def _check_test_data(jobs: list[Job], rules: Rules) -> list[Finding]:
    patterns = {p.lower() for p in rules.v("test_client_patterns", [])}
    out = []
    for job in jobs:
        if _looks_like_test_data(job.client_name, patterns) or \
                _looks_like_test_data(job.caregiver_name, patterns):
            out.append(Finding(
                "test_booking", STOP if job.is_payable else REVIEW,
                f"Booking {job.booking_id} looks like test data",
                f"The client on this booking is \"{job.client_name}\", which looks like a "
                f"test record rather than a real job. It has {job.paid_to_caregiver} of pay on it"
                + (" and is being paid." if job.is_payable
                   else f", but is not being paid because its status is {job.status}."),
                "Delete it in Sitterwise so it stops turning up in exports.",
                job.caregiver_key, job.display_name, [job.booking_id],
            ))
    return out


def _check_unknown_service_types(jobs: list[Job], rules: Rules) -> list[Finding]:
    known = rules.known_service_types
    unknown = sorted({j.service_type for j in jobs if j.service_type and j.service_type not in known})
    return [Finding(
        "unknown_service_type", REVIEW,
        f"New kind of job: {kind}",
        f"This export has {kind} jobs, which the app has not seen before. It has paid "
        "them the same way as everything else.",
        "If this kind of job is paid differently, add it to Settings.",
        booking_ids=[j.booking_id for j in jobs if j.service_type == kind][:20],
    ) for kind in unknown]


def _check_unclosed_jobs(all_jobs: list[Job], period_start, period_end, rules: Rules) -> list[Finding]:
    if not period_start or not period_end:
        return []
    stragglers = [
        j for j in all_jobs
        if j.status in rules.flag_if_past_dated_statuses
        and j.workday and period_start <= j.workday <= period_end
        and j.workday < date.today()
    ]
    if not stragglers:
        return []
    names = sorted({j.display_name for j in stragglers if j.display_name})
    return [Finding(
        "not_closed_out", REVIEW,
        f"{len(stragglers)} job{'s' if len(stragglers) != 1 else ''} in this period "
        "were never closed out",
        "These jobs are in the past but are still marked \"confirmed\" in Sitterwise, so "
        f"they are not being paid: {', '.join(names[:8])}"
        + (" and others." if len(names) > 8 else "."),
        "If they were worked, mark them completed in Sitterwise and upload the export again.",
        booking_ids=[j.booking_id for j in stragglers],
    )]


def _check_settings(rules: Rules) -> list[Finding]:
    out = []
    if rules.seventh_day_enabled and not rules.workweek_start_confirmed:
        out.append(Finding(
            "workweek_unconfirmed", NOTE,
            "Nobody has confirmed which day the work week starts",
            f"The app is treating the work week as starting on "
            f"{str(rules.overtime.get('workweek_start_day', 'sunday')).title()}. That only "
            "changes the maths for weekly overtime and seventh-day rules.",
            "Set it in Settings and mark it confirmed so this stops asking.",
        ))
    return out


# --- per caregiver ---------------------------------------------------------

def _check_caregiver(caregiver: CaregiverPayroll, roster: dict[str, RosterEntry],
                     rules: Rules) -> list[Finding]:
    out: list[Finding] = []
    key, name = caregiver.key, caregiver.name

    if not name:
        out.append(Finding(
            "missing_caregiver", STOP,
            "A job has no caregiver on it",
            f"{len(caregiver.jobs)} job(s) in this period have a blank caregiver name, so "
            "there is nobody to pay.",
            "Fill in the caregiver in Sitterwise and upload the export again.",
            key, "(no name)", [j.booking_id for j in caregiver.jobs],
        ))
        return out

    entry = roster.get(key)
    if entry is None:
        out.append(Finding(
            "not_on_roster", STOP,
            f"{name} is not on the payroll roster",
            f"{name} worked {caregiver.hours_worked} hours this period but has no roster "
            "entry, so the app does not know whether they can be paid.",
            "Add them on the Roster screen, or import a fresh employee list from OnPay.",
            key, name, [j.booking_id for j in caregiver.jobs],
        ))
    elif entry.is_blocking:
        out.append(Finding(
            "not_in_onpay", STOP,
            f"{name} is not set up in OnPay",
            f"{name} is owed {caregiver.total_paid} this period but is marked "
            f"\"{entry.status_label}\".",
            "Set them up in OnPay, then update their status on the Roster screen.",
            key, name, [j.booking_id for j in caregiver.jobs],
        ))
    elif entry.needs_attention and entry.source != "added_automatically":
        out.append(Finding(
            "onpay_incomplete", REVIEW,
            f"{name} - {entry.status_label}",
            f"{name} is owed {caregiver.total_paid} this period but their OnPay setup is "
            f"marked \"{entry.status_label}\".",
            "Finish their setup in OnPay, or pay them another way this time.",
            key, name, [j.booking_id for j in caregiver.jobs],
        ))

    for job in caregiver.jobs:
        out += _check_job(job, caregiver, rules)

    out += _check_overlaps(caregiver)

    if caregiver.dt_hours > 0:
        out.append(Finding(
            "double_time", REVIEW,
            f"{name} worked double time",
            f"{caregiver.dt_hours} hours past {rules.daily_dt_threshold} in a day, which is "
            f"paid at double time. That is {caregiver.dt_premium} on top of normal pay.",
            "Check the long day is real before paying it.",
            key, name, _dt_bookings(caregiver),
        ))
    if caregiver.uses_multiple_rates and (caregiver.ot_hours > 0 or caregiver.dt_hours > 0):
        rates = ", ".join(f"${t.rate:.2f}" for t in caregiver.tiers if t.hours > 0)
        week = next((w for w in caregiver.weeks if w.ot_hours or w.dt_hours), None)
        out.append(Finding(
            "mixed_rate_overtime", REVIEW,
            f"{name} has overtime across two different rates",
            f"{name} worked at {rates} this period, so their overtime is based on a blended "
            f"rate of ${week.regular_rate:.4f} an hour, not on either rate on its own. "
            "OnPay will not work this out correctly on its own.",
            "Enter the overtime in OnPay as a dollar amount, not as hours. The card shows the figure.",
            key, name, _ot_bookings(caregiver) + _dt_bookings(caregiver),
        ))

    if rules.warn_bonus_with_overtime and caregiver.bonus > 0 and (
            caregiver.ot_hours > 0 or caregiver.dt_hours > 0):
        out.append(Finding(
            "bonus_with_overtime", REVIEW,
            f"{name} has both a bonus and overtime",
            f"{name} received {caregiver.bonus} in bonuses and worked overtime in the same "
            "period. If those bonuses are earned rather than a gift, California generally "
            "requires them to raise the overtime rate. The app has not done that.",
            "Decide whether these bonuses are discretionary. If not, add the extra as a manual adjustment.",
            key, name, [j.booking_id for j in caregiver.jobs if j.bonus or j.lifesaver_bonus],
        ))

    for week in caregiver.weeks:
        if week.crossed_disabled_weekly_threshold:
            out.append(Finding(
                "weekly_threshold_not_paid", REVIEW,
                f"{name} worked {week.hours_worked} hours in one week",
                f"That is over the {rules.weekly_ot_threshold}-hour weekly overtime threshold, "
                "but weekly overtime is switched off in Settings, so no extra is being paid "
                "for it beyond the daily overtime already included.",
                "If weekly overtime should apply, turn it on in Settings and re-check.",
                key, name, [b for d in week.days for b in d.booking_ids],
            ))

    if caregiver.adjustments:
        out.append(Finding(
            "manual_adjustment", NOTE,
            f"{name} has {len(caregiver.adjustments)} manual adjustment"
            f"{'s' if len(caregiver.adjustments) != 1 else ''}",
            "; ".join(
                f"{a.kind} changed from {a.original_value} to {a.new_value}"
                f" ({a.reason or 'no reason given'})" for a in caregiver.adjustments),
            "The original imported values are kept and shown on the card.",
            key, name, [a.booking_id for a in caregiver.adjustments if a.booking_id],
        ))
    return out


def _dt_bookings(caregiver: CaregiverPayroll) -> list[str]:
    return [b for w in caregiver.weeks for d in w.days if d.dt_hours > 0 for b in d.booking_ids]


def _ot_bookings(caregiver: CaregiverPayroll) -> list[str]:
    return [b for w in caregiver.weeks for d in w.days if d.ot_hours > 0 for b in d.booking_ids]


def _check_job(job: Job, caregiver: CaregiverPayroll, rules: Rules) -> list[Finding]:
    out = []
    name, key = caregiver.name, caregiver.key
    long_shift = Decimal(str(rules.v("suspiciously_long_shift_hours", 12)))
    short_shift = Decimal(str(rules.v("suspiciously_short_shift_hours", 0.5)))

    if job.hours_worked <= 0:
        out.append(Finding(
            "no_hours", STOP,
            f"{name}'s job on {_when(job)} has no hours",
            f"Booking {job.booking_id} starts and ends at times that give it "
            f"{job.hours_worked} hours, so there is nothing to pay.",
            "Fix the times in Sitterwise, or correct the hours here as a manual adjustment.",
            key, name, [job.booking_id],
        ))
    elif job.hours_worked > long_shift:
        out.append(Finding(
            "long_shift", REVIEW,
            f"{name} has a {job.hours_worked}-hour shift",
            f"Booking {job.booking_id} on {_when(job)} runs from "
            f"{job.start:%-I:%M %p} to {job.end:%-I:%M %p}. That is unusually long.",
            "Check it is real. Long shifts are where double time comes from.",
            key, name, [job.booking_id],
        ))
    elif job.hours_worked < short_shift:
        out.append(Finding(
            "short_shift", REVIEW,
            f"{name} has a {job.hours_worked}-hour shift",
            f"Booking {job.booking_id} on {_when(job)} is under {short_shift} hours, "
            "which usually means the times are wrong.",
            "Check the times in Sitterwise.",
            key, name, [job.booking_id],
        ))

    if job.tier_key in ("unknown", "other") or job.rate_basis in ("unmatched", "none"):
        out.append(Finding(
            "rate_not_recognised", STOP,
            f"{name}'s job on {_when(job)} does not match a known pay rate",
            f"Booking {job.booking_id} paid {job.paid_to_caregiver} for {job.hours_worked} "
            f"hours. That is not {' or '.join('$%.2f' % t['rate'] for t in rules.tiers)} an hour, "
            "so the app cannot tell which rate was meant.",
            "Fix the pay in Sitterwise and upload again, or set the rate here as a manual adjustment.",
            key, name, [job.booking_id],
        ))

    if job.hours_exported is not None and job.hours_worked > 0:
        gap = abs(job.hours_exported - job.hours_worked)
        if gap > Decimal(str(rules.v("hours_mismatch_tolerance", 0.01))):
            out.append(Finding(
                "hours_disagree", REVIEW,
                f"{name}'s hours do not agree on {_when(job)}",
                f"Sitterwise says {job.hours_exported} hours on booking {job.booking_id}, but "
                f"the start and end times work out to {job.hours_worked}. The app used the "
                f"clock, which pays {job.straight_pay}.",
                "Check which one is right in Sitterwise.",
                key, name, [job.booking_id],
            ))

    big_tip = Decimal(str(rules.v("large_tip_warning", 200)))
    if job.tip > big_tip:
        out.append(Finding(
            "large_tip", REVIEW,
            f"{name} has a {job.tip} tip",
            f"Booking {job.booking_id} on {_when(job)} carries an unusually large tip.",
            "Just worth a second look before it goes through.",
            key, name, [job.booking_id],
        ))

    if job.mileage_rejected_reason == "service_type":
        eligible = " or ".join(sorted(rules.mileage_eligible_service_types)) or "any"
        out.append(Finding(
            "mileage_not_allowed", REVIEW,
            f"{name} may have claimed mileage on a job that does not qualify",
            f"Booking {job.booking_id} on {_when(job)} is a {job.service_type} job with a "
            f"{job.other_reimbursement} reimbursement that is an exact number of miles at the "
            f"current rate. Mileage is only paid on {eligible} jobs, so the app has NOT paid this "
            "as mileage - it is sitting in other reimbursements instead.",
            "Check what it was really for. If somebody claimed mileage on a job outside the "
            "Care.com programme, take it off before payroll goes through.",
            key, name, [job.booking_id],
        ))

    if job.mileage_rejected_reason == "under_minimum":
        out.append(Finding(
            "mileage_under_minimum", REVIEW,
            f"{name}'s mileage claim on {_when(job)} is under the {rules.minimum_miles}-mile minimum",
            f"Booking {job.booking_id} has a {job.other_reimbursement} reimbursement, which is a "
            f"trip shorter than the {rules.minimum_miles} miles a mileage claim needs. The app has "
            "not paid it as mileage.",
            "Check whether this should have been claimed at all.",
            key, name, [job.booking_id],
        ))

    if job.mileage_amount > 0 and job.sitterwise_cut > 0 and job.mileage_amount > job.sitterwise_cut:
        out.append(Finding(
            "mileage_exceeds_commission", REVIEW,
            f"{name}'s mileage on {_when(job)} is more than Sitterwise earned on the job",
            f"Booking {job.booking_id} pays {job.mileage_amount} of mileage "
            f"({job.mileage_miles} miles) but Sitterwise's cut on it is only {job.sitterwise_cut}. "
            "That job loses money.",
            "Worth checking the trip really was that far, and that the job qualified for mileage.",
            key, name, [job.booking_id],
        ))

    big_reimb = Decimal(str(rules.v("large_reimbursement_warning", 200)))
    if job.total_reimbursement > big_reimb:
        out.append(Finding(
            "large_reimbursement", REVIEW,
            f"{name} has a {job.total_reimbursement} reimbursement",
            f"Booking {job.booking_id} on {_when(job)} has an unusually large reimbursement.",
            "Check there is a receipt for it.",
            key, name, [job.booking_id],
        ))

    return out


def _check_overlaps(caregiver: CaregiverPayroll) -> list[Finding]:
    out = []
    shifts = sorted(
        [(j.start, j.end, j.booking_id) for j in caregiver.jobs if j.start and j.end])
    for (start_a, end_a, id_a), (start_b, end_b, id_b) in zip(shifts, shifts[1:]):
        if id_a == id_b:
            continue          # the same booking twice - already reported as a duplicate
        if start_b < end_a:
            out.append(Finding(
                "overlapping_shifts", STOP,
                f"{caregiver.name} is booked in two places at once",
                f"Booking {id_a} runs {start_a:%b %-d %-I:%M %p} to {end_a:%-I:%M %p}, and "
                f"booking {id_b} starts at {start_b:%-I:%M %p} before that one ends. One "
                "person cannot work both, so these hours are being counted twice.",
                "Fix the times in Sitterwise and upload again.",
                caregiver.key, caregiver.name, [id_a, id_b],
            ))
    return out


# --- things that are wrong with the data as a whole ------------------------

def _check_data_gaps(jobs: list[Job], rules: Rules) -> list[Finding]:
    """Gaps caused by Sitterwise not storing something, not by a bad booking.

    These are raised once for the whole payroll rather than on every job,
    because otherwise they would drown out the findings that need action.
    """
    out = []
    inferred = [j for j in jobs if j.rate_basis.startswith("inferred_from_pay")]
    if inferred:
        out.append(Finding(
            "tier_inferred", NOTE,
            "Pay rates were worked out from the amounts, not read from the export",
            f"Sitterwise does not export a pay rate or a number of children, so for all "
            f"{len(inferred)} paid jobs the app worked backwards from what each job paid "
            "to decide whether it was the regular or the 3-4 children rate. That means a "
            "job entered at the wrong rate in Sitterwise will look correct here.",
            "Adding a children count and a pay rate to Sitterwise would let the app check this properly.",
            booking_ids=[j.booking_id for j in inferred][:50],
        ))

    untipped = [j for j in jobs if j.tip_was_blank and j.status == "completed"]
    if untipped:
        who = sorted({j.display_name for j in untipped if j.display_name})
        out.append(Finding(
            "tips_not_recorded", REVIEW,
            f"{len(untipped)} finished jobs have no tip recorded at all",
            "In this export the tip field is only ever filled in once a job is marked paid. "
            f"These {len(untipped)} jobs are finished but not yet marked paid, so their tip "
            "field is empty rather than zero - which means a tip could be missing rather "
            f"than genuinely absent. Affects {len(who)} caregivers: "
            + ", ".join(who[:6]) + (" and others." if len(who) > 6 else "."),
            "Mark these jobs paid in Sitterwise so tips get recorded, then upload again. "
            "Any tip you know about can be added here as a manual adjustment.",
            booking_ids=[j.booking_id for j in untipped],
        ))

    undescribed = [j for j in jobs if j.other_reimbursement > 0]
    if undescribed:
        total = sum((j.other_reimbursement for j in undescribed), ZERO)
        who = sorted({j.display_name for j in undescribed if j.display_name})
        out.append(Finding(
            "reimbursements_no_description", REVIEW,
            f"{len(undescribed)} reimbursements totalling ${total} have no description",
            "These are not mileage, and Sitterwise has nowhere to record what they were "
            f"for. Affects {', '.join(who)}.",
            "Check what each one was for before paying it. Adding a description field to "
            "Sitterwise would fix this for good.",
            booking_ids=[j.booking_id for j in undescribed],
        ))

    mileage_jobs = [j for j in jobs if j.mileage_miles]
    if mileage_jobs:
        total = sum((j.mileage_amount for j in mileage_jobs), ZERO)
        out.append(Finding(
            "mileage_inferred", NOTE,
            f"{len(mileage_jobs)} reimbursements were treated as mileage",
            f"{total} of reimbursements divide into a whole number of miles at the current "
            "rate, so the app treated them as mileage. Sitterwise has no mileage field, so "
            "this is worked out from the amount.",
            "Adding miles and a mileage rate to Sitterwise would remove the guesswork.",
            booking_ids=[j.booking_id for j in mileage_jobs],
        ))
    return out


def _when(job: Job) -> str:
    return f"{job.start:%b %-d}" if job.start else "an unknown date"


# --- rolling findings up into what Amy sees --------------------------------

def caregiver_status(caregiver_key: str, findings: list[Finding]) -> str:
    mine = [f for f in findings if f.caregiver_key == caregiver_key]
    if any(f.level == STOP for f in mine):
        return "blocked"
    if any(f.level == REVIEW for f in mine):
        return "needs_review"
    return "ready"


def summarise(caregivers: list[CaregiverPayroll], findings: list[Finding]) -> dict:
    statuses = {c.key: caregiver_status(c.key, findings) for c in caregivers}
    counts = Counter(statuses.values())
    return {
        "ready": counts.get("ready", 0),
        "needs_review": counts.get("needs_review", 0),
        "blocked": counts.get("blocked", 0),
        "total": len(caregivers),
        "statuses": statuses,
        "can_finalize": counts.get("blocked", 0) == 0,
        "stop_count": sum(1 for f in findings if f.level == STOP),
        "review_count": sum(1 for f in findings if f.level == REVIEW),
        "note_count": sum(1 for f in findings if f.level == NOTE),
    }
