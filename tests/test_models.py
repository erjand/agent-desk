"""Tests for agent-desk/lib/models.py, which picks the model and effort each run uses."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import models  # noqa: E402


def chosen(project=None):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"models": project} if project else {}, handle)
    result = models.settings(handle.name)
    Path(handle.name).unlink()
    return result


class PickTest(unittest.TestCase):
    def test_agents_work_on_opus_at_high_effort_and_chores_on_opus_at_medium(self):
        self.assertEqual(models.pick(chosen(), "agent"), ("opus", "high"))
        self.assertEqual(models.pick(chosen(), "chore", skill="refactor"), ("opus", "medium"))

    def test_the_audit_runs_on_opus_at_high_effort(self):
        self.assertEqual(models.pick(chosen(), "chore", skill="audit"), ("opus", "high"))

    def test_the_comment_chore_runs_on_sonnet_and_the_test_chore_on_opus(self):
        self.assertEqual(models.pick(chosen(), "chore", skill="trim-comments"), ("sonnet", "medium"))
        self.assertEqual(models.pick(chosen(), "chore", skill="add-tests"), ("opus", "medium"))

    def test_a_chore_takes_its_settings_by_its_full_name_only(self):
        # A project still running the upstream singleshot- skills gets the chore defaults, not the renamed chores'.
        self.assertEqual(models.pick(chosen(), "chore", skill="singleshot-comment-one"), ("opus", "medium"))
        project = chosen({"chores": {"comment-one": {"model": "haiku"}, "singleshot-untested-one": {"effort": "low"}}})
        self.assertEqual(models.pick(project, "chore", skill="singleshot-comment-one"), ("opus", "medium"))
        self.assertEqual(models.pick(project, "chore", skill="singleshot-untested-one"), ("opus", "low"))

    def test_a_fix_runs_on_sonnet_at_medium_effort(self):
        self.assertEqual(models.pick(chosen(), "fix"), ("sonnet", "medium"))
        self.assertEqual(models.pick(chosen({"fix": {"model": "opus"}}), "fix"), ("opus", "medium"))

    def test_a_project_overrides_a_kind_a_chore_or_a_label(self):
        project = chosen({"agent": {"model": "opus"}, "chores": {"add-tests": {"effort": "high"}},
                          "labels": {"design": {"effort": "max"}, "small": {"model": "sonnet"}}})
        self.assertEqual(models.pick(project, "agent", labels=["bug"]), ("opus", "high"))
        self.assertEqual(models.pick(project, "agent", labels=["design", "small"]), ("sonnet", "max"))
        self.assertEqual(models.pick(project, "chore", skill="add-tests"), ("opus", "high"))

    def test_an_effort_claude_does_not_know_is_left_unset(self):
        self.assertEqual(models.pick(chosen({"chore": {"effort": "extreme"}}), "chore", skill="refactor"),
                         ("opus", ""))


if __name__ == "__main__":
    unittest.main()
