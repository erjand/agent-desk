"""Tests for the base:<branch> label: lib/worktree.sh cutting a branch from the local base, lib/work-issue.sh stopping on
a base the main checkout has no branch for, and lib/clean.sh leaving main and the release to the merge and push queues. Each runs the script
against a real origin and main checkout in a temporary folder, with a stand-in for gh that logs its arguments and,
for work-issue, a temporary store."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

import store  # noqa: E402


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Repo(unittest.TestCase):
    """An origin with main and local-desk, a main checkout cloned from it whose main and local-desk each hold a commit
    origin lacks, as after the merge queue moved them, an origin-only branch, and a gh stand-in."""

    # The stand-in's answers, as `case` patterns on its arguments and what each prints.
    ANSWERS = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        origin = root / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=root)
        self.main = root / "desk"
        git("clone", "-q", str(origin), str(self.main), cwd=root)
        for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            git("config", key, value, cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", "on main", cwd=self.main)
        git("push", "-q", "origin", "main", cwd=self.main)
        git("checkout", "-q", "-b", "local-desk", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", "on local-desk", cwd=self.main)
        git("push", "-q", "origin", "local-desk", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", "on the local local-desk", cwd=self.main)
        git("push", "-q", "origin", "local-desk:only-on-origin", cwd=self.main)
        git("checkout", "-q", "main", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", "on the local main", cwd=self.main)
        (self.main / ".agent-desk.json").write_text("{}")
        self.main_sha = git("rev-parse", "main", cwd=self.main)
        self.base_sha = git("rev-parse", "local-desk", cwd=self.main)

        fakes = root / "fakes"
        fakes.mkdir()
        self.calls = root / "gh.log"
        self.calls.touch()
        cases = "".join(f'    {pattern}) printf \'%s\\n\' {answer!r};;\n' for pattern, answer in self.ANSWERS.items())
        gh = fakes / "gh"
        gh.write_text(f'#!/bin/bash\necho "$*" >>"{self.calls}"\ncase "$*" in\n{cases}esac\n')
        gh.chmod(0o755)
        state = root / "state"
        self.env = {**os.environ, "PATH": f"{fakes}:{os.environ['PATH']}", "AGENT_DESK_CHECKOUT": str(self.main),
                    "GH_REPO": "o/r", "XDG_STATE_HOME": str(state), "XDG_CONFIG_HOME": str(root / "config")}
        for name in ("AGENT_DESK_UNIT", "REPLY_PR", "EVENT", "AGENT_DESK_BASE"):
            self.env.pop(name, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_lib(self, script, *args, env=None):
        return subprocess.run(["bash", str(LIB / script), *args], cwd=self.main, env={**self.env, **(env or {})},
                              capture_output=True, text=True)

    def gh_calls(self):
        return self.calls.read_text().splitlines()

    def worktree(self, slug):
        return self.main.parent / f"desk-{slug}"


class IssueBasesTest(Repo):
    def bases(self, labels):
        result = subprocess.run(["bash", "-c", f'source "{LIB}/common.sh"; desk_issue_bases "$1"', "-", labels],
                                env=self.env, capture_output=True, text=True, check=True)
        return result.stdout.splitlines()

    def test_an_issue_without_the_label_names_no_base(self):
        self.assertEqual(self.bases("agent-ready,enhancement"), [])
        self.assertEqual(self.bases(""), [])
        self.assertEqual(self.bases("base:"), [])

    def test_the_label_names_its_branch(self):
        self.assertEqual(self.bases("in-progress,base:local-desk,serial:web"), ["local-desk"])
        self.assertEqual(self.bases("base:a,base:b"), ["a", "b"])


class WorktreeBaseTest(Repo):
    def test_the_default_base_is_main(self):
        result = self.run_lib("worktree.sh", "issue-5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worktree("issue-5")), self.main_sha)

    def test_a_named_base_cuts_the_branch_from_it(self):
        result = self.run_lib("worktree.sh", "issue-5", "local-desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "agent/issue-5", cwd=self.main), self.base_sha)

    def test_the_base_can_come_from_the_environment(self):
        result = self.run_lib("worktree.sh", "issue-5", env={"AGENT_DESK_BASE": "local-desk"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "agent/issue-5", cwd=self.main), self.base_sha)

    def test_a_base_without_a_local_branch_makes_nothing(self):
        for base in ("nope", "only-on-origin"):
            result = self.run_lib("worktree.sh", "issue-5", base)
            self.assertEqual(result.returncode, 4)
            self.assertIn(f"The main checkout has no local branch {base} to cut agent/issue-5 from.", result.stderr)
            self.assertFalse(self.worktree("issue-5").exists())
            self.assertEqual(git("branch", "--list", "agent/issue-5", cwd=self.main), "")

    def test_a_branch_starts_with_origin_out_of_reach(self):
        git("remote", "set-url", "origin", str(self.main.parent / "missing.git"), cwd=self.main)
        result = self.run_lib("worktree.sh", "issue-5", "local-desk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "agent/issue-5", cwd=self.main), self.base_sha)

    def test_a_local_agent_branch_that_exists_is_checked_out(self):
        git("branch", "agent/issue-5", "local-desk~1", cwd=self.main)
        result = self.run_lib("worktree.sh", "issue-5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worktree("issue-5")),
                         git("rev-parse", "local-desk~1", cwd=self.main))


class WorkIssueBaseTest(Repo):
    # work-issue and serial claim and release read the issue, its labels, and its state from the store.
    def setUp(self):
        super().setUp()
        desk = store.Store(Path(self.env["XDG_STATE_HOME"]) / "agent-desk" / "desk.sqlite")
        self.addCleanup(desk.close)
        for number, labels in ((73, ["base:nope"]), (74, ["base:a", "base:b"])):
            desk.create("o/r", "issue", f"Issue {number}", labels=labels, number=number)
            desk.set_state("o/r", number, "in-progress")
        self.desk = desk

    def notes(self, number):
        return [one["body"] for one in self.desk.item("o/r", number)["comments"]]

    def test_a_base_without_a_local_branch_stops_the_run_with_a_comment_naming_it(self):
        result = self.run_lib("work-issue.sh", "73", env={"AGENT_DESK_UNIT": "agent-desk-agent-o-r-73-1.service"})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.desk.item("o/r", 73)["status"], "open")
        [note] = self.notes(73)
        self.assertTrue(note.startswith("The agent didn't start: the main checkout has no branch `nope`, which the "
                                        "issue's `base:nope` label names."), note)
        self.assertIn("(log: `journalctl --user -u agent-desk-agent-o-r-73-1.service`)", note)
        self.assertTrue(note.endswith("<sub>Posted by agent-desk in agent-desk-agent-o-r-73-1</sub>"), note)
        self.assertFalse(self.worktree("issue-73").exists())

    def test_two_base_labels_stop_the_run(self):
        result = self.run_lib("work-issue.sh", "74")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("more than one base label (`base:a` `base:b`)", "\n".join(self.notes(74)))
        self.assertFalse(self.worktree("issue-74").exists())


class CleanBaseTest(Repo):
    """The release a cleanup no longer publishes. tests/test_clean.py covers the rest of the cleanup against the store;
    here the store has no review of the branch, so the cleanup takes its merge as one into main."""

    # lib/serial.sh's release still reads GitHub.
    ANSWERS = {
        '"issue list"*': "[]",
    }

    def clean(self, issue, version=None, lib=LIB):
        """Runs the cleanup of a merged agent/issue-<issue> after main moves on origin, changing VERSION to `version`
        when given, and returns what it prints."""
        slug = f"issue-{issue}"
        git("push", "-q", "origin", f"origin/local-desk:refs/heads/agent/{slug}", cwd=self.main)
        # main moves on origin, so a fast-forward shows in the main checkout.
        other = self.main.parent / "other"
        git("clone", "-q", "-b", "main", str(self.main.parent / "origin.git"), str(other), cwd=self.main.parent)
        if version:
            (other / "VERSION").write_text(version + "\n")
            git("add", "VERSION", cwd=other)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
            "commit", "-q", "--allow-empty", "-m", "moved", cwd=other)
        git("push", "-q", "origin", "main", cwd=other)
        result = subprocess.run(["bash", str(lib / "clean.sh"), f"agent/{slug}", "true"], cwd=self.main,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def as_desk_root(self, publish_status=0):
        """Makes the main checkout agent-desk's own, with lib/ and bin/ copied in, since serial release reads the store
        through bin/agent-desk, and a scripts/release.py that logs its arguments and exits with `publish_status`, and
        returns its lib/ and the log."""
        shutil.copytree(LIB, self.main / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(LIB.parent / "bin", self.main / "bin")
        (self.main / "scripts").mkdir()
        log = self.main.parent / "release.log"
        (self.main / "scripts" / "release.py").write_text(
            f"import os, sys\nopen({str(log)!r}, 'a').write(os.getcwd() + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
            f"sys.exit({publish_status})\n")
        return self.main / "lib", log

    def test_a_merge_leaves_main_and_the_release_to_the_merge_and_push_queues(self):
        lib, log = self.as_desk_root()
        before = git("rev-parse", "HEAD", cwd=self.main)
        out = self.clean(9, version="0.2.0", lib=lib)
        self.assertIn("Closed #9", out)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.main), before)
        self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
