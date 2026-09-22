# Sitterwise Payroll

Turns a Sitterwise bookings export into everything you need to enter payroll
into OnPay, with the arithmetic shown for every figure.

Runs on your own computer. Payroll data stays local unless you choose to upload it to OnPay or share the private PDF folder and task with Claude Cowork.

Sitterwise payroll runs **weekly, Monday through Sunday**. Pay stubs go out by
email through OnPay on Tuesday, and direct deposit lands on Friday.

---

## Running it

For a downloadable Mac app with its own Python runtime and a private history
transfer, see [Standalone Mac distribution](docs/MAC-DISTRIBUTION.md).
The source-folder launcher described below remains available for development.

Double-click **`Start Sitterwise Payroll.command`**. It opens in your browser.
Leave the black window open while you work; close it when you're done.

The first time, it may take a moment to set itself up.

From a terminal instead:

```
python3 run.py
```

### Making it a proper Mac application

```
python3 make_mac_app.py
```

**Keep this folder out of Documents, Desktop and Downloads.** macOS guards
those: Terminal has your permission to read them, a newly built application
does not, and payroll starts and stops again with nothing to show for it. The
home folder is fine — `~/sitterwise-payroll-builder`. The builder checks and
tells you rather than letting you find out.

Builds **Sitterwise Payroll.app** next to this file, with its own icon. Drag
it to the Dock or into Applications and payroll starts from there — no
Terminal window, no command to remember. Quitting the application stops it.

The .app is a small wrapper that starts `run.py`; nothing is copied, so it
keeps working as this folder is updated. If the folder is ever moved, the
application says so — run `make_mac_app.py` again to point it at the new
place.

---

## How a payroll goes

1. **Upload bookings** and choose the Monday–Sunday pay week.
2. **Download OnPay CSV**, import it once in OnPay, and compare the people,
   hours, and total shown in the app.
3. **Share caregiver breakdowns.** Download and unzip the caregiver PDFs. Give
   the folder and **Copy task for Claude Cowork** text to Cowork with browser
   access to your signed-in OnPay session. It verifies pay, uploads each person’s
   PDF to their employee Files, enables Employee Viewable, and adds a short
   paycheck note pointing to **OnPay → Menu → My Files**.
4. Review and submit payroll in OnPay. **CalSavers** is directly below the PDF
   step: upload the OnPay Payroll Register PDF and enter the verified amounts.
   Then **Mark this week finished** in the app to prevent duplicate pay.

Use fresh exports that include recently paid bookings. A late tip on one of
those bookings is added to the next payroll's Tips item and caregiver PDF,
without paying the old wages again. Only the increase above tips already paid
is included. After the OnPay CSV is downloaded, that payroll's late tips are
fixed; further increases wait for the next payroll. At a month boundary, upload
both months. Tips on older bookings require an updated export of that month.

The main navigation is New payroll, This payroll, History, and More. Settings,
roster connections, detailed checks, manual entry, and reports are under More.
Only actionable checks appear in the everyday workflow; an unknown local
roster status no longer means someone is absent from OnPay. An employee list
without Clock Users can confirm presence without falsely reporting incomplete setup.

A download that would omit anyone is blocked until their issue is resolved.
Scheduled pay needs a verified OnPay Clock User too. If an employee has both
booking wages and recurring pay on Regular, the duplicate pay-item check
stops the import so a verified separate mapping can be configured first.

[Weekly guide and setup](docs/WEEKLY-PAYROLL.md)

## What it works out for you

- **Rates** — $23 regular, $28 for 3–4 children. Use the exported pay rate;
  infer it from booking pay only when the rate is missing.
- **The four-hour minimum** — a 2.5-hour job paid for 4 hours shows the
  top-up as its own line. Those extra hours are paid but not worked, so they
  don't trigger overtime.
- **California overtime** — time and a half over 8 hours a day, double time
  over 12, seventh-consecutive-day rules. Weekly overtime is off but still
  warned about; across all four real August pay weeks it would have added
  nothing, because daily overtime already covers everyone who passed 40 hours.
- **Two rates in one week** — overtime and double time follow the rate of
  the job being worked. The weekly weighted rate is used whenever it is
  higher. For example, 1.5 overtime hours on a $28 job receive a $21 premium
  in addition to the $42 already included in hourly wages. Start times decide
  which job crosses each threshold; CSV row order does not.
- **Lifesaver bonuses** — flat-sum incentive overtime is added automatically.
  Bookings with both bonus columns populated require clarification before export.
- **Tips** — kept out of wages and out of the overtime rate. Their own OnPay
  category.
- **Mileage** — use the exported reimbursement amount. Distance, mileage
  rates, and approval metadata do not change it or create review warnings.
- **Reimbursements** — never taxable, never mixed with wages.

---

## Changing the rules

Your settings live in **`data/rules.json`**, and most of them have a box on the
**Settings** screen. Rates, the minimum, overtime thresholds, mileage rates by
date, and which booking statuses get paid.

Every finished payroll stores the rules it was run with, so reopening July
shows July's rules rather than today's.

The repository's `rules.json` supplies defaults for a new installation.
Updating the code preserves existing settings. The September job-rate policy
uses `overtime.regular_rate_method: job_rate_with_weighted_floor`; older saved
payrolls retain `weighted_average` and their original amounts. A private
history transfer carries the owner's current settings to the replacement Mac.

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
legal name, which is often not the name Sitterwise shows — married names, preferred names and middle
initials all do the same thing. The roster keeps a **Name in OnPay** for these,
and the import matches on Clock User, employee id, or a legal name already
recorded, before it falls back to the name. Without that, importing OnPay's
list would add a second entry for the same person and then report the first as
missing from OnPay.

---

## Exports

Payroll detail · OnPay entry sheet · Payroll summary · Things needing
attention · Caregiver detail · OnPay lines and notes · OnPay import file.

### The OnPay import file

OnPay switched CSV upload on for this account on 4 September 2026 and sent the
specification. The file is **one row per pay item**, not one per person, with
eight fixed columns and numeric pay types. Pay types used: 1 Regular, 119 3-4 Children, 17 Overtime Premium,
121 Double Time Premium, 7 Bonus, 107 Reimbursement, 208 Controlled Tips. All of that lives in `onpay_mapping.json`.

**Every paid hour stays at its original rate.** The standard tier uses item 1
and the higher tier uses item 119. Overtime and double-time premiums are
separate cash amounts, for both single-rate and mixed-rate caregivers:

```
Regular              5.00h @ $23.00
3-4 Children         5.00h @ $28.00
Overtime Premium                   $25.50
```

The premium is already calculated by this app. Items 2 and 22 are never used:
OnPay recalculates those items, which can change the amount paid.

**Required OnPay setup:** Overtime Premium is custom item **17** and Double
Time Premium is custom item **121**. Both must be **Non-Hourly**. Their CSV
rows have `treat_as_cash=1`, a `cash_amount`, and blank `hours` and `rate`.
Hourly rate rows keep `treat_as_cash` blank. Item 4 is Prior Pay Adjustment;
do not rename it or use it for double time.

The Exports screen shows the people, paid hours and total actually in the
upload, plus the required premium-item setup. Compare these totals with
OnPay after importing. An empty file, conflicting pay-item mapping, shared
Clock User or inconsistent total blocks the download. If a duplicate run
has everybody marked already paid, reopen the original run from History.

The last CSV column retains the configured overtime hours. Whether OnPay
uses that column on custom items for OBBB reporting is still unconfirmed;
matching the pay total alone does not verify that reporting.

Salary is pay item 1 with a cash amount and no hours, the way OnPay's own
template writes it. The four-hour minimum rides in the regular row — guarantee
pay is always the guarantee hours at that tier's rate, so it comes out exact.

### Caregiver PDFs and the short paycheck note

The app makes one Sitterwise-branded PDF per caregiver, showing jobs, hours,
rates, minimum-pay top-ups, overtime, tips, bonuses, reimbursements and scheduled
pay. It uses the same calculated figures as the CSV; historical runs keep their
saved rules. PDFs are generated locally and contain only that caregiver’s data.

Cowork’s task verifies identities and totals, uploads each individual file under
**Workers → employee → HR → Files**, enables **Employee Viewable**, and checks the
saved document. It then adds: “Your payroll breakdown is in OnPay > Menu > My
Files.” Existing different documents and memos are preserved for review. Payroll
remains unsubmitted for the operator. Never put private breakdowns in Company
Documents, a shared all-workers folder, or another worker’s profile.

If pay changes, download the folder and task again. Amounts changed only in
OnPay also need to be reflected in the app to appear in these breakdowns.
See [the weekly guide](docs/WEEKLY-PAYROLL.md).

### Legacy manual line-note helper — `onpay_notes.py`

This optional helper remains for older workflows. The main workflow uses PDFs
and the short My Files memo instead.
There is no way to send them automatically: OnPay's import file has no column
for a note, and OnPay has no API that writes payroll. They are typed in.

So after uploading the import file, run:

```
python3 onpay_notes.py
```

It takes the latest payroll and walks through every note one at a time, each
one already on the clipboard — click the note box on that line in OnPay,
paste, press return, next. It remembers where it got to, so stopping halfway
costs nothing; `--restart` goes back to the beginning and `--list` just prints
them all.

It reads the payroll and writes to the clipboard. It cannot change an amount
and it cannot run payroll — every note is pasted by the person running it.

That screen also shows each line's hours and rate exactly as the import file
writes them, so entering somebody by hand comes to the same money. Regular
includes all nine hours for a caregiver with one hour of overtime; the extra
half-rate premium is a separate cash amount, so the hours are counted once.

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
onpay_notes.py       walks the OnPay line notes onto the clipboard
make_mac_app.py      builds the Mac application and its icon
onpay_mapping.json   the OnPay import column layout
payroll/             the code
  money.py           decimal arithmetic, never floating point
  rules.py           reading and checking the rules file
  importer.py        reading a Sitterwise export
  engine.py          hours, rates, overtime, the regular rate
  validate.py        the payroll check
  combine.py         joining two months for a pay week that crosses one
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
