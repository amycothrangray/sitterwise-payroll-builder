# What to change in Sitterwise

Everything the payroll app currently has to guess at, and what would stop it
guessing. Written to be handed to Aji.

The short version: **payroll is being reconstructed from a single dollar
figure per job.** Sitterwise exports what each job paid, but not the rate, the
number of children, the miles, or what a reimbursement was for. The app works
backwards from the money, which works — but it means a job entered at the
wrong rate looks perfectly correct.

Fixing the first four items below turns the app from *reproducing* payroll
into *checking* it.

---

## Must have

### 1. `caregiver_id`

Right now the only thing identifying a caregiver is their display name.
One typo, one married name, one extra space, and payroll silently splits one
person into two — or fails to match them to OnPay.

```
caregiver_id            stable, immutable, never reused
onpay_clock_user        the matching id in OnPay (see note at the end)
```

### 2. `children_count`

**This is the most important one.** The $23 vs $28 decision rests entirely on
how many children were on the job, and that number is nowhere in the export.
The app infers the tier by dividing pay by hours — so it can reproduce a wrong
rate but never detect one.

```
children_count          integer, required on every childcare booking
```

With this, the app can finally answer "this job had 3 children but was paid at
$23." Without it, that check is impossible.

### 3. The rate and hours as separate fields

`Paid to Caregiver` is a single number with the rate, the hours, and the
four-hour minimum all baked in and unrecoverable.

```
pay_rate                the $/hr actually applied
pay_tier                standard | three_to_four | group | pet
hours_worked            actual clock time
hours_billed            what the caregiver is paid for
minimum_applied         boolean
guarantee_hours         hours paid but not worked
```

The four-hour minimum is real and currently invisible: 199 of 200 worked jobs
in the August export fit `max(hours, 4) × rate` exactly. A 3.75-hour job pays
$92.00 and nothing anywhere says why. This matters for more than tidiness —
guarantee hours are **paid but not worked**, so they must not count toward
overtime thresholds. The app already handles this, but only because it worked
the rule out from the arithmetic.

### 4. Mileage, properly gated

Mileage is Care.com only, 40-mile minimum. All seven mileage reimbursements in
the August export were Corporate jobs and the smallest was exactly 40 miles —
the policy is being followed by people, with nothing enforcing it.

**Do not add a plain mileage field to the booking form.** Someone will fill it
in on a regular job. On a 4-hour Babysitter job Sitterwise earns $48; a
60-mile claim is $45.60. One mistake wipes out the job.

**Put the flag on the client account, not the job type:**

```
client.mileage_program_enabled    boolean, default FALSE
client.mileage_minimum_miles      number,  default 40
client.mileage_rate               number,  default 0.76
```

Care.com gets TRUE. Every family gets FALSE by default, forever. A booking's
eligibility is then **derived, never typed**:

```
booking.mileage_eligible = client.mileage_program_enabled     (read-only)
```

Better than keying off Service Type: when the threshold changes, or a second
corporate client joins the programme, it is a setting rather than a code
change.

**Make the request a separate record, not a field:**

```
mileage_request
  booking_id
  round_trip_miles
  status              requested | approved | rejected
  rate_snapshot       locked at approval so history never shifts
  amount              derived: miles x rate_snapshot
  approved_by         admin user
  approved_at
  rejection_reason
```

Only `approved` requests reach payroll. That one rule is the whole protection.

**Enforce it three times, all server-side:**

1. The form does not render the mileage option on an ineligible booking.
2. The API rejects a mileage request against an ineligible booking, even a
   hand-crafted one.
3. Nothing is payable until an admin approves it.

Gate 1 alone is what usually gets built. It is the one that fails.

**Show the money at risk on the approval screen:**

```
Mileage requested:      54 miles x $0.76 = $41.04
Sitterwise cut on job:  $48.00
Left after mileage:     $6.96            <-- warn
```

**Put the policy in front of the caregiver, at the moment they claim.** On
Care.com jobs only, the checkout screen should say in plain words what can be
claimed, what the limit is, and what needs a form - so nobody is guessing from
memory. On every other job the mileage option should not appear at all, and
saying nothing is the right thing to say.

**Worth considering:** if Sitterwise holds the caregiver's home address it can
compute home -> job -> home itself and store `computed_round_trip_miles`
alongside what was claimed. The request then becomes "confirm 54 miles" rather
than "type a number", and a mismatch flags itself.

**Also worth checking:** if Care.com reimburses Sitterwise for mileage, the
approved request should raise the client charge at the same time it raises the
caregiver payment — one record driving both sides. Then mileage never costs
Sitterwise anything and the commission risk disappears for eligible jobs.

---

## Should have

### 5. Bring the reimbursement type into the export

Sitterwise records what a reimbursement was for - parking, supplies, a
booster seat - somewhere. **It is not in the export.** Fourteen reimbursements
in August, not one description among them.

This is the clearest case of something already being collected and then thrown
away on the way out. Whatever field holds that note today, add it to the
export.

Then split the single money column so type is never inferred:

```
mileage_amount                   derived from the approved request only
other_reimbursement_amount
other_reimbursement_type         parking | supplies | tolls | other
other_reimbursement_description  free text, required when the amount is above zero
```

Two reasons this matters beyond tidiness:

- Reimbursements are **not taxable and not wages**. They have to be separable
  with certainty rather than by dividing by $0.76 and hoping.
- Right now the payroll app has to flag every undescribed reimbursement for
  review, because it cannot tell parking from a mileage claim someone should
  not have made. With a type field, most of those flags disappear.

### 6. Tips that can be recorded before a job is marked paid

Every tip in the August export sits on a row with status `paid`. Fifty-nine
finished jobs have an empty tip field — and an empty field is indistinguishable
from a genuine zero.

```
tip_amount
tip_method          cash | card
tip_recorded_at
tip_finalized       boolean
```

Cash and card tips are entered differently in OnPay: card tips are money
Sitterwise pays out, cash tips are money the caregiver already has.

### 7. Blank must mean something different from zero

`Tip` is blank on 182 rows and the string `"0.00"` on 117. In the database
those are probably different facts; the export flattens them. Keep NULL for
"nobody has said" and 0 for "asked, and the answer was none".

### 8. A payroll status, separate from the booking status

`confirmed` currently means both "hasn't happened yet" and "was worked and
nobody closed it out". Eight past-dated bookings sit in that state.

```
payroll_status      not_ready | ready_to_pay | paid_out
exported_for_payroll_at
```

That last timestamp makes double-paying structurally impossible rather than
something the app has to check for.

### 9. Keep test data out of the export

Booking 15245, client "Test Test", carries $92.00 of caregiver pay. Anything
importing blindly would pay it. Either exclude test bookings from exports or
add an `is_test` flag.

### 10. Never export a blank caregiver on a live booking

Booking 14870 has no caregiver and 30 hours on it.

### 11. Fix or drop `Total Hours`

It disagrees with the start and end times on 16 of 324 rows and is zero on
every booking that was not closed out. Either always recompute it from the
timestamps or remove it — the app recomputes from the clock regardless.

### 12. Record actual worked times, not just scheduled ones

Every hour in the export is scheduled time. California overtime is owed on
hours actually worked. If a caregiver closes a job out with real start and end
times, that is what payroll should use.

---

## Nice to have

### 13. A work-setting tag

```
work_setting        private_home | hotel | vacation_rental | commercial_venue
```

`Location Type` already carries most of this. Making it authoritative matters
because which California wage order applies may depend on it — 121 of 324
August jobs were at hotels, which are not private households.

### 14. Export numbers as numbers

Every value in the export is a string, including money, hours and dates.

### 15. A pay-period parameter on the export

The export is a whole calendar month with no notion of a pay period, so the
app has to ask which half you mean every time.

---

## About OnPay

OnPay matches imported payroll rows to employees by a **"Clock User"** set on
each employee's profile under Job. There are no formatting rules for it.

**Set every caregiver's OnPay Clock User to their Sitterwise `caregiver_id`.**
Then name-matching disappears from payroll permanently, and the app can build
an OnPay import file that lands on the right person every time.

Two things to ask OnPay support for:

1. Switch CSV hours import on for the account — it is off by default.
2. Send the exact CSV column specification. It is not published anywhere.

When they reply, the column names go in `onpay_mapping.json`. No code changes.
