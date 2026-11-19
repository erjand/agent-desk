"""Tests for agent-desk/lib/dispatch.py's gate on the state, labels, and blockers of the issue a run works."""

import fcntl
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch  # noqa: E402
import run_control  # noqa: E402
import store  # noqa: E402
from dispatch_case import REPO, DispatchCase  # noqa: E402


class IssueGateTest(DispatchCase):
    def issue(self, status="queued", labels=(), blocked_by=()):
        number = self.store.create(REPO, "issue", "Work", labels=labels)
        self.store.set_state(REPO, number, status)
        for blocker in blocked_by:
            self.store.link(REPO, blocker, number, "blocks")
        return number

    def labelled(self, number):
        return self.request("agent", str(number), env={"EVENT": "issues"})

    def notes(self, number):
        return [one["body"] for one in self.store.item(REPO, number)["comments"]]

    def test_a_blocked_issue_waits_without_a_slot_and_starts_when_its_blocker_closes(self):
        self.configure({"slots": {"agent": 1}})
        blocker = self.issue("open")
        number = self.issue(blocked_by=[blocker])
        run = self.labelled(number)
        other = self.request("agent", "pr-40")
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.status(other)), ("requested", "running"))
        self.assertIn(f"Run {run} waits: it waits for #{blocker} to close.", self.said)
        self.assertEqual(self.store.item(REPO, number)["status"], "waiting")
        self.assertEqual(self.notes(number), [f"Waiting for #{blocker} to close. This issue starts on its own once "
                                              "nothing blocks it, from a main that has that work."
                                              "\n\n<sub>Posted by agent-desk</sub>"])
        self.end(other)
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "requested")
        self.assertEqual(len(self.notes(number)), 1)
        # The blocker's close is the event that starts it, with no retry due.
        self.store.set_state(REPO, blocker, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")

    def test_an_agent_request_on_an_issue_already_worked_goes_on_past_its_blockers(self):
        blocker = self.issue("open")
        number = self.issue("in-progress", blocked_by=[blocker])
        run = self.request("agent", str(number), env={"EVENT": "issue_comment", "REPLY_PR": "50"})
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")
        self.assertEqual(self.store.item(REPO, number)["status"], "in-progress")

    def test_a_serial_group_starts_its_waiting_issues_one_at_a_time(self):
        holder = self.issue("in-progress", labels=["serial:audio"])
        first, second = self.issue(labels=["serial:audio"]), self.issue(labels=["serial:audio", "bug"])
        elsewhere = self.issue(labels=["serial:water"])
        runs = [self.labelled(first), self.labelled(second), self.labelled(elsewhere)]
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["requested", "requested", "running"])
        self.assertIn(f"Run {runs[0]} waits: #{holder} holds its serial slot.", self.said)
        self.assertEqual([self.store.item(REPO, one)["status"] for one in (first, second)], ["waiting", "waiting"])
        self.end(runs[2])
        # The holder's review merges, and the first waiting issue's run starts; its run holds the slot before it
        # claims the issue.
        self.store.set_state(REPO, holder, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs[:2]], ["running", "requested"])
        self.assertEqual(self.store.item(REPO, first)["status"], "waiting")
        self.assertIn(f"Run {runs[1]} waits: #{first} holds its serial slot.", self.said)
        # It claims the issue and stops without a review, which frees the slot.
        self.store.set_state(REPO, first, "in-progress")
        self.end(runs[0])
        self.dispatcher.tick()
        self.assertEqual(self.status(runs[1]), "requested")
        self.store.set_state(REPO, first, "open")
        self.dispatcher.tick()
        self.assertEqual(self.status(runs[1]), "running")

    def test_fresh_starts_take_free_slots_in_the_start_order_and_other_requests_keep_their_time(self):
        first, second, third = self.issue(), self.issue(), self.issue()
        self.store.move(REPO, third, "top")
        runs = {first: self.labelled(first)}
        comment = self.request("agent", "99")
        runs.update({second: self.labelled(second), third: self.labelled(third)})
        self.dispatcher.tick()
        # The order is #3, #1, #2; the comment's run keeps the second place its request time gives it.
        self.assertEqual(self.systemd.commands(), [["work-issue", str(third)], ["work-issue", "99"]])
        self.store.move(REPO, second, "top")
        self.end(comment)
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands()[2:], [["work-issue", str(second)]])
        self.assertEqual(self.status(runs[first]), "requested")

    def test_a_move_alone_reorders_the_waiting_requests_for_the_next_free_slot(self):
        self.configure({"slots": {"agent": 1}})
        busy = self.request("agent", "99")
        first, second = self.issue(), self.issue()
        runs = [self.labelled(first), self.labelled(second)]
        self.dispatcher.tick()
        self.store.move(REPO, second, "up")
        self.assertTrue(self.dispatcher.tick())
        self.end(busy)
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["requested", "running"])

    def test_a_serial_groups_slot_goes_to_its_first_issue_in_the_start_order(self):
        holder = self.issue("in-progress", labels=["serial:audio"])
        first, second = self.issue(labels=["serial:audio"]), self.issue(labels=["serial:audio"])
        runs = [self.labelled(first), self.labelled(second)]
        self.dispatcher.tick()
        self.assertEqual([self.store.item(REPO, one)["status"] for one in (first, second)], ["waiting", "waiting"])
        self.store.move(REPO, second, "top")
        self.store.set_state(REPO, holder, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["requested", "running"])
        self.assertIn(f"Run {runs[0]} waits: #{second} holds its serial slot.", self.said)

    def test_hold_keeps_a_blocked_issue_as_it_is_until_resume(self):
        blocker = self.issue("open")
        number = self.issue(blocked_by=[blocker])
        self.hold = {"state": "held", "picked": []}
        run = self.labelled(number)
        self.dispatcher.tick()
        self.assertIn(f"Run {run} waits: the desk holds new runs.", self.said)
        self.assertEqual((self.store.item(REPO, number)["status"], self.notes(number)), ("queued", []))
        self.hold = {"state": "running", "picked": []}
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.store.item(REPO, number)["status"]), ("requested", "waiting"))

    def test_in_manual_a_blocked_issue_waits_for_its_pick_and_then_for_its_blocker(self):
        blocker = self.issue("open")
        number = self.issue(blocked_by=[blocker])
        self.hold = {"state": "manual", "picked": []}
        run = self.labelled(number)
        self.dispatcher.tick()
        self.assertEqual(self.store.item(REPO, number)["status"], "queued")
        self.hold = {"state": "manual", "picked": [f"{REPO}#{number}"]}
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.store.item(REPO, number)["status"]), ("requested", "waiting"))
        self.store.set_state(REPO, blocker, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")

    def test_stopping_a_waiting_request_puts_its_issue_back_to_the_state_it_had_or_open_for_queued(self):
        blocker = self.issue("open")
        holder = self.issue("in-progress", labels=["serial"])
        queued = self.issue(blocked_by=[blocker])
        worked = self.issue("in-progress", labels=["serial"])
        runs = [self.labelled(queued), self.request("agent", str(worked), env={"EVENT": "issue_comment"})]
        self.dispatcher.tick()
        self.assertIn(f"Run {runs[1]} waits: #{holder} holds its serial slot.", self.said)
        self.assertEqual([self.store.item(REPO, one)["status"] for one in (queued, worked)], ["waiting", "waiting"])
        for number in (queued, worked):
            self.assertEqual(run_control.stop_runs(self.store, REPO, "agent", str(number), run=self.systemd), [])
        self.assertEqual([self.status(run) for run in runs], ["cancelled", "cancelled"])
        # Queued again, the issue would make the wake ask for the run just stopped.
        self.assertEqual([self.store.item(REPO, one)["status"] for one in (queued, worked)], ["open", "in-progress"])
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.requested(queued), [])

    def test_pause_stops_an_issues_run_and_holds_it_until_unpause_queues_it_at_the_top(self):
        self.configure({"slots": {"agent": 1}})
        paused, behind = self.issue(), self.issue()
        run, later = self.labelled(paused), self.labelled(behind)
        self.dispatcher.tick()
        self.store.set_state(REPO, paused, "in-progress")
        reply = self.request("agent", str(paused), env={"EVENT": "issue_comment"})
        self.dispatcher.tick()
        unit = self.store.run(run)["unit"]
        said = run_control.pause_issue(self.store, REPO, paused, run=self.systemd)
        self.assertEqual(said, f"Paused #{paused}: stopped {unit} and put it on hold.")
        self.assertEqual(self.systemd.stopped, [unit])
        self.assertEqual((self.status(reply), self.store.item(REPO, paused)["status"]), ("cancelled", "on-hold"))
        self.assertEqual(self.notes(paused), [run_control.PAUSE_NOTE])
        # The freed slot goes to the next issue, and nothing asks for the paused one again.
        self.end(run, "cancelled")
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.status(later), "running")
        self.assertEqual(self.requested(paused), [])
        self.store.set_state(REPO, behind, "in-progress")
        queued = self.issue()
        self.assertEqual(run_control.unpause_issue(self.store, REPO, paused),
                         f"Queued #{paused} at the top of the start order.")
        self.assertEqual([one["number"] for one in self.store.order(REPO)], [paused, queued])
        self.dispatcher.tick()
        self.assertEqual(len(self.requested(paused)), 1)

    def test_a_request_for_an_issue_on_hold_waits_for_resume_and_starts_as_its_one_run(self):
        number = self.issue()
        run = self.labelled(number)
        self.dispatcher.tick()
        self.store.set_state(REPO, number, "in-progress")
        run_control.pause_issue(self.store, REPO, number, run=self.systemd)
        self.end(run, "cancelled")
        # A comment sent to the agent while the issue is on hold asks for a run, which waits for Resume.
        reply = self.request("agent", str(number), env={"EVENT": "issue_comment", "REPLY_PR": "9"})
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.status(reply), "requested")
        self.assertEqual(self.waits(reply), ("hold", f"#{number} to resume"))
        self.assertIn(f"Run {reply} waits: #{number} is on hold until Resume.", self.said)
        self.assertEqual(self.store.item(REPO, number)["status"], "on-hold")
        # Resume clears the gate, and the wake asks for no second run beside the one that waited.
        run_control.unpause_issue(self.store, REPO, number)
        self.dispatcher.tick()
        self.assertEqual(self.status(reply), "running")
        self.assertEqual(self.requested(number), [reply])

    def test_pause_and_unpause_refuse_an_issue_they_cannot_act_on_and_change_nothing(self):
        idle = self.issue("in-progress")
        with self.assertRaisesRegex(store.StoreError, f"No agent run works #{idle}"):
            run_control.pause_issue(self.store, REPO, idle, run=self.systemd)
        with self.assertRaisesRegex(store.StoreError, f"#{idle} is in-progress, not on hold"):
            run_control.unpause_issue(self.store, REPO, idle)
        self.assertEqual((self.store.item(REPO, idle)["status"], self.notes(idle), self.systemd.stopped),
                         ("in-progress", [], []))

    def test_cancelling_a_waiting_request_puts_its_issue_back_once_no_other_request_waits(self):
        self.issue("in-progress", labels=["serial"])
        worked = self.issue("in-progress", labels=["serial"])
        first = self.request("agent", str(worked), env={"EVENT": "issue_comment", "REQUEST": "a"})
        second = self.request("agent", str(worked), env={"EVENT": "issue_comment", "REQUEST": "b"})
        self.dispatcher.tick()
        self.assertEqual(self.store.item(REPO, worked)["status"], "waiting")
        run_control.cancel_run(self.store, first)
        self.assertEqual(self.store.item(REPO, worked)["status"], "waiting")
        run_control.cancel_run(self.store, second)
        self.assertEqual(self.store.item(REPO, worked)["status"], "in-progress")

    def test_a_request_on_an_issue_on_hold_or_in_needs_design_waits_without_moving_it(self):
        self.issue("in-progress", labels=["serial"])
        numbers = [self.issue(status, labels=["serial"]) for status in ("on-hold", "needs-design")]
        runs = [self.request("agent", str(number), env={"EVENT": "issue_comment"}) for number in numbers]
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["requested", "requested"])
        self.assertEqual([self.store.item(REPO, one)["status"] for one in numbers], ["on-hold", "needs-design"])
        self.assertEqual([self.notes(one) for one in numbers], [[], []])

    def requested(self, number):
        """The requests for an issue's agent run, oldest first."""
        return [row["id"] for row in self.store.runs(("requested", "running", "failed"))
                if (row["kind"], row["target"]) == ("agent", str(number))]

    def test_an_issue_a_claim_moved_to_waiting_starts_when_its_blocker_closes_by_hand(self):
        # agent-desk start, or a run that lost its claim, leaves the issue waiting with no request behind it.
        blocker = self.issue("open")
        number = self.issue("waiting", labels=["benchmark"], blocked_by=[blocker])
        self.dispatcher.tick()
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.requested(number), [])
        self.store.set_state(REPO, blocker, "closed", "completed")
        self.dispatcher.tick()
        [run] = self.requested(number)
        self.assertEqual(self.store.item(REPO, number)["status"], "queued")
        self.assertEqual(self.store.request_of(run)["payload"]["env"], {"EVENT": "issues"})
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.requested(number)), ("running", [run]))
        self.assertEqual(self.systemd.env()["EVENT"], "issues")
        self.assertIn("RuntimeMaxSec=300min", self.systemd.properties())

    def test_waiting_issues_with_no_request_take_their_serial_slot_one_at_a_time(self):
        holder = self.issue("in-progress", labels=["serial:audio"])
        first, second = self.issue("waiting", labels=["serial:audio"]), self.issue("waiting", labels=["serial:audio"])
        asked = self.labelled(self.issue(labels=["serial:audio"]))
        self.dispatcher.tick()
        self.assertEqual((self.requested(first), self.requested(second)), ([], []))
        self.store.set_state(REPO, holder, "closed", "completed")
        self.dispatcher.tick()
        # The request that waited already takes the slot, so the issues without one wait for it.
        self.assertEqual(self.status(asked), "running")
        self.assertEqual((self.requested(first), self.requested(second)), ([], []))
        self.end(asked)
        self.dispatcher.tick()
        [run] = self.requested(first)
        self.assertEqual(self.requested(second), [])
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")
        self.store.set_state(REPO, first, "in-progress")
        self.end(run)
        self.dispatcher.tick()
        self.assertEqual(self.requested(second), [])
        self.store.set_state(REPO, first, "open")
        self.dispatcher.tick()
        self.assertEqual(len(self.requested(second)), 1)

    def test_the_wake_asks_for_the_first_waiting_issue_of_a_group_in_the_start_order(self):
        holder = self.issue("in-progress", labels=["serial:audio"])
        first, second = self.issue("waiting", labels=["serial:audio"]), self.issue("waiting", labels=["serial:audio"])
        self.store.move(REPO, second, "top")
        self.store.set_state(REPO, holder, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual((len(self.requested(second)), self.requested(first)), (1, []))

    def test_the_wake_waits_while_serial_release_holds_its_lock(self):
        blocker = self.issue("open")
        number = self.issue("waiting", blocked_by=[blocker])
        (self.checkout / ".git").mkdir()
        with open(self.checkout / ".git" / "serial-slot.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.store.set_state(REPO, blocker, "closed", "completed")
            self.dispatcher.tick()
            self.dispatcher.tick()
            self.assertEqual(self.requested(number), [])
            fcntl.flock(lock, fcntl.LOCK_UN)
        self.dispatcher.tick()
        self.assertEqual(len(self.requested(number)), 1)

    def test_a_parked_request_whose_unit_fails_to_start_is_asked_for_again_on_reconcile(self):
        blocker = self.issue("open")
        number = self.issue(blocked_by=[blocker])
        run = self.labelled(number)
        self.dispatcher.tick()
        self.systemd.fail_start = True
        self.store.set_state(REPO, blocker, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.store.item(REPO, number)["status"]), ("failed", "waiting"))
        self.systemd.fail_start = False
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.dispatcher.tick()
        again = self.requested(number)[-1]
        self.assertNotEqual(again, run)
        self.assertEqual(self.status(again), "running")
        self.assertEqual(len(self.notes(number)), 1)

    def test_a_parked_request_dropped_for_no_checkout_is_asked_for_again_once_the_checkout_is_back(self):
        blocker = self.issue("open")
        number = self.issue(blocked_by=[blocker])
        run = self.labelled(number)
        self.dispatcher.tick()
        (self.folder / "projects").write_text("")
        self.store.set_state(REPO, blocker, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.store.item(REPO, number)["status"]), ("failed", "waiting"))
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.requested(number), [run])
        (self.folder / "projects").write_text(f"{REPO} {self.checkout}\n")
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.dispatcher.tick()
        self.assertEqual(self.status(self.requested(number)[-1]), "running")


    def label(self, number):
        """`agent-desk issue edit <n> --add-label agent-ready`, which asks for no run itself."""
        self.store.edit(REPO, number, add_labels=["agent-ready"])

    def test_in_auto_an_issue_labelled_from_the_command_line_starts_its_run(self):
        number = self.issue("open", labels=["benchmark"])
        self.label(number)
        self.dispatcher.tick()
        [run] = self.requested(number)
        self.assertIn(f"Asked for a run of #{number} in {REPO}, which was queued with no request.", self.said)
        self.assertEqual(self.store.request_of(run)["payload"]["env"], {"EVENT": "issues"})
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")
        self.assertEqual(self.systemd.env()["EVENT"], "issues")
        self.assertIn("RuntimeMaxSec=300min", self.systemd.properties())
        # Its run ending, or a reconcile, asks for no second one while the issue stays queued.
        self.end(run, "failed")
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.requested(number), [run])

    def test_in_manual_an_issue_labelled_from_the_command_line_waits_for_start(self):
        self.hold = {"state": "manual", "picked": []}
        number = self.issue("open")
        self.label(number)
        self.dispatcher.tick()
        self.dispatcher.tick()
        [run] = self.requested(number)
        self.assertEqual(self.status(run), "requested")
        self.assertIn(f"Run {run} waits: the desk is in Manual and nobody pressed Start on it; Start runs it.", self.said)
        self.hold = {"state": "manual", "picked": [f"{REPO}#{number}"]}
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.requested(number)), ("running", [run]))

    def test_an_agent_ready_press_starts_one_run_when_the_wake_asks_first(self):
        # The button labels the issue, the wake asks for its run, the run starts, and then the button's request comes.
        for state in ({"state": "running", "picked": []}, {"state": "manual", "picked": []}):
            with self.subTest(state=state["state"]):
                number = self.issue("open")
                self.hold = {**state, "picked": [f"{REPO}#{number}"]}
                self.label(number)
                self.dispatcher.tick()
                self.dispatcher.tick()
                [asked] = self.requested(number)
                self.assertEqual(self.status(asked), "running")
                pressed = self.labelled(number)
                self.dispatcher.tick()
                self.assertEqual(self.status(pressed), "ignored")
                self.assertIn(f"Ignored run {pressed}: run {asked} already works agent {number} with the same "
                              "environment.", self.said)
                self.assertEqual(self.requested(number), [asked])
                self.assertEqual(len([unit for unit in self.systemd.units() if f"-{number}-" in unit]), 1)

    def test_an_agent_ready_press_starts_one_run_when_both_requests_wait(self):
        number = self.issue("open")
        self.label(number)
        self.dispatcher.tick()
        [asked] = self.requested(number)
        pressed = self.labelled(number)
        self.dispatcher.tick()
        self.assertEqual((self.status(asked), self.status(pressed)), ("running", "ignored"))

    def test_the_button_asking_before_the_wake_leaves_the_wake_nothing_to_ask(self):
        number = self.issue("open")
        self.label(number)
        pressed = self.labelled(number)
        self.dispatcher.tick()
        self.dispatcher.tick()
        self.assertEqual((self.requested(number), self.status(pressed)), ([pressed], "running"))

    def test_a_run_queued_again_while_its_failed_run_still_works_waits_behind_it(self):
        # lib/release.py queues an issue whose run failed before the run's end is recorded.
        number = self.issue("open")
        self.label(number)
        self.dispatcher.tick()
        self.dispatcher.tick()
        [failed] = self.requested(number)
        self.store.set_state(REPO, number, "in-progress")
        self.store.set_state(REPO, number, "queued")
        again = self.labelled(number)
        self.dispatcher.tick()
        self.assertEqual(self.status(again), "requested")
        self.end(failed, "failed")
        self.dispatcher.tick()
        self.assertEqual(self.status(again), "running")
        self.assertEqual(self.requested(number), [failed, again])

    def test_a_queued_issue_whose_request_was_cancelled_or_failed_to_start_is_not_asked_for_again(self):
        held, failing = self.issue(), self.issue()
        held_run, failing_run = self.labelled(held), self.labelled(failing)
        self.hold = {"state": "held", "picked": []}
        self.dispatcher.tick()
        run_control.cancel_run(self.store, held_run)
        self.hold = {"state": "running", "picked": []}
        self.systemd.fail_start = True
        self.dispatcher.tick()
        self.assertEqual((self.status(held_run), self.status(failing_run)), ("cancelled", "failed"))
        self.systemd.fail_start = False
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.dispatcher.tick()
        self.assertEqual((self.requested(held), self.requested(failing)), ([], [failing_run]))
        # Labelling it again queues it again, which asks once more.
        self.store.set_state(REPO, held, "open")
        self.label(held)
        self.dispatcher.tick()
        self.assertEqual(len(self.requested(held)), 1)

    def test_a_queued_serial_issue_the_wake_asks_for_holds_its_slot_for_the_waiting_ones(self):
        holder = self.issue("in-progress", labels=["serial:web"])
        waiting = self.issue("waiting", labels=["serial:web"])
        queued = self.issue("open", labels=["serial:web"])
        self.label(queued)
        self.store.set_state(REPO, holder, "closed", "completed")
        self.dispatcher.tick()
        self.assertEqual((len(self.requested(queued)), self.requested(waiting)), (1, []))

    def test_in_manual_a_request_waiting_for_start_leaves_its_serial_slot_to_a_picked_issue(self):
        # The oldest issue was released to queued and waits for Start; the newest of those waiting is picked.
        stuck = self.issue("queued", labels=["serial:atlas"])
        held = self.labelled(stuck)
        unpicked = self.issue("waiting", labels=["serial:atlas"])
        picked = self.issue("waiting", labels=["serial:atlas"])
        self.hold = {"state": "running", "picked": [f"{REPO}#{picked}"], "projects": {REPO: "manual"}}
        self.dispatcher.tick()
        [run] = self.requested(picked)
        self.assertEqual((self.requested(unpicked), self.status(held)), ([], "requested"))
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")
        # Start on the queued issue picks it, and it waits behind the picked issue that took the slot.
        self.hold = {**self.hold, "picked": [f"{REPO}#{picked}", f"{REPO}#{stuck}"]}
        self.dispatcher.tick()
        self.assertEqual(self.status(held), "requested")
        self.assertIn(f"Run {held} waits: #{picked} holds its serial slot.", self.said)
        self.assertEqual(self.store.item(REPO, stuck)["status"], "waiting")
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.requested(unpicked), [])

    def test_in_manual_a_request_holds_its_serial_slot_only_once_start_picks_it(self):
        for picks_it in (False, True):
            with self.subTest(picks_it=picks_it):
                queued = self.issue("queued", labels=["serial:atlas"])
                comment = self.request("agent", str(queued), env={"EVENT": "issue_comment"})
                waiting = self.issue("waiting", labels=["serial:atlas"])
                self.hold = {"state": "manual", "picked": [f"{REPO}#{waiting}"],
                             "picked_runs": [comment] if picks_it else []}
                self.time += dispatch.RECONCILE_SECONDS
                self.dispatcher.tick()
                self.assertEqual(len(self.requested(waiting)), 0 if picks_it else 1)
                for run in [comment, *self.requested(waiting)]:
                    self.store.run_ended(run, "cancelled")
                for number in (queued, waiting):
                    self.store.set_state(REPO, number, "closed", "completed")
                self.dispatcher.tick()

    def test_in_manual_with_nothing_picked_a_waiting_serial_issue_waits_and_others_are_asked_for(self):
        serial = self.issue("waiting", labels=["serial:atlas"])
        plain = self.issue("waiting")
        self.hold = {"state": "manual", "picked": []}
        self.dispatcher.tick()
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.requested(serial), [])
        self.assertEqual(self.store.item(REPO, serial)["status"], "waiting")
        [run] = self.requested(plain)
        self.assertEqual(self.status(run), "requested")

    def test_in_manual_start_on_a_waiting_serial_issue_starts_it_once_its_slot_is_free(self):
        holder = self.issue("in-progress", labels=["serial:atlas"])
        number = self.issue("waiting", labels=["serial:atlas"])
        self.hold = {"state": "manual", "picked": []}
        self.dispatcher.tick()
        self.hold = {"state": "manual", "picked": [f"{REPO}#{number}"]}
        self.dispatcher.tick()
        self.assertEqual(self.requested(number), [])
        self.store.set_state(REPO, holder, "closed", "completed")
        self.dispatcher.tick()
        [run] = self.requested(number)
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")

    def test_a_pick_wakes_a_waiting_serial_issue_without_waiting_for_the_reconcile(self):
        number = self.issue("waiting", labels=["serial:atlas"])
        self.hold = {"state": "manual", "picked": []}
        self.dispatcher.tick()
        self.assertEqual(self.requested(number), [])
        self.hold = {"state": "manual", "picked": [f"{REPO}#{number}"]}
        self.dispatcher.tick()
        self.assertEqual(len(self.requested(number)), 1)

    def test_in_auto_a_queued_serial_issue_holds_its_slot_while_its_request_waits(self):
        self.configure({"slots": {"agent": 1}})
        busy = self.request("agent", "pr-40")
        queued = self.issue("queued", labels=["serial:atlas"])
        run = self.labelled(queued)
        waiting = self.issue("waiting", labels=["serial:atlas"])
        self.dispatcher.tick()
        self.assertEqual((self.status(busy), self.status(run), self.requested(waiting)), ("running", "requested", []))


if __name__ == "__main__":
    unittest.main()
