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
    # Somebody's legal name in OnPay is often not the name Sitterwise shows -
    # Lissa's OnPay record is under Elisabeth R Gray. Without somewhere to
    # record that, importing OnPay's employee list makes a second roster
    # entry for the same person and then reports the first as missing.
    onpay_name: str = ""
    # What OnPay says it pays this person. Kept as text exactly as exported so
    # nothing is rounded on the way in. It is shown beside them, never used to
    # pay anybody: a rate is a thing Amy agreed with a caregiver, so the app
    # reports a disagreement rather than picking a side.
    onpay_rate: str = ""
    onpay_pay_type: str = ""
    onpay_pay_frequency: str = ""
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
_MIDDLE_HEADERS = {"middle", "middlename", "middleinitial"}
_LAST_HEADERS = {"last", "lastname", "employeelastname", "legallastname"}
_CLOCK_HEADERS = {"clockuser", "clockuserid", "clockid", "externalid"}
_ID_HEADERS = {"employeeid", "id", "employeenumber", "empid", "empnum"}
_STATUS_HEADERS = {"status", "employmentstatus", "employeestatus", "active"}
_END_HEADERS = {"terminationdate", "termdate", "enddate", "separationdate"}
_RATE_HEADERS = {"rate", "payrate", "hourlyrate", "primaryrate"}
_HIRE_HEADERS = {"hiredate", "startdate", "datehired"}
_TYPE_HEADERS = {"type", "paytype", "employmenttype"}
_FREQUENCY_HEADERS = {"payfrequency", "frequency", "payperiod"}
_DD_HEADERS = {
    "directdeposit", "directdepositstatus", "paymentmethod", "paymethod",
    "hasdirectdeposit", "bankaccount", "bankaccounts",
}

# A sheet of people who have left. OnPay's Employee Detail report puts them
# on their own tab rather than in a column.
_INACTIVE_SHEETS = ("inactive", "terminated", "former", "archived")

# How far down to look for the row of column names. OnPay's report opens with
# a title, the company name and the date it was run, so the header is not the
# first row of the sheet.
_HEADER_SEARCH_ROWS = 25


def _squash(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _looks_like_a_header(cells) -> bool:
    """A row of column names, rather than a title or somebody's details."""
    # A row naming a name column is a header however few columns it has. A
    # file of nothing but names and Clock Users - which is all the roster
    # needs - is two columns wide.
    squashed = {_squash(c) for c in cells if c is not None and str(c).strip()}
    return bool(squashed & _NAME_HEADERS
                or (squashed & _FIRST_HEADERS and squashed & _LAST_HEADERS))


def _table(rows) -> tuple[list[str], list[dict]]:
    """Split a sheet into its column names and the people underneath them."""
    for index, cells in enumerate(rows[:_HEADER_SEARCH_ROWS]):
        if _looks_like_a_header(cells):
            header = [str(h or "") for h in cells]
            body = [r for r in rows[index + 1:]
                    if any(c is not None and str(c).strip() for c in r)]
            return header, body
    return [], []


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _sheets(path: Path) -> list[tuple[str, list[str], list[dict]]]:
    """Every sheet of an export, as (sheet name, columns, people)."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        out = []
        for sheet in workbook.worksheets:
            header, body = _table(list(sheet.iter_rows(values_only=True)))
            if header:
                out.append((sheet.title, header,
                            [dict(zip(header, r)) for r in body]))
        workbook.close()
        return out
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [tuple(r) for r in csv.reader(fh)]
    header, body = _table(rows)
    if not header:
        return []
    # A name written Okafor, Tess without quotes around it splits into two
    # fields and shoves every column after it one place across, so the Clock
    # User read for somebody would be a piece of their own name. A row wider
    # than its header is that, and it is not safe to guess at.
    wide = sum(1 for row in body if len(row) > len(header))
    if wide:
        raise ValueError(
            f"{wide} {'row has' if wide == 1 else 'rows have'} more values than there "
            f"are columns. A name with a comma in it needs quotes around it, "
            f'like "Okafor, Tess" - without them the columns after it shift across '
            "and the wrong value is read for each one.")
    return [("", header, [dict(zip(header, r)) for r in body])]


def _one_row_each(found: list[tuple[RosterEntry, bool, str]],
                  problems: list[str]) -> tuple[list[RosterEntry], list[str]]:
    """Cut the file down to one row per person, or say why it could not be.

    Two rows under one name is not always two people. Somebody who left and
    came back has a closed record and a live one, and the live one is the
    truth. Two live records is a different thing: Sitterwise really does have
    two Maria Brants, on different rates. Nothing can be filled in safely for
    either of them from a name, so they are left for a person and said out
    loud rather than matched to whichever came first.
    """
    grouped: dict[str, list[tuple[RosterEntry, bool, str]]] = {}
    for entry, has_left, told_apart_by in found:
        grouped.setdefault(entry.caregiver_key, []).append((entry, has_left, told_apart_by))

    entries: list[RosterEntry] = []
    ambiguous: list[str] = []
    for rows in grouped.values():
        here = [entry for entry, has_left, _ in rows if not has_left]
        if len(rows) == 1:
            entries.append(rows[0][0])
        elif len(here) == 1:
            entry = here[0]
            entry.note = (entry.note + " OnPay also has a closed record "
                          "under this name.").strip()
            entries.append(entry)
        else:
            # Say what actually differs. Being told two people share a name
            # only sends somebody back to OnPay to find out which is which.
            told = [t for _, _, t in rows if t]
            ambiguous.append(f"{rows[0][0].display_name}"
                             + (f" ({'; '.join(told)})" if told else ""))

    if ambiguous:
        problems.append(
            f"More than one person is in this file under "
            f"{'this name' if len(ambiguous) == 1 else 'each of these names'}: "
            f"{', '.join(sorted(ambiguous))}. Nothing was filled in for them - "
            "say which is which on their row so nobody is paid at the other "
            "one's rate.")
    return entries, problems


def parse_onpay_employee_export(path: Path | str) -> tuple[list[RosterEntry], list[str]]:
    """Read an OnPay employee export (CSV or xlsx) into roster entries.

    OnPay's exports vary by account, so this matches columns loosely and
    reports what it could not understand rather than guessing.
    """
    path = Path(path)
    sheets = _sheets(path)
    problems: list[str] = []
    if not sheets:
        return [], [
            "Could not find a row of column names in this file, so nobody could be "
            "matched. The app looks for a 'Name' column, or 'First name' and "
            "'Last name'."
        ]

    # each person, whether they have left, and what tells them apart from
    # somebody else of the same name
    found: list[tuple[RosterEntry, bool, str]] = []
    identified = 0
    saw_direct_deposit = False

    for sheet_name, header, body in sheets:
        lookup = {_squash(h): h for h in header}

        def pick(candidates):
            for key in candidates:
                if key in lookup:
                    return lookup[key]
            return None

        name_col = pick(_NAME_HEADERS)
        first_col, last_col = pick(_FIRST_HEADERS), pick(_LAST_HEADERS)
        middle_col = pick(_MIDDLE_HEADERS)
        clock_col, id_col = pick(_CLOCK_HEADERS), pick(_ID_HEADERS)
        status_col, end_col = pick(_STATUS_HEADERS), pick(_END_HEADERS)
        rate_col, type_col = pick(_RATE_HEADERS), pick(_TYPE_HEADERS)
        hire_col = pick(_HIRE_HEADERS)
        frequency_col = pick(_FREQUENCY_HEADERS)
        dd_col = pick(_DD_HEADERS)
        saw_direct_deposit = saw_direct_deposit or bool(dd_col)

        sheet_is_past = any(word in sheet_name.lower() for word in _INACTIVE_SHEETS)

        for row in body:
            if name_col:
                display = _text(row.get(name_col))
            else:
                display = " ".join(
                    part for part in (_text(row.get(first_col)), _text(row.get(last_col)))
                    if part)
            if not display:
                continue
            if "," in display and not name_col_is_natural(display):
                last, _, first = display.partition(",")
                display = f"{first.strip()} {last.strip()}".strip()

            clock = _text(row.get(clock_col)) if clock_col else ""
            employee_id = _text(row.get(id_col)) if id_col else ""
            if clock or employee_id:
                identified += 1

            # Somebody who has left, either because this whole sheet is the
            # leavers or because their record carries a leaving date.
            has_left = sheet_is_past or (bool(_text(row.get(end_col))) if end_col else False)

            status = READY
            if has_left:
                status = SETUP_INCOMPLETE
            elif status_col:
                raw = _text(row.get(status_col)).lower()
                if raw in ("inactive", "terminated", "false", "no", "0"):
                    status = SETUP_INCOMPLETE
            if dd_col and status == READY:
                raw = _text(row.get(dd_col)).lower()
                if raw in ("", "none", "no", "false", "0", "check", "paper check", "manual"):
                    status = DIRECT_DEPOSIT_INCOMPLETE

            # OnPay's own name for them, middle name and all. This is what a
            # roster entry is matched on when Sitterwise calls somebody
            # something else - Lissa's OnPay record is Elisabeth R Gray.
            legal = " ".join(filter(None, [
                _text(row.get(first_col)), _text(row.get(middle_col)) if middle_col else "",
                _text(row.get(last_col))])) if not name_col else display
            note = "Marked as no longer employed in OnPay." if has_left else ""

            told_apart_by = ", ".join(filter(None, [
                f"${_text(row.get(rate_col))}" if rate_col and _text(row.get(rate_col)) else "",
                f"hired {_text(row.get(hire_col))[:10]}" if hire_col and _text(row.get(hire_col)) else "",
            ]))
            found.append((RosterEntry(
                caregiver_key=normalise_name(display),
                display_name=display,
                onpay_name=legal if legal and legal != display else "",
                status=status,
                onpay_clock_user=clock,
                onpay_employee_id=employee_id,
                onpay_rate=_text(row.get(rate_col)) if rate_col else "",
                onpay_pay_type=_text(row.get(type_col)) if type_col else "",
                onpay_pay_frequency=_text(row.get(frequency_col)) if frequency_col else "",
                note=note,
                source="onpay_import",
            ), has_left, told_apart_by))

    if not found:
        return [], ["This file has column names but no people underneath them."]

    entries, problems = _one_row_each(found, problems)
    if not entries:
        # Everybody in it needed a person to sort out. The reason is already
        # in problems; losing it here would leave the screen saying the file
        # was empty, which it was not.
        return [], problems

    if not identified:
        problems.append(
            "Nobody in this file has a Clock User or an Employee Number. That is the "
            "number the OnPay import file identifies people by, so this export can "
            "fill in names and rates but cannot make anybody ready to pay. In OnPay, "
            "the report that carries it is the one to export instead.")
        for entry in entries:
            if entry.status == READY:
                entry.status = SETUP_INCOMPLETE
    if not saw_direct_deposit:
        problems.append(
            "This file has no direct deposit column, so direct deposit is not "
            "something the app can confirm from it.")
    return entries, problems


def name_col_is_natural(value: str) -> bool:
    """'Smith, Jane' is last-comma-first; 'Jane Smith, Jr' is not."""
    tail = value.split(",")[-1].strip().lower()
    return tail in {"jr", "sr", "ii", "iii", "iv", "jr.", "sr."}


# --- folding an import into the roster already here -------------------------

def merge_import(existing: dict[str, RosterEntry],
                 entries: list[RosterEntry]) -> tuple[list[RosterEntry], dict]:
    """Work out what an OnPay export changes about the roster.

    Nothing is written here and nothing is decided quietly. An import fills
    in blanks and brings across what OnPay knows - a legal name, a rate - and
    leaves alone anything a person put on the roster themselves.
    """
    # OnPay knows people by their legal name, which is often not the name
    # Sitterwise shows - Lissa's OnPay record is Elisabeth R Gray. Matching on
    # the name alone would make a second roster entry for the same person and
    # then report the first as missing from OnPay. So a Clock User, an
    # employee id, or a legal name already recorded all count as the same one.
    by_clock = {e.onpay_clock_user.strip().casefold(): key
                for key, e in existing.items() if e.onpay_clock_user.strip()}
    by_emp_id = {e.onpay_employee_id.strip().casefold(): key
                 for key, e in existing.items() if e.onpay_employee_id.strip()}
    by_onpay_name = {normalise_name(e.onpay_name): key
                     for key, e in existing.items() if e.onpay_name.strip()}

    def already_known_as(entry: RosterEntry) -> str:
        for table, value in (
            (by_clock, entry.onpay_clock_user.strip().casefold()),
            (by_emp_id, entry.onpay_employee_id.strip().casefold()),
            (by_onpay_name, entry.caregiver_key),
            (by_onpay_name, normalise_name(entry.onpay_name)),
        ):
            if value and value in table:
                return table[value]
        return ""

    to_save: list[RosterEntry] = []
    linked = 0
    in_onpay_only: list[str] = []
    matched: set[str] = set()

    for entry in entries:
        onpay_name = entry.display_name
        key = entry.caregiver_key
        if key not in existing:
            other = already_known_as(entry)
            if other:
                key = other
                linked += 1
        if key not in existing:
            # In OnPay, but has never worked a Sitterwise booking. Adding them
            # would bury the handful of caregivers this payroll is about under
            # everybody the company has ever hired. They are counted and named
            # back, not dropped silently - and the moment one of them works,
            # they get a roster entry and the next import fills them in.
            in_onpay_only.append(entry.display_name)
            continue

        previous = existing[key]
        entry.caregiver_key = key
        entry.display_name = previous.display_name
        if onpay_name != previous.display_name:
            # Keep the fullest version of OnPay's name, so the next import
            # still recognises them.
            entry.onpay_name = entry.onpay_name or onpay_name

        # Fill in the blanks; never rub out something a person put there. This
        # export carries no Clock User at all, so importing it must not wipe
        # the ones Amy typed in - nor knock somebody back to "setup
        # incomplete" when the file simply has nothing to say about them.
        for field in ("onpay_clock_user", "onpay_employee_id", "onpay_rate",
                      "onpay_pay_type", "onpay_pay_frequency"):
            if not getattr(entry, field):
                setattr(entry, field, getattr(previous, field))
        if entry.status != READY and (previous.onpay_clock_user
                                      or previous.onpay_employee_id):
            entry.status = previous.status

        # A note somebody wrote is theirs. One the app wrote for itself is
        # replaced by whatever the import has to say instead.
        app_wrote_it = (previous.source == "added_automatically"
                        or previous.note.startswith("Added automatically"))
        if previous.note and not app_wrote_it:
            entry.note = previous.note

        to_save.append(entry)
        matched.add(key)

    return to_save, {
        "updated": len(to_save),
        "linked": linked,
        "in_onpay_only": len(in_onpay_only),
        "not_in_onpay_file": sorted(e.display_name for key, e in existing.items()
                                    if key not in matched),
    }


# --- handing out Clock Users -----------------------------------------------

# Where a made-up Clock User starts. Four digits, clear of anything OnPay
# might use for itself, and short enough to read off a screen and type.
FIRST_CLOCK_USER = 1001


def assign_clock_users(roster: dict[str, RosterEntry],
                       start: int = FIRST_CLOCK_USER) -> list[RosterEntry]:
    """Give a Clock User to everybody on the roster who has not got one.

    OnPay confirmed on 8 September 2026 that the payroll import identifies
    people by Clock User, that it is required, and that there is no way to
    load them in bulk - each one is typed into that person's OnPay profile by
    hand. So the numbers are ours to choose, and the app holds the same ones
    it hands over, rather than somebody keeping a list in two places.

    A Clock User already recorded is never changed and never handed out
    again, including one belonging to somebody who has left. A number that
    has been used is spent: giving it to a second person would put one
    caregiver's hours on another one's pay.
    """
    taken = set()
    for entry in roster.values():
        number = entry.onpay_clock_user.strip()
        if number:
            taken.add(number)

    next_number = start
    while str(next_number) in taken:
        next_number += 1

    assigned = []
    # In the order they are shown, so the list somebody types from reads down
    # the same way the roster does.
    for entry in sorted(roster.values(), key=lambda e: e.display_name.casefold()):
        if entry.onpay_clock_user.strip():
            continue
        entry.onpay_clock_user = str(next_number)
        assigned.append(entry)
        taken.add(str(next_number))
        while str(next_number) in taken:
            next_number += 1
    return assigned


def compare_clock_users(roster: dict[str, RosterEntry],
                        entries: list[RosterEntry]) -> dict:
    """Check the Clock Users in OnPay against the ones the app handed out.

    They are typed into OnPay one profile at a time, sixty-odd times, and a
    typo does not announce itself. A number that matches nobody makes that
    caregiver's pay line fail to import, which is loud and recoverable. A
    number that matches somebody ELSE pays one caregiver's hours to another,
    which is neither. So every number is read back and compared, and this
    says which of the two happened.

    Nothing is saved. This only reports.
    """
    ours = {key: e.onpay_clock_user.strip() for key, e in roster.items()
            if e.onpay_clock_user.strip()}
    by_number: dict[str, list[str]] = {}
    for key, number in ours.items():
        by_number.setdefault(number, []).append(roster[key].display_name)

    def known_as(entry: RosterEntry) -> str:
        """The roster entry this read-back row is about.

        Matched on the name shown, the legal name OnPay holds them under, or
        the key itself - the file comes back with whatever OnPay calls them,
        which for Lissa is Elisabeth R Gray.
        """
        if entry.caregiver_key in roster:
            return entry.caregiver_key
        wanted = normalise_name(entry.display_name)
        for key, e in roster.items():
            if wanted in (normalise_name(e.display_name),
                          normalise_name(e.onpay_name), e.caregiver_key):
                return key
        return ""

    agree, wrong, unknown = [], [], []
    seen: set[str] = set()
    for entry in entries:
        key = known_as(entry)
        theirs = entry.onpay_clock_user.strip()
        if not key:
            unknown.append({"name": entry.display_name, "onpay": theirs,
                            "why": "nobody on the roster goes by this name"})
            continue
        seen.add(key)
        mine = ours.get(key, "")
        if not mine:
            unknown.append({"name": roster[key].display_name, "onpay": theirs,
                            "why": "the app has not given this caregiver a number"})
        elif theirs == mine:
            agree.append({"name": roster[key].display_name, "clock_user": mine})
        else:
            # Whose number did they actually get? That is the difference
            # between a line that fails to import and one that pays the
            # wrong person.
            belongs_to = [n for n in by_number.get(theirs, [])
                          if n != roster[key].display_name]
            wrong.append({
                "name": roster[key].display_name, "ours": mine, "onpay": theirs,
                "why": (f"that number belongs to {', '.join(belongs_to)}"
                        if belongs_to else "that number belongs to nobody"),
                "pays_someone_else": bool(belongs_to),
            })

    missing = [{"name": e.display_name, "ours": ours[key]}
               for key, e in roster.items() if key in ours and key not in seen]
    return {"agree": agree, "wrong": wrong, "unknown": unknown, "missing": missing,
            "checked": len(entries)}
