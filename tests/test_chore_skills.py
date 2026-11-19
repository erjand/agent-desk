"""Tests for agent-desk/lib/chore_skills.py, which warns about chores whose skill Claude Code can't find."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import chore_skills  # noqa: E402


def skill(folder, name):
    (folder / "skills" / name).mkdir(parents=True)
    (folder / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


def plugin(folder, name):
    (folder / ".claude-plugin").mkdir(parents=True)
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name}))


class ResolvesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.checkout = root / "project"
        self.home = root / "home" / ".claude"
        self.checkout.mkdir()
        self.home.mkdir(parents=True)
        self.root = root
        patch = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home), "CLAUDE_CODE_PLUGIN_DIRS": ""})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def resolves(self, name):
        return chore_skills.resolves(name, self.checkout)

    def test_a_skill_or_command_in_the_project_resolves(self):
        skill(self.checkout / ".claude", "audit")
        (self.checkout / ".claude" / "commands").mkdir()
        (self.checkout / ".claude" / "commands" / "tidy.md").write_text("Tidy one file.\n")
        self.assertTrue(self.resolves("audit"))
        self.assertTrue(self.resolves("tidy"))

    def test_a_users_skill_resolves_from_the_claude_config_dir(self):
        skill(self.home, "singleshot-refactor-one")
        self.assertTrue(self.resolves("singleshot-refactor-one"))

    def test_a_missing_skill_or_a_folder_without_skill_md_does_not_resolve(self):
        (self.home / "skills" / "singleshot-comment-one").mkdir(parents=True)
        self.assertFalse(self.resolves("singleshot-comment-one"))
        self.assertFalse(self.resolves("audit"))

    def test_a_plugin_skill_resolves_by_its_plugin_and_bare_but_not_under_another_plugin(self):
        inbox = self.root / "inbox"
        plugin(inbox, "agent-desk")
        skill(inbox, "work-issue")
        os.environ["CLAUDE_CODE_PLUGIN_DIRS"] = f" {inbox} "
        self.assertTrue(self.resolves("agent-desk:work-issue"))
        self.assertTrue(self.resolves("work-issue"))
        self.assertFalse(self.resolves("other:work-issue"))

    def test_plugin_dirs_come_from_the_users_settings_too(self):
        inbox = self.root / "inbox"
        plugin(inbox, "agent-desk")
        skill(inbox, "resource-report")
        (self.home / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": str(inbox)}}))
        self.assertTrue(self.resolves("agent-desk:resource-report"))

    def test_an_installed_plugin_counts_for_the_user_or_this_project_only(self):
        mine, theirs = self.root / "cache" / "mine", self.root / "cache" / "theirs"
        skill(mine, "lint-one")
        skill(theirs, "draw-one")
        (self.home / "plugins").mkdir()
        (self.home / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
            "tools@market": [{"scope": "user", "installPath": str(mine)}],
            "art@market": [{"scope": "local", "projectPath": str(self.root / "elsewhere"), "installPath": str(theirs)}],
        }}))
        self.assertTrue(self.resolves("tools:lint-one"))
        self.assertFalse(self.resolves("art:draw-one"))
        self.assertFalse(self.resolves("draw-one"))


class MainTest(unittest.TestCase):
    def test_it_warns_once_for_each_missing_skill_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            skill(home, "audit")
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home), "CLAUDE_CODE_PLUGIN_DIRS": ""}), \
                    contextlib.redirect_stdout(out):
                status = chore_skills.main(["chore_skills.py", tmp, "audit", "singleshot-untested-one"])
        self.assertEqual(status, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("no skill named singleshot-untested-one", lines[0])


if __name__ == "__main__":
    unittest.main()
