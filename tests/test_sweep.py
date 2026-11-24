"""Tests for lib/sweep.sh, which removes the agent worktrees and local agent/* branches no run, review, or person still
needs. Each runs the script against a temporary store and a real origin and main checkout."""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work_scripts import REPO, Desk, git  # noqa: E402


class SweepCase(Desk):
    def setUp(self):
        super().setUp()
        # The dispatcher starts the sweep in a unit of its own, outside any run.
        self.env.update({"AGENT_DESK_RUN": "", "AGENT_DESK_UNIT": ""})

    def sweep(self):
        result = self.run_lib("sweep.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def branch(self, slug, push=True):
        """A worktree on agent/<slug> from main with one commit, pushed to origin unless push is False."""
        dest = self.worktree(slug)
        git("worktree", "add", "-q", "-b", f"agent/{slug}", str(dest), "main", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", f"work on {slug}", cwd=dest)
        if push:
            git("push", "-q", "-u", "origin", f"agent/{slug}", cwd=dest)
        return dest

    def has_local(self, branch):
        return bool(git("branch", "--list", branch, cwd=self.main))


class WorktreeTest(SweepCase):
    def test_a_finished_issues_worktree_and_branch_go(self):
        issue = self.issue(status="closed")
        dest = self.branch(f"issue-{issue}")

        out = self.sweep()
        self.assertIn(f"Removed {dest}.", out)
        self.assertIn(f"Deleted local agent/issue-{issue}.", out)
        self.assertFalse(dest.exists())
        self.assertFalse(self.has_local(f"agent/issue-{issue}"))

    def test_an_open_issue_whose_review_closed_loses_its_worktree(self):
        issue = self.issue(status="open")
        dest = self.branch(f"issue-{issue}")
        review = self.store.create(REPO, "review", "Change", branch=f"agent/issue-{issue}", draft=True)
        self.store.set_state(REPO, review, "closed", "closed")

        self.assertIn(f"Removed {dest}.", self.sweep())
        self.assertFalse(dest.exists())

    def test_a_worktree_whose_run_is_queued_stays(self):
        issue = self.issue(status="queued")
        dest = self.branch(f"issue-{issue}")
        self.store.request_run(REPO, "agent", str(issue))

        out = self.sweep()
        self.assertIn(f"Kept {dest}: a run of #{issue} is running or queued.", out)
        self.assertTrue(dest.is_dir())
        self.assertTrue(self.has_local(f"agent/issue-{issue}"))

    def test_a_worktree_of_an_issue_in_progress_waiting_or_on_hold_stays(self):
        kept = {status: self.issue(status=status) for status in ("in-progress", "waiting", "on-hold")}
        dests = {status: self.branch(f"issue-{issue}") for status, issue in kept.items()}

        out = self.sweep()
        for status, issue in kept.items():
            self.assertIn(f"Kept {dests[status]}: #{issue} is {status}.", out)
            self.assertTrue(dests[status].is_dir())

    def test_a_worktree_with_an_open_review_stays(self):
        issue = self.issue(status="open")
        dest = self.branch(f"issue-{issue}")
        review = self.store.create(REPO, "review", "Change", branch=f"agent/issue-{issue}", draft=True)

        self.assertIn(f"Kept {dest}: its review #{review} is open.", self.sweep())
        self.assertTrue(dest.is_dir())

    def test_a_worktree_with_a_process_in_it_stays_for_a_later_sweep(self):
        issue = self.issue(status="open")
        dest = self.branch(f"issue-{issue}")
        worker = subprocess.Popen(["sleep", "30"], cwd=dest)
        self.addCleanup(worker.wait)
        self.addCleanup(worker.kill)

        out = self.sweep()
        self.assertIn(f"Kept {dest}: a process is working in it: {worker.pid} sleep 30", out)
        self.assertTrue(dest.is_dir())
        self.assertTrue(self.has_local(f"agent/issue-{issue}"))

        worker.kill()
        worker.wait()
        self.assertIn(f"Removed {dest}.", self.sweep())
        self.assertFalse(dest.exists())

    def test_a_chore_worktree_goes_once_no_run_of_its_chore_waits(self):
        dest = self.branch("chore-audit-20261111-1200")
        run = self.store.request_run(REPO, "chore", "audit")

        self.assertIn(f"Kept {dest}: a run of the audit chore is running or queued.", self.sweep())
        self.store.run_ended(run, "cancelled")
        self.assertIn(f"Removed {dest}.", self.sweep())
        self.assertFalse(dest.exists())

    def test_a_worktree_made_by_hand_stays(self):
        dest = self.branch("sound-try")

        self.assertIn(f"Kept {dest}: it isn't an issue's or a chore's worktree.", self.sweep())
        self.assertTrue(dest.is_dir())
        self.assertTrue(self.has_local("agent/sound-try"))

    def test_a_worktree_with_unpushed_commits_goes_and_its_branch_stays(self):
        issue = self.issue(status="closed")
        dest = self.branch(f"issue-{issue}", push=False)

        out = self.sweep()
        self.assertIn(f"Removed {dest}.", out)
        self.assertIn(f"Kept local agent/issue-{issue}: it has commits that aren't on origin or main.", out)
        self.assertFalse(dest.exists())
        self.assertTrue(self.has_local(f"agent/issue-{issue}"))


class BranchTest(SweepCase):
    def without_worktree(self, slug, push=True):
        dest = self.branch(slug, push=push)
        git("worktree", "remove", str(dest), cwd=self.main)

    def test_a_branch_whose_worktree_went_by_hand_goes_once_origin_holds_it(self):
        self.without_worktree("sound-try")

        self.assertIn("Deleted local agent/sound-try.", self.sweep())
        self.assertFalse(self.has_local("agent/sound-try"))

    def test_a_branch_main_holds_goes(self):
        self.without_worktree("visibility-sweep", push=False)
        git("merge", "-q", "--ff-only", "agent/visibility-sweep", cwd=self.main)

        self.assertIn("Deleted local agent/visibility-sweep.", self.sweep())
        self.assertFalse(self.has_local("agent/visibility-sweep"))

    def test_a_branch_with_unpushed_commits_stays(self):
        self.without_worktree("night-hike", push=False)

        self.assertIn("Kept local agent/night-hike: it has commits that aren't on origin or main.", self.sweep())
        self.assertTrue(self.has_local("agent/night-hike"))

    def test_a_branch_with_an_open_review_stays(self):
        self.without_worktree("serial-groups")
        review = self.store.create(REPO, "review", "Change", branch="agent/serial-groups", draft=True)

        self.assertIn(f"Kept local agent/serial-groups: its review #{review} is open.", self.sweep())
        self.assertTrue(self.has_local("agent/serial-groups"))

    def test_the_branch_of_an_issue_in_progress_stays(self):
        issue = self.issue(status="in-progress")
        self.without_worktree(f"issue-{issue}")

        self.assertIn(f"Kept local agent/issue-{issue}: #{issue} is in-progress.", self.sweep())
        self.assertTrue(self.has_local(f"agent/issue-{issue}"))

    def test_each_worktree_and_branch_gets_one_line(self):
        issue = self.issue(status="closed")
        self.branch(f"issue-{issue}", push=False)

        out = self.sweep()
        self.assertEqual(out.count(f"desk-issue-{issue}"), 1, out)
        self.assertEqual(out.count(f"agent/issue-{issue}"), 1, out)


if __name__ == "__main__":
    unittest.main()
