"""Tests for agent-desk/lib/config.py, which the shell scripts read .agent-desk.json through."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "lib" / "config.py"

SETTINGS = {
    "test": "just test",
    "env": {"WINE": "~/.wine", "MODE": "ci"},
    "worktree": {"seeds": [".godot/imported", "art/audio"], "setup": "just build"},
    "sync": {"skipWhile": []},
    "chores": {"every": {"audit": 3, "singleshot-untested-one": 4}, "gapHours": 18},
}


def read(*args, settings=SETTINGS):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(settings, handle)
    result = subprocess.run([sys.executable, str(CONFIG), handle.name, *args], capture_output=True, text=True)
    Path(handle.name).unlink()
    return result.returncode, result.stdout


class ConfigTest(unittest.TestCase):
    def test_a_string_prints_as_is(self):
        self.assertEqual(read("test"), (0, "just test\n"))

    def test_a_list_prints_one_item_per_line(self):
        self.assertEqual(read("worktree.seeds"), (0, ".godot/imported\nart/audio\n"))

    def test_an_object_prints_one_pair_per_line(self):
        self.assertEqual(read("env"), (0, "WINE=~/.wine\nMODE=ci\n"))

    def test_an_empty_list_prints_nothing(self):
        self.assertEqual(read("sync.skipWhile"), (0, "\n"))

    def test_a_missing_key_takes_the_default(self):
        self.assertEqual(read("sync.run", ""), (0, "\n"))
        self.assertEqual(read("chores.maxAuditIssues", "5"), (0, "5\n"))

    def test_a_missing_key_without_a_default_fails(self):
        self.assertEqual(read("sync.run")[0], 1)

    def test_chores_print_in_order_with_their_days(self):
        self.assertEqual(read("--chores"), (0, "audit 3\nsingleshot-untested-one 4\n"))

    def test_a_paused_chore_keeps_its_days_and_says_paused(self):
        settings = {"chores": {"every": {"audit": 3, "singleshot-untested-one": 4}, "paused": ["audit"]}}
        self.assertEqual(read("--chores", settings=settings), (0, "audit 3 paused\nsingleshot-untested-one 4\n"))

    def test_a_paused_name_missing_from_every_is_ignored(self):
        settings = {"chores": {"every": {"audit": 3}, "paused": ["singleshot-untested-one"]}}
        self.assertEqual(read("--chores", settings=settings), (0, "audit 3\n"))

    def test_paused_that_is_not_a_list_pauses_nothing(self):
        settings = {"chores": {"every": {"audit": 3}, "paused": "audit"}}
        self.assertEqual(read("--chores", settings=settings), (0, "audit 3\n"))

    def test_the_shared_chores_file_issues_under_their_labels_by_default(self):
        self.assertEqual(read("--issue-label", "audit"), (0, "audit\n"))
        self.assertEqual(read("--issue-label", "security-audit"), (0, "security\n"))

    def test_a_chore_without_an_issue_label_fails(self):
        self.assertEqual(read("--issue-label", "singleshot-untested-one"), (1, ""))
        self.assertEqual(read("--issue-label")[0], 1)

    def test_issue_labels_add_to_and_change_the_defaults(self):
        settings = {"chores": {"issueLabels": {"triage": "bug", "security-audit": "sec", "audit": ""}}}
        self.assertEqual(read("--issue-label", "triage", settings=settings), (0, "bug\n"))
        self.assertEqual(read("--issue-label", "security-audit", settings=settings), (0, "sec\n"))
        # An empty label takes a default away, so the chore waits on its review instead.
        self.assertEqual(read("--issue-label", "audit", settings=settings), (1, ""))


if __name__ == "__main__":
    unittest.main()
