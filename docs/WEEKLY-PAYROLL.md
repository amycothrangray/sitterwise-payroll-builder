# Weekly payroll

Open Sitterwise Payroll. GitHub Desktop and Terminal are not part of payroll.

The main payroll screen has **This week’s reminders** for Odds & Ends and each
scheduled payment. Review the external Odds & Ends list too, even if no entries
are saved in the app. Apply eligible saved entries there; handle other entries
in OnPay and mark them done in Odds & Ends. After importing, verify the scheduled
amounts in OnPay and check the boxes. Do not add pay that is already included.
The checks are saved for this week and start fresh next week.

1. **Upload bookings.** Choose your Sitterwise CSV or Excel export, then the pay week.
   Use a fresh export that includes last week's bookings, so late tips are found.
   At month-end, upload both months together.
   Check **Pay date** against OnPay. New weeks suggest the following Friday;
   change it if needed. The date is saved and printed on the breakdowns.
2. **Download OnPay CSV.** Import it once into that pay week in OnPay. Compare the
   app's people, hours, and total with OnPay before continuing.
3. **Share caregiver breakdowns.** Download caregiver PDFs and unzip the folder.
   Give that folder to Claude Cowork, then use **Copy task for Claude Cowork** and
   paste the task. Cowork verifies pay, uploads each PDF to that employee’s
   **HR → Files**, enables **Employee Viewable**, and checks the saved document.
   It then adds one short paycheck memo: “Your payroll breakdown is in OnPay >
   Menu > My Files.” You review and submit payroll in OnPay.
   If any pay or the pay date changes, download a fresh folder and copy the task again.
4. **CalSavers.** Directly below the caregiver breakdowns step, upload the OnPay Payroll
   Register PDF after reviewing and submitting payroll in OnPay. Enter the
   verified contributions in CalSavers. Select **Mark this week finished** in
   this app; CalSavers remains visible on the finished screen too.
   Downloading or copying notes does not submit payroll.

Late tips on previously paid bookings are included automatically in the next
payroll's Tips amount and caregiver PDF. Only the unpaid increase is added;
the booking's wages and hours are not paid again. Tips discovered after an
OnPay CSV has been downloaded wait for the following payroll. Always mark a
submitted week finished so the app knows which tips were paid.

The app learns about tips from your uploads. If a tip is added to an older
month, include a fresh export of that month too. It cannot find a tip that
Sitterwise leaves out of the export.

## One-time setup

Every paid worker, including people with only scheduled pay, needs a verified
OnPay Clock User. An Employee ID is a different field and is not substituted.
Pay items and types must match the company mapping. Overtime and double-time
premiums use Non-Hourly custom items; normal hours retain their original rates.

Verify scheduled pay under **More → Settings & backup**. A dated first-period
amount applies only once; later weeks use the regular weekly amount.

A missing identifier, duplicate pay item, unresolved payroll error, or saved
unapplied pay change must be fixed before downloading a complete CSV. A warning
about a genuine pay question still needs review. The app cannot guarantee that
OnPay settings or source bookings will never change.

For the PDF sharing task, Claude Cowork needs browser access and an OnPay login.
The task contains private paycheck data, and copying it alone does not send it.
It tells Cowork to verify identity and pay, share only each person’s own PDF, save the short memo,
read it back, and leave
payroll unsubmitted. Existing different notes are preserved pending review.


## History and moving Macs

Use **More → Settings & backup → Download private history file**. Restore it
once into an empty app on the receiving Mac. Share this file privately; it is
separate from the downloadable software. Use only one working payroll copy.

## CalSavers

After submitting payroll in OnPay, open its Payroll Register report and choose
**Save as PDF**. On the main payroll screen, directly after the caregiver breakdowns step,
choose **CalSavers → Upload register PDF**. It is also at the top of Reports.
The reader checks the employee contributions against the register total before
showing amounts to enter in CalSavers. This upload accepts PDF, not spreadsheets.

## Updating Lissa’s app

Quit Sitterwise Payroll, open the new installer, and replace the app in
Applications. Open it again; current history and settings stay on this Mac.
Do not restore an older history file when updating an existing installation.

## Caregiver breakdowns

Caregivers open OnPay’s menu and select **My Files**. Each PDF shows jobs, actual
hours, job rates, minimum-pay top-ups, overtime premiums, Lifesaver bonuses, tips,
reimbursements and any scheduled pay or adjustments. Taxes and take-home pay
remain on the official OnPay pay stub. Saved older runs retain their saved rules.

Never share the whole ZIP or manifest with a caregiver, and never use Company
Documents or an all-workers folder for private payroll breakdowns. Existing
different documents or memos are preserved for review. Downloading the folder
does not upload anything, mark tips paid or submit payroll.

Download PDFs for a finished payroll from its record. If its reconstructed totals
no longer agree with the saved record, the app stops the download for review.
Older records have no assumed payday; enter their actual check date if you want
it printed on the PDFs. This does not reopen payroll or change paid amounts.
Manual changes made only in OnPay must also be reflected in the payroll app if
they are to appear in its PDFs; Cowork stops if those totals do not match.
