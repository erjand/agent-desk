"""Tests for agent-desk/lib/run_control.py, which stops, cancels, and lists runs for the command line."""

import io
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch  # noqa: E402
import hold  # noqa: E402
import run_control  # noqa: E402
import store  # noqa: E402
from test_dispatch import REPO, DispatchCase  # noqa: E402


class StopTest(DispatchCase):
    def test_stop_ends_the_units_working_a_target_and_drops_its_waiting_requests(self):
        working = self.request("agent", "7")
        self.dispatcher.tick()
        waiting = self.request("agent", "7")
        other = self.request("agent", "8")
        self.dispatcher.tick()
        unit = self.store.run(working)["unit"]
        calls = []
        self.assertEqual(run_control.stop_runs(self.store, REPO, "agent", "7", dry=True, run=calls.append), [unit])
        self.assertEqual((calls, self.status(waiting)), ([], "requested"))
        self.assertEqual(run_control.stop_runs(self.store, REPO, "agent", "7", run=calls.append), [unit])
        self.assertEqual(calls, [["systemctl", "--user", "stop", "--no-block", unit]])
        self.assertEqual((self.status(waiting), self.status(other)), ("cancelled", "running"))
        self.assertEqual(run_control.stop_runs(self.store, "org/other", "agent", "8", run=calls.append), [])


class CancelTest(DispatchCase):
    def test_cancel_ends_one_waiting_request_and_leaves_every_other_run_alone(self):
        working = self.request("agent", "7")
        self.dispatcher.tick()
        waiting = self.request("agent", "7", env={"REQUEST": "a"})
        kept = self.request("agent", "7", env={"REQUEST": "b"})
        self.dispatcher.tick()
        self.assertEqual(run_control.cancel_run(self.store, waiting),
                         f"Cancelled run {waiting}, which waited to work agent 7 in {REPO}.")
        self.assertEqual([self.status(run) for run in (working, waiting, kept)], ["running", "cancelled", "requested"])
        self.assertEqual(self.systemd.stopped, [])
        self.dispatcher.tick()
        self.assertNotIn(waiting, self.dispatcher.pending)

    def test_cancel_refuses_a_run_that_works_or_has_ended(self):
        working = self.request("agent", "7")
        self.dispatcher.tick()
        unit = self.store.run(working)["unit"]
        ended = self.request("chore", "audit")
        self.store.run_ended(ended, "failed")
        for run, said in ((working, f"Run {working} works now in {unit}"),
                          (ended, f"Run {ended} ended already as failed")):
            with self.assertRaises(store.StoreError) as raised:
                run_control.cancel_run(self.store, run)
            self.assertIn(said, str(raised.exception))
        self.assertEqual((self.status(working), self.status(ended)), ("running", "failed"))
        err = io.StringIO()
        with mock.patch.object(store, "Store", lambda **_: self.store), mock.patch.object(sys, "stderr", err), \
                mock.patch.object(self.store, "close", lambda: None):
            self.assertEqual(dispatch.main(["cancel", str(working)]), 1)
        self.assertIn(f"agent-desk: Run {working} works now", err.getvalue())

    def test_a_run_that_starts_as_cancel_reads_it_stays_running(self):
        run = self.request("agent", "7")
        self.store.run_started(run, "agent-desk-unit")
        self.assertFalse(self.store.run_ended(run, "cancelled", waiting=True))
        self.assertEqual(self.status(run), "running")


class CancelWaitingTest(DispatchCase):
    def test_cancel_waiting_ends_the_issues_waiting_agent_requests_and_leaves_its_running_one(self):
        working = self.request("agent", "7")
        self.dispatcher.tick()
        waiting = [self.request("agent", "7", env={"REQUEST": "a"}), self.request("agent", "idea-7")]
        others = [self.request("agent", "8"), self.request("chore", "7")]
        self.assertEqual(run_control.cancel_waiting(self.store, REPO, 7), 2)
        self.assertEqual([self.status(run) for run in waiting], ["cancelled", "cancelled"])
        self.assertEqual(self.status(working), "running")
        self.assertEqual([self.status(run) for run in others], ["requested", "requested"])
        self.assertEqual(run_control.cancel_waiting(self.store, "org/other", 8), 0)
        self.assertEqual(self.status(others[0]), "requested")


class UnqueueTest(DispatchCase):
    def issue(self, status):
        number = self.store.create(REPO, "issue", "Work")
        self.store.set_state(REPO, number, status)
        return number

    def test_unqueue_cancels_the_waiting_requests_sets_open_and_drops_the_pick(self):
        for status in ("queued", "waiting"):
            number = self.issue(status)
            waiting = [self.request("agent", str(number), env={"EVENT": "issues"}),
                       self.request("agent", str(number), env={"REQUEST": "b"})]
            other = self.request("agent", str(number + 100))
            spent = []
            said = run_control.unqueue_issue(self.store, REPO, number,
                                             spend=lambda issue, repo: spent.append((issue, repo)))
            self.assertEqual(said, f"Moved #{number} to Open, cancelling 2 waiting runs.")
            self.assertEqual([self.status(run) for run in waiting], ["cancelled", "cancelled"])
            self.assertEqual(self.status(other), "requested")
            self.assertEqual(self.store.item(REPO, number)["status"], "open")
            self.assertEqual(spent, [(number, REPO)])

    def test_the_wake_asks_for_no_run_of_an_unqueued_issue(self):
        number = self.issue("waiting")
        run_control.unqueue_issue(self.store, REPO, number, spend=lambda issue, repo: None)
        self.dispatcher.wake()
        self.dispatcher.tick()
        self.assertEqual([one for one in self.store.runs(("requested", "running"))
                          if one["target"] == str(number)], [])

    def test_unqueue_refuses_an_issue_whose_run_started_and_changes_nothing(self):
        number = self.issue("queued")
        working = self.request("agent", str(number))
        self.dispatcher.tick()
        waiting = self.request("agent", str(number), env={"REQUEST": "b"})
        unit = self.store.run(working)["unit"]
        spent = []
        with self.assertRaises(store.StoreError) as raised:
            run_control.unqueue_issue(self.store, REPO, number, spend=lambda issue, repo: spent.append(issue))
        self.assertEqual(str(raised.exception), f"Couldn't move #{number} to Open: its run started in {unit}.")
        self.assertEqual((self.status(working), self.status(waiting)), ("running", "requested"))
        self.assertEqual((self.store.item(REPO, number)["status"], spent), ("queued", []))

    def test_unqueue_refuses_an_issue_that_is_neither_queued_nor_waiting(self):
        number = self.issue("in-progress")
        with self.assertRaises(store.StoreError) as raised:
            run_control.unqueue_issue(self.store, REPO, number, spend=lambda issue, repo: None)
        self.assertIn("it is in-progress, not queued or waiting", str(raised.exception))
        self.assertEqual(self.store.item(REPO, number)["status"], "in-progress")

    def test_the_unqueue_verb_moves_the_issue_and_drops_its_pick(self):
        number = self.issue("queued")
        self.request("agent", str(number), env={"EVENT": "issues"})
        spent, out = [], io.StringIO()
        with mock.patch.object(store, "Store", lambda **_: self.store), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(self.store, "close", lambda: None), \
                mock.patch.object(hold, "spend", lambda issue, repo: spent.append((issue, repo))):
            self.assertEqual(dispatch.main(["unqueue", str(number), "--repo", REPO]), 0)
        self.assertEqual(out.getvalue(), f"Moved #{number} to Open, cancelling 1 waiting run.\n")
        self.assertEqual((self.store.item(REPO, number)["status"], spent), ("open", [(number, REPO)]))


class AgainTest(DispatchCase):
    def test_in_manual_again_on_a_labelled_run_picks_nothing_and_waits_for_start(self):
        self.hold = {"state": "manual", "picked": [f"{REPO}#7"]}
        first = self.request("agent", "7", env={"EVENT": "issues"})
        self.dispatcher.tick()
        self.assertEqual(self.status(first), "running")
        self.hold["picked"].clear()
        self.store.run_ended(first, "failed", 1)
        again = self.store.request_again(first)
        self.dispatcher.tick()
        self.assertEqual((self.status(again), self.hold["picked"]), ("requested", []))
        self.assertEqual(self.store.request_of(again)["payload"]["env"], {"EVENT": "issues"})
        self.hold["picked"].append(f"{REPO}#7")
        self.dispatcher.tick()
        self.assertEqual(self.status(again), "running")

    def test_in_manual_again_on_an_agent_comment_chore_or_fix_waits_for_start(self):
        self.hold = {"state": "manual", "picked": [], "picked_runs": []}
        runs = [self.request("chore", "audit"), self.request("fix", "8"),
                self.request("agent", "7", env={"EVENT": "issue_comment", "REQUEST": "try again"})]
        for run in runs:
            self.store.run_ended(run, "failed", 1)
        again = [self.store.request_again(run) for run in runs]
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in again], ["requested"] * 3)
        self.assertEqual((self.hold["picked"], self.hold["picked_runs"]), ([], []))
        self.hold = {"state": "manual", "picked": [], "picked_runs": again}
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in again], ["running"] * 3)

    def test_the_again_verb_asks_for_the_run_and_picks_nothing(self):
        failed = self.request("agent", "7", env={"EVENT": "issues"})
        self.store.run_ended(failed, "failed", 1)
        picked, out = [], io.StringIO()
        with mock.patch.object(store, "Store", lambda **_: self.store), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(self.store, "close", lambda: None), \
                mock.patch.object(hold, "pick", lambda issue, repo: picked.append((issue, repo))):
            self.assertEqual(dispatch.main(["again", str(failed)]), 0)
        self.assertEqual(picked, [])
        self.assertEqual(self.status(int(out.getvalue())), "requested")


class LiveRunsTest(DispatchCase):
    def test_the_board_reads_a_projects_waiting_and_working_runs(self):
        working = self.request("agent", "7")
        self.dispatcher.tick()
        waiting = self.request("agent", "7")
        ended = self.request("chore", "audit")
        self.dispatcher.tick()
        self.store.run_ended(ended, "succeeded", 0)
        listed = self.store.live_runs(REPO)
        self.assertEqual([(one["id"], one["kind"], one["target"], one["status"]) for one in listed],
                         [(working, "agent", "7", "running"), (waiting, "agent", "7", "requested")])
        self.assertEqual(listed[0]["unit"], self.store.run(working)["unit"])
        self.assertTrue(listed[0]["started_at"])
        self.assertEqual((listed[0]["benchmark"], listed[1]["unit"]), (False, None))
        # The request that waits names the gate that holds it and why; the run that works names none.
        self.assertEqual((listed[1]["gate"], listed[1]["reason"]), ("lock", f"agent run {working} to end"))
        self.assertNotIn("gate", listed[0])
        self.assertEqual(self.store.live_runs("org/other"), [])

    def test_each_working_run_lists_the_limit_its_unit_runs_under(self):
        self.configure({"limitMinutes": {"agent": 360, "chore": 75}})
        runs = [self.request("agent", "7"), self.request("agent", "8", benchmark=True), self.request("fix", "9"),
                self.request("chore", "audit"), self.request("agent", "idea-10")]
        waiting = self.request("agent", "7")
        self.dispatcher.tick()
        listed = {one["id"]: one for one in self.store.live_runs(REPO)}
        self.assertEqual([listed[run]["limit"] for run in runs], [360, 300, 90, 75, dispatch.DISCUSS_MINUTES])
        self.assertNotIn("limit", listed[waiting])
        # A run started before its event recorded a limit lists none, so the board falls back to the built-in ones.
        older = self.store.request_run(REPO, "fix", "11")
        self.store.run_started(older, "agent-desk-fix-org-game-11-1.service")
        self.assertNotIn("limit", next(one for one in self.store.live_runs(REPO) if one["id"] == older))

    def test_each_run_says_whether_it_is_a_fresh_start_that_manual_holds_for_start(self):
        runs = [self.request("agent", "7", env={"EVENT": "issues"}),
                self.request("agent", "8", env={"EVENT": "issue_comment"}), self.request("agent", "9"),
                self.request("agent", "pr-4", env={"EVENT": "issues"}), self.request("fix", "5")]
        listed = {one["id"]: one["labelled"] for one in self.store.live_runs(REPO)}
        self.assertEqual([listed[run] for run in runs], [True, False, False, False, False])

    def test_a_failed_run_is_listed_for_a_day_until_a_later_run_asks_for_its_target(self):
        failed = self.request("agent", "7", benchmark=True, env={"EVENT": "issues"})
        timed = self.request("fix", "8")
        fine = self.request("chore", "audit")
        self.dispatcher.tick()
        self.store.run_ended(failed, "failed", 1)
        self.store.run_ended(timed, "timed-out")
        self.store.run_ended(fine, "cancelled")
        listed = self.store.live_runs(REPO)
        self.assertEqual([(one["id"], one["status"]) for one in listed], [(failed, "failed"), (timed, "timed-out")])
        self.assertTrue(listed[0]["ended_at"])
        later = time.time() + dispatch.FAILED_SECONDS + 60
        self.assertEqual(self.store.live_runs(REPO, now=later), [])
        again = self.store.request_again(failed)
        asked = self.store.request_of(again)["payload"]
        self.assertEqual((asked["kind"], asked["target"], asked["benchmark"], asked["env"]),
                         ("agent", "7", True, {"EVENT": "issues"}))
        self.assertEqual([(one["id"], one["status"]) for one in self.store.live_runs(REPO)],
                         [(again, "requested"), (timed, "timed-out")])


if __name__ == "__main__":
    unittest.main()
