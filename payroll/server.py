"""The local web server behind the app.

Runs on Amy's own machine. No accounts, no cloud, nothing leaves the laptop.
Uploaded exports are kept in data/uploads so a payroll run can always be
rebuilt from its original file.
"""
from __future__ import annotations

import errno
import hashlib
import json
import mimetypes
import re
import shutil
import threading
import urllib.error
import urllib.request
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import exports, extras
from .engine import Adjustment
from .importer import import_export
from .roster import (NOT_IN_ONPAY, READY, RosterEntry, STATUS_LABELS,
                     normalise_name, parse_onpay_employee_export)
from .rules import DEFAULT_RULES_PATH, Rules, RulesError
from .run import (build_run, half_month_periods, period_label, suggest_period,
                  weeks_in)
from .store import DATA_DIR, Store

APP_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = APP_ROOT / "web"
UPLOAD_DIR = DATA_DIR / "uploads"

_import_cache: dict[str, object] = {}
_cache_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot turn {type(value)} into JSON")


# ---------------------------------------------------------------------------
# building a run from what is stored
# ---------------------------------------------------------------------------

def _cached_import(path: Path, rules: Rules):
    key = f"{path}:{path.stat().st_mtime_ns}:{rules.version}:{id(rules)}"
    with _cache_lock:
        cached = _import_cache.get(key)
        if cached is not None:
            return cached
    result = import_export(path, rules)
    with _cache_lock:
        _import_cache.clear()
        _import_cache[key] = result
    return result


def load_run(store: Store, run_id: str):
    record = store.get_run(run_id)
    if not record:
        raise ApiError("That payroll run no longer exists.", 404)
    source = Path(record["source_path"])
    if not source.exists():
        raise ApiError(
            f"The export this payroll was built from is missing ({record['source_filename']}). "
            "Upload it again to reopen this run.", 410)

    rules = Rules.from_snapshot(json.loads(record["rules_snapshot"]))
    result = _cached_import(source, rules)
    roster = store.roster()
    recurring = store.list_recurring(active_only=True)
    store.ensure_roster_entries(
        [(j.caregiver_key, j.display_name) for j in result.jobs
         if j.caregiver_key and j.is_payable]
        # Somebody on recurring pay has no bookings, so nothing else would
        # ever put them on the roster - and without a roster entry they read
        # as "not in OnPay", which stops payroll rather than asking about it.
        + [(e["caregiver_key"], e["person_name"]) for e in recurring
           if e["caregiver_key"]])
    roster = store.roster()

    run = build_run(
        source, rules,
        date.fromisoformat(record["period_start"]),
        date.fromisoformat(record["period_end"]),
        roster=roster,
        adjustments=store.adjustments(run_id),
        previously_paid=store.previously_paid(exclude_run_id=run_id),
        import_result=result,
        recurring=recurring,
    )
    return record, run, roster


def waiting_notes(store: Store, record: dict) -> list[dict]:
    """Open notes belonging to this payroll.

    A note is either pinned to a pay period or marked "next payroll", which
    means the next one anybody runs.
    """
    start, end = record["period_start"], record["period_end"]
    out = []
    for note in store.list_notes("open"):
        applies = note.get("applies_to") or "next"
        if applies == "next" or start <= applies <= end:
            out.append(note)
    return out


def run_payload(store: Store, run_id: str) -> dict:
    record, run, roster = load_run(store, run_id)
    entered = store.entered_map(run_id)
    summary = run.summary
    caregivers = []
    for caregiver in run.caregivers:
        entry = roster.get(caregiver.key)
        data = caregiver.to_dict()
        data["status"] = summary["statuses"].get(caregiver.key, "ready")
        data["entered"] = bool(entered.get(caregiver.key))
        data["roster"] = entry.to_dict() if entry else None
        data["findings"] = [f.to_dict() for f in run.findings
                            if f.caregiver_key == caregiver.key]
        # The OnPay lines, with the note that goes beside each one. OnPay's
        # import file has no column for a note, so these are typed in.
        mapping = exports.load_onpay_mapping()
        data["onpay_lines"] = [{
            "pay_id": row["id"],
            "name": exports.onpay_pay_item_name(row["id"], mapping),
            "hours": str(row["hours"]) if row["hours"] else "",
            "rate": str(row["rate"]) if row["rate"] else "",
            "amount": str(exports.onpay_row_total(row)),
            "note": row.get("note", ""),
        } for row in exports.onpay_pay_rows(
            caregiver, entry.onpay_clock_user if entry else "", mapping)]
        caregivers.append(data)

    return {
        "run": {
            "id": record["id"],
            "label": run.label,
            "period_start": run.period_start.isoformat(),
            "period_end": run.period_end.isoformat(),
            "status": record["status"],
            "created_at": record["created_at"],
            "finalized_at": record["finalized_at"],
            "source_filename": record["source_filename"],
            "rules_version": record["rules_version"],
            "locked": record["status"] == "finalized",
        },
        "summary": summary,
        "totals": run.totals(),
        "reconciliation": run.reconciliation.to_dict(),
        "findings": [f.to_dict() for f in run.findings],
        "waiting_notes": [
            dict(n, kind_label=extras.note_label(n["kind"]),
                 problem=extras.note_problem(n),
                 applies_itself=n["kind"] in extras.APPLIES_ITSELF)
            for n in waiting_notes(store, record)
        ],
        "applied_notes": store.notes_for_run(run_id),
        "caregivers": caregivers,
        "excluded_jobs": [j.to_dict() for j in run.excluded_jobs],
        "entered_count": sum(1 for c in run.caregivers if entered.get(c.key)),
        "rules": {
            "tiers": [{"key": t["key"], "label": t["label"], "rate": str(t["rate"])}
                      for t in run.rules.tiers],
            "daily_ot_threshold": str(run.rules.daily_ot_threshold),
            "daily_dt_threshold": str(run.rules.daily_dt_threshold),
            "weekly_ot_enabled": run.rules.weekly_ot_enabled,
            "weekly_ot_threshold": str(run.rules.weekly_ot_threshold),
            "minimum_hours": str(run.rules.minimum_hours),
            "version": run.rules.version,
        },
    }


# ---------------------------------------------------------------------------
# request handling
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "SitterwisePayroll"
    store: Store = None            # set on the server instance

    def log_message(self, fmt, *args):   # keep the terminal quiet
        pass

    # -- plumbing -------------------------------------------------------
    def _send(self, status, body: bytes, content_type="application/json",
              extra_headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, default=_json_default).encode("utf-8"))

    def _error(self, message, status=400):
        self._json({"error": message}, status)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(f"The app could not read that request: {exc}")

    # -- routing --------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/"):
                return self._api_get(path, parse_qs(parsed.query))
            return self._static(path)
        except ApiError as exc:
            return self._error(exc.message, exc.status)
        except Exception as exc:                       # never crash the app
            return self._error(f"Something went wrong: {exc}", 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            return self._api_post(unquote(parsed.path))
        except ApiError as exc:
            return self._error(exc.message, exc.status)
        except RulesError as exc:
            return self._error(f"Those settings cannot be used: {exc}")
        except Exception as exc:
            return self._error(f"Something went wrong: {exc}", 500)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            return self._api_delete(unquote(parsed.path))
        except ApiError as exc:
            return self._error(exc.message, exc.status)
        except Exception as exc:
            return self._error(f"Something went wrong: {exc}", 500)

    # -- static files ---------------------------------------------------
    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        target = (WEB_ROOT / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            return self._send(404, b"Not found", "text/plain")
        kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), kind)

    # -- GET api --------------------------------------------------------
    def _api_get(self, path, query):
        store = self.store
        if path == "/api/state":
            roster = store.roster()
            return self._json({
                "runs": store.list_runs(),
                "roster_count": len(roster),
                "roster_needing_attention": sum(1 for e in roster.values() if e.needs_attention),
                "rules_version": Rules.load().version,
            })
        if path == "/api/roster":
            return self._json({
                "roster": [e.to_dict() for e in store.roster().values()],
                "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            })
        if path == "/api/settings":
            return self._json({
                "rules": json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8")),
                "path": str(DEFAULT_RULES_PATH),
                "onpay_mapping": exports.load_onpay_mapping(),
                "onpay_mapping_path": str(exports.MAPPING_PATH),
            })
        if path == "/api/audit":
            return self._json({"entries": store.audit_trail(query.get("run", [None])[0])})
        if path == "/api/notes":
            notes = store.list_notes(query.get("status", [None])[0])
            for note in notes:
                note["kind_label"] = extras.note_label(note["kind"])
                note["problem"] = extras.note_problem(note)
                note["applies_itself"] = note["kind"] in extras.APPLIES_ITSELF
            return self._json({
                "notes": notes,
                "kinds": [{"key": k, "label": v, "applies_itself": k in extras.APPLIES_ITSELF}
                          for k, v in extras.NOTE_KINDS.items()],
            })
        if path == "/api/recurring":
            return self._json({"entries": store.list_recurring()})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)", path)
        if match:
            return self._json(run_payload(store, match.group(1)))

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/export/([a-z_]+)", path)
        if match:
            return self._export(match.group(1), match.group(2))

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/exports", path)
        if match:
            run_id = match.group(1)
            _, run, roster = load_run(store, run_id)
            listing = exports.all_exports(run, roster, store.entered_map(run_id))
            return self._json({"exports": [
                {k: v for k, v in item.items() if k != "content"} for item in listing]})

        return self._error("No such thing here.", 404)

    def _apply_notes(self, run_id):
        """Carry this payroll's waiting notes into it as adjustments.

        One button, but never a silent one: each note becomes an ordinary
        adjustment carrying the note's own words, so it shows up in the
        caregiver's adjustment list and in the audit trail like every other
        manual change. Notes needing a person's judgement are left alone.
        """
        store = self.store
        self._require_open(run_id)
        record = store.get_run(run_id)
        applied, skipped = [], []
        for note in waiting_notes(store, record):
            problem = extras.note_problem(note)
            if note["kind"] not in extras.APPLIES_ITSELF:
                skipped.append({"note": note, "why": "This one needs you to decide."})
                continue
            if problem:
                skipped.append({"note": note, "why": problem})
                continue
            store.add_adjustment(run_id, extras.note_to_adjustment(note))
            store.mark_note_applied(note["id"], run_id)
            applied.append(note["id"])
        return self._json({"ok": True, "applied": len(applied), "skipped": skipped})

    def _export(self, run_id, key):
        store = self.store
        _, run, roster = load_run(store, run_id)
        listing = exports.all_exports(run, roster, store.entered_map(run_id))
        item = next((e for e in listing if e["key"] == key), None)
        if not item:
            raise ApiError("That export does not exist.", 404)
        store.log("export_downloaded", item["name"], run_id)
        self._send(200, item["content"].encode("utf-8-sig"), "text/csv; charset=utf-8",
                   {"Content-Disposition": f'attachment; filename="{item["filename"]}"'})

    # -- POST api -------------------------------------------------------
    def _api_post(self, path):
        store = self.store

        if path == "/api/upload":
            return self._upload()

        if path == "/api/notes":
            data = self._json_body()
            if not data.get("kind"):
                raise ApiError("Pick what kind of note this is.")
            name = (data.get("caregiver_name") or "").strip()
            data["caregiver_name"] = name
            data["caregiver_key"] = data.get("caregiver_key") or normalise_name(name)
            note_id = store.add_note(data)
            return self._json({"ok": True, "id": note_id})

        if path == "/api/recurring":
            data = self._json_body()
            name = (data.get("person_name") or "").strip()
            if not name:
                raise ApiError("Who is this payment for?")
            if extras.amount_of(data.get("amount")) == 0:
                raise ApiError("How much should they be paid?")
            data["person_name"] = name
            data["caregiver_key"] = data.get("caregiver_key") or normalise_name(name)
            return self._json({"ok": True, "id": store.add_recurring(data)})

        if path == "/api/runs":
            return self._create_run(self._json_body())

        if path == "/api/roster":
            data = self._json_body()
            key = data.get("caregiver_key") or normalise_name(data.get("display_name", ""))
            if not key:
                raise ApiError("A caregiver needs a name.")
            entry = RosterEntry(
                caregiver_key=key,
                display_name=data.get("display_name", "").strip() or key.title(),
                status=data.get("status", NOT_IN_ONPAY),
                onpay_clock_user=data.get("onpay_clock_user", "").strip(),
                onpay_employee_id=data.get("onpay_employee_id", "").strip(),
                onpay_name=data.get("onpay_name", "").strip(),
                note=data.get("note", "").strip(),
                source=data.get("source", "manual"),
            )
            if entry.status not in STATUS_LABELS:
                raise ApiError(f"'{entry.status}' is not a status the app knows.")
            store.upsert_roster_entry(entry)
            return self._json({"ok": True, "entry": entry.to_dict()})

        match = re.fullmatch(r"/api/notes/([0-9a-f]+)", path)
        if match:
            store.update_note(match.group(1), self._json_body())
            return self._json({"ok": True})

        match = re.fullmatch(r"/api/recurring/([0-9a-f]+)", path)
        if match:
            store.update_recurring(match.group(1), self._json_body())
            return self._json({"ok": True})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/notes/apply", path)
        if match:
            return self._apply_notes(match.group(1))

        if path == "/api/roster/import":
            return self._import_roster()

        if path == "/api/settings":
            data = self._json_body()
            rules_data = data.get("rules")
            if rules_data is not None:
                Rules(rules_data)          # refuses to save something unusable
                DEFAULT_RULES_PATH.write_text(
                    json.dumps(rules_data, indent=2) + "\n", encoding="utf-8")
                store.log("settings_saved", f"rules version {rules_data.get('version')}")
            mapping = data.get("onpay_mapping")
            if mapping is not None:
                exports.MAPPING_PATH.write_text(
                    json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
                store.log("settings_saved", "OnPay column mapping")
            with _cache_lock:
                _import_cache.clear()
            return self._json({"ok": True})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/entered", path)
        if match:
            data = self._json_body()
            self._require_open(match.group(1))
            store.set_entered(match.group(1), data["caregiver_key"], bool(data.get("entered")))
            return self._json({"ok": True})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/adjustments", path)
        if match:
            return self._add_adjustment(match.group(1), self._json_body())

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/finalize", path)
        if match:
            run_id = match.group(1)
            self._require_open(run_id)
            _, run, _ = load_run(store, run_id)
            if not run.summary["can_finalize"]:
                raise ApiError(
                    "There are still things that have to be sorted out before this payroll "
                    "can be finished. They are listed at the top of the payroll check.")
            store.finalize_run(run_id, [j.booking_id for j in run.period_jobs], run.totals())
            return self._json({"ok": True})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/unlock", path)
        if match:
            data = self._json_body()
            store.unlock_run(match.group(1), data.get("reason", ""))
            return self._json({"ok": True})

        return self._error("No such thing here.", 404)

    def _api_delete(self, path):
        store = self.store
        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/adjustments/([0-9a-f]+)", path)
        if match:
            self._require_open(match.group(1))
            store.remove_adjustment(match.group(1), match.group(2))
            return self._json({"ok": True})
        match = re.fullmatch(r"/api/runs/([0-9a-f]+)", path)
        if match:
            try:
                store.delete_run(match.group(1))
            except ValueError as exc:
                raise ApiError(str(exc))
            return self._json({"ok": True})
        match = re.fullmatch(r"/api/notes/([0-9a-f]+)", path)
        if match:
            store.delete_note(match.group(1))
            return self._json({"ok": True})
        match = re.fullmatch(r"/api/recurring/([0-9a-f]+)", path)
        if match:
            store.delete_recurring(match.group(1))
            return self._json({"ok": True})
        match = re.fullmatch(r"/api/roster/(.+)", path)
        if match:
            key = match.group(1)
            store.db.execute("DELETE FROM roster WHERE caregiver_key=?", (key,))
            store.db.commit()
            store.log("roster_removed", key)
            return self._json({"ok": True})
        return self._error("No such thing here.", 404)

    # -- helpers --------------------------------------------------------
    def _require_open(self, run_id):
        record = self.store.get_run(run_id)
        if not record:
            raise ApiError("That payroll run no longer exists.", 404)
        if record["status"] == "finalized":
            raise ApiError(
                "This payroll is finished and locked. Unlock it first if you need to change it.")

    def _save_upload(self) -> Path:
        filename = self.headers.get("X-Filename") or "upload.xlsx"
        filename = Path(unquote(filename)).name
        suffix = Path(filename).suffix.lower()
        if suffix not in (".xlsx", ".xlsm", ".csv"):
            raise ApiError(
                "The app can read .xlsx and .csv files. That one is a "
                f"{suffix or 'file with no extension'}.")
        raw = self._body()
        if not raw:
            raise ApiError("That file came through empty.")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(raw).hexdigest()[:16]
        target = UPLOAD_DIR / f"{digest}-{filename}"
        target.write_bytes(raw)
        return target

    def _upload(self):
        target = self._save_upload()
        rules = Rules.load()
        result = _cached_import(target, rules)
        start, end, note = suggest_period(result, rules)
        payable = [j for j in result.jobs if j.is_payable]
        caregivers = {j.caregiver_key for j in payable if j.caregiver_key}
        choices = []
        week_start = rules.workweek_start_index
        for lo, hi in weeks_in(result, 0 if week_start is None else week_start):
            count = sum(1 for j in payable if j.workday and lo <= j.workday <= hi)
            if count:
                choices.append({"start": lo.isoformat(), "end": hi.isoformat(),
                                "label": period_label(lo, hi), "jobs": count,
                                "kind": "week"})
        if rules.offer_half_months:
            months = sorted({(j.workday.year, j.workday.month)
                             for j in result.jobs if j.workday})
            for year, month in months:
                for lo, hi, label in half_month_periods(year, month):
                    count = sum(1 for j in payable if j.workday and lo <= j.workday <= hi)
                    if count:
                        choices.append({"start": lo.isoformat(), "end": hi.isoformat(),
                                        "label": label, "jobs": count, "kind": "half_month"})
        self.store.log("export_uploaded",
                       f"{target.name}: {len(result.jobs)} bookings, "
                       f"{len(caregivers)} caregivers")
        return self._json({
            "source_path": str(target),
            "source_filename": target.name,
            "source_sha256": result.source_sha256,
            "rows": result.row_count,
            "jobs": len(result.jobs),
            "payable_jobs": len(payable),
            "caregivers": len(caregivers),
            "min_date": result.min_date.isoformat() if result.min_date else None,
            "max_date": result.max_date.isoformat() if result.max_date else None,
            "suggested": {"start": start.isoformat(), "end": end.isoformat(),
                          "label": period_label(start, end), "note": note},
            "period_choices": choices,
            "unmapped_columns": result.unmapped_columns,
            "missing_columns": result.missing_columns,
            "parse_errors": result.parse_errors,
        })

    def _create_run(self, data):
        store = self.store
        source = Path(data.get("source_path", ""))
        if not source.exists():
            raise ApiError("That upload has gone. Please upload the export again.")
        try:
            start = date.fromisoformat(data["period_start"])
            end = date.fromisoformat(data["period_end"])
        except (KeyError, ValueError):
            raise ApiError("The app needs a start and end date for the pay period.")
        if end < start:
            raise ApiError("The pay period ends before it starts.")

        rules = Rules.load()
        result = _cached_import(source, rules)
        payable = [j for j in result.jobs
                   if j.is_payable and j.workday and start <= j.workday <= end]
        if not payable:
            raise ApiError(
                f"There are no jobs to pay between {start:%b %-d} and {end:%b %-d} in this "
                "export. Check the pay period.")

        store.ensure_roster_entries(
            [(j.caregiver_key, j.display_name) for j in payable if j.caregiver_key])
        run_id = store.create_run(
            period_label(start, end), start, end, rules.snapshot(),
            data.get("source_filename") or source.name,
            result.source_sha256, str(source))
        return self._json({"ok": True, "run_id": run_id})

    def _add_adjustment(self, run_id, data):
        self._require_open(run_id)
        kind = data.get("kind", "")
        if kind not in ("hours", "rate", "tip", "mileage", "reimbursement", "adjustment"):
            raise ApiError(f"'{kind}' is not something the app can adjust.")
        if not data.get("caregiver_key"):
            raise ApiError("An adjustment has to belong to a caregiver.")
        if not str(data.get("reason", "")).strip():
            raise ApiError("Please say why you are making this change - it goes on the record.")
        try:
            Decimal(str(data.get("new_value", "")).strip() or "x")
        except Exception:
            raise ApiError("The new value needs to be a number.")
        adj = Adjustment(
            id="", caregiver_key=data["caregiver_key"], kind=kind,
            booking_id=str(data.get("booking_id", "")).strip(),
            original_value=str(data.get("original_value", "")),
            new_value=str(data.get("new_value", "")).strip(),
            reason=str(data.get("reason", "")).strip(),
            taxable=bool(data.get("taxable", True)),
        )
        if kind != "adjustment" and not adj.booking_id:
            raise ApiError(f"Changing {kind} needs a booking to change it on.")
        return self._json({"ok": True, "id": self.store.add_adjustment(run_id, adj)})

    def _import_roster(self):
        target = self._save_upload()
        entries, problems = parse_onpay_employee_export(target)
        if not entries and problems:
            raise ApiError(problems[0])
        existing = self.store.roster()

        # OnPay knows people by their legal name, which is often not the name
        # Sitterwise shows - Lissa's OnPay record is Elisabeth R Gray. Matching
        # on the name alone would make a second roster entry for her and then
        # report the first one as missing from OnPay. So a Clock User, an
        # employee id, or a legal name already recorded against somebody all
        # count as the same person.
        by_clock = {e.onpay_clock_user.strip().casefold(): key
                    for key, e in existing.items() if e.onpay_clock_user.strip()}
        by_emp_id = {e.onpay_employee_id.strip().casefold(): key
                     for key, e in existing.items() if e.onpay_employee_id.strip()}
        by_onpay_name = {normalise_name(e.onpay_name): key
                         for key, e in existing.items() if e.onpay_name.strip()}

        def already_known_as(entry) -> str:
            for table, value in (
                (by_clock, entry.onpay_clock_user.strip().casefold()),
                (by_emp_id, entry.onpay_employee_id.strip().casefold()),
                (by_onpay_name, entry.caregiver_key),
            ):
                if value and value in table:
                    return table[value]
            return ""

        added = updated = linked = 0
        matched_keys = set()
        for entry in entries:
            onpay_name = entry.display_name
            key = entry.caregiver_key
            if key not in existing:
                other = already_known_as(entry)
                if other:
                    # Same person under a different name. Keep the roster entry
                    # that is already tied to their bookings.
                    key = other
                    linked += 1
            if key in existing:
                previous = existing[key]
                entry.caregiver_key = key
                entry.display_name = previous.display_name
                entry.note = previous.note
                if key != previous.caregiver_key or onpay_name != previous.display_name:
                    entry.onpay_name = onpay_name
                updated += 1
            else:
                added += 1
            matched_keys.add(entry.caregiver_key)
            self.store.upsert_roster_entry(entry, quiet=True)

        self.store.log("roster_imported",
                       f"{target.name}: {added} added, {updated} updated, "
                       f"{linked} matched under a different name")
        unmatched = sorted(
            e.display_name for key, e in existing.items() if key not in matched_keys)
        return self._json({
            "ok": True, "added": added, "updated": updated, "linked": linked,
            "problems": problems,
            "not_in_onpay_file": unmatched,
        })


def _payroll_already_on(port: int) -> bool:
    """True if this app is the thing already holding the port.

    Double-clicking the launcher twice is the common way to hit "address
    already in use", and the honest answer then is "it is already running",
    not a stack trace.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as resp:
            if resp.status != 200:
                return False
            json.loads(resp.read().decode("utf-8"))
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def serve(port: int = 8756, open_browser: bool = True, data_path: Path | None = None):
    httpd = None
    for candidate in range(port, port + 10):
        try:
            Handler.store = Store(data_path)
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            if _payroll_already_on(candidate):
                url = f"http://127.0.0.1:{candidate}/"
                print("\n  Sitterwise Payroll is already running.")
                print(f"  It is open at {url} - no need to start it twice.")
                print("  You can close this window.\n")
                if open_browser:
                    webbrowser.open(url)
                return
            # Something else has the port. Try the next one.

    if httpd is None:
        print("\n  Could not find a free port to run on.")
        print(f"  Ports {port} to {port + 9} are all taken by something else.")
        print("  Restarting the Mac clears this. Or run:  python3 run.py --port 9100\n")
        return

    url = f"http://127.0.0.1:{port}/"
    print("\n  Sitterwise Payroll is running.")
    print(f"  Open {url} in your browser.")
    print("  Leave this window open while you work. Close it when you are done.\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
    finally:
        httpd.server_close()
