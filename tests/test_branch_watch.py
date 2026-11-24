"""Tests for agent-desk/lib/branch_watch.py, which records each move of a project's agent/* heads and checks the newest
head, and for lib/checks.py's run of one check in its unit."""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import branch_watch  # noqa: E402
import checks  # noqa: E402
import merges  # noqa: E402
import run_units  # noqa: E402
from dispatch_case import REPO, GitCase  # noqa: E402
from test_worktree import fake_cp  # noqa: E402


class WatchTest(GitCase):
    def setUp(self):
        super().setUp()
        self.configure({"test": "just test"})

    def test_a_moved_head_is_recorded_and_checked_once_it_stands_still_for_a_minute(self):
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.wait(1)
        self.assertEqual(self.moves(), [{"branch": "agent/issue-5", "head": head, "before": None}])
        self.wait(branch_watch.SETTLE_SECONDS - 1)
        self.assertEqual(self.store.checks(("queued", "running")), [])
        self.wait(1)
        [check] = self.store.checks(("running",))
        self.assertEqual((check["branch"], check["head_sha"], check["name"], check["attempt"]),
                         ("agent/issue-5", head, "test", 1))
        self.assertEqual(Path(check["log_path"]), self.folder / "logs" / "org__game" / f"{check['id']}.log")
        unit = f"agent-desk-check-org-game-{check['id']}.service"
        self.assertEqual(self.systemd.units(), [unit])
        properties = self.systemd.properties()
        for setting in ("Nice=10", "CPUWeight=20", "IOWeight=20", "RuntimeMaxSec=15min"):
            self.assertIn(setting, properties)
        self.assertIn('checks.py" "stopped"', next(p for p in properties if p.startswith("ExecStopPost=")))
        args = self.systemd.started[0]
        self.assertEqual(args[args.index("--slice") + 1], "agentdesk.slice")
        self.assertEqual(args[args.index("--") + 3:], [str(run_units.LIB / "checks.py"), "run", str(check["id"]),
                                                       str(self.checkout)])
        self.assertEqual(self.systemd.env()["AGENT_DESK_UNIT"], unit)
        self.assertEqual(self.systemd.env()["TMPDIR"], f"/tmp/ad-check-{check['id']}")

    def test_a_check_takes_the_projects_test_minutes_as_its_time_limit(self):
        self.configure({"test": "just test", "testMinutes": 30})
        self.commit("agent/issue-5", "b.txt", "work\n")
        self.settle()
        self.assertEqual(len(self.store.checks(("running",))), 1)
        self.assertIn("RuntimeMaxSec=30min", self.systemd.properties())

    def test_a_check_keeps_15_minutes_when_test_minutes_isnt_a_whole_number_above_0(self):
        for minutes in (0, -5, 2.5, "30", True, None):
            self.assertEqual(checks.test_minutes({"testMinutes": minutes}), 15)
        self.assertEqual(checks.test_minutes({}), 15)
        self.assertEqual(checks.test_minutes({"testMinutes": 45}), 45)

    def test_a_head_is_checked_as_soon_as_the_run_working_its_branch_ends(self):
        run = self.request("agent", "5")
        self.dispatcher.tick()
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.wait(10)
        self.assertEqual(self.store.checks(("queued", "running")), [])
        self.end(run)
        self.wait(1)
        [check] = self.store.checks(("running",))
        self.assertEqual(check["head_sha"], head)

    def test_a_discussion_of_the_issue_works_no_branch_so_its_head_settles_as_usual(self):
        self.request("agent", "idea-5")
        self.dispatcher.tick()
        self.assertEqual(branch_watch.run_branch(self.store, {"project": REPO, "kind": "agent", "target": "idea-5"}),
                         None)
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.wait(1)
        self.wait(branch_watch.SETTLE_SECONDS)
        [check] = self.store.checks(("running",))
        self.assertEqual(check["head_sha"], head)

    def test_a_branch_that_keeps_moving_waits_for_its_newest_head(self):
        self.commit("agent/issue-5", "b.txt", "one\n")
        self.wait(30)
        head = self.commit("agent/issue-5", "b.txt", "two\n")
        self.wait(branch_watch.SETTLE_SECONDS)
        self.assertEqual(self.store.checks(("queued", "running")), [])
        self.wait(1)
        self.assertEqual([check["head_sha"] for check in self.store.checks(("running",))], [head])

    def test_a_new_head_supersedes_and_stops_the_check_of_the_old_one(self):
        old = self.commit("agent/issue-5", "b.txt", "one\n")
        self.settle()
        [running] = self.store.checks(("running",))
        new = self.commit("agent/issue-5", "b.txt", "two\n")
        self.wait(1)
        self.assertEqual(self.store.check(running["id"])["status"], "superseded")
        self.assertEqual(self.systemd.stopped, [f"agent-desk-check-org-game-{running['id']}.service"])
        self.assertEqual(self.moves()[-1], {"branch": "agent/issue-5", "head": new, "before": old})
        self.settle()
        self.assertEqual([check["head_sha"] for check in self.store.checks(("running",))], [new])

    def test_a_deleted_branch_supersedes_its_check(self):
        self.commit("agent/issue-5", "b.txt", "one\n")
        self.settle()
        [running] = self.store.checks(("running",))
        self.git("branch", "-q", "-D", "agent/issue-5")
        self.wait(1)
        self.assertEqual(self.store.check(running["id"])["status"], "superseded")
        self.assertEqual(self.moves()[-1]["head"], None)
        self.assertEqual(self.store.branch_heads(REPO), {})

    def test_an_empty_test_command_or_a_head_main_holds_gets_no_check(self):
        self.configure({"test": ""})
        self.commit("agent/issue-5", "b.txt", "work\n")
        self.git("branch", "agent/issue-6", "main")
        self.settle()
        self.assertEqual(len(self.moves()), 2)
        self.configure({"test": "just test"})
        self.commit("agent/issue-5", "b.txt", "more\n")
        self.settle()
        self.assertEqual([check["branch"] for check in self.store.checks(("running",))], ["agent/issue-5"])

    def test_a_head_its_review_base_holds_gets_no_check_though_main_lacks_it(self):
        self.commit("local-desk", "desk.txt", "desk\n")
        self.git("branch", "agent/issue-5", "local-desk")
        self.git("branch", "agent/issue-6", "local-desk")
        self.store.create(REPO, "review", "Five", branch="agent/issue-5", base="local-desk")
        self.settle()
        self.assertEqual([check["branch"] for check in self.store.checks(("running",))], ["agent/issue-6"])

    def test_checks_wait_for_a_free_check_slot_and_while_the_desk_holds(self):
        self.configure({"test": "just test", "slots": {"check": 1}})
        self.hold = {"state": "held", "picked": []}
        self.commit("agent/issue-5", "b.txt", "five\n")
        self.commit("agent/issue-6", "b.txt", "six\n")
        self.settle()
        waits = lambda: [(check["wait_gate"], check["wait_reason"]) for check in self.store.checks(("queued",))]
        self.assertEqual(waits(), [("mode", "Resume")] * 2)
        self.hold = {"state": "running", "picked": []}
        self.wait(1)
        [first] = self.store.checks(("running",))
        # The check that started clears its reason, and the one left records the slot it waits for.
        self.assertEqual((first["wait_gate"], first["wait_reason"]), (None, None))
        self.assertEqual(waits(), [("slot", "a free check slot")])
        self.store.check_ended(first["id"], "passed")
        self.wait(1)
        [second] = self.store.checks(("running",))
        self.assertEqual((second["branch"], second["wait_gate"]), ("agent/issue-6", None))
        self.assertEqual(sum(" waits: " in line for line in self.said), 3)

    def test_a_check_slot_override_holds_checks_past_its_count(self):
        self.configure({"test": "just test", "slots": {"check": 2}})
        self.hold = {"state": "running", "picked": [], "slots": {REPO: {"check": 1}}}
        self.commit("agent/issue-5", "b.txt", "five\n")
        self.commit("agent/issue-6", "b.txt", "six\n")
        self.settle()
        self.assertEqual(len(self.store.checks(("running",))), 1)
        [waiting] = self.store.checks(("queued",))
        self.assertEqual((waiting["wait_gate"], waiting["wait_reason"]), ("slot", "a free check slot"))
        self.assertIn(f"Check {waiting['id']} of {waiting['branch']} waits: its project's 1 check slots are busy.",
                      self.said)

    def checked(self, status):
        return sorted(check["branch"] for check in self.store.checks((status,)))

    def test_a_held_project_starts_the_check_of_a_working_agent_run_and_holds_the_rest_for_resume(self):
        self.request("agent", "5")
        self.dispatcher.tick()
        self.hold = {"state": "running", "picked": [], "projects": {REPO: "held"}}
        self.commit("agent/issue-5", "b.txt", "five\n")
        self.commit("agent/issue-6", "b.txt", "six\n")
        self.settle()
        self.assertEqual(self.checked("running"), ["agent/issue-5"])
        self.assertEqual(self.checked("queued"), ["agent/issue-6"])
        self.hold = {"state": "running", "picked": []}
        self.wait(1)
        self.assertEqual(self.checked("running"), ["agent/issue-5", "agent/issue-6"])

    def test_a_held_project_starts_the_check_of_a_working_fix_run_within_its_check_slots(self):
        self.configure({"test": "just test", "slots": {"check": 1}})
        review = self.store.create(REPO, "review", "Five", branch="agent/issue-5")
        self.request("fix", str(review))
        self.request("agent", "7")
        self.dispatcher.tick()
        self.assertEqual(len(self.dispatcher.running), 2)
        self.hold = {"state": "held", "picked": []}
        for branch in ("agent/issue-5", "agent/issue-6", "agent/issue-7"):
            self.commit(branch, "b.txt", f"{branch}\n")
        self.settle()
        [first] = self.store.checks(("running",))
        self.assertIn(first["branch"], ("agent/issue-5", "agent/issue-7"))
        self.store.check_ended(first["id"], "passed")
        self.wait(1)
        self.assertEqual(self.checked("running"), sorted({"agent/issue-5", "agent/issue-7"} - {first["branch"]}))
        self.assertEqual(self.checked("queued"), ["agent/issue-6"])

    def test_a_restart_checks_a_move_made_while_the_dispatcher_was_down_and_ends_a_vanished_check(self):
        self.commit("agent/issue-5", "b.txt", "one\n")
        self.settle()
        [running] = self.store.checks(("running",))
        self.systemd.live.clear()
        head = self.commit("agent/issue-6", "b.txt", "six\n")
        self.dispatcher = self.new_dispatcher()
        self.assertEqual(self.store.check(running["id"])["status"], "lost")
        self.settle()
        self.assertEqual([check["head_sha"] for check in self.store.checks(("running",))], [head])
        self.assertEqual([move["branch"] for move in self.moves()], ["agent/issue-5", "agent/issue-6"])

    def test_a_restart_inside_the_settle_window_still_checks_the_head_a_minute_later(self):
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.wait(30)
        self.assertEqual(self.moves(), [{"branch": "agent/issue-5", "head": head, "before": None}])
        self.dispatcher = self.new_dispatcher()
        self.wait(branch_watch.SETTLE_SECONDS - 1)
        self.assertEqual(self.store.checks(("queued", "running")), [])
        self.wait(1)
        self.assertEqual([check["head_sha"] for check in self.store.checks(("running",))], [head])
        self.assertEqual(len(self.moves()), 1)

    def test_a_restart_queues_no_check_of_a_head_already_checked_or_held_by_its_base(self):
        self.commit("agent/issue-5", "b.txt", "work\n")
        self.git("branch", "agent/issue-6", "main")
        self.settle()
        [check] = self.store.checks(("running",))
        self.store.check_ended(check["id"], "passed")
        self.dispatcher = self.new_dispatcher()
        self.settle()
        self.assertEqual([row["id"] for row in self.store.checks(("queued", "running", "passed"))], [check["id"]])

    def test_each_open_review_is_tried_against_its_base_whenever_either_moves_without_a_test_command(self):
        self.configure({"test": ""})
        review = self.store.create(REPO, "review", "Five", branch="agent/issue-5")

        def mergeable():
            return {one["number"]: one for one in self.store.board(REPO)["reviews"]}[review]["mergeable"]

        self.commit("agent/issue-5", "a.txt", "five\n")
        self.wait(1)
        self.assertEqual(mergeable(), "MERGEABLE")
        # Main changed the same line, so the head conflicts until the branch takes main in.
        main = self.commit("main", "a.txt", "main\n")
        self.wait(1)
        self.assertEqual(mergeable(), "CONFLICTING")
        branch = self.store.branch(REPO, "agent/issue-5")
        self.assertEqual((branch["base"], branch["base_sha"]), ("main", main))
        self.git("checkout", "-q", "agent/issue-5")
        self.git("merge", "-q", "-X", "theirs", "main")
        self.git("checkout", "-q", "main")
        self.wait(1)
        self.assertEqual(mergeable(), "MERGEABLE")
        tries = [event["payload"]["mergeable"] for event in self.store.events_since(0)
                 if event["type"] == "branch-merges"]
        self.assertEqual(tries, ["MERGEABLE", "CONFLICTING", "MERGEABLE"])
        self.wait(5)
        self.assertEqual(len([one for one in self.store.events_since(0) if one["type"] == "branch-merges"]), 3)
        self.assertEqual(self.store.checks(("queued", "running")), [])

    def test_a_review_into_another_base_is_tried_against_that_base_and_a_closed_one_is_left_alone(self):
        self.configure({"test": ""})
        self.commit("agent/issue-4", "a.txt", "four\n")
        stacked = self.store.create(REPO, "review", "Six", branch="agent/issue-6", base="agent/issue-4")
        closed = self.store.create(REPO, "review", "Seven", branch="agent/issue-7")
        self.store.set_state(REPO, closed, "closed")
        self.commit("agent/issue-6", "a.txt", "six\n")
        self.commit("agent/issue-7", "a.txt", "seven\n")
        self.wait(1)
        self.assertEqual(self.store.branch(REPO, "agent/issue-6")["base"], "agent/issue-4")
        self.assertEqual(self.store.branch(REPO, "agent/issue-6")["mergeable"], "CONFLICTING")
        self.assertIsNone(self.store.branch(REPO, "agent/issue-7")["mergeable"])
        self.assertEqual(self.store.item(REPO, stacked, "review")["mergeable"], "CONFLICTING")

    def test_a_conflict_beside_a_folder_both_sides_changed_is_conflicting(self):
        # git 2.56's `merge-tree --write-tree --quiet` exits 0 on this pair, though the full merge conflicts in a.txt.
        self.configure({"test": ""})
        (self.checkout / "t").mkdir()
        self.commit("main", "t/x.txt", "x\n")
        self.commit("main", "t/y.txt", "y\n")
        review = self.store.create(REPO, "review", "Five", branch="agent/issue-5")
        self.commit("agent/issue-5", "a.txt", "five\n")
        head = self.commit("agent/issue-5", "t/y.txt", "y from the branch\n")
        self.commit("main", "a.txt", "main\n")
        main = self.commit("main", "t/x.txt", "x from main\n")
        self.assertEqual(checks.merges_with(self.checkout, head, main), "CONFLICTING")
        self.wait(1)
        self.assertEqual(self.store.item(REPO, review, "review")["mergeable"], "CONFLICTING")


class RecheckTest(GitCase):
    """The re-check of a ready review whose base moved after its head's check passed, with `testOnBaseMove` on."""

    def setUp(self):
        super().setUp()
        self.configure({"test": "just test", "testOnBaseMove": True})
        self.review = self.store.create(REPO, "review", "Five", branch="agent/issue-5")
        self.head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.settle()
        [check] = self.store.checks(("running",))
        self.finish(check["id"], self.git("rev-parse", "main"))

    def finish(self, check, base_sha, status="passed"):
        """Ends a running check as its unit does once it merged main at `base_sha`."""
        if not self.store.check(check)["base_sha"]:
            self.store.check_merges(check, "main", base_sha)
        self.store.check_ended(check, status)
        self.systemd.live.discard(checks.unit_name(REPO, check))

    def rechecks(self, statuses=("queued", "running")):
        return [row for row in self.store.checks(statuses) if row["recheck"]]

    def test_a_base_that_moves_once_gets_one_re_check_of_the_head_merged_with_it_a_minute_later(self):
        main = self.commit("main", "c.txt", "main\n")
        self.wait(1)
        self.assertEqual(self.rechecks(), [])
        self.wait(branch_watch.SETTLE_SECONDS)
        [recheck] = self.rechecks(("running",))
        self.assertEqual((recheck["head_sha"], recheck["base"], recheck["base_sha"], recheck["attempt"]),
                         (self.head, "main", main, 1))
        self.assertEqual(self.store.head_checks(REPO, self.head)[0]["id"], recheck["id"])
        self.finish(recheck["id"], main)
        self.settle()
        self.assertEqual([row["id"] for row in self.store.checks(("queued", "running", "passed"))
                          if row["recheck"]], [recheck["id"]])

    def test_a_base_that_moves_three_times_in_a_row_gets_one_re_check_of_its_newest_commit(self):
        for name in ("c.txt", "d.txt", "e.txt"):
            newest = self.commit("main", name, "main\n")
            self.wait(20)
        self.assertEqual(self.rechecks(), [])
        self.wait(branch_watch.SETTLE_SECONDS)
        [recheck] = self.rechecks(("running",))
        self.assertEqual(recheck["base_sha"], newest)
        # A move while it runs supersedes it and stops its unit, and the newer commit gets a re-check of its own.
        newer = self.commit("main", "f.txt", "main\n")
        self.wait(1)
        self.assertEqual(self.store.check(recheck["id"])["status"], "superseded")
        self.assertEqual(self.systemd.stopped, [checks.unit_name(REPO, recheck["id"])])
        self.wait(branch_watch.SETTLE_SECONDS)
        [again] = self.rechecks(("running",))
        self.assertEqual(again["base_sha"], newer)
        # The head's own result stands while the re-check runs.
        [shown] = self.store.newest_checks(REPO, "agent/issue-5", self.head)
        self.assertEqual((shown["status"], shown["recheck"]), ("passed", False))

    def test_a_review_that_conflicts_with_its_moved_base_gets_no_re_check(self):
        self.commit("main", "b.txt", "main's own\n")
        self.wait(branch_watch.SETTLE_SECONDS + 1)
        self.assertEqual(self.store.branch(REPO, "agent/issue-5")["mergeable"], "CONFLICTING")
        self.assertEqual(self.rechecks(), [])

    def test_a_project_with_the_key_off_gets_no_re_check_and_cancels_one_queued(self):
        self.configure({"test": "just test"})
        self.commit("main", "c.txt", "main\n")
        self.wait(branch_watch.SETTLE_SECONDS + 1)
        self.assertEqual(self.rechecks(), [])
        queued = self.store.check_queue(REPO, "agent/issue-5", self.head, "main", self.git("rev-parse", "main"),
                                        recheck=True)
        self.wait(1)
        self.assertEqual(self.store.check(queued)["status"], "cancelled")

    def test_a_draft_a_review_an_agent_works_and_a_head_that_failed_get_no_re_check(self):
        self.store.review_update(REPO, self.review, draft=True)
        self.commit("main", "c.txt", "main\n")
        self.wait(branch_watch.SETTLE_SECONDS + 1)
        self.assertEqual(self.rechecks(), [])
        self.store.review_update(REPO, self.review, draft=False)
        run = self.request("agent", "5")
        self.wait(branch_watch.SETTLE_SECONDS + 1)
        self.assertEqual(self.rechecks(), [])
        self.end(run)
        self.store.create(REPO, "review", "Six", branch="agent/issue-6")
        self.commit("agent/issue-6", "g.txt", "six\n")
        self.settle()
        [check] = [row for row in self.store.checks(("running",)) if row["branch"] == "agent/issue-6"]
        self.finish(check["id"], self.git("rev-parse", "main"), "failed")
        self.commit("main", "d.txt", "main\n")
        self.wait(branch_watch.SETTLE_SECONDS + 1)
        # Once its run ends, the ready review of agent/issue-5 gets its re-check, and the failed head none.
        self.assertEqual([row["branch"] for row in self.rechecks()], ["agent/issue-5"])

    def test_a_re_check_starts_after_the_check_of_a_moved_head_and_waits_for_a_free_slot(self):
        self.configure({"test": "just test", "testOnBaseMove": True, "slots": {"check": 1}})
        self.commit("agent/issue-6", "g.txt", "six\n")
        self.settle()
        [six] = self.store.checks(("running",))
        self.commit("main", "c.txt", "main\n")
        self.wait(branch_watch.SETTLE_SECONDS + 1)
        [recheck] = self.rechecks(("queued",))
        self.assertEqual((recheck["wait_gate"], recheck["wait_reason"]), ("slot", "a free check slot"))
        self.commit("agent/issue-7", "h.txt", "seven\n")
        self.settle()
        self.finish(six["id"], self.git("rev-parse", "main"))
        self.wait(1)
        [running] = self.store.checks(("running",))
        self.assertEqual(running["branch"], "agent/issue-7")
        self.finish(running["id"], self.git("rev-parse", "main"))
        self.wait(1)
        self.assertEqual([row["id"] for row in self.store.checks(("running",))], [recheck["id"]])


class CheckRunTest(GitCase):
    """lib/checks.py's run of one check in its unit."""

    def queue(self, head, branch="agent/issue-5"):
        check = self.store.check_queue(REPO, branch, head)
        self.store.check_started(check, checks.log_path(REPO, check))
        return check

    def run_check(self, check):
        with mock.patch("sys.stdout"):
            checks.run_main(self.store, check, str(self.checkout))
        return self.store.check(check)

    def log(self, check):
        return checks.log_path(REPO, check).read_text()

    def worktrees(self):
        return self.git("worktree", "list").count("\n") + 1

    def test_a_check_tests_the_head_merged_with_main_with_the_seeds_and_setup(self):
        (self.checkout / "cache").mkdir()
        (self.checkout / "cache" / "seed.txt").write_text("seeded\n")
        self.configure({"test": "grep -q main a.txt && grep -q work b.txt && test -f cache/seed.txt && test -f built",
                        "worktree": {"seeds": ["cache", "missing"], "setup": "touch built"}})
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.commit("main", "a.txt", "main\n")
        check = self.queue(head)
        row = self.run_check(check)
        self.assertEqual((row["status"], row["name"]), ("passed", "test"), self.log(check))
        self.assertIsNotNone(row["ended_at"])
        self.assertIn("== Test: grep", self.log(check))
        self.assertFalse(checks.work_path(REPO, check).exists())
        self.assertEqual(self.worktrees(), 1)
        self.assertEqual(self.store.checks(("queued",)), [])

    def test_a_seed_copy_that_meets_a_vanished_file_copies_again_and_a_copy_that_keeps_meeting_one_fails_setup(self):
        (self.checkout / "target").mkdir()
        (self.checkout / "target" / "app").write_text("built\n")
        self.configure({"test": "test -f target/app && test -f built", "worktree": {"seeds": ["target"],
                                                                                    "setup": "touch built"}})
        fakes = self.folder / "fakes"
        fakes.mkdir()
        path = {"PATH": f"{fakes}:{os.environ['PATH']}"}
        for failures, outcome in ((1, ("passed", "test")), (3, ("failed", "setup"))):
            calls = fake_cp(fakes, failures)
            calls.unlink(missing_ok=True)
            check = self.queue(self.commit("agent/issue-5", "b.txt", f"{failures}\n"))
            with mock.patch.dict(os.environ, path):
                row = self.run_check(check)
            self.assertEqual((row["status"], row["name"]), outcome, self.log(check))
            self.assertEqual(len(calls.read_text().splitlines()), min(failures + 1, 3))
            self.assertIn("Pass 1 of 3 met files gone from", self.log(check))
            self.assertFalse(checks.work_path(REPO, check).exists())

    def test_a_merge_conflict_fails_the_check_as_merge_without_testing_or_a_rerun(self):
        self.configure({"test": "touch $HOME/tested"})
        head = self.commit("agent/issue-5", "a.txt", "branch\n")
        self.commit("main", "a.txt", "main\n")
        check = self.queue(head)
        with mock.patch.dict(os.environ, {"HOME": str(self.folder)}):
            row = self.run_check(check)
        self.assertEqual((row["status"], row["name"]), ("failed", "merge"))
        self.assertIn("CONFLICT", self.log(check))
        self.assertFalse((self.folder / "tested").exists())
        self.assertEqual(self.store.checks(("queued",)), [])
        self.assertEqual(self.worktrees(), 1)

    def test_a_check_of_a_review_on_another_base_merges_that_base_from_origin_when_it_has_no_local_branch(self):
        self.configure({"test": "test -f desk.txt && test -f b.txt && test ! -f main.txt"})
        self.commit("local-desk", "desk.txt", "desk\n")
        self.git("branch", "agent/issue-5", "local-desk")
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.commit("main", "main.txt", "main\n")
        # The main checkout knows local-desk only as origin's, as agent-desk's own checkout does.
        self.git("update-ref", "refs/remotes/origin/local-desk", "local-desk")
        self.git("branch", "-q", "-D", "local-desk")
        self.store.create(REPO, "review", "Five", branch="agent/issue-5", base="local-desk")
        check = self.queue(head)
        row = self.run_check(check)
        self.assertEqual((row["status"], row["name"]), ("passed", "test"), self.log(check))
        desk = self.git("rev-parse", "origin/local-desk")
        self.assertTrue(self.log(check).startswith(f"Check {check} of agent/issue-5 at {head} merged with local-desk at"
                                                   f" {desk}, attempt 1."))
        self.assertEqual((row["base"], row["base_sha"], row["recheck"]), ("local-desk", desk, 0))
        self.assertIn("== Merge local-desk\n", self.log(check))

    def test_a_re_check_merges_the_commit_of_its_base_it_names_and_records_nothing_newer(self):
        self.configure({"test": "test -f c.txt && test ! -f d.txt"})
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        named = self.commit("main", "c.txt", "main\n")
        self.commit("main", "d.txt", "main\n")
        check = self.store.check_queue(REPO, "agent/issue-5", head, "main", named, recheck=True)
        self.store.check_started(check, checks.log_path(REPO, check))
        row = self.run_check(check)
        self.assertEqual((row["status"], row["base_sha"]), ("passed", named), self.log(check))
        self.assertTrue(self.log(check).startswith(
            f"Check {check} of agent/issue-5 at {head} merged with main at {named}, attempt 1, a re-check after the"
            " base moved."))

    def test_a_branch_without_an_open_review_merges_main(self):
        self.configure({"test": "test -f main.txt"})
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        self.commit("local-desk", "desk.txt", "desk\n")
        self.commit("main", "main.txt", "main\n")
        closed = self.store.create(REPO, "review", "Five", branch="agent/issue-5", base="local-desk")
        self.store.set_state(REPO, closed, "closed")
        check = self.queue(head)
        self.assertEqual(self.run_check(check)["status"], "passed", self.log(check))
        self.assertIn("== Merge main\n", self.log(check))

    def test_a_merge_queue_branch_merges_the_base_of_the_batch_that_made_it(self):
        with mock.patch.object(merges, "STATE", self.folder / "merges"):
            self.assertEqual(checks.base_of(self.store, REPO, "merge-queue/1"), "main")
            path = merges.state_path(REPO)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"queue": [], "batch": {"branch": "merge-queue/1", "base": "local-desk"}}))
            self.assertEqual(checks.base_of(self.store, REPO, "merge-queue/1"), "local-desk")
            self.assertEqual(checks.base_of(self.store, REPO, "merge-queue/2"), "main")

    def test_a_failed_check_runs_again_once_before_it_counts_as_failed(self):
        self.configure({"test": "echo 'FAILED test_widget' && exit 1"})
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        first = self.queue(head)
        self.assertEqual(self.run_check(first)["status"], "failed")
        [second] = self.store.checks(("queued",))
        self.assertEqual((second["head_sha"], second["attempt"], second["name"]), (head, 2, "test"))
        self.store.check_started(second["id"], checks.log_path(REPO, second["id"]))
        self.assertEqual(self.run_check(second["id"])["status"], "failed")
        self.assertEqual(self.store.checks(("queued",)), [])
        ended = [event["payload"] for event in self.store.events_since(0) if event["type"] == "check-ended"]
        self.assertEqual([(one["status"], one["attempt"]) for one in ended], [("failed", 1), ("failed", 2)])

    def test_a_timed_out_check_is_recorded_by_its_exit_handler_and_its_worktree_removed(self):
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        check = self.queue(head)
        work = checks.work_path(REPO, check)
        self.git("worktree", "add", "-q", "--detach", str(work), head)
        checks.stopped(self.store, check, str(self.checkout), {"SERVICE_RESULT": "timeout", "EXIT_CODE": "killed"})
        self.assertEqual(self.store.check(check)["status"], "timed-out")
        self.assertEqual([row["attempt"] for row in self.store.checks(("queued",))], [2])
        self.assertFalse(work.exists())
        self.assertEqual(self.worktrees(), 1)

    def test_a_check_runs_the_teardown_in_its_worktree_before_removing_it(self):
        torn = self.folder / "torn"
        self.configure({"test": "true", "worktree": {"setup": "touch built", "teardown": f"ls >{torn}; touch left"}})
        check = self.queue(self.commit("agent/issue-5", "b.txt", "work\n"))
        row = self.run_check(check)
        self.assertEqual(row["status"], "passed", self.log(check))
        self.assertEqual(torn.read_text().split(), ["a.txt", "b.txt", "built"])
        self.assertIn("== Tear down: ls", self.log(check))
        self.assertFalse(checks.work_path(REPO, check).exists())
        self.assertEqual(self.worktrees(), 1)

    def test_a_failed_teardown_is_logged_and_the_worktree_still_goes(self):
        self.configure({"test": "true", "worktree": {"teardown": "echo no database; exit 4"}})
        check = self.queue(self.commit("agent/issue-5", "b.txt", "work\n"))
        self.assertEqual(self.run_check(check)["status"], "passed", self.log(check))
        self.assertIn("no database\nThe worktree teardown failed with status 4.\n", self.log(check))
        self.assertFalse(checks.work_path(REPO, check).exists())
        self.assertEqual(self.worktrees(), 1)

    def test_a_teardown_past_its_limit_is_stopped_with_what_it_started(self):
        work = self.folder / "work"
        work.mkdir()
        self.configure({"worktree": {"teardown": f"sleep 30 & echo $! >{self.folder}/child; wait"}})
        log = self.folder / "teardown.log"
        with mock.patch.object(checks, "TEARDOWN_SECONDS", 0.5), open(log, "w") as out:
            checks.teardown(self.checkout, work, out)
        self.assertIn("The worktree teardown ran past 0.5s and was stopped.", log.read_text())
        # The killed sleep may stay a zombie for a moment, until the process that adopts it reaps it.
        stat = Path(f"/proc/{int((self.folder / 'child').read_text())}/stat")
        for _ in range(50):
            try:
                if stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
            except FileNotFoundError:
                break
            time.sleep(0.1)
        else:
            self.fail("the teardown's sleep outlived its limit")

    def test_a_project_without_a_teardown_runs_none(self):
        self.configure({"test": "true"})
        check = self.queue(self.commit("agent/issue-5", "b.txt", "work\n"))
        self.assertEqual(self.run_check(check)["status"], "passed", self.log(check))
        self.assertNotIn("Tear down", self.log(check))

    def test_the_exit_handler_runs_the_teardown_into_the_checks_log(self):
        self.configure({"worktree": {"teardown": "echo torn down"}})
        head = self.commit("agent/issue-5", "b.txt", "work\n")
        check = self.queue(head)
        work = checks.work_path(REPO, check)
        self.git("worktree", "add", "-q", "--detach", str(work), head)
        checks.stopped(self.store, check, str(self.checkout), {"SERVICE_RESULT": "timeout", "EXIT_CODE": "killed"})
        self.assertIn("== Tear down: echo torn down\ntorn down\n", self.log(check))
        self.assertFalse(work.exists())

    def test_the_exit_handler_leaves_a_recorded_end_alone(self):
        check = self.queue(self.commit("agent/issue-5", "b.txt", "work\n"))
        self.store.branch_moved(REPO, "agent/issue-5", None, "x")
        checks.stopped(self.store, check, str(self.checkout), {"SERVICE_RESULT": "success", "EXIT_CODE": "killed"})
        self.assertEqual(self.store.check(check)["status"], "superseded")


if __name__ == "__main__":
    unittest.main()
