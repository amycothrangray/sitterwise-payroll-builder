# How to check the app's arithmetic

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

---

## The payroll as a whole

| | |
|---|---|
| Caregivers | 24 |
| Jobs paid | 43 |
| 3-4 Children hours | 27.00 at $28.00 = $756.00 |
| Unknown rate hours | 4.00 at $25.00 = $99.99 |
| Regular hours | 199.50 at $23.00 = $4588.50 |
| Minimum-guarantee hours | 1.50 = $34.50 |
| Overtime | 16.00 hrs, $186.50 premium |
| Double time | 1.00 hrs, $23.00 premium |
| Tips | $125.00 |
| Bonuses | $15.00 |
| Mileage | 194 miles = $147.44 |
| Other reimbursements | $68.10 |
| **Taxable earnings** | **$5828.49** |
| **Reimbursements** | **$215.54** |
| **Total being paid** | **$6044.03** |

Payroll check: **11 ready, 7 needing a look, 6 that cannot be paid**.

Reconciliation: 45 jobs dated in this period, 43 paid, 43 accounted for. 1 left out (status is cancelled). 1 left out (worked but never closed out in sitterwise). Balances: **yes**.

---

## Rosa Delgado

*Only regular-rate jobs*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-03 | 90001 | 5.00 | $23.00 | $115.00 |  |
| 2026-08-05 | 90002 | 5.00 | $23.00 | $115.00 |  |
| 2026-08-07 | 90003 | 5.00 | $23.00 | $115.00 |  |

**What they are paid:**

| | |
|---|---:|
| Regular: 15.00 hrs x $23.00 | $345.00 |
| **Taxable earnings** | **$345.00** |
| **Total being paid** | **$345.00** |

---

## Ivy Chen

*Only 3-4 children jobs*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-04 | 90004 | 6.00 | $28.00 | $168.00 |  |
| 2026-08-06 | 90005 | 6.00 | $28.00 | $168.00 |  |

**What they are paid:**

| | |
|---|---:|
| 3-4 Children: 12.00 hrs x $28.00 | $336.00 |
| **Taxable earnings** | **$336.00** |
| **Total being paid** | **$336.00** |

---

## Mona Patel

*Both rates, no overtime*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-03 | 90006 | 6.00 | $23.00 | $138.00 |  |
| 2026-08-05 | 90007 | 4.00 | $28.00 | $112.00 |  |

**What they are paid:**

| | |
|---|---:|
| 3-4 Children: 4.00 hrs x $28.00 | $112.00 |
| Regular: 6.00 hrs x $23.00 | $138.00 |
| **Taxable earnings** | **$250.00** |
| **Total being paid** | **$250.00** |

---

## Dana Reyes

*Crosses daily overtime at one rate*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-03 | 90008 | 10.00 | $23.00 | $230.00 |  |
| 2026-08-06 | 90009 | 4.00 | $23.00 | $92.00 |  |

**The overtime:**

Week beginning 2026-08-02. One rate this week, so the regular rate is $23.00 an hour.

- **2026-08-03** - 10.00 hours. The first 8.00 are normal, the next 2.00 are overtime.
- Overtime premium: 2.00 x 0.5 x $23.0000 = **$23.00**


**What they are paid:**

| | |
|---|---:|
| Regular: 14.00 hrs x $23.00 | $322.00 |
| Overtime premium: 2.00 hrs | $23.00 |
| **Taxable earnings** | **$345.00** |
| **Total being paid** | **$345.00** |

**What the payroll check says:**

- **Note** - Dana Reyes has 2.00 hours of overtime. Worth 23.00 in premium pay on top of straight time.

---

## Tess Okafor

*Overtime while working both rates*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-04 | 90010 | 5.00 | $23.00 | $115.00 |  |
| 2026-08-04 | 90011 | 5.00 | $28.00 | $140.00 |  |

**The overtime:**

Week beginning 2026-08-02. Two rates this week, so overtime uses the weighted average: $255.00 of straight-time pay divided by 10.00 hours worked = $25.5000 an hour.

- **2026-08-04** - 10.00 hours. The first 8.00 are normal, the next 2.00 are overtime.
- Overtime premium: 2.00 x 0.5 x $25.5000 = **$25.50**


**What they are paid:**

| | |
|---|---:|
| 3-4 Children: 5.00 hrs x $28.00 | $140.00 |
| Regular: 5.00 hrs x $23.00 | $115.00 |
| Overtime premium: 2.00 hrs | $25.50 |
| **Taxable earnings** | **$280.50** |
| **Total being paid** | **$280.50** |

**What the payroll check says:**

- **Needs a look** - Tess Okafor has overtime across two different rates. Tess Okafor worked at $28.00, $23.00 this period, so their overtime is based on a blended rate of $25.5000 an hour, not on either rate on its own. OnPay will not work this out correctly on its own.
- **Note** - Tess Okafor has 2.00 hours of overtime. Worth 25.50 in premium pay on top of straight time.

---

## Priya Raman

*Double time past 12 hours*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-05 | 90012 | 13.00 | $23.00 | $299.00 |  |

**The overtime:**

Week beginning 2026-08-02. One rate this week, so the regular rate is $23.00 an hour.

- **2026-08-05** - 13.00 hours. The first 8.00 are normal, 4.00 are overtime, and 1.00 past 12.00 hours are double time.
- Overtime premium: 4.00 x 0.5 x $23.0000 = **$46.00**
- Double time premium: 1.00 x 1.0 x $23.0000 = **$23.00**


**What they are paid:**

| | |
|---|---:|
| Regular: 13.00 hrs x $23.00 | $299.00 |
| Overtime premium: 4.00 hrs | $46.00 |
| Double time premium: 1.00 hrs | $23.00 |
| **Taxable earnings** | **$368.00** |
| **Total being paid** | **$368.00** |

**What the payroll check says:**

- **Needs a look** - Priya Raman worked double time. 1.00 hours past 12.00 in a day, which is paid at double time. That is 23.00 on top of normal pay.
- **Needs a look** - Priya Raman has a 13.00-hour shift. Booking 90012 on Aug 5 runs from 7:00 AM to 8:00 PM. That is unusually long.
- **Note** - Priya Raman has 4.00 hours of overtime. Worth 46.00 in premium pay on top of straight time.

---

## Ruth Ozeki

*Seventh consecutive day*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-02 | 90028 | 4.00 | $23.00 | $92.00 |  |
| 2026-08-03 | 90029 | 4.00 | $23.00 | $92.00 |  |
| 2026-08-04 | 90030 | 4.00 | $23.00 | $92.00 |  |
| 2026-08-05 | 90031 | 4.00 | $23.00 | $92.00 |  |
| 2026-08-06 | 90032 | 4.00 | $23.00 | $92.00 |  |
| 2026-08-07 | 90033 | 4.00 | $23.00 | $92.00 |  |
| 2026-08-08 | 90034 | 4.00 | $23.00 | $92.00 |  |

**The overtime:**

Week beginning 2026-08-02. One rate this week, so the regular rate is $23.00 an hour.

- **2026-08-08** - Seventh day in a row worked. The first 4.00 hours are at time and a half.
- Overtime premium: 4.00 x 0.5 x $23.0000 = **$46.00**


**What they are paid:**

| | |
|---|---:|
| Regular: 28.00 hrs x $23.00 | $644.00 |
| Overtime premium: 4.00 hrs | $46.00 |
| **Taxable earnings** | **$690.00** |
| **Total being paid** | **$690.00** |

**What the payroll check says:**

- **Note** - Ruth Ozeki has 4.00 hours of overtime. Worth 46.00 in premium pay on top of straight time.

---

## Cass Moreau

*Over 40 hours, weekly overtime switched off*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-02 | 90035 | 7.00 | $23.00 | $161.00 |  |
| 2026-08-03 | 90036 | 7.00 | $23.00 | $161.00 |  |
| 2026-08-04 | 90037 | 7.00 | $23.00 | $161.00 |  |
| 2026-08-05 | 90038 | 7.00 | $23.00 | $161.00 |  |
| 2026-08-06 | 90039 | 7.00 | $23.00 | $161.00 |  |
| 2026-08-07 | 90040 | 7.00 | $23.00 | $161.00 |  |

**What they are paid:**

| | |
|---|---:|
| Regular: 42.00 hrs x $23.00 | $966.00 |
| **Taxable earnings** | **$966.00** |
| **Total being paid** | **$966.00** |

**What the payroll check says:**

- **Needs a look** - Cass Moreau worked 42.00 hours in one week. That is over the 40.00-hour weekly overtime threshold, but weekly overtime is switched off in Settings, so no extra is being paid for it beyond the daily overtime already included.

---

## Lena Voss

*A bonus alongside overtime*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-03 | 90041 | 9.00 | $23.00 | $207.00 | bonus $15.00 |

**The overtime:**

Week beginning 2026-08-02. One rate this week, so the regular rate is $23.00 an hour.

- **2026-08-03** - 9.00 hours. The first 8.00 are normal, the next 1.00 are overtime.
- Overtime premium: 1.00 x 0.5 x $23.0000 = **$11.50**


**What they are paid:**

| | |
|---|---:|
| Regular: 9.00 hrs x $23.00 | $207.00 |
| Overtime premium: 1.00 hrs | $11.50 |
| Bonuses | $15.00 |
| **Taxable earnings** | **$233.50** |
| **Total being paid** | **$233.50** |

**What the payroll check says:**

- **Needs a look** - Lena Voss has both a bonus and overtime. Lena Voss received 15.00 in bonuses and worked overtime in the same period. If those bonuses are earned rather than a gift, California generally requires them to raise the overtime rate. The app has not done that.
- **Note** - Lena Voss has 1.00 hours of overtime. Worth 11.50 in premium pay on top of straight time.

---

## Belle Cruz

*The four-hour minimum*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-07 | 90025 | 2.50 | $23.00 | $57.50 | paid for 4.00, 4-hr minimum |

**What they are paid:**

| | |
|---|---:|
| Regular: 2.50 hrs x $23.00 | $57.50 |
| Four-hour minimum top-up: 1.50 hrs | $34.50 |
| **Taxable earnings** | **$92.00** |
| **Total being paid** | **$92.00** |

---

## Nina Alvarez

*A tip*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-06 | 90013 | 4.00 | $23.00 | $92.00 | tip $75.00 |

**What they are paid:**

| | |
|---|---:|
| Regular: 4.00 hrs x $23.00 | $92.00 |
| Tips | $75.00 |
| **Taxable earnings** | **$167.00** |
| **Total being paid** | **$167.00** |

---

## Gwen Mabry

*Mileage on a Care.com job*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-07 | 90014 | 5.00 | $23.00 | $115.00 | 40 mi = $30.40 |

**What they are paid:**

| | |
|---|---:|
| Regular: 5.00 hrs x $23.00 | $115.00 |
| **Taxable earnings** | **$115.00** |
| Mileage: 40 miles | $30.40 |
| **Total being paid** | **$145.40** |

---

## Sofia Bright

*A reimbursement that is not mileage*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-07 | 90015 | 4.00 | $23.00 | $92.00 | reimbursement $22.50 |

**What they are paid:**

| | |
|---|---:|
| Regular: 4.00 hrs x $23.00 | $92.00 |
| **Taxable earnings** | **$92.00** |
| Other reimbursement (not taxable) | $22.50 |
| **Total being paid** | **$114.50** |

---

## Hana Kimura

*Both a tip and mileage*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-08 | 90019 | 6.00 | $28.00 | $168.00 | tip $50.00; 54 mi = $41.04 |

**What they are paid:**

| | |
|---|---:|
| 3-4 Children: 6.00 hrs x $28.00 | $168.00 |
| Tips | $50.00 |
| **Taxable earnings** | **$218.00** |
| Mileage: 54 miles | $41.04 |
| **Total being paid** | **$259.04** |

---

## Faye Nakamura

*Mileage claimed on a job that does not qualify*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-06 | 90016 | 5.00 | $23.00 | $115.00 | reimbursement $30.40 |

**What they are paid:**

| | |
|---|---:|
| Regular: 5.00 hrs x $23.00 | $115.00 |
| **Taxable earnings** | **$115.00** |
| Other reimbursement (not taxable) | $30.40 |
| **Total being paid** | **$145.40** |

**What the payroll check says:**

- **Needs a look** - Faye Nakamura may have claimed mileage on a job that does not qualify. Booking 90016 on Aug 6 is a Babysitter job with a 30.40 reimbursement that is an exact number of miles at the current rate. Mileage is only paid on Corporate (Invoiced) jobs, so the app has NOT paid this as mileage - it is sitting in other reimbursements instead.

---

## Nadia Okoro

*A Care.com claim under the 40-mile minimum*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-05 | 90018 | 4.00 | $23.00 | $92.00 | reimbursement $15.20 |

**What they are paid:**

| | |
|---|---:|
| Regular: 4.00 hrs x $23.00 | $92.00 |
| **Taxable earnings** | **$92.00** |
| Other reimbursement (not taxable) | $15.20 |
| **Total being paid** | **$107.20** |

**What the payroll check says:**

- **Needs a look** - Nadia Okoro's mileage claim on Aug 5 is under the 40-mile minimum. Booking 90018 has a 15.20 reimbursement, which is a trip shorter than the 40 miles a mileage claim needs. The app has not paid it as mileage.

---

## Della Cruz

*Mileage larger than the commission on the job*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-07 | 90017 | 4.00 | $23.00 | $92.00 | 100 mi = $76.00 |

**What they are paid:**

| | |
|---|---:|
| Regular: 4.00 hrs x $23.00 | $92.00 |
| **Taxable earnings** | **$92.00** |
| Mileage: 100 miles | $76.00 |
| **Total being paid** | **$168.00** |

**What the payroll check says:**

- **Needs a look** - Della Cruz's mileage on Aug 7 is more than Sitterwise earned on the job. Booking 90017 pays 76.00 of mileage (100 miles) but Sitterwise's cut on it is only 48.00. That job loses money.

---

## Cleo Barnes

*The same booking twice*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-04 | 95001 | 5.00 | $23.00 | $115.00 |  |
| 2026-08-04 | 95001 | 5.00 | $23.00 | $115.00 |  |

**The overtime:**

Week beginning 2026-08-02. One rate this week, so the regular rate is $23.00 an hour.

- **2026-08-04** - 10.00 hours. The first 8.00 are normal, the next 2.00 are overtime.
- Overtime premium: 2.00 x 0.5 x $23.0000 = **$23.00**


**What they are paid:**

| | |
|---|---:|
| Regular: 10.00 hrs x $23.00 | $230.00 |
| Overtime premium: 2.00 hrs | $23.00 |
| **Taxable earnings** | **$253.00** |
| **Total being paid** | **$253.00** |

**What the payroll check says:**

- **CANNOT BE PAID** - Booking 95001 appears 2 times. The same booking shows up 2 times in this export, which would pay Cleo Barnes more than once for one job.
- **CANNOT BE PAID** - Cleo Barnes has the same shift booked twice. Bookings 95001 and 95001 are all on Aug 4 at 9:00 AM for the same person.
- **Note** - Cleo Barnes has 2.00 hours of overtime. Worth 23.00 in premium pay on top of straight time.

---

## Ada Whitlow

*Two shifts at the same time*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-05 | 90022 | 5.00 | $23.00 | $115.00 |  |
| 2026-08-05 | 90023 | 4.00 | $23.00 | $92.00 |  |

**The overtime:**

Week beginning 2026-08-02. One rate this week, so the regular rate is $23.00 an hour.

- **2026-08-05** - 9.00 hours. The first 8.00 are normal, the next 1.00 are overtime.
- Overtime premium: 1.00 x 0.5 x $23.0000 = **$11.50**


**What they are paid:**

| | |
|---|---:|
| Regular: 9.00 hrs x $23.00 | $207.00 |
| Overtime premium: 1.00 hrs | $11.50 |
| **Taxable earnings** | **$218.50** |
| **Total being paid** | **$218.50** |

**What the payroll check says:**

- **CANNOT BE PAID** - Ada Whitlow is booked in two places at once. Booking 90022 runs Aug 5 9:00 AM to 2:00 PM, and booking 90023 starts at 12:00 PM before that one ends. One person cannot work both, so these hours are being counted twice.
- **Note** - Ada Whitlow has 1.00 hours of overtime. Worth 11.50 in premium pay on top of straight time.

---

## June Salter

*Pay that matches no known rate*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-06 | 90024 | 4.00 | not recognised | $99.99 |  |

**What they are paid:**

| | |
|---|---:|
| Unknown rate: 4.00 hrs x $25.00 | $99.99 |
| **Taxable earnings** | **$99.99** |
| **Total being paid** | **$99.99** |

**What the payroll check says:**

- **CANNOT BE PAID** - June Salter's job on Aug 6 does not match a known pay rate. Booking 90024 paid 99.99 for 4.00 hours. That is not $23.00 or $28.00 an hour, so the app cannot tell which rate was meant.

---

## Opal Grant

*Not set up in OnPay*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-04 | 90026 | 6.00 | $23.00 | $138.00 |  |

**What they are paid:**

| | |
|---|---:|
| Regular: 6.00 hrs x $23.00 | $138.00 |
| **Taxable earnings** | **$138.00** |
| **Total being paid** | **$138.00** |

**What the payroll check says:**

- **CANNOT BE PAID** - Opal Grant is not set up in OnPay. Opal Grant is owed 138.00 this period but is marked "Not in OnPay".

---

## Vera Lund

*Corrected by hand (see below)*

**The jobs:**

| Date | Booking | Hours | Rate | Straight pay | |
|---|---|---:|---:|---:|---|
| 2026-08-06 | 95500 | 5.00 | $23.00 | $115.00 |  |

**What they are paid:**

| | |
|---|---:|
| Regular: 5.00 hrs x $23.00 | $115.00 |
| **Taxable earnings** | **$115.00** |
| **Total being paid** | **$115.00** |

---

## Corrected by hand

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

Seven further tests run against a real Sitterwise export when one is available
(put it in `tests/fixtures/real/`, which git ignores, or point
`SITTERWISE_EXPORT` at it). They check that all 324 rows read without error,
that every paid job matches a known rate, that the payroll balances, that the
four-hour minimum is recognised, that mileage only ever lands on Care.com jobs
of 40 miles or more, and that these totals for Aug 1-15 have not moved:

| | |
|---|---:|
| Hours worked | 977.50 |
| Straight-time wages | $23,418.75 |
| Four-hour minimum top-ups | $83.50 |
| Overtime | 51.25 hrs, $618.41 premium |
| Double time | 1.25 hrs, $28.75 premium |
| Tips | $635.00 |
| **Total being paid** | **$25,326.05** |

If a change moves one of those numbers, that should be a decision somebody
made - not a surprise.

