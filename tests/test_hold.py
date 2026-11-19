"""Tests for agent-desk/lib/hold.py, which says which new runs the dispatcher starts."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import hold  # noqa: E402
import store  # noqa: E402

# The store read the tests stand in for, which one test runs against a store of its own.
WAITING = hold.waiting_requests


class HoldCase(unittest.TestCase):
    """A state folder of its own, with no store to read."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        state = Path(self.folder.name)
        self.patches = [mock.patch.object(hold, "STATE", state), mock.patch.object(hold, "HOLD", state / "hold.json"),
                        mock.patch.object(hold, "LOCK", state / "hold.lock"),
                        mock.patch.object(hold, "waiting_requests", return_value=[])]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.folder.cleanup()


class HoldTest(HoldCase):
    def test_the_runs_working_are_the_dispatchers_running_units(self):
        path = Path(self.folder.name) / "desk.sqlite"
        with mock.patch.object(store, "DATABASE", path), mock.patch.dict(os.environ, {"AGENT_DESK_RUN": ""}):
            desk = store.Store()
            self.addCleanup(desk.close)
            run = desk.request_run("org/game", "chore", "audit")
            desk.run_started(run, "u.service")
            desk.request_run("org/game", "agent", "7")
            self.assertEqual(hold.running(), [{"repo": "org/game", "run": run, "workflow": "chores", "runner": "chore",
                                               "unit": "u.service", "desk": True}])

    def test_hold_notes_the_mode_before_and_resume_goes_back_to_it(self):
        hold.set_state("manual")
        hold.set_state("held", now="t1")
        data = hold.status()
        self.assertEqual((data["state"], data["since"], data["before"]), ("held", "t1", "manual"))
        hold.resume()
        data = hold.status()
        self.assertEqual((data["state"], data.get("before")), ("manual", None))

    def test_auto_clears_the_state(self):
        hold.set_state("held", now="t1")
        hold.set_state("running")
        self.assertEqual(hold.status(), {"state": "running", "since": None, "picked": [], "picked_runs": []})

    def test_a_state_this_code_no_longer_has_reads_as_auto_and_keeps_the_usage_limit(self):
        limit = {"on": True, "percent": 80}
        hold.save({"state": "sleep", "since": "t1", "before": "manual", "requeue": [{"repo": "org/game", "run": 4}],
                   "usageLimit": limit})
        self.assertEqual(hold.status(), {"state": "running", "since": None, "usageLimit": limit, "picked": [],
                                         "picked_runs": []})
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hold.main(["hold.py", "set", "sleep"]), 2)
            self.assertEqual(hold.main(["hold.py", "stop"]), 2)

    def manual(self):
        hold.set_state("manual")

    def test_start_picks_an_issue_once(self):
        self.manual()
        with mock.patch.object(hold, "repo_here", return_value="org/game"):
            self.assertEqual(hold.main(["hold.py", "start", "12"]), 0)
        hold.pick(12, "org/game")
        self.assertEqual(hold.status()["picked"], ["org/game#12"])

    def test_an_issue_keeps_its_pick_until_its_run_claims_it(self):
        self.manual()
        hold.pick(12, "org/game")
        with mock.patch.dict(os.environ, {"GH_REPO": "org/game"}):
            self.assertEqual(hold.main(["hold.py", "spend", "12"]), 0)
        self.assertEqual(hold.status()["picked"], [])

    def test_start_runs_picks_each_request_once_and_drops_picks_that_no_longer_wait(self):
        self.manual()
        with redirect_stdout(io.StringIO()):
            hold.start_runs([5, 6], waiting={5, 6})
        with redirect_stdout(io.StringIO()) as out:
            hold.start_runs([7, 7, 6], waiting={6, 7})
        self.assertEqual(json.loads(out.getvalue()), {"picked": 3})
        self.assertEqual(hold.status()["picked_runs"], [6, 7])
        self.assertEqual(hold.status()["picked"], [])

    def test_start_runs_keeps_every_pick_when_the_store_can_not_be_read(self):
        self.manual()
        with redirect_stdout(io.StringIO()):
            hold.start_runs([5], waiting={5})
        with mock.patch.object(hold, "waiting_runs", return_value=None), redirect_stdout(io.StringIO()):
            hold.start_runs([6])
        self.assertEqual(hold.status()["picked_runs"], [5, 6])

    def test_start_runs_from_the_command_line_and_status_prints_the_picked_requests(self):
        self.manual()
        with mock.patch.object(hold, "waiting_runs", return_value={4, 9}), redirect_stdout(io.StringIO()):
            self.assertEqual(hold.main(["hold.py", "start-runs", "4", "9"]), 0)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hold.main(["hold.py", "status"]), 0)
        status = json.loads(out.getvalue())
        self.assertEqual(status["pickedRuns"], [4, 9])
        self.assertNotIn("requeue", status)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hold.main(["hold.py", "start-runs"]), 2)
            self.assertEqual(hold.main(["hold.py", "start-runs", "x"]), 2)

    def test_the_runs_that_wait_are_the_store_s_requested_runs(self):
        path = Path(self.folder.name) / "desk.sqlite"
        desk = store.Store(path)
        self.addCleanup(desk.close)
        waits = desk.request_run("org/game", "fix", "82")
        ended = desk.request_run("org/game", "chore", "audit")
        desk.run_ended(ended, "cancelled")
        with mock.patch.object(hold, "waiting_requests", WAITING):
            self.assertEqual(hold.waiting_runs(path), {waits})
            self.assertEqual(hold.waiting_requests(path),
                             [{"id": waits, "project": "org/game", "kind": "fix", "target": "82"}])

    def test_manual_holds_the_chore_timer_but_not_the_merge_queue_fixes(self):
        self.manual()
        self.assertEqual(hold.main(["hold.py", "held"]), 0)
        self.assertEqual(hold.main(["hold.py", "held", "fix"]), 1)
        hold.set_state("held")
        self.assertEqual(hold.main(["hold.py", "held", "fix"]), 0)

    def test_one_project_in_auto_runs_its_chore_timer_while_another_in_manual_holds_it(self):
        self.manual()
        hold.main(["hold.py", "set", "running", "org/game"])
        with mock.patch.dict(os.environ, {"GH_REPO": "org/game"}):
            self.assertEqual(hold.main(["hold.py", "held"]), 1)
        with mock.patch.dict(os.environ, {"GH_REPO": "org/town"}):
            self.assertEqual(hold.main(["hold.py", "held"]), 0)

    def test_a_held_project_holds_while_the_others_run(self):
        hold.set_state("held", repo="org/town")
        data = hold.status()
        self.assertEqual((hold.mode_of(data, "org/game"), hold.mode_of(data, "org/town")), ("running", "held"))

    def test_a_mode_for_every_project_drops_the_projects_own(self):
        hold.set_state("manual", repo="org/town")
        hold.set_state("running")
        self.assertNotIn("projects", hold.status())
        self.assertEqual(hold.mode_of(hold.status(), "org/town"), "running")

    def test_resume_after_hold_gives_each_project_back_its_own_mode(self):
        hold.set_state("manual")
        hold.set_state("running", repo="org/game")
        hold.set_state("held")
        self.assertEqual(hold.status()["before_projects"], {"org/game": "running"})
        hold.resume()
        data = hold.status()
        self.assertEqual((data["state"], data["projects"]), ("manual", {"org/game": "running"}))
        self.assertNotIn("before_projects", data)


# Requests that wait in the store: issue 12's fresh start and a comment sent to it in org/game, a fix of org/game's
# review 30, and issue 7's fresh start in org/town.
REQUESTS = [{"id": 5, "project": "org/game", "kind": "agent", "target": "12"},
            {"id": 6, "project": "org/game", "kind": "agent", "target": "12"},
            {"id": 8, "project": "org/game", "kind": "fix", "target": "30"},
            {"id": 9, "project": "org/town", "kind": "agent", "target": "7"}]


class PicksTest(HoldCase):
    """When the issues and requests Start picked for Manual drop: Unpick, and a project going to Auto."""

    def setUp(self):
        super().setUp()
        self.waiting = mock.patch.object(hold, "waiting_requests", return_value=REQUESTS)
        self.waiting.start()
        self.addCleanup(self.waiting.stop)
        with redirect_stdout(io.StringIO()):
            hold.set_state("manual")
        hold.update(lambda data: data.update(picked=["org/game#12", "org/game#13", "org/town#7"],
                                             picked_runs=[6, 8, 9, 99]))

    def picks(self):
        data = hold.status()
        return data["picked"], data["picked_runs"]

    def switch(self, state, repo=None):
        with redirect_stdout(io.StringIO()):
            hold.set_state(state, repo=repo)

    def test_auto_for_every_project_drops_every_pick_of_an_issue_and_of_a_request_that_waits(self):
        self.switch("running")
        self.assertEqual(self.picks(), ([], [99]))

    def test_auto_for_one_project_drops_only_its_picks(self):
        self.switch("running", "org/game")
        self.assertEqual(self.picks(), (["org/town#7"], [9, 99]))

    def test_auto_drops_the_issue_picks_and_keeps_the_request_picks_when_the_store_can_not_be_read(self):
        with mock.patch.object(hold, "waiting_requests", return_value=None):
            self.switch("running")
        self.assertEqual(self.picks(), ([], [6, 8, 9, 99]))

    def test_manual_and_hold_keep_every_pick_for_every_project_and_for_one(self):
        kept = self.picks()
        for state, repo in (("manual", None), ("manual", "org/game"), ("held", "org/game"), ("held", None)):
            self.switch(state, repo)
            self.assertEqual(self.picks(), kept, (state, repo))

    def test_resume_into_manual_keeps_the_picks_start_made_on_hold(self):
        self.switch("held")
        hold.pick(14, "org/game")
        hold.resume()
        self.assertEqual(hold.status()["state"], "manual")
        self.assertEqual(self.picks(), (["org/game#12", "org/game#13", "org/town#7", "org/game#14"], [6, 8, 9, 99]))

    def test_resume_into_auto_after_hold_drops_every_pick(self):
        self.switch("running")
        hold.update(lambda data: data.update(picked=["org/game#12"], picked_runs=[6]))
        self.switch("held")
        self.assertEqual(self.picks(), (["org/game#12"], [6]))
        hold.resume()
        self.assertEqual(self.picks(), ([], []))

    def test_resume_drops_the_picks_of_a_project_back_in_auto_and_keeps_those_of_one_back_in_manual(self):
        self.switch("running", "org/town")
        hold.update(lambda data: data["picked"].append("org/town#3"))
        self.switch("held")
        hold.resume()
        self.assertEqual(hold.status()["projects"], {"org/town": "running"})
        self.assertEqual(self.picks(), (["org/game#12", "org/game#13"], [6, 8, 99]))

    def test_unpick_drops_an_issue_s_pick_and_those_of_its_agent_requests(self):
        self.assertTrue(hold.unpick(12, "org/game"))
        self.assertEqual(self.picks(), (["org/game#13", "org/town#7"], [8, 9, 99]))

    def test_unpick_refuses_an_issue_with_no_pick_and_changes_nothing(self):
        hold.spend(12, "org/game")
        kept = self.picks()
        self.assertFalse(hold.unpick(12, "org/game"))
        self.assertFalse(hold.unpick(7, "org/game"))
        self.assertEqual(self.picks(), kept)

    def test_unpick_from_the_command_line_says_why_it_refuses(self):
        self.assertEqual(hold.main(["hold.py", "unpick", "13", "org/game"]), 0)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(hold.main(["hold.py", "unpick", "13", "org/game"]), 1)
        self.assertIn("#13 in org/game has no pick", err.getvalue())
        with mock.patch.object(hold, "repo_here", return_value="org/town"):
            self.assertEqual(hold.main(["hold.py", "unpick", "7"]), 0)
        self.assertEqual(self.picks(), (["org/game#12"], [6, 8, 99]))

    def test_unpick_all_drops_one_project_s_picks_or_every_one(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hold.main(["hold.py", "unpick", "--all", "org/game"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"dropped": 2})
        self.assertEqual(self.picks(), (["org/town#7"], [9, 99]))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hold.main(["hold.py", "unpick", "--all"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"dropped": 1})
        self.assertEqual(self.picks(), ([], [99]))

    def test_unpick_takes_an_issue_or_all_and_at_most_one_project(self):
        with redirect_stderr(io.StringIO()):
            for argv in (["unpick"], ["unpick", "x"], ["unpick", "--all", "game"], ["unpick", "12", "a/b", "c"]):
                self.assertEqual(hold.main(["hold.py", *argv]), 2, argv)



class SlotsTest(HoldCase):
    """
    `agent-desk slots` on org/game, whose config sets 3 agent slots and 1 check slot, and org/town, which sets none.
    """

    def setUp(self):
        super().setUp()
        folder = Path(self.folder.name)
        for name, config in (("game", {"slots": {"agent": 3, "check": 1}}), ("town", None)):
            (folder / name).mkdir()
            if config:
                (folder / name / ".agent-desk.json").write_text(json.dumps(config))
        (folder / "projects").write_text(f"org/game {folder / 'game'}\norg/town {folder / 'town'}\n")
        patch = mock.patch.object(store, "PROJECTS", folder / "projects")
        patch.start()
        self.addCleanup(patch.stop)

    def slots(self, *args, repo=""):
        """Runs `agent-desk slots` with `repo` as the current directory's project, and returns its status and output."""
        with (mock.patch.object(hold, "repo_here", return_value=repo), redirect_stdout(io.StringIO()) as out,
              redirect_stderr(io.StringIO()) as err):
            code = hold.main(["hold.py", "slots", *args])
        return code, out.getvalue() + err.getvalue()

    def overrides(self):
        return hold.status().get("slots")

    def test_set_overrides_each_kind_named_above_or_below_the_config_and_keeps_the_others(self):
        code, out = self.slots("org/game", "agent=1", "fix=4")
        self.assertEqual(code, 0, out)
        self.assertIn("  agent       1   config  3   override\n", out)
        self.assertIn("  check       1   config  1\n", out)
        self.slots("org/game", "chore=0")
        self.assertEqual(self.overrides(), {"org/game": {"agent": 1, "fix": 4, "chore": 0}})

    def test_a_count_equal_to_the_config_s_drops_that_kind_s_override_and_the_last_drops_the_project(self):
        self.slots("org/game", "agent=1", "check=2")
        self.slots("org/game", "agent=3")
        self.assertEqual(self.overrides(), {"org/game": {"check": 2}})
        self.slots("org/game", "check=1")
        self.assertIsNone(self.overrides())

    def test_reset_drops_the_kinds_named_or_every_override_of_the_project(self):
        self.slots("org/game", "agent=1", "fix=0", "check=4")
        self.slots("org/town", "agent=5")
        self.assertEqual(self.slots("org/game", "--reset", "fix", "check")[0], 0)
        self.assertEqual(self.overrides(), {"org/game": {"agent": 1}, "org/town": {"agent": 5}})
        self.slots("org/game", "--reset")
        self.assertEqual(self.overrides(), {"org/town": {"agent": 5}})

    def test_without_a_project_the_setting_forms_act_on_the_current_one_and_refuse_outside_a_project(self):
        self.assertEqual(self.slots("agent=1", repo="org/town")[0], 0)
        self.assertEqual(self.overrides(), {"org/town": {"agent": 1}})
        for args in (["agent=2"], ["--reset"]):
            code, out = self.slots(*args)
            self.assertEqual(code, 1, args)
            self.assertIn("No project here", out)
        self.assertEqual(self.overrides(), {"org/town": {"agent": 1}})
        self.slots("--reset", repo="org/town")
        self.assertIsNone(self.overrides())

    def test_print_lists_every_project_or_the_one_named_with_its_config_and_overrides(self):
        self.slots("org/town", "benchmark=0")
        code, out = self.slots()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0::6], ["org/game", "org/town"])
        self.assertIn("  benchmark   0   config  2   override", out)
        code, out = self.slots("org/game")
        self.assertEqual(out.splitlines()[:2], ["org/game", "  agent       3   config  3"])
        self.assertEqual(self.slots("org/elsewhere")[0], 1)

    def test_a_kind_or_count_it_does_not_know_or_an_unknown_project_changes_nothing(self):
        for args in (["agent"], ["agents=1"], ["agent=x"], ["agent=-1"], ["agent=1", "fix"], ["--reset", "agents"]):
            self.assertEqual(self.slots("org/game", *args)[0], 2, args)
        self.assertEqual(self.slots("org/elsewhere", "agent=1")[0], 1)
        self.assertIsNone(self.overrides())

    def test_mode_changes_and_resume_keep_every_project_s_overrides(self):
        self.slots("org/game", "agent=1")
        self.slots("org/town", "check=0")
        kept = self.overrides()
        for state, repo in (("held", "org/game"), ("manual", None), ("held", None), ("running", "org/town"),
                            ("running", None), ("held", None)):
            hold.set_state(state, repo=repo)
            self.assertEqual(self.overrides(), kept, (state, repo))
        hold.resume()
        self.assertEqual(self.overrides(), kept)

    def test_status_prints_the_overrides_and_keeps_them_when_the_state_goes_missing(self):
        self.slots("org/game", "agent=1")
        with redirect_stdout(io.StringIO()) as out:
            hold.main(["hold.py", "status"])
        self.assertEqual(json.loads(out.getvalue())["slots"], {"org/game": {"agent": 1}})
        hold.HOLD.write_text(json.dumps({"slots": {"org/game": {"agent": 1}}}))
        self.assertEqual((hold.status()["state"], self.overrides()), ("running", {"org/game": {"agent": 1}}))


if __name__ == "__main__":
    unittest.main()
