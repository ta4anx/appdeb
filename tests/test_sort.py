import unittest
from pathlib import Path

from appdeb.models import DesktopEntry, InstalledApp
from appdeb.ui.installed import sort_apps


def make_app(
    name,
    *,
    desktop_id=None,
    kind="system",
    size_bytes=0,
    version="",
    install_date=None,
    last_launched=None,
):
    entry = DesktopEntry(
        id=desktop_id or f"{name.lower()}.desktop",
        path=Path("/nonexistent") / (desktop_id or f"{name.lower()}.desktop"),
        name=name,
        comment="",
        exec_line="",
        icon="",
        terminal=False,
    )
    db = {}
    if install_date is not None:
        db["install_date"] = install_date
    if last_launched is not None:
        db["last_launched"] = last_launched
    return InstalledApp(
        entry=entry,
        kind=kind,
        version=version,
        size_bytes=size_bytes,
        db=db,
    )


class SortAppsTests(unittest.TestCase):
    def setUp(self):
        self.alpha = make_app(
            "Alpha", kind="flatpak", size_bytes=100, version="1.9",
            install_date="2024-01-01T00:00:00+00:00",
        )
        self.bravo = make_app(
            "bravo", kind="appimage", size_bytes=300, version="1.10",
            install_date="2024-03-01T00:00:00+00:00",
        )
        self.charlie = make_app(
            "Charlie", kind="flatpak", size_bytes=200, version="1.2",
            last_launched="2024-02-01T00:00:00+00:00",
        )
        self.delta = make_app("delta", kind="snap", size_bytes=50)
        self.apps = [self.bravo, self.delta, self.alpha, self.charlie]

    def names(self, key):
        return [app.display_name for app in sort_apps(self.apps, key)]

    def test_name_is_case_insensitive(self):
        self.assertEqual(self.names("name"), ["Alpha", "bravo", "Charlie", "delta"])

    def test_name_desc(self):
        self.assertEqual(self.names("name_desc"), ["delta", "Charlie", "bravo", "Alpha"])

    def test_kind_uses_label_then_name(self):
        self.assertEqual(self.names("kind"), ["bravo", "Alpha", "Charlie", "delta"])

    def test_size_is_largest_first_with_name_tiebreak(self):
        self.assertEqual(self.names("size"), ["bravo", "Charlie", "Alpha", "delta"])
        tie_a = make_app("Zeta", size_bytes=500)
        tie_b = make_app("Alpha", size_bytes=500, desktop_id="alpha2.desktop")
        self.assertEqual(
            [app.display_name for app in sort_apps([tie_a, tie_b], "size")],
            ["Alpha", "Zeta"],
        )

    def test_version_is_natural(self):
        self.assertEqual(self.names("version"), ["Charlie", "Alpha", "bravo", "delta"])
        versions = [
            make_app("a", version="1.2"),
            make_app("b", version="1.10"),
            make_app("c", version="1.9"),
        ]
        self.assertEqual(
            [app.version for app in sort_apps(versions, "version")],
            ["1.2", "1.9", "1.10"],
        )

    def test_recent_uses_install_date_or_last_launched_missing_last(self):
        self.assertEqual(self.names("recent"), ["bravo", "Charlie", "Alpha", "delta"])

    def test_unknown_key_falls_back_to_name(self):
        self.assertEqual(self.names("bogus"), self.names("name"))


if __name__ == "__main__":
    unittest.main()
