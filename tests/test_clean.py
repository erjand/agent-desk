"""Tests for lib/clean.sh, which cleans up after an agent review merges or closes. Each runs the script against a
temporary store, a real origin and main checkout, a stand-in for gh that serves lib/serial.sh, and a stand-in for
systemctl that logs the units the cleanup stops."""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work_scripts import LIB, REPO, Desk, git  # noqa: E402

SIGNED = "\n\n<sub>Posted by agent-desk</sub>"
UNIT = "agent-desk-agent-o-r-5-2.service"


class CleanCase(Desk):
    def setUp(self):
        super().setUp()
        self.systemctl_log = self.root / "systemctl.log"
        fake = self.root / "fakes" / "systemctl"
        fake.write_text(f'#!/bin/bash\necho "$*" >>"{self.systemctl_log}"\n')
        fake.chmod(0o755)
        # The dispatcher starts the cleanup in a unit of its own, outside any run.
        self.env.update({"AGENT_DESK_RUN": "", "AGENT_DESK_UNIT": ""})
        self.other = self.root / "other"
        git("clone", "-q", str(self.root / "origin.git"), str(self.other), cwd=self.root)

    def clean(self, branch, merged):
        result = self.run_lib("clean.sh", branch, "true" if merged else "false")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def branch(self, slug, base="main"):
        """A worktree on agent/<slug> from origin/<base>, with one commit pushed to origin."""
        git("fetch", "-q", "origin", cwd=self.main)
        dest = self.worktree(slug)
        git("worktree", "add", "-q", "-b", f"agent/{slug}", str(dest), f"origin/{base}", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", f"work on {slug}", cwd=dest)
        git("push", "-q", "-u", "origin", f"agent/{slug}", cwd=dest)
        return dest

    def open_review(self, slug, base="main", fixes=None):
        return self.store.create(REPO, "review", f"Work on {slug}", branch=f"agent/{slug}", base=base, draft=True,
                                 fixes=[fixes] if fixes else [])

    def land(self, review, slug, into="main"):
        """Merges agent/<slug> into <into> on origin from another clone, and records the merge in the store."""
        git("fetch", "-q", "origin", cwd=self.other)
        git("checkout", "-q", "-B", into, f"origin/{into}", cwd=self.other)
        git("merge", "-q", "--no-ff", "-m", f"Merge agent/{slug}", f"origin/agent/{slug}", cwd=self.other)
        git("push", "-q", "origin", into, cwd=self.other)
        sha = git("rev-parse", "HEAD", cwd=self.other)
        self.store.merge(REPO, review, sha)
        return sha

    def on_origin(self, branch):
        return bool(git("ls-remote", "--heads", "origin", branch, cwd=self.main))

    def has_local(self, branch):
        return bool(git("branch", "--list", branch, cwd=self.main))


class MergedTest(CleanCase):
    def test_a_merge_into_main_closes_the_issue_with_a_note_and_removes_the_branch_and_worktree(self):
        issue = self.issue()
        slug = f"issue-{issue}"
        dest = self.branch(slug)
        review = self.open_review(slug, fixes=issue)
        before = git("rev-parse", "HEAD", cwd=self.main)
        self.land(review, slug)

        self.clean(f"agent/{slug}", merged=True)
        self.assertEqual(self.item(issue)["status"], "closed")
        self.assertEqual(self.notes(issue), [("desk", f"Landed on main with #{review}.{SIGNED}")])
        self.assertFalse(dest.exists())
        self.assertFalse(self.has_local(f"agent/{slug}"))
        self.assertFalse(self.on_origin(f"agent/{slug}"))
        # The merge queue moves main, so the cleanup leaves the main checkout where it was.
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.main), before)
        self.assert_no_github_writes()

    def test_a_merge_into_main_closes_an_issue_the_review_does_not_fix_and_the_issues_stacked_on_it(self):
        issue = self.issue()
        slug = f"issue-{issue}"
        self.branch(slug)
        review = self.open_review(slug)
        # A stacked review merged into the branch earlier and kept its issue in progress.
        stacked = self.issue()
        stacked_slug = f"issue-{stacked}"
        self.branch(stacked_slug, base=f"agent/{slug}")
        stacked_review = self.open_review(stacked_slug, base=f"agent/{slug}", fixes=stacked)
        self.land(stacked_review, stacked_slug, into=f"agent/{slug}")
        self.assertIn("Kept", self.clean(f"agent/{stacked_slug}", merged=True))
        self.assertEqual(self.item(stacked)["status"], "in-progress")
        self.land(review, slug)

        self.clean(f"agent/{slug}", merged=True)
        self.assertEqual(self.item(issue)["status"], "closed")
        self.assertEqual(self.notes(issue), [("desk", f"Landed on main with #{review}.{SIGNED}")])
        self.assertEqual(self.item(stacked)["status"], "closed")
        self.assertEqual(self.notes(stacked),
                         [("desk", f"Landed on main with #{review}, which holds #{stacked_review}.{SIGNED}")])

    def test_a_merge_into_another_base_leaves_the_issue_open_and_main_where_it_was(self):
        git("push", "-q", "origin", "main:refs/heads/local-desk", cwd=self.main)
        issue = self.issue(labels=["base:local-desk"])
        slug = f"issue-{issue}"
        dest = self.branch(slug, base="local-desk")
        review = self.open_review(slug, base="local-desk", fixes=issue)
        before = git("rev-parse", "HEAD", cwd=self.main)
        self.land(review, slug, into="local-desk")

        self.clean(f"agent/{slug}", merged=True)
        self.assertEqual(self.item(issue)["state"], "OPEN")
        self.assertEqual(self.item(issue)["status"], "open")
        self.assertEqual(self.notes(issue), [])
        self.assertFalse(dest.exists())
        self.assertFalse(self.on_origin(f"agent/{slug}"))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.main), before)


    def test_a_merge_whose_issue_and_review_the_store_lacks_still_cleans_up(self):
        dest = self.branch("issue-40")
        git("push", "-q", "origin", "agent/issue-40:main", cwd=dest)

        self.clean("agent/issue-40", merged=True)
        self.assertFalse(dest.exists())
        self.assertFalse(self.on_origin("agent/issue-40"))


class ClosedTest(CleanCase):
    def test_a_review_closed_without_merging_frees_the_issue_and_keeps_the_branch_on_origin(self):
        issue = self.issue()
        slug = f"issue-{issue}"
        dest = self.branch(slug)
        review = self.open_review(slug, fixes=issue)
        self.store.set_state(REPO, review, "closed", "closed")

        self.clean(f"agent/{slug}", merged=False)
        self.assertEqual(self.item(issue)["status"], "open")
        self.assertEqual(self.notes(issue), [])
        self.assertFalse(dest.exists())
        self.assertFalse(self.has_local(f"agent/{slug}"))
        self.assertTrue(self.on_origin(f"agent/{slug}"))

    def test_a_worktree_in_use_stays(self):
        issue = self.issue()
        slug = f"issue-{issue}"
        dest = self.branch(slug)
        self.store.set_state(REPO, self.open_review(slug), "closed", "closed")
        worker = subprocess.Popen(["sleep", "30"], cwd=dest)
        self.addCleanup(worker.wait)
        self.addCleanup(worker.kill)

        out = self.clean(f"agent/{slug}", merged=False)
        self.assertIn(f"Kept {dest}: a process is working in it.", out)
        self.assertTrue(dest.is_dir())
        self.assertTrue(self.has_local(f"agent/{slug}"))
        self.assertEqual(self.item(issue)["status"], "open")

    def test_a_running_agent_is_stopped_after_a_note_its_release_reads(self):
        issue = self.issue()
        slug = f"issue-{issue}"
        self.branch(slug)
        review = self.open_review(slug)
        run = self.store.request_run(REPO, "agent", str(issue))
        self.store.run_started(run, UNIT)
        self.store.set_state(REPO, review, "closed", "closed")

        out = self.clean(f"agent/{slug}", merged=False)
        self.assertIn(f"Stopping agent run {UNIT}.", out)
        self.assertIn(f"--user stop --no-block {UNIT}", self.systemctl_log.read_text())
        self.assertEqual(self.notes(issue), [
            ("desk", f"The agent stopped: review #{review} closed while it worked "
                     f"(log: `journalctl --user -u {UNIT}`).{SIGNED}")])
        # The stopped unit's exit handler releases the issue, which finds the outcome reported.
        release = subprocess.run([sys.executable, "-B", str(LIB / "release.py"), str(issue), "cancelled"],
                                 cwd=self.main, env={**self.env, "AGENT_DESK_UNIT": UNIT, "AGENT_DESK_RUN": str(run)},
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(release.returncode, 0, release.stderr)
        self.assertIn("nothing to release", release.stdout)
        self.assertEqual(len(self.notes(issue)), 1)

    def unpushed(self, slug):
        """agent/<slug> with one commit only this machine holds, its worktree removed by hand."""
        dest = self.worktree(slug)
        git("worktree", "add", "-q", "-b", f"agent/{slug}", str(dest), "origin/main", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", f"work on {slug}", cwd=dest)
        git("worktree", "remove", str(dest), cwd=self.main)
        return git("rev-parse", f"agent/{slug}", cwd=self.main)

    def test_a_branch_never_pushed_keeps_its_commits_when_its_worktree_is_gone(self):
        issue = self.issue()
        slug = f"issue-{issue}"
        head = self.unpushed(slug)
        self.store.set_state(REPO, self.open_review(slug), "closed", "not-planned")

        out = self.clean(f"agent/{slug}", merged=False)
        self.assertIn(f"Kept local agent/{slug}: it has commits that aren't on origin or main.", out)
        self.assertEqual(git("rev-parse", f"agent/{slug}", cwd=self.main), head)
        self.assertEqual(self.item(issue)["status"], "open")

    def test_a_branch_never_pushed_goes_once_its_base_holds_its_commits(self):
        issue = self.issue()
        slug = f"issue-{issue}"
        self.unpushed(slug)
        review = self.open_review(slug, fixes=issue)
        # The merge happens on this machine alone, into the local main.
        git("merge", "-q", "--ff-only", f"agent/{slug}", cwd=self.main)
        self.store.merge(REPO, review, git("rev-parse", "HEAD", cwd=self.main))

        out = self.clean(f"agent/{slug}", merged=True)
        self.assertIn(f"Deleted local agent/{slug}.", out)
        self.assertFalse(self.has_local(f"agent/{slug}"))

    def test_a_merge_into_a_local_base_cleans_up_with_origin_out_of_reach(self):
        git("branch", "local-desk", "main", cwd=self.main)
        issue = self.issue(labels=["base:local-desk"])
        slug = f"issue-{issue}"
        self.unpushed(slug)
        review = self.open_review(slug, base="local-desk", fixes=issue)
        # The merge queue moved the local base, and origin has never seen the branch or its commits.
        git("branch", "-f", "local-desk", f"agent/{slug}", cwd=self.main)
        self.store.merge(REPO, review, git("rev-parse", "local-desk", cwd=self.main))
        self.lose_origin()
        # The deletion on origin retries; the waits between its tries pass at once.
        sleep = self.root / "fakes" / "sleep"
        sleep.write_text("#!/bin/bash\n")
        sleep.chmod(0o755)

        out = self.clean(f"agent/{slug}", merged=True)
        self.assertIn(f"Deleted local agent/{slug}.", out)
        self.assertIn(f"Couldn't delete agent/{slug} on origin.", out)
        self.assertFalse(self.has_local(f"agent/{slug}"))
        self.assertEqual(self.item(issue)["status"], "open")

    def test_a_branch_outside_agent_is_refused(self):
        result = self.run_lib("clean.sh", "feature/menu", "true")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Only agent/* branches are cleaned", result.stderr)


class SourceTest(unittest.TestCase):
    def test_the_script_calls_no_gh(self):
        source = (LIB / "clean.sh").read_text()
        self.assertNotRegex(source, r"(?m)^[^#]*\bgh\s")


if __name__ == "__main__":
    unittest.main()
