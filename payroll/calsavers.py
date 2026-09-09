"""The CalSavers contributions to send, read out of OnPay's payroll register.

CalSavers is California's retirement programme. Money is withheld from a
caregiver's cheque by OnPay and then has to be sent to CalSavers separately,
within seven days of the pay date. OnPay does not send it for this account,
so somebody reads the figures off the register and types them into the
CalSavers portal.

Doing that by hand is where it goes wrong. The register's run totals sit at
the end of the document, inside what looks like the last person's section,
so the last caregiver appears to have a deduction equal to everybody's put
together. Read carelessly, that pays a contribution for somebody who has
none and doubles the total.

So nothing here is taken on trust. Every amount found is added up and
checked against the run total the register states for itself, and if the two
do not agree the file is refused rather than half-read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

# The deduction as OnPay names it. Matched loosely because an account may
# call it CalSavers, CalSavers Roth, or carry a suffix.
DEDUCTION = re.compile(r"^\s*(calsavers[^$]*?)\s*\$?([\d,]+\.\d\d)\s*$", re.I)
BLOCK_START = "Employee No."
RUN_DEDUCTIONS = re.compile(r"^\s*Deductions\s*$", re.I)
MONEY = re.compile(r"^\s*\$?([\d,]+\.\d\d)\s*$")
CHECK_DATE = re.compile(r"Check Date\s+(\d{1,2}/\d{1,2}/\d{4})")
PERIOD = re.compile(r"Period\s+(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})")


def money(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


@dataclass
class Contribution:
    name: str
    amount: Decimal

    def to_dict(self) -> dict:
        return {"name": self.name, "amount": str(self.amount)}


@dataclass
class Contributions:
    people: list[Contribution]
    total: Decimal
    pay_date: str = ""
    period: str = ""
    problems: list[str] = None

    def __post_init__(self):
        self.problems = self.problems or []

    @property
    def ok(self) -> bool:
        return not self.problems and bool(self.people)

    def to_dict(self) -> dict:
        return {
            "people": [p.to_dict() for p in self.people],
            "total": str(self.total),
            "pay_date": self.pay_date,
            "period": self.period,
            "problems": self.problems,
            "ok": self.ok,
        }


def register_text(path: Path | str) -> str:
    """The words out of an OnPay register PDF.

    Kept apart from the reading of it so the rules below can be tested
    without a PDF, and so a machine with no PDF reader installed fails here
    with something worth saying rather than deep inside the parsing.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:                       # pragma: no cover - env
        raise RuntimeError(
            "Reading a PDF needs the pypdf package, which is not installed. "
            "In Terminal, run:  python3 -m pip install pypdf"
        ) from exc
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _run_total(lines: list[str]) -> Decimal | None:
    """The deductions total the register states for the whole run.

    It is in the summary at the end, as the word Deductions on its own line
    with the amount underneath. Taken from the last such pair, because the
    per-person sections use the same word as a heading.
    """
    found = None
    for index, line in enumerate(lines[:-1]):
        if RUN_DEDUCTIONS.match(line):
            match = MONEY.match(lines[index + 1])
            if match:
                found = money(match.group(1))
    return found


def read_contributions(text: str) -> Contributions:
    """Who is owed a CalSavers contribution this run, and how much."""
    lines = [line.rstrip() for line in text.splitlines()]
    starts = [i for i, line in enumerate(lines) if line.strip().startswith(BLOCK_START)]
    problems: list[str] = []

    if not starts:
        return Contributions([], Decimal("0"), problems=[
            "This does not look like an OnPay payroll register - there are no "
            "employee sections in it."])

    pay_date = ""
    period = ""
    for line in lines:
        if not pay_date:
            match = CHECK_DATE.search(line)
            if match:
                pay_date = match.group(1)
        if not period:
            match = PERIOD.search(line)
            if match:
                period = f"{match.group(1)} - {match.group(2)}"

    found: list[Contribution] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        name = lines[start - 1].strip() if start else ""
        for line in lines[start:end]:
            match = DEDUCTION.match(line)
            if match:
                try:
                    found.append(Contribution(name, money(match.group(2))))
                except InvalidOperation:
                    problems.append(f"Could not read the amount on this line: {line.strip()}")

    stated = _run_total(lines)
    if stated is None:
        return Contributions([], Decimal("0"), pay_date, period, problems + [
            "The register does not state a deductions total for the run, so there is "
            "nothing to check the amounts against. Nothing was read out of it."])

    # The run totals sit inside the last person's section, so the last
    # caregiver looks like they have a deduction equal to everybody's put
    # together. Drop one occurrence of the run total and see whether what is
    # left adds up to it.
    total = sum((c.amount for c in found), Decimal("0"))
    if found and total == stated * 2:
        for position in range(len(found) - 1, -1, -1):
            if found[position].amount == stated:
                found.pop(position)
                break
        total = sum((c.amount for c in found), Decimal("0"))

    if total != stated:
        problems.append(
            f"The contributions read out of this file come to {total}, but the register "
            f"says the run's deductions are {stated}. Nothing is listed, because a list "
            "that does not add up is worse than no list.")
        return Contributions([], stated, pay_date, period, problems)

    return Contributions(found, stated, pay_date, period, problems)


def read_register(path: Path | str) -> Contributions:
    return read_contributions(register_text(path))
