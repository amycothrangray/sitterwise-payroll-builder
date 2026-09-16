"""Entry point for the self-contained Mac app; never requires Terminal."""
import os
from pathlib import Path
import subprocess
import sys
import traceback
from datetime import date

from payroll.paths import DATA_DIR


def main():
    os.umask(0o077)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = DATA_DIR / "app-launch.log"
    if log.exists() and log.stat().st_size > 2_000_000:
        log.replace(DATA_DIR / "app-launch.previous.log")
    stream = open(log, "a", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    try:
        from payroll import settings_files
        from payroll.store import Store
        from payroll.transfer import create_archive
        settings_files.rules_path()
        settings_files.mapping_path()
        database = DATA_DIR / "payroll.sqlite3"
        backup = DATA_DIR / "backups" / f"automatic-{date.today().isoformat()}.sitterwise"
        if database.exists() and not backup.exists():
            store = Store(database)
            try:
                if store.list_runs():
                    backup.parent.mkdir(exist_ok=True)
                    backup.write_bytes(create_archive(store))
            finally:
                store.close()
        from run import main as run
        return run()
    except Exception as exc:
        traceback.print_exc()
        subprocess.run(["/usr/bin/osascript", "-e", "on run argv",
                        "-e", 'display alert "Sitterwise Payroll" message (item 1 of argv) as critical',
                        "-e", "end run", str(exc) + "\n\nDetails are in " + str(log)], check=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
