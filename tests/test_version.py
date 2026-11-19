"""Tests for agent-desk's version: the VERSION file, the plugin manifest and changelog that follow it, and
`agent-desk --version`, which prints it."""

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()


class VersionTest(unittest.TestCase):
    def test_version_has_three_numbers(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_the_plugin_manifest_matches_version(self):
        manifest = json.loads((ROOT / "inbox" / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], VERSION)

    def test_the_changelog_names_version_as_its_latest_release(self):
        releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", (ROOT / "CHANGELOG.md").read_text(), re.MULTILINE)
        self.assertEqual(releases[0], VERSION)

    def test_the_command_prints_version(self):
        result = subprocess.run([str(ROOT / "bin" / "agent-desk"), "--version"], capture_output=True, text=True,
                                check=True)
        self.assertEqual(result.stdout, f"agent-desk {VERSION}\n")


if __name__ == "__main__":
    unittest.main()
