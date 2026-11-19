"""Tests for agent-desk/lib/dispatch.py, which starts requested runs as systemd units."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch  # noqa: E402
import memory  # noqa: E402
import run_control  # noqa: E402
import run_units  # noqa: E402
import store  # noqa: E402
from dispatch_case import REPO, DispatchCase  # noqa: E402


class GateTest(DispatchCase):
    def test_a_request_waits_for_a_free_slot_of_its_kind_and_starts_when_one_frees(self):
        first, second, third = (self.request("agent", str(n)) for n in (1, 2, 3))
        chore = self.request("chore", "audit")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["work-issue", "1"], ["work-issue", "2"], ["run-chore", "audit"]])
        self.assertEqual([self.status(run) for run in (first, second, third, chore)],
                         ["running", "running", "requested", "running"])
        self.assertIn(f"Run {third} waits: its project's 2 agent slots are busy.", self.said)
        self.end(first)
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands()[-1], ["work-issue", "3"])
        self.assertEqual(self.status(third), "running")

    def test_a_slot_override_below_the_config_holds_a_request_and_starts_it_once_it_rises(self):
        first, second = (self.request("agent", str(n)) for n in (1, 2))
        self.dispatcher.tick()
        # Lowering the count to 1 stops neither run, and a new request waits until the busy count drops below it.
        self.hold = {"state": "running", "picked": [], "slots": {REPO: {"agent": 1}}}
        self.dispatcher.tick()
        self.end(first)
        third = self.request("agent", "3")
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in (second, third)], ["running", "requested"])
        self.assertEqual(self.waits(third), ("slot", "a free agent slot"))
        self.assertIn(f"Run {third} waits: its project's 1 agent slots are busy.", self.said)
        # Raising it above the config's 2 starts the request at the next pass.
        self.hold = {"state": "running", "picked": [], "slots": {REPO: {"agent": 3}}}
        self.dispatcher.tick()
        self.assertEqual(self.status(third), "running")

    def test_a_project_without_slots_takes_the_defaults_and_a_zero_count_turns_a_kind_off(self):
        self.configure({"slots": {"fix": 0}})
        runs = [self.request("agent", str(n)) for n in range(1, 5)]
        fix = self.request("fix", "9")
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["running"] * 3 + ["requested"])
        self.assertEqual(self.status(fix), "requested")

    def test_a_target_runs_once_at_a_time_and_its_later_requests_keep_their_order(self):
        first = self.request("agent", "5")
        fix = self.request("fix", "5")
        again = self.request("agent", "5")
        other = self.request("agent", "6")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["work-issue", "5"], ["work-issue", "6"]])
        self.assertIn(f"Run {fix} waits: another run holds agent-5.", self.said)
        self.assertEqual(self.waits(fix), ("lock", f"agent run {first} to end"))
        self.assertEqual(self.waits(again), ("lock", f"run {fix} ahead of it"))
        self.end(first)
        self.end(other)
        self.dispatcher.tick()
        # The fix asked first, so it goes before the second agent request, which waits behind it.
        self.assertEqual(self.systemd.commands()[2:], [["fix", "5"]])
        self.assertEqual(self.status(again), "requested")
        self.end(fix)
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands()[3:], [["work-issue", "5"]])

    def test_an_agent_pr_request_holds_the_same_target_as_a_fix_of_that_pr(self):
        self.request("agent", "pr-8")
        fix = self.request("fix", "8")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["work-pr", "8"]])
        self.assertEqual(self.status(fix), "requested")

    def test_a_request_waits_for_spare_memory_and_claims_it_under_the_units_process(self):
        self.memory.ready = False
        run = self.request("chore", "singleshot-untested-one")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])
        self.assertEqual(self.memory.asked, [(REPO, "chore/singleshot-untested-one", [])])
        self.assertIn(f"Run {run} waits: the run needs 6 GB and 8 GB stays free for the user, and 3.0 GB is free.",
                      self.said)
        # Memory frees without an event, so the request tries again after a while.
        self.memory.ready = True
        self.time += 1
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])
        self.time += dispatch.RETRY_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")
        self.assertEqual(self.memory.claims, [(6, self.systemd.pids[self.systemd.units()[0]])])

    def test_a_waiting_request_records_its_gate_and_reason_until_it_starts(self):
        first, second, third = (self.request("agent", str(n)) for n in (1, 2, 3))
        self.dispatcher.tick()
        self.assertEqual(self.waits(third), ("slot", "a free agent slot"))
        self.assertEqual(self.waits(first), (None, None))
        # The record moves with the gate that holds the request.
        self.end(first)
        self.memory.ready = False
        self.memory.reason = memory.Shortfall("the run needs 1 GB and 8 GB stays free for the user, and 5.3 GB is free",
                                              "memory: 5.3 GB free of 9 GB needed", 5.3, 9)
        self.dispatcher.tick()
        self.assertEqual(self.waits(third), ("memory", "memory: 5.3 GB free of 9 GB needed"))
        self.memory.ready = True
        self.time += dispatch.RETRY_SECONDS
        self.dispatcher.tick()
        self.assertEqual((self.status(third), self.waits(third)), ("running", (None, None)))
        self.assertEqual(self.waits(second), (None, None))

    def test_a_memory_reason_records_again_only_once_the_free_memory_moves_half_a_gb(self):
        self.memory.ready = False
        run = self.request("chore", "audit")

        def free(gb):
            self.memory.reason = memory.Shortfall(f"the run needs 1 GB and 8 GB stays free for the user, and {gb} GB is "
                                                  "free", f"memory: {gb} GB free of 9 GB needed", gb, 9)
            self.time += dispatch.RETRY_SECONDS
            self.dispatcher.tick()
            return self.waits(run)[1], sum(line.startswith(f"Run {run} waits:") for line in self.said)

        self.assertEqual(free(5.3), ("memory: 5.3 GB free of 9 GB needed", 1))
        self.assertEqual(free(5.6), ("memory: 5.3 GB free of 9 GB needed", 1))
        self.assertEqual(free(4.9), ("memory: 5.3 GB free of 9 GB needed", 1))
        self.assertEqual(free(5.8), ("memory: 5.8 GB free of 9 GB needed", 2))
        # Pressure is another reason, whatever the figures.
        self.memory.reason = memory.Shortfall("memory pressure is 12%, over 5%", "memory pressure under 5%, now 12%")
        self.time += dispatch.RETRY_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.waits(run), ("memory", "memory pressure under 5%, now 12%"))
        # A gate that changes records at once, and Resume back to the same memory reason records it again.
        self.hold = {"state": "held", "picked": []}
        self.dispatcher.tick()
        self.assertEqual(self.waits(run), ("mode", "Resume"))
        self.hold = {"state": "running", "picked": []}
        self.dispatcher.tick()
        self.assertEqual(self.waits(run), ("memory", "memory pressure under 5%, now 12%"))

    def test_a_request_that_ends_while_it_waits_clears_its_reason(self):
        self.hold = {"state": "held", "picked": []}
        run = self.request("fix", "4")
        self.dispatcher.tick()
        self.assertEqual(self.waits(run), ("mode", "Resume"))
        run_control.cancel_run(self.store, run)
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.waits(run)), ("cancelled", (None, None)))
        self.assertNotIn(run, self.dispatcher.reasons)

    def test_a_shrunk_claim_is_logged_once_naming_its_run(self):
        run = self.request("chore", "audit")
        self.dispatcher.tick()
        unit = self.systemd.units()[0]
        pid = self.systemd.pids[unit]
        self.memory.units = {pid: unit, 4343: "agent-desk-runner.service"}
        self.memory.shrinking = [(pid, 9.7, 2.6, 0.6, 42), (4242, 6, 3.5, 1.5, 30.5)]
        for _ in range(2):
            self.time += dispatch.RECONCILE_SECONDS
            self.dispatcher.tick()
        self.memory.shrinking = [(4343, 6, 4, 2.0, 12)]
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual([line for line in self.said if "claim" in line], [
            f"The memory claim of run {run} shrinks from 9.7 GB to 2.6 GB: its peak so far is 0.6 GB, and it is past "
            "its settle time of 42 min.",
            "The memory claim of process 4242 shrinks from 6 GB to 3.5 GB: its peak so far is 1.5 GB, and it is past "
            "its settle time of 30.5 min.",
            "The memory claim of the run in agent-desk-runner.service shrinks from 6 GB to 4 GB: its peak so far is "
            "2.0 GB, and it is past its settle time of 12 min."])

    def test_a_fix_the_merge_queue_waits_on_starts_ahead_of_older_requests(self):
        self.request("agent", "1")
        self.request("chore", "audit")
        self.request("fix", "9")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["fix", "9"], ["work-issue", "1"], ["run-chore", "audit"]])

    def test_while_a_fix_waits_for_memory_no_other_request_starts(self):
        agent, chore = self.request("agent", "1"), self.request("chore", "audit")
        fix = self.request("fix", "9")
        self.memory.short = {"fix"}
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])
        self.assertEqual(self.waits(fix), ("memory", self.memory.reason))
        for run in (agent, chore):
            self.assertEqual(self.waits(run), ("memory", f"merge-queue fix run {fix} to start"))
        self.assertIn(f"Run {agent} waits: merge-queue fix run {fix} waits for memory and starts first.", self.said)
        # The memory that frees goes to the fix, and the rest start behind it.
        self.memory.short = set()
        self.time += dispatch.RETRY_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["fix", "9"], ["work-issue", "1"], ["run-chore", "audit"]])

    def test_a_fix_that_waits_for_anything_but_memory_holds_no_other_request(self):
        self.configure({"slots": {"agent": 2, "fix": 0}})
        self.request("fix", "9")
        agent = self.request("agent", "1")
        self.memory.short = {"fix"}
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["work-issue", "1"]])
        self.assertEqual(self.status(agent), "running")

    def test_a_request_held_by_memory_pressure_says_so(self):
        self.memory.ready = False
        self.memory.reason = "memory pressure is 12%, over 5%"
        run = self.request("agent", "3")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])
        self.assertIn(f"Run {run} waits: memory pressure is 12%, over 5%.", self.said)

    def test_a_benchmark_request_takes_a_benchmark_slot_and_the_longer_limit(self):
        self.configure({"slots": {"agent": 3, "benchmark": 1}})
        self.request("agent", "1", benchmark=True)
        second = self.request("agent", "2", benchmark=True)
        self.request("agent", "3")
        self.request("fix", "4")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["fix", "4"], ["work-issue", "1"], ["work-issue", "3"]])
        self.assertIn(f"Run {second} waits: its project's 1 benchmark slots are busy.", self.said)
        limits = [next(p for p in self.systemd.properties(i) if p.startswith("RuntimeMaxSec")) for i in range(3)]
        self.assertEqual(limits, ["RuntimeMaxSec=90min", "RuntimeMaxSec=300min", "RuntimeMaxSec=180min"])
        self.assertEqual(self.memory.asked[1], (REPO, "agent", ["benchmark"]))

    def test_a_project_sets_its_runs_limits_and_keeps_the_built_in_ones_it_leaves_out(self):
        self.configure({"limitMinutes": {"agent": 360}})
        self.request("agent", "1")
        self.request("agent", "2", benchmark=True)
        self.request("fix", "3")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["fix", "3"], ["work-issue", "1"], ["work-issue", "2"]])
        limits = [next(p for p in self.systemd.properties(i) if p.startswith("RuntimeMaxSec")) for i in range(3)]
        self.assertEqual(limits, ["RuntimeMaxSec=90min", "RuntimeMaxSec=360min", "RuntimeMaxSec=300min"])

    def test_a_benchmark_run_takes_the_projects_benchmark_limit(self):
        self.configure({"limitMinutes": {"benchmark": 480}})
        self.request("agent", "1", benchmark=True)
        self.dispatcher.tick()
        self.assertIn("RuntimeMaxSec=480min", self.systemd.properties())

    def test_the_dispatcher_ignores_a_request_an_agent_wrote(self):
        run = self.request("agent", "3", agent=True)
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])
        self.assertEqual(self.status(run), "ignored")
        self.assertIn(f"Ignored run {run}: an agent asked for it.", self.said)

    def test_a_second_request_like_one_that_waits_is_ignored(self):
        self.configure({"slots": {"agent": 1}})
        self.request("agent", "1")
        first = self.request("agent", "2", env={"REQUEST": "a"})
        twin = self.request("agent", "2", env={"REQUEST": "a"})
        other = self.request("agent", "2", env={"REQUEST": "b"})
        benchmark = self.request("agent", "2", benchmark=True, env={"REQUEST": "a"})
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in (first, twin, other, benchmark)],
                         ["requested", "ignored", "requested", "requested"])
        self.assertIn(f"Ignored run {twin}: run {first} already waits for agent 2 with the same environment.",
                      self.said)

    def test_a_request_like_a_working_run_waits_behind_it(self):
        working = self.request("agent", "3")
        self.dispatcher.tick()
        behind = self.request("agent", "3")
        self.dispatcher.tick()
        self.assertEqual((self.status(working), self.status(behind)), ("running", "requested"))
        again = self.request("agent", "3")
        self.dispatcher.tick()
        self.assertEqual(self.status(again), "ignored")

    def test_a_unit_that_fails_to_start_ends_its_run_as_failed(self):
        self.systemd.fail_start = True
        run = self.request("agent", "3")
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "failed")
        self.assertEqual(self.dispatcher.pending, {})

    def test_a_request_for_a_project_with_no_checkout_fails(self):
        run = self.store.request_run("org/elsewhere", "agent", "3")
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "failed")


class UnitTest(DispatchCase):
    def test_the_unit_carries_its_run_project_priority_limit_and_exit_handler(self):
        self.configure({"env": {"GODOT": "~/bin/godot"}})
        run = self.request("agent", "12", env={"REPLY_PR": "14", "EVENT": "issue_comment"})
        self.dispatcher.tick()
        unit = self.systemd.units()[0]
        self.assertEqual(unit, f"agent-desk-agent-org-game-12-{run}.service")
        self.assertEqual(self.store.run(run)["unit"], unit)
        properties = self.systemd.properties()
        for setting in ("Nice=10", "CPUWeight=20", "IOWeight=20", "RuntimeMaxSec=180min"):
            self.assertIn(setting, properties)
        high = dispatch.memory.default_gb("agentMemoryHigh")
        self.assertIn(f"MemoryHigh={high}G", properties)
        self.assertNotIn("MemoryHigh=50%", properties)
        handler = next(p for p in properties if p.startswith("ExecStopPost="))
        self.assertIn('dispatch.py" "stopped"', handler)
        env = self.systemd.env()
        self.assertEqual(env["AGENT_DESK_RUN"], str(run))
        self.assertEqual(env["AGENT_DESK_UNIT"], self.store.run(run)["unit"])
        self.assertEqual(env["GH_REPO"], REPO)
        self.assertEqual(env["REPLY_PR"], "14")
        self.assertEqual(env["EVENT"], "issue_comment")
        self.assertEqual(env["GODOT"], f"{Path.home()}/bin/godot")
        args = self.systemd.started[0]
        self.assertEqual(args[args.index("--working-directory") + 1], str(self.checkout))
        self.assertTrue(args[args.index("--") + 1].endswith("/bin/agent-desk"))
        self.assertEqual(args[args.index("--slice") + 1], "agentdesk.slice")


class RestartTest(DispatchCase):
    def test_a_restart_keeps_running_units_marks_vanished_ones_lost_and_starts_waiting_requests(self):
        kept, vanished = self.request("agent", "1"), self.request("agent", "2")
        self.dispatcher.tick()
        waiting = self.request("agent", "3")
        # The dispatcher stops before it sees the third request, and the second unit dies with it down.
        self.systemd.live.discard(self.store.run(vanished)["unit"])
        restarted = self.new_dispatcher()
        self.assertEqual(set(restarted.running), {kept})
        self.assertEqual(self.status(vanished), "lost")
        self.assertEqual(self.status(kept), "running")
        restarted.tick()
        self.assertEqual(self.status(waiting), "running")
        self.assertEqual(len(self.systemd.started), 3)
        # The kept run still holds a slot: a fourth request waits.
        fourth = self.request("agent", "4")
        restarted.tick()
        self.assertEqual(self.status(fourth), "requested")

    def test_a_restart_that_cannot_list_units_keeps_its_running_runs(self):
        run = self.request("agent", "1")
        self.dispatcher.tick()
        self.systemd.fail_list = True
        restarted = self.new_dispatcher()
        self.assertEqual(set(restarted.running), {run})
        self.assertEqual(self.status(run), "running")

    def test_the_dispatcher_ends_a_run_whose_unit_vanished_without_its_handler(self):
        run = self.request("agent", "1")
        self.dispatcher.tick()
        self.systemd.live.clear()
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "lost")
        self.assertEqual(self.dispatcher.running, {})

    def test_the_dispatcher_starts_nothing_once_the_schema_is_newer_than_its_code(self):
        self.store.db.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
        run = self.request_raw()
        self.dispatcher.tick()
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])
        self.assertEqual(self.status(run), "requested")
        self.assertEqual(sum("newer than this dispatcher's" in line for line in self.said), 1)

    def request_raw(self):
        """A request newer code wrote, which this code's store refuses to write itself."""
        db = self.store.db
        run = db.execute("INSERT INTO runs (project, kind, target, status) VALUES (?, 'agent', '1', 'requested')",
                         (REPO,)).lastrowid
        db.execute("INSERT INTO events (project, number, type, payload, author, at) VALUES (?, 1, 'run-requested', ?,"
                   " 'you', ?)", (REPO, json.dumps({"run": run, "kind": "agent", "target": "1"}), store.now()))
        return run


class CleanTest(DispatchCase):
    def review(self, branch="agent/issue-7", base="main"):
        return self.store.create(REPO, "review", "Work", branch=branch, base=base)

    def test_a_merged_review_starts_its_cleanup_in_a_unit_of_its_own(self):
        review = self.review()
        self.store.merge(REPO, review, "abc123")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["clean", "agent/issue-7", "true"]])
        [unit] = self.systemd.units()
        self.assertRegex(unit, r"^agent-desk-clean-org-game-issue-7-\d+\.service$")
        args = self.systemd.started[0]
        self.assertEqual(args[args.index("--working-directory") + 1], str(self.checkout))
        self.assertEqual(Path(args[args.index("--") + 1]), run_units.AGENT_DESK)
        self.assertEqual(args[args.index("--slice") + 1], "agentdesk.slice")
        self.assertEqual(self.systemd.env()["GH_REPO"], REPO)
        self.assertIn(f"RuntimeMaxSec={dispatch.CLEAN_MINUTES}min", self.systemd.properties())
        self.assertIn(f"Started the cleanup of agent/issue-7 in {unit}: review #{review} merged.", self.said)

    def test_a_review_closed_without_merging_starts_an_unmerged_cleanup(self):
        review = self.review("agent/chore-audit-20261111-1200")
        self.store.set_state(REPO, review, "closed", "closed")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["clean", "agent/chore-audit-20261111-1200", "false"]])
        # A review closed again after a reopen gets a cleanup of its own.
        self.store.set_state(REPO, review, "open")
        self.store.set_state(REPO, review, "closed", "closed")
        self.dispatcher.tick()
        self.assertEqual(len(set(self.systemd.units())), 2)

    def test_other_branches_issues_and_reopens_start_no_cleanup(self):
        self.store.merge(REPO, self.review("feature/menu"), "abc123")
        issue = self.store.create(REPO, "issue", "Work")
        self.store.set_state(REPO, issue, "closed", "completed")
        self.store.set_state(REPO, issue, "open")
        self.store.review_update(REPO, self.review(), draft=False)
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])

    def test_a_cleanup_that_fails_to_start_is_logged(self):
        self.systemd.fail_start = True
        self.store.merge(REPO, self.review(), "abc123")
        self.dispatcher.tick()
        self.assertIn("The cleanup of agent/issue-7 failed to start: Unit already exists.", self.said)

    def test_a_review_closed_with_any_reason_starts_a_cleanup_and_a_merge_starts_one(self):
        for reason in ("closed", "completed", "not-planned", "merged"):
            self.store.set_state(REPO, self.review(f"agent/issue-{reason}"), "closed", reason)
        self.store.merge(REPO, self.review(), "abc123")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [
            ["clean", "agent/issue-closed", "false"], ["clean", "agent/issue-completed", "false"],
            ["clean", "agent/issue-not-planned", "false"], ["clean", "agent/issue-merged", "false"],
            ["clean", "agent/issue-7", "true"]])

    def test_a_review_that_ended_while_the_dispatcher_was_down_gets_its_cleanup_on_start(self):
        self.store.merge(REPO, self.review(), "abc123")
        self.dispatcher.tick()
        # The dispatcher stops, and two reviews end before it starts again.
        self.store.merge(REPO, self.review("agent/issue-8"), "def456")
        self.store.set_state(REPO, self.review("agent/issue-9"), "closed", "not-planned")
        restarted = self.new_dispatcher()
        self.assertEqual(self.systemd.commands(), [
            ["clean", "agent/issue-7", "true"], ["clean", "agent/issue-8", "true"],
            ["clean", "agent/issue-9", "false"]])
        restarted.tick()
        self.time += dispatch.RECONCILE_SECONDS
        restarted.tick()
        # Another restart starts none of them again.
        self.new_dispatcher().tick()
        self.assertEqual(len(self.systemd.started), 3)

    def test_a_cleanup_that_failed_to_start_starts_on_the_next_reconcile(self):
        self.systemd.fail_start = True
        self.store.merge(REPO, self.review(), "abc123")
        self.dispatcher.tick()
        self.systemd.fail_start = False
        self.store.merge(REPO, self.review("agent/issue-8"), "def456")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(), [["clean", "agent/issue-8", "true"]])
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.systemd.commands(),
                         [["clean", "agent/issue-8", "true"], ["clean", "agent/issue-7", "true"]])
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(len(self.systemd.started), 2)

    def test_a_review_in_a_project_without_a_checkout_cleans_nothing(self):
        review = self.store.create("org/other", "review", "Work", branch="agent/issue-3")
        self.store.merge("org/other", review, "abc123")
        self.dispatcher.tick()
        self.assertEqual(self.systemd.started, [])
        self.assertTrue(any("which has no checkout here" in line for line in self.said))


class ModeTest(DispatchCase):
    def test_hold_keeps_every_request_waiting_until_resume(self):
        self.hold = {"state": "held", "picked": []}
        run = self.request("agent", "7")
        chore = self.request("chore", "audit")
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.status(chore)), ("requested", "requested"))
        self.assertIn(f"Run {run} waits: the desk holds new runs.", self.said)
        self.hold = {"state": "running", "picked": []}
        self.dispatcher.tick()
        self.assertEqual((self.status(run), self.status(chore)), ("running", "running"))

    def test_a_project_on_hold_waits_while_another_starts(self):
        self.hold = {"state": "running", "picked": [], "projects": {REPO: "held"}}
        run = self.request("agent", "7")
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "requested")
        self.hold = {"state": "held", "picked": [], "projects": {REPO: "running"}}
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")

    def test_in_manual_every_agent_fix_and_chore_request_waits_for_start(self):
        self.hold = {"state": "manual", "picked": [], "picked_runs": []}
        requests = {"fresh start": self.request("agent", "7", env={"EVENT": "issues"}),
                    "back to its PR": self.request("agent", "8", env={"EVENT": "issue_comment", "REPLY_PR": "80"}),
                    "comment": self.request("agent", "9", env={"EVENT": "issue_comment"}),
                    "plain": self.request("agent", "10"),
                    "pr": self.request("agent", "pr-81", env={"REQUEST": "fix it"}),
                    "discussion": self.request("agent", "idea-11"),
                    "fix": self.request("fix", "82"),
                    "chore": self.request("chore", "audit")}
        self.dispatcher.tick()
        for kind, run in requests.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.status(run), "requested")
                self.assertIn(f"Run {run} waits: the desk is in Manual and nobody pressed Start on it; Start runs it.",
                              self.said)

    def test_in_manual_start_picks_a_request_by_its_run_and_no_other(self):
        self.hold = {"state": "manual", "picked": [], "picked_runs": []}
        back = self.request("agent", "8", env={"EVENT": "issue_comment", "REPLY_PR": "80"})
        fix = self.request("fix", "82")
        chore = self.request("chore", "audit")
        other = self.request("agent", "9", env={"EVENT": "issue_comment"})
        self.dispatcher.tick()
        self.hold = {"state": "manual", "picked": [], "picked_runs": [back, fix, chore]}
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in (back, fix, chore, other)],
                         ["running", "running", "running", "requested"])

    def test_in_manual_an_issue_pick_starts_its_fresh_start_and_not_its_comment_runs(self):
        self.hold = {"state": "manual", "picked": [f"{REPO}#7"], "picked_runs": []}
        fresh = self.request("agent", "7", env={"EVENT": "issues"})
        self.dispatcher.tick()
        self.assertEqual(self.status(fresh), "running")
        self.end(fresh)
        comment = self.request("agent", "7", env={"EVENT": "issue_comment"})
        self.dispatcher.tick()
        self.assertEqual(self.status(comment), "requested")

    def test_in_manual_two_picked_requests_of_one_issue_run_one_after_the_other(self):
        self.hold = {"state": "manual", "picked": [], "picked_runs": []}
        first = self.request("agent", "8", env={"EVENT": "issue_comment"})
        second = self.request("agent", "8", env={"EVENT": "issue_comment", "REPLY_PR": "80"})
        self.dispatcher.tick()
        self.hold = {"state": "manual", "picked": [], "picked_runs": [first, second]}
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in (first, second)], ["running", "requested"])
        self.end(first)
        self.dispatcher.tick()
        self.assertEqual(self.status(second), "running")

    def test_in_manual_a_request_made_in_auto_waits_for_start_after_the_switch(self):
        self.configure({"slots": {"agent": 1}})
        self.hold = {"state": "running", "picked": [], "picked_runs": []}
        busy = self.request("agent", "pr-40")
        waiting = self.request("agent", "8", env={"EVENT": "issue_comment"})
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in (busy, waiting)], ["running", "requested"])
        self.hold = {"state": "manual", "picked": [], "picked_runs": []}
        self.end(busy)
        self.dispatcher.tick()
        self.assertEqual(self.status(waiting), "requested")
        self.assertIn(f"Run {waiting} waits: the desk is in Manual and nobody pressed Start on it; Start runs it.",
                      self.said)

    def test_in_manual_branch_checks_still_start(self):
        self.hold = {"state": "manual", "picked": [], "picked_runs": []}
        self.assertIsNone(self.dispatcher.held({"id": 1, "kind": "check", "project": REPO,
                                                "target": "agent/issue-7", "env": {}}))

    def test_in_auto_every_request_starts_and_picks_change_nothing(self):
        self.hold = {"state": "running", "picked": [], "picked_runs": []}
        runs = [self.request("agent", "8", env={"EVENT": "issue_comment"}), self.request("chore", "audit")]
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["running", "running"])

    def test_a_pick_on_hold_starts_its_request_on_resume_into_manual(self):
        picked = self.request("agent", "7", env={"EVENT": "issues"})
        unpicked = self.request("agent", "8", env={"EVENT": "issues"})
        fix = self.request("fix", "82")
        self.hold = {"state": "held", "before": "manual", "picked": [f"{REPO}#7"], "picked_runs": [fix]}
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in (picked, unpicked, fix)], ["requested"] * 3)
        self.hold = {"state": "manual", "picked": [f"{REPO}#7"], "picked_runs": [fix]}
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in (picked, unpicked, fix)], ["running", "requested", "running"])


class RequestTest(DispatchCase):
    def run_request(self, *argv):
        return dispatch.request_main(dispatch.parser().parse_args(["request", *argv]), self.store)

    def test_a_request_writes_a_requested_run_and_its_event(self):
        run = self.run_request("agent", "12", "--benchmark", "--env", "REPLY_PR=14")
        self.assertEqual(self.status(run), "requested")
        event = self.store.request_of(run)
        self.assertEqual((event["number"], event["author"]), (12, "you"))
        self.assertEqual(event["payload"], {"run": run, "kind": "agent", "target": "12", "benchmark": True,
                                            "env": {"REPLY_PR": "14"}})
        self.assertEqual(self.store.project_path(REPO), str(self.checkout))

    def test_a_request_refuses_a_check_a_bad_target_or_other_variables(self):
        for argv in (("check", "12"), ("agent", "abc"), ("fix", "pr-3"), ("chore", "Audit"),
                     ("chore", "audit", "--benchmark"), ("agent", "3", "--env", "PATH=/tmp")):
            with self.assertRaises(store.StoreError, msg=argv):
                self.run_request(*argv)


if __name__ == "__main__":
    unittest.main()
