"""Tests for the weekly usage-limit gate in agent-desk/lib/dispatch.py, with lib/usage.py's check stubbed."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dispatch_case import REPO, DispatchCase, dispatch  # noqa: E402

NOTE = "weekly plan usage is 81%, at the 80% limit; resets Wed 04:00"


class UsageLimitTest(DispatchCase):
    def setUp(self):
        super().setUp()
        # What lib/usage.py's check says: why the limit holds, and why it can't read usage.
        self.usage = (NOTE, None)
        self.dispatcher.usage_check = lambda state: self.usage

    def test_the_limit_holds_agent_chore_and_fix_requests_without_a_slot_until_usage_falls(self):
        runs = [self.request("agent", "7"), self.request("chore", "audit"), self.request("fix", "12")]
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["requested"] * 3)
        self.assertEqual((self.systemd.started, self.memory.asked), ([], []))
        self.assertIn(f"Run {runs[0]} waits: {NOTE}.", self.said)
        self.assertEqual([self.waits(run) for run in runs], [("usage", "weekly usage to fall under its limit")] * 3)
        # A new reading over the limit records nothing again.
        self.usage = ("weekly plan usage is 82%, at the 80% limit; resets Wed 04:00", None)
        self.time += dispatch.RETRY_SECONDS
        self.dispatcher.tick()
        self.assertEqual(sum("waits: weekly plan usage" in line for line in self.said), 3)
        # A reading under the limit reaches the dispatcher with no event, and its retry starts the runs.
        self.usage = (None, None)
        self.time += dispatch.RETRY_SECONDS
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["running"] * 3)

    def test_check_runs_spend_no_tokens_and_pass_the_limit(self):
        request = {"kind": "check", "project": REPO, "target": "agent/issue-7", "env": {}}
        self.assertIsNone(self.dispatcher.held(request))
        self.assertEqual(self.dispatcher.held({**request, "kind": "agent", "target": "7"}),
                         dispatch.Wait("usage", dispatch.USAGE_WAIT, NOTE))

    def test_the_limit_is_one_more_reason_to_hold_and_resume_never_lifts_it(self):
        self.hold = {"state": "held", "picked": []}
        run = self.request("agent", "7")
        self.dispatcher.tick()
        self.assertIn(f"Run {run} waits: the desk holds new runs.", self.said)
        self.hold = {"state": "manual", "picked": [], "picked_runs": [run]}
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "requested")
        self.assertIn(f"Run {run} waits: {NOTE}.", self.said)
        self.hold = {"state": "running", "picked": []}
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "requested")

    def test_a_limit_that_can_not_read_usage_lets_runs_start_and_says_so_once(self):
        self.usage = (None, "the usage endpoint answered HTTP 401")
        runs = [self.request("agent", "7"), self.request("chore", "audit")]
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["running"] * 2)
        said = [line for line in self.said if "can't read usage" in line]
        self.assertEqual(said, ["The weekly usage limit can't read usage, so runs start: "
                                "the usage endpoint answered HTTP 401."])


if __name__ == "__main__":
    unittest.main()
