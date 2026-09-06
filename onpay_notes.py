#!/usr/bin/env python3
"""Put the note on every OnPay payroll line, one at a time.

    python3 onpay_notes.py              walk through the latest payroll
    python3 onpay_notes.py --list       just print them all
    python3 onpay_notes.py --run ID     a particular payroll

Caregivers can see these notes on their pay stubs, which is why Ethan typed
the job dates and family names onto each line. The app already works out the
wording; this hands it over one line at a time with the note already on the
clipboard, so each one is a click and a paste rather than something to read
off a screen and retype.

It does not touch OnPay. It cannot change an amount, and it cannot run
payroll. Everything it does happens on this computer.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from payroll import exports                                          # noqa: E402
from payroll.server import load_run                                  # noqa: E402
from payroll.store import Store                                      # noqa: E402


class Clipboard:
    """The Mac clipboard, or nothing if this is not a Mac."""

    def __init__(self):
        self.tool = shutil.which("pbcopy") or shutil.which("xclip")
        self.args = [self.tool] if self.tool and self.tool.endswith("pbcopy") else \
            ([self.tool, "-selection", "clipboard"] if self.tool else None)

    def copy(self, text: str) -> bool:
        if not self.args:
            return False
        try:
            subprocess.run(self.args, input=text.encode("utf-8"), check=True)
            return True
        except (subprocess.SubprocessError, OSError):
            return False


def pay_lines(store: Store, run_id: str) -> tuple[dict, list[dict]]:
    """Every pay line that has something to say, in the order they are entered."""
    record, run, roster = load_run(store, run_id)
    mapping = exports.load_onpay_mapping()
    statuses = run.summary["statuses"]

    lines = []
    for caregiver in run.caregivers:
        if statuses.get(caregiver.key) == "blocked":
            continue                      # not in OnPay at all, so nothing to note
        entry = roster.get(caregiver.key)
        clock = entry.onpay_clock_user if entry else ""
        if not clock:
            continue                      # entered by hand; the app says who
        for row in exports.onpay_pay_rows(caregiver, clock, mapping):
            if not row.get("note"):
                continue
            lines.append({
                "caregiver": caregiver.name,
                "clock": clock,
                "item": exports.onpay_pay_item_name(row["id"], mapping),
                "amount": exports.onpay_row_total(row),
                "hours": row["hours"],
                "rate": row["rate"],
                "note": row["note"],
            })
    return record, lines


def describe(line: dict) -> str:
    """For reading, so the rate is shown to the cent rather than to four
    decimal places - the file keeps the full precision, a person does not
    need it."""
    if line["hours"]:
        return f"{line['hours']}h @ ${line['rate']:.2f} = ${line['amount']}"
    return f"${line['amount']}"


class Progress:
    """Where the walkthrough got to, so being interrupted costs nothing.

    Forty notes is a long enough sit that a phone call should not mean
    starting over. Kept beside the payroll history, not in it - losing this
    file loses nothing but your place.
    """

    def __init__(self, store: Store, run_id: str):
        self.path = Path(store.path).parent / "onpay-notes-progress.json"
        self.run_id = run_id

    def _all(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @property
    def done(self) -> int:
        try:
            return int(self._all().get(self.run_id, 0))
        except (TypeError, ValueError):
            return 0

    def save(self, count: int) -> None:
        data = self._all()
        data[self.run_id] = count
        try:
            self.path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass                          # losing your place is not worth a crash

    def clear(self) -> None:
        data = self._all()
        data.pop(self.run_id, None)
        try:
            self.path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass


def latest_run(store: Store) -> str | None:
    runs = store.list_runs()
    if not runs:
        return None
    open_runs = [r for r in runs if r["status"] == "open"]
    return (open_runs or runs)[0]["id"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Put the note on every OnPay payroll line, one at a time.")
    parser.add_argument("--run", help="a payroll run id (default: the latest one)")
    parser.add_argument("--list", action="store_true",
                        help="print every note and stop, without walking through them")
    parser.add_argument("--data", type=Path, default=None, help="where the payroll history is")
    parser.add_argument("--restart", action="store_true",
                        help="start from the first note again, ignoring where you got to")
    args = parser.parse_args()

    store = Store(args.data)
    try:
        run_id = args.run or latest_run(store)
        if not run_id:
            print("\n  There are no payrolls yet. Build one in the app first.\n")
            return 1

        record, lines = pay_lines(store, run_id)
        print(f"\n  {record['label']}")

        if not lines:
            print("\n  No notes to add. Either nobody has a Clock User yet, or this "
                  "payroll\n  has nothing worth noting on a line.\n")
            return 0

        if args.list:
            print()
            for line in lines:
                print(f"  {line['caregiver']} · {line['item']} · {describe(line)}")
                print(f"      {line['note']}\n")
            print(f"  {len(lines)} notes.\n")
            return 0

        progress = Progress(store, run_id)
        start_at = 0 if args.restart else min(progress.done, len(lines))
        if args.restart:
            progress.clear()

        clipboard = Clipboard()
        if start_at:
            print(f"\n  You did {start_at} of {len(lines)} last time. "
                  f"Carrying on from {start_at + 1}.")
            print("  Run this with --restart to go back to the beginning.\n")
        print(f"  {len(lines) - start_at} notes to put on payroll lines in OnPay.\n")
        print("  Open the payroll in OnPay, then for each one: click the note box on")
        print("  that line, paste, and press return here for the next.")
        if not clipboard.args:
            print("\n  (No clipboard on this machine, so the notes are printed to copy)")
        print("  Press control-C to stop. Nothing here changes OnPay.\n")

        for number, line in enumerate(lines[start_at:], start_at + 1):
            print("  " + "-" * 66)
            print(f"  {number} of {len(lines)}   {line['caregiver']}"
                  f"   (Clock User {line['clock']})")
            print(f"  {line['item']} · {describe(line)}")
            print(f"\n      {line['note']}\n")
            copied = clipboard.copy(line["note"])
            prompt = ("  Copied. Paste it in OnPay, then press return "
                      if copied else "  Copy the line above, then press return ")
            try:
                input(prompt)
            except (KeyboardInterrupt, EOFError):
                print(f"\n\n  Stopped after {number - 1} of {len(lines)}. "
                      "Run this again to carry on from there.\n")
                return 0
            progress.save(number)

        progress.clear()
        print("  " + "-" * 66)
        print(f"\n  That is all {len(lines)}. Nothing in OnPay was changed by this "
              "script -\n  you pasted every one of them yourself.\n")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
