"""Tests for agent-desk/lib/run_end.py, the exit handler that records how a run's unit ended."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_end  # noqa: E402
from test_dispatch import REPO, DispatchCase  # noqa: E402


class StoppedTest(DispatchCase):
    reported = ()

    def setUp(self):
        super().setUp()
        self.reported = []

    def started(self, kind, target):
        run = self.request(kind, target)
        self.dispatcher.tick()
        return run

    def stop(self, run, result, code, status):
        released = []
        environ = {"AGENT_DESK_RUN": str(run), "SERVICE_RESULT": result, "EXIT_CODE": code, "EXIT_STATUS": status,
                   "GH_REPO": REPO}
        ending = run_end.stopped(self.store, environ,
                                  release=lambda *args, **kw: released.append((*args, *kw.values())),
                                  report=lambda *args: self.reported.append(args))
        return ending, released

    def test_outcomes_follow_what_systemd_reports(self):
        self.assertEqual(run_end.outcome("success", "exited", "0"), ("succeeded", 0))
        self.assertEqual(run_end.outcome("exit-code", "exited", "3"), ("failed", 3))
        self.assertEqual(run_end.outcome("success", "killed", "TERM"), ("cancelled", None))
        self.assertEqual(run_end.outcome("timeout", "killed", "TERM"), ("timed-out", None))
        self.assertEqual(run_end.outcome("oom-kill", "killed", "KILL"), ("failed", None))

    def test_a_failed_cancelled_or_timed_out_agent_run_releases_its_issue_and_records_the_end(self):
        for result, code, status, ending in (("exit-code", "exited", "1", "failed"),
                                             ("success", "killed", "TERM", "cancelled"),
                                             ("timeout", "killed", "TERM", "timed-out")):
            run = self.started("agent", "7")
            got, released = self.stop(run, result, code, status)
            self.assertEqual(got, ending)
            self.assertEqual(released[0][:3], ("7", ending, self.store.run(run)["unit"]))
            self.assertNotIn("AGENT_DESK_RUN", released[0][3])
            self.assertEqual(self.status(run), ending)
            self.dispatcher.tick()

    def test_a_run_that_stopped_early_records_so_and_keeps_its_issue(self):
        self.assertEqual(run_end.outcome("exit-code", "exited", "75"), ("stopped", 75))
        run = self.started("agent", "7")
        self.assertEqual(self.stop(run, "exit-code", "exited", "75"), ("stopped", []))
        self.assertEqual(self.status(run), "stopped")
        self.assertEqual(self.store.run(run)["exit_code"], 75)
        self.assertEqual(self.reported, [])

    def test_a_finished_run_or_a_run_on_no_issue_releases_nothing(self):
        run = self.started("agent", "7")
        self.assertEqual(self.stop(run, "success", "exited", "0"), ("succeeded", []))
        self.assertEqual(self.store.run(run)["exit_code"], 0)
        for kind, target in (("chore", "audit"), ("fix", "8"), ("agent", "pr-9")):
            run = self.started(kind, target)
            self.assertEqual(self.stop(run, "exit-code", "exited", "1"), ("failed", []))

    def test_a_discussion_of_an_idea_that_ended_so_releases_it_as_a_discussion(self):
        for result, code, status, ending in (("exit-code", "exited", "1", "failed"),
                                             ("timeout", "killed", "TERM", "timed-out")):
            run = self.started("agent", "idea-7")
            got, released = self.stop(run, result, code, status)
            self.assertEqual(got, ending)
            self.assertEqual(len(released), 1)
            self.assertEqual(released[0][:3], ("7", ending, self.store.run(run)["unit"]))
            self.assertEqual(released[0][4:], (True,))
            self.assertEqual(self.reported, [])
            self.dispatcher.tick()
        run = self.started("agent", "idea-7")
        self.assertEqual(self.stop(run, "success", "exited", "0"), ("succeeded", []))

    def test_a_cancelled_or_timed_out_run_on_a_pr_says_so_on_the_pr(self):
        for kind, target, pr in (("fix", "8", "8"), ("agent", "pr-9", "9")):
            for result in ("success", "timeout"):
                run = self.started(kind, target)
                ending, released = self.stop(run, result, "killed", "TERM")
                self.assertEqual(released, [])
                self.assertEqual(self.reported.pop()[1:], (REPO, pr, kind, self.store.run(run)["unit"]))
                self.assertEqual(self.status(run), ending)
                self.dispatcher.tick()
        run = self.started("chore", "audit")
        self.stop(run, "success", "killed", "TERM")
        self.assertEqual(self.reported, [])

    def test_the_end_is_recorded_when_the_release_fails(self):
        run = self.started("agent", "7")

        def broken(*args):
            raise OSError("gh is missing")

        with self.assertRaises(OSError):
            run_end.stopped(self.store, {"AGENT_DESK_RUN": str(run), "SERVICE_RESULT": "exit-code",
                                          "EXIT_CODE": "exited", "EXIT_STATUS": "1"}, release=broken)
        self.assertEqual(self.status(run), "failed")


    def test_the_report_on_a_review_goes_in_the_store_as_the_desk(self):
        review = self.store.create(REPO, "review", "Fix", branch="agent/chore-audit-1")
        unit = "agent-desk-fix-org-game-8-4.service"
        with mock.patch.dict(os.environ, {"AGENT_DESK_AUTHOR": "desk"}):
            run_end.report_pr(self.store, REPO, str(review), "fix", unit)
        [note] = self.store.item(REPO, review)["comments"]
        self.assertEqual(note["author"]["login"], "desk")
        self.assertEqual(note["body"], f"The fix run was cancelled or timed out (log: `journalctl --user -u {unit}`)."
                                       "\n\n<sub>Posted by agent-desk in agent-desk-fix-org-game-8-4</sub>")
        out = io.StringIO()
        with redirect_stdout(out):
            run_end.report_pr(self.store, REPO, "40", "fix", unit)
        self.assertIn("Couldn't say so on #40", out.getvalue())


if __name__ == "__main__":
    unittest.main()
