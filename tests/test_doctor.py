"""Tests for agent-desk/lib/doctor.py, which agent-desk doctor runs to check the machine and the project."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import doctor  # noqa: E402

CONFIG = {
    "worktree": {"seeds": [], "setup": ""},
    "sync": {"run": "", "skipWhile": []},
    "chores": {"every": {"audit": 7, "tidy": 3}, "gapHours": 18},
}


class ConfigTest(unittest.TestCase):
    def problems(self, config):
        return doctor.config_problems(json.dumps(config))

    def test_a_complete_config_has_no_problems(self):
        self.assertEqual(self.problems(CONFIG), [])

    def test_the_starter_config_install_writes_has_no_problems(self):
        starter = {**CONFIG, "host": "github", "test": "", "chores": {"every": {}, "gapHours": 18,
                                                                     "maxAuditIssues": 5}}
        self.assertEqual(self.problems(starter), [])

    def test_the_host_must_be_one_agent_desk_publishes_to(self):
        self.assertEqual(self.problems({**CONFIG, "host": "none"}), [])
        self.assertEqual(self.problems({**CONFIG, "host": "gitlab"}), ["host must be one of github, none"])

    def test_the_test_command_must_be_text_when_set(self):
        self.assertEqual(self.problems({**CONFIG, "test": "just test"}), [])
        self.assertEqual(self.problems({**CONFIG, "test": ["just", "test"]}),
                         ['test must be the command that tests a branch, such as "just test", or "" for none'])

    def test_test_minutes_must_be_a_whole_number_above_0_when_set(self):
        self.assertEqual(self.problems({**CONFIG, "testMinutes": 30}), [])
        for minutes in (0, -1, 2.5, "30", True):
            self.assertEqual(self.problems({**CONFIG, "testMinutes": minutes}),
                             ["testMinutes must be a whole number of minutes above 0"])

    def test_limit_minutes_must_name_run_kinds_with_whole_minutes_above_0(self):
        self.assertEqual(self.problems({**CONFIG, "limitMinutes": {"agent": 360, "benchmark": 480}}), [])
        self.assertEqual(self.problems({**CONFIG, "limitMinutes": 360}),
                         ["limitMinutes must be an object of run kinds to minutes"])
        self.assertEqual(self.problems({**CONFIG, "limitMinutes": {"check": 30}}),
                         ["limitMinutes.check isn't a run kind; the kinds are agent, benchmark, chore, fix"])
        for minutes in (0, -1, 2.5, "30", True):
            self.assertEqual(self.problems({**CONFIG, "limitMinutes": {"fix": minutes}}),
                             ["limitMinutes.fix must be a whole number of minutes above 0"])

    def test_shrink_claims_must_be_a_boolean_when_set(self):
        self.assertEqual(self.problems({**CONFIG, "resources": {"shrinkClaims": False}}), [])
        for value in ("false", 0, [True]):
            self.assertEqual(self.problems({**CONFIG, "resources": {"shrinkClaims": value}}),
                             ["resources.shrinkClaims must be true or false"])

    def test_invalid_json_says_so(self):
        problems = doctor.config_problems('{"slots": ')
        self.assertEqual(len(problems), 1)
        self.assertIn("isn't valid JSON", problems[0])

    def test_a_list_is_not_a_config(self):
        self.assertEqual(doctor.config_problems("[]"), ["it isn't a JSON object"])

    def test_the_runners_key_is_rejected_and_names_slots_and_env(self):
        problems = self.problems({**CONFIG, "runners": {"test": "game", "agent": "game-agent"}})
        self.assertEqual(problems, ["runners is gone, since the dispatcher starts every run: set how many runs of each "
                                    "kind start at once under slots, and move runners.env to env"])

    def test_env_must_map_names_to_strings(self):
        self.assertEqual(self.problems({**CONFIG, "env": {"WINE": "~/.wine"}}), [])
        for env in (["WINE=~/.wine"], {"WINE": 3}):
            self.assertEqual(self.problems({**CONFIG, "env": env}), ["env must be an object of variable names to strings"])

    def test_chore_days_must_be_positive_numbers(self):
        problems = self.problems({**CONFIG, "chores": {"every": {"audit": 0, "tidy": "7", "lint": True, "ok": 0.5}}})
        self.assertEqual(problems, [f"chores.every.{skill} must be a number of days above 0"
                                    for skill in ("audit", "tidy", "lint")])

    def test_chore_issue_labels_must_be_strings(self):
        self.assertEqual(self.problems({**CONFIG, "chores": {"issueLabels": {"triage": "bug", "audit": ""}}}), [])
        self.assertEqual(self.problems({**CONFIG, "chores": {"issueLabels": "security"}}),
                         ["chores.issueLabels must be an object of skill names to labels"])
        self.assertEqual(self.problems({**CONFIG, "chores": {"issueLabels": {"triage": 3}}}),
                         ['chores.issueLabels.triage must be a label, or "" for a chore that opens reviews'])

    def test_slots_must_be_known_kinds_with_whole_counts(self):
        self.assertEqual(self.problems({**CONFIG, "slots": {"agent": 3, "check": 0, "benchmark": 1}}), [])
        self.assertEqual(self.problems({**CONFIG, "slots": 3}), ["slots must be an object of run kinds to counts"])
        problems = self.problems({**CONFIG, "slots": {"agents": 3, "fix": -1, "chore": 1.5, "check": True}})
        self.assertEqual(problems, [
            "slots.agents isn't a slot; the slots are agent, chore, fix, check, benchmark",
            *[f"slots.{kind} must be a whole number of runs, 0 or more" for kind in ("fix", "chore", "check")]])

    def test_lists_must_be_lists(self):
        problems = self.problems({**CONFIG, "worktree": {"seeds": ".env"}})
        self.assertEqual(problems, ["worktree.seeds must be a list"])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "desk.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_missing_database_warns_and_registers_nothing(self):
        self.assertEqual(doctor.check_store(self.path).status, "warn")
        self.assertEqual(doctor.store_projects(self.path), {})
        self.assertFalse(self.path.exists())

    def test_a_database_install_wrote_opens_and_names_its_projects(self):
        desk = doctor.store.Store(self.path)
        desk.project("owner/game", "/home/me/game")
        desk.close()
        self.assertEqual(doctor.check_store(self.path), doctor.Check("ok", f"the local store at {self.path} opens"))
        self.assertEqual(doctor.store_projects(self.path), {"owner/game": "/home/me/game"})

    def test_a_schema_newer_than_the_code_fails(self):
        doctor.store.Store(self.path).close()
        db = doctor.sqlite3.connect(self.path)
        db.execute(f"PRAGMA user_version = {doctor.store.SCHEMA_VERSION + 1}")
        db.close()
        check = doctor.check_store(self.path)
        self.assertEqual(check.status, "fail")
        self.assertIn("newer than this checkout's", check.what)

    def test_a_file_that_isnt_a_database_fails(self):
        self.path.write_text("not a database")
        self.assertEqual(doctor.check_store(self.path).status, "fail")
        self.assertIsNone(doctor.store_projects(self.path))


class SliceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.units = Path(self.tmp.name)
        self.path = self.units / "agentdesk.slice"

    def test_a_missing_slice_warns_and_names_the_install(self):
        check = doctor.check_slice(self.units)
        self.assertEqual(check.status, "warn")
        self.assertIn(f"{self.path} is missing", check.what)
        self.assertEqual(check.fix, "agent-desk dispatch install")

    def test_the_slice_install_writes_passes(self):
        self.path.write_text(doctor.run_units.slice_text())
        self.assertEqual(doctor.check_slice(self.units),
                         doctor.Check("ok", "agentdesk.slice caps the runs at MemoryHigh=50%"))

    def test_another_memory_high_warns_with_both_values(self):
        self.path.write_text(doctor.run_units.slice_text().replace("MemoryHigh=50%", "MemoryHigh=80%"))
        self.assertEqual(doctor.check_slice(self.units),
                         doctor.Check("warn", "agentdesk.slice sets MemoryHigh=80% where install writes 50%",
                                      "agent-desk dispatch install"))

    def test_a_slice_without_memory_high_warns(self):
        self.path.write_text("[Slice]\nCPUWeight=20\n")
        self.assertEqual(doctor.check_slice(self.units),
                         doctor.Check("warn", "agentdesk.slice sets no MemoryHigh where install writes 50%",
                                      "agent-desk dispatch install"))

    def test_other_changes_warn_that_the_file_differs(self):
        self.path.write_text(doctor.run_units.slice_text() + "MemoryMax=40G\n")
        self.assertEqual(doctor.check_slice(self.units),
                         doctor.Check("warn", f"{self.path} differs from what install writes",
                                      "agent-desk dispatch install"))


class GitIdentityTest(unittest.TestCase):
    """check_git_identity against a real git, with the account's config in a temporary file and no system config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name, "gitconfig")
        self.config.write_text("")
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.config), "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)

    def set(self, key, value):
        self.assertEqual(doctor.run("git", "config", "--file", str(self.config), key, value)[0], 0)

    def test_no_identity_fails_with_both_fixes(self):
        self.assertEqual(doctor.check_git_identity(), doctor.Check(
            "fail", "git has no user.name or user.email for this account, so the agents can't commit their work",
            'git config --global user.name "Your Name"; git config --global user.email you@example.com'))

    def test_a_missing_email_fails_with_its_fix(self):
        self.set("user.name", "Ada Lovelace")
        self.assertEqual(doctor.check_git_identity(), doctor.Check(
            "fail", "git has no user.email for this account, so the agents can't commit their work",
            "git config --global user.email you@example.com"))

    def test_an_empty_name_fails(self):
        self.set("user.name", "")
        self.set("user.email", "ada@example.com")
        self.assertEqual(doctor.check_git_identity().status, "fail")

    def test_both_set_passes_and_names_the_identity(self):
        self.set("user.name", "Ada Lovelace")
        self.set("user.email", "ada@example.com")
        self.assertEqual(doctor.check_git_identity(),
                         doctor.Check("ok", "git commits as Ada Lovelace <ada@example.com>"))

    def test_the_commands_check_includes_it_after_git(self):
        with mock.patch.object(doctor.shutil, "which", side_effect=lambda name: None if name != "git" else name):
            checks = doctor.check_commands()
        self.assertEqual([check.what for check in checks[:2]], [
            "git", "git has no user.name or user.email for this account, so the agents can't commit their work"])


class StateFolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "agent-desk"

    def test_a_missing_state_folder_passes(self):
        self.assertEqual(doctor.check_state(self.state).status, "ok")

    def test_a_folder_group_or_others_can_enter_warns_and_its_fix_narrows_it(self):
        self.state.mkdir()
        self.state.chmod(0o755)
        check = doctor.check_state(self.state)
        self.assertEqual(check.status, "warn")
        self.assertIn("0755", check.what)
        self.assertEqual(check.fix, f"chmod 700 {self.state}")
        doctor.subprocess.run(check.fix.split(), check=True)
        self.assertEqual(doctor.check_state(self.state),
                         doctor.Check("ok", f"the state folder {self.state} is private to its owner"))

    def test_the_folder_the_store_creates_passes(self):
        with mock.patch.object(doctor.store, "STATE", self.state):
            doctor.store.Store(self.state / "desk.sqlite").close()
        self.assertEqual(doctor.check_state(self.state).status, "ok")


class LeftoversTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.cache = Path(self.tmp.name) / "cache"

    def test_nothing_left_passes(self):
        self.state.mkdir()
        (self.state / "desk.sqlite").touch()
        self.assertEqual(doctor.check_leftovers(self.state, self.cache).status, "ok")

    def test_the_budget_queue_cache_and_ledger_left_warn_and_the_fix_deletes_them(self):
        (self.state / "budget-queue").mkdir(parents=True)
        (self.state / "budget-queue" / "0001-42").touch()
        (self.state / "budget-last-start").touch()
        (self.state / "failures").mkdir()
        (self.state / "desk.sqlite").touch()
        (self.cache / "gh" / "pr").mkdir(parents=True)
        check = doctor.check_leftovers(self.state, self.cache)
        left = [self.state / "budget-queue", self.state / "budget-last-start", self.state / "failures",
                self.cache / "gh"]
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.fix, "rm -r " + " ".join(map(str, left)))
        doctor.subprocess.run(check.fix.split(), check=True)
        self.assertEqual(doctor.check_leftovers(self.state, self.cache).status, "ok")
        self.assertTrue((self.state / "desk.sqlite").exists())


class ProjectStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.main = self.root / "game"
        self.main.mkdir()
        (self.main / ".agent-desk.json").write_text(json.dumps({**CONFIG, "host": "none"}))
        (self.root / "config" / "agent-desk").mkdir(parents=True)
        (self.root / "config" / "agent-desk" / "projects").write_text(f"owner/game {self.main}\n")
        self.path = self.root / "desk.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def checks(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")}), \
                mock.patch.object(doctor.store, "DATABASE", self.path), \
                mock.patch.object(doctor.chore_skills, "resolves", return_value=True):
            return [(one.status, one.what) for one in doctor.check_project(ROOT, "owner/game", self.main, self.main)
                    if "store" in one.what]

    def test_a_project_the_store_lacks_fails(self):
        doctor.store.Store(self.path).close()
        self.assertEqual(self.checks(), [("fail", "the local store doesn't register owner/game")])

    def test_a_project_registered_at_its_checkout_passes(self):
        desk = doctor.store.Store(self.path)
        desk.project("owner/game", str(self.main))
        desk.close()
        self.assertEqual(self.checks(), [("ok", f"the local store registers owner/game at {self.main}")])

    def test_a_project_registered_elsewhere_fails(self):
        desk = doctor.store.Store(self.path)
        desk.project("owner/game", "/elsewhere")
        desk.close()
        self.assertEqual(self.checks(),
                         [("fail", f"the local store registers owner/game at /elsewhere, not {self.main}")])


class ProjectTestCommandTest(unittest.TestCase):
    """check_project's warning about a project that sets no `test` command."""

    def checks(self, config):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        main = Path(tmp.name)
        (main / ".agent-desk.json").write_text(json.dumps({**CONFIG, "host": "none", **config}))
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(main / "config")}), \
                mock.patch.object(doctor.store, "DATABASE", main / "desk.sqlite"), \
                mock.patch.object(doctor.chore_skills, "resolves", return_value=True):
            return main, [one for one in doctor.check_project(ROOT, "owner/game", main, main)
                          if "test" in one.what]

    def test_a_missing_or_empty_test_command_warns_that_reviews_merge_untested(self):
        for config in ({}, {"test": ""}, {"test": "  "}):
            main, found = self.checks(config)
            path = main / ".agent-desk.json"
            self.assertEqual(found, [doctor.Check(
                "warn", f"{path} sets no test command, so no branch check runs and its reviews merge untested",
                f'set "test" in {path} to the command that tests a branch, such as "just test"')])

    def test_a_test_command_passes(self):
        main, found = self.checks({"test": "just test"})
        self.assertEqual([(one.status, one.what) for one in found],
                         [("ok", f"{main / '.agent-desk.json'} sets test, which each branch check runs: just test")])

    def test_the_warning_keeps_doctors_exit_status(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        main = Path(tmp.name)
        (main / ".agent-desk.json").write_text(json.dumps({**CONFIG, "host": "none", "test": ""}))
        (main / "config" / "agent-desk").mkdir(parents=True)
        (main / "config" / "agent-desk" / "projects").write_text(f"owner/game {main}\n")
        desk = doctor.store.Store(main / "desk.sqlite")
        desk.project("owner/game", str(main))
        desk.close()
        ok = doctor.Check("ok", "a machine check")
        printed = []
        with mock.patch.object(doctor, "check_commands", return_value=[ok]), \
                mock.patch.object(doctor, "check_cgroup", return_value=ok), \
                mock.patch.object(doctor, "check_path", return_value=ok), \
                mock.patch.object(doctor, "check_plugin", return_value=[ok]), \
                mock.patch.object(doctor, "check_state", return_value=ok), \
                mock.patch.object(doctor, "check_leftovers", return_value=ok), \
                mock.patch.object(doctor, "check_store", return_value=ok), \
                mock.patch.object(doctor, "check_usage", return_value=ok), \
                mock.patch.object(doctor.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(main / "config")}), \
                mock.patch.object(doctor.store, "DATABASE", main / "desk.sqlite"), \
                mock.patch.object(doctor.chore_skills, "resolves", return_value=True), \
                mock.patch("builtins.print", lambda *args, **kwargs: printed.append(" ".join(map(str, args)))):
            status = doctor.main([str(ROOT), "owner/game", str(main), str(main)])
        self.assertEqual(status, 0)
        self.assertIn("warn    " + f"{main / '.agent-desk.json'} sets no test command, so no branch check runs and its "
                      "reviews merge untested", printed)
        self.assertEqual(printed[-1], "0 failed, 1 warnings.")


class GitHubTest(unittest.TestCase):
    """check_github, with gh's answers stood in by `answers`: its exit status and output for each subcommand."""

    def checks(self, answers, on_path=True):
        def run(*command, env=None):
            return answers.get(command[1], (0, ""))

        with mock.patch.object(doctor.shutil, "which", return_value="/usr/bin/gh" if on_path else None), \
                mock.patch.object(doctor, "run", run):
            return [(one.status, one.what) for one in doctor.check_github("owner/game")]

    def test_gh_missing_or_logged_out_warns_without_failing(self):
        self.assertEqual(self.checks({}, on_path=False),
                         [("warn", "gh isn't on PATH, and releases and agent-desk import need it")])
        self.assertEqual(self.checks({"auth": (1, "")}),
                         [("warn", "gh isn't logged in, and releases and agent-desk import need it")])

    def test_an_open_pr_from_an_agent_branch_is_named(self):
        prs = [{"number": 7, "headRefName": "agent/issue-3"}, {"number": 8, "headRefName": "fix-typo"},
               {"number": 9, "headRefName": "agent/chore-audit-1"}]
        self.assertEqual(self.checks({"pr": (0, json.dumps(prs))}), [
            ("ok", "gh is logged in, for releases and imports"),
            ("warn", "GitHub has open PRs from agent/* branches, which the desk never sees: #7, #9"),
        ])

    def test_no_agent_pr_passes(self):
        prs = [{"number": 8, "headRefName": "fix-typo"}]
        self.assertEqual(self.checks({"pr": (0, json.dumps(prs))})[-1],
                         ("ok", "GitHub has no open PR from an agent/* branch"))

    def test_a_failed_listing_warns(self):
        self.assertEqual(self.checks({"pr": (1, "")})[-1], ("warn", "gh couldn't list owner/game's open PRs on GitHub"))

    def test_a_project_on_another_host_skips_github(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        main = Path(tmp.name)
        (main / ".agent-desk.json").write_text(json.dumps({**CONFIG, "host": "none"}))
        with mock.patch.object(doctor, "check_github") as github, \
                mock.patch.object(doctor.store, "DATABASE", main / "desk.sqlite"), \
                mock.patch.object(doctor.chore_skills, "resolves", return_value=True):
            doctor.check_project(ROOT, "owner/game", main, main)
        github.assert_not_called()


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def plugin(self, name, folder, skills=()):
        manifest = self.root / folder / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": name}))
        for skill in skills:
            path = self.root / folder / "skills" / skill / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {skill}\ndescription: x\n---\n")
        return (self.root / folder).resolve()

    def folders(self, env="", settings=None, installed=None, checkout="game"):
        home = self.root / "claude"
        home.mkdir(exist_ok=True)
        (home / "settings.json").write_text(json.dumps(settings or {}))
        (home / "plugins").mkdir(exist_ok=True)
        (home / "plugins" / "installed_plugins.json").write_text(json.dumps(installed or {}))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_PLUGIN_DIRS": env}):
            return doctor.plugin_folders(self.root / checkout, home)

    def test_finds_the_plugin_in_the_environment_or_settings(self):
        desk = self.plugin("agent-desk", "desk/inbox")
        other = self.plugin("other", "other")
        settings = {"env": {"CLAUDE_CODE_PLUGIN_DIRS": f"{other} : {desk}"}}
        self.assertEqual(self.folders(settings=settings), [desk])
        self.assertEqual(self.folders(env=str(desk)), [desk])
        self.assertEqual(self.folders(env=str(desk), settings=settings), [desk])

    def test_finds_an_installed_plugin_for_the_user_or_the_checkout(self):
        desk = self.plugin("agent-desk", "cache/agent-desk")
        (self.root / "game").mkdir()
        local = {"scope": "local", "projectPath": str(self.root / "game"), "installPath": str(desk)}
        installed = {"plugins": {"agent-desk@market": [local]}}
        self.assertEqual(self.folders(installed=installed, checkout="elsewhere"), [])
        self.assertEqual(self.folders(installed=installed), [desk])
        user = {"plugins": {"agent-desk@market": [{"scope": "user", "installPath": str(desk)}]}}
        self.assertEqual(self.folders(installed=user, checkout="elsewhere"), [desk])

    def test_two_copies_both_show(self):
        one = self.plugin("agent-desk", "one/inbox")
        two = self.plugin("agent-desk", "two/inbox")
        self.assertEqual(self.folders(env=f"{one}:{two}"), [one, two])

    def test_a_marketplace_install_passes_at_the_checkout_version_and_warns_at_another(self):
        home = self.root / "claude"
        (home / "plugins").mkdir(parents=True)
        (home / "settings.json").write_text("{}")
        desk = self.root / "desk"
        (desk / "inbox" / "skills" / "work-issue").mkdir(parents=True)
        (desk / "inbox" / "skills" / "work-issue" / "SKILL.md").write_text("---\nname: work-issue\n---\n")
        (desk / "VERSION").write_text("0.2.0\n")
        cache = self.plugin("agent-desk", "cache/agent-desk/0.2.0", ["work-issue"])
        (home / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"plugins": {"agent-desk@agent-desk": [{"scope": "user", "installPath": str(cache)}]}}))

        def checks(version):
            (cache / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "agent-desk", "version": version}))
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_PLUGIN_DIRS": "", "CLAUDE_CONFIG_DIR": str(home)}):
                return [(one.status, one.what) for one in doctor.check_plugin(desk, self.root / "game")]

        self.assertEqual(checks("0.2.0"), [("ok", f"the agent-desk plugin loads from its marketplace install at {cache}"),
                                           ("ok", "its skills resolve: work-issue")])
        self.assertEqual(checks("0.1.0")[0],
                         ("warn", "the installed agent-desk plugin is version 0.1.0, and this checkout is 0.2.0"))

    def test_missing_skills_are_named(self):
        desk = self.plugin("agent-desk", "desk/inbox", ["work-issue"])
        wrong = desk / "skills" / "create-issue" / "SKILL.md"
        wrong.parent.mkdir(parents=True)
        wrong.write_text("---\nname: other\n---\n")
        self.assertEqual(doctor.missing_skills(desk, ["work-issue", "create-issue", "resource-report"]),
                         ["create-issue", "resource-report"])


class NodeTest(unittest.TestCase):
    def test_reads_the_major_version(self):
        self.assertEqual(doctor.node_major("v24.1.0"), 24)
        self.assertEqual(doctor.node_major("v9.0.0"), 9)
        self.assertEqual(doctor.node_major(""), 0)


if __name__ == "__main__":
    unittest.main()
