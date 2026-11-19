"""Tests for agent-desk/lib/chore_rename.py, which `agent-desk chores rename` runs, against a temporary store, projects
file, and ledger folder, with a stand-in for the schedule review that web/chores.test.ts covers."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import chore_rename  # noqa: E402
import run_control  # noqa: E402
import store  # noqa: E402

GAME, TOOL, NOTES = "org/game", "org/tool", "org/notes"
OLD, NEW = "singleshot-refactor-one", "refactor"


class ChoreRenameTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.desk = store.Store(self.root / "desk.sqlite")
        self.addCleanup(self.desk.close)
        self.ledgers = self.root / "chores"
        self.projects = self.root / "projects"
        self.reviews = []
        self.printed = []
        lines = []
        for project, every in ((GAME, {"audit": 7, OLD: 7}), (TOOL, {OLD: 3}), (NOTES, {"audit": 7})):
            checkout = self.root / project.split("/")[1]
            checkout.mkdir()
            (checkout / ".agent-desk.json").write_text(json.dumps({"chores": {"every": every}}))
            lines.append(f"{project} {checkout}")
            self.desk.project(project, str(checkout))
        self.projects.write_text("\n".join(lines) + "\n")
        self.ended(GAME, OLD)
        self.ended(GAME, OLD)
        self.ended(GAME, "audit")
        self.ended(TOOL, OLD)

    def ended(self, project, skill):
        run = self.desk.request_run(project, "chore", skill)
        self.desk.run_started(run, f"agent-desk-chore-{skill}-{run}.service")
        self.desk.run_ended(run, "succeeded", 0)
        return run

    def config(self, project, settings):
        (self.root / project.split("/")[1] / ".agent-desk.json").write_text(json.dumps(settings))

    def review(self, project, checkout, old, new):
        self.reviews.append((project, Path(checkout).name, old, new))
        return 60 + len(self.reviews)

    def rename(self, *args, review=None):
        return chore_rename.main([*args], desk=self.desk, review=review or self.review,
                                 projects_file=self.projects, ledgers=self.ledgers, out=self.printed.append)

    def targets(self, project):
        return [row["target"] for row in self.desk.db.execute(
            "SELECT target FROM runs WHERE project = ? ORDER BY id", (project,))]

    def test_the_runs_ledger_and_config_review_move_to_the_new_name(self):
        ledger = self.ledgers / "org__game" / f"{OLD}.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("{}\n")
        last = self.desk.last_event()
        self.assertEqual(self.rename(OLD, NEW, "-R", GAME), 0)
        self.assertEqual(self.targets(GAME), [NEW, NEW, "audit"])
        self.assertEqual(self.targets(TOOL), [OLD])
        self.assertEqual(self.reviews, [(GAME, "game", OLD, NEW)])
        self.assertEqual(self.printed, [
            f"{GAME}: renaming {OLD} to {NEW}",
            f"- config: opened or updated review #61, which renames {OLD} to {NEW} in chores.every.",
            f"- runs: moved 2 runs of {OLD} to {NEW}.",
            f"- ledger: moved {OLD}.jsonl to {NEW}.jsonl."])
        self.assertFalse(ledger.exists())
        self.assertEqual((ledger.parent / f"{NEW}.jsonl").read_text(), "{}\n")
        self.assertEqual([(event["type"], event["payload"]) for event in self.desk.events_since(last)],
                         [("chore-renamed", {"from": OLD, "to": NEW, "runs": 2})])
        self.assertEqual(chore_rename.renames(self.desk, GAME), [(OLD, NEW)])

    def test_the_schedule_counts_the_old_name_from_the_new_names_runs_until_the_review_merges(self):
        before = dict(line.split("\t", 1) for line in run_control.chore_runs(self.desk, GAME))
        self.rename(OLD, NEW, "-R", GAME)
        after = dict(line.split("\t", 1) for line in run_control.chore_runs(self.desk, GAME))
        self.assertEqual(after, {**before, NEW: before[OLD]})
        # A run of the old name after the rename counts for it alone.
        self.desk.request_run(GAME, "chore", OLD)
        now = dict(line.split("\t", 1) for line in run_control.chore_runs(self.desk, GAME))
        self.assertEqual((now[OLD].split("\t")[1], now[NEW]), ("requested", before[OLD]))

    def test_a_config_naming_the_chore_only_for_its_model_still_gets_a_review(self):
        self.config(GAME, {"models": {"chores": {OLD: {"model": "sonnet"}}}, "chores": {"paused": [OLD]}})
        self.assertEqual(self.rename(OLD, NEW, "-R", GAME), 0)
        self.assertIn(f"- config: opened or updated review #61, which renames {OLD} to {NEW} in chores.paused, "
                      "models.chores.", self.printed)

    def test_runs_without_a_config_entry_move_without_a_review(self):
        self.config(GAME, {"chores": {"every": {"audit": 7}}})
        self.assertEqual(self.rename(OLD, NEW, "-R", GAME), 0)
        self.assertEqual(self.reviews, [])
        self.assertIn(f"- config: names no {OLD}, so no review.", self.printed)
        self.assertIn(f"- ledger: {OLD} has no ledger to move.", self.printed)
        self.assertEqual(self.targets(GAME), [NEW, NEW, "audit"])

    def test_a_new_name_with_runs_or_a_config_entry_is_refused_and_says_which(self):
        self.assertEqual(self.rename(OLD, "audit", "-R", GAME), 1)
        self.assertEqual(self.printed[-1], f"{GAME}: refused, since audit already has 1 run and a config entry in "
                         "chores.every; nothing changed.")
        self.config(GAME, {"chores": {"every": {OLD: 7}}, "models": {"chores": {NEW: {"model": "opus"}}}})
        self.assertEqual(self.rename(OLD, NEW, "-R", GAME), 1)
        self.assertEqual(self.printed[-1], f"{GAME}: refused, since {NEW} already has a config entry in "
                         "models.chores; nothing changed.")
        self.ended(NOTES, NEW)
        self.assertEqual(self.rename("audit", NEW, "-R", NOTES), 1)
        self.assertEqual(self.printed[-1], f"{NOTES}: refused, since {NEW} already has 1 run; nothing changed.")
        self.assertEqual((self.reviews, self.targets(GAME)), ([], [OLD, OLD, "audit"]))
        self.assertEqual(chore_rename.renames(self.desk, GAME), [])

    def test_an_old_name_without_runs_or_a_config_entry_is_refused(self):
        self.assertEqual(self.rename("tidy", NEW, "-R", GAME), 1)
        self.assertEqual(self.printed[-1], f"{GAME}: refused, since tidy has no runs and no config entry; nothing "
                         "changed.")
        self.assertEqual(self.reviews, [])

    def test_a_failed_review_moves_no_run(self):
        def failing(*_):
            raise chore_rename.Stopped("the config's review failed, so nothing else changed: git push failed")

        self.assertEqual(self.rename(OLD, NEW, "-R", GAME, review=failing), 1)
        self.assertEqual(self.printed[-1], f"{GAME}: the config's review failed, so nothing else changed: git push "
                         "failed")
        self.assertEqual(self.targets(GAME), [OLD, OLD, "audit"])

    def test_all_renames_in_every_project_whose_config_names_the_chore_and_skips_the_rest(self):
        self.assertEqual(self.rename(OLD, NEW, "--all"), 0)
        self.assertEqual([(project, old, new) for project, _, old, new in self.reviews],
                         [(GAME, OLD, NEW), (TOOL, OLD, NEW)])
        self.assertIn(f"{NOTES}: skipped, since its config doesn't name {OLD}.", self.printed)
        self.assertEqual((self.targets(GAME), self.targets(TOOL)), ([NEW, NEW, "audit"], [NEW]))

    def test_all_goes_on_past_a_project_that_refuses(self):
        self.ended(GAME, NEW)
        self.assertEqual(self.rename(OLD, NEW, "--all"), 1)
        self.assertEqual([project for project, *_ in self.reviews], [TOOL])
        self.assertEqual(self.targets(TOOL), [NEW])

    def test_a_name_a_request_would_refuse_is_refused_before_anything(self):
        with redirect_stderr(io.StringIO()) as said:
            self.assertEqual(self.rename(OLD, "Add Tests", "-R", GAME), 2)
            self.assertEqual(self.rename(OLD, OLD, "-R", GAME), 2)
        self.assertIn("lowercase letters, digits, and dashes, not Add Tests", said.getvalue())
        self.assertEqual(self.reviews, [])


if __name__ == "__main__":
    unittest.main()
