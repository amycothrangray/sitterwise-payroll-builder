"""The caregiver roster - who is set up to actually be paid.

Sitterwise does not know anything about OnPay, so this is the one place where
payroll-only information lives. It can be filled in by hand, but the reliable
way is to import an employee export out of OnPay: that way nobody is
maintaining the same fact in two systems.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, asdict
from pathlib import Path

READY = "onpay_ready"
DIRECT_DEPOSIT_INCOMPLETE = "direct_deposit_incomplete"
SETUP_INCOMPLETE = "onpay_setup_incomplete"
NOT_IN_ONPAY = "not_in_onpay"

STATUS_LABELS = {
    READY: "OnPay Ready",
    DIRECT_DEPOSIT_INCOMPLETE: "Direct Deposit Incomplete",
    SETUP_INCOMPLETE: "OnPay Setup Incomplete",
    NOT_IN_ONPAY: "Not in OnPay",
}
STATUS_ICONS = {
    READY: "check",
    DIRECT_DEPOSIT_INCOMPLETE: "warn",
    SETUP_INCOMPLETE: "warn",
    NOT_IN_ONPAY: "stop",
}
BLOCKING_STATUSES = {NOT_IN_ONPAY}


def normalise_name(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


@dataclass
class RosterEntry:
    caregiver_key: str
    display_name: str
    status: str = NOT_IN_ONPAY
    onpay_clock_user: str = ""
    onpay_employee_id: str = ""
    note: str = ""
    updated_at: str = ""
    source: str = "manual"          # manual | onpay_import

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def is_blocking(self) -> bool:
        return self.status in BLOCKING_STATUSES

    @property
    def needs_attention(self) -> bool:
        return self.status != READY

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status_label"] = self.status_label
        data["status_icon"] = STATUS_ICONS.get(self.status, "warn")
        data["is_blocking"] = self.is_blocking
        data["needs_attention"] = self.needs_attention
        return data


# --- importing an employee list out of OnPay -------------------------------

_NAME_HEADERS = {
    "name", "employeename", "employee", "fullname", "legalname",
}
_FIRST_HEADERS = {"first", "firstname", "employeefirstname", "legalfirstname"}
_LAST_HEADERS = {"last", "lastname", "employeelastname", "legallastname"}
_CLOCK_HEADERS = {"clockuser", "clockuserid", "clockid", "externalid"}
_ID_HEADERS = {"employeeid", "id", "employeenumber", "empid"}
_STATUS_HEADERS = {"status", "employmentstatus", "employeestatus", "active"}
_DD_HEADERS = {
    "directdeposit", "directdepositstatus", "paymentmethod", "paymethod",
    "hasdirectdeposit", "bankaccount", "bankaccounts",
}


def _squash(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def parse_onpay_employee_export(path: Path | str) -> tuple[list[RosterEntry], list[str]]:
    """Read an OnPay employee export (CSV or xlsx) into roster entries.

    OnPay's exports vary by account, so this matches columns loosely and
    reports what it could not understand rather than guessing.
    """
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        rows = list(workbook.worksheets[0].iter_rows(values_only=True))
        workbook.close()
        header = [str(h or "") for h in (rows[0] if rows else [])]
        body = [dict(zip(header, r)) for r in rows[1:]
                if any(c is not None and str(c).strip() for c in r)]
    else:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            body = list(csv.DictReader(fh))
        header = list(body[0].keys()) if body else []

    lookup = {_squash(h): h for h in header}

    def pick(candidates):
        for key in candidates:
            if key in lookup:
                return lookup[key]
        return None

    name_col = pick(_NAME_HEADERS)
    first_col = pick(_FIRST_HEADERS)
    last_col = pick(_LAST_HEADERS)
    clock_col = pick(_CLOCK_HEADERS)
    id_col = pick(_ID_HEADERS)
    status_col = pick(_STATUS_HEADERS)
    dd_col = pick(_DD_HEADERS)

    problems = []
    if not name_col and not (first_col and last_col):
        problems.append(
            "Could not find a name column in this file, so nobody could be matched. "
            "The app looks for a 'Name' column, or 'First name' and 'Last name'."
        )
        return [], problems
    if not dd_col:
        problems.append(
            "This file has no direct deposit column, so everyone imported will need "
            "their direct deposit status set by hand."
        )

    entries = []
    for row in body:
        if name_col:
            display = str(row.get(name_col) or "").strip()
        else:
            display = f"{str(row.get(first_col) or '').strip()} {str(row.get(last_col) or '').strip()}".strip()
        if not display:
            continue
        if "," in display and not name_col_is_natural(display):
            last, _, first = display.partition(",")
            display = f"{first.strip()} {last.strip()}".strip()

        status = READY
        if status_col:
            raw = str(row.get(status_col) or "").strip().lower()
            if raw in ("inactive", "terminated", "false", "no", "0"):
                status = SETUP_INCOMPLETE
        if dd_col:
            raw = str(row.get(dd_col) or "").strip().lower()
            if raw in ("", "none", "no", "false", "0", "check", "paper check", "manual"):
                status = DIRECT_DEPOSIT_INCOMPLETE

        entries.append(RosterEntry(
            caregiver_key=normalise_name(display),
            display_name=display,
            status=status,
            onpay_clock_user=str(row.get(clock_col) or "").strip() if clock_col else "",
            onpay_employee_id=str(row.get(id_col) or "").strip() if id_col else "",
            source="onpay_import",
        ))
    return entries, problems


def name_col_is_natural(value: str) -> bool:
    """'Smith, Jane' is last-comma-first; 'Jane Smith, Jr' is not."""
    tail = value.split(",")[-1].strip().lower()
    return tail in {"jr", "sr", "ii", "iii", "iv", "jr.", "sr."}
