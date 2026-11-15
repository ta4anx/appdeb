"""Tests for snap channel and held-update parameters."""

import unittest
from unittest import mock

from appdeb.backends import snap
from appdeb.backends.runner import BackendError, CommandResult

INFO_OUTPUT = "\n".join(
    [
        "name:      gnome-calculator",
        "summary:   GNOME Calculator",
        "publisher: Canonical**",
        "tracking:  latest/stable",
        "channels:",
        "  latest/stable:    48.1 2025-05-02 (972) 2.15MB -",
        "  latest/candidate: 48.1 2025-06-11 (975) 2.15MB -",
        "  latest/beta:      48.2 2025-07-01 (980) 2.15MB -",
        "  latest/edge:      ^",
        "installed:          48.1 (972) 2.15MB -",
    ]
)

CHANNELS_OUTPUT = "\n".join(
    [
        "channels:",
        "  latest/stable:    48.1 2025-05-02 (972) 2.15MB -",
        "  latest/candidate: 48.1 2025-06-11 (975) 2.15MB -",
        "  latest/edge:      ^",
    ]
)

HELD_OUTPUT = "\n".join(
    [
        "Name     Version                     Rev    Tracking       Publisher  Notes",
        "spotify  1.2.95.453.g0eeebbed        122    latest/stable  spotify**  held",
    ]
)

UNHELD_OUTPUT = "\n".join(
    [
        "Name     Version                     Rev    Tracking       Publisher  Notes",
        "spotify  1.2.95.453.g0eeebbed        122    latest/stable  spotify**  -",
    ]
)


def ok_result(args, **kwargs):
    return CommandResult(args=[str(a) for a in args], returncode=0)


class SnapChannelsTests(unittest.TestCase):
    def test_parse_channels_returns_versions_without_dates(self):
        channels = snap.parse_channels(CHANNELS_OUTPUT)
        self.assertEqual(
            channels,
            {
                "latest/stable": "48.1",
                "latest/candidate": "48.1",
                "latest/edge": "",
            },
        )

    def test_parse_channels_stops_at_next_top_level_key(self):
        channels = snap.parse_channels(INFO_OUTPUT)
        self.assertEqual(len(channels), 4)
        self.assertNotIn("installed", channels)
        self.assertEqual(channels["latest/beta"], "48.2")

    def test_parse_channels_without_block_is_empty(self):
        self.assertEqual(snap.parse_channels("name: gnome-calculator\n"), {})

    def test_channels_runs_snap_info(self):
        output = CommandResult(["snap"], 0, INFO_OUTPUT)
        with mock.patch.object(snap, "available", return_value=True), mock.patch.object(
            snap.runner, "run", return_value=output
        ) as run:
            channels = snap.channels("gnome-calculator")
        self.assertEqual(run.call_args.args[0], ["snap", "info", "gnome-calculator"])
        self.assertEqual(channels["latest/stable"], "48.1")

    def test_channels_failure_raises_with_tail(self):
        failing = CommandResult(["snap"], 1, "error: no snap found")
        with mock.patch.object(snap, "available", return_value=True), mock.patch.object(
            snap.runner, "run", return_value=failing
        ):
            with self.assertRaises(BackendError) as caught:
                snap.channels("missing")
        self.assertIn("no snap found", caught.exception.output)


class SnapHeldTests(unittest.TestCase):
    def test_parse_held_true(self):
        self.assertTrue(snap.parse_held(HELD_OUTPUT))

    def test_parse_held_false(self):
        self.assertFalse(snap.parse_held(UNHELD_OUTPUT))

    def test_parse_held_without_rows(self):
        self.assertFalse(snap.parse_held("Name  Version  Notes"))

    def test_held_uses_snap_list(self):
        output = CommandResult(["snap"], 0, HELD_OUTPUT)
        with mock.patch.object(snap, "available", return_value=True), mock.patch.object(
            snap.runner, "run", return_value=output
        ) as run:
            self.assertTrue(snap.held("spotify"))
        self.assertEqual(run.call_args.args[0], ["snap", "list", "spotify"])

    def test_held_failure_is_false(self):
        failing = CommandResult(["snap"], 1, "error: no matching snaps installed")
        with mock.patch.object(snap, "available", return_value=True), mock.patch.object(
            snap.runner, "run", return_value=failing
        ):
            self.assertFalse(snap.held("gnome-calculator"))


class SnapActionArgvTests(unittest.TestCase):
    def test_refresh_with_channel_argv(self):
        with mock.patch.object(snap.runner, "run", return_value=ok_result([])) as run:
            snap.refresh("gnome-calculator", channel="latest/beta")
        self.assertEqual(
            run.call_args.args[0],
            ["pkexec", "snap", "refresh", "--channel=latest/beta", "gnome-calculator"],
        )

    def test_refresh_without_channel_argv(self):
        with mock.patch.object(snap.runner, "run", return_value=ok_result([])) as run:
            snap.refresh("gnome-calculator")
        self.assertEqual(
            run.call_args.args[0],
            ["pkexec", "snap", "refresh", "gnome-calculator"],
        )

    def test_refresh_positional_progress_and_cancel(self):
        progress = lambda _line: None  # noqa: E731
        with mock.patch.object(snap.runner, "run", return_value=ok_result([])) as run:
            snap.refresh("gnome-calculator", progress)
        self.assertEqual(run.call_args.args[0], ["pkexec", "snap", "refresh", "gnome-calculator"])
        self.assertIs(run.call_args.kwargs["progress"], progress)

    def test_refresh_all_argv(self):
        with mock.patch.object(snap.runner, "run", return_value=ok_result([])) as run:
            snap.refresh(None)
        self.assertEqual(run.call_args.args[0], ["pkexec", "snap", "refresh"])

    def test_hold_and_unhold_argv(self):
        with mock.patch.object(snap.runner, "run", return_value=ok_result([])) as run:
            snap.hold("gnome-calculator", True)
            snap.hold("gnome-calculator", False)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[0], ["pkexec", "snap", "refresh", "--hold", "gnome-calculator"])
        self.assertEqual(calls[1], ["pkexec", "snap", "refresh", "--unhold", "gnome-calculator"])

    def test_hold_failure_raises(self):
        failing = CommandResult(["snap"], 1, "error: cannot hold")
        with mock.patch.object(snap.runner, "run", return_value=failing):
            with self.assertRaises(BackendError) as caught:
                snap.hold("gnome-calculator", True)
        self.assertIn("cannot hold", caught.exception.output)

    def test_refresh_failure_raises(self):
        failing = CommandResult(["snap"], 1, "error: cannot refresh")
        with mock.patch.object(snap.runner, "run", return_value=failing):
            with self.assertRaises(BackendError) as caught:
                snap.refresh("gnome-calculator", channel="latest/edge")
        self.assertIn("cannot refresh", caught.exception.output)


if __name__ == "__main__":
    unittest.main()
