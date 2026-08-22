# Fields that would help payroll

A short list for Aji. Everything here is an **addition** — nothing needs
taking out.

That matters because this same export also bills clients. A booking with no
caregiver on it may still be invoiceable depending on the corporate
arrangement, so payroll filters on its own side rather than asking the export
to leave anything behind.

The export works today. Payroll just has to work a few things out from the
numbers rather than reading them, and these fields would let it read them.

---

## The four that would help most

### 1. The pay rate

`Paid to Caregiver` is one total, so payroll divides pay by hours to work out
whether a job was the $23 or the $28 tier.

```
pay_rate     the $/hr applied
pay_tier     standard | three_to_four | group | pet
```

It resolves every real job cleanly, with one blind spot: a 4-hour job at
either tier can produce the same total, so a job priced at the wrong tier
looks correct downstream. Sitterwise knows the rate when it prices the job —
sending it along removes the guesswork, and keeps old payroll readable if
rates ever change.

### 2. Mileage worked out by Sitterwise

Policy is Care.com jobs only, and only the miles above 40 on a round trip. At
the moment that arithmetic happens by hand, which is easy to lose track of:

| Booking | Paid | Round trip | Policy pays |
|---|---:|---:|---:|
| 14829 | $30.40 | 40 mi | $0.00 |
| 15175 | $32.68 | 43 mi | $2.28 |
| 14750 | $41.04 | 54 mi | $10.64 |
| 15184 | $51.68 | 68 mi | $21.28 |

All seven August claims were paid for the whole drive rather than the miles
above 40 — each one out by exactly 40 miles' worth, $212.80 across the month.
Nothing a person should be expected to catch every time.

```
round_trip_miles    what the caregiver enters
payable_miles       computed: max(0, round_trip_miles - 40)
mileage_amount      computed: payable_miles x rate
needs_form          computed: round_trip_miles > 50
```

Computing `payable_miles` and `mileage_amount` rather than accepting them
handles it permanently. It would also help to keep the mileage option off
non-Care.com bookings — a flag on the client account (`mileage_program_enabled`,
default off) is more durable than keying off the service type.

### 3. What a reimbursement was for

Sitterwise records this somewhere — parking, supplies, a booster seat — but it
doesn't reach the export. Fourteen reimbursements in August, no descriptions.

```
other_reimbursement_type          parking | supplies | tolls | other
other_reimbursement_description   free text
```

Two reasons it matters more than it looks:

Reimbursements are **not taxable and not wages**, so they have to be
separable from pay with certainty. Right now the app tells mileage from
everything else by checking whether the amount divides into whole miles at the
IRS rate — which works, but it's arithmetic standing in for a fact Sitterwise
already has.

And because it can't tell what the rest were for, it flags every one of them
for review. That's a handful of manual checks every single payroll run, on
money that was probably fine. A type field turns most of those into nothing.

### 4. A caregiver ID

Payroll matches caregivers by display name today, so a name change or a stray
space splits one person in two.

```
caregiver_id        stable, never reused
onpay_clock_user    the matching ID in OnPay
```

OnPay matches imported rows on a field called **Clock User**. Setting it to
`caregiver_id` would let payroll join on an ID instead of a spelling.

---

## Smaller things, whenever convenient

**Hours worked vs hours paid.** The four-hour minimum is real but invisible: a
3.75-hour job pays $92.00 and nothing says why. `hours_worked`,
`hours_billed` and `minimum_applied` would make it explicit. Worth doing
because guarantee hours are paid but not worked, so under California law they
don't count toward overtime.

**Tips.** Every tip lands on a row already marked paid, so a finished job
shows an empty tip field whether or not there was one. Being able to record a
tip before a job is closed out would remove that ambiguity.

**Blank vs zero.** `Tip` is blank on 182 rows and `"0.00"` on 117. Keeping
NULL for "not asked" and 0 for "none" preserves a real distinction.

**Care.com job number.** The mileage form asks for it, and it isn't in the
export, so nothing can tie a request back to its job.

**A test flag.** Booking 15245 (client "Test Test") carries $92.00 of pay. An
`is_test` flag would let payroll skip it while invoicing keeps whatever it
needs.

**Numbers as numbers.** Everything currently exports as a string, including
money and dates.

**`Total Hours`.** Disagrees with the start and end times on 16 of 324 rows.
Payroll uses the clock either way, so this is only worth fixing if invoicing
relies on it.

---

## If only some get done

The first three. The pay rate ends the guesswork, computed mileage ends the
hand arithmetic, and the reimbursement type saves a review every run. The
caregiver ID is small and makes everything downstream steadier.

Happy to talk any of this through.
