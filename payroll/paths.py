"""Keep a distributed application's records outside its replaceable bundle."""
from pathlib import Path
import os
import sys


APP_ROOT = Path(__file__).resolve().parent.parent


def data_directory() -> Path:
    override = os.environ.get("SITTERWISE_PAYROLL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Application Support" / "Sitterwise Payroll"
    return APP_ROOT / "data"


DATA_DIR = data_directory()
