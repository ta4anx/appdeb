import unittest
from unittest import mock

from appdeb.backends import snap
from appdeb.backends.runner import BackendError, CommandResult

LIST_OUTPUT = "\n".join(
    [
        "Name                       Version                         Rev    Tracking            Publisher            Notes",
        "arduino                    1.8.19                          97     latest/stable       snapcrafters*        -",
        "blender                    5.3.0                           7998   latest/stable       blenderfoundation**  classic",
        "gnome-calculator           48.1                            972    latest/stable       canonical**          -",
        "WARNING: There is 1 new warning. See 'snap warnings'.",
    ]
)

FIND_OUTPUT = "\n".join(
    [
        "Name                         Version               Publisher          Notes    Summary",
        "calculator-linux             1.0.1                 johnycode          -        Math Solver & Calculator",
        "gnome-calculator             48.1                  canonical**        -        GNOME Calculator",
        "kcalc                        26.04.3               kde**              -        Scientific calculator",
    ]
)

INFO_OUTPUT = "\n".join(
    [
        "name:      gnome-calculator",
        "summary:   GNOME Calculator",
        "publisher: Canonical**",
        "store-url: https://snapcraft.io/gnome-calculator",
        "license:   GPL-3.0+",
        "description: |",
        "  GNOME Calculator is an application that solves mathematical equations.",
        "  Though it at first appears to be a simple calculator with only basic",
        "  arithmetic operations, you can switch into Advanced, Financial, or",
        "  Programming mode to find a surprising set of capabilities.",
        " ",
        "  The Advanced calculator supports many operations, including:",
        "  logarithms, factorials, trigonometric and hyperbolic functions.",
        "snap-id: J8OcDPQ0JM8dbvk29HRqpWVI9kBw0atG",
        "confinement: strict",
        "channels:",
        "  latest/stable:    48.1 2025-05-02 (972) 2.15MB -",
        "  latest/candidate: 48.1 2025-06-11 (975) 2.15MB -",
        "  latest/beta:      ^",
        "  latest/edge:      ^",
    ]
)


def fake_result(args, **kwargs):
    joined = " ".join(str(a) for a in args)
    if "find" in joined:
        output = FIND_OUTPUT
    elif "info" in joined:
        output = INFO_OUTPUT
    elif "version" in joined:
        output = "snap    2.77.1+ubuntu26.04.2\nsnapd   2.77.1\nseries  16"
    elif "list" in joined:
        output = LIST_OUTPUT
    else:
        output = ""
    return CommandResult(
        args=[str(a) for a in args],
        returncode=0,
        output=output,
        lines=output.splitlines(),
    )


class SnapParseTests(unittest.TestCase):
    def test_parse_list_columns(self):
        packages = snap.parse_list(LIST_OUTPUT)
        self.assertEqual([p.name for p in packages], ["arduino", "blender", "gnome-calculator"])
        self.assertEqual(packages[0].version, "1.8.19")
        self.assertEqual(packages[0].revision, "97")
        self.assertEqual(packages[0].tracking, "latest/stable")
        self.assertEqual(packages[0].publisher, "snapcrafters")
        self.assertEqual(packages[1].notes, "classic")

    def test_parse_find_names_and_publisher(self):
        packages = snap.parse_find(FIND_OUTPUT)
        self.assertEqual(
            [p.name for p in packages],
            ["calculator-linux", "gnome-calculator", "kcalc"],
        )
        self.assertEqual(packages[0].publisher, "johnycode")
        self.assertEqual(packages[1].publisher, "canonical")
        self.assertEqual(packages[2].version, "26.04.3")

    def test_parse_info_description_and_stable_version(self):
        data = snap.parse_info(INFO_OUTPUT)
        self.assertEqual(data["name"], "gnome-calculator")
        self.assertEqual(data["version"], "48.1")
        self.assertEqual(data["summary"], "GNOME Calculator")
        self.assertEqual(data["publisher"], "Canonical")
        self.assertEqual(data["store_url"], "https://snapcraft.io/gnome-calculator")
        self.assertEqual(data["license"], "GPL-3.0+")
        self.assertEqual(data["confinement"], "strict")
        self.assertIn("solves mathematical equations", data["description"])

    def test_parse_info_keeps_blank_paragraphs(self):
        data = snap.parse_info(INFO_OUTPUT)
        self.assertIn("capabilities.", data["description"])
        self.assertIn("The Advanced calculator", data["description"])

    def test_list_installed_and_version(self):
        with mock.patch.object(snap.runner, "run", side_effect=fake_result):
            packages = snap.list_installed()
            self.assertEqual(len(packages), 3)
            self.assertEqual(snap.version(), "2.77.1+ubuntu26.04.2")

    def test_search_requires_two_chars(self):
        with mock.patch.object(snap.runner, "run", side_effect=fake_result) as run:
            self.assertEqual(snap.search("c"), [])
            run.assert_not_called()

    def test_search_uses_color_never(self):
        with mock.patch.object(snap.runner, "run", side_effect=fake_result) as run:
            results = snap.search("calculator")
        self.assertEqual(len(results), 3)
        self.assertEqual(
            run.call_args.args[0], ["snap", "find", "--color=never", "calculator"]
        )

    def test_info_returns_empty_on_failure(self):
        failing = CommandResult(["snap"], 1, "error: no snap found", ["error"])
        with mock.patch.object(snap.runner, "run", return_value=failing):
            self.assertEqual(snap.info("missing"), {})

    def test_install_uses_pkexec(self):
        with mock.patch.object(snap.runner, "run", return_value=CommandResult([], 0)) as run:
            snap.install("gnome-calculator")
        self.assertEqual(
            run.call_args.args[0],
            ["pkexec", "snap", "install", "gnome-calculator"],
        )

    def test_uninstall_and_refresh_argv(self):
        with mock.patch.object(snap.runner, "run", return_value=CommandResult([], 0)) as run:
            snap.uninstall("gnome-calculator", purge=True)
            snap.uninstall("gnome-calculator")
            snap.refresh(None)
            snap.refresh("gnome-calculator")
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            calls[0],
            ["pkexec", "snap", "remove", "--purge", "gnome-calculator"],
        )
        self.assertEqual(calls[1], ["pkexec", "snap", "remove", "gnome-calculator"])
        self.assertEqual(calls[2], ["pkexec", "snap", "refresh"])
        self.assertEqual(calls[3], ["pkexec", "snap", "refresh", "gnome-calculator"])

    def test_install_failure_raises(self):
        failing = CommandResult(["snap"], 1, "error: not found", ["error"])
        with mock.patch.object(snap.runner, "run", return_value=failing):
            with self.assertRaises(BackendError):
                snap.install("missing")


if __name__ == "__main__":
    unittest.main()
