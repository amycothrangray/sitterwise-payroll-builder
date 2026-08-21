"""Build the test payroll export.

This writes a file with exactly the columns Sitterwise produces, containing a
deliberately awkward payroll: every rate combination, every kind of extra
payment, and every mistake the app is supposed to catch.

Run it with:  python3 tests/fixtures/make_fixture.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

HEADER = [
    "Booking ID", "ULID", "Client Name", "Client Email", "Client Phone", "Service Type",
    "Location Type", "Hotel", "Address", "Start Date", "Start Time", "End Date", "End Time",
    "Total Hours", "Caregiver Name", "Status", "Payment Status", "Charge to Client",
    "Paid to Caregiver", "Sitterwise Cut", "Reimbursement", "Tip", "Bonus", "Total Amount",
    "Created At", "Admin Notes", "Lifesaver Bonus",
]

MINIMUM_HOURS = Decimal("4")
_next_id = [90000]


def booking(caregiver, day, start, hours, rate, *, status="completed", tip=None,
            reimbursement=None, bonus="0.00", lifesaver=0, service="Babysitter",
            location="Private Home", hotel="", client="Test Family", paid=None,
            total_hours=None, booking_id=None, notes=""):
    """One row, priced the way Sitterwise prices it: hours x rate, with a
    four-hour minimum, and never a rate column."""
    _next_id[0] += 1
    ident = booking_id or _next_id[0]
    begin = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
    finish = begin + timedelta(hours=float(hours))
    payable = max(Decimal(str(hours)), MINIMUM_HOURS)
    pay = Decimal(str(paid)) if paid is not None else (payable * Decimal(str(rate)))
    charge = payable * Decimal("35")
    return {
        "Booking ID": ident,
        "ULID": f"01TEST{ident}",
        "Client Name": client,
        "Client Email": "family@example.com",
        "Client Phone": "+16195550000",
        "Service Type": service,
        "Location Type": location,
        "Hotel": hotel,
        "Address": "1 Test Way, San Diego, CA, 92101",
        "Start Date": begin.date().isoformat(),
        "Start Time": begin.strftime("%H:%M"),
        "End Date": finish.date().isoformat(),
        "End Time": finish.strftime("%H:%M"),
        "Total Hours": f"{Decimal(str(total_hours if total_hours is not None else hours)):.2f}",
        "Caregiver Name": caregiver,
        "Status": status,
        "Payment Status": "charged" if status in ("paid", "completed") else "pending",
        "Charge to Client": f"{charge:.2f}",
        "Paid to Caregiver": f"{pay:.2f}",
        "Sitterwise Cut": f"{charge - pay:.2f}",
        "Reimbursement": reimbursement,
        "Tip": tip,
        "Bonus": bonus,
        "Total Amount": f"{charge:.2f}",
        "Created At": "2026-07-20 09:00",
        "Admin Notes": notes,
        "Lifesaver Bonus": lifesaver,
    }


def d(day: int) -> date:
    return date(2026, 8, day)


def build_rows() -> list[dict]:
    rows: list[dict] = []
    add = rows.append

    # -- only the regular rate ------------------------------------------
    for day in (3, 5, 7):
        add(booking("Rosa Delgado", d(day), "09:00", 5, 23))

    # -- only the 3-4 children rate -------------------------------------
    for day in (4, 6):
        add(booking("Ivy Chen", d(day), "10:00", 6, 28, client="Big Family"))

    # -- both rates, no overtime ----------------------------------------
    add(booking("Mona Patel", d(3), "09:00", 6, 23))
    add(booking("Mona Patel", d(5), "09:00", 4, 28, client="Big Family"))

    # -- crosses daily overtime on one rate ------------------------------
    add(booking("Dana Reyes", d(3), "08:00", 10, 23))
    add(booking("Dana Reyes", d(6), "09:00", 4, 23))

    # -- overtime while working both rates in one day --------------------
    add(booking("Tess Okafor", d(4), "07:00", 5, 23))
    add(booking("Tess Okafor", d(4), "13:00", 5, 28, client="Big Family"))

    # -- double time -----------------------------------------------------
    add(booking("Priya Raman", d(5), "07:00", 13, 23, location="Hotel",
                hotel="Hotel del Coronado"))

    # -- a tip ------------------------------------------------------------
    add(booking("Nina Alvarez", d(6), "17:00", 4, 23, status="paid", tip="75.00"))

    # -- mileage (a whole number of miles at $0.76) -----------------------
    add(booking("Gwen Mabry", d(7), "09:00", 5, 23, reimbursement="30.40",
                service="Corporate (Invoiced)", client="Care Family",
                notes="Drove to Carlsbad"))

    # -- a reimbursement that is not mileage ------------------------------
    add(booking("Sofia Bright", d(7), "09:00", 4, 23, reimbursement="22.50"))

    # -- mileage claimed on a job that does not qualify for it -------------
    # 40 miles at $0.76, but on a Babysitter job. Mileage is Care.com only.
    add(booking("Faye Nakamura", d(6), "09:00", 5, 23, reimbursement="30.40"))

    # -- a Care.com mileage claim bigger than the commission on the job ----
    add(booking("Della Cruz", d(7), "09:00", 4, 23, service="Corporate (Invoiced)",
                reimbursement="76.00", client="Care Family"))

    # -- a Care.com claim under the 40-mile minimum ------------------------
    add(booking("Nadia Okoro", d(5), "09:00", 4, 23, service="Corporate (Invoiced)",
                reimbursement="15.20", client="Care Family"))

    # -- both a tip and a reimbursement -----------------------------------
    add(booking("Hana Kimura", d(8), "12:00", 6, 28, status="paid", tip="50.00",
                reimbursement="41.04", service="Corporate (Invoiced)", client="Care Family"))

    # -- the same booking twice -------------------------------------------
    add(booking("Cleo Barnes", d(4), "09:00", 5, 23, booking_id=95001))
    add(booking("Cleo Barnes", d(4), "09:00", 5, 23, booking_id=95001))

    # -- two shifts at the same time --------------------------------------
    add(booking("Ada Whitlow", d(5), "09:00", 5, 23))
    add(booking("Ada Whitlow", d(5), "12:00", 4, 23))

    # -- pay that matches no rate we know about ---------------------------
    add(booking("June Salter", d(6), "09:00", 4, 23, paid="99.99"))

    # -- the four-hour minimum --------------------------------------------
    add(booking("Belle Cruz", d(7), "18:00", 2.5, 23, status="paid"))

    # -- worked, but nobody has set them up in OnPay -----------------------
    add(booking("Opal Grant", d(4), "09:00", 6, 23))

    # -- someone whose figures get corrected by hand -----------------------
    add(booking("Vera Lund", d(6), "16:00", 5, 23, booking_id=95500))

    # -- seven days in a row, inside one Monday-to-Sunday pay week ----------
    for day in range(3, 10):          # Mon Aug 3 to Sun Aug 9
        add(booking("Ruth Ozeki", d(day), "09:00", 4, 23))

    # -- over 40 hours in a week, but never over 8 in a day ----------------
    for day in range(3, 9):           # Mon Aug 3 to Sat Aug 8
        add(booking("Cass Moreau", d(day), "09:00", 7, 23))

    # -- a bonus alongside overtime ----------------------------------------
    add(booking("Lena Voss", d(3), "08:00", 9, 23, lifesaver=15))

    # -- things that must never be paid -------------------------------------
    add(booking("Rosa Delgado", d(9), "09:00", 5, 23, status="cancelled"))
    add(booking("Rosa Delgado", d(4), "09:00", 5, 23, status="confirmed"))
    add(booking("", d(4), "09:00", 5, 23, client="Nobody Assigned"))
    add(booking("Zed Tester", d(6), "09:00", 4, 23, client="Test Test"))

    # -- outside the pay period ---------------------------------------------
    add(booking("Rosa Delgado", d(20), "09:00", 5, 23))
    add(booking("Ivy Chen", d(22), "09:00", 6, 28))

    # -- mileage in the first half of the year, when the IRS rate was lower --
    add(booking("Gwen Mabry", date(2026, 3, 10), "09:00", 5, 23, reimbursement="29.00",
                service="Corporate (Invoiced)", client="Care Family"))

    return rows


def write(path: Path) -> Path:
    import openpyxl
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(HEADER)
    for row in build_rows():
        sheet.append([row[column] for column in HEADER])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "test-payroll.xlsx"
    print(f"wrote {write(target)}")
