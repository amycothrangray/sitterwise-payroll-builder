# Bookings export fields for payroll

The payroll app uses Caregiver ID as OnPay Clock User. It keeps actual hours
worked separate from the four-hour paid minimum and uses the reimbursement
amount supplied by Sitterwise. Payroll results do not need to be imported
back into Sitterwise.

Keep stable Booking ID and Caregiver ID values, booking status, work date,
actual start and end times, explicit hours worked, hours paid, pay rate, tips,
and reimbursement dollar amounts in the download. Document the time zone
and whether an end time on the next day crosses midnight.

There is one incentive type: the Lifesaver Bonus. Please export one canonical
bonus amount, or document explicitly whether the Bonus and Lifesaver Bonus
columns duplicate the same payment or represent separate payments. Until
that is settled, a booking with both columns populated requires review.

Use invented people and booking IDs in public examples and bug reports.
Share real exports and employee-specific questions privately.
