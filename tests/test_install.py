"""Tests for install.sh, which sets agent-desk up from its checkout, and lib/plugin_dirs.py, which it runs."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import plugin_dirs  # noqa: E402

COMMAND = ROOT / "bin" / "agent-desk"
INBOX = ROOT / "inbox"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.home / ".local" / "bin"
        self.settings = self.home / ".claude" / "settings.json"
        self.units = self.home / ".config" / "systemd" / "user"
        self.state = self.home / ".local" / "state"
        # A stand-in for systemctl logs its arguments and reports the web page as running when `active` exists.
        self.fakes = self.root / "fakes"
        self.fakes.mkdir()
        self.calls = self.root / "systemctl.log"
        systemctl = self.fakes / "systemctl"
        systemctl.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >>"{self.calls}"\n'
            f'case "$*" in *is-active*) [ -e "{self.root}/active" ];; esac\n'
        )
        systemctl.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, path_has_bin=True):
        path = [str(self.fakes)] + ([str(self.bin)] if path_has_bin else []) + ["/usr/bin", "/bin"]
        env = {
            "HOME": str(self.home),
            "PATH": ":".join(path),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.state),
        }
        result = subprocess.run(
            [str(ROOT / "install.sh")], env=env, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def systemctl_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def write_units(self, old):
        self.units.mkdir(parents=True)
        (self.units / "agent-desk-chores.service").write_text(
            f"[Service]\nType=oneshot\nExecStart={old}/bin/agent-desk chores tick-all\n"
        )
        (self.units / "agent-desk-merges.service").write_text(
            f"[Service]\nType=oneshot\nExecStart={old}/bin/agent-desk chores merges-all\n"
        )
        (self.units / "agent-desk-backup.service").write_text(
            f'[Service]\nType=oneshot\nEnvironment="AGENT_DESK_DATA=/data"\nExecStart={old}/bin/agent-desk backup tick\n'
        )
        (self.units / "agent-desk-web.service").write_text(
            "[Service]\nEnvironment=AGENT_DESK_WEB_PORT=7490\n"
            f"ExecStart=/usr/local/bin/node {old}/web/server.ts\nRestart=on-failure\n"
        )
        (self.units / "agent-desk-chores.timer").write_text("[Timer]\nOnUnitActiveSec=5min\n")

    def test_a_fresh_home_gets_the_link_and_the_plugin(self):
        out = self.install()
        link = self.bin / "agent-desk"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), COMMAND)
        self.assertEqual(json.loads(self.settings.read_text()), {"env": {"CLAUDE_CODE_PLUGIN_DIRS": str(INBOX)}})
        self.assertNotIn("isn't on PATH", out)
        self.assertEqual(self.systemctl_calls(), [])

    def test_a_home_with_the_marketplace_plugin_gets_the_link_and_no_plugin_folder(self):
        self.settings.parent.mkdir(parents=True)
        settings = {"enabledPlugins": {"agent-desk@agent-desk": True}}
        self.settings.write_text(json.dumps(settings))
        out = self.install()
        self.assertEqual((self.bin / "agent-desk").resolve(), COMMAND)
        self.assertEqual(json.loads(self.settings.read_text()), settings)
        self.assertIn("agent-desk@agent-desk is installed from a marketplace", out)

    def test_a_regular_file_in_the_way_is_backed_up(self):
        self.bin.mkdir(parents=True)
        (self.bin / "agent-desk").write_text("#!/bin/sh\necho old\n")
        out = self.install()
        self.assertEqual((self.bin / "agent-desk").resolve(), COMMAND)
        backups = list((self.state / "agent-desk" / "backups").glob("*/agent-desk"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "#!/bin/sh\necho old\n")
        self.assertIn(f"backup  {self.bin / 'agent-desk'}", out)

    def test_a_missing_bin_on_path_is_named(self):
        out = self.install(path_has_bin=False)
        self.assertIn(f"{self.bin} isn't on PATH", out)

    def test_settings_keep_their_other_keys_and_their_link(self):
        real = self.root / "agents" / "settings.json"
        real.parent.mkdir()
        original = {
            "env": {"CLAUDE_CODE_PLUGIN_DIRS": "~/plugins/other", "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"},
            "permissions": {"allow": ["Bash(ls*)"], "deny": []},
            "statusLine": {"type": "command", "command": "~/.claude/statusline-command.sh — ü"},
        }
        real.write_text(json.dumps(original, indent=2, ensure_ascii=False) + "\n")
        self.settings.parent.mkdir()
        self.settings.symlink_to(real)
        self.install()
        self.assertTrue(self.settings.is_symlink())
        expected = json.loads(json.dumps(original))
        expected["env"]["CLAUDE_CODE_PLUGIN_DIRS"] = f"~/plugins/other:{INBOX}"
        self.assertEqual(json.loads(real.read_text()), expected)
        # Only the variable's line differs, so the file keeps Claude Code's own formatting.
        before = (json.dumps(original, indent=2, ensure_ascii=False) + "\n").splitlines()
        after = real.read_text().splitlines()
        self.assertEqual([a for a, b in zip(before, after) if a != b], ['    "CLAUDE_CODE_PLUGIN_DIRS": "~/plugins/other",'])

    def test_units_point_at_this_checkout_and_the_running_page_restarts(self):
        self.write_units("/old/agent-desk")
        (self.root / "active").touch()
        out = self.install()
        chores = (self.units / "agent-desk-chores.service").read_text()
        marker = f'Environment="AGENT_DESK_INSTALLED={ROOT}"\n'
        self.assertIn(f"ExecStart={COMMAND} chores tick-all\n", chores)
        self.assertIn(f"[Service]\n{marker}", chores)
        merges = (self.units / "agent-desk-merges.service").read_text()
        self.assertIn(f"ExecStart={COMMAND} chores merges-all\n", merges)
        self.assertIn(f"[Service]\n{marker}", merges)
        self.assertEqual((self.units / "agent-desk-backup.service").read_text(),
                         f'[Service]\n{marker}Type=oneshot\nEnvironment="AGENT_DESK_DATA=/data"\n'
                         f"ExecStart={COMMAND} backup tick\n")
        web = (self.units / "agent-desk-web.service").read_text()
        self.assertEqual(
            web,
            f"[Service]\n{marker}Environment=AGENT_DESK_WEB_PORT=7490\n"
            f"ExecStart=/usr/local/bin/node {ROOT}/web/server.ts\nRestart=on-failure\n",
        )
        self.assertEqual((self.units / "agent-desk-chores.timer").read_text(), "[Timer]\nOnUnitActiveSec=5min\n")
        self.assertEqual(
            self.systemctl_calls(),
            ["--user daemon-reload", "--user --quiet is-active agent-desk-web.service",
             "--user restart agent-desk-web.service"],
        )
        self.assertIn("restarted agent-desk-web.service", out)

    def test_the_running_dispatcher_points_at_this_checkout_and_restarts(self):
        self.units.mkdir(parents=True)
        (self.units / "agent-desk-dispatch.service").write_text(
            '[Service]\nEnvironment="PATH=/usr/bin"\nEnvironment="AGENT_DESK_INSTALLED=/old/agent-desk"\n'
            'ExecStart=/old/agent-desk/bin/agent-desk dispatch serve\n'
        )
        (self.root / "active").touch()
        out = self.install()
        self.assertEqual((self.units / "agent-desk-dispatch.service").read_text(),
                         f'[Service]\nEnvironment="AGENT_DESK_INSTALLED={ROOT}"\nEnvironment="PATH=/usr/bin"\n'
                         f"ExecStart={COMMAND} dispatch serve\n")
        self.assertEqual(
            self.systemctl_calls(),
            ["--user daemon-reload", "--user --quiet is-active agent-desk-dispatch.service",
             "--user restart agent-desk-dispatch.service"],
        )
        self.assertIn("restarted agent-desk-dispatch.service", out)

    def test_a_stopped_page_stays_stopped(self):
        self.write_units("/old/agent-desk")
        self.install()
        self.assertNotIn("--user restart agent-desk-web.service", self.systemctl_calls())

    def test_a_second_run_changes_nothing(self):
        self.write_units("/old/agent-desk")
        self.settings.parent.mkdir()
        self.settings.write_text('{\n  "env": {\n    "OTHER": "1"\n  }\n}\n')
        self.install()
        files = sorted(p for p in self.home.rglob("*") if p.is_file() and not p.is_symlink())
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        calls = len(self.systemctl_calls())
        out = self.install()
        after = sorted(p for p in self.home.rglob("*") if p.is_file() and not p.is_symlink())
        self.assertEqual(after, files)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in after}, before)
        self.assertEqual(len(self.systemctl_calls()), calls)
        self.assertTrue(all(line.startswith("ok ") for line in out.splitlines()), out)


class PluginDirsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "desk" / "inbox"
        self.inbox.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def plugin(self, name, plugin_name):
        folder = self.root / name
        (folder / ".claude-plugin").mkdir(parents=True)
        (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": plugin_name}))
        return folder

    def test_an_empty_value_takes_the_folder(self):
        self.assertEqual(plugin_dirs.updated("", self.inbox), (str(self.inbox), "added"))

    def test_an_entry_through_a_link_counts_as_there(self):
        link = self.root / "link"
        link.symlink_to(self.root / "desk")
        value = f" /x : {link}/inbox "
        self.assertEqual(plugin_dirs.updated(value, self.inbox), (value, "ok"))

    def test_another_agent_desk_inbox_gives_way(self):
        old = self.plugin("old", "agent-desk")
        other = self.plugin("other", "something-else")
        value, change = plugin_dirs.updated(f"{other}:{old}", self.inbox)
        self.assertEqual(value, f"{other}:{self.inbox}")
        self.assertEqual(change, f"replaced {old}")

    def test_a_tilde_entry_expands_against_home(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            self.assertEqual(plugin_dirs.updated("~/desk/inbox", self.inbox), ("~/desk/inbox", "ok"))

    def test_a_disabled_or_other_plugin_still_takes_the_folder(self):
        self.assertIsNone(plugin_dirs.installed({"enabledPlugins": {"agent-desk@agent-desk": False,
                                                                    "agent-desk-extras@x": True}}))
        self.assertEqual(plugin_dirs.installed({"enabledPlugins": {"agent-desk@mine": True}}), "agent-desk@mine")

    def test_the_marketplace_plugin_beside_a_listed_inbox_is_named(self):
        old = self.plugin("old", "agent-desk")
        path = self.root / "settings.json"
        settings = {"env": {"CLAUDE_CODE_PLUGIN_DIRS": str(old)}, "enabledPlugins": {"agent-desk@agent-desk": True}}
        path.write_text(json.dumps(settings))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(plugin_dirs.main(path, self.inbox), 0)
        self.assertEqual(json.loads(path.read_text()), settings)
        self.assertIn(f"also loads {old}", out.getvalue())

    def test_settings_that_are_not_an_object_are_left_alone(self):
        path = self.root / "settings.json"
        path.write_text("[]\n")
        self.assertEqual(plugin_dirs.main(path, self.inbox), 1)
        self.assertEqual(path.read_text(), "[]\n")


if __name__ == "__main__":
    unittest.main()
