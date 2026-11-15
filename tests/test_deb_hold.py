"""Tests for the apt-mark package hold integration."""

import unittest
from unittest import mock

from appdeb.backends import deb
from appdeb.backends.runner import BackendError, CommandResult

SHOWHOLD_OUTPUT = "\n".join(
    [
        "apt",
        "linux-image-generic",
        "WARNING: apt does not have a stable CLI interface.",
        "",
    ]
)


def ok_result(args, **kwargs):
    return CommandResult(args=[str(a) for a in args], returncode=0)


class ShowHoldParseTests(unittest.TestCase):
    def test_parse_showhold_names(self):
        self.assertEqual(
            deb.parse_showhold(SHOWHOLD_OUTPUT),
            ["apt", "linux-image-generic"],
        )

    def test_parse_showhold_empty(self):
        self.assertEqual(deb.parse_showhold(""), [])

    def test_parse_showhold_deduplicates(self):
        self.assertEqual(deb.parse_showhold("htop\nhtop\ncurl\n"), ["htop", "curl"])


class HeldTests(unittest.TestCase):
    def test_held_true(self):
        result = CommandResult(["apt-mark"], 0, SHOWHOLD_OUTPUT)
        with mock.patch.object(deb.runner, "capture", return_value=result) as capture:
            self.assertTrue(deb.held("apt"))
        self.assertEqual(capture.call_args.args[0], ["apt-mark", "showhold"])

    def test_held_false_for_missing_package(self):
        result = CommandResult(["apt-mark"], 0, SHOWHOLD_OUTPUT)
        with mock.patch.object(deb.runner, "capture", return_value=result):
            self.assertFalse(deb.held("htop"))

    def test_held_false_on_command_failure(self):
        result = CommandResult(["apt-mark"], 1, "error")
        with mock.patch.object(deb.runner, "capture", return_value=result):
            self.assertFalse(deb.held("apt"))

    def test_held_false_for_empty_name(self):
        with mock.patch.object(deb.runner, "capture") as capture:
            self.assertFalse(deb.held(""))
        capture.assert_not_called()


class HoldArgvTests(unittest.TestCase):
    def test_hold_argv(self):
        with mock.patch.object(deb.runner, "run", return_value=ok_result([])) as run:
            deb.hold("htop", True)
        self.assertEqual(
            run.call_args.args[0],
            ["pkexec", "apt-mark", "hold", "htop"],
        )

    def test_unhold_argv(self):
        with mock.patch.object(deb.runner, "run", return_value=ok_result([])) as run:
            deb.hold("htop", False)
        self.assertEqual(
            run.call_args.args[0],
            ["pkexec", "apt-mark", "unhold", "htop"],
        )

    def test_hold_passes_progress_and_cancel(self):
        progress = lambda _line: None  # noqa: E731
        cancel = object()
        with mock.patch.object(deb.runner, "run", return_value=ok_result([])) as run:
            deb.hold("htop", True, progress=progress, cancel=cancel)
        self.assertIs(run.call_args.kwargs["progress"], progress)
        self.assertIs(run.call_args.kwargs["cancel"], cancel)

    def test_hold_failure_raises_with_tail(self):
        failing = CommandResult(["apt-mark"], 1, "E: Could not get lock")
        with mock.patch.object(deb.runner, "run", return_value=failing):
            with self.assertRaises(BackendError) as caught:
                deb.hold("htop", True)
        self.assertIn("Could not get lock", caught.exception.output)

    def test_hold_rejects_empty_package(self):
        with mock.patch.object(deb.runner, "run") as run:
            with self.assertRaises(BackendError):
                deb.hold("", True)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
