"""Tests for agent-desk/lib/board.py, the one read behind the inbox's board, and the golden file it shares with the
pane's tests."""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import backup  # noqa: E402
import board  # noqa: E402
import fixes  # noqa: E402
import hold  # noqa: E402
import merges  # noqa: E402
import pushes  # noqa: E402
import store  # noqa: E402

REPO = "org/game"
# The pane's tests build the board from this file, so a field renamed on either side fails a suite.
GOLDEN = Path(__file__).resolve().parents[1] / "inbox" / "hooks" / "board.golden.ts"
GOLDEN_HEAD = (
    "// What `agent-desk board --json` prints for the store tests/test_board.py seeds, which board.test.ts builds the\n"
    "// board from. tests/test_board.py fails when the command's output differs from it; to change it, run\n"
    "// AGENT_DESK_GOLDEN=write python3 -B -m unittest tests.test_board, then make board.test.ts pass again.\n"
    "import type { StoreBoard } from './board.ts'\n"
    "\n"
    "export const GOLDEN: StoreBoard = ")
NOW = 1793361600  # 2026-10-30T12:00:00Z
CHORES = ["Chore          Every  Last run                     Next",
          "audit          7d     done Mon Nov 9 08:41         Mon Nov 16 08:41"]


class BoardCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.clock = ["2026-11-10T09:00:00Z"]
        env = {key: value for key, value in os.environ.items()
               if key not in ("AGENT_DESK_RUN", "AGENT_DESK_AUTHOR")}
        env.update(GH_REPO=REPO, TZ="UTC")
        patches = [mock.patch.dict(os.environ, env, clear=True),
                   mock.patch.object(store, "STATE", self.folder),
                   mock.patch.object(store, "DATABASE", self.folder / "desk.sqlite"),
                   mock.patch.object(store, "now", lambda: self.clock[0]),
                   mock.patch.object(merges, "STATE", self.folder / "merges"),
                   mock.patch.object(pushes, "STATE", self.folder / "pushes"),
                   mock.patch.object(fixes, "STATE", self.folder / "fixes"),
                   mock.patch.object(hold, "HOLD", self.folder / "hold.json"),
                   mock.patch.object(board, "machine", lambda: "desk-1"),
                   mock.patch.object(board, "web_address", lambda: "http://127.0.0.1:7480")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        time.tzset()
        self.addCleanup(time.tzset)
        self.store = store.Store()
        self.addCleanup(self.store.close)

    def at(self, stamp):
        self.clock[0] = f"2026-11-10T{stamp}:00Z"

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            status = board.main(list(argv))
        return status, out.getvalue().strip()


class GoldenTest(BoardCase):
    def seed(self):
        """A project with an item in each of the board's columns."""
        desk, issue = self.store, lambda title, **extra: self.store.create(REPO, "issue", title, **extra)
        open_one = issue("Add a map")
        issue("Picked for Manual", labels=["agent-ready"])
        issue("Waits on its blocker", labels=["waiting"], blocked_by=[open_one])
        issue("Waits on its serial group", labels=["waiting", "serial:web"])
        holder = issue("Holds the serial slot", labels=["in-progress", "serial:web"])
        desk.comment(REPO, holder, f"An agent started on `agent/issue-{holder}`.")
        issue("Pick the colors", labels=["needs-design"])
        issue("Parked", labels=["on-hold"])
        issue("Maybe later", labels=["idea"])
        working = issue("Being worked", labels=["in-progress", "benchmark"])
        reviews = {}
        for name, draft in (("passing", False), ("failing", False), ("conflicting", False), ("merging", False),
                            ("draft", True), ("merged", False)):
            number = issue(f"Has a {name} review", labels=["in-progress"])
            branch = f"agent/issue-{number}"
            reviews[name] = desk.create(REPO, "review", f"The {name} review", branch=branch, draft=draft)
            desk.branch_moved(REPO, branch, f"{name}-head", None)
            if name == "draft":
                desk.comment(REPO, number, f"An agent started on `{branch}`.")
                desk.comment(REPO, number, f"The agent finished: https://example.test/{reviews[name]} (draft).")
            if name in ("passing", "merging", "draft"):
                desk.check_ended(desk.check_queue(REPO, branch, f"{name}-head"), "passed")
            if name == "failing":
                check = desk.check_queue(REPO, branch, f"{name}-head")
                desk.check_started(check, "/state/checks/org__game/9.log")
                desk.check_ended(check, "failed", rerun=True)
                self.at("10:45")
                retry = desk.checks(("queued",), REPO, branch)[0]["id"]
                desk.check_started(retry, f"/state/checks/org__game/{retry}.log")
                self.at("09:00")
            if name == "conflicting":
                desk.merge_tried(REPO, branch, f"{name}-head", "main", "main-head", "CONFLICTING")
            if name == "merged":
                self.at("10:30")
                desk.merge(REPO, reviews[name], "f00d")
                self.at("09:00")
        # Fix sent the conflicting review back to its agent, and the dispatcher hasn't answered the comment yet.
        desk.comment_answered(None, None, desk.last_event(), "start")
        desk.comment(REPO, reviews["conflicting"], "Merge main and fix the conflicts.", to_agent=True)
        self.at("11:00")
        run = desk.request_run(REPO, "agent", str(working))
        desk.run_started(run, f"agent-desk-agent-org-game-{working}-{run}.service")
        stopped = desk.request_run(REPO, "agent", str(holder))
        desk.run_started(stopped, "u.service")
        self.at("11:30")
        desk.run_ended(stopped, "failed", 1)
        merges.save(merges.state_path(REPO), {"queue": [{"pr": reviews["merging"], "added": NOW}], "dropped": []})
        fixes.save_plan(REPO, [{"pr": reviews["conflicting"], "verdict": "auto-fix on Merge"}])
        hold.HOLD.write_text(json.dumps({"state": "manual", "since": None, "picked": [f"{REPO}#2"]}))
        board.chores_path(REPO).parent.mkdir(parents=True)
        board.chores_path(REPO).write_text("\n".join(CHORES) + "\n")
        backup.save(store.DATABASE, {"at": NOW - 600, "remote": "origin", "committedAt": NOW - 600,
                                     "pushedAt": NOW - 3000, "waiting": True})

    def test_the_board_read_matches_the_golden_file_the_panes_tests_read(self):
        self.seed()
        read = board.read(self.store, REPO, NOW)
        if os.environ.get("AGENT_DESK_GOLDEN") == "write":
            GOLDEN.write_text(GOLDEN_HEAD + json.dumps(read, indent=2) + "\n")
        golden = json.loads(GOLDEN.read_text().removeprefix(GOLDEN_HEAD))
        self.assertEqual(read, golden, f"agent-desk board --json no longer prints {GOLDEN.name}; see its header")

    def test_the_read_carries_the_saved_queue_plan_mode_chores_clock_and_machine(self):
        self.seed()
        read = board.read(self.store, REPO, NOW)
        self.assertEqual(read["queue"], {"queue": [{"pr": 17, "note": "next to merge"}], "dropped": []})
        self.assertEqual(read["fixes"], [{"pr": 15, "verdict": "auto-fix on Merge"}])
        self.assertEqual((read["hold"]["state"], read["hold"]["picked"]), ("manual", [f"{REPO}#2"]))
        self.assertEqual((read["chores"], read["at"], read["machine"], read["web"]),
                         (CHORES, "12:00 +0000", "desk-1", "http://127.0.0.1:7480"))
        reviews = {one["title"]: one for one in read["reviews"]}
        self.assertEqual(reviews["The conflicting review"]["mergeable"], "CONFLICTING")
        self.assertEqual(reviews["The failing review"]["checks"][0]["status"], "failed")

    def test_the_read_carries_the_default_slots_and_the_running_retry(self):
        self.seed()
        read = board.read(self.store, REPO, NOW)
        self.assertEqual(read["slots"], {"agent": 3, "chore": 1, "fix": 1, "check": 2, "benchmark": 2})
        failing = next(one for one in read["reviews"] if one["title"] == "The failing review")
        self.assertEqual(read["checks"], [{"branch": failing["branch"], "review": failing["number"],
                                           "head": "failing-head", "startedAt": "2026-11-10T10:45:00Z"}])


class WebAddressTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = Path(folder.name)
        env = {key: value for key, value in os.environ.items() if key != "AGENT_DESK_WEB_PORT"}
        patch = mock.patch.dict(os.environ, {**env, "XDG_CONFIG_HOME": str(self.config)}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, port):
        unit = self.config / "systemd" / "user" / "agent-desk-web.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(f"[Unit]\nDescription=agent-desk\n\n[Service]\nEnvironment=AGENT_DESK_WEB_PORT={port}\n"
                        "ExecStart=node server.ts\n")

    def test_the_page_serves_at_7480_without_a_port_or_a_service(self):
        self.assertEqual(board.web_address(), "http://127.0.0.1:7480")

    def test_the_installed_service_names_the_port(self):
        self.install(7490)
        self.assertEqual(board.web_address(), "http://127.0.0.1:7490")

    def test_agent_desk_web_port_wins_as_the_server_reads_it(self):
        self.install(7490)
        with mock.patch.dict(os.environ, {"AGENT_DESK_WEB_PORT": "7481"}):
            self.assertEqual(board.web_address(), "http://127.0.0.1:7481")


class ReadTest(BoardCase):
    def test_a_project_with_nothing_saved_reads_an_empty_queue_plan_and_chores(self):
        read = board.read(self.store, REPO, NOW)
        self.assertEqual((read["queue"], read["fixes"], read["chores"], read["hold"]["state"]),
                         ({"queue": [], "dropped": []}, [], [], "running"))

    def test_a_fixes_plan_saved_with_fixes_off_is_empty_and_a_broken_one_reads_empty(self):
        fixes.save_plan(REPO, [{"pr": 3, "verdict": "auto-fix on Merge"}, {"pr": "x"}])
        self.assertEqual(fixes.saved_plan(REPO), [{"pr": 3, "verdict": "auto-fix on Merge"}])
        fixes.plan_path(REPO).write_text("not json")
        self.assertEqual(fixes.saved_plan(REPO), [])

    def test_the_queue_says_how_far_main_is_ahead_of_origin_in_the_projects_checkout(self):
        origin, checkout = self.folder / "origin.git", self.folder / "repo"
        config = self.folder / "gitconfig"
        config.write_text("[user]\n\tname = T\n\temail = t@example.com\n[init]\n\tdefaultBranch = main\n")
        os.environ.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1")
        for args in (["init", "-q", "--bare", str(origin)], ["clone", "-q", str(origin), str(checkout)],
                     ["-C", str(checkout), "commit", "-q", "--allow-empty", "-m", "start"],
                     ["-C", str(checkout), "push", "-q", "origin", "main"],
                     ["-C", str(checkout), "commit", "-q", "--allow-empty", "-m", "merged one"],
                     ["-C", str(checkout), "commit", "-q", "--allow-empty", "-m", "merged two"]):
            subprocess.run(["git", *args], check=True, capture_output=True)
        self.store.project(REPO, str(checkout))
        read = board.read(self.store, REPO, NOW)
        self.assertEqual(read["queue"], {"queue": [], "dropped": [], "ahead": 2, "push": {"state": "ok"},
                                         "agentBranches": {"pushed": 0, "waiting": 0}})

    def test_the_projects_config_sets_its_slots_over_the_defaults(self):
        checkout = self.folder / "repo"
        checkout.mkdir()
        (checkout / ".agent-desk.json").write_text(json.dumps({"slots": {"agent": 1, "fix": 0, "check": "two"}}))
        self.store.project(REPO, str(checkout))
        self.assertEqual(board.read(self.store, REPO, NOW)["slots"],
                         {"agent": 1, "chore": 1, "fix": 0, "check": 2, "benchmark": 2})

    def test_a_running_check_of_a_branch_without_a_review_names_none_and_ended_ones_leave(self):
        running, ended = (self.store.check_queue(REPO, branch, "h1") for branch in ("agent/chore-audit", "agent/x"))
        self.store.check_started(running, "/tmp/1.log")
        self.store.check_ended(ended, "passed")
        self.store.check_started(self.store.check_queue("org/other", "agent/issue-1", "h2"), "/tmp/2.log")
        self.assertEqual(board.read(self.store, REPO, NOW)["checks"],
                         [{"branch": "agent/chore-audit", "review": None, "head": "h1",
                           "startedAt": "2026-11-10T09:00:00Z"}])

    def test_a_queued_check_the_dispatcher_holds_lists_its_gate_and_reason_until_it_starts(self):
        held, free = (self.store.check_queue(REPO, branch, "h1") for branch in ("merge-queue/1", "agent/x"))
        self.store.check_waits(held, "slot", "a free check slot")
        self.assertEqual(board.read(self.store, REPO, NOW)["waitingChecks"],
                         [{"branch": "merge-queue/1", "review": None, "head": "h1", "gate": "slot",
                           "reason": "a free check slot"}])
        self.store.check_started(held, "/tmp/1.log")
        self.assertEqual(board.read(self.store, REPO, NOW)["waitingChecks"], [])
        self.assertFalse(self.store.check_waits(held, "slot", "a free check slot"))
        self.assertTrue(self.store.check_waits(free, "mode", "Resume"))

    def test_board_json_prints_the_read_and_plain_output_lists_it(self):
        self.store.create(REPO, "issue", "Add a store", labels=["in-progress"])
        self.store.create(REPO, "review", "The store", branch="agent/issue-1", draft=True)
        status, out = self.cli("--json")
        self.assertEqual((status, [one["title"] for one in json.loads(out)["issues"]]), (0, ["Add a store"]))
        status, out = self.cli()
        self.assertEqual(out.splitlines(), ["#1\tin-progress\tAdd a store", "#2\tdraft\tThe store (no checks)"])


if __name__ == "__main__":
    unittest.main()
