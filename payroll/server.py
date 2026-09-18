"""The local web server behind the app.

Runs on Amy's own machine. No accounts, no cloud, nothing leaves the laptop.
Uploaded exports are kept in data/uploads so a payroll run can always be
rebuilt from its original file.
"""
from __future__ import annotations

import errno
import hashlib
from functools import wraps
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import calsavers, combine, exports, extras, late_tips, settings_files, transfer
from .security import MAX_UPLOAD_BYTES, MAX_JSON_BYTES, access_token, local_headers
from .engine import Adjustment
from .importer import import_export
from .roster import (NOT_IN_ONPAY, READY, RosterEntry, STATUS_LABELS,
                     assign_clock_users, clock_users_from_sitterwise,
                     compare_clock_users, confirm_setup, merge_import,
                     normalise_name, parse_onpay_employee_export)
from .rules import Rules, RulesError
from .run import (build_run, half_month_periods, period_label, suggest_period,
                  weeks_in)
from .store import DATA_DIR, Store

APP_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = APP_ROOT / "web"
UPLOAD_DIR = DATA_DIR / "uploads"

_import_cache: dict[str, object] = {}
_cache_lock = threading.Lock()
_api_lock = threading.RLock()


def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _api_lock:
            return fn(*args, **kwargs)
    return wrapped


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
    if not source.is_absolute():
        source = store.path.parent / source
    if not source.exists():
        raise ApiError(
            f"The export this payroll was built from is missing ({record['source_filename']}). "
            "Upload it again to reopen this run.", 410)

    rules = Rules.from_snapshot(json.loads(record["rules_snapshot"]))
    result = _cached_import(source, rules)
    roster = store.roster()
    recurring = store.list_recurring(active_only=True)
    carried_tips, tip_problems = late_tips.for_run(store, record)
    store.ensure_roster_entries(
        [(j.caregiver_key, j.display_name) for j in result.jobs
         if j.caregiver_key and j.is_payable]
        # Somebody on recurring pay has no bookings, so nothing else would
        # ever put them on the roster - and without a roster entry they read
        # as "not in OnPay", which stops payroll rather than asking about it.
        + [(e["caregiver_key"], e["person_name"]) for e in recurring
           if e["caregiver_key"]]
        + [(t["caregiver_key"], t["caregiver_name"]) for t in carried_tips])
    roster = store.roster()

    run = build_run(
        source, rules,
        date.fromisoformat(record["period_start"]),
        date.fromisoformat(record["period_end"]),
        roster=roster,
        adjustments=store.adjustments(run_id),
        previously_paid=store.previously_paid(exclude_run_id=run_id),
        import_result=result,
        recurring=recurring, late_tips=carried_tips, tip_problems=tip_problems,
    )
    return record, run, roster


def waiting_notes(store: Store, record: dict) -> list[dict]:
    """Open notes belonging to this payroll.

    A note is either pinned to a pay period or marked "next payroll", which
    means the next one anybody runs.
    """
    start, end = record["period_start"], record["period_end"]
    if record["status"] == "finalized":
        return []
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
            caregiver, exports.clock_user_for(caregiver, entry), mapping)]
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
        "late_tips": run.late_tips,
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
    httpd = None            # set by serve(), so the app can be asked to stop
    server_version = "SitterwisePayroll"
    store: Store = None            # set on the server instance

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):   # keep the terminal quiet
        pass

    # -- plumbing -------------------------------------------------------
    def _send(self, status, body: bytes, content_type="application/json",
              extra_headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        # Inline handlers remain in the existing UI; imported strings must
        # be encoded separately for HTML and JavaScript contexts.
        self.send_header("Content-Security-Policy", "default-src 'none'; "
                         "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'none'; object-src 'none'")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, default=_json_default).encode("utf-8"))

    def _error(self, message, status=400):
        self._json({"error": message}, status)

    def _body(self) -> bytes:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            raise ApiError("A valid request length is required.", 400)
        if self.headers.get("Transfer-Encoding"):
            raise ApiError("Chunked requests are not supported.", 400)
        length = int(lengths[0])
        limit = transfer.MAX_BYTES if urlparse(self.path).path == "/api/history-transfer" else MAX_UPLOAD_BYTES
        if "application/json" in self.headers.get("Content-Type", ""):
            limit = MAX_JSON_BYTES
        if length > limit:
            raise ApiError("This file or request is too large.", 413)
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            raise ApiError("The upload was interrupted. Please try again.")
        return raw

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ApiError("The app needs a JSON object.")
            return value
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(f"The app could not read that request: {exc}")

    # -- routing --------------------------------------------------------
    def _check_request(self):
        port = self.server.server_address[1]
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise ApiError("This app only accepts its own local address.", 403)
        origin = self.headers.get("Origin")
        if ((origin is not None and origin != "http://" + hosts[0])
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            raise ApiError("Requests from other websites are not allowed.", 403)
        if not self.path.startswith("/"):
            raise ApiError("Invalid request address.")
        if unquote(urlparse(self.path).path).startswith("/api/"):
            expected = "Bearer " + self.server.access_token
            if not secrets.compare_digest(self.headers.get("Authorization", ""), expected):
                raise ApiError("Open Sitterwise Payroll from the app or its launcher to connect securely.", 401)

    @serialized
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            self._check_request()
            if path.startswith("/api/"):
                return self._api_get(path, parse_qs(parsed.query))
            return self._static(path)
        except ApiError as exc:
            return self._error(exc.message, exc.status)
        except Exception as exc:                       # never crash the app
            return self._error(f"Something went wrong: {exc}", 500)

    @serialized
    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            self._check_request()
            if unquote(parsed.path) == "/api/stop":
                self._json({"ok": True})
                if self.httpd is not None:
                    threading.Thread(target=self.httpd.shutdown, daemon=True).start()
                return
            return self._api_post(unquote(parsed.path))
        except ApiError as exc:
            return self._error(exc.message, exc.status)
        except RulesError as exc:
            return self._error(f"Those settings cannot be used: {exc}")
        except Exception as exc:
            return self._error(f"Something went wrong: {exc}", 500)

    @serialized
    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            self._check_request()
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
        if not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file():
            return self._send(404, b"Not found", "text/plain")
        kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), kind)

    # -- GET api --------------------------------------------------------
    def _api_get(self, path, query):
        store = self.store
        if path == "/api/history-transfer":
            try:
                raw = transfer.create_archive(store, settings_files.DATA_DIR)
            except ValueError as exc:
                raise ApiError(str(exc))
            filename = f"Sitterwise-payroll-history-{date.today().isoformat()}.sitterwise"
            return self._send(200, raw, "application/octet-stream", {
                "Content-Disposition": f'attachment; filename="{filename}"'})
        if path == "/api/state":
            roster = store.roster()
            return self._json({
                "runs": store.list_runs(),
                "roster_count": len(roster),
                "roster_needing_attention": sum(1 for e in roster.values() if e.needs_attention),
                "rules_version": Rules.load().version,
                "build": BUILD,
                "data_path": str(store.path.resolve()),
            })
        if path == "/api/roster":
            return self._json({
                "roster": [e.to_dict() for e in store.roster().values()],
                "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            })
        if path == "/api/settings":
            return self._json({
                # What is in force, which is your copy in data/ - not the
                # defaults the app was shipped with.
                "rules": json.loads(
                    settings_files.rules_path().read_text(encoding="utf-8")),
                "path": str(settings_files.rules_path()),
                "onpay_mapping": exports.load_onpay_mapping(),
                "onpay_mapping_path": str(settings_files.mapping_path()),
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
        record, run, roster = load_run(store, run_id)
        listing = exports.all_exports(run, roster, store.entered_map(run_id))
        item = next((e for e in listing if e["key"] == key), None)
        if not item:
            raise ApiError("That export does not exist.", 404)
        if item.get("download_blocked"):
            raise ApiError("The OnPay file cannot be downloaded yet: " + " ".join(
                p["problem"] for p in item["problems"] if p.get("blocking")))
        if key == "onpay_import" and waiting_notes(store, record):
            raise ApiError("Saved pay changes need attention before downloading. "
                           "Open Payroll checks to apply or resolve them.")
        if key == "onpay_import" and record['status'] != 'finalized':
            # Money already exported for OnPay must not grow when another
            # booking file arrives. Further tip increases wait for next time.
            with store.db:
                store.db.execute("UPDATE runs SET late_tips_snapshot=?,tip_export_snapshot=? WHERE id=?",
                    (json.dumps(run.late_tips), json.dumps(late_tips.payments_for(run)), run_id))
        store.log("export_downloaded", item["name"], run_id)
        self._send(200, item["content"].encode("utf-8-sig"),
                   item.get("content_type", "text/csv; charset=utf-8"),
                   {"Content-Disposition": f'attachment; filename="{item["filename"]}"'})

    # -- POST api -------------------------------------------------------
    def _api_post(self, path):
        store = self.store

        if path == "/api/history-transfer":
            try:
                result = transfer.restore_archive(self._body(), store)
            except ValueError as exc:
                raise ApiError(str(exc))
            with _cache_lock:
                _import_cache.clear()
            return self._json({"ok": True, "counts": result["counts"]})

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
            try:
                entry_id = store.add_recurring(data)
            except ValueError as exc:
                raise ApiError(str(exc))
            return self._json({"ok": True, "id": entry_id})

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
            try:
                store.update_recurring(match.group(1), self._json_body())
            except ValueError as exc:
                raise ApiError(str(exc))
            return self._json({"ok": True})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/notes/apply", path)
        if match:
            return self._apply_notes(match.group(1))

        if path == "/api/calsavers":
            # The register OnPay prints before a payroll is processed. Only
            # read - it never reaches CalSavers from here, somebody types it.
            target = self._save_upload()
            try:
                found = calsavers.read_register(target)
            except RuntimeError as exc:
                raise ApiError(str(exc))
            self.store.log("calsavers_read",
                           f"{target.name}: {len(found.people)} contributions, {found.total}")
            return self._json(found.to_dict())

        if path == "/api/roster/clock-users":
            # OnPay has no bulk import for Clock Users, so somebody types each
            # one into a profile by hand. The app picks the numbers and keeps
            # them, so the two ends cannot drift apart.
            roster = self.store.roster()
            assigned = assign_clock_users(roster)
            for entry in assigned:
                self.store.upsert_roster_entry(entry, quiet=True)
            if assigned:
                self.store.log(
                    "clock_users_assigned",
                    ", ".join(f"{e.display_name} {e.onpay_clock_user}" for e in assigned))
            return self._json({
                "ok": True,
                "assigned": [{"name": e.display_name, "clock_user": e.onpay_clock_user}
                             for e in assigned],
                "already_had_one": sum(1 for e in roster.values()
                                       if e.onpay_clock_user.strip()) - len(assigned),
            })

        if path == "/api/roster/confirm-setup":
            # Somebody has looked in OnPay and found these people there. The
            # app cannot know that on its own - a Clock User on a booking does
            # not prove OnPay holds it - so it is recorded as what it is.
            roster = store.roster()
            # The whole roster, not only whoever happened to work this week.
            # Somebody checking OnPay checks their caregivers, and a warning
            # that will not go away is a warning people learn to ignore.
            changed = confirm_setup(roster, list(roster))
            for entry in changed:
                store.upsert_roster_entry(entry, quiet=True)
            if changed:
                store.log("roster_confirmed",
                          f"{len(changed)} confirmed as set up in OnPay: "
                          + ", ".join(e.display_name for e in changed))
            return self._json({
                "ok": True,
                "confirmed": [e.display_name for e in changed],
                "still_waiting": sorted(
                    f"{e.display_name} ({e.status_label})" for e in roster.values()
                    if e.needs_attention),
            })

        if path == "/api/roster/clock-users/from-sitterwise":
            # Sitterwise's own caregiver number, used as the Clock User, so a
            # person is one number in both systems instead of a name here and
            # an invented number there.
            runs = store.list_runs()
            if not runs:
                raise ApiError("There are no payrolls yet, so there are no Sitterwise "
                               "caregiver numbers to read. Build a payroll first.")
            _, run, _ = load_run(store, runs[0]["id"])
            ids = {c.key: c.caregiver_id for c in run.caregivers if c.caregiver_id}
            if not ids:
                raise ApiError(
                    "The bookings in the latest payroll carry no Sitterwise caregiver "
                    "numbers. That export predates the Caregiver ID column - take a "
                    "fresh one out of Sitterwise.")
            roster = store.roster()
            report = clock_users_from_sitterwise(roster, ids)
            for entry in report.pop("changed"):
                store.upsert_roster_entry(entry, quiet=True)
            store.log("clock_users_from_sitterwise",
                      f"{len(report['setting'])} set, {len(report['changing'])} changed "
                      f"from the payroll of {runs[0]['label']}")
            return self._json({"ok": True, "run": runs[0]["label"], **report})

        if path == "/api/roster/clock-users/check":
            # The numbers read back out of OnPay, checked against the ones
            # the app handed out. Nothing is saved - this only reports.
            target = self._save_upload()
            try:
                entries, problems = parse_onpay_employee_export(target)
            except ValueError as exc:
                raise ApiError(str(exc))
            if not entries and problems:
                raise ApiError(" ".join(problems))
            report = compare_clock_users(self.store.roster(), entries)
            self.store.log("clock_users_checked",
                           f"{target.name}: {len(report['agree'])} agree, "
                           f"{len(report['wrong'])} wrong, {len(report['missing'])} not read back")
            return self._json({"ok": True, "problems": problems, **report})

        if path == "/api/roster/import":
            return self._import_roster()

        if path == "/api/settings":
            data = self._json_body()
            rules_data = data.get("rules")
            if rules_data is not None:
                Rules(rules_data)          # refuses to save something unusable
                settings_files.rules_path().write_text(
                    json.dumps(rules_data, indent=2) + "\n", encoding="utf-8")
                store.log("settings_saved", f"rules version {rules_data.get('version')}")
            mapping = data.get("onpay_mapping")
            if mapping is not None:
                settings_files.mapping_path().write_text(
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
            record, run, _ = load_run(store, run_id)
            if (record['tip_export_snapshot'] is not None
                    and json.loads(record['tip_export_snapshot']) != late_tips.payments_for(run)):
                raise ApiError("Tips changed after the last OnPay download. Download the updated file and verify OnPay before marking this payroll finished.")
            if not run.summary["can_finalize"]:
                raise ApiError(
                    "There are still things that have to be sorted out before this payroll "
                    "can be finished. They are listed at the top of the payroll check.")
            store.finalize_run(run_id, [j.booking_id for j in run.period_jobs], run.totals(),
                               late_tips.payments_for(run), run.late_tips)
            return self._json({"ok": True})

        match = re.fullmatch(r"/api/runs/([0-9a-f]+)/unlock", path)
        if match:
            data = self._json_body()
            try:
                store.unlock_run(match.group(1), data.get("reason", ""))
            except ValueError as exc:
                raise ApiError(str(exc))
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
        if not target.resolve().is_relative_to(UPLOAD_DIR.resolve()) or target.is_symlink():
            raise ApiError("This upload destination is not safe.")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as saved:
            saved.write(raw)
        return target

    def _upload(self):
        target = self._save_upload()

        # A pay week that crosses a month end needs both months' exports,
        # because Sitterwise exports a month at a time. Uploading the second
        # file joins it to the first rather than replacing it.
        combined = None
        earlier = [self._uploaded_path(unquote(p)) for p in
                   (self.headers.get("X-Combine-With") or "").split("|") if p.strip()]
        if earlier:
            result = combine.combine_exports([*earlier, target], UPLOAD_DIR)
            combined = result.to_dict()
            self.store.log("exports_combined", result.summary)
            target = result.path

        rules = Rules.load()
        result = _cached_import(target, rules)
        late_tips.observe_export(self.store, result)
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
        # A late-tip-only payroll still needs a selectable next week.
        finished = [r for r in self.store.list_runs() if r['status'] == 'finalized']
        if finished:
            from datetime import timedelta
            next_start = date.fromisoformat(max(r['period_end'] for r in finished)) + timedelta(days=1)
            next_end = next_start + timedelta(days=6)
            pending, pending_problems = late_tips.for_run(self.store, dict(
                id='', status='open', period_start=next_start.isoformat(), period_end=next_end.isoformat()), reserve=False)
            if pending or pending_problems:
                choice = next((c for c in choices if c['start'] == next_start.isoformat()), None)
                if choice is None:
                    choice = dict(start=next_start.isoformat(), end=next_end.isoformat(),
                                  label=period_label(next_start, next_end), jobs=0, kind='week')
                    choices.append(choice)
                choice['late_tips'] = len(pending)
                if any(r['period_start'] == start.isoformat() and r['period_end'] == end.isoformat() for r in finished):
                    start, end = next_start, next_end
                    note = 'Late tips from previously paid bookings are ready for the next payroll.'
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
            "combined": combined,
            "unmapped_columns": result.unmapped_columns,
            "missing_columns": result.missing_columns,
            "parse_errors": result.parse_errors,
        })

    def _create_run(self, data):
        store = self.store
        source = self._uploaded_path(data.get("source_path", ""))
        try:
            start = date.fromisoformat(data["period_start"])
            end = date.fromisoformat(data["period_end"])
        except (KeyError, ValueError):
            raise ApiError("The app needs a start and end date for the pay period.")
        if end < start:
            raise ApiError("The pay period ends before it starts.")

        rules = Rules.load()
        result = _cached_import(source, rules)
        late_tips.observe_export(store, result)
        # A double click or re-upload must not create a second payroll for a
        # finished week. Keep the finalized run and its paid-booking ledger.
        for previous in sorted(store.list_runs(), key=lambda r: r["status"] != "finalized"):
            if (previous["period_start"] == start.isoformat()
                    and previous["period_end"] == end.isoformat()
                    and (previous["status"] == "finalized"
                         or previous["source_sha256"] == result.source_sha256)):
                return self._json({"ok": True, "run_id": previous["id"],
                                   "existing": True,
                                   "finalized": previous["status"] == "finalized"})
        payable = [j for j in result.jobs
                   if j.is_payable and j.workday and start <= j.workday <= end]
        carried, tip_problems = late_tips.for_run(store, dict(
            id='', status='open', period_start=start.isoformat(), period_end=end.isoformat()), reserve=False)
        if not payable and not carried and not tip_problems:
            raise ApiError(
                f"There are no jobs to pay between {start:%b %-d} and {end:%b %-d} in this "
                "export. Check the pay period.")

        store.ensure_roster_entries(
            [(j.caregiver_key, j.display_name) for j in payable if j.caregiver_key])
        run_id = store.create_run(
            period_label(start, end), start, end, rules.snapshot(),
            data.get("source_filename") or source.name,
            result.source_sha256, str(source))
        late_tips.for_run(store, store.get_run(run_id))
        return self._json({"ok": True, "run_id": run_id})

    def _uploaded_path(self, value):
        source = Path(value).resolve()
        if (not source.is_relative_to(UPLOAD_DIR.resolve()) or not source.is_file()
                or source.suffix.lower() not in (".csv", ".xlsx", ".xlsm")):
            raise ApiError("Please choose a bookings file uploaded into this app.")
        return source

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
        try:
            entries, problems = parse_onpay_employee_export(target)
        except ValueError as exc:
            raise ApiError(str(exc))
        if not entries and problems:
            raise ApiError(" ".join(problems))
        changed, report = merge_import(self.store.roster(), entries)
        for entry in changed:
            self.store.upsert_roster_entry(entry, quiet=True)
        self.store.log("roster_imported",
                       f"{target.name}: {report['updated']} updated, "
                       f"{report['linked']} matched under a different name, "
                       f"{report['in_onpay_only']} in OnPay only")
        return self._json({"ok": True, "problems": problems, **report})


def build_id() -> str:
    """Identify the actual code, including fixes installed before a commit.

    Comparing only HEAD kept the old server alive when files were replaced
    without moving the commit. Include a content fingerprint so relaunching
    also picks up an uncommitted fix or an installation with no .git folder.
    """
    root = APP_ROOT
    release = root / "build.json"
    if release.exists():
        return json.loads(release.read_text())["build"]
    revision = ""
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            head = (root / ".git" / head[5:]).read_text(encoding="utf-8").strip()
        revision = head[:7] + "-"
    except OSError:
        pass
    fingerprint = hashlib.sha256()
    for folder, pattern in (("payroll", "*.py"), ("web", "*")):
        for path in sorted((root / folder).glob(pattern)):
            if path.is_file():
                fingerprint.update(path.relative_to(root).as_posix().encode("utf-8"))
                fingerprint.update(b"\0")
                fingerprint.update(path.read_bytes())
    return revision + fingerprint.hexdigest()[:12]


BUILD = build_id()


def _running_build(port: int, data_path: Path | None = None) -> str | None:
    """The build the app on this port is running, or None if that is not us."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
            headers=local_headers(data_path or DATA_DIR / "payroll.sqlite3"))
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return None
            return str(json.loads(resp.read().decode("utf-8")).get("build", ""))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _ask_to_stop(port: int, data_path: Path | None = None) -> bool:
    """Ask the copy already running to stop, and wait for the port to free.

    Double-clicking the app after pulling an update used to reach the copy
    already running and open that instead - so an update could be pulled,
    the app relaunched, and the old code still be the thing answering. A new
    launch now takes over from an old one.
    """
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/stop", method="POST",
            headers=local_headers(data_path or DATA_DIR / "payroll.sqlite3"))
        urllib.request.urlopen(request, timeout=3).read()
    except (urllib.error.URLError, OSError):
        pass                      # an older copy has no way to be asked
    for _ in range(40):           # up to four seconds for the port to come free
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.1)
    return False


def _payroll_already_on(port: int) -> bool:
    """True if this app is the thing already holding the port.

    Double-clicking the launcher twice is the common way to hit "address
    already in use", and the honest answer then is "it is already running",
    not a stack trace.
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
            headers=local_headers(DATA_DIR / "payroll.sqlite3"))
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return False
            json.loads(resp.read().decode("utf-8"))
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _same_data_on(port: int, path: Path) -> bool:
    """A test or a newly installed app must never replace another data store."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers=local_headers(path))
        with urllib.request.urlopen(req, timeout=2) as resp:
            state = json.loads(resp.read().decode("utf-8"))
        saved_path = state.get("data_path")
        if saved_path:
            return Path(saved_path).resolve() == path.resolve()
        # Older versions have no data identity. Leave them running, and use
        # another port rather than guessing which records they own.
        return False
    except (urllib.error.URLError, OSError, ValueError):
        return False


def serve(port: int = 8756, open_browser: bool = True, data_path: Path | None = None):
    os.umask(0o077)
    httpd = None
    chosen_data = Path(data_path) if data_path else DATA_DIR / "payroll.sqlite3"
    token = access_token(chosen_data)
    for candidate in range(port, port + 10):
        try:
            Handler.store = Store(data_path)
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            if not _same_data_on(candidate, chosen_data):
                continue
            running = _running_build(candidate, chosen_data)
            if running is None:
                continue          # something else has the port; try the next one
            if running == BUILD and BUILD:
                url = f"http://127.0.0.1:{candidate}/"
                print("\n  Sitterwise Payroll is already running, on this same version.")
                print(f"  It is open at {url} - no need to start it twice.")
                print("  You can close this window.\n")
                if open_browser:
                    webbrowser.open(url + "#access=" + token)
                return
            # A different copy of the code is answering - almost always an
            # older one still running from before an update was pulled. Opening
            # it would quietly hand back the old app, which is exactly how an
            # update could look installed and not be. Take the port instead.
            print("\n  An older copy of payroll is running. Stopping it first.")
            if not _ask_to_stop(candidate, chosen_data):
                print("  It would not stop. In Terminal, run:  pkill -f run.py")
                print("  then start payroll again.\n")
                return
            try:
                Handler.store = Store(data_path)
                httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
                port = candidate
                break
            except OSError:
                continue

    if httpd is None:
        print("\n  Could not find a free port to run on.")
        print(f"  Ports {port} to {port + 9} are all taken by something else.")
        print("  Restarting the Mac clears this. Or run:  python3 run.py --port 9100\n")
        return

    Handler.httpd = httpd
    httpd.access_token = token
    httpd.timeout = 30
    url = f"http://127.0.0.1:{port}/"
    print("\n  Sitterwise Payroll is running." + (f"  (version {BUILD})" if BUILD else ""))
    print(f"  Open {url} in your browser.")
    print("  Leave this window open while you work. Close it when you are done.\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url + "#access=" + token)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
    finally:
        httpd.server_close()
