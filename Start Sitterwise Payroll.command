#!/bin/bash
# Double-click this to run Sitterwise Payroll.
# It opens in your browser. Everything stays on this computer.

umask 077

cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "  Python 3 is not installed on this Mac."
  echo "  Install it from https://www.python.org/downloads/ and try again."
  echo ""
  read -r -p "  Press return to close. " _
  exit 1
fi

if ! "$PY" -c "import openpyxl, defusedxml" >/dev/null 2>&1; then
  echo ""
  echo "  Setting things up for the first time. This takes a moment..."
  "$PY" -m pip install --quiet --user -r requirements.txt || {
    echo "  Could not install what the app needs. Try: python3 -m pip install -r requirements.txt"
    read -r -p "  Press return to close. " _
    exit 1
  }
fi

exec "$PY" run.py
