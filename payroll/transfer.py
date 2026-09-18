"""A portable, checked snapshot for moving payroll to another computer."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile

from .store import Store, SCHEMA, now

FORMAT = "sitterwise-payroll-history"
MAX_BYTES = 512 * 1024 * 1024
LEGACY_TABLES = ("runs", "paid_bookings", "adjustments", "entry_progress", "roster",
          "notes", "recurring_pay", "audit_log")
TABLES = LEGACY_TABLES + ("tip_payments", "tip_updates")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_archive(store: Store, settings_dir: Path | None = None) -> bytes:
    """Back up SQLite consistently; only the copied DB gets portable paths."""
    settings_dir = settings_dir or store.path.parent
    with tempfile.TemporaryDirectory(prefix="sitterwise-backup-") as tmp:
        database = Path(tmp) / "payroll.sqlite3"
        copied = sqlite3.connect(database)
        try:
            store.db.backup(copied)
            files = {}
            for run_id, old_path in copied.execute("SELECT id, source_path FROM runs").fetchall():
                source = Path(old_path)
                if not source.is_absolute():
                    source = store.path.parent / source
                if not source.is_file():
                    raise ValueError(f"The source export for payroll {run_id} is missing. Restore it before transferring history.")
                data = source.read_bytes()
                name = "uploads/" + _digest(data)[:16] + "-" + source.name
                files[name] = data
                copied.execute("UPDATE runs SET source_path=? WHERE id=?", (name, run_id))
            copied.commit()
            counts = {name: copied.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in TABLES}
        finally:
            copied.close()
        files["payroll.sqlite3"] = database.read_bytes()
        for name in ("rules.json", "onpay_mapping.json"):
            path = settings_dir / name
            if not path.is_file():
                raise ValueError(f"The saved {name} file is missing. Open Settings before making a backup.")
            files[name] = path.read_bytes()
        if sum(map(len, files.values())) > MAX_BYTES:
            raise ValueError("This history is too large for an in-app transfer.")
        manifest = {"format": FORMAT, "version": 2, "created_at": now(),
                    "counts": counts,
                    "files": {name: _digest(data) for name, data in files.items()}}
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        return out.getvalue()


def _unpack_checked(raw: bytes, folder: Path) -> dict:
    """Never extract arbitrary paths or trust an archive without checking it."""
    if len(raw) > MAX_BYTES:
        raise ValueError("This history file is too large.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or len(names) > 10000:
                raise ValueError("The history file has duplicate or too many entries.")
            if sum(entry.file_size for entry in entries) > MAX_BYTES:
                raise ValueError("This history file expands beyond the allowed size.")
            for name in names:
                path = PurePosixPath(name)
                allowed = name in {"manifest.json", "payroll.sqlite3", "rules.json", "onpay_mapping.json"}
                allowed |= len(path.parts) == 2 and path.parts[0] == "uploads"
                if (not allowed or path.is_absolute() or ".." in path.parts
                        or "\\" in name or str(path) != name):
                    raise ValueError("The history file contains an unexpected path.")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != FORMAT or manifest.get("version") not in (1, 2):
                raise ValueError("This is not a supported Sitterwise history file.")
            hashes = manifest.get("files", {})
            if set(hashes) != set(names) - {"manifest.json"}:
                raise ValueError("The history file's contents do not match its manifest.")
            if not {"payroll.sqlite3", "rules.json", "onpay_mapping.json"} <= set(hashes):
                raise ValueError("The history file is missing its database or settings.")
            for name, expected in hashes.items():
                data = archive.read(name)
                if _digest(data) != expected:
                    raise ValueError("The history file is damaged; a saved file failed verification.")
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            for name in ("rules.json", "onpay_mapping.json"):
                if not isinstance(json.loads((folder / name).read_text()), dict):
                    raise ValueError("The history file contains invalid settings.")
        database = sqlite3.connect(folder / "payroll.sqlite3")
        try:
            database.execute("PRAGMA trusted_schema=OFF")
            schema = database.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
            if any(kind in ('trigger', 'view') or (sql and 'CREATE VIRTUAL TABLE' in sql.upper())
                   for kind, name, sql in schema):
                raise ValueError("The history database contains unexpected executable objects.")
            if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("The saved payroll database did not pass its integrity check.")
            for name in (LEGACY_TABLES if manifest["version"] == 1 else TABLES):
                count = database.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                if count != manifest["counts"][name]:
                    raise ValueError("The saved payroll record counts do not match.")
            for (path,) in database.execute("SELECT source_path FROM runs"):
                if path not in hashes or not path.startswith("uploads/"):
                    raise ValueError("A payroll refers to an export missing from the history file.")
        finally:
            database.close()
        return manifest
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
        raise ValueError("This history file is incomplete or invalid.") from exc


def restore_archive(raw: bytes, store: Store) -> dict:
    """Restore into an empty app only; never replace an operator's payroll."""
    for name in TABLES:
        if store.db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]:
            raise ValueError("This app already has payroll records. History can only be restored into an empty app, so existing work cannot be overwritten.")
    root = store.path.parent
    with tempfile.TemporaryDirectory(prefix="sitterwise-restore-", dir=root.parent) as tmp:
        folder = Path(tmp)
        manifest = _unpack_checked(raw, folder)
        for name in manifest["files"]:
            target = root / name
            if (target.is_symlink() or not target.resolve().is_relative_to(root.resolve())
                    or any(parent.is_symlink() for parent in target.parents if parent != root and root in parent.parents)):
                raise ValueError("The history destination contains an unsafe symbolic link.")
        # Everything is verified before changing the destination. Preserve any
        # blank-install settings if a filesystem error interrupts the restore.
        originals = {}
        written = []
        try:
            for name in manifest["files"]:
                if name == "payroll.sqlite3":
                    continue
                target = root / name
                originals[name] = target.read_bytes() if target.exists() else None
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(folder / name, target)
                os.chmod(target, 0o600)
                written.append(name)
            source = sqlite3.connect(folder / "payroll.sqlite3")
            try:
                source.backup(store.db)
                store.db.executescript(SCHEMA)
                store._add_missing_columns()
                store.normalise_roster_statuses()
            finally:
                source.close()
        except Exception:
            for name in written:
                target = root / name
                if originals[name] is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(originals[name])
            raise
        store.log("history_restored", f"Restored {manifest['counts']['runs']} payrolls from the private transfer file")
        return manifest
