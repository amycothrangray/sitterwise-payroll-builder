"""Where payroll runs are kept.

A small SQLite file next to the app. It holds finished and in-progress
payroll runs, the caregiver roster, every manual adjustment, and an audit
trail of anything Amy changed by hand.

Nothing here ever alters an imported booking. Corrections are stored as
adjustments layered on top, so the original export can always be seen.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from .engine import Adjustment
from .roster import RosterEntry, READY, SETUP_INCOMPLETE

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',   -- open | finalized
    created_at      TEXT NOT NULL,
    finalized_at    TEXT,
    source_filename TEXT,
    source_sha256   TEXT,
    source_path     TEXT,
    rules_snapshot  TEXT NOT NULL,
    rules_version   TEXT,
    totals_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS paid_bookings (
    booking_id  TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    PRIMARY KEY (booking_id, run_id)
);

CREATE TABLE IF NOT EXISTS adjustments (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    caregiver_key  TEXT NOT NULL,
    kind           TEXT NOT NULL,
    booking_id     TEXT DEFAULT '',
    original_value TEXT DEFAULT '',
    new_value      TEXT DEFAULT '',
    reason         TEXT DEFAULT '',
    taxable        INTEGER DEFAULT 1,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_progress (
    run_id        TEXT NOT NULL,
    caregiver_key TEXT NOT NULL,
    entered       INTEGER NOT NULL DEFAULT 0,
    entered_at    TEXT,
    PRIMARY KEY (run_id, caregiver_key)
);

CREATE TABLE IF NOT EXISTS roster (
    caregiver_key     TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    status            TEXT NOT NULL,
    onpay_clock_user  TEXT DEFAULT '',
    onpay_employee_id TEXT DEFAULT '',
    onpay_name        TEXT DEFAULT '',
    note              TEXT DEFAULT '',
    updated_at        TEXT,
    source            TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    run_id     TEXT,
    action     TEXT NOT NULL,
    detail     TEXT
);

-- Things to remember on a future payroll. Replaces the "Payroll Odds &
-- Ends" spreadsheet. A note is written whenever somebody notices something;
-- it waits here until the payroll it belongs to is run, and is then applied
-- as an ordinary adjustment so it lands in the same audit trail as
-- everything else.
CREATE TABLE IF NOT EXISTS notes (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    created_by     TEXT DEFAULT '',
    kind           TEXT NOT NULL,
    caregiver_key  TEXT DEFAULT '',
    caregiver_name TEXT DEFAULT '',
    booking_id     TEXT DEFAULT '',
    amount         TEXT DEFAULT '',
    taxable        INTEGER DEFAULT 1,
    detail         TEXT DEFAULT '',
    applies_to     TEXT DEFAULT 'next',
    status         TEXT NOT NULL DEFAULT 'open',
    applied_run_id TEXT DEFAULT '',
    applied_at     TEXT DEFAULT '',
    resolved_by    TEXT DEFAULT ''
);

-- People paid for work that never appears in a bookings export: a monthly
-- salary, admin hours, phone days, training. Each entry produces its own
-- payroll line on the periods it is due.
CREATE TABLE IF NOT EXISTS recurring_pay (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    person_name   TEXT NOT NULL,
    caregiver_key TEXT NOT NULL,
    amount        TEXT NOT NULL,
    frequency     TEXT NOT NULL DEFAULT 'monthly',
    schedule      TEXT NOT NULL DEFAULT 'first_monday',
    taxable       INTEGER DEFAULT 1,
    active        INTEGER DEFAULT 1,
    note          TEXT DEFAULT ''
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DATA_DIR / "payroll.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._add_missing_columns()
        self.db.commit()

    def _add_missing_columns(self) -> None:
        """Bring an older payroll database up to date.

        CREATE TABLE IF NOT EXISTS leaves an existing table alone, so a
        column added after somebody started using the app has to be added
        here or their database quietly lacks it.
        """
        wanted = {"roster": {"onpay_name": "TEXT DEFAULT ''"}}
        for table, columns in wanted.items():
            have = {row["name"] for row in
                    self.db.execute(f"PRAGMA table_info({table})")}
            for name, spec in columns.items():
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")
                    self.log("database_updated", f"added {table}.{name}")

    def close(self) -> None:
        self.db.close()

    # -- audit ----------------------------------------------------------
    def log(self, action: str, detail: str = "", run_id: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO audit_log (at, run_id, action, detail) VALUES (?,?,?,?)",
            (now(), run_id, action, detail))
        self.db.commit()

    def audit_trail(self, run_id: str | None = None, limit: int = 500) -> list[dict]:
        if run_id:
            rows = self.db.execute(
                "SELECT * FROM audit_log WHERE run_id=? ORDER BY id DESC LIMIT ?",
                (run_id, limit))
        else:
            rows = self.db.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -- runs -----------------------------------------------------------
    def create_run(self, label, period_start: date, period_end: date, rules_snapshot: dict,
                   source_filename: str, source_sha256: str, source_path: str) -> str:
        run_id = uuid.uuid4().hex[:12]
        self.db.execute(
            """INSERT INTO runs (id,label,period_start,period_end,status,created_at,
                                 source_filename,source_sha256,source_path,
                                 rules_snapshot,rules_version)
               VALUES (?,?,?,?,'open',?,?,?,?,?,?)""",
            (run_id, label, period_start.isoformat(), period_end.isoformat(), now(),
             source_filename, source_sha256, source_path,
             json.dumps(rules_snapshot), str(rules_snapshot.get("version", ""))))
        self.db.commit()
        self.log("run_created", f"{label} from {source_filename}", run_id)
        return run_id

    def get_run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM runs ORDER BY period_start DESC, created_at DESC")
        return [dict(r) for r in rows]

    def delete_run(self, run_id: str) -> None:
        run = self.get_run(run_id)
        if not run:
            return
        if run["status"] == "finalized":
            raise ValueError("This payroll is locked. Unlock it before deleting it.")
        for table in ("paid_bookings", "adjustments", "entry_progress"):
            self.db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
        self.db.execute("DELETE FROM runs WHERE id=?", (run_id,))
        self.db.commit()
        self.log("run_deleted", run["label"], run_id)

    def finalize_run(self, run_id: str, booking_ids: list[str], totals: dict) -> None:
        self.db.execute(
            "UPDATE runs SET status='finalized', finalized_at=?, totals_snapshot=? WHERE id=?",
            (now(), json.dumps(totals), run_id))
        self.db.executemany(
            "INSERT OR REPLACE INTO paid_bookings (booking_id, run_id) VALUES (?,?)",
            [(b, run_id) for b in booking_ids])
        self.db.commit()
        self.log("run_finalized", f"{len(booking_ids)} bookings locked", run_id)

    def unlock_run(self, run_id: str, reason: str) -> None:
        self.db.execute(
            "UPDATE runs SET status='open', finalized_at=NULL WHERE id=?", (run_id,))
        self.db.execute("DELETE FROM paid_bookings WHERE run_id=?", (run_id,))
        self.db.commit()
        self.log("run_unlocked", reason or "no reason given", run_id)

    def previously_paid(self, exclude_run_id: str | None = None) -> dict[str, str]:
        """Booking id -> label of the finalised run that already paid it."""
        query = ("SELECT p.booking_id, r.label FROM paid_bookings p "
                 "JOIN runs r ON r.id = p.run_id WHERE r.status='finalized'")
        params: tuple = ()
        if exclude_run_id:
            query += " AND p.run_id != ?"
            params = (exclude_run_id,)
        return {r["booking_id"]: r["label"] for r in self.db.execute(query, params)}

    # -- adjustments ----------------------------------------------------
    def add_adjustment(self, run_id: str, adj: Adjustment) -> str:
        adj.id = adj.id or uuid.uuid4().hex[:12]
        adj.created_at = adj.created_at or now()
        self.db.execute(
            """INSERT INTO adjustments (id,run_id,caregiver_key,kind,booking_id,
                                        original_value,new_value,reason,taxable,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (adj.id, run_id, adj.caregiver_key, adj.kind, adj.booking_id,
             adj.original_value, adj.new_value, adj.reason, int(adj.taxable), adj.created_at))
        self.db.commit()
        self.log("adjustment_added",
                 f"{adj.caregiver_key}: {adj.kind} {adj.original_value} -> {adj.new_value} "
                 f"({adj.reason or 'no reason given'})", run_id)
        return adj.id

    def remove_adjustment(self, run_id: str, adjustment_id: str) -> None:
        row = self.db.execute("SELECT * FROM adjustments WHERE id=? AND run_id=?",
                              (adjustment_id, run_id)).fetchone()
        self.db.execute("DELETE FROM adjustments WHERE id=? AND run_id=?",
                        (adjustment_id, run_id))
        self.db.commit()
        if row:
            self.log("adjustment_removed",
                     f"{row['caregiver_key']}: {row['kind']} back to {row['original_value']}",
                     run_id)

    def adjustments(self, run_id: str) -> list[Adjustment]:
        rows = self.db.execute("SELECT * FROM adjustments WHERE run_id=? ORDER BY created_at",
                               (run_id,))
        return [Adjustment(
            id=r["id"], caregiver_key=r["caregiver_key"], kind=r["kind"],
            booking_id=r["booking_id"] or "", original_value=r["original_value"] or "",
            new_value=r["new_value"] or "", reason=r["reason"] or "",
            created_at=r["created_at"], taxable=bool(r["taxable"]),
        ) for r in rows]

    # -- OnPay entry progress -------------------------------------------
    def set_entered(self, run_id: str, caregiver_key: str, entered: bool) -> None:
        self.db.execute(
            """INSERT INTO entry_progress (run_id,caregiver_key,entered,entered_at)
               VALUES (?,?,?,?)
               ON CONFLICT(run_id,caregiver_key)
               DO UPDATE SET entered=excluded.entered, entered_at=excluded.entered_at""",
            (run_id, caregiver_key, int(entered), now() if entered else None))
        self.db.commit()

    def entered_map(self, run_id: str) -> dict[str, bool]:
        rows = self.db.execute(
            "SELECT caregiver_key, entered FROM entry_progress WHERE run_id=?", (run_id,))
        return {r["caregiver_key"]: bool(r["entered"]) for r in rows}

    # -- roster ---------------------------------------------------------
    def roster(self) -> dict[str, RosterEntry]:
        rows = self.db.execute("SELECT * FROM roster ORDER BY display_name")
        return {r["caregiver_key"]: RosterEntry(
            caregiver_key=r["caregiver_key"], display_name=r["display_name"],
            status=r["status"], onpay_clock_user=r["onpay_clock_user"] or "",
            onpay_employee_id=r["onpay_employee_id"] or "",
            onpay_name=r["onpay_name"] or "", note=r["note"] or "",
            updated_at=r["updated_at"] or "", source=r["source"] or "manual",
        ) for r in rows}

    def upsert_roster_entry(self, entry: RosterEntry, quiet: bool = False) -> None:
        existing = self.db.execute(
            "SELECT status FROM roster WHERE caregiver_key=?", (entry.caregiver_key,)).fetchone()
        entry.updated_at = now()
        self.db.execute(
            """INSERT INTO roster (caregiver_key,display_name,status,onpay_clock_user,
                                   onpay_employee_id,onpay_name,note,updated_at,source)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(caregiver_key) DO UPDATE SET
                 display_name=excluded.display_name, status=excluded.status,
                 onpay_clock_user=excluded.onpay_clock_user,
                 onpay_employee_id=excluded.onpay_employee_id,
                 onpay_name=excluded.onpay_name,
                 note=excluded.note, updated_at=excluded.updated_at,
                 source=excluded.source""",
            (entry.caregiver_key, entry.display_name, entry.status, entry.onpay_clock_user,
             entry.onpay_employee_id, entry.onpay_name, entry.note, entry.updated_at,
             entry.source))
        self.db.commit()
        if not quiet and (not existing or existing["status"] != entry.status):
            was = existing["status"] if existing else "not on the roster"
            self.log("roster_updated", f"{entry.display_name}: {was} -> {entry.status}")

    def ensure_roster_entries(self, people: list[tuple[str, str]]) -> int:
        """Add anyone being paid who is not on the roster yet.

        They come in as "OnPay Setup Incomplete", which means "nobody has told
        the app yet" rather than "definitely not set up". That shows up for
        review without blocking payroll, because the app genuinely does not
        know. Marking somebody "Not in OnPay" is a deliberate act by Amy, and
        that does block.
        """
        added = 0
        known = set(self.roster())
        for key, name in people:
            if key and key not in known:
                self.upsert_roster_entry(
                    RosterEntry(caregiver_key=key, display_name=name,
                                status=SETUP_INCOMPLETE, source="added_automatically",
                                note="Added automatically - confirm their OnPay setup"),
                    quiet=True)
                known.add(key)
                added += 1
        if added:
            self.log("roster_seeded",
                     f"{added} caregivers added to the roster, awaiting their OnPay status")
        return added

    # -- payroll notes ---------------------------------------------------
    # The old "Payroll Odds & Ends" sheet, with the retyping taken out. A
    # note is not a reminder to do arithmetic later; it carries the numbers,
    # and the run it belongs to applies it.
    def add_note(self, note: dict) -> str:
        note_id = note.get("id") or uuid.uuid4().hex[:12]
        self.db.execute(
            """INSERT INTO notes (id,created_at,created_by,kind,caregiver_key,
                                  caregiver_name,booking_id,amount,taxable,detail,
                                  applies_to,status,applied_run_id,applied_at,resolved_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'','','')""",
            (note_id, note.get("created_at") or now(), note.get("created_by", ""),
             note["kind"], note.get("caregiver_key", ""), note.get("caregiver_name", ""),
             note.get("booking_id", ""), str(note.get("amount", "")),
             int(bool(note.get("taxable", True))), note.get("detail", ""),
             note.get("applies_to") or "next", note.get("status") or "open"))
        self.db.commit()
        who = note.get("caregiver_name") or "no one in particular"
        self.log("note_added", f"{note['kind']} for {who}: {note.get('detail', '')}")
        return note_id

    def list_notes(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.db.execute(
                "SELECT * FROM notes WHERE status=? ORDER BY created_at DESC", (status,))
        else:
            rows = self.db.execute("SELECT * FROM notes ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    def get_note(self, note_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return dict(row) if row else None

    def update_note(self, note_id: str, fields: dict) -> None:
        allowed = ("kind", "caregiver_key", "caregiver_name", "booking_id", "amount",
                   "taxable", "detail", "applies_to", "status")
        sets, values = [], []
        for key in allowed:
            if key in fields:
                sets.append(f"{key}=?")
                values.append(int(bool(fields[key])) if key == "taxable"
                              else str(fields[key]))
        if not sets:
            return
        values.append(note_id)
        self.db.execute(f"UPDATE notes SET {','.join(sets)} WHERE id=?", values)
        self.db.commit()
        self.log("note_edited", f"note {note_id}: {', '.join(sets)}")

    def delete_note(self, note_id: str) -> None:
        row = self.get_note(note_id)
        self.db.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self.db.commit()
        if row:
            self.log("note_deleted", f"{row['kind']}: {row['detail']}")

    def mark_note_applied(self, note_id: str, run_id: str, who: str = "") -> None:
        self.db.execute(
            "UPDATE notes SET status='applied', applied_run_id=?, applied_at=?, "
            "resolved_by=? WHERE id=?", (run_id, now(), who, note_id))
        self.db.commit()
        self.log("note_applied", f"note {note_id} went into this payroll", run_id)

    def reopen_note(self, note_id: str) -> None:
        """A run was deleted or unlocked, so its notes are waiting again."""
        self.db.execute(
            "UPDATE notes SET status='open', applied_run_id='', applied_at='', "
            "resolved_by='' WHERE id=?", (note_id,))
        self.db.commit()

    def notes_for_run(self, run_id: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM notes WHERE applied_run_id=?", (run_id,))
        return [dict(r) for r in rows]

    # -- recurring and non-booking pay -----------------------------------
    def add_recurring(self, entry: dict) -> str:
        entry_id = entry.get("id") or uuid.uuid4().hex[:12]
        self.db.execute(
            """INSERT INTO recurring_pay (id,created_at,person_name,caregiver_key,amount,
                                          frequency,schedule,taxable,active,note)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (entry_id, entry.get("created_at") or now(), entry["person_name"],
             entry["caregiver_key"], str(entry["amount"]),
             entry.get("frequency", "monthly"), entry.get("schedule", "first_monday"),
             int(bool(entry.get("taxable", True))), int(bool(entry.get("active", True))),
             entry.get("note", "")))
        self.db.commit()
        self.log("recurring_added",
                 f"{entry['person_name']}: {entry['amount']} {entry.get('frequency', 'monthly')}")
        return entry_id

    def list_recurring(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM recurring_pay"
        if active_only:
            sql += " WHERE active=1"
        return [dict(r) for r in self.db.execute(sql + " ORDER BY person_name")]

    def update_recurring(self, entry_id: str, fields: dict) -> None:
        allowed = ("person_name", "caregiver_key", "amount", "frequency", "schedule",
                   "taxable", "active", "note")
        sets, values = [], []
        for key in allowed:
            if key in fields:
                sets.append(f"{key}=?")
                values.append(int(bool(fields[key])) if key in ("taxable", "active")
                              else str(fields[key]))
        if not sets:
            return
        values.append(entry_id)
        self.db.execute(f"UPDATE recurring_pay SET {','.join(sets)} WHERE id=?", values)
        self.db.commit()
        self.log("recurring_edited", f"{entry_id}: {', '.join(sets)}")

    def delete_recurring(self, entry_id: str) -> None:
        row = self.db.execute("SELECT * FROM recurring_pay WHERE id=?",
                              (entry_id,)).fetchone()
        self.db.execute("DELETE FROM recurring_pay WHERE id=?", (entry_id,))
        self.db.commit()
        if row:
            self.log("recurring_deleted", f"{row['person_name']}: {row['amount']}")
