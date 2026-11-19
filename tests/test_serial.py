"""Tests for lib/serial.sh claim and release, run against a temporary store in a state folder of their own."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERIAL = ROOT / "lib" / "serial.sh"
sys.path.insert(0, str(ROOT / "lib"))

import store  # noqa: E402

REPO = "o/r"


class SerialCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.checkout = root / "checkout"
        (self.checkout / ".git").mkdir(parents=True)
        (self.checkout / ".agent-desk.json").write_text("{}")
        # Anything that reaches GitHub fails the test.
        fakes = root / "fakes"
        fakes.mkdir()
        (fakes / "gh").write_text("#!/bin/sh\necho \"gh $*\" >&2\nexit 1\n")
        (fakes / "gh").chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("AGENT_DESK_RUN", "AGENT_DESK_UNIT", "AGENT_DESK_AUTHOR")}
        self.env.update({"PATH": f"{fakes}:{os.environ['PATH']}", "AGENT_DESK_CHECKOUT": str(self.checkout),
                         "GH_REPO": REPO, "XDG_STATE_HOME": str(root / "state"),
                         "XDG_CONFIG_HOME": str(root / "config")})
        self.database = root / "state" / "agent-desk" / "desk.sqlite"
        self.store = store.Store(self.database)
        self.addCleanup(self.store.close)

    def issue(self, status="open", labels=(), blocked_by=()):
        number = self.store.create(REPO, "issue", "Work", labels=labels)
        self.store.set_state(REPO, number, status)
        for blocker in blocked_by:
            self.store.link(REPO, blocker, number, "blocks")
        return number

    def serial(self, *args):
        return subprocess.run(["bash", str(SERIAL), *args], cwd=self.checkout, env=self.env, capture_output=True,
                              text=True, timeout=60)

    def item(self, number):
        fresh = store.Store(self.database)
        try:
            return fresh.item(REPO, number)
        finally:
            fresh.close()

    def notes(self, number):
        return [(one["author"]["login"], one["body"]) for one in self.item(number)["comments"]]

    def requests(self):
        fresh = store.Store(self.database)
        try:
            return [(row["target"], fresh.request_of(row["id"])["payload"]["env"], fresh.request_of(row["id"])["author"])
                    for row in fresh.runs(("requested",))]
        finally:
            fresh.close()


class ClaimTest(SerialCase):
    def test_a_claim_moves_a_queued_or_needs_design_issue_to_in_progress(self):
        for status in ("queued", "needs-design", "waiting", "open"):
            number = self.issue(status, labels=["bug"])
            done = self.serial("claim", str(number))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.item(number)["status"], "in-progress")
            self.assertEqual(self.notes(number), [])

    def test_a_fresh_start_with_an_open_blocker_waits_and_says_so_once(self):
        blocker = self.issue()
        closed = self.issue("closed")
        number = self.issue("queued", blocked_by=[blocker, closed])
        done = self.serial("claim", str(number))
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(done.stdout, f"#{number} waits for #{blocker}.\n")
        self.assertEqual(self.item(number)["status"], "waiting")
        self.assertEqual(self.notes(number), [
            ("desk", f"Waiting for #{blocker} to close. This issue starts on its own once nothing blocks it, from a "
                     "main that has that work.\n\n<sub>Posted by agent-desk</sub>")])

    def test_an_issue_already_worked_goes_on_past_its_blockers(self):
        blocker = self.issue()
        number = self.issue("in-progress", blocked_by=[blocker])
        self.assertEqual(self.serial("claim", str(number)).returncode, 0)
        self.assertEqual(self.item(number)["status"], "in-progress")

    def test_a_serial_issue_waits_while_another_in_its_group_is_in_progress(self):
        holder = self.issue("in-progress", labels=["serial:audio"])
        other_group = self.issue("in-progress", labels=["serial:water"])
        number = self.issue("queued", labels=["serial:audio", "bug"])
        done = self.serial("claim", str(number))
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(done.stdout, f"#{number} waits for #{holder}.\n")
        self.assertEqual(self.item(number)["status"], "waiting")
        self.assertIn("Waiting for the serial slot.", self.notes(number)[0][1])
        # Waiting already, a second claim says nothing more.
        self.assertEqual(self.serial("claim", str(number)).returncode, 3)
        self.assertEqual(len(self.notes(number)), 1)
        self.store.set_state(REPO, holder, "closed")
        self.assertEqual(self.serial("claim", str(number)).returncode, 0)
        self.assertEqual(self.item(number)["status"], "in-progress")
        self.assertEqual(self.item(other_group)["status"], "in-progress")

    def test_a_closed_issue_stays_closed(self):
        number = self.issue("closed")
        self.assertEqual(self.serial("claim", str(number)).returncode, 0)
        self.assertEqual(self.item(number)["status"], "closed")

    def test_an_issue_the_store_lacks_fails_the_claim(self):
        done = self.serial("claim", "99")
        self.assertEqual(done.returncode, 1)
        self.assertIn("no issue #99", done.stderr)


class ReleaseTest(SerialCase):
    def test_release_asks_for_each_waiting_issue_nothing_blocks_as_a_label_would(self):
        blocker = self.issue("closed")
        free = self.issue("waiting", blocked_by=[blocker])
        still_open = self.issue()
        blocked = self.issue("waiting", blocked_by=[still_open])
        done = self.serial("release")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"Started #{free}.\n")
        self.assertEqual((self.item(free)["status"], self.item(blocked)["status"]), ("queued", "waiting"))
        self.assertEqual(self.requests(), [(str(free), {"EVENT": "issues"}, "desk")])

    def test_release_starts_the_oldest_waiting_issue_of_a_serial_group(self):
        first = self.issue("waiting", labels=["serial:audio"])
        second = self.issue("waiting", labels=["serial:audio"])
        self.assertEqual(self.serial("release").stdout, f"Started #{first}.\n")
        self.assertEqual((self.item(first)["status"], self.item(second)["status"]), ("queued", "waiting"))
        # The queued issue holds the group's slot until its run claims it and then finishes.
        self.assertEqual(self.serial("release").stdout, "")
        self.store.set_state(REPO, first, "in-progress")
        self.assertEqual(self.serial("release").stdout, "")
        self.store.set_state(REPO, first, "closed")
        self.assertEqual(self.serial("release").stdout, f"Started #{second}.\n")

    def test_release_leaves_an_issue_whose_run_waits_at_the_dispatcher(self):
        number = self.issue("waiting")
        self.store.request_run(REPO, "agent", str(number), env={"EVENT": "issues"})
        self.assertEqual(self.serial("release").stdout, "")
        self.assertEqual(self.item(number)["status"], "waiting")
        self.assertEqual(len(self.requests()), 1)

    def manual(self, *picked):
        """Puts the project in Manual with the given issues picked."""
        hold = Path(self.env["XDG_STATE_HOME"]) / "agent-desk" / "hold.json"
        hold.write_text(json.dumps({"state": "running", "since": None, "projects": {REPO: "manual"},
                                    "picked": [f"{REPO}#{number}" for number in picked] + ["other/repo#1"]}))

    def test_in_manual_a_queued_issue_waiting_for_start_leaves_the_slot_to_a_picked_one(self):
        # The oldest issue was released to queued and waits for Start; the newest is picked.
        stuck = self.issue("queued", labels=["serial:atlas"])
        self.store.request_run(REPO, "agent", str(stuck), env={"EVENT": "issues"})
        unpicked = self.issue("waiting", labels=["serial:atlas"])
        picked = self.issue("waiting", labels=["serial:atlas"])
        free = self.issue("waiting")
        self.manual(picked)
        done = self.serial("release")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"Started #{picked}.\nStarted #{free}.\n")
        self.assertEqual([self.item(one)["status"] for one in (stuck, unpicked, picked, free)],
                         ["queued", "waiting", "queued", "queued"])
        # The picked issue, queued now, holds the group's slot.
        self.manual(picked, unpicked)
        self.assertEqual(self.serial("release").stdout, "")

    def test_in_manual_with_nothing_picked_no_serial_issue_starts(self):
        first = self.issue("waiting", labels=["serial:atlas"])
        self.issue("waiting", labels=["serial:atlas"])
        self.manual()
        self.assertEqual(self.serial("release").stdout, "")
        self.assertEqual(self.item(first)["status"], "waiting")

    def test_in_manual_a_picked_queued_issue_holds_its_slot(self):
        holder = self.issue("queued", labels=["serial:atlas"])
        number = self.issue("waiting", labels=["serial:atlas"])
        self.manual(holder, number)
        self.assertEqual(self.serial("release").stdout, "")

    def test_in_auto_a_queued_issue_holds_its_slot(self):
        holder = self.issue("queued", labels=["serial:atlas"])
        self.store.request_run(REPO, "agent", str(holder), env={"EVENT": "issues"})
        number = self.issue("waiting", labels=["serial:atlas"])
        self.assertEqual(self.serial("release").stdout, "")
        self.store.set_state(REPO, holder, "closed")
        self.assertEqual(self.serial("release").stdout, f"Started #{number}.\n")


if __name__ == "__main__":
    unittest.main()
