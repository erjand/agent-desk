"""Tests for agent-desk/lib/rename.py, run as `agent-desk project rename` runs it, against a temporary state folder,
projects file, main checkout, and backup repository."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import assets  # noqa: E402
import store  # noqa: E402

OLD, NEW = "org/game", "org/game-dev"
# Each table with a `project` column, which the rename rewrites.
TABLES = ("assets", "branches", "checks", "comments", "events", "items", "label_colors", "labels", "links", "reviews",
          "runs")


class RenameCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.state = self.root / "state" / "agent-desk"
        self.state.mkdir(parents=True)
        self.projects = self.root / "config" / "agent-desk" / "projects"
        self.projects.parent.mkdir(parents=True)
        self.data = self.root / "data"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("AGENT_DESK_", "GH_", "GITHUB_", "XDG_", "GIT_"))}
        self.env.update({
            "HOME": str(self.root), "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_CACHE_HOME": str(self.root / "cache"),
            "AGENT_DESK_DATA": str(self.data), "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1",
        })
        self.checkout = self.root / "game"
        self.git("init", "--quiet", "--initial-branch=main", str(self.checkout))
        self.git("-C", str(self.checkout), "remote", "add", "origin", "git@github.com:org/game.git")
        self.projects.write_text(f"other/thing {self.root / 'thing'}\n{OLD} {self.checkout}\n")
        self.hold({"state": "running", "projects": {OLD: "held", "other/thing": "manual"},
                   "slots": {OLD: {"agent": 1}, "other/thing": {"fix": 0}},
                   "picked": [f"{OLD}#2", "other/thing#2", f"{OLD}-x#3"]})
        self.seed()

    def git(self, *args):
        return subprocess.run(["git", *args], check=True, env=self.env, capture_output=True, text=True).stdout

    def hold(self, data):
        (self.state / "hold.json").write_text(json.dumps(data))

    def store(self):
        desk = store.Store(self.state / "desk.sqlite")
        self.addCleanup(desk.close)
        return desk

    def seed(self):
        """A project with a row in every table, its files in the state folder, and another project beside it."""
        desk = self.store()
        desk.project(OLD, str(self.checkout))
        desk.create(OLD, "issue", "One", labels=["bug"])
        desk.create(OLD, "issue", "Two", labels=["agent-ready"], blocked_by=[1])
        review = desk.create(OLD, "review", "Fix", branch="agent/issue-2", head_sha="a" * 40, fixes=[2])
        desk.comment(OLD, review, "Looks good")
        desk.set_label_color(OLD, "bug", "d73a4a")
        desk.branch_moved(OLD, "agent/issue-2", "a" * 40, None)
        check = desk.check_queue(OLD, "agent/issue-2", "a" * 40)
        self.log = self.state / "checks" / "org__game" / f"{check}.log"
        self.log.parent.mkdir(parents=True)
        self.log.write_text("passed\n")
        desk.check_started(check, str(self.log))
        desk.check_ended(check, "passed", name="test")
        run = desk.request_run(OLD, "agent", "2")
        desk.run_started(run, "agent-desk-agent-org-game-2-1.service")
        desk.run_ended(run, "succeeded", 0)
        picture = self.root / "shot.png"
        picture.write_bytes(b"picture")
        assets.save(desk, OLD, review, [picture], root=self.state / "assets")
        desk.create("other/thing", "issue", "Elsewhere")
        for name in ("org__game.json", "org__game.lock"):
            for place in ("merges", "pushes"):
                (self.state / place).mkdir(exist_ok=True)
                (self.state / place / name).write_text("{}" if name.endswith(".json") else "")
        (self.state / "fixes").mkdir()
        for name in ("org__game.tsv", "org__game.plan.json", "org__game.lock", "org__game-x.tsv"):
            (self.state / "fixes" / name).write_text("x\n")
        (self.state / "chores" / "org__game").mkdir(parents=True)
        (self.state / "chores" / "org__game" / "status.txt").write_text("ok\n")
        (self.state / "chore-repeat.json").write_text(json.dumps({OLD: {"audit": 2}, "other/thing": {"audit": 1}}))

    def rename(self, *args, status=0):
        done = subprocess.run([sys.executable, "-B", str(ROOT / "lib" / "rename.py"), *args], env=self.env,
                              cwd=self.root, capture_output=True, text=True)
        self.assertEqual(done.returncode, status, done.stdout + done.stderr)
        return done.stdout, done.stderr

    def counts(self, project):
        db = sqlite3.connect(self.state / "desk.sqlite")
        try:
            found = {table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE project = ?", (project,)).fetchone()[0]
                     for table in TABLES}
            found["projects"] = db.execute("SELECT COUNT(*) FROM projects WHERE slug = ?", (project,)).fetchone()[0]
        finally:
            db.close()
        return found

    def state_tree(self):
        return sorted(str(path.relative_to(self.state)) for path in self.state.rglob("*")
                      if path.is_file() and not path.name.startswith("desk.sqlite"))

    def backup_repo(self):
        """A backup repository holding the project's folder, as `agent-desk backup` writes it."""
        self.git("init", "--quiet", "--initial-branch=main", str(self.data))
        for name in ("1.json", "project.json"):
            (self.data / "org" / "game").mkdir(parents=True, exist_ok=True)
            (self.data / "org" / "game" / name).write_text(f"{name}\n")
        (self.data / "other" / "thing").mkdir(parents=True)
        (self.data / "other" / "thing" / "1.json").write_text("{}\n")
        self.git("-C", str(self.data), "add", ".")
        self.git("-C", str(self.data), "commit", "--quiet", "-m", "Back up")


class RenameTest(RenameCase):
    def test_the_store_moves_every_table_in_one_step_with_its_event(self):
        before = self.counts(OLD)
        self.assertTrue(all(before.values()), before)
        out, _ = self.rename(OLD, NEW)
        self.assertEqual([line[2:] for line in out.splitlines() if line.startswith("- ")],
                         ["check", "store", "files", "hold", "projects", "remote", "backup"])
        self.assertEqual(set(self.counts(OLD).values()), {0})
        after = self.counts(NEW)
        # The rename adds its own event under the new name.
        self.assertEqual(after, {**before, "events": before["events"] + 1})
        desk = self.store()
        self.assertEqual(desk.item(NEW, 2)["status"], "queued")
        self.assertEqual(desk.item("other/thing", 1)["title"], "Elsewhere")
        event = desk.events_since(0)[-1]
        self.assertEqual((event["project"], event["type"], event["payload"]),
                         (NEW, "project-renamed", {"from": OLD, "to": NEW}))
        log = desk.db.execute("SELECT log_path FROM checks").fetchone()[0]
        self.assertEqual(log, str(self.state / "checks" / "org__game-dev" / self.log.name))
        self.assertEqual(Path(log).read_text(), "passed\n")
        picture = desk.db.execute("SELECT path FROM assets").fetchone()[0]
        self.assertEqual(Path(picture).parent, self.state / "assets" / "org__game-dev")
        self.assertEqual(Path(picture).read_bytes(), b"picture")

    def test_the_state_files_hold_and_chore_queue_follow_the_new_name(self):
        self.rename(OLD, NEW)
        tree = self.state_tree()
        self.assertFalse([name for name in tree if "org__game/" in name or "org__game." in name], tree)
        for name in ("merges/org__game-dev.json", "merges/org__game-dev.lock", "pushes/org__game-dev.json",
                     "fixes/org__game-dev.tsv", "fixes/org__game-dev.plan.json",
                     "fixes/org__game-dev.lock", "chores/org__game-dev/status.txt"):
            self.assertIn(name, tree)
        # Another project whose name starts with the old one keeps its files.
        self.assertEqual((self.state / "fixes" / "org__game-x.tsv").read_text(), "x\n")
        hold = json.loads((self.state / "hold.json").read_text())
        self.assertEqual(hold["projects"], {NEW: "held", "other/thing": "manual"})
        self.assertEqual(hold["slots"], {NEW: {"agent": 1}, "other/thing": {"fix": 0}})
        self.assertEqual(hold["picked"], [f"{NEW}#2", "other/thing#2", f"{OLD}-x#3"])
        self.assertEqual(json.loads((self.state / "chore-repeat.json").read_text()),
                         {NEW: {"audit": 2}, "other/thing": {"audit": 1}})

    def test_the_projects_line_and_a_host_origin_take_the_new_name(self):
        self.rename(OLD, NEW)
        self.assertEqual(self.projects.read_text(), f"other/thing {self.root / 'thing'}\n{NEW} {self.checkout}\n")
        origin = self.git("-C", str(self.checkout), "remote", "get-url", "origin").strip()
        self.assertEqual(origin, "git@github.com:org/game-dev.git")

    def test_an_https_origin_keeps_its_host_and_form(self):
        self.git("-C", str(self.checkout), "remote", "set-url", "origin", "https://github.com/org/game")
        self.rename(OLD, NEW)
        self.assertEqual(self.git("-C", str(self.checkout), "remote", "get-url", "origin").strip(),
                         "https://github.com/org/game-dev")

    def test_an_origin_that_is_a_path_or_missing_stays(self):
        self.git("-C", str(self.checkout), "remote", "set-url", "origin", str(self.root / "org" / "game.git"))
        out, _ = self.rename(OLD, NEW)
        self.assertEqual(self.git("-C", str(self.checkout), "remote", "get-url", "origin").strip(),
                         str(self.root / "org" / "game.git"))
        self.assertIn("which is no host address naming org/game", out)
        self.git("-C", str(self.checkout), "remote", "remove", "origin")
        self.rename(NEW, "org/game-3")
        self.assertEqual(self.git("-C", str(self.checkout), "remote"), "")

    def test_the_backup_folder_moves_with_git_mv_in_one_commit(self):
        self.backup_repo()
        self.rename(OLD, NEW)
        self.assertFalse((self.data / "org" / "game").exists())
        self.assertEqual((self.data / "org" / "game-dev" / "1.json").read_text(), "1.json\n")
        self.assertEqual(self.git("-C", str(self.data), "log", "-1", "--format=%s").strip(),
                         f"Rename {OLD} to {NEW}")
        changed = self.git("-C", str(self.data), "show", "--name-status", "--format=", "-M", "HEAD").split("\n")
        self.assertEqual(sorted(line.split("\t", 1)[1] for line in changed if line),
                         ["org/game/1.json\torg/game-dev/1.json", "org/game/project.json\torg/game-dev/project.json"])
        self.assertEqual(self.git("-C", str(self.data), "status", "--porcelain"), "")

    def test_a_backup_written_under_the_new_name_wins_over_the_old_folder(self):
        self.backup_repo()
        (self.data / "org" / "game-dev").mkdir()
        (self.data / "org" / "game-dev" / "1.json").write_text("newer\n")
        self.git("-C", str(self.data), "add", ".")
        self.git("-C", str(self.data), "commit", "--quiet", "-m", "Back up again")
        self.rename(OLD, NEW)
        self.assertFalse((self.data / "org" / "game").exists())
        self.assertEqual((self.data / "org" / "game-dev" / "1.json").read_text(), "newer\n")
        self.assertEqual((self.data / "org" / "game-dev" / "project.json").read_text(), "project.json\n")
        self.assertEqual(self.git("-C", str(self.data), "status", "--porcelain"), "")

    def test_a_dry_run_prints_each_step_and_changes_nothing(self):
        self.backup_repo()
        tree, projects, hold = self.state_tree(), self.projects.read_text(), (self.state / "hold.json").read_bytes()
        out, _ = self.rename("--dry-run", OLD, NEW)
        self.assertIn("Would rename org/game to org/game-dev in the store", out)
        self.assertIn("Would move merges/org__game.json to merges/org__game-dev.json.", out)
        self.assertIn("Would rename its mode, its slot overrides, its picks in hold.json.", out)
        self.assertIn("Would rename its queued chores in chore-repeat.json.", out)
        self.assertIn(f"Would change org/game's line in {self.projects} to org/game-dev.", out)
        self.assertIn("Would point origin of", out)
        self.assertIn("with git mv and commit it.", out)
        self.assertEqual(self.counts(NEW)["items"], 0)
        self.assertEqual((self.state_tree(), self.projects.read_text(), (self.state / "hold.json").read_bytes()),
                         (tree, projects, hold))
        self.assertEqual(self.git("-C", str(self.checkout), "remote", "get-url", "origin").strip(),
                         "git@github.com:org/game.git")
        self.assertTrue((self.data / "org" / "game").exists())


class RefusalTest(RenameCase):
    def test_it_refuses_off_hold(self):
        self.hold({"state": "manual"})
        _, err = self.rename(OLD, NEW, status=1)
        self.assertIn("stopped at check: org/game is on Manual, not Hold.", err)
        self.assertEqual(self.counts(NEW)["items"], 0)
        out, _ = self.rename("--dry-run", OLD, NEW)
        self.assertIn("Would refuse now: org/game is on Manual, not Hold.", out)

    def test_it_refuses_while_a_run_or_a_check_works(self):
        desk = self.store()
        run = desk.request_run(OLD, "agent", "1")
        desk.run_started(run, "agent-desk-agent-org-game-1-2.service")
        check = desk.check_queue(OLD, "agent/issue-1", "b" * 40)
        desk.check_started(check, str(self.state / "checks" / "org__game" / f"{check}.log"))
        _, err = self.rename(OLD, NEW, status=1)
        self.assertIn(f"1 run still working (agent {run})", err)
        self.assertIn("1 branch check running (agent/issue-1)", err)

    def test_a_check_hold_keeps_queued_moves_to_the_new_name(self):
        desk = self.store()
        check = desk.check_queue(OLD, "agent/issue-1", "b" * 40)
        desk.check_waits(check, "mode", "Resume")
        out, _ = self.rename("--dry-run", OLD, NEW)
        self.assertNotIn("Would refuse now", out)
        out, _ = self.rename(OLD, NEW)
        self.assertIn("1 branch check queued (agent/issue-1), which Hold keeps from starting, so the rename moves each "
                      "to the new name, where it starts on Resume.", out)
        self.assertEqual(desk.checks(("queued",), project=OLD), [])
        [row] = desk.checks(("queued",), project=NEW)
        self.assertEqual((row["id"], row["branch"], row["head_sha"], row["wait_gate"], row["log_path"]),
                         (check, "agent/issue-1", "b" * 40, "mode", None))

    def test_it_refuses_with_a_merge_queue(self):
        (self.state / "merges" / "org__game.json").write_text(json.dumps({"queue": [{"pr": 3}]}))
        _, err = self.rename(OLD, NEW, status=1)
        self.assertIn("the merge queue holds #3", err)

    def test_it_refuses_a_new_name_another_project_has(self):
        _, err = self.rename(OLD, "other/thing", status=1)
        self.assertIn("the store holds both org/game and other/thing", err)
        self.assertEqual(self.counts("other/thing")["items"], 1)

    def test_it_refuses_a_new_name_in_the_projects_file(self):
        self.projects.write_text(f"{OLD} {self.checkout}\n{NEW} {self.root / 'elsewhere'}\n")
        _, err = self.rename(OLD, NEW, status=1)
        self.assertIn(f"{self.projects} already names {NEW}", err)
        self.assertEqual(self.counts(NEW)["items"], 0)

    def test_it_refuses_a_project_the_store_lacks(self):
        _, err = self.rename("org/missing", NEW, status=1)
        self.assertIn("the store holds no project org/missing.", err)

    def test_it_refuses_a_name_that_isnt_owner_and_repo(self):
        self.rename(OLD, "game", status=2)
        self.rename(OLD, OLD, status=2)


class RerunTest(RenameCase):
    def test_a_rerun_after_a_failure_finishes_what_is_left(self):
        self.backup_repo()
        # A file that differs at both names stops the files step after the store step committed.
        (self.state / "pushes" / "org__game-dev.json").write_text('{"other": 1}')
        _, err = self.rename(OLD, NEW, status=1)
        self.assertIn("stopped at files: both", err)
        self.assertEqual(self.counts(OLD)["items"], 0)
        self.assertEqual(self.projects.read_text().split("\n")[1].split()[0], OLD)
        (self.state / "pushes" / "org__game-dev.json").unlink()
        out, _ = self.rename(OLD, NEW)
        self.assertIn("Done: the store holds org/game-dev.", out)
        self.assertIn("Moved pushes/org__game.json to pushes/org__game-dev.json.", out)
        self.assertEqual(self.projects.read_text().split("\n")[1].split()[0], NEW)
        self.assertEqual(json.loads((self.state / "hold.json").read_text())["projects"][NEW], "held")
        self.assertFalse((self.data / "org" / "game").exists())
        # A third run finds every step done.
        out, _ = self.rename(OLD, NEW)
        self.assertNotIn("Moved", out)
        self.assertNotIn("Renamed org/game to org/game-dev in", out)
        events = [one for one in self.store().events_since(0) if one["type"] == "project-renamed"]
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
