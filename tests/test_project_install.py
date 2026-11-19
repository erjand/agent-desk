"""Tests for lib/install.sh, which `agent-desk install` runs to set agent-desk up in a project."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lib" / "install.sh"
DESK = ROOT / "bin" / "agent-desk"


class InstallCase(unittest.TestCase):
    """install.sh, and the issue that follows it, on a machine whose gh always fails, as one with no gh login does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        # The origin names GitHub, as a project's does, and a stand-in for ssh serves it from a bare repository here,
        # so git reaches it without any GitHub login.
        remote = self.root / "remote" / "owner"
        remote.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote / "project.git")], check=True)
        ssh = self.root / "ssh"
        ssh.write_text(f'#!/bin/bash\ncd {remote.parent} && eval "$2"\n')
        ssh.chmod(0o755)
        identity = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_SSH_COMMAND": str(ssh), "GIT_SSH_VARIANT": "simple"}
        git_env = {**os.environ, **identity}
        self.checkout = self.root / "project"
        subprocess.run(["git", "clone", "-q", "git@github.com:owner/project.git", str(self.checkout)], check=True,
                       capture_output=True, env=git_env)
        self.git = ["git", "-C", str(self.checkout)]
        subprocess.run([*self.git, "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        subprocess.run([*self.git, "commit", "-q", "--allow-empty", "-m", "first"], check=True, env=git_env)
        subprocess.run([*self.git, "push", "-q", "origin", "main"], check=True, capture_output=True, env=git_env)
        # A stand-in for gh logs each call and fails it, as gh does without a login.
        self.fakes = self.root / "fakes"
        self.fakes.mkdir()
        self.calls = self.root / "gh.log"
        self.calls.write_text("")
        gh = self.fakes / "gh"
        gh.write_text("#!/usr/bin/env python3\n"
                      "import json, sys\n"
                      f"open({str(self.calls)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                      "print('You are not logged into any GitHub hosts. To log in, run: gh auth login',"
                      " file=sys.stderr)\n"
                      "sys.exit(4)\n")
        gh.chmod(0o755)
        self.env = {
            **{key: value for key, value in os.environ.items()
               if key not in ("GH_REPO", "AGENT_DESK_RUN", "AGENT_DESK_AUTHOR", "AGENT_DESK_CHECKOUT", "AGENT_DESK_UNIT",
                              "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID")},
            "HOME": str(self.home),
            "PATH": f"{self.fakes}:{os.environ['PATH']}",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            **identity,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *args, status=0):
        result = subprocess.run(
            [str(SCRIPT), *args], cwd=self.checkout, env=self.env, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, status, result.stderr)
        return result.stdout if status == 0 else result.stderr

    def desk(self, *args, cwd=None):
        result = subprocess.run([str(DESK), *args], cwd=cwd or self.checkout, env=self.env, capture_output=True,
                                text=True, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


class ProjectInstallTest(InstallCase):
    """A project whose origin is on GitHub."""

    def test_a_new_config_names_the_github_host_and_passes_the_doctor(self):
        out = self.install()
        self.assertIn("Wrote", out)
        config = (self.checkout / ".agent-desk.json").read_text()
        self.assertEqual(json.loads(config)["host"], "github")
        sys.path.insert(0, str(ROOT / "lib"))
        import doctor
        self.assertEqual(doctor.config_problems(config), [])

    def test_a_new_config_has_an_empty_test_command(self):
        self.install()
        config = json.loads((self.checkout / ".agent-desk.json").read_text())
        self.assertEqual(config["test"], "")
        self.assertNotIn("runners", config)

    def test_install_records_the_project_and_registers_it_in_the_store(self):
        out = self.install()
        projects = (self.home / ".config" / "agent-desk" / "projects").read_text()
        self.assertEqual(projects, f"owner/project {self.checkout.resolve()}\n")
        self.assertIn("Registered owner/project in the local store.", out)
        database = self.home / ".local" / "state" / "agent-desk" / "desk.sqlite"
        db = sqlite3.connect(database)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT slug, path FROM projects").fetchall(),
                         [("owner/project", str(self.checkout.resolve()))])

    def test_install_writes_no_workflows_and_creates_no_labels(self):
        self.install()
        self.assertFalse((self.checkout / ".github").exists())
        self.assertEqual(self.calls.read_text(), "")

    def test_install_then_an_issue_and_its_start_work_without_a_gh_login(self):
        self.install()
        subprocess.run([*self.git, "add", ".agent-desk.json"], check=True)
        subprocess.run([*self.git, "commit", "-q", "-m", "config"], check=True, env=self.env)
        number = self.desk("issue", "create", "--title", "First issue").strip()
        self.assertEqual(number, "1")
        out = self.desk("start", number)
        worktree = self.root / "project-issue-1"
        self.assertIn(f"Working #1 in {worktree}", out)
        branch = subprocess.run(["git", "-C", str(worktree), "branch", "--show-current"], capture_output=True,
                                text=True, check=True).stdout.strip()
        self.assertEqual(branch, "agent/issue-1")
        issue = json.loads(self.desk("issue", "view", "1", "--json", "status,comments"))
        self.assertEqual(issue["status"], "in-progress")
        self.assertTrue(issue["comments"][-1]["body"].startswith("An agent started on `agent/issue-1` in a local "
                                                                 "session."), issue["comments"])
        self.assertEqual(self.calls.read_text(), "")

    def test_install_takes_a_name_only_for_a_project_whose_host_is_none(self):
        err = self.install("owner/other", status=2)
        self.assertIn('only when .agent-desk.json sets "host": "none"', err)
        self.assertFalse((self.home / ".config" / "agent-desk" / "projects").exists())

    def test_install_follows_an_origin_renamed_on_github(self):
        self.install()
        subprocess.run([*self.git, "remote", "set-url", "origin", "git@github.com:owner/renamed.git"], check=True)
        self.assertIn("Recorded owner/renamed", self.install())
        projects = (self.home / ".config" / "agent-desk" / "projects").read_text().splitlines()
        self.assertIn(f"owner/renamed {self.checkout.resolve()}", projects)
        self.assertEqual(self.desk("issue", "create", "--title", "After the rename").strip(), "1")
        self.assertEqual(json.loads(self.desk("issue", "view", "1", "--repo", "owner/renamed", "--json", "title")),
                         {"title": "After the rename"})


class LocalProjectInstallTest(InstallCase):
    """A project whose config sets "host": "none", with an origin that is a path on disk or no remote at all."""

    def use_local_origin(self):
        """Points the checkout's origin at the bare repository by its path, as a scratch project's may."""
        bare = self.root / "remote" / "owner" / "project.git"
        subprocess.run([*self.git, "remote", "set-url", "origin", str(bare)], check=True)
        subprocess.run([*self.git, "fetch", "-q", "origin"], check=True, env=self.env)

    def commit_config(self):
        """Commits a config whose host is none, so worktrees cut from main carry it."""
        config = {"host": "none"}
        (self.checkout / ".agent-desk.json").write_text(json.dumps(config))
        subprocess.run([*self.git, "add", ".agent-desk.json"], check=True)
        subprocess.run([*self.git, "commit", "-q", "-m", "config"], check=True, env=self.env)
        if subprocess.run([*self.git, "remote"], capture_output=True, text=True, check=True).stdout.strip():
            subprocess.run([*self.git, "push", "-q", "origin", "main"], check=True, capture_output=True,
                           env=self.env)

    def board(self, cwd=None):
        return json.loads(self.desk("board", "--json", cwd=cwd))

    def test_a_local_origin_needs_the_name_then_every_command_finds_the_project(self):
        self.use_local_origin()
        self.commit_config()
        err = self.install(status=2)
        self.assertIn("name the project: agent-desk install <owner>/<repo>", err)
        self.assertIn("Recorded me/scratch", self.install("me/scratch"))
        self.assertIn("Recorded me/scratch", self.install())
        self.assertEqual(self.desk("issue", "create", "--title", "First issue").strip(), "1")
        self.assertEqual([issue["title"] for issue in self.board()["issues"]], ["First issue"])
        out = self.desk("start", "1")
        worktree = self.root / "project-issue-1"
        self.assertIn(f"Working #1 in {worktree}", out)
        self.assertEqual([issue["status"] for issue in self.board(cwd=worktree)["issues"]], ["in-progress"])
        self.assertEqual(json.loads(self.desk("issue", "view", "1", "--json", "number", cwd=worktree))["number"], 1)
        # The pane's Start picks the issue by the project's name, which hold.py finds the way the store does.
        picked = subprocess.run([sys.executable, "-B", str(ROOT / "lib" / "hold.py"), "start", "1"], cwd=worktree,
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual(picked.returncode, 0, picked.stderr)
        hold = json.loads((self.home / ".local" / "state" / "agent-desk" / "hold.json").read_text())
        self.assertEqual(hold["picked"], ["me/scratch#1"])
        self.assertEqual(self.calls.read_text(), "")

    def test_a_checkout_with_no_remote_installs_by_name_and_shows_its_board(self):
        subprocess.run([*self.git, "remote", "remove", "origin"], check=True)
        self.commit_config()
        self.assertIn("name the project", self.install(status=2))
        self.install("me/alone")
        projects = (self.home / ".config" / "agent-desk" / "projects").read_text()
        self.assertEqual(projects, f"me/alone {self.checkout.resolve()}\n")
        self.assertEqual(self.desk("issue", "create", "--title", "Alone").strip(), "1")
        (self.checkout / "sub").mkdir()
        self.assertEqual([issue["title"] for issue in self.board(cwd=self.checkout / "sub")["issues"]], ["Alone"])
        self.assertEqual(self.calls.read_text(), "")

    def test_a_first_install_by_name_writes_a_config_whose_host_is_none(self):
        self.use_local_origin()
        config = self.checkout / ".agent-desk.json"
        err = self.install(status=2)
        self.assertIn("agent-desk install <owner>/<repo> writes a config whose host is none", err)
        self.assertFalse(config.exists())
        out = self.install("me/fresh")
        self.assertIn("Wrote", out)
        self.assertIn("Recorded me/fresh", out)
        self.assertEqual(json.loads(config.read_text())["host"], "none")
        self.assertIn("Recorded me/fresh", self.install())
        self.assertEqual(self.desk("issue", "create", "--title", "Fresh").strip(), "1")
        self.assertEqual([issue["title"] for issue in self.board()["issues"]], ["Fresh"])

    def test_a_first_install_with_no_remote_writes_a_config_whose_host_is_none(self):
        subprocess.run([*self.git, "remote", "remove", "origin"], check=True)
        self.install("me/alone")
        self.assertEqual(json.loads((self.checkout / ".agent-desk.json").read_text())["host"], "none")

    def test_install_refuses_a_name_that_is_not_owner_and_repo(self):
        self.commit_config()
        self.assertIn("Name the project as <owner>/<repo>", self.install("scratch", status=2))


if __name__ == "__main__":
    unittest.main()
