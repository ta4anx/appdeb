import unittest
from unittest import mock

from appdeb.backends import flatpak
from appdeb.backends.runner import CommandResult

LIST_OUTPUT = "\n".join(
    [
        "com.nextcloud.desktopclient.nextcloud\tNextcloud Desktop Client\t35.0.3\tstable\tx86_64\tflathub\t29.6 MB\tsystem",
        "org.freecad.FreeCAD\tFreeCAD\t1.1.4\tstable\tx86_64\tflathub\t1.3 GB\tuser",
        "dk.pracedru.Symbolical\tSymbolical\t0.9.0.0\tstable\tx86_64\tflathub\t340.3 MB\tsystem",
    ]
)

SEARCH_OUTPUT = "\n".join(
    [
        "org.gnome.Calculator\tCalculator\tPerform arithmetic\t46.0\tstable\tflathub",
        "org.gnome.Calculator\tCalculator\tPerform arithmetic\t46.0\tstable\tflathub-beta",
        "org.gnome.Calculator.Devel\tCalculator (Devel)\tPerform arithmetic\t47.0\tmaster\tflathub",
        "org.kde.kcalc\tKCalc\tScientific calculator\t25.08\tstable\tflathub",
    ]
)

UPDATES_OUTPUT = "\n".join(
    [
        "org.kde.kdenlive\tKdenlive\t26.08.4\tstable\tflathub",
        "org.kde.kile\tKile\t3.0b5\tstable\tflathub",
    ]
)


def fake_result(args, **kwargs):
    joined = " ".join(args)
    if "list" in joined:
        output = LIST_OUTPUT
    elif "search" in joined:
        output = SEARCH_OUTPUT
    elif "remote-ls" in joined:
        output = UPDATES_OUTPUT
    else:
        output = ""
    return CommandResult(args=[str(a) for a in args], returncode=0, output=output, lines=output.splitlines())


class FlatpakParseTests(unittest.TestCase):
    def test_list_parses_columns_sizes_and_scope(self):
        with mock.patch.object(flatpak.runner, "run", side_effect=fake_result):
            apps = flatpak.list_installed()
        self.assertEqual(len(apps), 3)
        first = apps[0]
        self.assertEqual(first.application, "com.nextcloud.desktopclient.nextcloud")
        self.assertEqual(first.name, "Nextcloud Desktop Client")
        self.assertEqual(first.version, "35.0.3")
        self.assertEqual(first.branch, "stable")
        self.assertEqual(first.arch, "x86_64")
        self.assertEqual(first.origin, "flathub")
        self.assertEqual(first.size_bytes, 29600000)
        self.assertEqual(first.scope, "system")

        sizes = {app.application: app.size_bytes for app in apps}
        self.assertEqual(sizes["org.freecad.FreeCAD"], 1300000000)
        self.assertEqual(sizes["dk.pracedru.Symbolical"], 340300000)
        self.assertEqual({app.scope for app in apps}, {"system", "user"})

    def test_search_deduplicates_by_application_and_remote(self):
        with mock.patch.object(flatpak.runner, "run", side_effect=fake_result):
            results = flatpak.search("calculator")
        pairs = [(r.application, r.remotes) for r in results]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertIn(("org.gnome.Calculator", "flathub"), pairs)
        self.assertIn(("org.kde.kcalc", "flathub"), pairs)
        calculator = [r for r in results if r.application == "org.gnome.Calculator"]
        self.assertEqual(len(calculator), 2)
        self.assertEqual(calculator[0].name, "Calculator")
        self.assertEqual(calculator[0].version, "46.0")

    def test_search_requires_two_characters(self):
        with mock.patch.object(flatpak.runner, "run", side_effect=fake_result) as run:
            self.assertEqual(flatpak.search("a"), [])
            self.assertEqual(flatpak.search("   "), [])
        run.assert_not_called()

    def test_updates_parse(self):
        with mock.patch.object(flatpak.runner, "run", side_effect=fake_result):
            updates = flatpak.updates()
        self.assertEqual([u.application for u in updates], ["org.kde.kdenlive", "org.kde.kile"])
        self.assertEqual(updates[0].version, "26.08.4")

    def test_set_override_rejects_unknown_action(self):
        with self.assertRaises(flatpak.BackendError):
            flatpak.set_override("org.gnome.Calculator", "root", "/")

    def test_info_missing_returns_empty_dict_on_failure(self):
        failing = CommandResult(args=["flatpak"], returncode=1, output="error", lines=["error"])
        with mock.patch.object(flatpak.runner, "run", return_value=failing):
            self.assertEqual(flatpak.info("org.gnome.Calculator", "user"), {})


if __name__ == "__main__":
    unittest.main()
