"""Where the settings the app writes actually live.

The Settings screen writes the pay rules and the OnPay mapping. Those used
to be written straight over rules.json and onpay_mapping.json next to the
code - files git tracks. So the moment somebody saved a setting, their copy
differed from the repository, and `git pull` refused to update the app
rather than overwrite their work. Updating the app and keeping your settings
were in direct conflict.

The two files in the repository are the defaults, shipped with the code and
never written to. A copy in data/ is yours: it is made the first time the
app runs, read in preference to the default from then on, and is what the
Settings screen writes. Pulling an update can no longer touch it.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

DEFAULT_RULES = ROOT / "rules.json"
DEFAULT_MAPPING = ROOT / "onpay_mapping.json"
USER_RULES = DATA_DIR / "rules.json"
USER_MAPPING = DATA_DIR / "onpay_mapping.json"


def _mine_or_default(mine: Path, default: Path) -> Path:
    """Your copy if you have one, otherwise the one that came with the app.

    The copy is taken from whatever is on disk now, not from a pristine
    default, so settings saved before this existed are carried across rather
    than reset.
    """
    if mine.exists():
        return mine
    if default.exists():
        try:
            mine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(default, mine)
            return mine
        except OSError:
            return default        # read-only disk: still work, just do not save
    return default


def rules_path() -> Path:
    return _mine_or_default(USER_RULES, DEFAULT_RULES)


def mapping_path() -> Path:
    return _mine_or_default(USER_MAPPING, DEFAULT_MAPPING)
