import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from appdeb import sources
from appdeb.backends.brew import BrewResult
from appdeb.backends.flatpak import FlatpakResult
from appdeb.backends.snap import SnapPackage
from appdeb.models import DesktopEntry, InstalledApp


def make_app(tmp: Path, desktop_id: str, name: str, kind: str, source_ref="") -> InstalledApp:
    path = tmp / desktop_id
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        "Exec=/bin/true\n",
        encoding="utf-8",
    )
    entry = DesktopEntry(
        id=desktop_id,
        path=path,
        name=name,
        comment="",
        exec_line="/bin/true",
        icon="",
        terminal=False,
    )
    return InstalledApp(entry=entry, kind=kind, source_ref=source_ref)


class SourceMatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-sources-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _patch_managers(self, *, flatpak=(), snap=(), brew=(), snap_info=None):
        managers = {
            "flatpak.available": True,
            "snap.available": True,
            "brew.available": True,
            "flatpak.search": list(flatpak),
            "snap.search": list(snap),
            "brew.search": list(brew),
            "apps.scan_installed": [],
            "snap.info": snap_info or {"summary": ""},
        }
        patches = {}
        for key, value in managers.items():
            module_name, attribute = key.split(".")
            module = getattr(sources, module_name)
            patches[key] = mock.patch.object(
                module, attribute, return_value=value
            ) if not callable(value) else mock.patch.object(module, attribute, value)
        for patch in patches.values():
            patch.start()
            self.addCleanup(patch.stop)

    def _calculator(self):
        return make_app(
            self.tmp,
            "org.gnome.Calculator.desktop",
            "Calculator",
            "flatpak",
            source_ref="org.gnome.Calculator",
        )

    def test_calculator_keeps_only_related_candidates(self):
        self._patch_managers(
            flatpak=[
                FlatpakResult("org.gnome.Calculator", "Calculator"),
                FlatpakResult("org.kde.kalk", "Kalk"),
                FlatpakResult("io.liri.Calculator", "Calculator"),
            ],
            snap=[
                SnapPackage("gnome-calculator", "48.1", publisher="canonical"),
                SnapPackage("kstars", "3.7.6", publisher="kde"),
                SnapPackage("calculator-linux", "1.0.1", publisher="johnycode"),
            ],
            brew=[
                BrewResult("gnome-autoar", "GNOME library for archive handling"),
                BrewResult("calcure", "Modern TUI calendar"),
                BrewResult("ripgrep", "Search tool"),
            ],
            snap_info={"summary": "GNOME Calculator"},
        )
        candidates = sources.find_sources(self._calculator())

        snap_refs = {c.ref for c in candidates if c.kind == "snap"}
        brew_refs = {c.ref for c in candidates if c.kind == "brew"}
        flatpak_refs = {c.ref for c in candidates if c.kind == "flatpak"}
        self.assertIn("gnome-calculator", snap_refs)
        self.assertNotIn("kstars", snap_refs)
        self.assertNotIn("gnome-autoar", brew_refs)
        self.assertNotIn("calcure", brew_refs)
        self.assertEqual(flatpak_refs, set())
        self.assertTrue(all(c.kind != "flatpak" for c in candidates))
        gnome = next(c for c in candidates if c.ref == "gnome-calculator")
        self.assertEqual(gnome.summary, "GNOME Calculator")

    def test_explicit_include_allows_current_kind(self):
        self._patch_managers(
            flatpak=[
                FlatpakResult("org.kde.kalk", "Kalk"),
                FlatpakResult("io.liri.Calculator", "Calculator"),
            ],
        )
        candidates = sources.find_sources(self._calculator(), include=sources.KINDS)
        flatpak_refs = {c.ref for c in candidates if c.kind == "flatpak"}
        self.assertEqual(flatpak_refs, {"io.liri.Calculator"})

    def test_spotify_snap_does_not_return_unrelated_flatpaks(self):
        app = make_app(self.tmp, "spotify_spotify.desktop", "Spotify", "snap")
        self._patch_managers(
            flatpak=[
                FlatpakResult("com.spotify.Client", "Spotify"),
                FlatpakResult("org.gnome.Rhythmbox3", "Rhythmbox"),
            ],
            snap=[
                SnapPackage("spotify", "1.2.95", publisher="Spotify"),
                SnapPackage("kdeconnect", "24.08", publisher="KDE"),
            ],
            brew=[
                BrewResult("spotify_player", "Lightweight Spotify client"),
                BrewResult("mpd", "Music Player Daemon"),
            ],
            snap_info={"summary": "Music for everyone"},
        )
        candidates = sources.find_sources(app)
        flatpak_refs = {c.ref for c in candidates if c.kind == "flatpak"}
        self.assertEqual(flatpak_refs, {"com.spotify.Client"})
        self.assertNotIn("org.gnome.Rhythmbox3", flatpak_refs)
        self.assertEqual({c.kind for c in candidates}, {"flatpak", "brew"})
        self.assertNotIn("spotify", {c.ref for c in candidates if c.kind == "snap"})

    def test_cap_is_twelve_per_kind(self):
        self._patch_managers(
            snap=[SnapPackage(f"calculator-{index}", "1.0") for index in range(20)],
        )
        candidates = sources.find_sources(self._calculator(), include=("snap",))
        self.assertEqual(len(candidates), 12)
        self.assertEqual({c.kind for c in candidates}, {"snap"})


if __name__ == "__main__":
    unittest.main()
