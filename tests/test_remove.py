"""Tests for agent-desk/lib/remove.py, run as `agent-desk project remove` runs it, against the temporary state folder,
projects file, main checkout, and backup repository the rename tests set up."""

import json
import sqlite3
import subprocess
import sys
import unittest

from test_rename import OLD, ROOT, RenameCase

OTHER = "other/thing"


class RemoveCase(RenameCase):
    def setUp(self):
        super().setUp()
        # The boards the pane once kept, named for each main checkout's path.
        self.boards = self.state / "boards"
        self.boards.mkdir()
        self.board = self.boards / (str(self.checkout).strip("/").replace("/", "_") + ".json")
        self.board.write_text("{}")
        self.other_board = self.boards / (str(self.root / "thing").strip("/").replace("/", "_") + ".json")
        self.other_board.write_text("{}")

    def remove(self, *args, status=0):
        done = subprocess.run([sys.executable, "-B", str(ROOT / "lib" / "remove.py"), *args], env=self.env,
                              cwd=self.root, capture_output=True, text=True)
        self.assertEqual(done.returncode, status, done.stdout + done.stderr)
        return done.stdout, done.stderr

    def other_rows(self):
        return self.counts(OTHER)

    def removed_events(self):
        db = sqlite3.connect(self.state / "desk.sqlite")
        try:
            return db.execute("SELECT project, payload FROM events WHERE type = 'project-removed'").fetchall()
        finally:
            db.close()


class RemoveTest(RemoveCase):
    def test_it_removes_the_project_everywhere_and_leaves_the_other_alone(self):
        self.backup_repo()
        others = self.other_rows()
        self.assertTrue(all(self.counts(OLD).values()))
        out, _ = self.remove(OLD)
        self.assertEqual([line[2:] for line in out.splitlines() if line.startswith("- ")],
                         ["check", "store", "files", "hold", "projects", "backup"])
        self.assertEqual(set(self.counts(OLD).values()), {0})
        self.assertEqual(self.other_rows(), others)
        self.assertEqual(self.removed_events(), [(None, json.dumps({"repo": OLD}))])
        tree = self.state_tree()
        self.assertFalse([name for name in tree if "org__game/" in name or "org__game." in name], tree)
        self.assertEqual((self.state / "fixes" / "org__game-x.tsv").read_text(), "x\n")
        self.assertFalse(self.board.exists())
        self.assertTrue(self.other_board.exists())
        hold = json.loads((self.state / "hold.json").read_text())
        self.assertEqual(hold["projects"], {OTHER: "manual"})
        self.assertEqual(hold["picked"], [f"{OTHER}#2", f"{OLD}-x#3"])
        self.assertEqual(json.loads((self.state / "chore-repeat.json").read_text()), {OTHER: {"audit": 1}})
        self.assertEqual(self.projects.read_text(), f"{OTHER} {self.root / 'thing'}\n")
        self.assertFalse((self.data / "org" / "game").exists())
        self.assertEqual((self.data / OTHER / "1.json").read_text(), "{}\n")
        self.assertEqual(self.git("-C", str(self.data), "log", "-1", "--format=%s").strip(), f"Remove {OLD}")
        # The backup's history still holds the project.
        self.assertEqual(self.git("-C", str(self.data), "show", "HEAD~1:org/game/1.json"), "1.json\n")
        self.assertEqual(self.git("-C", str(self.data), "status", "--porcelain"), "")
        self.assertIn(f"Removed {OLD}.", out)

    def test_it_leaves_the_checkout_and_its_worktrees_and_names_them(self):
        self.git("-C", str(self.checkout), "commit", "--quiet", "--allow-empty", "-m", "Start")
        worktree = self.root / "game-issue-2"
        self.git("-C", str(self.checkout), "worktree", "add", "--quiet", "-b", "agent/issue-2", str(worktree))
        out, _ = self.remove(OLD)
        self.assertIn(f"Left the main checkout at {self.checkout} and its worktrees", out)
        self.assertIn(f"    {worktree}\n", out)
        self.assertTrue(worktree.is_dir())
        self.assertIn("agent/issue-2", self.git("-C", str(self.checkout), "branch"))
        self.assertEqual(self.git("-C", str(self.checkout), "remote", "get-url", "origin").strip(),
                         "git@github.com:org/game.git")

    def test_a_dry_run_prints_each_step_and_changes_nothing(self):
        self.backup_repo()
        before = (self.counts(OLD), self.state_tree(), self.projects.read_text(),
                  (self.state / "hold.json").read_bytes(), (self.state / "chore-repeat.json").read_bytes())
        out, _ = self.remove("--dry-run", OLD)
        self.assertIn(f"Removing {OLD} (dry run)", out)
        self.assertIn(f"Would delete {OLD} from the store (", out)
        self.assertIn("Would delete merges/org__game.json.", out)
        self.assertIn(f"Would delete boards/{self.board.name}.", out)
        self.assertIn("Would drop its mode, its picks from hold.json.", out)
        self.assertIn("Would drop its queued chores from chore-repeat.json.", out)
        self.assertIn(f"Would drop {OLD}'s line from {self.projects}.", out)
        self.assertIn("with git rm and commit it.", out)
        self.assertIn("Changed nothing.", out)
        self.assertEqual((self.counts(OLD), self.state_tree(), self.projects.read_text(),
                          (self.state / "hold.json").read_bytes(), (self.state / "chore-repeat.json").read_bytes()),
                         before)
        self.assertTrue((self.data / "org" / "game").exists())
        self.assertEqual(self.removed_events(), [])

    def test_a_name_the_desk_does_not_hold_stops_with_status_1(self):
        _, err = self.remove("org/missing", status=1)
        self.assertIn(f"stopped at check: neither the store nor {self.projects} holds org/missing.", err)
        _, err = self.remove("--dry-run", "org/missing", status=1)
        self.assertIn("holds org/missing.", err)

    def test_it_refuses_a_name_that_isnt_owner_and_repo(self):
        self.remove("game", status=2)


class RefusalTest(RemoveCase):
    def test_it_refuses_off_hold_and_a_dry_run_says_why(self):
        self.hold({"state": "manual"})
        _, err = self.remove(OLD, status=1)
        self.assertIn(f"stopped at check: {OLD} is on Manual, not Hold. Run `agent-desk hold {OLD}` first", err)
        self.assertEqual(self.counts(OLD)["items"], 3)
        out, _ = self.remove("--dry-run", OLD)
        self.assertIn(f"Would refuse now: {OLD} is on Manual, not Hold.", out)

    def test_it_refuses_while_a_run_works_or_waits_or_a_check_waits(self):
        desk = self.store()
        run = desk.request_run(OLD, "agent", "1")
        desk.run_started(run, "agent-desk-agent-org-game-1-2.service")
        waiting = desk.request_run(OLD, "chore", "audit")
        desk.check_queue(OLD, "agent/issue-1", "b" * 40)
        _, err = self.remove(OLD, status=1)
        self.assertIn(f"1 run still working (agent {run})", err)
        self.assertIn("1 branch check queued or running (agent/issue-1)", err)
        self.assertIn(f"1 run request waiting (chore audit as run {waiting})", err)
        self.assertIn("`agent-desk request --cancel <run>`", err)
        self.assertEqual(self.counts(OLD)["projects"], 1)

    def test_it_refuses_with_a_merge_queue(self):
        (self.state / "merges" / "org__game.json").write_text(json.dumps({"queue": [{"pr": 3}]}))
        _, err = self.remove(OLD, status=1)
        self.assertIn("the merge queue holds #3", err)


class RerunTest(RemoveCase):
    def test_a_rerun_after_a_failed_step_finishes_what_is_left(self):
        self.backup_repo()
        # A lock on the backup's index stops the last step, after the hold step dropped the project's mode.
        lock = self.data / ".git" / "index.lock"
        lock.write_text("")
        _, err = self.remove(OLD, status=1)
        self.assertIn("stopped at backup:", err)
        self.assertEqual(set(self.counts(OLD).values()), {0})
        self.assertEqual(self.projects.read_text(), f"{OTHER} {self.root / 'thing'}\n")
        self.assertNotIn(OLD, json.loads((self.state / "hold.json").read_text())["projects"])
        self.assertTrue((self.data / "org" / "game").exists())
        lock.unlink()
        # With the desk off Hold the project, which has no mode of its own any more, still counts as held.
        self.hold({"state": "running", "projects": {OTHER: "manual"}})
        out, _ = self.remove(OLD)
        self.assertIn(f"Finishing an earlier removal: the store and {self.projects} hold no {OLD}.", out)
        self.assertIn(f"Done: the store holds no {OLD}.", out)
        self.assertIn(f"Deleted {OLD}/ from {self.data} and committed it.", out)
        self.assertFalse((self.data / "org" / "game").exists())
        self.assertEqual(len(self.removed_events()), 1)
        # Once every step is done, nothing holds the name.
        _, err = self.remove(OLD, status=1)
        self.assertIn(f"holds {OLD}.", err)

    def test_a_rerun_after_a_failed_files_step_finishes_what_is_left(self):
        fixes = self.state / "fixes"
        fixes.chmod(0o500)
        self.addCleanup(fixes.chmod, 0o700)
        _, err = self.remove(OLD, status=1)
        self.assertIn("stopped at files:", err)
        self.assertEqual(set(self.counts(OLD).values()), {0})
        self.assertIn(OLD, self.projects.read_text())
        fixes.chmod(0o700)
        out, _ = self.remove(OLD)
        self.assertIn("Deleted fixes/org__game.tsv.", out)
        self.assertNotIn(OLD, self.projects.read_text())
        self.assertFalse((fixes / "org__game.tsv").exists())


if __name__ == "__main__":
    unittest.main()
