"""Tests for agent-desk/lib/branch_watch.py, which records each move of a project's agent/* heads and checks the newest
head, and for lib/checks.py's run of one check in its unit."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import branch_watch  # noqa: E402
import checks  # noqa: E402
import dispatch  # noqa: E402
import merges  # noqa: E402
from test_dispatch import REPO, DispatchCase  # noqa: E402


class GitCase(DispatchCase):
    """A project whose main checkout is a git repository with a commit on main, and checks that log and work in the
    test's folder."""

    def setUp(self):
        super().setUp()
        for patch in (mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}),
                      mock.patch.object(checks, "LOGS", self.folder / "logs"),
                      mock.patch.object(checks, "WORK", self.folder / "work")):
            patch.start()
            self.addCleanup(patch.stop)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.commit("main", "a.txt", "base\n")

    def git(self, *args):
        done = subprocess.run(["git", "-C", str(self.checkout), *args], capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def commit(self, branch, name, text):
        """Commits `text` to file `name` on `branch`, cut from main when it's new, and returns the new head."""
        if branch != "main":
            known = subprocess.run(["git", "-C", str(self.checkout), "rev-parse", "-q", "--verify",
                                    f"refs/heads/{branch}"], capture_output=True).returncode == 0
            self.git("checkout", "-q", *([branch] if known else ["-b", branch, "main"]))
        (self.checkout / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", f"Change {name}")
        head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return head

    def wait(self, seconds):
        """Lets `seconds` pass a second at a time, with a wake each second."""
        for _ in range(seconds):
            self.time += 1
            self.dispatcher.tick()

    def settle(self):
        """Lets a head that moves now settle: the next wake reads it, and its check starts SETTLE_SECONDS later."""
        self.wait(branch_watch.SETTLE_SECONDS + 1)

    def moves(self):
        return [event["payload"] for event in self.store.events_since(0) if event["type"] == "branch-moved"]


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
        self.assertEqual(args[args.index("--") + 3:], [str(dispatch.LIB / "checks.py"), "run", str(check["id"]),
                                                       str(self.checkout)])
        self.assertEqual(self.systemd.env()["AGENT_DESK_UNIT"], unit)

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
        self.assertEqual(len(self.store.checks(("queued",))), 2)
        self.hold = {"state": "running", "picked": []}
        self.wait(1)
        [first] = self.store.checks(("running",))
        self.store.check_ended(first["id"], "passed")
        self.wait(1)
        self.assertEqual([check["branch"] for check in self.store.checks(("running",))], ["agent/issue-6"])

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
        self.assertTrue(self.log(check).startswith(f"Check {check} of agent/issue-5 at {head} merged with local-desk,"))
        self.assertIn("== Merge local-desk\n", self.log(check))

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

    def test_the_exit_handler_leaves_a_recorded_end_alone(self):
        check = self.queue(self.commit("agent/issue-5", "b.txt", "work\n"))
        self.store.branch_moved(REPO, "agent/issue-5", None, "x")
        checks.stopped(self.store, check, str(self.checkout), {"SERVICE_RESULT": "success", "EXIT_CODE": "killed"})
        self.assertEqual(self.store.check(check)["status"], "superseded")


if __name__ == "__main__":
    unittest.main()
