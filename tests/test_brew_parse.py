import json
import unittest
from unittest import mock

from appdeb.backends import brew
from appdeb.backends.runner import BackendError, CommandResult

LIST_OUTPUT = "\n".join(
    [
        "codexbar 0.84.0",
        "gh 2.104.0",
    ]
)

INFO_JSON = json.dumps(
    {
        "formulae": [
            {
                "name": "gh",
                "full_name": "gh",
                "desc": "GitHub command-line tool",
                "homepage": "https://cli.github.com/",
                "versions": {"stable": "2.104.0", "head": "HEAD", "bottle": True},
                "caveats": None,
                "installed": [{"version": "2.104.0"}],
            },
            {
                "name": "codexbar",
                "full_name": "codexbar",
                "desc": "Menu bar status for coding agents",
                "homepage": "https://example.invalid/codexbar",
                "versions": {"stable": "0.84.0", "head": "HEAD", "bottle": True},
                "caveats": "Requires a restart.",
                "installed": [],
            },
        ]
    }
)

SEARCH_OUTPUT = "\n".join(
    [
        "==> Formulae",
        "ripgrep",
        "ripgrep-all",
        "",
        "Wall time: 1.31 seconds",
    ]
)

OUTDATED_OUTPUT = "\n".join(
    [
        "gh (2.103.0) < 2.104.0",
        "codexbar (0.83.0) < 0.84.0",
    ]
)


def fake_result(args, **kwargs):
    joined = " ".join(str(a) for a in args)
    output = ""
    if "list" in joined and "info" not in joined:
        output = LIST_OUTPUT
    elif "search" in joined:
        output = SEARCH_OUTPUT
    elif "outdated" in joined:
        output = OUTDATED_OUTPUT
    elif "info" in joined:
        output = INFO_JSON
    return CommandResult(
        args=[str(a) for a in args],
        returncode=0,
        output=output,
        lines=output.splitlines(),
    )


class BrewParseTests(unittest.TestCase):
    def test_parse_list_versions_and_installed(self):
        packages = brew.parse_list(LIST_OUTPUT)
        self.assertEqual([p.name for p in packages], ["codexbar", "gh"])
        self.assertEqual(packages[1].version, "2.104.0")
        self.assertTrue(all(p.installed for p in packages))

    def test_parse_info_json_marks_installed(self):
        packages = brew.parse_info_json(INFO_JSON)
        by_name = {p.name: p for p in packages}
        self.assertEqual(by_name["gh"].version, "2.104.0")
        self.assertEqual(by_name["gh"].description, "GitHub command-line tool")
        self.assertTrue(by_name["gh"].installed)
        self.assertFalse(by_name["codexbar"].installed)

    def test_parse_info_json_invalid_returns_empty(self):
        self.assertEqual(brew.parse_info_json("not json"), [])

    def test_parse_search_skips_headers_and_timing(self):
        self.assertEqual(brew.parse_search(SEARCH_OUTPUT), ["ripgrep", "ripgrep-all"])

    def test_list_installed_enriches_descriptions(self):
        with mock.patch.object(brew.runner, "run", side_effect=fake_result):
            packages = brew.list_installed()
        self.assertEqual([p.name for p in packages], ["codexbar", "gh"])
        self.assertEqual(packages[0].description, "Menu bar status for coding agents")
        self.assertEqual(packages[1].description, "GitHub command-line tool")

    def test_search_enriches_results_and_requires_two_chars(self):
        with mock.patch.object(brew.runner, "run", side_effect=fake_result) as run:
            self.assertEqual(brew.search("r"), [])
            run.assert_not_called()

        with mock.patch.object(brew.runner, "run", side_effect=fake_result):
            results = brew.search("ripgrep")
        self.assertEqual([r.name for r in results], ["ripgrep", "ripgrep-all"])

    def test_info_reads_homepage_caveats_and_installed_version(self):
        with mock.patch.object(brew.runner, "run", side_effect=fake_result):
            data = brew.info("gh")
        self.assertEqual(data["name"], "gh")
        self.assertEqual(data["homepage"], "https://cli.github.com/")
        self.assertEqual(data["installed_version"], "2.104.0")
        self.assertEqual(data["version"], "2.104.0")

    def test_outdated_parses_old_new_pairs(self):
        with mock.patch.object(brew.runner, "run", side_effect=fake_result):
            results = brew.outdated()
        self.assertEqual([r.name for r in results], ["gh", "codexbar"])
        self.assertEqual(results[0].version, "2.104.0")

    def test_install_argv_is_args_only(self):
        with mock.patch.object(brew.runner, "run", return_value=CommandResult([], 0)) as run:
            brew.install("ripgrep")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["brew", "install", "--formula", "ripgrep"])

    def test_uninstall_and_update_argv(self):
        with mock.patch.object(brew.runner, "run", return_value=CommandResult([], 0)) as run:
            brew.uninstall("ripgrep")
            brew.update(None)
            brew.update("ripgrep")
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[0], ["brew", "uninstall", "--formula", "ripgrep"])
        self.assertEqual(calls[1], ["brew", "upgrade"])
        self.assertEqual(calls[2], ["brew", "upgrade", "--formula", "ripgrep"])

    def test_install_failure_raises_backend_error(self):
        failing = CommandResult(["brew"], 1, "Error: formula not found", ["Error"])
        with mock.patch.object(brew.runner, "run", return_value=failing):
            with self.assertRaises(BackendError):
                brew.install("missing")


if __name__ == "__main__":
    unittest.main()
