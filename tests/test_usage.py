"""Tests for agent-desk/lib/usage.py, the weekly plan-usage limit that holds new runs, with the fetch stubbed."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import doctor  # noqa: E402
import hold  # noqa: E402
import usage  # noqa: E402

NOW = 1_800_000_000.0
# Wed 2027-01-20 04:00 UTC, a reset after NOW.
RESETS = "2027-01-20T04:00:00.000000+00:00"
TOKEN = "stub-oauth-access-token"


class Fetch:
    """The usage endpoint: answers each call with the next reading, or raises UsageError for None."""

    def __init__(self, *percents):
        self.percents = list(percents)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        percent = self.percents.pop(0) if len(self.percents) > 1 else self.percents[0]
        if percent is None:
            raise usage.UsageError("the usage endpoint answered HTTP 401")
        return {"percent": percent, "resets_at": RESETS}


class UsageCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state = Path(folder.name)
        self.state = state
        for patch in (mock.patch.object(hold, "STATE", state), mock.patch.object(hold, "HOLD", state / "hold.json"),
                      mock.patch.object(hold, "LOCK", state / "hold.lock"),
                      mock.patch.object(usage, "CACHE", state / "usage.json"),
                      mock.patch.object(usage, "LOCK", state / "usage.lock"),
                      mock.patch.dict(os.environ, {"TZ": "UTC", "CLAUDE_CONFIG_DIR": str(state / "claude")})):
            patch.start()
            self.addCleanup(patch.stop)
        time.tzset()
        self.addCleanup(time.tzset)

    def limit(self, percent=80, on=True):
        return {"state": "running", "since": None, "usageLimit": {"on": on, "percent": percent}}

    def command(self, *args, fetch=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(usage, "fetch", fetch or Fetch(40.0)):
            code = usage.main(["usage.py", *args])
        return code, out.getvalue(), err.getvalue()


class GateTest(UsageCase):
    def test_the_limit_holds_at_and_over_it_and_lets_runs_start_under_it(self):
        for percent, held in ((79.9, False), (80.0, True), (81.4, True)):
            usage.CACHE.unlink(missing_ok=True)
            reason = usage.holds(self.limit(80), NOW, Fetch(percent))
            self.assertEqual(reason is not None, held, percent)
        self.assertEqual(reason, "weekly plan usage is 81%, at the 80% limit; resets Wed 04:00")

    def test_the_limit_off_or_never_set_holds_nothing_and_fetches_nothing(self):
        fetch = Fetch(99.0)
        for state in (self.limit(80, on=False), {"state": "running", "since": None}):
            self.assertEqual(usage.check(state, NOW, fetch), (None, None))
        self.assertEqual(fetch.calls, 0)

    def test_a_reading_serves_every_check_for_five_minutes(self):
        fetch = Fetch(90.0, 50.0)
        self.assertIsNotNone(usage.holds(self.limit(), NOW, fetch))
        self.assertIsNotNone(usage.holds(self.limit(), NOW + usage.FETCH_SECONDS - 1, fetch))
        self.assertEqual(fetch.calls, 1)
        # Once the reading is five minutes old, the next check fetches, and a reading under the limit lets runs start.
        self.assertIsNone(usage.holds(self.limit(), NOW + usage.FETCH_SECONDS, fetch))
        self.assertEqual(fetch.calls, 2)

    def test_a_failed_fetch_with_no_reading_lets_runs_start_and_says_why(self):
        self.assertEqual(usage.check(self.limit(), NOW, Fetch(None)), (None, "the usage endpoint answered HTTP 401"))

    def test_a_failed_fetch_holds_on_the_last_reading_for_an_hour_and_then_lets_runs_start(self):
        fetch = Fetch(90.0, None)
        self.assertIsNotNone(usage.holds(self.limit(), NOW, fetch))
        reason, problem = usage.check(self.limit(), NOW + usage.FETCH_SECONDS, fetch)
        self.assertEqual((reason is not None, problem), (True, None))
        self.assertEqual(usage.cached()["error"], "the usage endpoint answered HTTP 401")
        # A failed fetch counts as a try, so the endpoint hears from the desk at most every five minutes.
        usage.holds(self.limit(), NOW + usage.FETCH_SECONDS + 1, fetch)
        self.assertEqual(fetch.calls, 2)
        self.assertEqual(usage.check(self.limit(), NOW + usage.STALE_SECONDS, fetch),
                         (None, "the usage endpoint answered HTTP 401"))

    def test_a_reading_from_before_its_window_reset_lets_runs_start(self):
        reset = usage.resets({"resets_at": RESETS})
        fetch = Fetch(95.0, None)
        self.assertIsNotNone(usage.holds(self.limit(), reset - 60, fetch))
        # The reset makes a fetch due at once, sooner than five minutes after the last.
        self.assertEqual(usage.check(self.limit(), reset + 1, fetch), (None, "the usage endpoint answered HTTP 401"))
        self.assertEqual(fetch.calls, 2)

    def test_the_chore_timer_holds_while_the_limit_holds_and_the_fixes_follow_the_mode(self):
        hold.save(self.limit(80))
        with mock.patch.object(usage, "fetch", Fetch(85.0)):
            self.assertEqual(hold.main(["hold.py", "held"]), 0)
            self.assertEqual(hold.main(["hold.py", "held", "fix"]), 1)
        usage.CACHE.unlink()
        with mock.patch.object(usage, "fetch", Fetch(20.0)):
            self.assertEqual(hold.main(["hold.py", "held"]), 1)

    def test_the_mode_keeps_the_limit_through_hold_resume_and_a_lost_state(self):
        hold.save(self.limit(70))
        with redirect_stdout(io.StringIO()):
            hold.set_state("held")
            hold.resume()
            hold.set_state("manual")
        self.assertEqual(hold.status()["usageLimit"], {"on": True, "percent": 70})
        hold.save({"usageLimit": {"on": False, "percent": 70}})
        self.assertEqual(hold.status(), {"state": "running", "since": None, "picked": [], "picked_runs": [],
                                         "usageLimit": {"on": False, "percent": 70}})
        out = io.StringIO()
        with redirect_stdout(out):
            hold.main(["hold.py", "status"])
        self.assertEqual(json.loads(out.getvalue())["usageLimit"], {"on": False, "percent": 70})

    def test_doctor_warns_when_the_limit_can_not_read_usage(self):
        self.assertEqual(doctor.check_usage(self.limit(80, on=False)).status, "ok")
        check = doctor.check_usage(self.limit(80), NOW, Fetch(None))
        self.assertEqual(check.status, "warn")
        self.assertIn("HTTP 401", check.what)
        usage.CACHE.unlink()
        self.assertEqual(doctor.check_usage(self.limit(80), NOW, Fetch(85.0)).status, "ok")


class FetchTest(UsageCase):
    def sign_in(self):
        folder = self.state / "claude"
        folder.mkdir()
        (folder / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}))

    def test_a_fetch_sends_the_token_and_reads_both_windows(self):
        self.sign_in()
        sent = []
        short_reset = "2026-01-01T08:00:00Z"

        def urlopen(request, timeout):
            sent.append(request)
            return io.BytesIO(json.dumps({"five_hour": {"utilization": 3.0, "resets_at": short_reset},
                                          "seven_day": {"utilization": 42.5, "resets_at": RESETS}}).encode())
        with mock.patch.object(usage.urllib.request, "urlopen", urlopen):
            self.assertEqual(usage.reading(NOW), {"percent": 42.5, "resets_at": RESETS, "read_at": NOW,
                             "tried_at": NOW, "error": None,
                             "five_hour": {"percent": 3.0, "resets_at": short_reset, "read_at": NOW}})
        self.assertEqual(sent[0].get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(sent[0].get_header("Anthropic-beta"), "oauth-2025-04-20")
        self.assertEqual(sent[0].get_header("User-agent"), "agent-desk")

    def test_a_five_hour_reading_neither_renews_nor_replaces_the_week_used_by_the_limit(self):
        self.sign_in()
        usage.reading(NOW, Fetch(95.0))
        now = NOW + usage.STALE_SECONDS
        reply = io.BytesIO(b'{"five_hour":{"utilization":98},"seven_day":null}')
        with mock.patch.object(usage.urllib.request, "urlopen", return_value=reply):
            data = usage.reading(now)
        self.assertEqual(data["read_at"], NOW)
        self.assertEqual(data["percent"], 95)
        self.assertEqual(data["five_hour"], {"percent": 98, "resets_at": None, "read_at": now})
        self.assertEqual(usage.check({"usageLimit": {"on": True, "percent": 80}}, now), (None, usage.NO_WEEK))
        reply = io.BytesIO(b'{"seven_day":{"utilization":42}}')
        with mock.patch.object(usage.urllib.request, "urlopen", return_value=reply):
            data = usage.reading(now + usage.FETCH_SECONDS)
        self.assertEqual(data["percent"], 42)
        self.assertNotIn("five_hour", data)

    def test_a_failed_fetch_says_why_without_the_token(self):
        self.sign_in()
        answers = [urllib.error.HTTPError(usage.URL, 401, "Unauthorized", {}, None),
                   urllib.error.URLError("Name or service not known"), io.BytesIO(b"<html>"),
                   io.BytesIO(json.dumps({"seven_day": None}).encode())]
        for answer in answers:
            def urlopen(request, timeout, answer=answer):
                if isinstance(answer, Exception):
                    raise answer
                return answer
            with mock.patch.object(usage.urllib.request, "urlopen", urlopen):
                with self.assertRaises(usage.UsageError) as caught:
                    usage.fetch()
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_a_machine_with_no_sign_in_fetches_nothing(self):
        with mock.patch.object(usage.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(usage.UsageError):
                usage.fetch()
        urlopen.assert_not_called()


class CommandTest(UsageCase):
    def test_a_percentage_sets_the_limit_and_turns_it_on(self):
        for arg in ("80", "80%"):
            code, out, _ = self.command(arg)
            self.assertEqual(code, 0)
            self.assertEqual(hold.status()["usageLimit"], {"on": True, "percent": 80})
        self.assertIn("The weekly usage limit is on at 80%.", out)
        self.assertIn("Weekly plan usage read 40% at ", out)
        self.assertIn("the window resets Wed 04:00.", out)

    def test_off_and_on_toggle_the_limit_and_keep_its_percentage(self):
        self.command("75")
        code, out, _ = self.command("off")
        self.assertEqual((code, hold.status()["usageLimit"]), (0, {"on": False, "percent": 75}))
        self.assertIn("The weekly usage limit is off; on, it holds new runs at 75%.", out)
        code, out, _ = self.command("on")
        self.assertEqual((code, hold.status()["usageLimit"]), (0, {"on": True, "percent": 75}))

    def test_on_with_no_percentage_set_says_to_set_one(self):
        code, _, err = self.command("on")
        self.assertEqual(code, 1)
        self.assertIn("agent-desk usage-limit <0-100>", err)
        self.assertNotIn("usageLimit", hold.status())

    def test_a_value_outside_0_to_100_or_not_a_number_exits_non_zero(self):
        for arg, code in (("101", 1), ("-5", 1), ("abc", 2), ("80.5", 2), ("--5", 2), ("", 2)):
            got, _, err = self.command(arg)
            self.assertEqual(got, code, arg)
            self.assertTrue(err, arg)
        self.assertEqual(self.command("80", "on")[0], 2)
        self.assertNotIn("usageLimit", hold.status())

    def test_with_no_argument_it_prints_the_limit_and_the_reading(self):
        code, out, _ = self.command()
        self.assertEqual(code, 0)
        self.assertIn("The weekly usage limit is off.", out)
        self.assertIn("No reading yet.", out)
        self.command("30", fetch=Fetch(45.0))
        out = self.command()[1]
        self.assertIn("It holds new agent, chore, and fix runs: weekly plan usage is 45%, at the 30% limit", out)
        usage.CACHE.unlink()
        out = self.command(fetch=Fetch(None))[1]
        self.assertIn("It can't read usage, so runs start: the usage endpoint answered HTTP 401.", out)


if __name__ == "__main__":
    unittest.main()
