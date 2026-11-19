"""Tests for agent-desk/lib/merges.py, which merges the queued reviews in local git, each tested against the main it
lands on. The rules run on plain values; the ticks run git against temporary repositories and a temporary store."""

import copy
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import fixes  # noqa: E402
import merges  # noqa: E402
import store  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "lib"
NOW = 1_800_000_000
MAIN = "m1"
REPO = "owner/repo"


def pr(state="OPEN", mergeable="MERGEABLE", draft=False, branch="agent/issue-7", head="h1", base="main"):
    return {"state": state, "isDraft": draft, "mergeable": mergeable, "headRefOid": head, "baseRefName": base,
            "headRefName": branch, "title": "Add the thing"}


def check(status="passed", name="test", attempt=1):
    return {"name": name, "status": status, "attempt": attempt}


def decide(one=None, found=(check(),), has_main=True, item=None, testing=True):
    return merges.decide(one or pr(), list(found), has_main, MAIN, item or {}, testing)


def fix_step(one=None, starts=(), active=(), item=None, chosen=None, cause=merges.CONFLICTS):
    return merges.fix_step(one or pr(mergeable="CONFLICTING"), 1, list(starts), set(active), MAIN,
                           {} if item is None else item, NOW, chosen or fixes.DEFAULTS, cause)


class DecideTest(unittest.TestCase):
    def test_a_review_holding_main_merges_once_its_check_passes(self):
        self.assertEqual(decide(), ("merge", None))

    def test_a_review_behind_main_takes_main_first(self):
        self.assertEqual(decide(has_main=False), ("update", None))

    def test_a_review_waits_for_its_check(self):
        self.assertEqual(decide(found=[check("queued")]), ("wait", "checks starting"))
        self.assertEqual(decide(found=[check("running")]), ("wait", "checks running"))

    def test_a_head_without_a_check_gets_one_or_merges_in_a_project_without_tests(self):
        self.assertEqual(decide(found=[]), ("check", None))
        self.assertEqual(decide(found=[], testing=False), ("merge", None))

    def test_a_failed_check_drops_the_review(self):
        for status in store.CHECK_FAILURES:
            self.assertEqual(decide(found=[check(status, attempt=2)]), ("drop", "checks failed"))

    def test_merged_closed_draft_and_gone_reviews_leave(self):
        self.assertEqual(decide(pr(state="MERGED")), ("drop", None))
        self.assertEqual(decide(pr(state="CLOSED")), ("drop", "closed"))
        self.assertEqual(decide(pr(draft=True)), ("drop", "converted to draft"))
        self.assertEqual(decide(pr(head=None)), ("drop", "its branch agent/issue-7 is gone"))
        self.assertEqual(decide({"state": "GONE"}), ("drop", "it's no longer in the store"))
        self.assertEqual(merges.decide(pr(base="local-desk"), [], False, None, {}),
                         ("drop", "the main checkout has no local-desk branch"))

    def test_a_conflicting_review_goes_to_a_fix(self):
        self.assertEqual(decide(pr(mergeable="CONFLICTING"), has_main=False), ("fix", merges.CONFLICTS))
        self.assertEqual(decide(pr(mergeable="UNKNOWN"), has_main=False), ("wait", "git couldn't try the merge"))

    def test_main_that_merged_in_but_did_not_take_drops_the_review(self):
        self.assertEqual(decide(has_main=False, item={"main": MAIN}), ("drop", "main merge didn't land"))


class MainBrokeTest(unittest.TestCase):
    def test_checks_that_main_broke_go_to_a_fix(self):
        self.assertEqual(decide(found=[check("failed", attempt=2)], item={"passed": True}), ("fix", merges.BROKE))

    def test_checks_that_failed_before_main_merged_in_drop_the_review(self):
        self.assertEqual(decide(found=[check("failed")], item={"passed": False}), ("drop", "checks failed"))

    def test_a_head_under_fix_waits_for_it_even_when_main_moves(self):
        self.assertEqual(decide(has_main=False, item={"passed": True, "brokenHead": "h1"}), ("fix", merges.BROKE))

    def test_a_fixed_head_that_fails_too_drops_the_review(self):
        item = {"passed": True, "brokenHead": "h0"}
        self.assertEqual(decide(found=[check("failed")], item=item),
                         ("drop", f"{merges.BROKE}; auto-fix couldn't make them pass"))

    def test_the_fix_holds_the_queue_and_notes_the_head_it_mends(self):
        item = {"passed": True}
        self.assertEqual(fix_step(pr(), item=item, cause=merges.BROKE),
                         ("wait", f"{merges.BROKE}; auto-fix adapting it to main"))
        self.assertEqual((item["brokenHead"], item["brokenAt"]), ("h1", NOW))

    def test_a_review_no_fix_will_come_for_leaves_as_its_checks_failed(self):
        self.assertEqual(fix_step(pr(branch="feature/x"), cause=merges.BROKE), ("drop", "checks failed"))


class FixStepTest(unittest.TestCase):
    def test_a_conflicting_agent_review_holds_the_queue_for_its_fix(self):
        item = {}
        self.assertEqual(fix_step(item=item), ("wait", f"{merges.CONFLICTS}; auto-fix resolving"))
        self.assertEqual((item["conflictHead"], item["conflictAt"]), ("h1", NOW))

    def test_a_fix_that_failed_lets_the_review_go_unless_a_run_still_works_it(self):
        starts = [(NOW - 600, 1, "h1", MAIN)]
        self.assertEqual(fix_step(starts=starts), ("drop", f"{merges.CONFLICTS}; auto-fix couldn't resolve them"))
        self.assertEqual(fix_step(starts=starts, active=[1]), ("wait", f"{merges.CONFLICTS}; auto-fix resolving"))

    def test_a_review_no_fix_will_come_for_leaves_at_once(self):
        self.assertEqual(fix_step(chosen={"auto": False, "maxTries": 2}), ("drop", merges.CONFLICTS))

    def test_a_review_leaves_once_its_head_has_conflicted_for_fix_wait(self):
        item = {"conflictHead": "h1", "conflictAt": NOW - merges.FIX_WAIT}
        self.assertEqual(fix_step(item=item), ("drop", f"{merges.CONFLICTS}; no auto-fix landed in 60 minutes"))


def batch(prs=(1, 2), sha="b1", main=MAIN):
    return {"prs": list(prs), "sha": sha, "main": main, "branch": "merge-queue/1", "base": "main", "at": NOW,
            "heads": [f"h{n}" for n in prs], "branches": [f"agent/issue-{n}" for n in prs]}


class ChooseBatchTest(unittest.TestCase):
    def test_the_passing_reviews_at_the_head_go_together(self):
        sights = [(1, pr(), [check()]), (2, pr(), [check()]), (3, pr(), [check("failed")]), (4, pr(), [check()])]
        self.assertEqual([number for number, _ in merges.choose_batch(sights, "update")], [1, 2])

    def test_a_review_for_another_base_ends_the_batch(self):
        sights = [(1, pr(), [check()]), (2, pr(base="local-desk"), [check()])]
        self.assertEqual(merges.choose_batch(sights, "update"), [])

    def test_a_head_ready_to_merge_alone_is_not_batched(self):
        self.assertEqual(merges.choose_batch([(1, pr(), [check()]), (2, pr(), [check()])], "merge"), [])

    def test_a_review_without_passed_checks_cannot_join(self):
        for found in ([], [check("running")], [check("failed")]):
            self.assertFalse(merges.batchable(pr(), found))
        self.assertFalse(merges.batchable(pr(mergeable="CONFLICTING"), [check()]))


class BatchStepTest(unittest.TestCase):
    def step(self, one=None, main=MAIN, found=(check(),), queue=(1, 2, 3), heads=("h1", "h2")):
        return merges.batch_step(one or batch(), [{"pr": n} for n in queue], main, list(found), list(heads))

    def test_a_batch_whose_check_passed_lands(self):
        self.assertEqual(self.step(), ("land", None))

    def test_a_batch_waits_for_its_check(self):
        self.assertEqual(self.step(found=[check("queued")]), ("wait", "checks starting"))
        self.assertEqual(self.step(found=[check("running")]), ("wait", "checks running"))

    def test_a_failed_or_cancelled_check_fails_the_batch(self):
        self.assertEqual(self.step(found=[check("failed")]), ("fail", "checks failed"))
        self.assertEqual(self.step(found=[]), ("fail", "check never ran"))

    def test_main_moving_or_the_queue_changing_starts_the_batch_again(self):
        self.assertEqual(self.step(main="m2"), ("abandon", "main moved"))
        self.assertEqual(self.step(queue=(2, 1)), ("abandon", "the queue changed"))

    def test_a_member_whose_branch_moved_starts_the_batch_again(self):
        self.assertEqual(self.step(heads=("h1", "h9")), ("abandon", "#2 moved"))
        self.assertEqual(self.step(heads=("h1", None)), ("abandon", "#2 moved"))
        unknown = {key: value for key, value in batch().items() if key != "heads"}
        self.assertEqual(self.step(unknown), ("abandon", "a member moved"))


class ShownTest(unittest.TestCase):
    def test_the_queue_shows_each_review_behind_the_one_before_and_where_main_stands(self):
        state = {"queue": [{"pr": 1, "note": "checks running"}, {"pr": 2}], "dropped": [
            {"pr": 3, "reason": "checks failed", "at": NOW, "head": "h3"}]}
        origin = {"ahead": 2, "push": {"state": "ok"}}
        self.assertEqual(merges.shown(state, origin), {
            "queue": [{"pr": 1, "note": "checks running"}, {"pr": 2, "note": "after #1"}],
            "dropped": [{"pr": 3, "reason": "checks failed", "head": "h3"}], "ahead": 2, "push": {"state": "ok"}})

    def test_a_stopped_review_carries_its_stop_and_the_reviews_behind_it_wait_on_it(self):
        stop = {"where": "the main checkout", "files": "README.md"}
        state = {"queue": [{"pr": 275, "note": "stopped: ...", "stopped": stop}, {"pr": 289}], "dropped": []}
        self.assertEqual(merges.shown(state)["queue"], [{"pr": 275, "note": "stopped: ...", "stopped": stop},
                                                        {"pr": 289, "note": "after #275"}])

    def test_a_stop_names_the_main_checkout_or_the_worktree_folder_and_lists_the_files(self):
        with tempfile.TemporaryDirectory() as root:
            main = Path(root) / "demo"
            main.mkdir()
            self.assertEqual(merges.stop_of(main, Path(root) / "demo" / ".", {"b.md", "a.md"}),
                             {"where": "the main checkout", "files": "a.md, b.md"})
            self.assertEqual(merges.stop_of(main, Path(root) / "demo-local", {"1", "2", "3", "4", "5"}),
                             {"where": "demo-local", "files": "1, 2, 3 and 2 more"})


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True).stdout.strip()


class GitCase(unittest.TestCase):
    """
    A main checkout with a file on main, a temporary store, and a fixes ledger of its own. Each review is a commit on
    an agent branch, in a worktree beside the checkout when the test asks for one.
    """

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "gitconfig").write_text("[user]\n\tname = Tester\n\temail = t@example.com\n"
                                             "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        for patcher in (mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"),
                                                     "GIT_CONFIG_NOSYSTEM": "1"}),
                        mock.patch.object(fixes, "STATE", self.root / "fixes")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.main = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.main)], check=True)
        self.commit(self.main, {"shared.txt": "one\ntwo\nthree\n"}, "start")
        self.desk = store.Store(self.root / "desk.sqlite")
        self.addCleanup(self.desk.close)
        self.said = []
        self.now = NOW

    def commit(self, where, files, message):
        for name, text in files.items():
            (Path(where) / name).write_text(text)
            git(where, "add", name)
        git(where, "commit", "-q", "-m", message)
        return git(where, "rev-parse", "HEAD")

    def review(self, slug, files, worktree=False, base="main"):
        """A review of agent/<slug>, one commit off <base> that writes `files`, and the branch's folder."""
        branch = f"agent/{slug}"
        if worktree:
            where = self.root / f"repo-{slug}"
            git(self.main, "worktree", "add", "-q", "-b", branch, str(where), base)
        else:
            where = self.root / f"scratch-{slug}"
            git(self.main, "worktree", "add", "-q", "-b", branch, str(where), base)
        self.commit(where, files, f"work on {slug}")
        if not worktree:
            git(self.main, "worktree", "remove", str(where))
        return self.desk.create(REPO, "review", f"Work on {slug}", branch=branch, base=base), where

    def move_main(self, files):
        """A commit on main, made in the main checkout as a person would."""
        return self.commit(self.main, files, "main moves")

    def head(self, branch):
        return git(self.main, "rev-parse", f"refs/heads/{branch}")

    def finish(self, branch, status="passed", head=None):
        """Ends the check of the branch's head, queuing one first when none waits."""
        head = head or self.head(branch)
        waiting = [row for row in self.desk.checks(("queued", "running"), REPO, branch) if row["head_sha"] == head]
        number = waiting[-1]["id"] if waiting else self.desk.check_queue(REPO, branch, head)
        self.desk.check_started(number, self.root / "check.log")
        self.desk.check_ended(number, status)

    def queue(self, *numbers):
        return {"queue": [{"pr": number, "added": NOW} for number in numbers], "dropped": []}

    def tick(self, state, testing=True, chosen=None):
        self.at = merges.Tick(self.desk, REPO, self.main, self.now, self.said.append, chosen, testing=testing)
        merges.tick(self.at, state)
        return state

    def merged_sha(self, number):
        return self.desk.item(REPO, number)["mergeCommit"]["oid"]


class TickTest(GitCase):
    def test_a_clean_review_holding_main_lands_on_main_as_tested(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        before, head = self.head("main"), self.head("agent/issue-7")
        self.finish("agent/issue-7")

        state = self.tick(self.queue(number))
        self.assertEqual(state["queue"], [])
        main = self.head("main")
        self.assertEqual(git(self.main, "rev-parse", "HEAD"), main)
        self.assertEqual(git(self.main, "log", "-1", "--format=%P", main).split(), [before, head])
        self.assertEqual(git(self.main, "rev-parse", f"{main}^{{tree}}"), git(self.main, "rev-parse", f"{head}^{{tree}}"))
        self.assertEqual(git(self.main, "log", "-1", "--format=%s", main), "Merge review #1 from agent/issue-7")
        self.assertEqual((self.main / "new.txt").read_text(), "new\n")
        self.assertEqual(self.merged_sha(number), main)
        self.assertEqual(self.desk.item(REPO, number)["state"], "MERGED")
        self.assertEqual(self.at.landed, [("main", before, main)])

    def test_a_tick_killed_after_landing_records_the_merge_it_made_on_the_next(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        self.finish("agent/issue-7")
        saved = self.queue(number)
        # The tick dies once main moved, before it records the review or saves the queue.
        with mock.patch.object(merges.Tick, "record", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.tick(copy.deepcopy(saved))
        landed = self.head("main")
        self.assertEqual(self.desk.item(REPO, number)["state"], "OPEN")

        state = self.tick(saved)
        self.assertEqual(state["queue"], [])
        self.assertEqual(self.head("main"), landed)
        self.assertEqual(self.merged_sha(number), landed)
        self.assertEqual(self.desk.checks(("queued",), REPO), [])

    def test_a_merge_the_store_failed_to_record_stays_queued_until_a_later_tick_records_it(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        self.finish("agent/issue-7")
        failing = mock.patch.object(self.desk, "merge", side_effect=store.StoreError("database is locked"))
        with failing:
            state = self.tick(self.queue(number))
        landed = self.head("main")
        self.assertEqual(state["queue"][0]["note"],
                         f"merged as {landed[:7]}; the store couldn't record it: database is locked")
        self.assertEqual(self.desk.item(REPO, number)["state"], "OPEN")

        self.now += 60
        state = self.tick(state)
        self.assertEqual(state["queue"], [])
        self.assertEqual(self.head("main"), landed)
        self.assertEqual(self.merged_sha(number), landed)

    def test_a_review_its_base_took_as_it_was_records_its_own_head(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        head = self.head("agent/issue-7")
        git(self.main, "merge", "-q", "--ff-only", "agent/issue-7")
        self.assertEqual(self.tick(self.queue(number))["queue"], [])
        self.assertEqual(self.merged_sha(number), head)
        self.assertEqual(self.at.landed, [])

    def test_a_head_without_a_check_gets_one_and_waits(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        state = self.tick(self.queue(number))
        self.assertEqual(state["queue"][0]["note"], "checks starting")
        [row] = self.desk.checks(("queued",), REPO, "agent/issue-7")
        self.assertEqual(row["head_sha"], self.head("agent/issue-7"))

    def test_a_project_without_tests_merges_a_review_once_it_holds_main(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        self.move_main({"other.txt": "other\n"})
        state = self.tick(self.queue(number), testing=False)
        self.assertEqual(state["queue"], [])
        self.assertTrue((self.main / "new.txt").exists())
        self.assertEqual(self.desk.checks(("queued",), REPO), [])

    def test_a_review_behind_main_takes_main_in_its_worktree_and_lands_once_that_head_passes(self):
        number, folder = self.review("issue-7", {"new.txt": "new\n"}, worktree=True)
        self.finish("agent/issue-7")
        main = self.move_main({"other.txt": "other\n"})

        state = self.tick(self.queue(number))
        item = state["queue"][0]
        head = self.head("agent/issue-7")
        self.assertEqual(git(folder, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.main, "log", "-1", "--format=%P", head).split()[1], main)
        self.assertTrue((folder / "other.txt").exists())
        self.assertEqual((item["note"], item["main"], item["passed"]), ("merged main; checks starting", main, True))
        self.assertEqual([row["head_sha"] for row in self.desk.checks(("queued",), REPO, "agent/issue-7")], [head])

        self.now += 60
        self.finish("agent/issue-7")
        state = self.tick(state)
        self.assertEqual(state["queue"], [])
        self.assertEqual(git(self.main, "log", "-1", "--format=%P", self.head("main")).split(), [main, head])

    def test_a_review_without_a_worktree_takes_main_without_one(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        old = self.head("agent/issue-7")
        main = self.move_main({"other.txt": "other\n"})
        self.tick(self.queue(number))
        self.assertEqual(git(self.main, "log", "-1", "--format=%P", self.head("agent/issue-7")).split(), [old, main])

    def test_a_worktree_with_uncommitted_changes_waits(self):
        number, folder = self.review("issue-7", {"new.txt": "new\n"}, worktree=True)
        self.move_main({"other.txt": "other\n"})
        (folder / "new.txt").write_text("half done\n")
        old = self.head("agent/issue-7")
        state = self.tick(self.queue(number))
        self.assertEqual(state["queue"][0]["note"], f"its worktree {folder} has uncommitted changes")
        self.assertEqual(self.head("agent/issue-7"), old)

    def test_an_untracked_file_main_adds_keeps_its_review_waiting_and_the_queue_goes_on(self):
        first, folder = self.review("issue-7", {"seven.txt": "7\n"}, worktree=True)
        self.move_main({"other.txt": "other\n"})
        (folder / "other.txt").write_text("mine\n")
        old = self.head("agent/issue-7")
        second, _ = self.review("issue-8", {"eight.txt": "8\n"})
        self.finish("agent/issue-8")

        state = self.tick(self.queue(first, second))
        why = f"its worktree {folder} has untracked other.txt, which merging main would overwrite"
        self.assertEqual(state["queue"], [{"pr": first, "added": NOW, "note": why, "aside": True}])
        self.assertEqual(self.head("agent/issue-7"), old)
        self.assertEqual((folder / "other.txt").read_text(), "mine\n")
        self.assertEqual(self.merged_sha(second), self.head("main"))
        self.assertEqual(merges.shown(state)["queue"], [{"pr": first, "note": why}])

        # Once the file goes, the review takes main on the next tick.
        (folder / "other.txt").unlink()
        state = self.tick(state)
        self.assertEqual(state["queue"][0]["note"], "merged main; checks starting")
        self.assertNotIn("aside", state["queue"][0])

    def test_reviews_behind_one_set_aside_show_the_review_they_wait_on(self):
        state = {"queue": [{"pr": 1, "note": "its worktree has uncommitted changes", "aside": True},
                           {"pr": 2, "note": "checks running"}, {"pr": 3}], "dropped": []}
        self.assertEqual([one["note"] for one in merges.shown(state)["queue"]],
                         ["its worktree has uncommitted changes", "checks running", "after #2"])

    def test_a_conflict_holds_the_queue_for_a_fix_or_leaves_without_one(self):
        number, folder = self.review("issue-7", {"shared.txt": "one\nTWO\nthree\n"}, worktree=True)
        self.finish("agent/issue-7")
        self.move_main({"shared.txt": "one\n2\nthree\n"})
        old = self.head("agent/issue-7")

        state = self.tick(self.queue(number))
        self.assertEqual(state["queue"][0]["note"], f"{merges.CONFLICTS}; auto-fix resolving")
        self.assertEqual(self.head("agent/issue-7"), old)
        self.assertEqual(git(folder, "status", "--porcelain"), "")

        state = self.tick(self.queue(number), chosen={"auto": False, "maxTries": 2})
        self.assertEqual(state["queue"], [])
        self.assertEqual(state["dropped"][0]["reason"], merges.CONFLICTS)
        [note] = self.desk.item(REPO, number)["comments"]
        self.assertEqual(note["author"]["login"], "desk")
        self.assertEqual(note["body"], f"Left the merge queue on {old[:7]}: {merges.CONFLICTS}.{merges.DESK_MARK}")

    def test_a_conflict_beside_a_folder_both_sides_changed_holds_the_queue_for_a_fix(self):
        # git 2.56's `merge-tree --write-tree --quiet` exits 0 on this pair, though the full merge conflicts in
        # shared.txt.
        (self.main / "t").mkdir()
        self.move_main({"t/x.txt": "x\n", "t/y.txt": "y\n"})
        number, folder = self.review("issue-7", {"shared.txt": "one\nTWO\nthree\n", "t/y.txt": "y from the branch\n"},
                                     worktree=True)
        self.finish("agent/issue-7")
        self.move_main({"shared.txt": "one\n2\nthree\n", "t/x.txt": "x from main\n"})

        state = self.tick(self.queue(number))
        self.assertEqual(state["queue"][0]["note"], f"{merges.CONFLICTS}; auto-fix resolving")
        self.assertEqual(git(folder, "status", "--porcelain"), "")

    def test_a_worktree_merge_that_conflicts_where_merge_tree_finds_none_names_the_files(self):
        number, folder = self.review("issue-7", {"shared.txt": "one\nTWO\nthree\n"}, worktree=True)
        self.finish("agent/issue-7")
        self.move_main({"shared.txt": "one\n2\nthree\n"})

        with mock.patch.object(merges.checks, "merges_with", return_value="MERGEABLE"):
            state = self.tick(self.queue(number))
        why = f"git couldn't merge main in its worktree {folder}: main conflicts in shared.txt"
        self.assertEqual(state["queue"], [{"pr": number, "added": NOW, "note": why, "aside": True}])
        self.assertEqual(git(folder, "status", "--porcelain"), "")

    def test_a_failed_check_drops_the_review_and_the_queue_goes_on(self):
        first, _ = self.review("issue-7", {"seven.txt": "7\n"})
        second, _ = self.review("issue-8", {"eight.txt": "8\n"})
        self.finish("agent/issue-7", "failed")
        self.finish("agent/issue-8")
        state = self.tick(self.queue(first, second))
        self.assertEqual([one["pr"] for one in state["dropped"]], [first])
        self.assertEqual(state["dropped"][0]["reason"], "checks failed")
        # The second took main, since the first never landed, and waits for its check.
        self.assertEqual(state["queue"], [])
        self.assertTrue((self.main / "eight.txt").exists())
        self.assertFalse((self.main / "seven.txt").exists())

    def test_a_dirty_main_checkout_stops_the_queue_until_the_change_goes(self):
        number, _ = self.review("issue-7", {"shared.txt": "one\ntwo\nthree\nfour\n"})
        self.finish("agent/issue-7")
        (self.main / "shared.txt").write_text("mine\n")
        before = self.head("main")

        second, _ = self.review("issue-8", {"eight.txt": "8\n"})
        state = self.tick(self.queue(number, second), testing=False)
        self.assertEqual(self.head("main"), before)
        self.assertEqual(state["queue"][0]["note"],
                         "stopped: the main checkout has uncommitted changes to shared.txt, which the merge changes")
        self.assertEqual(state["queue"][0]["stopped"], {"where": "the main checkout", "files": "shared.txt"})
        self.assertNotIn("stopped", state["queue"][1])
        self.assertEqual((self.main / "shared.txt").read_text(), "mine\n")
        # A tick that finds the change still there keeps the stop.
        self.assertEqual(self.tick(state, testing=False)["queue"][0]["stopped"]["files"], "shared.txt")

        git(self.main, "checkout", "--", "shared.txt")
        self.assertEqual(self.tick(state, testing=False)["queue"], [])
        self.assertEqual((self.main / "shared.txt").read_text(), "one\ntwo\nthree\nfour\n")

    def test_a_change_to_a_file_the_merge_leaves_alone_stays(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        self.finish("agent/issue-7")
        (self.main / "shared.txt").write_text("mine\n")
        self.assertEqual(self.tick(self.queue(number))["queue"], [])
        self.assertEqual((self.main / "shared.txt").read_text(), "mine\n")
        self.assertTrue((self.main / "new.txt").exists())

    def test_a_main_checkout_on_another_branch_keeps_its_files_while_main_moves(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        self.finish("agent/issue-7")
        git(self.main, "checkout", "-q", "-b", "mine")
        self.assertEqual(self.tick(self.queue(number))["queue"], [])
        self.assertFalse((self.main / "new.txt").exists())
        self.assertEqual(git(self.main, "show", f"{self.head('main')}:new.txt"), "new")

    def test_a_detached_main_checkout_keeps_its_files_while_main_moves(self):
        number, _ = self.review("issue-7", {"new.txt": "new\n"})
        self.finish("agent/issue-7")
        detached = git(self.main, "rev-parse", "HEAD")
        git(self.main, "checkout", "-q", "--detach")
        self.assertEqual(self.tick(self.queue(number))["queue"], [])
        self.assertEqual(git(self.main, "rev-parse", "HEAD"), detached)
        self.assertFalse((self.main / "new.txt").exists())
        self.assertEqual(self.merged_sha(number), self.head("main"))

    def test_a_review_for_another_base_lands_on_that_base(self):
        git(self.main, "branch", "local-desk")
        number, _ = self.review("issue-7", {"new.txt": "new\n"}, base="local-desk")
        self.finish("agent/issue-7")
        main = self.head("main")
        self.assertEqual(self.tick(self.queue(number))["queue"], [])
        self.assertEqual(self.head("main"), main)
        self.assertEqual(self.merged_sha(number), self.head("local-desk"))


class BatchTest(GitCase):
    def two_behind_main(self):
        """Two reviews whose checks passed, then a main that moved past both."""
        first, _ = self.review("issue-7", {"seven.txt": "7\n"})
        second, _ = self.review("issue-8", {"eight.txt": "8\n"})
        self.finish("agent/issue-7")
        self.finish("agent/issue-8")
        return first, second, self.move_main({"other.txt": "other\n"})

    def test_passing_reviews_are_merged_onto_a_branch_and_tested_together_then_land(self):
        first, second, main = self.two_behind_main()
        state = self.tick(self.queue(first, second))
        batch = state["batch"]
        self.assertEqual((batch["prs"], batch["branch"], batch["main"]), ([first, second], f"merge-queue/{NOW}", main))
        self.assertEqual(self.head(batch["branch"]), batch["sha"])
        [row] = self.desk.checks(("queued",), REPO, batch["branch"])
        self.assertEqual(row["head_sha"], batch["sha"])
        self.assertEqual(state["queue"][0]["note"], f"batch with #{second}: checks starting")
        # Neither branch moved: the batch merged them as they stood.
        self.assertEqual(self.head("main"), main)

        self.now += 60
        self.finish(batch["branch"], head=batch["sha"])
        state = self.tick(state)
        self.assertNotIn("batch", state)
        self.assertEqual(state["queue"], [])
        self.assertEqual(self.head("main"), batch["sha"])
        self.assertEqual([self.merged_sha(first), self.merged_sha(second)], batch["merges"])
        self.assertTrue((self.main / "seven.txt").exists() and (self.main / "eight.txt").exists())
        self.assertEqual(git(self.main, "branch", "--list", "merge-queue/*"), "")

    def test_a_failed_batch_halves_the_next_one(self):
        first, second, _ = self.two_behind_main()
        state = self.tick(self.queue(first, second))
        branch, sha = state["batch"]["branch"], state["batch"]["sha"]
        self.finish(branch, "failed", head=sha)
        self.now += 60
        state = self.tick(state)
        self.assertNotIn("batch", state)
        self.assertEqual(state["limit"], 1)
        self.assertEqual(git(self.main, "branch", "--list", "merge-queue/*"), "")
        # The oldest goes alone: it takes main and waits for its check.
        self.assertEqual(state["queue"][0]["note"], "merged main; checks starting")
        self.assertIn(f"#{first}, #{second}: the batch's checks failed, so the next batch takes 1.", self.said)

    def test_main_moving_starts_the_batch_again_and_ends_its_check(self):
        first, second, _ = self.two_behind_main()
        state = self.tick(self.queue(first, second))
        old = state["batch"]
        self.move_main({"more.txt": "more\n"})
        self.now += 60
        with mock.patch.object(merges.subprocess, "run", wraps=subprocess.run):
            state = self.tick(state)
        self.assertIn(f"#{first}, #{second}: the batch starts again, since main moved.", self.said)
        self.assertEqual(self.desk.checks(("queued",), REPO, old["branch"]), [])
        self.assertEqual(state["batch"]["branch"], f"merge-queue/{self.now}")

    def test_a_member_pushed_to_during_its_test_starts_the_batch_again(self):
        first, second, _ = self.two_behind_main()
        state = self.tick(self.queue(first, second))
        tested = state["batch"]
        self.finish(tested["branch"], head=tested["sha"])
        # An agent commits to the first review's branch while the batch is tested.
        where = self.root / "late"
        git(self.main, "worktree", "add", "-q", str(where), "agent/issue-7")
        late = self.commit(where, {"late.txt": "late\n"}, "late work")
        self.now += 60
        state = self.tick(state)
        self.assertIn(f"#{first}, #{second}: the batch starts again, since #{first} moved.", self.said)
        self.assertNotEqual(self.head("main"), tested["sha"])
        self.assertEqual(self.desk.item(REPO, first)["state"], "OPEN")
        # The first review's new head takes main and waits for its own check.
        self.assertNotIn("batch", state)
        self.assertEqual(git(self.main, "rev-parse", "agent/issue-7^1"), late)

    def test_a_batch_onto_a_dirty_main_checkout_waits(self):
        first, second, _ = self.two_behind_main()
        state = self.tick(self.queue(first, second))
        self.finish(state["batch"]["branch"], head=state["batch"]["sha"])
        (self.main / "eight.txt").write_text("mine\n")
        state = self.tick(state)
        self.assertIn("batch", state)
        self.assertIn("has uncommitted changes to eight.txt", state["queue"][0]["note"])
        stop = {"where": "the main checkout", "files": "eight.txt"}
        self.assertEqual([item.get("stopped") for item in state["queue"]], [stop, stop])

        (self.main / "eight.txt").unlink()
        state = self.tick(state)
        self.assertEqual(state["queue"], [])


class CommandTest(GitCase):
    def test_a_tick_git_cannot_finish_keeps_the_queue_and_says_why_on_its_first_review(self):
        with mock.patch.object(merges, "STATE", self.root / "merges"), \
                mock.patch.dict(os.environ, {"GH_REPO": REPO}), \
                mock.patch.object(merges.store, "Store", return_value=self.desk), \
                mock.patch.object(merges, "tick", side_effect=merges.GitError("git commit-tree failed: no identity")), \
                mock.patch.object(merges.pushes, "run_tick") as pushed, \
                mock.patch.object(merges.pushes, "shown", return_value={"ahead": 0, "push": {"state": "ok"}}), \
                mock.patch("builtins.print"):
            merges.save(merges.state_path(REPO), self.queue(4))
            self.assertEqual(merges.main(["merges.py", "tick", str(self.main)]), 0)
            state = merges.load(merges.state_path(REPO), NOW)
        self.assertEqual(state["queue"], [{"pr": 4, "added": NOW, "note": "waits: git commit-tree failed: no identity"}])
        pushed.assert_called_once()

    def command(self, mode, *numbers, run=""):
        """merges.main in `mode`, inside a run when `run` names one, returning its status and what it printed."""
        with mock.patch.object(merges, "STATE", self.root / "merges"), \
                mock.patch.dict(os.environ, {"GH_REPO": REPO, "AGENT_DESK_RUN": run, "AGENT_DESK_AUTHOR": ""}), \
                mock.patch.object(merges.store, "Store", return_value=self.desk), \
                mock.patch.object(merges, "tick") as ticked, \
                mock.patch.object(merges.pushes, "run_tick"), \
                mock.patch.object(merges.pushes, "shown", return_value={"ahead": 0, "push": {"state": "ok"}}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            status = merges.main(["merges.py", mode, str(self.main), *numbers])
        self.ticked = ticked
        return status, out.getvalue() + err.getvalue()

    def queue_file(self):
        with mock.patch.object(merges, "STATE", self.root / "merges"):
            return merges.state_path(REPO).read_bytes()

    def test_an_agent_run_cannot_queue_a_merge_and_leaves_the_queue_as_it_was(self):
        with mock.patch.object(merges, "STATE", self.root / "merges"):
            merges.save(merges.state_path(REPO), self.queue(4))
        before = self.queue_file()
        status, said = self.command("add", "7", run="12")
        self.assertEqual(status, 1)
        self.assertIn("An agent run can't queue a merge", said)
        self.assertEqual(self.queue_file(), before)

    def test_an_agent_run_queues_nothing_when_no_queue_file_exists(self):
        status, _ = self.command("add", "7", run="12")
        self.assertEqual(status, 1)
        self.assertFalse((self.root / "merges").exists())

    def test_outside_a_run_merges_add_queues_as_before(self):
        status, said = self.command("add", "7")
        self.assertEqual(status, 0)
        self.assertIn("#7: queued to merge.", said)
        with mock.patch.object(merges, "STATE", self.root / "merges"):
            self.assertEqual([item["pr"] for item in merges.load(merges.state_path(REPO), NOW)["queue"]], [7])

    def test_remove_status_and_tick_work_inside_a_run_as_outside_one(self):
        for run in ("", "12"):
            with self.subTest(run=run):
                with mock.patch.object(merges, "STATE", self.root / "merges"):
                    merges.save(merges.state_path(REPO), self.queue(4, 5))
                self.assertEqual(self.command("status", run=run)[0], 0)
                self.assertEqual(self.command("tick", run=run)[0], 0)
                self.ticked.assert_called_once()
                self.assertEqual(self.command("remove", "4", run=run)[0], 0)
                with mock.patch.object(merges, "STATE", self.root / "merges"):
                    self.assertEqual([item["pr"] for item in merges.load(merges.state_path(REPO), NOW)["queue"]], [5])


class AfterLandingTest(GitCase):
    def test_a_landing_on_the_main_checkout_runs_the_sync_unless_a_program_runs_there(self):
        (self.main / ".agent-desk.json").write_text('{"sync": {"run": "touch synced", "skipWhile": ["nothing-here"]}}')
        landed = [("main", "a", "b")]
        merges.after_landing(self.main, landed, self.said.append, desk_root=self.root)
        self.assertTrue((self.main / "synced").exists())
        (self.main / "synced").unlink()
        with mock.patch.object(merges, "running_in", return_value="godot"):
            merges.after_landing(self.main, landed, self.said.append, desk_root=self.root)
        self.assertFalse((self.main / "synced").exists())
        self.assertIn(f"Skipped the sync: godot is running in {self.main.resolve()}.", self.said)

    def test_a_landing_on_another_branch_runs_nothing(self):
        (self.main / ".agent-desk.json").write_text('{"sync": {"run": "touch synced"}}')
        git(self.main, "branch", "local-desk")
        merges.after_landing(self.main, [("local-desk", "a", "b")], self.said.append, desk_root=self.root)
        self.assertFalse((self.main / "synced").exists())

    def test_a_landing_that_changes_the_dispatcher_restarts_it_in_the_desks_own_checkout(self):
        before = self.head("main")
        (self.main / "lib").mkdir()
        after = self.commit(self.main, {"lib/dispatch.py": "x\n"}, "change the dispatcher")
        calls = []

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        merges.after_landing(self.main, [("main", before, after)], self.said.append, desk_root=self.main, run=run)
        self.assertIn(["systemctl", "--user", "restart", merges.DISPATCH_UNIT], calls)
        self.assertNotIn(["systemctl", "--user", "restart", merges.WEB_UNIT], calls)
        calls.clear()
        merges.after_landing(self.main, [("main", before, after)], self.said.append, desk_root=self.root, run=run)
        self.assertEqual(calls, [])

    def test_a_landing_that_changes_the_web_pages_code_restarts_its_server_when_it_runs(self):
        before = self.head("main")
        (self.main / "inbox" / "hooks").mkdir(parents=True)
        after = self.commit(self.main, {"inbox/hooks/board.ts": "x\n"}, "change the board")
        calls, active = [], {"code": 0}

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, active["code"] if "is-active" in args else 0)

        merges.after_landing(self.main, [("main", before, after)], self.said.append, desk_root=self.main, run=run)
        self.assertIn(["systemctl", "--user", "restart", merges.WEB_UNIT], calls)
        self.assertNotIn(["systemctl", "--user", "restart", merges.DISPATCH_UNIT], calls)
        self.assertIn(f"Restarted {merges.WEB_UNIT}.", self.said)
        calls.clear()
        active["code"] = 3
        merges.after_landing(self.main, [("main", before, after)], self.said.append, desk_root=self.main, run=run)
        self.assertEqual(calls, [["systemctl", "--user", "--quiet", "is-active", merges.WEB_UNIT]])

    def test_the_web_pages_code_is_everything_under_web_and_the_pane_hooks(self):
        self.assertTrue(merges.runs_from({"README.md", "web/server.ts"}, merges.WEB_CODE))
        self.assertTrue(merges.runs_from({"inbox/hooks/board.ts"}, merges.WEB_CODE))
        self.assertTrue(merges.runs_from({"web/vendor/preact-htm.js"}, merges.WEB_CODE))
        self.assertFalse(merges.runs_from({"inbox/skills/work-issue/SKILL.md", "lib/web.sh", "webby/x"},
                                          merges.WEB_CODE))
        code = merges.dispatcher_code(LIB.parent)
        self.assertTrue(merges.runs_from({"lib/store.py"}, code))
        self.assertFalse(merges.runs_from({"lib/store.pyc", "lib/storage.py"}, code))

    def test_the_dispatchers_code_is_dispatch_py_and_every_lib_module_it_imports_directly_or_through_another(self):
        lib = self.root / "walk" / "lib"
        lib.mkdir(parents=True)
        files = {
            "dispatch.py": "import os\nimport hold, store  # noqa\nfrom run_units import x\n",
            "hold.py": "from . import nothing\n",
            "store.py": "import json\n\n\ndef later():\n    import review_checks\n",
            "review_checks.py": "from checks.part import y\n",
            "checks.py": "import store\n",
            "run_units.py": "this is not python(\n",
            "web_only.py": "import dispatch\n",
        }
        for name, text in files.items():
            (lib / name).write_text(text)
        self.assertEqual(merges.dispatcher_code(lib.parent), tuple(sorted(
            f"lib/{name}.py" for name in ("dispatch", "hold", "store", "review_checks", "checks", "run_units"))))
        self.assertEqual(merges.dispatcher_code(self.root / "empty"), ("lib/dispatch.py",))

    def test_a_change_to_any_lib_module_the_dispatcher_loads_restarts_it(self):
        with tempfile.TemporaryDirectory() as state:
            loaded = subprocess.run(
                [sys.executable, "-I", "-c", "import os, sys\nsys.path.insert(0, sys.argv[1])\nimport dispatch\n"
                 "print('\\n'.join(sorted(name for name, module in list(sys.modules.items())\n"
                 "    if os.path.dirname(os.path.abspath(getattr(module, '__file__', None) or '')) == sys.argv[1])))",
                 str(LIB)], capture_output=True, text=True, env={**os.environ, "XDG_STATE_HOME": state}, check=True)
        modules = loaded.stdout.split()
        self.assertIn("dispatch", modules)
        self.assertIn("checks", modules)
        code = merges.dispatcher_code(LIB.parent)
        for name in modules:
            self.assertTrue(merges.runs_from({f"lib/{name}.py"}, code), f"a change to lib/{name}.py restarts nothing")

    def test_a_landing_that_changes_a_module_the_dispatcher_imports_restarts_it(self):
        (self.main / "lib").mkdir()
        self.commit(self.main, {"lib/dispatch.py": "import run_units\n", "lib/run_units.py": "import branch_watch\n"},
                    "add the dispatcher")
        calls = []

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        for name, restarts in (("lib/branch_watch.py", True), ("lib/web_only.py", False), ("lib/run_units.py", True)):
            calls.clear()
            before = self.head("main")
            after = self.commit(self.main, {name: "x\n"}, f"change {name}")
            merges.after_landing(self.main, [("main", before, after)], self.said.append, desk_root=self.main, run=run)
            self.assertEqual(["systemctl", "--user", "restart", merges.DISPATCH_UNIT] in calls, restarts, name)

if __name__ == "__main__":
    unittest.main()
