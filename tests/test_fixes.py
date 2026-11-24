"""Tests for agent-desk/lib/fixes.py, which picks the reviews whose conflicts with main an agent should resolve now."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import fixes  # noqa: E402
import store  # noqa: E402

REPO = "owner/repo"
NOW = 1_800_000_000
CHOSEN = {"auto": True, "maxTries": 2}
MAIN = "m1"
BASES = {"main": MAIN, "local-desk": "l1"}


def pr(number, branch="agent/issue-7", mergeable="CONFLICTING", draft=False, head="h1", title=None, base="main",
       base_broke=False):
    return {"number": number, "title": title or f"PR {number}", "headRefName": branch, "baseRefName": base,
            "headRefOid": head, "isDraft": draft, "mergeable": mergeable, "baseBroke": base_broke}


def verdicts(prs, active=(), starts=(), busy=lambda one: False, queue=None, broken=None, bases=None):
    # Each PR heads the merge queue unless the test lays the queue out.
    queue = [one["number"] for one in prs][:1] if queue is None else queue
    return [(one["number"], verdict) for one, verdict in fixes.plan(prs, set(active), list(starts), bases or BASES,
                                                                    CHOSEN, busy=busy, queue=queue, broken=broken)]


class PlanTest(unittest.TestCase):
    def test_the_queues_oldest_pr_from_any_agent_branch_is_due(self):
        for branch in ("agent/issue-7", "agent/chore-comment-one-20261028-1411", "agent/knowledge-fixes"):
            self.assertEqual(verdicts([pr(1, branch=branch)]), [(1, "start")])

    def test_only_conflicting_ready_agent_prs_count(self):
        prs = [pr(1, mergeable="MERGEABLE"), pr(2, mergeable="UNKNOWN"), pr(3, draft=True),
               pr(4, branch="feature/mine"), pr(5, branch="agent/nested/slug")]
        self.assertEqual(verdicts(prs, queue=[1, 2, 3, 4, 5]), [])

    def test_a_pr_whose_checks_main_broke_is_due_on_that_head(self):
        self.assertEqual(verdicts([pr(1, mergeable="MERGEABLE")], broken={1: "h1"}), [(1, "start")])
        # A later head, as a fix pushes, or a draft gets no fix.
        self.assertEqual(verdicts([pr(1, mergeable="MERGEABLE", head="h2")], broken={1: "h1"}), [])
        self.assertEqual(verdicts([pr(1, mergeable="MERGEABLE", draft=True)], broken={1: "h1"}), [])
        # It waits for its turn, and its tries count as a conflict fix's do.
        self.assertEqual(verdicts([pr(1, mergeable="MERGEABLE"), pr(2)], queue=[2, 1], broken={1: "h1"}),
                         [(1, "auto-fix when next to merge"), (2, "start")])
        [(_, verdict)] = verdicts([pr(1, mergeable="MERGEABLE")], starts=[(NOW - 600, 1, "h1", MAIN)],
                                  broken={1: "h1"})
        self.assertTrue(verdict.startswith("auto-fix tried this main"), verdict)

    def test_a_pr_whose_re_check_failed_after_main_moved_is_due_without_merge_or_its_turn(self):
        broke = pr(1, mergeable="MERGEABLE", base_broke=True)
        self.assertEqual(verdicts([broke], queue=[]), [(1, "start")])
        self.assertEqual(verdicts([broke, pr(2)], queue=[2, 1]), [(1, "start"), (2, "start")])
        # A draft gets no fix, and its tries count as any fix's do.
        self.assertEqual(verdicts([pr(1, mergeable="MERGEABLE", base_broke=True, draft=True)], queue=[]), [])
        [(_, verdict)] = verdicts([broke], starts=[(NOW - 600, 1, "h1", MAIN)], queue=[])
        self.assertTrue(verdict.startswith("auto-fix tried this main"), verdict)
        self.assertEqual(verdicts([broke], active=[1], queue=[]), [(1, "agent working or queued")])

    def test_a_pr_waits_for_merge_and_then_for_its_turn(self):
        self.assertEqual(verdicts([pr(1), pr(2), pr(3)], queue=[3, 2]),
                         [(1, "auto-fix on Merge"), (2, "auto-fix when next to merge"), (3, "start")])

    def test_a_fix_waits_while_a_run_works_the_review(self):
        self.assertEqual(verdicts([pr(1)], active=[1]), [(1, "agent working or queued")])
        self.assertEqual(verdicts([pr(1)], active=[2]), [(1, "start")])

    def test_a_fix_waits_while_someone_works_in_the_worktree(self):
        self.assertEqual(verdicts([pr(1)], busy=lambda one: True), [(1, "worktree in use")])

    def test_a_pr_gets_one_fix_for_each_main(self):
        [(_, verdict)] = verdicts([pr(1)], starts=[(NOW - 600, 1, "h1", MAIN)])
        self.assertTrue(verdict.startswith("auto-fix tried this main"), verdict)
        self.assertEqual(verdicts([pr(1)], starts=[(NOW - 600, 1, "h1", "m0")]), [(1, "start")])

    def test_a_pr_into_another_base_gets_one_fix_for_each_commit_of_that_base(self):
        tried = [(NOW - 600, 1, "h1", "l1")]
        self.assertEqual(verdicts([pr(1, base="local-desk")], starts=tried),
                         [(1, "auto-fix tried this local-desk; retries when local-desk moves")])
        # Its base moved while main stood still.
        self.assertEqual(verdicts([pr(1, base="local-desk")], starts=tried, bases={"main": MAIN, "local-desk": "l2"}),
                         [(1, "start")])
        # A try against main's head doesn't count against a PR into local-desk.
        self.assertEqual(verdicts([pr(1, base="local-desk")], starts=[(NOW - 600, 1, "h1", MAIN)]), [(1, "start")])

    def test_a_pr_the_list_gives_no_base_reads_as_one_into_main(self):
        one = pr(1)
        del one["baseRefName"]
        self.assertEqual(verdicts([one], starts=[(NOW - 600, 1, "h1", MAIN)]),
                         [(1, "auto-fix tried this main; retries when main moves")])

    def test_a_head_gets_max_tries_fixes_and_a_new_head_starts_over(self):
        starts = [(NOW - 900, 1, "h1", "m0"), (NOW - 600, 1, "h1", "m-1")]
        self.assertEqual(verdicts([pr(1)], starts=starts), [(1, "2 auto-fixes failed; use Fix")])
        self.assertEqual(verdicts([pr(1, head="h2")], starts=starts), [(1, "start")])


class LedgerTest(unittest.TestCase):
    def test_starts_are_kept_for_thirty_days(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "owner__repo.tsv"
            fixes.note(path, NOW - fixes.KEEP_SECONDS - 1, 1, "h0", "m0")
            fixes.note(path, NOW, 2, "h1", "m1")
            self.assertEqual(fixes.read_ledger(path, NOW), [(NOW, 2, "h1", "m1")])


class StoreCase(unittest.TestCase):
    """A temporary store and a main checkout with a main, a local-desk, and two agent branches."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.desk = store.Store(self.root / "desk.sqlite")
        self.addCleanup(self.desk.close)
        self.checkout = self.root / "repo"
        env = {"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        (self.root / "gitconfig").write_text("[user]\n\tname = Tester\n\temail = t@example.com\n"
                                             "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for args in (["init", "-q", str(self.checkout)], ["-C", str(self.checkout), "commit", "-q", "--allow-empty",
                                                          "-m", "start"],
                     ["-C", str(self.checkout), "branch", "local-desk"],
                     ["-C", str(self.checkout), "branch", "agent/issue-7"],
                     ["-C", str(self.checkout), "branch", "agent/issue-8"]):
            subprocess.run(["git", *args], check=True, capture_output=True)

    def sha(self, ref):
        return subprocess.run(["git", "-C", str(self.checkout), "rev-parse", ref], check=True, capture_output=True,
                              text=True).stdout.strip()


class GatherTest(StoreCase):
    def test_reads_the_open_reviews_with_their_heads_from_git_and_each_base_once(self):
        one = self.desk.create(REPO, "review", "Seven", branch="agent/issue-7")
        two = self.desk.create(REPO, "review", "Eight", branch="agent/issue-8", base="local-desk")
        self.desk.create(REPO, "review", "Gone", branch="agent/issue-9")
        prs, bases = fixes.gather(self.desk, REPO, self.checkout)
        self.assertEqual(sorted((one["number"], one["headRefOid"], one["baseRefName"]) for one in prs),
                         [(one, self.sha("agent/issue-7"), "main"), (two, self.sha("agent/issue-8"), "local-desk")])
        self.assertEqual(bases, {"main": self.sha("main"), "local-desk": self.sha("local-desk")})

    def test_a_head_whose_re_check_failed_after_its_retry_reads_as_broken_by_its_base(self):
        self.desk.create(REPO, "review", "Seven", branch="agent/issue-7")
        head = self.sha("agent/issue-7")

        def broke():
            [one] = fixes.gather(self.desk, REPO, self.checkout)[0]
            return one["baseBroke"]

        first = self.desk.check_queue(REPO, "agent/issue-7", head)
        self.desk.check_ended(first, "passed")
        self.assertFalse(broke())
        again = self.desk.check_queue(REPO, "agent/issue-7", head, "main", self.sha("main"), recheck=True)
        self.desk.check_ended(again, "failed", rerun=True)
        # The failure stands only once its second attempt fails too.
        self.assertFalse(broke())
        [retry] = self.desk.checks(("queued",))
        self.assertEqual((retry["attempt"], retry["recheck"], retry["base_sha"]), (2, 1, self.sha("main")))
        self.desk.check_ended(retry["id"], "failed")
        self.assertTrue(broke())

    def test_the_reviews_a_run_works_or_waits_to_are_active(self):
        seven = self.desk.create(REPO, "review", "Seven", branch="agent/issue-7")
        eight = self.desk.create(REPO, "review", "Eight", branch="agent/issue-8")
        other = self.desk.create(REPO, "review", "Other", branch="agent/other")
        self.assertEqual(fixes.active_reviews(self.desk, REPO), set())
        self.desk.request_run(REPO, "agent", "7")
        self.desk.request_run(REPO, "fix", str(eight))
        done = self.desk.request_run(REPO, "agent", f"pr-{other}")
        self.desk.request_run("someone/else", "fix", "40")
        self.assertEqual(fixes.active_reviews(self.desk, REPO), {seven, eight, other})
        self.desk.run_ended(done, "succeeded")
        self.assertEqual(fixes.active_reviews(self.desk, REPO), {seven, eight})


class TickTest(unittest.TestCase):
    def test_a_start_notes_the_head_of_the_prs_own_base(self):
        with tempfile.TemporaryDirectory() as folder:
            checkout = Path(folder) / "repo"
            checkout.mkdir()
            started = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.object(fixes, "STATE", Path(folder) / "fixes"), \
                    mock.patch.dict(os.environ, {"GH_REPO": "owner/repo"}), \
                    mock.patch.object(fixes.merges, "load", return_value={"queue": [{"pr": 1}]}), \
                    mock.patch.object(fixes.store, "Store"), \
                    mock.patch.object(fixes, "gather", return_value=([pr(1, base="local-desk")], BASES)), \
                    mock.patch.object(fixes, "active_reviews", return_value=set()), \
                    mock.patch.object(fixes, "in_use", return_value=False), \
                    mock.patch.object(fixes.subprocess, "run", return_value=started) as run, \
                    mock.patch("builtins.print"):
                self.assertEqual(fixes.main(["fixes.py", "tick", str(checkout)]), 0)
                self.assertEqual(run.call_args.args[0][1:], ["request", "fix", "1"])
                ledger = fixes.read_ledger(fixes.ledger_path("owner/repo"), int(fixes.time.time()))
            self.assertEqual([start[1:] for start in ledger], [(1, "h1", "l1")])

    def test_each_status_and_tick_saves_the_plan_it_shows_for_the_board_and_fixes_off_saves_none(self):
        with tempfile.TemporaryDirectory() as folder:
            checkout = Path(folder) / "repo"
            checkout.mkdir()
            with mock.patch.object(fixes, "STATE", Path(folder) / "fixes"), \
                    mock.patch.dict(os.environ, {"GH_REPO": "owner/repo"}), \
                    mock.patch.object(fixes.merges, "load", return_value={"queue": []}), \
                    mock.patch.object(fixes.store, "Store"), \
                    mock.patch.object(fixes, "gather", return_value=([pr(1)], BASES)), \
                    mock.patch.object(fixes, "active_reviews", return_value=set()), \
                    mock.patch.object(fixes, "in_use", return_value=False), \
                    mock.patch("builtins.print"):
                self.assertEqual(fixes.main(["fixes.py", "status", str(checkout)]), 0)
                self.assertEqual(fixes.saved_plan("owner/repo"), [{"pr": 1, "verdict": "auto-fix on Merge"}])
                (checkout / ".agent-desk.json").write_text('{"fixes": {"auto": false}}')
                self.assertEqual(fixes.main(["fixes.py", "tick", str(checkout)]), 0)
                self.assertEqual(fixes.saved_plan("owner/repo"), [])


class WorktreeTest(unittest.TestCase):
    def test_a_branch_works_in_its_slug_beside_the_main_checkout(self):
        self.assertEqual(fixes.worktree_of("/dev/web-app", "agent/issue-7"), Path("/dev/web-app-issue-7"))

    def test_settings_lay_the_project_over_the_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write('{"fixes": {"maxTries": 5}}')
        self.assertEqual(fixes.settings(handle.name), {"auto": True, "maxTries": 5})
        Path(handle.name).unlink()


if __name__ == "__main__":
    unittest.main()
