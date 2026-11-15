import gzip
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from appdeb import config, storemeta
from appdeb.models import DesktopEntry, InstalledApp

FLATHUB = {
    "id": "org.gnome.Calculator",
    "name": "Calculator",
    "summary": "Perform arithmetic, scientific or financial calculations",
    "description": "<p>Calculator is an application that solves mathematical equations.</p>",
    "developer_name": "The GNOME Project",
    "urls": {"homepage": "https://apps.gnome.org/Calculator"},
    "project_license": "GPL-3.0-or-later",
    "categories": ["Utility", "Calculator"],
    "releases": [
        {
            "type": "stable",
            "version": "51.1",
            "timestamp": "1791590400",
        }
    ],
    "screenshots": [
        {
            "caption": "Main window",
            "default": True,
            "sizes": [
                {
                    "width": "410",
                    "height": "666",
                    "scale": "1x",
                    "src": "https://dl.flathub.org/media/org/gnome/Calculator/image-1_orig.png",
                },
                {
                    "width": "224",
                    "height": "363",
                    "scale": "1x",
                    "src": "https://dl.flathub.org/media/org/gnome/Calculator/image-1_224x363@1.png",
                },
            ],
        },
        {
            "caption": "Preferences",
            "default": False,
            "sizes": [
                {
                    "width": "800",
                    "height": "600",
                    "scale": "1x",
                    "src": "https://dl.flathub.org/media/org/gnome/Calculator/image-2_800.png",
                },
                {
                    "width": "640",
                    "height": "480",
                    "scale": "1x",
                    "src": "https://dl.flathub.org/media/org/gnome/Calculator/image-2_640.png",
                },
            ],
        },
    ],
}

SNAP_API = {
    "snap": {
        "name": "spotify",
        "title": "Spotify",
        "summary": "Music for everyone",
        "description": "Play music.",
        "publisher": {"display-name": "Spotify", "username": "spotify"},
        "website": "https://open.spotify.com/",
        "license": "Proprietary",
        "version": "1.2.3",
        "media": [
            {
                "type": "icon",
                "url": "https://dashboard.snapcraft.io/site_media/spotify-icon.png",
                "width": 256,
                "height": 256,
            },
            {
                "type": "screenshot",
                "url": "https://dashboard.snapcraft.io/site_media/spotify-1.png",
                "width": 1399,
                "height": 787,
            },
            {
                "type": "banner",
                "url": "https://dashboard.snapcraft.io/site_media/spotify-banner.png",
                "width": 1218,
                "height": 240,
            },
            {
                "type": "screenshot",
                "url": "https://dashboard.snapcraft.io/site_media/spotify-2.png",
                "width": 1399,
                "height": 787,
            },
            {
                "type": "screenshot",
                "url": "http://insecure.invalid/spotify-3.png",
                "width": 1399,
                "height": 787,
            },
        ],
    }
}

APPSTREAM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<components version="0.11">
  <component type="desktop-application">
    <id>org.gnome.Calculator.desktop</id>
    <name>Calculator</name>
    <summary>Perform arithmetic, scientific or financial calculations</summary>
    <description><p>Calculator is an application that solves mathematical equations.</p></description>
    <developer_name>The GNOME Project</developer_name>
    <project_license>GPL-3.0-or-later</project_license>
    <urls><url type="homepage">https://apps.gnome.org/Calculator</url></urls>
    <categories><category>Utility</category><category>Calculator</category></categories>
    <screenshots>
      <screenshot type="default">
        <image>https://dl.flathub.org/media/org/gnome/Calculator/offline.png</image>
        <caption>Main window</caption>
      </screenshot>
    </screenshots>
    <releases><release version="51.1" date="2026-10-10"/></releases>
  </component>
</components>
"""


def make_app(tmp: Path, desktop_id: str, kind: str, *, source_ref="", filename=None, extra=""):
    path = Path(filename) if filename else tmp / desktop_id
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Fixture\n"
        "Comment=A fixture application\n"
        "Exec=/bin/true\n"
        f"{extra}"
    )
    path.write_text(body, encoding="utf-8")
    entry = DesktopEntry(
        id=desktop_id,
        path=path,
        name="Fixture",
        comment="A fixture application",
        exec_line="/bin/true",
        icon="",
        terminal=False,
    )
    return InstalledApp(entry=entry, kind=kind, source_ref=source_ref)


class StoreMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-storemeta-"))
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

    def test_flathub_details_parse_and_cache(self):
        app = make_app(self.tmp, "org.gnome.Calculator.desktop", "flatpak")
        with mock.patch.object(storemeta, "_http_get_json", return_value=FLATHUB) as http:
            details = storemeta.details(app)
            self.assertIsNotNone(details)
            details_again = storemeta.details(app)
        self.assertEqual(http.call_count, 1)
        assert details is not None and details_again is not None
        self.assertEqual(details.source, "flatpak")
        self.assertEqual(details.app_id, "org.gnome.Calculator")
        self.assertEqual(details.name, "Calculator")
        self.assertEqual(details.version, "51.1")
        self.assertEqual(details.license, "GPL-3.0-or-later")
        self.assertEqual(details.homepage, "https://apps.gnome.org/Calculator")
        self.assertEqual(details.developer, "The GNOME Project")
        self.assertIn("solves mathematical equations", details.description)
        self.assertNotIn("<p>", details.description)
        self.assertEqual(details.updated, "2026-10-10")
        self.assertEqual(len(details.screenshots), 2)
        self.assertTrue(details.screenshots[0].url.endswith("_orig.png"))
        self.assertTrue(details.screenshots[1].url.endswith("image-2_640.png"))
        self.assertEqual(details.screenshots[0].caption, "Main window")
        self.assertEqual(details_again.name, "Calculator")
        cache_file = storemeta._cache_file("org.gnome.Calculator")
        self.assertTrue(cache_file.is_file())

    def test_network_failure_falls_back_to_offline_catalog(self):
        app = make_app(self.tmp, "org.gnome.Calculator.desktop", "flatpak")
        catalog = self.tmp / "appstream.xml.gz"
        with gzip.open(catalog, "wt", encoding="utf-8") as handle:
            handle.write(APPSTREAM_XML)
        with mock.patch.object(storemeta, "_appstream_paths", return_value=[catalog]):
            with mock.patch.object(storemeta, "_http_get_json", side_effect=OSError("offline")):
                details = storemeta.details(app)
        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.source, "flatpak")
        self.assertEqual(details.name, "Calculator")
        self.assertIn("solves mathematical equations", details.summary + details.description)
        self.assertEqual(details.version, "51.1")
        self.assertEqual(len(details.screenshots), 1)
        self.assertTrue(details.screenshots[0].url.endswith("offline.png"))

    def test_allow_network_false_skips_http(self):
        app = make_app(self.tmp, "org.gnome.Calculator.desktop", "flatpak")
        catalog = self.tmp / "appstream.xml.gz"
        with gzip.open(catalog, "wt", encoding="utf-8") as handle:
            handle.write(APPSTREAM_XML)
        with mock.patch.object(storemeta, "_appstream_paths", return_value=[catalog]):
            with mock.patch.object(storemeta, "_http_get_json") as http:
                details = storemeta.details(app, allow_network=False)
        http.assert_not_called()
        self.assertIsNotNone(details)

    def test_snap_details_uses_store_api_and_maps_media(self):
        app = make_app(self.tmp, "spotify_spotify.desktop", "snap")
        calls = []

        def fake_json(url, timeout=8.0, headers=None):
            calls.append((url, headers))
            return SNAP_API

        with mock.patch.object(storemeta, "_http_get_json", side_effect=fake_json):
            details = storemeta.details(app)
            details_again = storemeta.details(app)
        self.assertEqual(len(calls), 1)
        url, headers = calls[0]
        self.assertIn("api.snapcraft.io/v2/snaps/info/spotify", url)
        self.assertIn("fields=", url)
        self.assertEqual(headers, {"Snap-Device-Series": "16"})
        assert details is not None and details_again is not None
        self.assertEqual(details.source, "snap")
        self.assertEqual(details.app_id, "spotify")
        self.assertEqual(details.name, "Spotify")
        self.assertEqual(details.summary, "Music for everyone")
        self.assertEqual(details.description, "Play music.")
        self.assertEqual(details.developer, "Spotify")
        self.assertEqual(details.homepage, "https://open.spotify.com/")
        self.assertEqual(details.license, "Proprietary")
        self.assertEqual(details.version, "1.2.3")
        self.assertEqual([shot.url for shot in details.screenshots], [
            "https://dashboard.snapcraft.io/site_media/spotify-1.png",
            "https://dashboard.snapcraft.io/site_media/spotify-2.png",
        ])
        self.assertEqual(details_again.name, "Spotify")
        self.assertTrue(storemeta._cache_file("snap:spotify").is_file())

    def test_snap_details_falls_back_to_cli_on_api_failure(self):
        app = make_app(self.tmp, "spotify_spotify.desktop", "snap")
        meta = {
            "name": "spotify",
            "version": "1.2.3",
            "summary": "Music for everyone",
            "description": "Play music.",
            "publisher": "Spotify",
            "store_url": "https://snapcraft.io/spotify",
            "license": "Proprietary",
        }
        with mock.patch.object(storemeta, "_http_get_json", side_effect=OSError("offline")):
            with mock.patch.object(storemeta.snap, "info", return_value=meta):
                details = storemeta.details(app)
        assert details is not None
        self.assertEqual(details.source, "snap")
        self.assertEqual(details.app_id, "spotify")
        self.assertEqual(details.name, "spotify")
        self.assertEqual(details.summary, "Music for everyone")
        self.assertEqual(details.homepage, "https://snapcraft.io/spotify")
        self.assertEqual(details.screenshots, [])

    def test_snap_details_allow_network_false_uses_cli(self):
        app = make_app(self.tmp, "spotify_spotify.desktop", "snap")
        meta = {"name": "spotify", "summary": "Music for everyone", "version": "1.2.3"}
        with mock.patch.object(storemeta, "_http_get_json") as http:
            with mock.patch.object(storemeta.snap, "info", return_value=meta):
                details = storemeta.details(app, allow_network=False)
        http.assert_not_called()
        assert details is not None
        self.assertEqual(details.summary, "Music for everyone")

    def test_brew_details(self):
        app = make_app(self.tmp, "gh.desktop", "brew", source_ref="gh")
        meta = {
            "name": "gh",
            "description": "GitHub command-line tool",
            "homepage": "https://cli.github.com/",
            "version": "2.104.0",
            "caveats": "",
            "installed_version": "2.104.0",
        }
        with mock.patch.object(storemeta.brew, "info", return_value=meta):
            details = storemeta.details(app)
        assert details is not None
        self.assertEqual(details.source, "brew")
        self.assertEqual(details.name, "gh")
        self.assertEqual(details.version, "2.104.0")
        self.assertEqual(details.homepage, "https://cli.github.com/")

    def test_appimage_store_url_minimal_details(self):
        app = make_app(
            self.tmp,
            "appdeb-fixture.desktop",
            "appimage",
            extra="X-Appdeb-Store-Url=https://example.invalid/fixture\n",
        )
        details = storemeta.details(app, allow_network=False)
        assert details is not None
        self.assertEqual(details.source, "appimage")
        self.assertEqual(details.homepage, "https://example.invalid/fixture")

    def test_unknown_app_returns_none(self):
        app = make_app(self.tmp, "plain.desktop", "appimage")
        self.assertIsNone(storemeta.details(app))

    def test_download_screenshot_writes_and_reuses_file(self):
        image = Image.new("RGBA", (2, 2), (53, 132, 228, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = buffer.getvalue()
        shot = storemeta.StoreScreenshot(url="https://dl.flathub.org/media/shot.png")
        with mock.patch.object(storemeta, "_http_get_bytes", return_value=payload) as http:
            first = storemeta.download_screenshot("org.gnome.Calculator", shot)
            second = storemeta.download_screenshot("org.gnome.Calculator", shot)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(http.call_count, 1)
        assert first is not None
        self.assertTrue(first.name.startswith(hashlib.sha1(b"org.gnome.Calculator").hexdigest()))
        with Image.open(first) as opened:
            self.assertEqual(opened.size, (2, 2))

    def test_download_rejects_non_https(self):
        shot = storemeta.StoreScreenshot(url="http://example.invalid/shot.png")
        self.assertIsNone(storemeta.download_screenshot("fixture", shot))

    def test_clear_cache_removes_metadata_and_screenshots(self):
        app = make_app(self.tmp, "org.gnome.Calculator.desktop", "flatpak")
        with mock.patch.object(storemeta, "_http_get_json", return_value=FLATHUB):
            storemeta.details(app)
        self.assertTrue(storemeta._cache_file("org.gnome.Calculator").is_file())
        storemeta.clear_cache()
        self.assertFalse(storemeta._cache_file("org.gnome.Calculator").is_file())
        self.assertFalse(storemeta._screenshot_dir().exists())


if __name__ == "__main__":
    unittest.main()
