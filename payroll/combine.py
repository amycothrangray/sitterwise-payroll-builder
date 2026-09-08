"""Putting two monthly exports together for one pay week.

Sitterwise exports a month at a time, and payroll runs Monday to Sunday, so
every few months a pay week lands across a month end - the week of Monday
31 August 2026 needs one day from August's export and six from September's.

This joins them into a single file for that payroll. It keeps the cells
exactly as they were exported, never edits a value, and says out loud what
it did: how many rows came from each file, which bookings were in both, and
any booking whose two copies disagree.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .importer import COLUMN_MAP, _squash, read_workbook


@dataclass
class PartSummary:
    name: str
    rows: int
    first_day: str = ""
    last_day: str = ""
    used: int = 0

    def to_dict(self) -> dict:
        return {"name": self.name, "rows": self.rows, "used": self.used,
                "first_day": self.first_day, "last_day": self.last_day}


@dataclass
class CombineResult:
    path: Path
    parts: list[PartSummary]
    total_rows: int
    seen_twice: list[str] = field(default_factory=list)
    disagreements: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        bits = [f"{p.name} ({p.used} of {p.rows})" for p in self.parts]
        text = f"{self.total_rows} bookings from " + " and ".join(bits)
        if self.seen_twice:
            text += (f". {len(self.seen_twice)} "
                     f"{'booking was' if len(self.seen_twice) == 1 else 'bookings were'} "
                     "in both files and counted once")
        return text + "."

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "parts": [p.to_dict() for p in self.parts],
            "total_rows": self.total_rows,
            "seen_twice": self.seen_twice,
            "disagreements": self.disagreements,
            "problems": self.problems,
            "summary": self.summary,
        }


def _column_for(header: list[str], wanted: str) -> str:
    for column in header:
        if COLUMN_MAP.get(_squash(column)) == wanted:
            return column
    return ""


def _day_of(value) -> str:
    """The date part of a start-date cell, however the export wrote it."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], pattern).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _as_uploaded(path: Path) -> str:
    """The name as it came off the person's computer.

    Uploads are stored with a short content hash in front so two files with
    the same name cannot collide. That prefix is for the app, not for the
    person reading the screen.
    """
    name = path.name
    head, dash, rest = name.partition("-")
    if dash and len(head) == 16 and all(c in "0123456789abcdef" for c in head):
        return rest
    return name


def combine_exports(paths: list[Path | str], out_dir: Path | str) -> CombineResult:
    """Join several exports into one file, keeping every cell as exported.

    A booking in more than one file is kept once. Where the copies differ -
    somebody corrected the hours after the first export was taken - the copy
    from the file given last wins, because that is the more recent picture,
    and the difference is reported rather than quietly chosen.
    """
    paths = [Path(p) for p in paths]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    header: list[str] = []
    by_booking: dict[str, dict] = {}
    order: list[str] = []
    # Which file each booking came from. A booking repeated inside one export
    # is a problem the payroll check reports; removing it here would hide it
    # and quietly drop the pay. Only a repeat from an earlier file is a
    # genuine overlap between two months.
    came_from: dict[str, int] = {}
    parts: list[PartSummary] = []
    seen_twice: list[str] = []
    disagreements: list[dict] = []
    problems: list[str] = []

    for file_index, path in enumerate(paths):
        columns, rows, _ = read_workbook(path)
        for column in columns:
            if column and column not in header:
                header.append(column)

        id_column = _column_for(columns, "booking_id")
        date_column = _column_for(columns, "start_date")
        if not id_column:
            problems.append(
                f"{_as_uploaded(path)} has no Booking ID column, so bookings in it could "
                "not be matched against the other file. It was left out.")
            parts.append(PartSummary(_as_uploaded(path), len(rows)))
            continue

        days = sorted(d for d in (_day_of(r.get(date_column)) for r in rows) if d)
        part = PartSummary(_as_uploaded(path), len(rows),
                           first_day=days[0] if days else "",
                           last_day=days[-1] if days else "")

        for row in rows:
            booking = _text(row.get(id_column))
            if not booking:
                # No id to match on, so it can only be kept as its own row.
                key = f"__row{len(order)}__"
                by_booking[key] = dict(row)
                order.append(key)
                part.used += 1
                continue

            if booking in came_from and came_from[booking] == file_index:
                # Twice in the same export. Keep both rows exactly as the
                # export had them and let the payroll check raise it.
                key = f"__repeat{len(order)}__"
                by_booking[key] = dict(row)
                order.append(key)
                part.used += 1
                continue

            if booking in by_booking:
                previous = by_booking[booking]
                differing = sorted(
                    column for column in set(previous) | set(row)
                    if _text(previous.get(column)) != _text(row.get(column)))
                if differing:
                    disagreements.append({
                        "booking_id": booking,
                        "columns": differing,
                        "kept_from": _as_uploaded(path),
                    })
                    by_booking[booking] = {**previous, **row}
                seen_twice.append(booking)
            else:
                by_booking[booking] = dict(row)
                order.append(booking)
                came_from[booking] = file_index
                part.used += 1

        parts.append(part)

    stamp = "-".join(Path(_as_uploaded(p)).stem[:18] for p in paths) or "combined"
    target = out_dir / f"combined-{stamp}.csv"
    with open(target, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for key in order:
            row = by_booking[key]
            writer.writerow({column: _cell(row.get(column)) for column in header})

    return CombineResult(
        path=target, parts=parts, total_rows=len(order),
        seen_twice=sorted(set(seen_twice)), disagreements=disagreements,
        problems=problems,
    )


def _cell(value):
    """Write a cell back the way the export had it."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value
