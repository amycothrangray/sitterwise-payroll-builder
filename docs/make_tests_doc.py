#!/usr/bin/env python3
"""Write docs/TESTS.md from the actual test payroll.

Generated rather than typed, so the documented figures are always the figures
the app produces.

    python3 docs/make_tests_doc.py > docs/TESTS.md
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from payroll.importer import import_export                       # noqa: E402
from payroll.roster import NOT_IN_ONPAY, READY, RosterEntry      # noqa: E402
from payroll.rules import Rules                                  # noqa: E402
from payroll.run import build_run                                # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "test-payroll.xlsx"

SCENARIOS = {
    'Rosa Delgado': 'Only regular-rate jobs',
    'Ivy Chen': 'Only 3-4 children jobs',
    'Mona Patel': 'Both rates, no overtime',
    'Dana Reyes': 'Crosses daily overtime at one rate',
    'Tess Okafor': 'Overtime while working both rates',
    'Priya Raman': 'Double time past 12 hours',
    'Ruth Ozeki': 'Seventh consecutive day',
    'Cass Moreau': 'Over 40 hours, weekly overtime switched off',
    'Lena Voss': 'A bonus alongside overtime',
    'Belle Cruz': 'The four-hour minimum',
    'Nina Alvarez': 'A tip',
    'Gwen Mabry': 'Mileage on a Care.com job',
    'Sofia Bright': 'A reimbursement that is not mileage',
    'Hana Kimura': 'Both a tip and mileage',
    'Faye Nakamura': 'Mileage claimed on a job that does not qualify',
    'Nadia Okoro': 'A Care.com claim under the 40-mile minimum',
    'Della Cruz': 'Mileage larger than the commission on the job',
    'Cleo Barnes': 'The same booking twice',
    'Ada Whitlow': 'Two shifts at the same time',
    'June Salter': 'Pay that matches no known rate',
    'Opal Grant': 'Not set up in OnPay',
    'Vera Lund': 'Corrected by hand (see below)',
}

HEAD = """# How to check the app's arithmetic

Every calculation the app makes, worked out by hand, against the test payroll
in `tests/fixtures/test-payroll.xlsx`. That file has the same columns
Sitterwise produces and contains every combination worth testing.

Regenerate this document with:

    python3 docs/make_tests_doc.py > docs/TESTS.md

Run the tests with:

    python3 -m unittest discover -s tests -t .

The rules in force below are the ones in `rules.json`: $23 and $28 an hour, a
four-hour booking minimum, overtime after 8 hours a day, double time after 12,
seventh-consecutive-day rules on, weekly overtime off, and mileage on Care.com
jobs of 40 miles or more at the IRS rate for the date of the job.

Sitterwise pays weekly, Monday to Sunday. The test payroll below deliberately
spans two of those weeks so the weekly grouping gets exercised.

---
"""

TAIL = """## Corrected by hand

Vera Lund is paid $115.00 - 5 hours at $23. Suppose the family confirms a $40
cash tip that never made it into Sitterwise.

Adding it as a manual adjustment:

| | |
|---|---:|
| Regular: 5.00 hrs x $23.00 | $115.00 |
| Tips *(manual adjustment)* | $40.00 |
| **Total being paid** | **$155.00** |

The app records all of this and none of it is hidden:

- the original value (`0.00`) and the new value (`40.00`)
- the reason typed in at the time
- the timestamp
- a **Manual adjustment** marker on her card, on the OnPay grid and in exports
- a line in the audit trail on the History screen

The imported job is never altered. Remove the adjustment and the figure goes
straight back to $115.00.

---

## The same payroll under personal-attendant rules

If your employment attorney confirms these caregivers are personal attendants
under the Domestic Worker Bill of Rights, change three things in Settings -
daily overtime to 9 hours, weekly to 45, double time off - and nothing else.
No code changes.

What moves:

| | 8/40 with double time | 9/45, no double time |
|---|---|---|
| Dana Reyes, 10-hour day | 2 hrs overtime, $23.00 premium | 1 hr overtime, $11.50 premium |
| Dana Reyes total | $345.00 | $333.50 |
| Priya Raman, 13-hour day | 4 hrs OT + 1 hr DT, $69.00 | 4 hrs OT, $46.00 |
| Priya Raman total | $368.00 | $345.00 |

What does not move: the rates, the four-hour minimum, tips, mileage and
reimbursements. There are tests for that too.

---

## On the real August export

Ten further tests run against a real Sitterwise export when one is available
(put it in `tests/fixtures/real/`, which git ignores, or point
`SITTERWISE_EXPORT` at it). They check that all 324 rows read without error,
that every paid job matches a known rate, that the payroll balances, that the
four-hour minimum is recognised, that mileage only ever lands on Care.com jobs
of 40 miles or more, that the pay week really does run Monday to Sunday, that
the app suggests the right week, that weekly overtime would add nothing, and
that these totals for the real pay week of **Mon 10 - Sun 16 August** have not
moved:

| | |
|---|---:|
| Caregivers | 29 |
| Jobs | 59 |
| Hours worked | 320.75 |
| Regular: 283.75 hrs at $23 | $6,526.25 |
| 3-4 children: 37.00 hrs at $28 | $1,036.00 |
| Four-hour minimum top-up | $23.00 |
| Overtime | 14.75 hrs, $182.06 premium |
| Tips | $145.00 |
| Bonuses | $60.00 |
| Mileage: 108 miles | $82.08 |
| Other reimbursements | $114.00 |
| **Taxable earnings** | **$7,972.31** |
| **Reimbursements** | **$196.08** |
| **Total being paid** | **$8,168.39** |

If a change moves one of those numbers, that should be a decision somebody
made - not a surprise.
"""


def main() -> None:
    rules = Rules.load()
    result = import_export(FIXTURE, rules)
    roster = {
        j.caregiver_key: RosterEntry(
            j.caregiver_key, j.display_name,
            NOT_IN_ONPAY if j.display_name == 'Opal Grant' else READY,
            onpay_clock_user=f"SW{j.booking_id}", source='onpay_import')
        for j in result.jobs if j.caregiver_key}
    run = build_run(FIXTURE, rules, date(2026, 8, 1), date(2026, 8, 15),
                    roster=roster, import_result=result)

    out = print
    out(HEAD)
    totals, summary, recon = run.totals(), run.summary, run.reconciliation

    out("## The payroll as a whole\n")
    out("| | |\n|---|---|")
    out(f"| Caregivers | {totals['caregivers']} |")
    out(f"| Jobs paid | {totals['jobs']} |")
    for tier in totals['tiers']:
        out(f"| {tier['label']} hours | {tier['hours']} at ${float(tier['rate']):.2f} "
            f"= ${tier['pay']} |")
    out(f"| Minimum-guarantee hours | {totals['guarantee_hours']} = ${totals['guarantee_pay']} |")
    out(f"| Overtime | {totals['ot_hours']} hrs, ${totals['ot_premium']} premium |")
    out(f"| Double time | {totals['dt_hours']} hrs, ${totals['dt_premium']} premium |")
    out(f"| Tips | ${totals['tips']} |")
    out(f"| Bonuses | ${totals['bonus']} |")
    out(f"| Mileage | {totals['mileage_miles']} miles = ${totals['mileage_amount']} |")
    out(f"| Other reimbursements | ${totals['other_reimbursement']} |")
    out(f"| **Taxable earnings** | **${totals['taxable_earnings']}** |")
    out(f"| **Reimbursements** | **${totals['reimbursements']}** |")
    out(f"| **Total being paid** | **${totals['total_paid']}** |")
    out(f"\nPayroll check: **{summary['ready']} ready, {summary['needs_review']} needing a "
        f"look, {summary['blocked']} that cannot be paid**.\n")
    left_out = " ".join(f"{count} left out ({why.lower()})."
                        for why, count in recon.exclusions.items())
    out(f"Reconciliation: {recon.jobs_in_period} jobs dated in this period, "
        f"{recon.jobs_paid} paid, {recon.jobs_accounted_for} accounted for. {left_out} "
        f"Balances: **{'yes' if recon.balances else 'NO'}**.\n")
    out("---\n")

    for name, scenario in SCENARIOS.items():
        caregiver = next((c for c in run.caregivers if c.name == name), None)
        if not caregiver:
            continue
        out(f"## {name}\n\n*{scenario}*\n")
        out("**The jobs:**\n")
        out("| Date | Booking | Hours | Rate | Straight pay | |")
        out("|---|---|---:|---:|---:|---|")
        for job in sorted(caregiver.jobs, key=lambda j: (j.workday or date.min, j.booking_id)):
            extra = []
            if job.minimum_applied:
                extra.append(f"paid for {job.hours_paid}, 4-hr minimum")
            if float(job.tip):
                extra.append(f"tip ${job.tip}")
            if job.mileage_miles:
                extra.append(f"{job.mileage_miles} mi = ${job.mileage_amount}")
            if float(job.other_reimbursement):
                extra.append(f"reimbursement ${job.other_reimbursement}")
            bonus = float(job.bonus) + float(job.lifesaver_bonus)
            if bonus:
                extra.append(f"bonus ${bonus:.2f}")
            rate = f"${float(job.rate):.2f}" if job.tier_key != 'unknown' else "not recognised"
            out(f"| {job.workday} | {job.booking_id} | {job.hours_worked} | {rate} | "
                f"${job.straight_pay} | {'; '.join(extra)} |")

        if any(w.ot_hours or w.dt_hours for w in caregiver.weeks):
            out("\n**The overtime:**\n")
            for week in caregiver.weeks:
                if not (week.ot_hours or week.dt_hours):
                    continue
                out(f"Week beginning {week.week_start}. {week.regular_rate_explanation}\n")
                for day in week.days:
                    if day.ot_hours or day.dt_hours:
                        out(f"- **{day.day}** - {day.explanation}")
                if week.ot_hours:
                    out(f"- Overtime premium: {week.ot_hours} x "
                        f"{float(rules.daily_ot_multiplier) - 1} x "
                        f"${float(week.regular_rate):.4f} = **${week.ot_premium}**")
                if week.dt_hours:
                    out(f"- Double time premium: {week.dt_hours} x "
                        f"{float(rules.daily_dt_multiplier) - 1} x "
                        f"${float(week.regular_rate):.4f} = **${week.dt_premium}**")
                out("")

        out("\n**What they are paid:**\n")
        out("| | |\n|---|---:|")
        for tier in caregiver.tiers:
            if float(tier.hours):
                out(f"| {tier.label}: {tier.hours} hrs x ${float(tier.rate):.2f} | ${tier.pay} |")
        if float(caregiver.guarantee_hours):
            out(f"| Four-hour minimum top-up: {caregiver.guarantee_hours} hrs | "
                f"${caregiver.guarantee_pay} |")
        if float(caregiver.ot_hours):
            out(f"| Overtime premium: {caregiver.ot_hours} hrs | ${caregiver.ot_premium} |")
        if float(caregiver.dt_hours):
            out(f"| Double time premium: {caregiver.dt_hours} hrs | ${caregiver.dt_premium} |")
        if float(caregiver.tips):
            out(f"| Tips | ${caregiver.tips} |")
        if float(caregiver.bonus):
            out(f"| Bonuses | ${caregiver.bonus} |")
        out(f"| **Taxable earnings** | **${caregiver.taxable_earnings}** |")
        if float(caregiver.mileage_amount):
            out(f"| Mileage: {caregiver.mileage_miles} miles | ${caregiver.mileage_amount} |")
        if float(caregiver.other_reimbursement):
            out(f"| Other reimbursement (not taxable) | ${caregiver.other_reimbursement} |")
        out(f"| **Total being paid** | **${caregiver.total_paid}** |")

        mine = [f for f in run.findings if f.caregiver_key == caregiver.key]
        if mine:
            out("\n**What the payroll check says:**\n")
            for finding in mine:
                mark = {'stop': 'CANNOT BE PAID', 'review': 'Needs a look',
                        'note': 'Note'}[finding.level]
                out(f"- **{mark}** - {finding.title}. {finding.detail}")
        out("\n---\n")

    out(TAIL)


if __name__ == "__main__":
    main()
