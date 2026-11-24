"""Tests for agent-desk/lib/backup.py, which backs the local store up to a git repository and to snapshots."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import backup  # noqa: E402
import store  # noqa: E402

REPO = "org/game"
OTHER = "org/tools"
CLOCK = 1_800_000_000


class BackupCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.database = self.root / "state" / "desk.sqlite"
        self.data = self.root / "agent-desk-data"
        self.snapshots = self.root / "state" / "snapshots"
        # Git reads no configuration from the machine running the tests, such as a signing key or a default branch.
        (self.root / "gitconfig").write_text("")
        env = {key: value for key, value in os.environ.items()
               if key not in ("AGENT_DESK_RUN", "AGENT_DESK_AUTHOR") and not key.startswith("GIT_")}
        env.update({"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.store = store.Store(self.database)
        self.addCleanup(self.store.close)

    def fill(self):
        """Files issues and a review in two projects, with labels, links, comments, and a merge."""
        first = self.store.create(REPO, "issue", "Add a store", "The body.", labels=["serial:web"], path="/src/game")
        second = self.store.create(REPO, "issue", "Use the store", labels=["agent-ready"])
        self.store.link(REPO, first, second, "blocks")
        self.store.comment(REPO, first, "A comment.")
        review = self.store.create(REPO, "review", "Add a store", branch="agent/issue-1", head_sha="abc")
        self.store.link(REPO, review, first, "fixes")
        self.store.comment(REPO, review, "On a line.", path="lib/store.py", line=12, head_sha="abc")
        self.store.merge(REPO, review, "def")
        self.store.create(OTHER, "issue", "A tool")

    def git(self, *args, folder=None):
        return subprocess.run(["git", "-C", str(folder or self.data), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def commits(self):
        return int(self.git("rev-list", "--count", "HEAD"))

    def remote(self, path=None):
        """Adds a bare repository as origin, or a path that doesn't exist, and returns the bare repository."""
        bare = path or self.root / "remote.git"
        if path is None:
            subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
        self.git("remote", "add", "origin", str(bare))
        return bare


class ExportTest(BackupCase):
    def test_each_item_becomes_a_file_with_sorted_keys(self):
        self.fill()
        backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(sorted(str(path.relative_to(self.data)) for path in self.data.glob("*/*/*.json")),
                         ["org/game/1.json", "org/game/2.json", "org/game/3.json", "org/game/label_colors.json",
                          "org/game/project.json", "org/tools/1.json", "org/tools/label_colors.json",
                          "org/tools/project.json"])
        text = (self.data / "org/game/3.json").read_text()
        item = json.loads(text)
        self.assertEqual(text, json.dumps(item, indent=2, sort_keys=True) + "\n")
        self.assertEqual(item["review"], {"base": "main", "branch": "agent/issue-1", "draft": 0, "head_sha": "abc",
                                          "merged_sha": "def"})
        self.assertEqual(item["links"], [{"kind": "fixes", "to": 1}])
        self.assertEqual([(one["body"], one["path"], one["line"]) for one in item["comments"]],
                         [("On a line.", "lib/store.py", 12)])
        # A line comment no Send to agent carried still names its empty sent_in, which a restore keeps.
        self.assertIsNone(item["comments"][0]["sent_in"])
        first = json.loads((self.data / "org/game/1.json").read_text())
        self.assertEqual((first["labels"], first["links"], first["state"], first["review"]),
                         (["serial:web"], [{"kind": "blocks", "to": 2}], "closed", None))
        self.assertEqual(json.loads((self.data / "org/game/2.json").read_text())["state"], "queued")
        self.assertEqual(json.loads((self.data / "org/game/project.json").read_text()),
                         {"mode": None, "next_number": 4, "path": "/src/game"})

    def test_an_unchanged_run_commits_nothing(self):
        self.fill()
        backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(self.commits(), 1)
        lines = backup.backup(self.database, self.data, CLOCK + 900)
        self.assertEqual(self.commits(), 1)
        self.assertIn("No item changed", lines[0])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_changed_item_commits_only_its_file(self):
        self.fill()
        backup.backup(self.database, self.data, CLOCK)
        self.store.edit(OTHER, 1, title="A better tool")
        lines = backup.backup(self.database, self.data, CLOCK + 900)
        self.assertEqual(self.commits(), 2)
        self.assertEqual(lines[0], f"Committed 1 changed file to {self.data}.")
        self.assertEqual(self.git("diff", "--name-only", "HEAD~1", "HEAD"), "org/tools/1.json")
        self.assertEqual(json.loads((self.data / "org/tools/1.json").read_text())["title"], "A better tool")

    def test_label_colors_sit_beside_the_project_with_sorted_keys(self):
        self.fill()
        self.store.import_label_colors(REPO, {"serial:web": "5319e7", "bug": "d73a4a"})
        backup.backup(self.database, self.data, CLOCK)
        self.assertEqual((self.data / "org/game/label_colors.json").read_text(),
                         '{\n  "bug": "d73a4a",\n  "serial:web": "5319e7"\n}\n')
        self.assertEqual(json.loads((self.data / "org/tools/label_colors.json").read_text()), {})
        self.store.set_label_color(REPO, "bug", "00ff00")
        backup.backup(self.database, self.data, CLOCK + 900)
        self.assertEqual(self.git("diff", "--name-only", "HEAD~1", "HEAD"), "org/game/label_colors.json")

    def test_an_empty_database_deletes_no_file(self):
        self.fill()
        backup.backup(self.database, self.data, CLOCK)
        empty = self.root / "empty" / "desk.sqlite"
        store.Store(empty).close()
        lines = backup.backup(empty, self.data, CLOCK + 900)
        self.assertEqual(self.commits(), 1)
        self.assertIn("No item changed", lines[0])

    def test_a_missing_database_backs_up_nothing(self):
        lines = backup.backup(self.root / "none" / "desk.sqlite", self.data, CLOCK)
        self.assertIn("nothing to back up", lines[0])
        self.assertFalse(self.data.exists())

    def test_a_folder_of_other_files_is_refused(self):
        self.data.mkdir()
        (self.data / "notes.txt").write_text("mine")
        with self.assertRaisesRegex(backup.BackupError, "isn't a git repository"):
            backup.backup(self.database, self.data, CLOCK)


class PushTest(BackupCase):
    def setUp(self):
        super().setUp()
        self.fill()

    def remote_head(self, bare):
        branch = self.git("symbolic-ref", "--short", "HEAD")
        return self.git("rev-parse", f"refs/heads/{branch}", folder=bare)

    def test_no_remote_commits_without_pushing(self):
        lines = backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(lines, [f"Created the backup repository {self.data}.",
                                 f"Committed 8 changed files to {self.data}."])
        self.assertEqual(self.commits(), 1)

    def test_pushes_at_most_once_an_hour(self):
        backup.backup(self.database, self.data, CLOCK)
        bare = self.remote()
        lines = backup.backup(self.database, self.data, CLOCK)
        self.assertIn("Pushed", lines[-1])
        self.assertEqual(self.remote_head(bare), self.git("rev-parse", "HEAD"))
        first = self.git("rev-parse", "HEAD")

        self.store.edit(REPO, 2, title="Use the store everywhere")
        lines = backup.backup(self.database, self.data, CLOCK + 1800)
        self.assertIn("waits until", lines[-1])
        self.assertEqual(self.remote_head(bare), first)

        # The commit still waits 45 minutes after the last push, with nothing new committed.
        lines = backup.backup(self.database, self.data, CLOCK + 2700)
        self.assertTrue(lines[0].startswith("No item changed"))
        self.assertIn("waits until", lines[-1])

        lines = backup.backup(self.database, self.data, CLOCK + 3600)
        self.assertIn("Pushed", lines[-1])
        self.assertEqual(self.remote_head(bare), self.git("rev-parse", "HEAD"))
        self.assertNotEqual(self.remote_head(bare), first)

    def test_a_failed_push_warns_and_a_later_run_retries(self):
        backup.backup(self.database, self.data, CLOCK)
        missing = self.root / "gone.git"
        self.remote(missing)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(backup, "DATA", self.data), mock.patch.object(store, "DATABASE", self.database), \
                mock.patch.object(backup.time, "time", return_value=CLOCK):
            status = backup.main([])
        self.assertEqual(status, 0)
        self.assertIn("agent-desk: warning: the push to origin failed", err.getvalue())
        self.assertEqual(self.git("config", "--default", "", "agent-desk.pushed-sha"), "")

        # The next run, 15 minutes on, pushes once the remote exists, though it has nothing new to commit.
        subprocess.run(["git", "init", "--quiet", "--bare", str(missing)], check=True)
        lines = backup.backup(self.database, self.data, CLOCK + 900)
        self.assertIn("No item changed", lines[0])
        self.assertIn("Pushed", lines[-1])
        self.assertEqual(self.remote_head(missing), self.git("rev-parse", "HEAD"))


class StateTest(BackupCase):
    """The state each run saves beside the database, which the board reads."""

    def setUp(self):
        super().setUp()
        self.fill()

    def test_no_remote_saves_the_commit_and_no_remote(self):
        self.assertIsNone(backup.saved(self.database))
        backup.backup(self.database, self.data, CLOCK)
        state = backup.saved(self.database)
        self.assertEqual({key: state[key] for key in ("at", "remote", "waiting")},
                         {"at": CLOCK, "remote": None, "waiting": False})
        self.assertEqual(state["committedAt"], int(self.git("log", "-1", "--format=%ct")))
        self.assertNotIn("pushedAt", state)

    def test_a_push_saves_its_time_and_a_commit_waits_for_the_next(self):
        backup.backup(self.database, self.data, CLOCK)
        self.remote()
        backup.backup(self.database, self.data, CLOCK)
        state = backup.saved(self.database)
        self.assertEqual((state["remote"], state["pushedAt"], state["waiting"]), ("origin", CLOCK, False))
        self.store.edit(REPO, 2, title="Use the store everywhere")
        backup.backup(self.database, self.data, CLOCK + 1800)
        state = backup.saved(self.database)
        self.assertEqual((state["pushedAt"], state["waiting"]), (CLOCK, True))
        self.assertNotIn("error", state)

    def test_a_failed_push_saves_its_error_until_a_push_works(self):
        backup.backup(self.database, self.data, CLOCK)
        missing = self.root / "gone.git"
        self.remote(missing)
        backup.backup(self.database, self.data, CLOCK)
        state = backup.saved(self.database)
        self.assertTrue(state["error"].startswith("the push to origin failed: "))
        self.assertTrue(state["waiting"])
        subprocess.run(["git", "init", "--quiet", "--bare", str(missing)], check=True)
        backup.backup(self.database, self.data, CLOCK + 900)
        state = backup.saved(self.database)
        self.assertNotIn("error", state)
        self.assertEqual((state["pushedAt"], state["waiting"]), (CLOCK + 900, False))

    def test_a_failed_backup_keeps_the_last_state_and_adds_its_error(self):
        backup.backup(self.database, self.data, CLOCK)
        committed = backup.saved(self.database)["committedAt"]
        with mock.patch.object(backup, "export", side_effect=OSError("disk full")):
            _, failed = backup.tick(self.database, self.data, self.snapshots, CLOCK + 900, date(2027, 1, 15))
        self.assertTrue(failed)
        state = backup.saved(self.database)
        self.assertEqual((state["at"], state["committedAt"], state["error"]),
                         (CLOCK + 900, committed, "the backup failed: disk full"))


class GitHubTest(BackupCase):
    """The visibility check before a push to github.com, with gh stubbed by a script on PATH."""

    def setUp(self):
        super().setUp()
        self.fill()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "gh-calls"
        gh = self.bin / "gh"
        gh.write_text(
            '#!/bin/sh\n'
            f'echo "$*" >> "{self.calls}"\n'
            'if [ -n "$STUB_GH_ERROR" ]; then echo "$STUB_GH_ERROR" >&2; exit 1; fi\n'
            'printf \'{"visibility":"%s"}\\n\' "$STUB_GH_VISIBILITY"\n')
        gh.chmod(0o755)
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"
        # A push to a URL other than a local path fails at once, without reaching the network, so a test sees that
        # the backup tried it.
        os.environ["GIT_ALLOW_PROTOCOL"] = "file"
        backup.backup(self.database, self.data, CLOCK)

    def point_at(self, url):
        self.git("remote", "add", "origin", url)

    def gh_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_reads_owner_and_repo_from_each_github_url_form(self):
        for url, slug in [("https://github.com/org/backup", "org/backup"),
                          ("https://github.com/org/backup.git", "org/backup"),
                          ("https://token@GitHub.com/org/backup.git/", "org/backup"),
                          ("git@github.com:org/backup.git", "org/backup"),
                          ("github.com:org/backup", "org/backup"),
                          ("ssh://git@github.com:22/org/backup.git", "org/backup"),
                          ("https://github.com/org", ""),
                          ("https://gitlab.com/org/backup.git", None),
                          ("git@github.com.example:org/backup", None),
                          (str(self.root / "remote.git"), None)]:
            self.assertEqual(backup.github_repository(url), slug, url)

    def test_a_private_remote_pushes(self):
        self.point_at("git@github.com:org/backup.git")
        os.environ["STUB_GH_VISIBILITY"] = "PRIVATE"
        backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(self.gh_calls(), ["repo view org/backup --json visibility"])
        # The push went ahead and failed on the transport the test refuses.
        self.assertTrue(backup.saved(self.database)["error"].startswith("the push to origin failed: "))

    def test_a_public_remote_skips_the_push_and_keeps_the_commit(self):
        self.point_at("https://github.com/org/backup.git")
        os.environ["STUB_GH_VISIBILITY"] = "PUBLIC"
        self.store.edit(REPO, 2, title="Use the store everywhere")
        lines = backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(self.commits(), 2)
        self.assertEqual(lines[-1], "agent-desk: warning: origin is the public GitHub repository org/backup, and the "
                                    "backup pushes only to a private repository, so it doesn't push.")
        state = backup.saved(self.database)
        self.assertEqual(state["error"], "origin is the public GitHub repository org/backup, and the backup pushes "
                                         "only to a private repository, so it doesn't push")
        self.assertTrue(state["waiting"])
        self.assertNotIn("pushedAt", state)
        self.assertEqual(self.git("config", "--default", "", "agent-desk.pushed-sha"), "")

    def test_a_push_url_on_github_is_checked_though_the_fetch_url_is_elsewhere(self):
        self.point_at(str(self.root / "remote.git"))
        self.git("config", "remote.origin.pushurl", "git@github.com:org/backup.git")
        os.environ["STUB_GH_VISIBILITY"] = "PUBLIC"
        backup.backup(self.database, self.data, CLOCK)
        self.assertIn("public GitHub repository org/backup", backup.saved(self.database)["error"])

    def test_a_failed_gh_call_skips_the_push_and_the_next_run_asks_again(self):
        self.point_at("https://github.com/org/backup.git")
        os.environ["STUB_GH_ERROR"] = "To get started with GitHub CLI, please run:  gh auth login"
        backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(backup.saved(self.database)["error"],
                         "the backup can't tell whether origin, the GitHub repository org/backup, is private, so it "
                         "doesn't push, and the next backup asks again: To get started with GitHub CLI, please run:  "
                         "gh auth login")
        self.assertEqual(self.commits(), 1)

        del os.environ["STUB_GH_ERROR"]
        os.environ["STUB_GH_VISIBILITY"] = "PRIVATE"
        backup.backup(self.database, self.data, CLOCK + 900)
        self.assertEqual(len(self.gh_calls()), 2)
        self.assertTrue(backup.saved(self.database)["error"].startswith("the push to origin failed: "))

    def test_gh_missing_skips_the_push(self):
        self.point_at("https://github.com/org/backup.git")
        only_git = self.root / "only-git"
        only_git.mkdir()
        (only_git / "git").symlink_to(shutil.which("git"))
        os.environ["PATH"] = str(only_git)
        backup.backup(self.database, self.data, CLOCK)
        self.assertTrue(backup.saved(self.database)["error"].endswith("is private, so it doesn't push, and the next "
                                                                      "backup asks again: gh isn't installed"))
        self.assertEqual(self.gh_calls(), [])

    def test_a_github_url_without_a_repository_skips_the_push(self):
        self.point_at("https://github.com/org")
        backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(backup.saved(self.database)["error"],
                         "origin pushes to https://github.com/org, which names no GitHub repository, so the backup "
                         "doesn't push")
        self.assertEqual(self.gh_calls(), [])

    def test_a_remote_on_another_host_pushes_without_asking_gh(self):
        self.point_at("https://gitlab.example/org/backup.git")
        backup.backup(self.database, self.data, CLOCK)
        self.assertEqual(self.gh_calls(), [])
        self.assertTrue(backup.saved(self.database)["error"].startswith("the push to origin failed: "))


class SnapshotTest(BackupCase):
    def test_writes_todays_copy_and_deletes_those_older_than_seven_days(self):
        self.fill()
        self.snapshots.mkdir(parents=True)
        for day in ("2026-11-01", "2026-11-04", "2026-11-05", "2026-11-10"):
            (self.snapshots / f"{day}.sqlite").write_text("old")
        (self.snapshots / "keep.txt").write_text("not a snapshot")
        lines = backup.snapshot(self.database, self.snapshots, date(2026, 11, 11))
        self.assertEqual(sorted(path.name for path in self.snapshots.iterdir()),
                         ["2026-11-05.sqlite", "2026-11-10.sqlite", "2026-11-11.sqlite", "keep.txt"])
        self.assertEqual(len(lines), 3)
        self.assertEqual(backup.counts(self.snapshots / "2026-11-11.sqlite"), {REPO: (2, 1), OTHER: (1, 0)})

    def test_a_second_snapshot_the_same_day_replaces_the_first(self):
        backup.snapshot(self.database, self.snapshots, date(2026, 11, 11))
        self.fill()
        backup.snapshot(self.database, self.snapshots, date(2026, 11, 11))
        self.assertEqual([path.name for path in self.snapshots.iterdir()], ["2026-11-11.sqlite"])
        self.assertEqual(backup.counts(self.snapshots / "2026-11-11.sqlite"), {REPO: (2, 1), OTHER: (1, 0)})

    def test_the_tick_backs_up_each_time_and_snapshots_once_a_day(self):
        self.fill()
        today = date(2026, 11, 11)
        lines, failed = backup.tick(self.database, self.data, self.snapshots, CLOCK, today)
        self.assertFalse(failed)
        self.assertTrue(any("2026-11-11.sqlite" in line for line in lines))
        before = (self.snapshots / "2026-11-11.sqlite").stat().st_mtime_ns
        self.store.edit(OTHER, 1, title="A better tool")
        lines, failed = backup.tick(self.database, self.data, self.snapshots, CLOCK + 900, today)
        self.assertEqual(self.commits(), 2)
        self.assertEqual((self.snapshots / "2026-11-11.sqlite").stat().st_mtime_ns, before)
        self.assertFalse(any("snapshot" in line or ".sqlite" in line for line in lines))


class RestoreTest(BackupCase):
    def setUp(self):
        super().setUp()
        self.fill()
        self.target = self.root / "rehearsal" / "agent-desk"

    def restored(self):
        again = store.Store(self.target / "desk.sqlite")
        self.addCleanup(again.close)
        return again

    def test_from_the_repository_with_matching_counts(self):
        backup.backup(self.database, self.data, CLOCK)
        lines = backup.restore(self.data, self.target)
        self.assertEqual(lines[1:], [f"{REPO}: 3 items (2 issues, 1 review)", f"{OTHER}: 1 item (1 issue, 0 reviews)"])
        self.assertEqual(backup.counts(self.target / "desk.sqlite"), backup.counts(self.database))
        again = self.restored()
        for project, number in ((REPO, 1), (REPO, 2), (REPO, 3), (OTHER, 1)):
            self.assertEqual(again.item(project, number), self.store.item(project, number))
        # New numbers continue past the restored ones.
        self.assertEqual(again.create(REPO, "issue", "After the restore"), 4)

    def test_the_backup_leaves_the_list_order_out_and_a_restore_lists_each_issue_by_number(self):
        for title in ("Paint", "Sand", "Oil"):
            self.store.create(REPO, "issue", title)
        self.store.list_move(REPO, 4, where="top")
        self.assertEqual([one["number"] for one in self.store.list_order(REPO)], [4, 6, 5])
        backup.backup(self.database, self.data, CLOCK)
        self.assertNotIn("rank", (self.data / REPO / "4.json").read_text())
        backup.restore(self.data, self.target)
        again = self.restored()
        self.assertEqual([one["number"] for one in again.list_order(REPO)], [6, 5, 4])
        ranks = again.db.execute("SELECT number, list_rank FROM items WHERE project = ? ORDER BY number", (REPO,))
        self.assertEqual([tuple(row) for row in ranks], [(1, 1), (2, 2), (3, None), (4, 4), (5, 5), (6, 6)])

    def test_an_imported_comment_keeps_its_remote_id_and_an_older_backup_restores_without_one(self):
        self.store.import_comments(REPO, [{"remote_id": "IC_kwDO1", "number": 1, "author": "you", "body": "Imported.",
                                           "path": None, "line": None, "head_sha": None, "to_agent": False,
                                           "created_at": "2026-10-01T10:00:00Z"}])
        backup.backup(self.database, self.data, CLOCK)
        older = self.data / REPO / "1.json"
        item = json.loads(older.read_text())
        self.assertEqual([one["remote_id"] for one in item["comments"]], [None, "IC_kwDO1"])
        backup.restore(self.data, self.target)
        found = self.restored().db.execute("SELECT remote_id FROM comments WHERE project = ? AND number = 1"
                                           " ORDER BY id", (REPO,)).fetchall()
        self.assertEqual([row[0] for row in found], [None, "IC_kwDO1"])
        for comment in item["comments"]:
            del comment["remote_id"]
        older.write_text(json.dumps(item))
        again = self.root / "older" / "agent-desk"
        backup.restore(self.data, again)
        self.assertEqual(backup.counts(again / "desk.sqlite"), backup.counts(self.database))

    def test_label_colors_come_back_and_an_older_backup_restores_without_them(self):
        self.store.import_label_colors(REPO, {"serial:web": "5319e7", "bug": "d73a4a"})
        self.store.set_label_color(OTHER, "idea", "c5def5")
        backup.backup(self.database, self.data, CLOCK)
        backup.restore(self.data, self.target)
        again = self.restored()
        self.assertEqual(again.label_colors(REPO), {"bug": "d73a4a", "serial:web": "5319e7"})
        self.assertEqual(again.label_colors(OTHER), {"idea": "c5def5"})
        for place in (REPO, OTHER):
            (self.data / place / "label_colors.json").unlink()
        older = self.root / "older" / "agent-desk"
        backup.restore(self.data, older)
        before = store.Store(older / "desk.sqlite")
        self.addCleanup(before.close)
        self.assertEqual((before.label_colors(REPO), before.label_colors(OTHER)), ({}, {}))
        self.assertEqual(backup.counts(older / "desk.sqlite"), backup.counts(self.database))

    def test_a_sent_line_comment_comes_back_sent_and_a_waiting_one_waiting(self):
        review = self.store.create(REPO, "review", "Paint", branch="agent/issue-8")
        line = self.store.comment(REPO, review, "Here.", path="a.py", line=1)
        sent = self.store.comment(REPO, review, "Fix it.", to_agent=True, quotes=[line])
        waiting = self.store.comment(REPO, review, "And here.", path="b.py", line=2)
        backup.backup(self.database, self.data, CLOCK)
        comments = json.loads((self.data / REPO / f"{review}.json").read_text())["comments"]
        self.assertEqual([one["sent_in"] for one in comments], [sent, None, None])
        backup.restore(self.data, self.target)
        again = self.restored()
        found = again.db.execute("SELECT sent_in FROM comments WHERE number = ? ORDER BY id", (review,)).fetchall()
        self.assertEqual([row[0] for row in found], [sent, None, None])
        again.comment(REPO, review, "Now this.", to_agent=True, quotes=[waiting])

    def test_an_older_backup_restores_every_line_comment_as_sent(self):
        review = self.store.create(REPO, "review", "Paint", branch="agent/issue-8")
        line = self.store.comment(REPO, review, "Here.", path="a.py", line=1)
        self.store.comment(REPO, review, "Fix it.", to_agent=True, quotes=[line])
        waiting = self.store.comment(REPO, review, "And here.", path="b.py", line=2)
        backup.backup(self.database, self.data, CLOCK)
        # A backup from before every comment carried sent_in, which left it out of some line comments, sent or not.
        path = self.data / REPO / f"{review}.json"
        item = json.loads(path.read_text())
        for comment in item["comments"]:
            del comment["sent_in"]
        path.write_text(backup.dump(item))
        backup.restore(self.data, self.target)
        again = self.restored()
        found = again.db.execute("SELECT path, sent_in FROM comments WHERE number = ? ORDER BY id",
                                 (review,)).fetchall()
        self.assertEqual([tuple(row) for row in found], [("a.py", 0), (None, None), ("b.py", 0)])
        with self.assertRaisesRegex(store.StoreError, "waiting to be sent"):
            again.comment(REPO, review, "Again.", to_agent=True, quotes=[waiting])

    def test_a_rebase_merge_keeps_its_commit_count(self):
        self.store.db.execute("UPDATE reviews SET merged_commits = 3 WHERE project = ?", (REPO,))
        self.store.db.commit()
        backup.backup(self.database, self.data, CLOCK)
        # project.json and label_colors.json sit beside the items, in whatever order the folder lists them.
        review = next(path for path in (self.data / REPO).glob("*.json")
                      if json.loads(path.read_text()).get("review"))
        self.assertEqual(json.loads(review.read_text())["review"]["merged_commits"], 3)
        backup.restore(self.data, self.target)
        self.assertEqual(self.restored().db.execute("SELECT merged_commits FROM reviews").fetchall()[0][0], 3)

    def test_from_a_snapshot_with_matching_counts(self):
        backup.snapshot(self.database, self.snapshots, date(2026, 11, 11))
        lines = backup.restore(self.snapshots / "2026-11-11.sqlite", self.target)
        self.assertIn("from the snapshot", lines[0])
        self.assertEqual(backup.counts(self.target / "desk.sqlite"), backup.counts(self.database))
        self.assertEqual(self.restored().last_event(), self.store.last_event())

    def test_a_snapshot_keeps_the_label_colors(self):
        self.store.set_label_color(REPO, "bug", "d73a4a")
        backup.snapshot(self.database, self.snapshots, date(2026, 11, 11))
        backup.restore(self.snapshots / "2026-11-11.sqlite", self.target)
        self.assertEqual(self.restored().label_colors(REPO), {"bug": "d73a4a"})

    def test_the_restored_database_stands_alone(self):
        backup.backup(self.database, self.data, CLOCK)
        backup.restore(self.data, self.target)
        self.assertEqual(sorted(path.name for path in self.target.iterdir()), ["desk.sqlite"])

    def test_refuses_to_replace_a_database(self):
        backup.backup(self.database, self.data, CLOCK)
        with self.assertRaisesRegex(backup.BackupError, "already exists"):
            backup.restore(self.data, self.database.parent)
        self.assertEqual(self.store.item(OTHER, 1)["title"], "A tool")

    def test_the_command_line_restores_and_refuses_again(self):
        backup.backup(self.database, self.data, CLOCK)
        argv = ["restore", "--from", str(self.data), "--to", str(self.target)]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(backup.main(argv), 0)
            self.assertEqual(backup.main(argv), 1)
        self.assertIn(f"{REPO}: 3 items", out.getvalue())
        self.assertIn("already exists", err.getvalue())


class InstallTest(BackupCase):
    def test_writes_the_units_and_enables_the_timer(self):
        units = self.root / "units"
        calls = []

        def systemctl(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(backup.subprocess, "run", systemctl), mock.patch.object(backup, "DATA", self.data):
            backup.install(units, Path("/opt/agent-desk/bin/agent-desk"))
        service = (units / "agent-desk-backup.service").read_text()
        self.assertIn(f'Environment="AGENT_DESK_DATA={self.data}"\n', service)
        self.assertIn("ExecStart=/opt/agent-desk/bin/agent-desk backup tick\n", service)
        # The variable that lets the unit migrate the store sits where ./install.sh puts it, so a reinstall keeps it.
        self.assertIn('[Service]\nEnvironment="AGENT_DESK_INSTALLED=/opt/agent-desk"\n', service)
        self.assertIn("OnUnitActiveSec=15min\n", (units / "agent-desk-backup.timer").read_text())
        self.assertEqual(calls, [["systemctl", "--user", "daemon-reload"],
                                 ["systemctl", "--user", "enable", "--now", "agent-desk-backup.timer"]])


if __name__ == "__main__":
    unittest.main()
