import os
import shutil
import tempfile
import unittest
from pathlib import Path

from appdeb import config, db


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-db-"))
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
        config.ensure_dirs()
        db.init()

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_upsert_get_delete(self):
        db.upsert("appdeb-fixture.desktop", kind="appimage", display_name="Fixture")
        row = db.get("appdeb-fixture.desktop")
        self.assertEqual(row["kind"], "appimage")
        self.assertEqual(row["display_name"], "Fixture")

        db.upsert("appdeb-fixture.desktop", version="1.2.3")
        row = db.get("appdeb-fixture.desktop")
        self.assertEqual(row["display_name"], "Fixture")
        self.assertEqual(row["version"], "1.2.3")

        self.assertIn("appdeb-fixture.desktop", db.all_rows())
        db.delete("appdeb-fixture.desktop")
        self.assertIsNone(db.get("appdeb-fixture.desktop"))

    def test_upsert_ignores_unknown_fields(self):
        db.upsert("appdeb-x.desktop", nope="value")
        self.assertIsNotNone(db.get("appdeb-x.desktop"))

    def test_settings_roundtrip(self):
        self.assertIsNone(db.setting("scope"))
        self.assertEqual(db.setting("scope", "user"), "user")
        db.set_setting("scope", "system")
        self.assertEqual(db.setting("scope"), "system")
        db.set_setting("scope", "user")
        self.assertEqual(db.setting("scope"), "user")

    def test_wal_mode_enabled(self):
        db.upsert("appdeb-wal.desktop", kind="deb")
        with db.connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()
