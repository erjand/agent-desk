"""Tests for agent-desk/lib/cutover.py, run as `agent-desk cutover` runs it, against the gh stand-in in
tests/fixtures/cutover/gh and temporary git repositories."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import cutover  # noqa: E402
import store  # noqa: E402

REPO = "org/game"
FAKE_GH = ROOT / "tests" / "fixtures" / "cutover"
AGENTS = "# Game\n\n## Agent Desk\n\n- **Tests.** `just test` runs them.\n\n## Other\n\nText.\n"
WORKFLOWS = ["agent.yml", "agent-cleanup.yml", "chores.yml", "fix.yml", "test.yml", "test-hosted.yml"]
# The steps a full cutover of the setup's project records: each GitHub change's intent and then its result.
ALL_STEPS = (["recorded"] + ["workflow-disabling", "workflow-disabled"] * 5
             + ["pr-closing", "pr-closed"])
# The steps `--issues-off` adds.
ISSUES_STEPS = ["issues-turning-off", "issues-off"]


class CutoverCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "gh").symlink_to(FAKE_GH / "gh")
        systemctl = self.bin / "systemctl"
        systemctl.write_text('#!/usr/bin/env bash\necho "$*" >>"$FAKE_SYSTEMCTL_LOG"\n'
                             '[[ $* != *is-active* || " $FAKE_ACTIVE " == *" ${*: -1} "* ]]\n')
        systemctl.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("AGENT_DESK_", "GH_", "GITHUB_", "XDG_", "GIT_"))}
        self.env.update({
            "HOME": str(self.root), "XDG_STATE_HOME": str(self.state), "XDG_CONFIG_HOME": str(self.config),
            "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_GH_STATE": str(self.root / "github.json"),
            "FAKE_GH_LOG": str(self.root / "gh.log"), "FAKE_SYSTEMCTL_LOG": str(self.root / "systemctl.log"),
            "FAKE_ACTIVE": "agent-desk-dispatch.service", "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1",
        })
        self.origin = self.root / "org" / "game.git"
        self.origin.parent.mkdir()
        self.checkout = self.root / "game"
        self.git("init", "--quiet", "--bare", "--initial-branch=main", str(self.origin))
        self.git("clone", "--quiet", str(self.origin), str(self.checkout))
        (self.checkout / "AGENTS.md").write_text(AGENTS)
        self.configure({"host": "github"})
        self.git("-C", str(self.checkout), "add", ".")
        self.git("-C", str(self.checkout), "commit", "--quiet", "-m", "start")
        self.git("-C", str(self.checkout), "push", "--quiet", "origin", "main")
        self.git("-C", str(self.checkout), "push", "--quiet", "origin", "main:agent/issue-2")
        (self.config / "agent-desk").mkdir(parents=True)
        (self.config / "agent-desk" / "projects").write_text(f"{REPO} {self.checkout}\n")
        self.hold({"state": "running", "projects": {REPO: "held"}})
        self.github({
            "repo": REPO, "has_issues": True,
            "workflows": [{"id": index + 1, "path": f".github/workflows/{name}", "state": "active"}
                          for index, name in enumerate(WORKFLOWS)],
            "issues": [{"number": 1, "labels": [{"name": "enhancement"}]},
                       {"number": 2, "labels": [{"name": "agent-ready"}, {"name": "serial:web"}]},
                       {"number": 6, "labels": [{"name": "needs-design"}, {"name": "in-progress"}]}],
            "pulls": {"7": {"state": "open", "head": "agent/issue-2", "sha": self.head("main"), "comments": []}},
        })

    def git(self, *args):
        return subprocess.run(["git", *args], check=True, env=self.env, capture_output=True, text=True).stdout

    def head(self, ref):
        return self.git("-C", str(self.checkout), "rev-parse", ref).strip()

    def pull(self, head, **fields):
        return {"state": "open", "head": head, "sha": self.head("main"), "comments": [], **fields}

    def configure(self, config):
        (self.checkout / ".agent-desk.json").write_text(json.dumps(config))

    def hold(self, data):
        (self.state / "agent-desk").mkdir(parents=True, exist_ok=True)
        (self.state / "agent-desk" / "hold.json").write_text(json.dumps(data))

    def github(self, data=None, **changes):
        path = self.root / "github.json"
        if data is None:
            data = json.loads(path.read_text())
        data.update(changes)
        path.write_text(json.dumps(data))
        return data

    def cutover(self, *args, status=0):
        done = subprocess.run([sys.executable, "-B", str(ROOT / "lib" / "cutover.py"), *args], env=self.env,
                              cwd=self.root, capture_output=True, text=True)
        self.assertEqual(done.returncode, status, done.stdout + done.stderr)
        return done.stdout, done.stderr

    def calls(self, changing=True):
        """The gh calls so far, or only those that change GitHub."""
        try:
            lines = (self.root / "gh.log").read_text().splitlines()
        except OSError:
            return []
        calls = [json.loads(line) for line in lines]
        return [call for call in calls if "-X" in call] if changing else calls

    def store(self):
        desk = store.Store(self.state / "agent-desk" / "desk.sqlite")
        self.addCleanup(desk.close)
        return desk

    def steps(self):
        return [step["step"] for step in self.store().cutover_steps(REPO)]

    def state_files(self):
        """Each file in the state folder with its contents."""
        folder = self.state / "agent-desk"
        return {path.name: path.read_bytes() for path in sorted(folder.iterdir()) if path.is_file()}

    def workflow_states(self):
        return {Path(one["path"]).name: one["state"] for one in self.github()["workflows"]}


class CutoverTest(CutoverCase):
    def test_a_cutover_takes_every_step_in_order(self):
        out, _ = self.cutover(REPO)
        names = [line[2:] for line in out.splitlines() if line.startswith("- ")]
        self.assertEqual(names[:8], ["check", "import", "reviews", "statuses", "record", "agents", "workflows",
                                     "pulls"])
        self.assertEqual(names[8], "issues")
        desk = self.store()
        self.assertEqual(desk.item(REPO, 7)["state"], "OPEN")
        self.assertIn("Fetched agent/issue-2 for PR #7.", out)
        self.assertIn("agent/issue-2 here holds PR #7's head.", out)
        self.assertIn("PR #7 from agent/issue-2 is review #7.", out)
        self.assertIn("Open issues by state: in-progress 1, open 1, queued 1.", out)
        self.assertEqual(subprocess.run(["git", "-C", str(self.checkout), "rev-parse", "--verify", "--quiet",
                                         "refs/heads/agent/issue-2"], capture_output=True).returncode, 0)
        github = self.github()
        self.assertTrue(github["has_issues"])
        self.assertIn("Left Issues as they are on GitHub.", out)
        self.assertEqual(self.workflow_states(), {**{name: "disabled_manually" for name in WORKFLOWS[:5]},
                                                  "test-hosted.yml": "active"})
        self.assertEqual(github["pulls"]["7"]["state"], "closed")
        [pointer] = github["pulls"]["7"]["comments"]
        self.assertIn("continues as review #7", pointer["body"])
        self.assertTrue(pointer["body"].endswith("<!-- agent-desk -->"))
        self.assertNotIn("@agent", pointer["body"])
        self.assertEqual(self.steps(), ALL_STEPS)
        agents = (self.checkout / "AGENTS.md").read_text()
        self.assertIn(f"- **Tests.** `just test` runs them.\n{cutover.TRACKER}\n\n## Other\n\nText.\n", agents)
        status = subprocess.run(["git", "-C", str(self.checkout), "status", "--porcelain"], capture_output=True,
                                text=True).stdout
        self.assertEqual(status, "")
        self.assertIn("Restarted agent-desk-dispatch.service on the new code.", out)
        self.assertIn("restart agent-desk-dispatch.service", (self.root / "systemctl.log").read_text())
        summary = out[out.index("\nSummary"):]
        self.assertNotIn("Turned Issues off", summary)
        self.assertIn("- org/game: 3 open issues (in-progress 1, open 1, queued 1) and 1 open review", summary)
        self.assertNotIn("runners", summary)
        self.assertIn("agent-desk-web.service isn't running. Turn it on with `agent-desk web on`", summary)

    def test_a_rerun_after_a_failed_close_finishes_without_a_second_pointer(self):
        self.github(fail="pulls/7 -f state=closed")
        _, err = self.cutover(REPO, status=1)
        self.assertIn("the cutover of org/game stopped at pulls: gh: Server Error (HTTP 502)", err)
        github = self.github()
        self.assertEqual(len(github["pulls"]["7"]["comments"]), 1)
        self.assertEqual(github["pulls"]["7"]["state"], "open")
        self.assertTrue(github["has_issues"])
        before = len(self.calls(changing=False))
        self.github(fail=None)
        out, _ = self.cutover(REPO)
        self.assertIn("- import, reviews, statuses, record: done", out)
        rerun = self.calls(changing=False)[before:]
        self.assertFalse(any(call[1] == "--paginate" and call[2] == "graphql" for call in rerun), rerun)
        self.assertFalse(any(call[-1].endswith("/disable") for call in rerun), rerun)
        self.assertIn("off already (disabled_manually)", out)
        github = self.github()
        self.assertEqual(len(github["pulls"]["7"]["comments"]), 1)
        self.assertEqual(github["pulls"]["7"]["state"], "closed")
        self.assertTrue(github["has_issues"])
        self.assertEqual(self.steps(), ALL_STEPS[:-2] + ["pr-closing"] + ALL_STEPS[-2:])
        self.assertEqual((self.checkout / "AGENTS.md").read_text().count("**Tracker.**"), 1)

    def test_a_failure_before_the_record_leaves_github_as_it_was(self):
        self.github(issues=[{"number": 1, "labels": [{"name": "waiting"}]}])
        _, err = self.cutover(REPO, status=1)
        self.assertIn("stopped at statuses: GitHub's labels and the store disagree: #1 is open, not waiting.", err)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.steps(), [])
        self.assertEqual((self.checkout / "AGENTS.md").read_text(), AGENTS)

    def test_an_agent_pr_whose_branch_cant_be_fetched_stops_the_cutover(self):
        self.github(pulls={"7": self.pull("agent/issue-9")})
        _, err = self.cutover(REPO, status=1)
        self.assertIn(f"stopped at reviews: PR #7's branch agent/issue-9 isn't in {self.checkout}, and fetching it "
                      "failed", err)
        self.assertIn("PR #7 from agent/issue-9 has no open review in the store.", err)
        self.assertEqual(self.calls(), [])

    def test_a_dry_run_prints_each_step_and_changes_nothing(self):
        self.hold({"state": "running"})
        before = self.state_files()
        self.assertNotIn("desk.sqlite", before)
        out, _ = self.cutover("--dry-run", REPO)
        self.assertIn("Would refuse now: org/game is on Auto, not Hold.", out)
        self.assertIn("Would import org/game from GitHub", out)
        self.assertIn("Would fetch agent/issue-2 for PR #7.", out)
        self.assertIn("PR #7 from agent/issue-2 becomes review #7.", out)
        self.assertIn("GitHub's open issues would take these states: in-progress 1, open 1, queued 1.", out)
        self.assertIn("Would disable .github/workflows/agent.yml.", out)
        self.assertIn("Would close PR #7 with a pointer to review #7.", out)
        self.assertIn("Left Issues as they are on GitHub.", out)
        self.assertIn("Would turn Issues off.", self.cutover("--dry-run", "--issues-off", REPO)[0])
        self.assertNotIn("Restarted", out)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.state_files(), before)
        self.assertEqual((self.checkout / "AGENTS.md").read_text(), AGENTS)
        self.assertNotIn("restart", (self.root / "systemctl.log").read_text())

    def test_a_dry_run_refuses_an_older_store_and_leaves_it_as_it_was(self):
        path = self.state / "agent-desk" / "desk.sqlite"
        store.Store(path).close()
        older = store.SCHEMA_VERSION - 1
        with sqlite3.connect(path) as db:
            db.execute(f"PRAGMA user_version = {older}")
        db.close()
        before = self.state_files()
        self.assertEqual(sorted(before), ["desk.sqlite", "hold.json"])
        _, err = self.cutover("--dry-run", REPO, status=1)
        self.assertIn(f"{path} has schema {older}, older than this code's {store.SCHEMA_VERSION}, and a dry run "
                      "never migrates it.", err)
        self.assertEqual(self.state_files(), before)
        self.assertEqual(self.calls(changing=False), [])

    def test_the_cutover_refuses_while_a_run_works(self):
        desk = self.store()
        run = desk.request_run(REPO, "agent", "2")
        desk.run_started(run, "agent-desk-agent-org-game-2-1.service")
        _, err = self.cutover(REPO, status=1)
        self.assertIn(f"stopped at check: 1 run still working (agent {run}). Run `agent-desk hold org/game` first",
                      err)
        self.assertEqual(self.calls(changing=False), [])

    def test_the_cutover_refuses_off_hold_or_with_a_merge_queue(self):
        self.hold({"state": "manual"})
        queue = self.state / "agent-desk" / "merges" / "org__game.json"
        queue.parent.mkdir(parents=True)
        queue.write_text(json.dumps({"queue": [{"pr": 7}]}))
        _, err = self.cutover(REPO, status=1)
        self.assertIn("org/game is on Manual, not Hold; the merge queue holds #7.", err)
        self.assertEqual(self.calls(changing=False), [])

    def test_a_project_without_a_host_cuts_over_without_github(self):
        self.configure({"host": "none"})
        self.git("-C", str(self.checkout), "commit", "--quiet", "-am", "host")
        out, _ = self.cutover(REPO)
        self.assertEqual(self.calls(changing=False), [])
        self.assertEqual(self.steps(), ["recorded"])
        self.assertIn("- workflows, pulls, issues: skipped, since the project's host is none", out)
        self.assertIn("**Tracker.**", (self.checkout / "AGENTS.md").read_text())

    def test_a_section_with_uncommitted_changes_leaves_the_line_for_you(self):
        (self.checkout / "AGENTS.md").write_text(AGENTS + "More.\n")
        out, _ = self.cutover(REPO)
        self.assertNotIn("**Tracker.**", (self.checkout / "AGENTS.md").read_text())
        self.assertIn("Add these lines to the Agent desk section of", out[out.index("Left for you"):])

    def assert_line_left_for_you(self, out, why):
        self.assertIn(f"{why}, so the line is left for you.", out)
        self.assertIn("Add these lines to the Agent desk section of", out[out.index("Left for you"):])
        self.assertEqual((self.checkout / "AGENTS.md").read_text(), AGENTS)
        self.assertEqual(self.git("-C", str(self.checkout), "log", "--format=%s", "main"), "start\n")

    def test_a_checkout_off_main_leaves_the_line_for_you(self):
        self.git("-C", str(self.checkout), "switch", "--quiet", "-c", "topic")
        out, _ = self.cutover(REPO)
        self.assert_line_left_for_you(out, f"{self.checkout} is on topic, not main")
        self.assertEqual(self.head("topic"), self.head("main"))
        self.git("-C", str(self.checkout), "switch", "--quiet", "--detach", "main")
        out, _ = self.cutover(REPO)
        self.assert_line_left_for_you(out, f"{self.checkout} is on a detached HEAD, not main")

    def test_a_checkout_with_a_staged_change_leaves_the_line_for_you(self):
        (self.checkout / "notes.txt").write_text("Staged.\n")
        self.git("-C", str(self.checkout), "add", "notes.txt")
        out, _ = self.cutover(REPO)
        self.assert_line_left_for_you(out, f"{self.checkout} has staged changes")
        self.assertEqual(self.git("-C", str(self.checkout), "diff", "--cached", "--name-only"), "notes.txt\n")

    def test_a_merge_rebase_or_cherry_pick_in_progress_leaves_the_line_for_you(self):
        git_dir = self.checkout / ".git"
        for marker, operation in [("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"),
                                  ("rebase-apply", "a rebase"), ("CHERRY_PICK_HEAD", "a cherry-pick"),
                                  ("REVERT_HEAD", "a revert")]:
            with self.subTest(marker):
                path = git_dir / marker
                if marker.startswith("rebase"):
                    path.mkdir()
                else:
                    path.write_text(self.head("main") + "\n")
                out, _ = self.cutover(REPO)
                self.assert_line_left_for_you(out, f"{self.checkout} has {operation} in progress")
                path.rmdir() if path.is_dir() else path.unlink()

    def test_the_tracker_line_wraps_as_agent_desks_own_agents_md_does(self):
        lines = cutover.TRACKER.split("\n")
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line) <= 120 for line in lines), lines)
        self.assertTrue(all(line.startswith("  ") and not line.startswith("   ") for line in lines[1:]), lines)

    # The public repository's releases leave AGENTS.md out, as scripts/publish-exclude lists.
    @unittest.skipUnless((ROOT / "AGENTS.md").exists(), "the checkout has no AGENTS.md")
    def test_the_tracker_line_matches_agent_desks_own_agents_md(self):
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn(f"\n{cutover.TRACKER}\n", agents)

    def test_a_failed_commit_leaves_the_wrapped_line_for_you_and_goes_on(self):
        hook = self.checkout / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
        hook.chmod(0o755)
        out, _ = self.cutover(REPO)
        self.assertIn("git couldn't commit the line added to", out)
        self.assertIn("so the file is back as it was and the line is left for you: refused", out)
        left = out[out.index("Left for you"):]
        indented = "\n".join("  " + line for line in cutover.TRACKER.split("\n"))
        self.assertIn(f"Add these lines to the Agent desk section of {self.checkout / 'AGENTS.md'}:\n{indented}\n",
                      left)
        self.assertEqual(self.steps(), ALL_STEPS)
        self.assertEqual((self.checkout / "AGENTS.md").read_text(), AGENTS)
        self.assertEqual(self.git("-C", str(self.checkout), "status", "--porcelain"), "")
        hook.unlink()
        out, _ = self.cutover(REPO)
        self.assertIn("Added the line naming `agent-desk issue`", out)
        self.assertEqual(self.git("-C", str(self.checkout), "log", "-1", "--format=%s"),
                         "Name agent-desk issue and review as the tracker's commands\n")
        self.assertEqual(self.git("-C", str(self.checkout), "status", "--porcelain"), "")

    def test_a_persons_a_bots_and_a_forks_pr_stay_open_on_github(self):
        self.github(pulls={"7": self.pull("agent/issue-2"), "8": self.pull("fix-typo"),
                           "9": self.pull("dependabot/npm_and_yarn/left-pad-2.0.0"),
                           "10": self.pull("agent/issue-2", **{"from": "someone/game"})})
        out, _ = self.cutover(REPO)
        pulls = self.github()["pulls"]
        self.assertEqual({number: pull["state"] for number, pull in pulls.items()},
                         {"7": "closed", "8": "open", "9": "open", "10": "open"})
        self.assertEqual([len(pull["comments"]) for pull in pulls.values()], [1, 0, 0, 0])
        self.assertEqual(self.steps().count("pr-closed"), 1)
        left = out[out.index("Left for you"):]
        self.assertIn("PR #8 of org/game, from fix-typo, stays open on GitHub, since it comes from a branch outside "
                      "agent/.", left)
        self.assertIn("PR #9 of org/game, from dependabot/npm_and_yarn/left-pad-2.0.0, stays open", left)
        self.assertIn("PR #10 of org/game, from agent/issue-2, stays open on GitHub, since its branch sits in the "
                      "fork someone/game.", left)

    def test_an_agent_pr_whose_review_closed_before_a_rerun_stays_open(self):
        self.github(fail="pulls/7 -f state=closed")
        self.cutover(REPO, status=1)
        desk = self.store()
        desk.set_state(REPO, 7, "closed")
        self.github(fail=None)
        out, _ = self.cutover(REPO)
        self.assertEqual(self.github()["pulls"]["7"]["state"], "open")
        self.assertIn("PR #7 of org/game, from agent/issue-2, stays open on GitHub, since the store has no open "
                      "review of it.", out)

    def test_a_local_branch_behind_its_pr_stops_the_cutover(self):
        self.git("-C", str(self.checkout), "branch", "agent/issue-2", "main")
        self.git("-C", str(self.checkout), "commit", "--quiet", "--allow-empty", "-m", "on GitHub")
        ahead = self.head("HEAD")
        self.git("-C", str(self.checkout), "push", "--quiet", "origin", "HEAD:agent/issue-2")
        self.git("-C", str(self.checkout), "reset", "--quiet", "--hard", "HEAD~1")
        self.github(pulls={"7": self.pull("agent/issue-2", sha=ahead)})
        _, err = self.cutover(REPO, status=1)
        self.assertIn(f"stopped at reviews: PR #7's head on GitHub, {ahead[:12]}, has commits agent/issue-2 here "
                      f"({self.head('main')[:12]}) lacks. Run `git -C {self.checkout} fetch origin "
                      "agent/issue-2:agent/issue-2`", err)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.steps(), [])
        worktree = self.root / "game-issue-2"
        self.git("-C", str(self.checkout), "worktree", "add", "--quiet", str(worktree), "agent/issue-2")
        _, err = self.cutover(REPO, status=1)
        self.assertIn(f"Git can't fetch into a branch that is checked out, and agent/issue-2 is checked out in "
                      f"{worktree}: run `git -C {worktree} pull --ff-only origin agent/issue-2` there", err)
        self.git("-C", str(worktree), "pull", "--quiet", "--ff-only", "origin", "agent/issue-2")
        out, _ = self.cutover(REPO)
        self.assertIn("agent/issue-2 here holds PR #7's head.", out)

    def test_a_local_branch_ahead_of_its_pr_passes(self):
        self.git("-C", str(self.checkout), "branch", "agent/issue-2", "main")
        self.git("-C", str(self.checkout), "checkout", "--quiet", "agent/issue-2")
        self.git("-C", str(self.checkout), "commit", "--quiet", "--allow-empty", "-m", "not pushed yet")
        self.git("-C", str(self.checkout), "checkout", "--quiet", "main")
        out, _ = self.cutover(REPO)
        self.assertIn("agent/issue-2 here holds PR #7's head and more.", out)

    def test_a_kill_after_a_disable_is_recorded_by_the_rerun(self):
        self.github(kill="workflows/1/disable")
        self.cutover(REPO, status=-9)
        self.assertEqual(self.workflow_states()["agent.yml"], "disabled_manually")
        self.assertEqual(self.steps(), ["recorded", "workflow-disabling"])
        out, _ = self.cutover(REPO)
        self.assertIn("agent.yml was disabled by a cutover that stopped before recording it; recorded it.", out)
        self.assertEqual(sorted(self.steps()), sorted(ALL_STEPS))
        self.assertIn("- org/game: Disabled .github/workflows/agent.yml", out[out.index("\nSummary"):])

    def test_a_kill_after_a_close_is_recorded_by_the_rerun(self):
        self.github(kill="pulls/7 -f state=closed")
        self.cutover(REPO, status=-9)
        self.assertEqual(self.github()["pulls"]["7"]["state"], "closed")
        self.assertEqual(self.steps()[-1], "pr-closing")
        out, _ = self.cutover(REPO)
        self.assertIn("PR #7 was closed by a cutover that stopped before recording it; recorded it.", out)
        self.assertEqual(self.steps(), ALL_STEPS)
        self.assertTrue(self.github()["has_issues"])

    def test_the_checkout_comes_only_from_the_projects_file(self):
        other = self.root / "other"
        self.git("clone", "--quiet", str(self.origin), str(other))
        (self.config / "agent-desk" / "projects").write_text(f"org/other {other}\n")
        done = subprocess.run([sys.executable, "-B", str(ROOT / "lib" / "cutover.py"), REPO], env=self.env,
                              cwd=self.checkout, capture_output=True, text=True)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(f"names no main checkout of {REPO}", done.stderr)
        self.assertEqual(self.calls(changing=False), [])

    def test_a_checkout_of_another_project_stops_the_cutover(self):
        other = self.root / "other"
        self.git("clone", "--quiet", str(self.origin), str(other))
        self.git("-C", str(other), "remote", "set-url", "origin", "git@github.com:org/other.git")
        (self.config / "agent-desk" / "projects").write_text(f"{REPO} {other}\n")
        _, err = self.cutover(REPO, status=1)
        self.assertIn(f"stopped at check: the origin of {other} is git@github.com:org/other.git, which isn't "
                      f"{REPO}.", err)
        self.assertEqual(self.calls(changing=False), [])
        self.assertEqual(self.git("-C", str(other), "status", "--porcelain"), "")
        _, err = self.cutover("--undo", REPO, status=1)
        self.assertIn("stopped at check: the origin of", err)

    def test_all_cuts_over_every_project_in_the_projects_file(self):
        out, _ = self.cutover("--all")
        self.assertIn(f"Cutting over org/game in {self.checkout}", out)
        self.assertTrue(self.github()["has_issues"])

    def test_issues_off_turns_issues_off_a_rerun_leaves_them_off_and_the_undo_turns_them_back_on(self):
        out, _ = self.cutover("--issues-off", REPO)
        self.assertFalse(self.github()["has_issues"])
        self.assertEqual(self.steps(), ALL_STEPS + ISSUES_STEPS)
        self.assertIn("- org/game: Turned Issues off", out[out.index("\nSummary"):])
        calls = len(self.calls())
        out, _ = self.cutover(REPO)
        self.assertIn("Left Issues as they are on GitHub.", out)
        self.assertFalse(self.github()["has_issues"])
        self.assertEqual(len(self.calls()), calls)
        out, _ = self.cutover("--undo", REPO)
        self.assertIn("Turned Issues back on.", out)
        self.assertTrue(self.github()["has_issues"])

    def test_the_undo_after_a_kill_after_issues_went_off_turns_them_back_on(self):
        self.github(kill="has_issues=false")
        self.cutover("--issues-off", REPO, status=-9)
        self.assertFalse(self.github()["has_issues"])
        self.assertEqual(self.steps()[-1], "issues-turning-off")
        out, _ = self.cutover(REPO)
        self.assertIn("Left Issues as they are on GitHub.", out)
        self.assertEqual(self.steps()[-1], "issues-turning-off")
        out, _ = self.cutover("--undo", REPO)
        self.assertIn("Turned Issues back on.", out)
        self.assertTrue(self.github()["has_issues"])


class UndoTest(CutoverCase):
    def test_the_undo_brings_github_back_and_lists_what_was_filed_locally(self):
        self.github(workflows=[{"id": 1, "path": ".github/workflows/agent.yml", "state": "active"},
                               {"id": 2, "path": ".github/workflows/fix.yml", "state": "disabled_manually"}])
        self.cutover(REPO)
        recorded = self.store().cutover_steps(REPO)[0]["at"]
        number = self.store().create(REPO, "issue", "Filed after the cutover")
        out, _ = self.cutover("--rollback", REPO)
        github = self.github()
        self.assertTrue(github["has_issues"])
        self.assertEqual(self.workflow_states(), {"agent.yml": "active", "fix.yml": "disabled_manually"})
        self.assertEqual(github["pulls"]["7"]["state"], "open")
        self.assertIn(f"#{number} issue, open: Filed after the cutover", out)
        self.assertIn(f"`agent-desk issue list --state all --since {recorded}`", out)
        self.assertIn("note the new number on any item a commit cites by its local one", out)
        self.assertIn("Check out its `pre-local-desk` tag", out)
        self.assertEqual(self.steps(), [])
        listed = subprocess.run([sys.executable, "-B", str(ROOT / "lib" / "store_cli.py"), "issue", "list", "--repo",
                                 REPO, "--state", "all", "--since", recorded], env=self.env, capture_output=True,
                                text=True, check=True).stdout
        self.assertEqual([line.split("\t")[0] for line in listed.splitlines()], [str(number)])

    def test_the_undo_after_a_kill_after_a_disable_enables_it_and_leaves_issues_on(self):
        self.github(kill="workflows/1/disable")
        self.cutover(REPO, status=-9)
        out, _ = self.cutover("--undo", REPO)
        self.assertEqual(set(self.workflow_states().values()), {"active"})
        self.assertIn("Enabled .github/workflows/agent.yml.", out)
        self.assertIn("The cutover didn't turn Issues off, so they stay as they are.", out)
        self.assertTrue(self.github()["has_issues"])
        self.assertNotIn(["api", "-X", "PATCH", f"repos/{REPO}", "-F", "has_issues=true"], self.calls())
        self.assertEqual(self.steps(), [])

    def test_the_undo_after_a_kill_after_a_close_reopens_it(self):
        self.github(kill="pulls/7 -f state=closed")
        self.cutover(REPO, status=-9)
        out, _ = self.cutover("--undo", REPO)
        self.assertEqual(self.github()["pulls"]["7"]["state"], "open")
        self.assertIn("Reopened PR #7.", out)
        self.assertEqual(set(self.workflow_states().values()), {"active"})
        self.assertTrue(self.github()["has_issues"])

    def test_the_undo_leaves_issues_off_that_were_off_before_the_cutover(self):
        self.github(has_issues=False)
        out, _ = self.cutover("--issues-off", REPO)
        self.assertIn("Issues are off already.", out)
        out, _ = self.cutover("--undo", REPO)
        self.assertIn("The cutover didn't turn Issues off, so they stay as they are.", out)
        self.assertFalse(self.github()["has_issues"])

    def test_the_undo_needs_a_cutover(self):
        _, err = self.cutover("--undo", REPO, status=1)
        self.assertIn("org/game has no cutover to undo.", err)

    def test_a_cutover_after_an_undo_starts_over(self):
        self.cutover(REPO)
        self.cutover("--undo", REPO)
        out, _ = self.cutover(REPO)
        self.assertIn("- import\n", out)
        self.assertEqual(self.steps(), ALL_STEPS)


class UsageTest(CutoverCase):
    def assert_one_prefix(self, err):
        self.assertTrue(err.startswith("usage: agent-desk cutover [--dry-run]"), err)
        self.assertEqual(err.count("usage:"), 1, err)
        self.assertIn("\n       agent-desk cutover --undo [--dry-run] <owner/repo>", err)

    def test_an_argument_argparse_rejects_prints_one_usage_prefix(self):
        _, err = self.cutover("--bogus", status=2)
        self.assert_one_prefix(err)
        self.assertIn("unrecognized arguments: --bogus", err)

    def test_a_bad_combination_of_flags_prints_one_usage_prefix(self):
        for args in (("--all", REPO), ("--undo", "--issues-off", REPO)):
            with self.subTest(args=args):
                _, err = self.cutover(*args, status=2)
                self.assert_one_prefix(err)


if __name__ == "__main__":
    unittest.main()
