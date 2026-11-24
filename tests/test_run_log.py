"""Tests for agent-desk/lib/run_log.py, which records each run's model, effort, length, and review in the store, and
imports the runs agent-desk logged before the store."""

import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import budget  # noqa: E402
import chore_rename  # noqa: E402
import run_log  # noqa: E402
import store  # noqa: E402


class RunLogTest(unittest.TestCase):
    def test_a_runs_length_turns_session_and_its_total_come_from_its_last_result_event(self):
        stream = "\n".join(json.dumps(event) for event in [
            {"type": "system", "subtype": "init", "session_id": "s-1"},
            {"type": "assistant", "message": {"content": []}},
            {"type": "result", "duration_ms": 786000, "num_turns": 14, "total_cost_usd": 1.2345},
        ])
        self.assertEqual(run_log.result_of(stream),
                         {"minutes": 13.1, "turns": 14, "reported": 1.2345, "session": "s-1"})
        self.assertEqual(run_log.result_of("not json"),
                         {"minutes": None, "turns": None, "reported": None, "session": None})

    def test_a_record_stores_the_result_labels_and_start_on_the_run_agent_desk_run_names(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = store.Store(Path(folder) / "desk.sqlite")
            self.addCleanup(desk.close)
            run = desk.request_run("org/game", "agent", "12")
            stream = Path(folder) / "stream.json"
            stream.write_text(json.dumps({"type": "result", "duration_ms": 600000, "num_turns": 9,
                                          "total_cost_usd": 2.5}))
            status = run_log.main(["run_log.py", "record", "opus", "high", str(stream), "40", "--labels",
                                   "bug,serial", "--start", "feedback"], {"AGENT_DESK_RUN": str(run)}, desk)
            self.assertEqual(status, 0)
            row = desk.run(run)
            self.assertEqual((row["model"], row["effort"], row["minutes"], row["turns"], row["cost"], row["review"],
                              json.loads(row["labels"]), row["start"]),
                             ("opus", "high", 10.0, 9, 2.5, 40, ["bug", "serial"], "feedback"))
            self.assertEqual([(event["number"], event["type"]) for event in desk.events_since()][-1],
                             (40, "run-result"))

    def test_the_session_command_records_the_session_on_the_run_agent_desk_run_names(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = store.Store(Path(folder) / "desk.sqlite")
            self.addCleanup(desk.close)
            run = desk.request_run("org/game", "fix", "40")
            self.assertEqual(run_log.main(["run_log.py", "session", "s-9"], {"AGENT_DESK_RUN": str(run)}, desk), 0)
            self.assertEqual(desk.run(run)["session"], "s-9")
            self.assertEqual(run_log.main(["run_log.py", "session", "s-9"], {}, desk), 0)

    def test_a_record_outside_a_dispatched_run_stores_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = store.Store(Path(folder) / "desk.sqlite")
            self.addCleanup(desk.close)
            last = desk.last_event()
            self.assertEqual(run_log.main(["run_log.py", "record", "opus", "high", "/missing", ""], {}, desk), 0)
            self.assertEqual(desk.last_event(), last)

    def test_a_record_without_the_options_leaves_their_fields_out(self):
        self.assertEqual(run_log.options(["opus", "high", "/tmp/s"]), (["opus", "high", "/tmp/s"], None, None))
        self.assertEqual(run_log.options(["--labels", "", "--start", "sideways"]), ([], [], None))


class SessionCostTest(unittest.TestCase):
    """A run that takes up an earlier run's session counts only what the session's reported total grew by."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.desk = store.Store(self.folder / "desk.sqlite")
        self.addCleanup(self.desk.close)

    def started(self, session, kind="agent", target="12"):
        """A run of `kind` that started `session`, as desk_claude_run records it before claude starts."""
        run = self.desk.request_run("org/game", kind, target)
        self.assertEqual(run_log.main(["run_log.py", "session", session], {"AGENT_DESK_RUN": str(run)}, self.desk), 0)
        return run

    def finished(self, run, session, total, start="new"):
        """Records the run's result from a stream whose result event reports the session's total so far."""
        stream = self.folder / f"stream-{run}"
        stream.write_text("\n".join(json.dumps(event) for event in [
            {"type": "system", "subtype": "init", "session_id": session},
            {"type": "result", "session_id": session, "duration_ms": 60000, "num_turns": 3,
             "total_cost_usd": total}]))
        status = run_log.main(["run_log.py", "record", "opus", "high", str(stream), "--start", start],
                              {"AGENT_DESK_RUN": str(run)}, self.desk)
        self.assertEqual(status, 0)
        return self.desk.run(run)

    def test_a_fresh_run_counts_the_whole_total_its_session_reported(self):
        row = self.finished(self.started("fresh"), "fresh", 2.345)
        self.assertEqual((row["session"], row["reported"], row["cost"]), ("fresh", 2.345, 2.35))

    def test_a_run_resumed_after_one_that_stopped_early_counts_only_what_the_total_grew_by(self):
        self.finished(self.started("s"), "s", 5.0)
        row = self.finished(self.started("s"), "s", 8.25, "resumed")
        self.assertEqual((row["reported"], row["cost"]), (8.25, 3.25))
        third = self.finished(self.started("s"), "s", 9.0, "resumed")
        self.assertEqual(third["cost"], 0.75)

    def test_a_run_resumed_after_one_cut_off_keeps_the_cut_off_runs_spend(self):
        cut = self.started("s")
        self.assertEqual((self.desk.run(cut)["session"], self.desk.run(cut)["cost"]), ("s", None))
        row = self.finished(self.started("s"), "s", 6.5, "resumed")
        self.assertEqual(row["cost"], 6.5)

    def test_a_run_resumed_after_a_cut_off_one_counts_from_the_last_report_before_it(self):
        self.finished(self.started("s"), "s", 5.0)
        self.started("s")
        row = self.finished(self.started("s"), "s", 12.0, "resumed")
        self.assertEqual(row["cost"], 7.0)

    def test_another_sessions_reports_and_a_total_that_shrank_count_whole(self):
        self.finished(self.started("other"), "other", 4.0)
        self.assertEqual(self.finished(self.started("s", "fix", "40"), "s", 3.0)["cost"], 3.0)
        self.assertEqual(self.finished(self.started("s", "fix", "40"), "s", 1.0, "resumed")["cost"], 1.0)

    def test_the_budget_counts_each_runs_own_spend(self):
        for total, start in ((5.0, "new"), (8.0, "resumed")):
            run = self.started("s", "chore", "audit")
            self.assertTrue(self.desk.run_started(run, f"unit-{run}", account="api"))
            self.finished(run, "s", total, start)
        now = time.time()
        self.assertEqual(budget.run_spend(self.desk, now - 3600, now + 3600), 8.0)


def old_line(**fields):
    """A line of the old runs.jsonl, with `fields` over an agent run's."""
    line = {"at": "2026-10-28T11:21:58-0600", "repo": "org/game", "kind": "agent", "name": "41", "model": "opus",
            "effort": "high", "status": 0, "minutes": 2.5, "turns": 10, "cost": 0.65, "pr": 70}
    return json.dumps({**line, **fields})


class ImportTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.desk = store.Store(self.folder / "desk.sqlite")
        self.addCleanup(self.desk.close)
        checkout = self.folder / "game"
        checkout.mkdir()
        (checkout / ".agent-desk.json").write_text(json.dumps(
            {"chores": {"every": {"audit": 7, "singleshot-refactor-one": 7}, "paused": ["singleshot-comment-one"]}}))
        self.desk.project("org/game", str(checkout))
        self.log = self.folder / "runs.jsonl"

    def run_import(self):
        return run_log.main(["run_log.py", "import", str(self.log)], {}, self.desk)

    def rows(self):
        columns = ("project", "kind", "target", "status", "exit_code", "started_at", "ended_at", "model", "effort",
                   "minutes", "turns", "cost", "review", "labels", "start")
        return [tuple(row[key] for key in columns) for row in self.desk.db.execute("SELECT * FROM runs ORDER BY id")]

    def test_an_import_adds_a_finished_run_for_each_chore_agent_and_fix_line(self):
        self.log.write_text("\n".join([
            old_line(kind="chore", name="refactor-one", model=None, effort="medium", pr=456),
            old_line(kind="chore", name="comment-one", at="2026-10-28T11:30:00-0600", minutes=None, pr=None),
            old_line(kind="chore", name="audit", at="2026-10-28T11:40:00-0600", status=1, pr=None),
            old_line(at="2026-11-01T09:00:00-0600", labels=["bug", "serial"], start="resumed"),
            old_line(kind="fix", name="70", at="2026-11-01T09:10:00-0600", status=None, minutes=0.5, pr=70,
                     merged=True),
            old_line(repo="org/other", kind="chore", name="untested-one", pr=None),
            "not json", "",
        ]) + "\n")
        last = self.desk.last_event()
        with redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(self.run_import(), 0)
        self.assertIn("added 6 runs", printed.getvalue())
        self.assertIn("1 lines it couldn't read", printed.getvalue())
        self.assertEqual(self.rows(), [
            ("org/game", "chore", "singleshot-refactor-one", "succeeded", 0, "2026-10-28T17:19:28Z",
             "2026-10-28T17:21:58Z", None, "medium", 2.5, 10, 0.65, 456, None, None),
            ("org/game", "chore", "singleshot-comment-one", "succeeded", 0, "2026-10-28T17:30:00Z",
             "2026-10-28T17:30:00Z", "opus", "high", None, 10, 0.65, None, None, None),
            ("org/game", "chore", "audit", "failed", 1, "2026-10-28T17:37:30Z", "2026-10-28T17:40:00Z", "opus",
             "high", 2.5, 10, 0.65, None, None, None),
            ("org/game", "agent", "41", "succeeded", 0, "2026-11-01T14:57:30Z", "2026-11-01T15:00:00Z", "opus", "high",
             2.5, 10, 0.65, 70, '["bug", "serial"]', "resumed"),
            ("org/game", "fix", "70", "failed", None, "2026-11-01T15:09:30Z", "2026-11-01T15:10:00Z", "opus", "high",
             0.5, 10, 0.65, 70, None, None),
            ("org/other", "chore", "untested-one", "succeeded", 0, "2026-10-28T17:19:28Z", "2026-10-28T17:21:58Z",
             "opus", "high", 2.5, 10, 0.65, None, None, None),
        ])
        self.assertEqual([(event["project"], event["type"], event["payload"]) for event in
                          self.desk.events_since(last)],
                         [("org/game", "imported", {"host": "runs.jsonl", "added": 5, "filled": 0}),
                          ("org/other", "imported", {"host": "runs.jsonl", "added": 1, "filled": 0})])

    def test_a_chore_renamed_since_imports_under_its_new_name_and_only_once(self):
        self.log.write_text(old_line(kind="chore", name="refactor-one", pr=456) + "\n")
        with redirect_stdout(io.StringIO()):
            self.run_import()
        chore_rename.move_runs(self.desk, "org/game", "singleshot-refactor-one", "refactor")
        (self.folder / "game" / ".agent-desk.json").write_text(json.dumps({"chores": {"every": {"refactor": 7}}}))
        with redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(self.run_import(), 0)
        self.assertIn("added 0 runs, filled in 0 the store held, and skipped 1", printed.getvalue())
        self.assertEqual([row[2] for row in self.rows()], ["refactor"])
        self.assertEqual(run_log.chore_skill("refactor-one", None, [("singleshot-refactor-one", "refactor"),
                                                                     ("refactor", "tidy")]), "tidy")
        self.assertEqual(run_log.chore_skill("audit", None), "audit")

    def test_a_second_import_adds_nothing_and_writes_no_event(self):
        self.log.write_text("\n".join([old_line(), old_line(at="2026-10-28T11:22:30-0600"),
                                       old_line(kind="chore", name="audit", pr=None)]) + "\n")
        with redirect_stdout(io.StringIO()):
            self.run_import()
        rows, last = self.rows(), self.desk.last_event()
        with redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(self.run_import(), 0)
        self.assertIn("added 0 runs, filled in 0 the store held, and skipped 3", printed.getvalue())
        self.assertEqual((self.rows(), self.desk.last_event()), (rows, last))
        self.assertEqual(len(rows), 3)

    def test_a_line_whose_run_the_dispatcher_recorded_fills_in_that_run(self):
        run = self.desk.request_run("org/game", "agent", "41")
        self.desk.run_started(run, "unit-41")
        self.desk.run_ended(run, "succeeded", 0)
        ended = self.desk.run(run)["ended_at"]
        at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(store.epoch_of(ended) - 4))
        self.desk.db.execute("UPDATE runs SET model = 'sonnet' WHERE id = ?", (run,))
        self.log.write_text(old_line(at=at, labels=["bug"], start="new") + "\n")
        with redirect_stdout(io.StringIO()) as printed:
            self.run_import()
        self.assertIn("added 0 runs, filled in 1", printed.getvalue())
        row = self.desk.run(run)
        self.assertEqual((row["model"], row["effort"], row["cost"], row["review"], row["labels"], row["start"],
                          row["ended_at"], row["unit"]),
                         ("sonnet", "high", 0.65, 70, '["bug"]', "new", ended, "unit-41"))
        self.assertEqual(len(self.rows()), 1)

    def test_a_missing_or_empty_log_writes_nothing(self):
        last = self.desk.last_event()
        with redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(self.run_import(), 0)
        self.assertIn("There is no old run log", printed.getvalue())
        self.log.write_text("")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.run_import(), 0)
        self.assertEqual((self.rows(), self.desk.last_event()), ([], last))

    def test_an_imported_chore_run_stays_behind_the_newer_runs_the_dispatcher_recorded(self):
        run = self.desk.request_run("org/game", "chore", "audit")
        self.desk.run_started(run, "unit-audit")
        self.desk.run_ended(run, "succeeded", 0)
        self.log.write_text(old_line(kind="chore", name="audit", pr=None) + "\n")
        with redirect_stdout(io.StringIO()):
            self.run_import()
        self.assertEqual([row["id"] for row in self.desk.chore_runs("org/game")], [run + 1, run])


if __name__ == "__main__":
    unittest.main()
