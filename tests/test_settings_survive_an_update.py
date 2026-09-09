"""Settings and app updates must not fight each other.

The Settings screen used to write straight over rules.json and
onpay_mapping.json - files git tracks. So the moment somebody saved a
setting, `git pull` refused to update the app rather than overwrite their
work, and updating the app and keeping your settings became a choice. That
is what blocked the four-hour minimum fix from reaching Amy's Mac.

The files in the repository are defaults now. A copy in data/ is yours.

Run them with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payroll import settings_files                                       # noqa: E402


class YourSettingsLiveInTheDataFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.shipped = self.root / "rules.json"
        self.shipped.write_text(json.dumps({"version": "shipped"}), encoding="utf-8")
        self.mine = self.root / "data" / "rules.json"

    def tearDown(self):
        self.tmp.cleanup()

    def chosen(self):
        return settings_files._mine_or_default(self.mine, self.shipped)

    def test_the_first_read_makes_your_copy(self):
        self.assertFalse(self.mine.exists())
        self.assertEqual(self.chosen(), self.mine)
        self.assertTrue(self.mine.exists())

    def test_your_copy_starts_as_whatever_was_already_set(self):
        """Settings saved before this existed are carried across, not reset."""
        self.shipped.write_text(json.dumps({"version": "amy's edits"}), encoding="utf-8")
        self.chosen()
        self.assertEqual(json.loads(self.mine.read_text())["version"], "amy's edits")

    def test_your_copy_is_preferred_once_it_exists(self):
        self.mine.parent.mkdir(parents=True, exist_ok=True)
        self.mine.write_text(json.dumps({"version": "mine"}), encoding="utf-8")
        self.assertEqual(self.chosen(), self.mine)
        self.assertEqual(json.loads(self.chosen().read_text())["version"], "mine")

    def test_an_update_to_the_shipped_file_does_not_disturb_yours(self):
        self.chosen()
        self.mine.write_text(json.dumps({"version": "mine"}), encoding="utf-8")
        self.shipped.write_text(json.dumps({"version": "newer"}), encoding="utf-8")
        self.assertEqual(json.loads(self.chosen().read_text())["version"], "mine")

    def test_saving_never_touches_the_shipped_file(self):
        self.chosen().write_text(json.dumps({"version": "saved"}), encoding="utf-8")
        self.assertEqual(json.loads(self.shipped.read_text())["version"], "shipped")

    def test_a_read_only_disk_still_gives_something_usable(self):
        """Losing the ability to save a setting must not stop payroll."""
        missing = self.root / "nowhere" / "deep" / "rules.json"
        shutil.rmtree(self.root / "data", ignore_errors=True)
        (self.root / "nowhere").write_text("not a directory", encoding="utf-8")
        self.assertEqual(settings_files._mine_or_default(missing, self.shipped),
                         self.shipped)


class TheRealFilesAreWiredUp(unittest.TestCase):
    def test_the_rules_the_app_reads_are_the_ones_in_data(self):
        self.assertEqual(settings_files.rules_path(), settings_files.USER_RULES)

    def test_the_mapping_the_app_reads_is_the_one_in_data(self):
        self.assertEqual(settings_files.mapping_path(), settings_files.USER_MAPPING)

    def test_the_shipped_defaults_are_still_there_to_copy_from(self):
        self.assertTrue(settings_files.DEFAULT_RULES.exists())
        self.assertTrue(settings_files.DEFAULT_MAPPING.exists())


if __name__ == "__main__":
    unittest.main()
