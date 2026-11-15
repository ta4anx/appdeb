import os
import shutil
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio
from PIL import Image

from appdeb import config, icons


class IconsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-icons-"))
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

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def make_png(self, name: str, size: int = 32) -> Path:
        path = self.tmp / name
        Image.new("RGBA", (size, size), (53, 132, 228, 255)).save(path)
        return path

    def test_install_png_creates_theme_entry(self):
        source = self.make_png("icon.png")
        name = icons.install_icon(source, "Antigravity.desktop")
        self.assertRegex(name, r"^appdeb-[0-9a-f]{12}$")

        target = config.user_icon_dir() / "256x256" / "apps" / f"{name}.png"
        self.assertTrue(target.is_file())
        with Image.open(target) as image:
            self.assertEqual(image.size, (32, 32))
            self.assertEqual(image.mode, "RGBA")

        self.assertTrue((config.user_icon_dir() / "index.theme").is_file())
        resolved = icons.resolve(name)
        self.assertIsInstance(resolved, Gio.ThemedIcon)

    def test_install_svg_goes_to_scalable(self):
        source = self.tmp / "icon.svg"
        source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>')
        name = icons.install_icon(source, "cura.desktop")
        self.assertTrue((config.user_icon_dir() / "scalable" / "apps" / f"{name}.svg").is_file())

    def test_invalid_image_raises(self):
        bad = self.tmp / "not-an-image.png"
        bad.write_text("nope")
        with self.assertRaises(ValueError):
            icons.install_icon(bad, "broken.desktop")

    def test_resolve_absolute_and_missing(self):
        existing = self.make_png("abs.png")
        self.assertIsInstance(icons.resolve(str(existing)), Gio.FileIcon)
        self.assertIsInstance(icons.resolve("/nonexistent/icon.png"), Gio.ThemedIcon)
        self.assertIsInstance(icons.resolve(""), Gio.ThemedIcon)

    def test_remove_icon_only_touches_appdeb_names(self):
        name = icons.install_icon(self.make_png("icon.png"), "Antigravity.desktop")
        target = config.user_icon_dir() / "256x256" / "apps" / f"{name}.png"
        icons.remove_icon(name)
        self.assertFalse(target.exists())
        icons.remove_icon("org.gnome.Nautilus")


if __name__ == "__main__":
    unittest.main()
