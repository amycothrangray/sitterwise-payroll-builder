# Sitterwise Payroll

Turns a Sitterwise bookings export into everything you need to enter payroll
into OnPay, with the arithmetic shown for every figure.

Runs on your own computer. No accounts, no cloud, nothing leaves the machine.

Sitterwise payroll runs **weekly, Monday through Sunday**. Pay stubs go out by
email through OnPay on Tuesday, and direct deposit lands on Friday.

---

## Running it

Double-click **`Start Sitterwise Payroll.command`**. It opens in your browser.
Leave the black window open while you work; close it when you're done.

The first time, it may take a moment to set itself up.

From a terminal instead:

```
python3 run.py
```

---

## How a payroll goes

**1. Upload** the export from Sitterwise. Your export is a whole month, so the
app asks which pay week you mean. Sitterwise pays **weekly, Monday to Sunday**,
so it lists those weeks with the number of jobs in each and pre-selects the
most recent complete one. Half-months are offered underneath in case you ever
need to run one.

**2. Payroll check.** Three numbers, in plain English:

```
41 caregivers are ready
 9 need a look from you
 0 can't be paid yet
```

Click any of them, or any warning, to go straight to the problem.

**3. Caregiver cards.** Collapsed by default. Open one and every figure can be
expanded down to the bookings it came from — including which days caused
overtime and how the blended rate was worked out.

**4. Enter in OnPay.** Two ways:

- the whole grid, one row per caregiver, in the categories OnPay wants
- **one caregiver at a time**, with huge numbers, copy buttons, and a
  "mark entered" button that moves you to the next person

Put OnPay in one window and this in the other.

**5. Check it adds up.** Every job in the period is either paid or explained,
and the app's figures are compared against Sitterwise's own.

**6. Finish.** The payroll locks. Those jobs can never be paid again in a
later run unless you deliberately unlock it.

---

## What it works out for you

- **Rates** — $23 regular, $28 for 3–4 children. The export carries neither a
  rate nor a children count, so the app works the tier out from what each job
  paid, and says so.
- **The four-hour minimum** — a 2.5-hour job paid for 4 hours shows the
  top-up as its own line. Those extra hours are paid but not worked, so they
  don't trigger overtime.
- **California overtime** — time and a half over 8 hours a day, double time
  over 12, seventh-consecutive-day rules. Weekly overtime is off but still
  warned about; across all four real August pay weeks it would have added
  nothing, because daily overtime already covers everyone who passed 40 hours.
- **Two rates in one week** — overtime uses the weighted average, with the
  sum shown on the card.
- **Tips** — kept out of wages and out of the overtime rate. Their own OnPay
  category.
- **Mileage** — Care.com jobs only, and only the miles **above 40** on a round
  trip. A mileage-shaped amount on any other job is paid as an ordinary
  reimbursement and flagged, as is anything over 50 miles that needed advance
  approval, and anything that looks like the whole drive was paid rather than
  the part policy covers.
- **Reimbursements** — never taxable, never mixed with wages.

---

## Changing the rules

Everything lives in **`rules.json`**, and most of it has a box on the
**Settings** screen. Rates, the minimum, overtime thresholds, mileage rates by
date, and which booking statuses get paid.

Every finished payroll stores the rules it was run with, so reopening July
shows July's rules rather than today's.

To switch to personal-attendant treatment (9 hours a day, 45 a week, no double
time), change three settings. No code changes. There are tests for it.

---

## Correcting something

Open a caregiver's card and click **Correct something**. Hours, rate, tip,
mileage, reimbursement, or a one-off amount.

Every change keeps the original value, the new value, your reason and the
timestamp, and is marked **Manual adjustment** wherever the number appears.
Imported bookings are never altered.

---

## Payroll notes

The **Notes** screen replaces the "Payroll Odds & Ends" sheet. When somebody
notices something during the week — a bonus owed, a caregiver who never
checked out, a Trustline fee to pay back — they write it down there and then.

The difference from the sheet is that a note carries its own numbers. When the
payroll it belongs to is run, the check screen says how many notes are waiting
and one button adds them. Each becomes an ordinary adjustment carrying the
note's own words, so it shows on the caregiver's card and in the audit trail
like every other manual change. Nothing is ever added silently.

Notes are typed, because most of what went in the sheet was:

| Kind | What it does |
|---|---|
| Bonus, cancellation pay, extra pay | Taxable, paid alongside the work |
| Reduce pay | Taxable, comes off |
| Reimbursement, mileage | Not taxable, and kept out of the overtime rate |
| Hours or rate correction | Corrects one booking — needs its booking number |
| Paid another way, paper check, plain note | Shown to you, never acted on by the app |

A note only applies itself when it says enough to be applied safely. One with
no caregiver on it, no amount, or an hours correction with no booking number is
listed with the reason it is waiting, rather than guessed at.

---

## Recurring and non-booking pay

Some people are paid for work that never appears as a booking: a monthly
salary, admin hours, phone days, training. Set them up in **Settings →
Recurring and non-booking pay** and each gets their own payroll line on the
payrolls they are due, with no hours and no overtime behind it.

Payroll runs weekly, Monday to Sunday, so **monthly** means the one payroll
whose week contains the first Monday of that month. That is exactly twelve
payments a year, and a week straddling a month end never pays twice.

Somebody who also worked bookings that week gets it folded into their own
payroll, so they receive one payment rather than two.

---

## The roster

Records whether each caregiver is actually set up in OnPay. Caregivers the app
adds itself are marked for review, not blocked — it doesn't know yet.
Marking someone **Not in OnPay** is a deliberate act and does block payroll.

Rather than keeping the same information in two places, export your employee
list out of OnPay and import it on the Roster screen.

**When OnPay knows somebody by another name.** OnPay holds people under their
legal name, which is often not the name Sitterwise shows — Lissa's OnPay
record is Elisabeth R Gray, and married names, preferred names and middle
initials all do the same thing. The roster keeps a **Name in OnPay** for these,
and the import matches on Clock User, employee id, or a legal name already
recorded, before it falls back to the name. Without that, importing OnPay's
list would add a second entry for the same person and then report the first as
missing from OnPay.

---

## Exports

Payroll detail · OnPay entry sheet · Payroll summary · Things needing
attention · Caregiver detail · OnPay import file.

### The OnPay import file

OnPay switched CSV upload on for this account on 4 September 2026 and sent the
specification. The file is **one row per pay item**, not one per person, with
eight fixed columns and numeric pay types. Pay types used: 1 Regular,
2 Overtime, 22 Double Overtime, 7 Bonus, 107 Reimbursement, 208 Controlled
Tips. All of that lives in `onpay_mapping.json`.

**One OnPay rule shapes the whole file:** an employee may appear only once on
pay item 1 and once on pay item 2. So a caregiver who worked two rates in a
week cannot have both on pay item 1, and the file is written two ways:

*One rate* — the ordinary presentation.

```
Regular    12.00h @ $23.00
Overtime    2.00h @ $34.50
```

*Two rates* — each rate keeps its own row at the rate actually worked, and the
overtime row carries only the premium, because the straight time is already
above it.

```
3-4 Children   5.00h @ $28.00     (a Custom pay type in OnPay)
Regular        5.00h @ $23.00
Overtime       2.00h @ $12.75     (half the $25.50 weighted regular rate)
```

Both come to the same money. The second just doesn't put a blended rate on the
wage statement in place of the rates the caregiver actually worked.

Salary is pay item 1 with a cash amount and no hours, the way OnPay's own
template writes it. The four-hour minimum rides in the regular row — guarantee
pay is always the guarantee hours at that tier's rate, so it comes out exact.

**Two things never reach the file.** Anyone the payroll check has stopped, and
anyone with no Clock User. Both are named on screen for entering by hand. The
app also adds up what OnPay will actually pay from the file and compares it
with what it worked out itself, so a rounding difference is something you see
rather than something you find later.

**The higher rate tier has its own OnPay pay item:** id **119**, which OnPay
shows as "Custom 4" and which is renamed "3-4 Children" on this account.

Beware that OnPay's pay items are identified by an internal id, and those ids
are *not* the numbers in the "Custom N" names — "Custom 1" is id 4 and
"Custom 4" is id 119. Check the id, not the name. The app checks the mapping
before it writes a file and says so if two tiers share an item, if the
standard rate has moved off item 1, or if a tier has landed on a flat-money
item.

---

## Checking it yourself

- **`docs/TESTS.md`** — every calculation worked out by hand, as a sum you can
  check without reading any code.
- **`docs/SITTERWISE-CHANGES.md`** — what to change in Sitterwise so the app
  stops having to guess. Written to hand to a developer.

```
python3 -m unittest discover -s tests -t .
```

76 tests. To run the regression tests against a real export, drop one in
`tests/fixtures/real/` — that folder is kept out of git because real exports
contain client names and phone numbers.

---

## Where things are

```
rules.json           every payroll rule
onpay_mapping.json   the OnPay import column layout
payroll/             the code
  money.py           decimal arithmetic, never floating point
  rules.py           reading and checking the rules file
  importer.py        reading a Sitterwise export
  engine.py          hours, rates, overtime, the regular rate
  validate.py        the payroll check
  extras.py          payroll notes, and pay that is not from a booking
  run.py             putting a payroll together, and reconciling it
  store.py           history, roster, adjustments, audit trail
  exports.py         the CSVs
  server.py          the local web server
web/                 the interface
data/                your payroll history (not in git)
```

---

## How overtime is set

**California 8/40 with double time** — decided 22 August 2026. Time and a half
over 8 hours in a day and 40 in a week, double time over 12 hours in a day,
and seventh-consecutive-day rules.

Weekly overtime is switched off. Because Sitterwise pays Monday-to-Sunday
weeks, daily overtime already covers everyone: across all four real August pay
weeks, turning weekly overtime on would have added nothing. Anyone who does
cross 40 hours still gets flagged, so it can never go unnoticed.

The alternative treatment — personal attendants under the Domestic Worker Bill
of Rights, at 9 hours a day and 45 a week with no double time — is not being
used. Every threshold is a setting, and there are tests proving the switch
works, so it can be revisited without rewriting anything.

## One thing still open

**The pay rate is not in the export.** Sitterwise sends the amount each job
paid, but not the rate it was paid at, so the app works the tier out by
dividing pay by hours. It resolves every real job cleanly, but it is
arithmetic standing in for a fact Sitterwise already knows.

Exporting `pay_rate` and `pay_tier` removes the guesswork entirely. See
`docs/SITTERWISE-CHANGES.md`.
