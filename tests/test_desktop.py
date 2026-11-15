import os
import shutil
import tempfile
import unittest
from pathlib import Path

from appdeb import config, db, desktop

PLACEHOLDER = """[Desktop Entry]
Version=1.0
Type=Application
Name=My App Name
Comment=Description of my app
Exec=/opt/myapp/bin/myapp
Icon=/opt/myapp/icon.png
Terminal=false
Categories=Utility;Application;
"""

REAL = """[Desktop Entry]
Type=Application
Version=1.5
Name=Antigravity
Comment=Agent console
Exec=/opt/Antigravity-x64/antigravity
Icon=/home/kaan/Apps/antigravity.png
Terminal=false
Categories=Development;
StartupWMClass=antigravity
"""


class DesktopTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._env = {}
        for key, sub in (
            ("HOME", "home"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_CACHE_HOME", "cache"),
        ):
            self._env[key] = os.environ.get(key)
            os.environ[key] = str(self.tmp / sub)
        self._env["XDG_DATA_DIRS"] = os.environ.get("XDG_DATA_DIRS")
        os.environ["XDG_DATA_DIRS"] = str(self.tmp / "system" / "share")
        config.ensure_dirs()
        db.init()
        (self.tmp / "home" / "Desktop").mkdir(parents=True, exist_ok=True)
        (self.tmp / "system" / "share" / "applications").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def write_system_entry(self, name: str, text: str) -> Path:
        path = self.tmp / "system" / "share" / "applications" / name
        path.write_text(text, encoding="utf-8")
        return path


class DuplicateGroupTests(DesktopTestCase):
    def test_duplicate_groups_are_counted_and_repaired(self):
        path = self.write_system_entry("Antigravity.desktop", PLACEHOLDER + REAL)
        self.assertEqual(desktop.duplicate_group_count(path), 2)

        entry = desktop.load(path)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.name, "Antigravity")
        self.assertEqual(entry.exec_line, "/opt/Antigravity-x64/antigravity")

        self.assertTrue(desktop.repair_duplicate_groups(path))
        self.assertEqual(desktop.duplicate_group_count(path), 1)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("My App Name", text)
        self.assertIn("Name=Antigravity", text)

        backups = list(config.backup_dir().glob("desktop-repair-*/*.desktop"))
        self.assertTrue(backups, "expected a backup copy of the repaired entry")

    def test_repair_is_a_noop_for_single_group(self):
        path = self.write_system_entry("appdeb-single.desktop", REAL)
        self.assertFalse(desktop.repair_duplicate_groups(path))

    def test_scan_deduplicates_by_desktop_id(self):
        self.write_system_entry("appdeb-single.desktop", REAL)
        (config.user_applications_dir() / "appdeb-single.desktop").write_text(
            REAL.replace("Name=Antigravity", "Name=Overridden"), encoding="utf-8"
        )
        entries = [e for e in desktop.scan() if e.id == "appdeb-single.desktop"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Overridden")
        self.assertEqual(entries[0].source, "user")


class RenameTests(DesktopTestCase):
    def test_rename_creates_user_override_and_keeps_original(self):
        path = self.write_system_entry("Antigravity.desktop", REAL)
        entry = desktop.load(path)
        self.assertIsNotNone(entry)

        renamed = desktop.rename(entry, "  Agent   Console  ")
        self.assertEqual(renamed.name, "Agent Console")
        self.assertEqual(renamed.path, config.user_applications_dir() / "Antigravity.desktop")
        self.assertEqual(renamed.source, "user")

        override = desktop.load_keyfile(renamed.path)
        self.assertEqual(
            override.get_string("Desktop Entry", "X-Appdeb-OriginalName"), "Antigravity"
        )
        self.assertEqual(desktop.load_keyfile(path).get_string("Desktop Entry", "Name"), "Antigravity")
        self.assertEqual((config.user_applications_dir() / "Antigravity.desktop").stat().st_mode & 0o777, 0o644)

        row = db.get("Antigravity.desktop")
        self.assertEqual(row["display_name"], "Agent Console")
        self.assertEqual(row["original_name"], "Antigravity")

    def test_rename_strips_locale_variants_and_rejects_bad_input(self):
        path = self.write_system_entry("appdeb-locale.desktop", REAL + "Name[tr]=Antigravite\n")
        entry = desktop.load(path)
        renamed = desktop.rename(entry, "Antigravity Two")
        keys = desktop._keys(desktop.load_keyfile(renamed.path))
        self.assertFalse([k for k in keys if k.startswith("Name[")])
        with self.assertRaises(ValueError):
            desktop.rename(entry, "   ")
        with self.assertRaises(ValueError):
            desktop.rename(entry, "bad/name")


class SetStringTests(DesktopTestCase):
    def test_set_string_writes_override_and_preserves_keys(self):
        path = self.write_system_entry("cura.desktop", REAL.replace("Antigravity", "Ultimaker Cura"))
        entry = desktop.load(path)
        self.assertEqual(entry.startup_wm_class, "antigravity")

        updated = desktop.set_string(entry, "StartupWMClass", "UltiMaker-Cura")
        self.assertEqual(updated.startup_wm_class, "UltiMaker-Cura")
        self.assertEqual(updated.path.parent, config.user_applications_dir())

        kf = desktop.load_keyfile(updated.path)
        self.assertEqual(kf.get_string("Desktop Entry", "Comment"), "Agent console")
        self.assertEqual(
            kf.get_string("Desktop Entry", "X-Appdeb-OriginalWMClass"), "antigravity"
        )

    def test_remove_key(self):
        path = self.write_system_entry("appdeb-remove.desktop", REAL)
        entry = desktop.load(path)
        updated = desktop.remove_key(entry, "StartupWMClass")
        self.assertEqual(updated.startup_wm_class, "")
        self.assertEqual(desktop.load(path).startup_wm_class, "antigravity")


class ShortcutTests(DesktopTestCase):
    def test_create_shortcut_is_executable_and_unique(self):
        path = self.write_system_entry("Antigravity.desktop", REAL)
        entry = desktop.load(path)

        first = desktop.create_shortcut(entry)
        self.assertEqual(first.name, "Antigravity.desktop")
        self.assertTrue(first.is_file())
        self.assertEqual(first.stat().st_mode & 0o777, 0o755)

        second = desktop.create_shortcut(entry)
        self.assertEqual(second.name, "Antigravity (2).desktop")

        self.assertTrue(desktop.remove_shortcut(entry))
        self.assertFalse(first.exists() or second.exists())

    def test_safe_shortcut_name(self):
        self.assertEqual(desktop.safe_shortcut_name("a/b\\c"), "abc")
        self.assertEqual(desktop.safe_shortcut_name("   "), "Application")


class ValidationTests(DesktopTestCase):
    def test_validate_reports_duplicate_group_error(self):
        path = self.write_system_entry("appdeb-broken.desktop", PLACEHOLDER + REAL)
        messages = desktop.validate(path)
        self.assertTrue(messages, "duplicate groups must be reported by desktop-file-validate")

    def test_validate_clean_entry(self):
        path = self.write_system_entry("appdeb-clean.desktop", REAL)
        self.assertEqual(desktop.validate(path), [])

    def test_hidden_entries_can_be_included(self):
        path = self.write_system_entry("appdeb-hidden.desktop", REAL + "NoDisplay=true\n")
        self.assertFalse([e for e in desktop.scan() if e.id == path.name])
        self.assertTrue([e for e in desktop.scan(include_hidden=True) if e.id == path.name])


if __name__ == "__main__":
    unittest.main()
