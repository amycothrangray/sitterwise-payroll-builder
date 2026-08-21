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
- **Mileage** — Care.com jobs only, 40 miles or more. A mileage-shaped amount
  on any other job is paid as an ordinary reimbursement and flagged.
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

## The roster

Records whether each caregiver is actually set up in OnPay. Caregivers the app
adds itself are marked for review, not blocked — it doesn't know yet.
Marking someone **Not in OnPay** is a deliberate act and does block payroll.

Rather than keeping the same information in two places, export your employee
list out of OnPay and import it on the Roster screen.

---

## Exports

Payroll detail · OnPay entry sheet · Payroll summary · Things needing
attention · Caregiver detail · OnPay import file.

The OnPay import file's columns come from `onpay_mapping.json`. OnPay doesn't
publish its CSV format and has to switch the import on for your account — ask
their support for both, then set the column names in Settings.

---

## Checking it yourself

- **`docs/TESTS.md`** — every calculation worked out by hand, as a sum you can
  check without reading any code.
- **`docs/SITTERWISE-CHANGES.md`** — what to change in Sitterwise so the app
  stops having to guess. Written to hand to a developer.

```
python3 -m unittest discover -s tests -t .
```

71 tests. To run the regression tests against a real export, drop one in
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
  run.py             putting a payroll together, and reconciling it
  store.py           history, roster, adjustments, audit trail
  exports.py         the CSVs
  server.py          the local web server
web/                 the interface
data/                your payroll history (not in git)
```

---

## Two things still open

**The overtime rules.** The app uses California's standard 8/40 rules with
double time. Whether these caregivers are instead "personal attendants" under
the Domestic Worker Bill of Rights (9/45, no double time) is a question for an
employment attorney — and it's complicated by the fact that a large share of
Sitterwise jobs happen in hotels and corporate venues rather than private
homes. The thresholds are all settings, so the answer can be applied without
rewriting anything.

**Children count.** Until Sitterwise records how many children were on a job,
the app can reproduce a wrong rate but can never detect one. This is the
single most valuable thing to add. See `docs/SITTERWISE-CHANGES.md`.
