"""Tests for agent-desk/lib/account.py, the account every Claude process the desk starts bills, and for what reads it:
the dispatcher's run rows and units, the weekly usage limit, the doctor, and lib/common.sh's desk_claude_run. No test
calls Anthropic."""

import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import account  # noqa: E402
import budget  # noqa: E402
import doctor  # noqa: E402
import hold  # noqa: E402
import store  # noqa: E402
import usage  # noqa: E402
from dispatch_case import REPO, DispatchCase  # noqa: E402

KEY = "sk-ant-api03-made-up-for-tests-WXYZ"


class AccountCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.state = self.root / "state"
        self.key = self.root / "config" / "agent-desk" / "anthropic-api-key"
        for patch in (mock.patch.object(hold, "STATE", self.state),
                      mock.patch.object(hold, "HOLD", self.state / "hold.json"),
                      mock.patch.object(hold, "LOCK", self.state / "hold.lock"),
                      mock.patch.object(usage, "CACHE", self.state / "usage.json"),
                      mock.patch.object(usage, "LOCK", self.state / "usage.lock"),
                      mock.patch.object(account, "KEY", self.key),
                      # `agent-desk account` reads the month's API spend from a store and a Chat ledger of the test's.
                      mock.patch.object(store, "DATABASE", self.state / "desk.sqlite"),
                      mock.patch.object(budget, "LEDGER", self.state / "chat-spend.jsonl"),
                      mock.patch.object(budget, "LOCK", self.state / "chat-spend.lock")):
            patch.start()
            self.addCleanup(patch.stop)

    def command(self, *args, stdin=""):
        """Runs `agent-desk account` with args, reading stdin as standard input that isn't a terminal."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            code = account.main(["account.py", *args])
        return code, out.getvalue(), err.getvalue()


class CommandTest(AccountCase):
    def test_the_account_starts_as_subscription_with_no_key(self):
        code, out, _ = self.command()
        self.assertEqual(code, 0)
        self.assertIn("bill the subscription account: the Claude plan Claude Code is signed in to.", out)
        self.assertIn("No API key is set. Set one with: agent-desk account key", out)
        self.assertEqual(account.current(), "subscription")

    def test_a_key_from_standard_input_is_saved_owner_only_and_shows_its_last_four_characters(self):
        code, out, _ = self.command("key", stdin=f"  {KEY}\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.key.read_text(), KEY)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        self.assertIn("Saved the API key ending in WXYZ", out)
        self.assertIn("switch to the key with: agent-desk account api", out)
        code, out, _ = self.command()
        self.assertIn("An API key ending in WXYZ is set", out)
        self.assertNotIn(KEY, out)

    def test_a_new_key_replaces_the_old_and_a_loose_leftover_file_is_made_private(self):
        self.key.parent.mkdir(parents=True)
        leftover = self.key.with_name(self.key.name + ".new")
        leftover.write_text("old")
        leftover.chmod(0o644)
        self.command("key", stdin="sk-ant-first-1111")
        self.command("key", stdin=KEY)
        self.assertEqual(self.key.read_text(), KEY)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)

    def test_a_key_from_a_terminal_comes_from_a_prompt_that_does_not_echo(self):
        terminal = mock.Mock(isatty=lambda: True)
        with mock.patch.object(account.getpass, "getpass", return_value=KEY) as prompt, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(account.set_key(terminal), 0)
        prompt.assert_called_once()
        self.assertEqual(self.key.read_text(), KEY)

    def test_an_empty_key_or_one_with_spaces_inside_is_refused(self):
        for given in ("", "   \n", "sk-ant one", f"{KEY}\n{KEY}"):
            code, _, err = self.command("key", stdin=given)
            self.assertEqual(code, 1, given)
            self.assertIn("nothing changed", err)
            self.assertFalse(self.key.exists())

    def test_api_is_refused_while_no_key_is_set_and_says_how_to_set_one(self):
        code, _, err = self.command("api")
        self.assertEqual(code, 1)
        self.assertIn("agent-desk account key", err)
        self.assertEqual(account.current(), "subscription")

    def test_api_and_subscription_switch_the_account_for_every_project(self):
        self.command("key", stdin=KEY)
        code, out, _ = self.command("api")
        self.assertEqual(code, 0)
        self.assertIn("bill the api account: the Anthropic API key.", out)
        self.assertEqual(json.loads((self.state / "hold.json").read_text())["account"], "api")
        self.assertNotIn(KEY, (self.state / "hold.json").read_text())
        self.command("subscription")
        self.assertEqual(account.current(), "subscription")

    def test_clearing_the_key_on_api_switches_back_to_subscription_and_says_so(self):
        self.command("key", stdin=KEY)
        self.command("api")
        code, out, _ = self.command("key", "--clear")
        self.assertEqual(code, 0)
        self.assertFalse(self.key.exists())
        self.assertIn("The account was api, so it is subscription now", out)
        self.assertEqual(account.current(), "subscription")
        code, out, _ = self.command("key", "--clear")
        self.assertEqual((code, out.strip()), (0, "No API key was set."))

    def test_the_key_never_comes_as_an_argument(self):
        code, _, err = self.command("key", KEY)
        self.assertEqual(code, 2)
        self.assertFalse(self.key.exists())
        self.assertNotIn(KEY, err)

    def test_the_web_pages_reading_names_whether_a_key_is_set_and_its_last_four_characters_alone(self):
        code, out, _ = self.command("json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"account": "subscription", "spent": 0.0, "budget": None, "held": False,
                                           "key": {"set": False, "ending": None}})
        self.command("key", stdin=KEY)
        self.command("api")
        self.command("budget", "200")
        code, out, _ = self.command("json")
        self.assertEqual(json.loads(out), {"account": "api", "spent": 0.0, "budget": {"on": True, "dollars": 200.0},
                                           "held": False, "key": {"set": True, "ending": "WXYZ"}})
        self.assertNotIn(KEY[:-4], out)

    def test_the_account_outlasts_a_state_that_went_missing(self):
        (self.state).mkdir()
        (self.state / "hold.json").write_text(json.dumps({"state": "gone", "account": "api"}))
        self.assertEqual(hold.status()["account"], "api")


class ClaudeFlagsTest(AccountCase):
    def test_subscription_gets_no_flags(self):
        self.assertEqual(account.claude(), ("subscription", ""))

    def test_api_gets_a_helper_that_prints_the_key_file_and_never_the_key_itself(self):
        self.command("key", stdin=KEY)
        self.command("api")
        billed, flags = account.claude()
        self.assertEqual(billed, "api")
        helper = json.loads(flags)["apiKeyHelper"]
        self.assertNotIn(KEY, flags)
        self.assertEqual(subprocess.run(helper, shell=True, capture_output=True, text=True).stdout, KEY)

    def test_an_account_named_overrides_the_setting(self):
        self.command("key", stdin=KEY)
        self.command("api")
        self.assertEqual(account.claude("subscription"), ("subscription", ""))

    def test_api_without_a_key_is_refused(self):
        code, out, err = self.command("claude", "api")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("the account is api, but no API key is set", err)


class UsageOnApiTest(AccountCase):
    def test_the_limit_holds_nothing_and_fetches_nothing_on_api(self):
        state = {"state": "running", "usageLimit": {"on": True, "percent": 80}, "account": "api"}
        fetch = mock.Mock(side_effect=AssertionError("fetched"))
        self.assertEqual(usage.check(state, get=fetch), (None, None))
        fetch.assert_not_called()
        held = usage.check({**state, "account": "subscription"}, get=lambda: {"percent": 90.0, "resets_at": None})
        self.assertIsNotNone(held[0])

    def test_the_command_says_the_limit_waits_for_the_subscription(self):
        hold.update(lambda data: data.update(usageLimit={"on": True, "percent": 80}, account="api"))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(usage.main(["usage.py"]), 0)
        self.assertIn("The account is api, so the limit waits for the subscription", out.getvalue())

    def test_the_doctor_says_the_limit_waits(self):
        state = {"state": "running", "usageLimit": {"on": True, "percent": 80}, "account": "api"}
        check = doctor.check_usage(state)
        self.assertEqual(check.status, "ok")
        self.assertIn("waits for the subscription", check.what)


class DoctorTest(AccountCase):
    def test_subscription_is_reported_and_passes(self):
        check = doctor.check_account({"state": "running"}, self.key)
        self.assertEqual(check.status, "ok")
        self.assertIn("the account is subscription", check.what)

    def test_api_passes_with_a_private_key_and_shows_only_its_last_four_characters(self):
        account.save_key(KEY, self.key)
        check = doctor.check_account({"account": "api"}, self.key)
        self.assertEqual(check.status, "ok")
        self.assertIn("ending in WXYZ", check.what)
        self.assertNotIn(KEY, check.what)

    def test_api_fails_on_a_missing_empty_or_readable_key_file(self):
        check = doctor.check_account({"account": "api"}, self.key)
        self.assertEqual(check.status, "fail")
        self.assertIn("no API key is set", check.what)
        account.save_key("", self.key)
        self.assertIn("holds no API key", doctor.check_account({"account": "api"}, self.key).what)
        account.save_key(KEY, self.key)
        self.key.chmod(0o640)
        check = doctor.check_account({"account": "api"}, self.key)
        self.assertEqual(check.status, "fail")
        self.assertIn("mode 0640", check.what)
        self.assertEqual(check.fix, f"chmod 600 {self.key}")
        self.assertNotIn(KEY, check.what + check.fix)


class RunRowTest(DispatchCase):
    def test_each_run_records_and_gets_the_account_set_as_it_starts(self):
        first = self.request("agent", "7")
        self.dispatcher.tick()
        self.hold = {**self.hold, "account": "api"}
        second = self.request("chore", "audit")
        self.time += 60
        self.dispatcher.tick()
        self.assertEqual([self.store.run(run)["account"] for run in (first, second)], ["subscription", "api"])
        self.assertEqual([self.systemd.env(index)["AGENT_DESK_ACCOUNT"] for index in (0, 1)],
                         ["subscription", "api"])
        listed = {one["id"]: one["account"] for one in self.store.live_runs(REPO)}
        self.assertEqual(listed, {first: "subscription", second: "api"})

    def test_no_unit_property_holds_the_key(self):
        self.hold = {**self.hold, "account": "api"}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}):
            self.request("agent", "7")
            self.dispatcher.tick()
        self.assertTrue(self.systemd.started)
        self.assertNotIn(KEY, " ".join(self.systemd.started[0]))

    def test_a_request_that_waits_names_no_account(self):
        self.memory.ready = False
        run = self.request("agent", "7")
        self.dispatcher.tick()
        [listed] = self.store.live_runs(REPO)
        self.assertNotIn("account", listed)
        self.assertIsNone(self.store.run(run)["account"])


class OnePlaceTest(unittest.TestCase):
    def test_every_script_starts_claude_through_desk_claude_run(self):
        """desk_claude_run is the one place a run's account takes effect, so no other script line runs claude -p."""
        lib = Path(__file__).resolve().parents[1] / "lib"
        found = [f"{path.name}: {line.strip()}" for path in sorted(lib.glob("*.sh")) if path.name != "common.sh"
                 for line in path.read_text().splitlines()
                 if not line.lstrip().startswith("#") and ("claude -p" in line or line.lstrip().startswith("claude "))]
        self.assertEqual(found, [])


class ClaudeRunTest(unittest.TestCase):
    """desk_claude_run in lib/common.sh, through which every script starts claude -p, bills the run's account and hides
    ANTHROPIC_API_KEY from claude and the agent's commands."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        fakes = self.root / "fakes"
        fakes.mkdir()
        self.args = self.root / "claude.args"
        self.seen = self.root / "seen-key"
        # The stand-in logs its arguments and the ANTHROPIC_API_KEY it and the agent's commands see.
        (fakes / "claude").write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >"{self.args}"\n'
                                      f'echo "${{ANTHROPIC_API_KEY-unset}}" >"{self.seen}"\n')
        (fakes / "claude").chmod(0o755)
        self.work = self.root / "work"
        self.work.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("AGENT_DESK_ACCOUNT", "AGENT_DESK_RUN", "AGENT_DESK_UNIT")}
        self.env.update({"PATH": f"{fakes}:{os.environ['PATH']}", "XDG_STATE_HOME": str(self.root / "state"),
                         "XDG_CONFIG_HOME": str(self.root / "config"), "HOME": str(self.root),
                         "ANTHROPIC_API_KEY": "sk-ant-from-the-environment", "DESK_HELPERS_ONLY": "1"})

    def save_key(self):
        key = self.root / "config" / "agent-desk" / "anthropic-api-key"
        key.parent.mkdir(parents=True)
        key.write_text(KEY)
        key.chmod(0o600)

    def run_claude(self, **env):
        # The helpers alone: sourcing all of common.sh needs a project's checkout, which the test folder isn't.
        lib = Path(__file__).resolve().parents[1] / "lib"
        return subprocess.run(["bash", "-c", f'source "{lib}/common.sh"; desk_model_args=(); '
                               'desk_claude_run "$1" "$1/stream" "/agent-desk:work-issue 1"', "run", str(self.work)],
                              env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def assert_billed(self, account, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Account: {account}.", result.stdout)
        self.assertEqual(self.seen.read_text(), "unset\n")
        args = self.args.read_text().splitlines()
        self.assertNotIn(KEY, "\n".join(args) + result.stdout + result.stderr)
        if account == "api":
            helper = json.loads(args[args.index("--settings") + 1])["apiKeyHelper"]
            self.assertEqual(subprocess.run(helper, shell=True, capture_output=True, text=True).stdout, KEY)
        else:
            self.assertNotIn("--settings", args)

    def test_a_run_bills_the_account_the_dispatcher_handed_it(self):
        self.save_key()
        for account in ("subscription", "api"):
            with self.subTest(account):
                self.assert_billed(account, self.run_claude(AGENT_DESK_ACCOUNT=account))

    def test_a_run_started_by_hand_bills_the_setting(self):
        self.save_key()
        (self.root / "state" / "agent-desk").mkdir(parents=True)
        (self.root / "state" / "agent-desk" / "hold.json").write_text(json.dumps({"state": "running",
                                                                                   "account": "api"}))
        self.assert_billed("api", self.run_claude())

    def test_a_run_on_api_without_a_key_starts_no_claude_and_says_why(self):
        result = self.run_claude(AGENT_DESK_ACCOUNT="api")
        self.assertEqual(result.returncode, 1)
        self.assertIn("the account is api, but no API key is set", result.stderr)
        self.assertFalse(self.args.exists())


class MigrationTest(unittest.TestCase):
    def test_runs_from_before_the_migration_read_as_subscription(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "older.sqlite"
            older = sqlite3.connect(path)
            before = next(index for index, migration in enumerate(store.MIGRATIONS)
                          if "ALTER TABLE runs ADD COLUMN account TEXT" in migration)
            for statement in [one for migration in store.MIGRATIONS[:before] for one in migration]:
                older.execute(statement)
            older.execute("INSERT INTO projects (slug) VALUES (?)", (REPO,))
            older.execute("INSERT INTO runs (project, kind, target, status, started_at) VALUES (?, 'agent', '1',"
                          " 'succeeded', '2026-01-01T00:00:00Z')", (REPO,))
            older.execute("INSERT INTO runs (project, kind, target, status) VALUES (?, 'agent', '2', 'requested')",
                          (REPO,))
            older.execute(f"PRAGMA user_version = {before}")
            older.commit()
            older.close()
            with mock.patch.object(store, "PROJECTS", Path(folder) / "projects"):
                desk = store.Store(path)
                try:
                    self.assertEqual([desk.run(run)["account"] for run in (1, 2)], ["subscription", None])
                    desk.run_started(2, "u.service", account="api")
                    self.assertEqual(desk.run(2)["account"], "api")
                finally:
                    desk.close()


if __name__ == "__main__":
    unittest.main()
