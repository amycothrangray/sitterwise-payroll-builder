#!/usr/bin/env python3
"""Start Sitterwise Payroll.

    python3 run.py              open the app in a browser
    python3 run.py --port 9000  use a different port
    python3 run.py --no-browser just start the server
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    parser = argparse.ArgumentParser(description="Sitterwise payroll preparation")
    parser.add_argument("--port", type=int, default=8756)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data", type=Path, default=None,
                        help="where to keep payroll history (default: data/payroll.sqlite3)")
    args = parser.parse_args()

    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("\n  This app needs openpyxl to read Sitterwise's .xlsx exports.")
        print("  Install it with:  python3 -m pip install openpyxl\n")
        return 1

    from payroll.server import serve
    serve(port=args.port, open_browser=not args.no_browser, data_path=args.data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
