"""Tests for lib/run-chore.sh and lib/chore.sh: each runs against a temporary store, a real origin and main checkout in a
temporary folder, and a stand-in for claude that can act as the agent. A chore finds its review in the store and asks
the dispatcher for its next run there; gh serves only lib/budget.py. lib/chores.sh reads
each chore's runs from the same store, with gh serving only its runner and open-PR reads."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work_scripts import REPO, SIGNED, UNIT, Desk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import chore_rename  # noqa: E402

SKILL = "tidy"
# The agent opens a review of the branch the run checked out, as the prompt tells it to.
OPEN_REVIEW = '"$DESK" review create --branch "$(git branch --show-current)" --title Tidy'


class ChoreDesk(Desk):
    def setUp(self):
        super().setUp()
        (self.main / ".agent-desk.json").write_text(json.dumps({"resources": {
            "needGB": {"chore": 0}, "reserveGB": 0, "maxMemoryPressure": 100}}))

    def requests(self):
        """Each chore run asked for, with the author of its request."""
        db = sqlite3.connect(self.database)
        try:
            return db.execute("SELECT runs.target, runs.status, events.author FROM runs JOIN events"
                              " ON events.type = 'run-requested' AND json_extract(events.payload, '$.run') = runs.id"
                              " WHERE runs.kind = 'chore' ORDER BY runs.id").fetchall()
        finally:
            db.close()

    def chore_worktrees(self):
        return sorted(path.name for path in self.root.glob("desk-chore-tidy-*"))


class RunChoreTest(ChoreDesk):
    def test_a_run_finds_the_review_its_agent_opened_and_keeps_its_worktree(self):
        result = self.run_lib("run-chore.sh", SKILL, FAKE_AGENT=OPEN_REVIEW)
        self.assertEqual(result.returncode, 0, result.stderr)
        [review] = self.store.items(REPO, "review")
        self.assertIn(f"The chore opened review #{review['number']}.", result.stdout)
        self.assertRegex(review["headRefName"], r"^agent/chore-tidy-\d{8}-\d{4}$")
        self.assertEqual(self.chore_worktrees(), [review["headRefName"].replace("agent/", "desk-")])
        [entry] = (self.root / "state" / "agent-desk" / "chores" / "o__r" / f"{SKILL}.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(entry)["pr"], review["number"])
        self.assertEqual(self.requests(), [])

    def test_the_prompt_asks_for_a_review_in_the_store(self):
        self.run_lib("run-chore.sh", SKILL)
        args = self.claude_args()
        prompt = "\n".join(args[2:args.index("--permission-mode")])
        self.assertTrue(prompt.startswith(f"/{SKILL}\n"))
        branch = "agent/" + Path(args[0]).name.removeprefix("desk-")
        self.assertIn(f"`agent-desk review create --branch {branch} --title <title> --body-file <file>`", prompt)
        self.assertNotIn("workflow", prompt)

    def test_a_run_without_a_review_removes_its_worktree(self):
        result = self.run_lib("run-chore.sh", SKILL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("The chore opened no review.", result.stdout)
        self.assertEqual(self.chore_worktrees(), [])

    def test_a_chore_queued_again_asks_the_dispatcher_for_its_next_run_as_the_desk(self):
        self.assertEqual(self.run_lib("chore.sh", SKILL, "2").returncode, 0)
        result = self.run_lib("run-chore.sh", SKILL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Asked for the next queued run of {SKILL}.", result.stdout)
        # Both requests carry the desk as their author, though the run's unit sets AGENT_DESK_RUN, so the dispatcher
        # starts them.
        self.assertEqual(self.requests(), [(SKILL, "requested", "desk"), (SKILL, "requested", "desk")])
        self.run_lib("run-chore.sh", SKILL)
        self.assertEqual(len(self.requests()), 2)

    def test_a_failed_run_asks_for_no_next_run(self):
        self.run_lib("chore.sh", SKILL, "forever")
        result = self.run_lib("run-chore.sh", SKILL, FAKE_AGENT="exit 1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Asked for the next queued run", result.stdout)
        self.assertEqual(len(self.requests()), 1)

    def test_a_run_that_stops_early_says_so_on_its_review_and_asks_for_no_next_run(self):
        self.run_lib("chore.sh", SKILL, "forever")
        result = self.run_lib("run-chore.sh", SKILL, FAKE_AGENT=OPEN_REVIEW, **self.stopped_early("just bench"))
        self.assertEqual(result.returncode, 75, result.stderr)
        early = "The agent stopped early: it ended its turn while `just bench` still ran, so the run stopped it"
        self.assertIn(f"{early}.", result.stdout)
        self.assertNotIn("Asked for the next queued run", result.stdout)
        self.assertEqual(len(self.requests()), 1)
        [review] = self.store.items(REPO, "review")
        self.assertEqual(self.notes(review["number"]), [
            ("desk", f"{early} (log: `journalctl --user -u {UNIT}`).{SIGNED}")])


class ChoreTest(ChoreDesk):
    def test_a_chore_starts_by_a_request_to_the_dispatcher(self):
        result = self.run_lib("chore.sh", SKILL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Asked for a run of {SKILL}.", result.stdout)
        self.assertEqual(self.requests(), [(SKILL, "requested", "desk")])
        self.assertNotIn("workflow", self.gh_log.read_text())

    def test_stop_asks_for_no_run(self):
        result = self.run_lib("chore.sh", SKILL, "stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests(), [])

    def test_a_count_that_isnt_one_is_refused(self):
        result = self.run_lib("chore.sh", SKILL, "twice")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.requests(), [])


class ScheduleTest(ChoreDesk):
    """lib/chores.sh against the store's runs, with no open PR on the gh stand-in."""

    def setUp(self):
        super().setUp()
        (self.main / ".agent-desk.json").write_text(json.dumps({
            "presence": "always",
            "resources": {"needGB": {"chore": 0}, "reserveGB": 0, "maxMemoryPressure": 100},
            "chores": {"every": {SKILL: 3}, "gapHours": 1, "minFreeGB": 0}}))
        (self.root / "fakes" / "gh").write_text(
            f'#!/bin/bash\necho "$*" >>"{self.gh_log}"\n'
            'case "$*" in\n'
            '  "api repos/o/r/actions/runners"*) echo "r1 online false a";;\n'
            '  "api repos/o/r --jq"*) echo User;;\n'
            '  "run list"*) echo 0;;\n'
            '  "pr list"*) echo "[]";;\n'
            'esac\n')

    def chores(self, mode):
        # The timer runs chores.sh outside any unit.
        result = self.run_lib("chores.sh", mode, AGENT_DESK_RUN="", AGENT_DESK_UNIT="")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def row(self):
        """The chore's row of chores status: its last run and when it runs next."""
        [line] = [line for line in self.chores("status").splitlines() if line.startswith("tidy ")]
        return line[22:50].strip(), line[50:].strip()

    def test_a_chore_never_run_is_due_and_a_tick_asks_for_it_once(self):
        self.assertEqual(self.row(), ("never", "now"))
        self.assertIn(f"Asked for a run of {SKILL}.", self.chores("tick"))
        self.assertEqual(self.requests(), [(SKILL, "requested", "desk")])
        self.assertEqual(self.chores("tick").strip(), "No chore is due.")
        self.assertEqual(len(self.requests()), 1)
        self.assertNotIn("workflow run", self.gh_log.read_text())

    def test_a_chore_started_by_hand_shows_running_then_run_and_is_not_due_after(self):
        self.run_lib("chore.sh", SKILL)
        self.assertEqual(self.row(), ("queued for 0 min", "after this run"))
        self.assertEqual(self.chores("tick").strip(), "No chore is due.")
        [run] = self.store.runs(("requested",))
        self.store.run_started(run["id"], "agent-desk-chore-o-r-tidy-1.service")
        self.assertEqual(self.row(), ("running for 0 min", "after this run"))
        self.assertEqual(self.chores("tick").strip(), "No chore is due.")
        self.store.run_ended(run["id"], "succeeded", 0)
        last, upcoming = self.row()
        self.assertRegex(last, r"^done \w{3} \w{3} \d+ \d\d:\d\d$")
        self.assertNotIn("now", upcoming)
        self.assertRegex(self.chores("tick"), rf"^{SKILL} starts no earlier than ")
        self.assertEqual(self.requests(), [(SKILL, "succeeded", "desk")])

    def test_a_failed_run_shows_how_it_ended(self):
        run = self.store.request_run(REPO, "chore", SKILL)
        self.store.run_started(run, "agent-desk-chore-o-r-tidy-1.service")
        self.store.run_ended(run, "timed-out")
        self.assertRegex(self.row()[0], r"^timed-out \w{3} ")

    def test_the_run_working_shows_over_the_request_queued_behind_it(self):
        working = self.store.request_run(REPO, "chore", SKILL)
        self.store.run_started(working, "agent-desk-chore-o-r-tidy-1.service")
        self.store.request_run(REPO, "chore", SKILL)
        self.assertEqual(self.row()[0], "running for 0 min")

    def test_an_ignored_request_counts_as_no_run(self):
        self.store.run_ended(self.store.request_run(REPO, "chore", SKILL), "ignored")
        self.assertEqual(self.row(), ("never", "now"))

    def test_a_chore_that_files_issues_waits_on_their_count_and_one_that_opens_reviews_on_its_review(self):
        (self.main / ".agent-desk.json").write_text(json.dumps({
            "presence": "always", "resources": {"needGB": {"chore": 0}, "reserveGB": 0, "maxMemoryPressure": 100},
            "chores": {"every": {"audit": 3, "security-audit": 3, SKILL: 3}, "gapHours": 1, "minFreeGB": 0,
                       "maxAuditIssues": 2}}))

        def nexts():
            return {line.split()[0]: line[50:].strip() for line in self.chores("status").splitlines()[1:]}

        # Issues under another chore's label, or closed, hold nothing back.
        self.store.create(REPO, "issue", "Audit finding", labels=("audit",))
        closed = self.store.create(REPO, "issue", "Fixed hole", labels=("security",))
        self.store.set_state(REPO, closed, "closed")
        for title in ("Open hole", "Another hole"):
            self.store.create(REPO, "issue", title, labels=("security",))
        self.store.create(REPO, "review", "Tidy", branch=f"agent/chore-{SKILL}-20261113-1200")
        self.assertEqual(nexts(), {"audit": "now", "security-audit": "waiting on 2 open security issues",
                                   SKILL: "waiting on PR #5"})
        self.assertIn("Asked for a run of audit.", self.chores("tick"))
        self.store.create(REPO, "issue", "Second finding", labels=("audit",))
        self.assertEqual(nexts()["audit"], "waiting on 2 open audit issues")
        self.assertEqual(self.chores("tick").strip(), "No chore is due.")
        self.assertEqual(self.requests(), [("audit", "requested", "desk")])

    def test_a_long_chore_name_widens_its_column_and_keeps_its_prefix(self):
        long = "singleshot-untested-one"
        (self.main / ".agent-desk.json").write_text(json.dumps({
            "presence": "always", "chores": {"every": {SKILL: 3, long: 7}, "gapHours": 1, "minFreeGB": 0}}))
        header, *rows = self.chores("status").splitlines()
        self.assertEqual(header.index(" Every") + 1, len(long) + 1)
        self.assertEqual([row.split()[:2] for row in rows], [[SKILL, "3d"], [long, "7d"]])
        self.assertEqual({row.index(" 3d" if row.startswith(SKILL) else " 7d") + 1 for row in rows}, {len(long) + 1})

    def test_a_chore_renamed_while_its_review_waits_counts_from_its_runs_under_the_new_name(self):
        run = self.store.request_run(REPO, "chore", SKILL)
        self.store.run_started(run, "agent-desk-chore-o-r-tidy-1.service")
        self.store.run_ended(run, "succeeded", 0)
        chore_rename.move_runs(self.store, REPO, SKILL, "tidy-up")
        last, upcoming = self.row()
        self.assertRegex(last, r"^done \w{3} ")
        self.assertNotIn("now", upcoming)
        self.assertRegex(self.chores("tick"), rf"^{SKILL} starts no earlier than ")


if __name__ == "__main__":
    unittest.main()
