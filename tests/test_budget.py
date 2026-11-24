"""Tests for agent-desk/lib/budget.py, the monthly API budget, and for what reads it: the month's spend from run rows and
the Chat's ledger, `agent-desk account`, the dispatcher's hold, the chore timer's check, the Chat's refusal, the board,
and the doctor. No test calls Anthropic."""

import calendar
import io
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import account  # noqa: E402
import budget  # noqa: E402
import doctor  # noqa: E402
import hold  # noqa: E402
import store  # noqa: E402
from dispatch_case import REPO, DispatchCase, dispatch  # noqa: E402
from test_account import KEY, AccountCase  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "lib"


def epoch(stamp):
    """A UTC time such as `2026-12-01T07:00:00Z` in epoch seconds."""
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def ran(desk, billed, started, cost, target="7"):
    """Adds a run that billed `billed`, started at `started`, and reported `cost`, to the store, ended."""
    with mock.patch.object(store, "now", lambda: started):
        run = desk.request_run(REPO, "agent", target)
        desk.run_started(run, f"u-{run}.service", account=billed)
    desk.run_result(run, cost=cost)
    desk.run_ended(run, "succeeded", 0)
    return run


class BudgetCase(AccountCase):
    """An account case with a store and a ledger of its own, in a zone whose month starts seven hours after UTC's."""

    def setUp(self):
        super().setUp()
        for patch in (mock.patch.dict(os.environ, {"TZ": "America/Denver"}),
                      mock.patch.object(budget, "LEDGER", self.state / "chat-spend.jsonl"),
                      mock.patch.object(budget, "LOCK", self.state / "chat-spend.lock"),
                      mock.patch.object(store, "DATABASE", self.state / "desk.sqlite")):
            patch.start()
            self.addCleanup(patch.stop)
        time.tzset()
        self.addCleanup(time.tzset)
        self.desk = store.Store(self.state / "desk.sqlite")
        self.addCleanup(self.desk.close)
        # Half an hour into December in Denver, which is 07:30 in UTC.
        self.now = epoch("2026-12-01T07:30:00Z")

    def api(self, dollars=200, on=True):
        """The account on `api` with a key, and the budget set."""
        self.key.parent.mkdir(parents=True, exist_ok=True)
        account.save_key(KEY, self.key)
        hold.update(lambda data: data.update(account="api", apiBudget={"on": on, "dollars": dollars}))


class SpendTest(BudgetCase):
    def test_the_month_starts_at_local_midnight_on_the_first(self):
        # Before 07:00 UTC on December 1 it is still November in Denver.
        ran(self.desk, "api", "2026-12-01T06:59:59Z", 50.0)
        ran(self.desk, "api", "2026-12-01T03:00:00Z", 40.0)
        ran(self.desk, "api", "2026-12-01T07:00:00Z", 12.5)
        budget.record("old", "api", 9.0, now=epoch("2026-12-01T06:59:00Z"))
        budget.record("new", "api", 0.75, now=epoch("2026-12-01T07:10:00Z"))
        self.assertEqual(budget.spent(self.desk, self.now), 13.25)
        # The last second of December still counts it, and January starts afresh.
        self.assertEqual(budget.spent(self.desk, epoch("2027-01-01T06:59:59Z")), 13.25)
        self.assertEqual(budget.spent(self.desk, epoch("2027-01-01T07:00:00Z")), 0)
        # November held the rest.
        self.assertEqual(budget.spent(self.desk, epoch("2026-11-15T12:00:00Z")), 99.0)

    def test_only_runs_that_billed_api_count_and_a_run_with_no_cost_counts_nothing(self):
        ran(self.desk, "api", "2026-12-01T08:00:00Z", 3.25)
        ran(self.desk, "subscription", "2026-12-01T08:00:00Z", 80.0)
        ran(self.desk, "api", "2026-12-01T08:00:00Z", None)
        waiting = self.desk.request_run(REPO, "agent", "8")
        self.assertIsNone(self.desk.run(waiting)["account"])
        self.assertEqual(budget.spent(self.desk, self.now + 3600), 3.25)

    def test_without_a_desk_the_spend_reads_the_store_read_only(self):
        ran(self.desk, "api", "2026-12-01T08:00:00Z", 3.25)
        self.assertEqual(budget.spent(now=self.now + 3600), 3.25)

    def test_a_chat_turn_counts_what_its_session_grew_by_on_the_account_it_billed(self):
        at = self.now
        self.assertEqual(budget.record("s1", "subscription", 0.50, now=at), 0.50)
        # Claude Code reports a resumed session's total so far, so the turn on the key cost 0.30.
        self.assertAlmostEqual(budget.record("s1", "api", 0.80, now=at + 1), 0.30)
        self.assertAlmostEqual(budget.record("s1", "api", 1.00, now=at + 2), 0.20)
        # Another session counts from nothing, and a total smaller than the last counts whole.
        self.assertEqual(budget.record("s2", "api", 0.40, now=at + 3), 0.40)
        self.assertEqual(budget.record("s1", "api", 0.10, now=at + 4), 0.10)
        self.assertAlmostEqual(budget.spent(self.desk, at + 5), 1.00)

    def test_record_refuses_a_cost_that_is_not_a_dollar_amount_or_an_unknown_account(self):
        for args in (["s", "api", "lots"], ["s", "api", "-1"], ["s", "api", "nan"], ["s", "plan", "1"]):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(budget.main(["budget.py", "record", *args]), 2, args)
        self.assertFalse(budget.LEDGER.exists())

    def test_a_ledger_line_that_is_not_a_turn_is_skipped(self):
        budget.record("s1", "api", 1.0, now=self.now)
        with open(budget.LEDGER, "a", encoding="utf-8") as out:
            out.write("not json\n[1]\n{\"at\": \"soon\", \"cost\": 5}\n")
        self.assertEqual(budget.spent(self.desk, self.now), 1.0)


class CommandTest(BudgetCase):
    def test_a_budget_turns_on_off_keeping_its_amount_and_on_again(self):
        code, out, _ = self.command("budget", "200")
        self.assertEqual(code, 0)
        self.assertIn("The API budget is on at $200.00 a month.", out)
        self.assertEqual(hold.status()["apiBudget"], {"on": True, "dollars": 200.0})
        code, out, _ = self.command("budget", "off")
        self.assertIn("The API budget is off; on, it holds new runs at $200.00 a month.", out)
        self.command("budget", "on")
        self.assertEqual(hold.status()["apiBudget"], {"on": True, "dollars": 200.0})
        self.command("budget", "$49.50")
        code, out, _ = self.command("budget")
        self.assertEqual(code, 0)
        self.assertIn("The API budget is on at $49.50 a month.", out)

    def test_anything_but_a_positive_number_of_dollars_is_refused(self):
        for given in ("0", "-5", "abc", "1.234", "inf", "nan", "200 dollars", "1e3"):
            code, _, err = self.command("budget", given)
            self.assertEqual(code, 1, given)
            self.assertIn("a positive number of dollars", err)
        self.assertNotIn("apiBudget", hold.status())
        code, _, err = self.command("budget", "on")
        self.assertEqual(code, 1)
        self.assertIn("no amount yet", err)

    def test_one_budget_covers_every_project_and_outlasts_a_state_that_went_missing(self):
        self.command("budget", "200")
        hold.HOLD.write_text(json.dumps({**json.loads(hold.HOLD.read_text()), "state": "gone"}))
        self.assertEqual(hold.status()["apiBudget"], {"on": True, "dollars": 200.0})
        with mock.patch.dict(os.environ, {"GH_REPO": "someone/else"}):
            self.assertEqual(budget.budget_of(hold.status()), {"on": True, "dollars": 200.0})

    def test_the_account_shows_both_accounts_and_marks_the_one_in_use(self):
        hold.STATE.mkdir(parents=True, exist_ok=True)
        (hold.STATE / "usage.json").write_text(json.dumps({"percent": 41.6, "read_at": self.now - 60,
                                                           "resets_at": "2026-12-02T11:00:00Z"}))
        ran(self.desk, "api", "2026-12-01T07:05:00Z", 63.2)
        out = account.describe(desk=self.desk, now=self.now)
        self.assertEqual(out.splitlines()[:2], ["* subscription  Week 41%, resets Wed 04:00 (read Tue 00:29)",
                                                "  api           API $63.20 this month"])
        self.api()
        out = account.describe(desk=self.desk, now=self.now)
        self.assertEqual(out.splitlines()[:2], ["  subscription  Week 41%, resets Wed 04:00 (read Tue 00:29)",
                                                "* api           API $63.20 of $200.00 this month"])
        self.assertNotIn("holds", out)

    def test_the_account_says_when_the_budget_holds(self):
        self.api(dollars=60)
        ran(self.desk, "api", "2026-12-01T07:05:00Z", 63.2)
        out = account.describe(desk=self.desk, now=self.now)
        self.assertIn("  subscription  Week: no reading yet", out)
        self.assertIn("The API budget holds new agent, chore, and fix runs and the Chat's turns: $63.20 of $60.00 "
                      "this month.", out)


class HoldTest(BudgetCase):
    def test_the_budget_holds_at_or_over_its_amount_on_api_only(self):
        self.api(dollars=10)
        ran(self.desk, "api", "2026-12-01T07:05:00Z", 9.99)
        self.assertIsNone(budget.holds(hold.status(), self.desk, self.now))
        budget.record("s1", "api", 0.01, now=self.now)
        self.assertEqual(budget.holds(hold.status(), self.desk, self.now), "the API budget: $10.00 of $10.00 this month")

    def test_each_way_the_hold_lifts(self):
        self.api(dollars=200)
        ran(self.desk, "api", "2026-12-01T07:05:00Z", 201.4)
        held = budget.holds(hold.status(), self.desk, self.now)
        self.assertEqual(held, "the API budget: $201.40 of $200.00 this month")
        # The month turns.
        self.assertIsNone(budget.holds(hold.status(), self.desk, epoch("2027-01-01T07:00:00Z")))
        # The budget rises.
        hold.update(lambda data: data["apiBudget"].update(dollars=250))
        self.assertIsNone(budget.holds(hold.status(), self.desk, self.now))
        hold.update(lambda data: data["apiBudget"].update(dollars=200))
        # The budget turns off.
        hold.update(lambda data: data["apiBudget"].update(on=False))
        self.assertIsNone(budget.holds(hold.status(), self.desk, self.now))
        hold.update(lambda data: data["apiBudget"].update(on=True))
        self.assertIsNotNone(budget.holds(hold.status(), self.desk, self.now))
        # The account switches to subscription, which the budget never holds.
        hold.update(lambda data: data.update(account="subscription"))
        self.assertIsNone(budget.holds(hold.status(), self.desk, self.now))

    def test_the_chore_timer_starts_nothing_while_the_budget_holds(self):
        self.api(dollars=1)
        budget.record("s1", "api", 2.0)

        def held():
            env = {**os.environ, "XDG_STATE_HOME": str(self.root / "xdg"), "GH_REPO": REPO}
            return subprocess.run([sys.executable, "-B", str(LIB / "hold.py"), "held"], env=env).returncode

        # The command reads the state folder XDG_STATE_HOME names, so the state moves there.
        folder = self.root / "xdg" / "agent-desk"
        folder.mkdir(parents=True)
        for name in ("hold.json", "chat-spend.jsonl"):
            (folder / name).write_text((self.state / name).read_text())
        self.assertEqual(held(), 0)
        hold_file = folder / "hold.json"
        hold_file.write_text(json.dumps({**json.loads(hold_file.read_text()), "account": "subscription"}))
        self.assertEqual(held(), 1)

    def test_the_chat_refuses_a_turn_while_the_budget_holds_and_says_why(self):
        self.api(dollars=1)
        budget.record("s1", "api", 2.0)
        code, out, err = self.command("chat")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("agent-desk: the API budget holds new turns: $2.00 of $1.00 this month; raise it with: "
                      "agent-desk account budget <dollars>", err)
        self.command("budget", "5")
        code, out, _ = self.command("chat")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "api")
        self.assertIn("apiKeyHelper", out.splitlines()[1])


class BoardAndDoctorTest(BudgetCase):
    def test_the_board_summary_carries_the_account_the_spend_and_the_budget(self):
        self.assertEqual(budget.summary(hold.status(), self.desk, self.now),
                         {"account": "subscription", "spent": 0.0, "budget": None, "held": False})
        self.api(dollars=200)
        ran(self.desk, "api", "2026-12-01T07:05:00Z", 201.404)
        self.assertEqual(budget.summary(hold.status(), self.desk, self.now),
                         {"account": "api", "spent": 201.4, "budget": {"on": True, "dollars": 200.0}, "held": True})

    def test_the_doctor_warns_on_api_with_no_budget_on(self):
        self.assertEqual(doctor.check_budget(hold.status()).status, "ok")
        self.api(on=False)
        check = doctor.check_budget(hold.status())
        self.assertEqual(check.status, "warn")
        self.assertIn("the account is api with no API budget on", check.what)
        self.assertEqual(check.fix, "agent-desk account budget <dollars>")
        hold.update(lambda data: data.pop("apiBudget"))
        self.assertEqual(doctor.check_budget(hold.status()).status, "warn")
        self.command("budget", "200")
        check = doctor.check_budget(hold.status(), self.desk, self.now)
        self.assertEqual((check.status, check.what), ("ok", "the API budget of $200.00 a month is on"))


class DispatchBudgetTest(DispatchCase):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch.object(budget, "LEDGER", self.folder / "chat-spend.jsonl"),
                      mock.patch.object(budget, "LOCK", self.folder / "chat-spend.lock")):
            patch.start()
            self.addCleanup(patch.stop)
        self.hold = {"state": "running", "picked": [], "account": "api", "apiBudget": {"on": True, "dollars": 200}}
        ran(self.store, "api", store.now(), 201.4, target="99")

    def test_the_budget_holds_agent_chore_fix_and_discussion_requests_and_names_the_spend(self):
        runs = [self.request("agent", "7"), self.request("chore", "audit"), self.request("fix", "12"),
                self.request("agent", "idea-8")]
        self.dispatcher.tick()
        self.assertEqual([self.status(run) for run in runs], ["requested"] * 4)
        self.assertEqual((self.systemd.started, self.memory.asked), ([], []))
        self.assertEqual([self.waits(run) for run in runs],
                         [("usage", "the API budget: $201.40 of $200.00 this month")] * 4)
        self.assertIn(f"Run {runs[0]} waits: the API budget: $201.40 of $200.00 this month.", self.said)
        check = {"kind": "check", "project": REPO, "target": "agent/issue-7", "env": {}}
        self.assertIsNone(self.dispatcher.held(check))

    def test_a_higher_budget_lifts_the_hold_with_no_event(self):
        run = self.request("agent", "7")
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "requested")
        self.hold = {**self.hold, "apiBudget": {"on": True, "dollars": 300}}
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")

    def test_switching_to_subscription_lifts_the_hold(self):
        run = self.request("agent", "7")
        self.dispatcher.tick()
        self.hold = {**self.hold, "account": "subscription"}
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")
        self.assertEqual(self.store.run(run)["account"], "subscription")

    def test_the_month_turning_lifts_the_hold_on_the_retry(self):
        run = self.request("agent", "7")
        self.dispatcher.tick()
        later = time.time() + 40 * 86400
        self.dispatcher.budget_check = lambda state, desk: budget.holds(state, desk, later)
        self.time += dispatch.RETRY_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")


if __name__ == "__main__":
    unittest.main()
