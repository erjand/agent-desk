"""Tests for lib/fix.sh: each runs against a temporary store, a main checkout in a temporary folder whose origin is out
of reach, since the fix reads and moves only local branches, and a stand-in for claude that can act as the agent. A
failing head's checks are rows in the store with their logs."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work_scripts import REPO, SIGNED, UNIT, Desk, git  # noqa: E402

BRANCH = "agent/chore-tidy-20261111-1200"
LOG = f" (log: `journalctl --user -u {UNIT}`)"
# A failed local check's log, as lib/checks.py writes it.
FAILED_LOG = "== Run test\nFAILED test_parse - AssertionError: 1 != 2\nThe check failed.\n"
# The agent resolves the conflicts in its own favor, as a stand-in for reading both sides, and commits the merge.
RESOLVE = "git checkout -q --ours . && git add -A && git commit -q --no-edit"


class FixTest(Desk):
    def setUp(self):
        super().setUp()
        (self.main / ".agent-desk.json").write_text(json.dumps({"resources": {
            "needGB": {"fix": 0}, "reserveGB": 0, "maxMemoryPressure": 100}}))
        self.dest = self.worktree(BRANCH.removeprefix("agent/"))

    def commit(self, text, message):
        (self.main / "notes.txt").write_text(text)
        git("add", "notes.txt", cwd=self.main)
        git("commit", "-q", "-m", message, cwd=self.main)

    def review_of(self, conflicting=True, branch=BRANCH):
        """A review of a local branch that changed notes.txt, and a local main that changed it too, or another file, with
        origin out of reach."""
        self.commit("first\n", "notes")
        git("checkout", "-q", "-b", branch, cwd=self.main)
        self.commit("from the branch\n", "branch side")
        git("checkout", "-q", "main", cwd=self.main)
        if conflicting:
            self.commit("from main\n", "main side")
        else:
            (self.main / "other.txt").write_text("more\n")
            git("add", "other.txt", cwd=self.main)
            git("commit", "-q", "-m", "elsewhere", cwd=self.main)
        self.lose_origin()
        return self.store.create(REPO, "review", "Tidy", branch=branch)

    def fix(self, review, agent=None):
        return self.run_lib("fix.sh", str(review), **({"FAKE_AGENT": agent} if agent else {}))

    def prompt(self):
        args = self.claude_args()
        return "\n".join(args[2:args.index("--permission-mode")])

    def test_a_conflicting_review_gets_an_agent_that_resolves_it(self):
        review = self.review_of()
        result = self.fix(review, RESOLVE)
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = self.prompt()
        self.assertIn(f"Review #{review}, of the branch {BRANCH} checked out in", prompt)
        self.assertIn("with conflicts in:\n\n- notes.txt\n", prompt)
        self.assertIn("A merge of main into it is in progress here", prompt)
        self.assertIn("the commits on main that touch each conflicted file", prompt)
        self.assertIn("so the commit reaches the review without a push", prompt)
        self.assertNotIn("origin", prompt)
        self.assertIn(f"`agent-desk review view {review}`", prompt)
        self.assertIn(f"`agent-desk review comment {review} --body <text>`", prompt)
        self.assertNotIn("gh", prompt.split())
        self.assertNotIn("<!-- agent-desk -->", prompt)
        session = self.claude_args()[self.claude_args().index("--session-id") + 1]
        self.assertEqual(self.notes(review), [
            ("desk", f"An agent started resolving the conflicts with main on `{BRANCH}`{LOG}. To take over, run "
                     f"`claude --resume {session}` in `{self.dest}`.{SIGNED}"),
            ("desk", f"The agent resolved the conflicts with main on `{BRANCH}`{LOG}.{SIGNED}"),
        ])
        git("merge-base", "--is-ancestor", "main", BRANCH, cwd=self.main)
        self.assertEqual(self.gh_log.read_text().strip(), "")

    def test_a_conflict_beside_a_folder_both_sides_changed_gets_an_agent(self):
        # git 2.56's `merge-tree --write-tree --quiet` exits 0 on this pair, though the full merge conflicts in notes.txt.
        (self.main / "t").mkdir()
        for name in ("x.txt", "y.txt"):
            (self.main / "t" / name).write_text(f"{name}\n")
        git("add", "t", cwd=self.main)
        git("commit", "-q", "-m", "folder", cwd=self.main)
        self.commit("first\n", "notes")
        git("checkout", "-q", "-b", BRANCH, cwd=self.main)
        (self.main / "t" / "y.txt").write_text("from the branch\n")
        git("add", "t", cwd=self.main)
        self.commit("from the branch\n", "branch side")
        git("checkout", "-q", "main", cwd=self.main)
        (self.main / "t" / "x.txt").write_text("from main\n")
        git("add", "t", cwd=self.main)
        self.commit("from main\n", "main side")
        self.lose_origin()
        review = self.store.create(REPO, "review", "Tidy", branch=BRANCH)
        result = self.fix(review, RESOLVE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("with conflicts in:\n\n- notes.txt\n", self.prompt())
        git("merge-base", "--is-ancestor", "main", BRANCH, cwd=self.main)

    def test_an_agent_that_leaves_the_conflicts_fails_and_the_merge_is_aborted(self):
        review = self.review_of()
        result = self.fix(review)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.notes(review)[-1],
                         ("desk", f"The agent couldn't resolve the conflicts with main on `{BRANCH}`{LOG}.{SIGNED}"))
        self.assertEqual(git("status", "--porcelain", cwd=self.dest), "")

    def test_an_agent_that_resolves_the_conflicts_and_stops_early_says_so_and_exits_stopped(self):
        review = self.review_of()
        result = self.run_lib("fix.sh", str(review), FAKE_AGENT=RESOLVE, **self.stopped_early("just test"))
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.notes(review)[-1], (
            "desk", f"The agent resolving the conflicts with main on `{BRANCH}` stopped early: it ended its turn while "
                    f"`just test` still ran, so the run stopped it{LOG}. Any commit it made is on `{BRANCH}`.{SIGNED}"))
        git("merge-base", "--is-ancestor", "main", BRANCH, cwd=self.main)

    def test_an_agent_that_stops_early_before_resolving_says_stopped_rather_than_failed(self):
        review = self.review_of()
        head = git("rev-parse", BRANCH, cwd=self.main)
        result = self.run_lib("fix.sh", str(review), **self.stopped_early("just test"))
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertTrue(self.notes(review)[-1][1].startswith(
            f"The agent resolving the conflicts with main on `{BRANCH}` stopped early: it ended its turn while "
            f"`just test` still ran"))
        # The merge the agent left unfinished is aborted, and the branch stays where it was.
        self.assertEqual(git("status", "--porcelain", cwd=self.dest), "")
        self.assertEqual(git("rev-parse", BRANCH, cwd=self.main), head)

    def test_a_review_that_merges_cleanly_with_passing_checks_needs_no_fix(self):
        review = self.review_of(conflicting=False)
        result = self.fix(review)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Review #{review} no longer conflicts with main, and no check fails on its head.",
                      result.stdout)
        self.assertEqual(self.notes(review), [])
        self.assertEqual(self.claude_args(), [])
        self.assertFalse(self.dest.exists())

    def test_a_review_whose_checks_fail_gets_an_agent_with_their_errors(self):
        review = self.review_of(conflicting=False)
        head = git("rev-parse", BRANCH, cwd=self.main)
        self.store.review_update(REPO, review, head_sha=head)
        log = self.root / "check.log"
        log.write_text(FAILED_LOG)
        check = self.store.check_queue(REPO, BRANCH, head)
        self.store.check_started(check, log)
        self.store.check_ended(check, "failed")
        agent = "echo fixed >notes.txt && git commit -q -am adapt"
        result = self.fix(review, agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = self.prompt()
        self.assertIn(f"test failed: {log}\n\nFAILED test_parse - AssertionError: 1 != 2\n", prompt)
        self.assertNotIn("The check failed.", prompt)
        self.assertNotIn("view", self.gh_log.read_text())
        self.assertIn(f"`agent-desk review view {review}`", prompt)
        self.assertNotIn("origin", prompt)
        self.assertEqual(git("log", "-1", "--format=%s", BRANCH, cwd=self.main), "adapt")
        self.assertEqual(self.notes(review)[-1],
                         ("desk", f"The agent adapted `{BRANCH}` to main and committed, which runs its checks again"
                                  f"{LOG}.{SIGNED}"))

    def test_a_merged_review_needs_no_fix(self):
        review = self.review_of()
        self.store.merge(REPO, review, git("rev-parse", "HEAD", cwd=self.main))
        result = self.fix(review)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Review #{review} is merged.", result.stdout)
        self.assertEqual(self.claude_args(), [])

    def test_a_branch_outside_agent_is_refused(self):
        review = self.review_of(branch="feature/tidy")
        result = self.fix(review)
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"Review #{review} is from feature/tidy", result.stderr)

    def test_a_review_the_store_lacks_stops_the_fix(self):
        result = self.fix(40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has no review #40", result.stderr)
        self.assertEqual(self.claude_args(), [])


if __name__ == "__main__":
    unittest.main()
