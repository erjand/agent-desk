"""Tests for agent-desk/lib/store.py, the SQLite store behind agent-desk issue and agent-desk review."""

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import store  # noqa: E402
import store_cli  # noqa: E402
import store_schema  # noqa: E402

REPO = "org/game"


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "desk.sqlite"
        self.store = store.Store(self.path)
        self.addCleanup(self.store.close)
        env = {key: value for key, value in os.environ.items()
               if key not in ("AGENT_DESK_RUN", "AGENT_DESK_AUTHOR")}
        env["GH_REPO"] = REPO
        patches = [mock.patch.dict(os.environ, env, clear=True), mock.patch.object(store, "DATABASE", self.path),
                   mock.patch.object(store, "PROJECTS", Path(self.folder.name) / "projects")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def events(self, last=0):
        return [(event["number"], event["type"]) for event in self.store.events_since(last)]

    def cli(self, *argv):
        """A command's exit status, what it printed, and what it said on the standard error."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = store_cli.main(list(argv))
        return status, out.getvalue().strip(), err.getvalue().strip()


class StateFolderTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.parent = Path(self.folder.name) / "state"
        self.state = self.parent / "agent-desk"
        patch = mock.patch.object(store, "STATE", self.state)
        patch.start()
        self.addCleanup(patch.stop)

    def open(self):
        store.Store(self.state / "desk.sqlite").close()

    def mode(self, path):
        return path.stat().st_mode & 0o777

    def test_a_new_state_folder_is_private(self):
        self.open()
        self.assertEqual(self.mode(self.state), 0o700)

    def test_an_open_state_folder_narrows_and_its_parent_keeps_its_mode(self):
        self.state.mkdir(parents=True)
        self.parent.chmod(0o755)
        self.state.chmod(0o755)
        self.open()
        self.assertEqual(self.mode(self.state), 0o700)
        self.assertEqual(self.mode(self.parent), 0o755)

    def test_a_database_outside_the_state_folder_leaves_its_folder_alone(self):
        other = Path(self.folder.name) / "elsewhere"
        other.mkdir(mode=0o755)
        other.chmod(0o755)
        store.Store(other / "desk.sqlite").close()
        self.assertEqual(self.mode(other), 0o755)


class SchemaTest(StoreCase):
    def test_a_new_database_is_in_wal_mode_at_the_current_version(self):
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)

    def test_opening_twice_migrates_once(self):
        self.store.create(REPO, "issue", "Kept")
        again = store.Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual(again.item(REPO, 1)["title"], "Kept")

    def test_older_code_reads_a_newer_database_and_refuses_to_write(self):
        self.store.create(REPO, "issue", "Kept")
        self.store.db.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
        old = store.Store(self.path)
        self.addCleanup(old.close)
        self.assertEqual(old.item(REPO, 1)["title"], "Kept")
        with self.assertRaisesRegex(store.StoreError, "newer than this agent-desk"):
            old.create(REPO, "issue", "Refused")

    def test_a_process_that_opened_first_stops_writing_once_newer_code_migrates(self):
        self.assertFalse(self.store.schema_newer())
        newer = store.Store(self.path)
        self.addCleanup(newer.close)
        newer.db.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
        self.assertTrue(self.store.schema_newer())
        with self.assertRaisesRegex(store.StoreError, "newer than this agent-desk"):
            self.store.create(REPO, "issue", "Refused")
        self.assertFalse(self.store.db.in_transaction)

    def test_a_write_takes_the_lock_at_once_and_a_failed_one_leaves_nothing(self):
        with self.assertRaises(store.StoreError):
            with self.store.write():
                self.store._project(REPO)
                self.assertTrue(self.store.db.in_transaction)
                raise store.StoreError("stop")
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM projects").fetchone())

    def test_a_write_inside_another_joins_it_and_commits_or_rolls_back_with_it(self):
        number = self.store.create(REPO, "issue", "Joined")
        last = self.store.last_event()
        with self.assertRaises(store.StoreError):
            with self.store.write():
                self.store.comment(REPO, number, "First")
                self.store.comment(REPO, number, "Second")
                raise store.StoreError("stop")
        self.assertEqual(self.store.last_event(), last)
        with self.store.write():
            self.store.comment(REPO, number, "First")
            self.store.comment(REPO, number, "Second")
        self.assertFalse(self.store.db.in_transaction)
        self.assertEqual(self.events(last), [(number, "comment-added"), (number, "comment-added")])
        saved = self.path.with_name(f"{self.path.name}-event")
        self.assertEqual(saved.read_text(encoding="utf-8"), f"{self.store.last_event()}\n")

    def test_writers_in_several_threads_each_get_their_own_number(self):
        numbers = []

        def file(index):
            one = store.Store(self.path)
            numbers.append(one.create(REPO, "issue", f"Issue {index}"))
            one.close()

        threads = [threading.Thread(target=file, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(numbers), list(range(1, 9)))


def older_database(path):
    """Writes a database one schema behind this code's, in rollback-journal mode, holding issue 1 and review 2."""
    with mock.patch.object(store, "SCHEMA_VERSION", store.SCHEMA_VERSION - 1):
        old = store.Store(path)
        old.create(REPO, "issue", "Old issue")
        old.create(REPO, "review", "Old review", branch="agent/issue-1")
        old.close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode = DELETE")
    db.close()
    return path


def version_of(path):
    db = sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True)
    try:
        return db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()


class InstalledTest(StoreCase):
    """Only the installed checkout's code creates or migrates the live database."""

    def setUp(self):
        super().setUp()
        self.live = Path(self.folder.name) / "live" / "desk.sqlite"
        self.worktree = Path(self.folder.name) / "agent-desk-issue-1"
        os.environ["PATH"] = "/usr/bin:/bin"
        os.environ.pop("AGENT_DESK_INSTALLED", None)
        for patch in (mock.patch.object(store, "LIVE", self.live), mock.patch.object(store, "CHECKOUT", self.worktree)):
            patch.start()
            self.addCleanup(patch.stop)

    def older_live(self):
        self.live.parent.mkdir()
        older_database(Path(self.folder.name) / "old.sqlite").rename(self.live)
        return self.live.read_bytes()

    def test_a_worktrees_code_refuses_to_create_the_live_database(self):
        with self.assertRaises(store.StoreError) as raised:
            store.Store(self.live)
        for named in (str(self.live), str(self.worktree), "XDG_STATE_HOME"):
            self.assertIn(named, str(raised.exception))
        self.assertFalse(self.live.parent.exists())

    def test_a_worktrees_code_refuses_to_migrate_the_live_database_and_changes_nothing(self):
        before = self.older_live()
        with self.assertRaisesRegex(store.StoreError, "older than this code's.*XDG_STATE_HOME"):
            store.Store(self.live)
        self.assertEqual(version_of(self.live), store.SCHEMA_VERSION - 1)
        self.assertEqual(self.live.read_bytes(), before)

    def test_the_checkout_agent_desk_installed_names_migrates_the_live_database(self):
        self.older_live()
        os.environ["AGENT_DESK_INSTALLED"] = str(self.worktree)
        desk = store.Store(self.live)
        self.addCleanup(desk.close)
        self.assertEqual(desk.version, store.SCHEMA_VERSION)
        self.assertEqual(desk.item(REPO, 1)["title"], "Old issue")

    def test_the_checkout_agent_desk_on_path_runs_creates_the_live_database(self):
        (self.worktree / "bin").mkdir(parents=True)
        command = self.worktree / "bin" / "agent-desk"
        command.write_text("#!/bin/sh\n")
        command.chmod(0o755)
        linked = Path(self.folder.name) / "path"
        linked.mkdir()
        (linked / "agent-desk").symlink_to(command)
        os.environ["PATH"] = f"{linked}:/usr/bin:/bin"
        desk = store.Store(self.live)
        self.addCleanup(desk.close)
        self.assertEqual(version_of(self.live), store.SCHEMA_VERSION)

    def test_a_worktrees_code_migrates_a_database_it_names(self):
        own = older_database(Path(self.folder.name) / "own.sqlite")
        store.Store(own).close()
        self.assertEqual(version_of(own), store.SCHEMA_VERSION)

    def test_this_checkouts_command_migrates_the_database_under_its_own_state_folder(self):
        state = Path(self.folder.name) / "state"
        (state / "agent-desk").mkdir(parents=True)
        own = older_database(Path(self.folder.name) / "old.sqlite").rename(state / "agent-desk" / "desk.sqlite")
        done = subprocess.run([sys.executable, "-B", str(Path(store.__file__)), "issue", "create", "--title", "New",
                               "--repo", REPO], env={**os.environ, "XDG_STATE_HOME": str(state)},
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(version_of(own), store.SCHEMA_VERSION)


class ReadOnlyTest(StoreCase):
    def test_a_read_only_store_creates_nothing_reads_empty_and_refuses_to_write(self):
        missing = Path(self.folder.name) / "none" / "desk.sqlite"
        desk = store.Store(missing, read_only=True)
        self.addCleanup(desk.close)
        self.assertEqual(desk.items(REPO, "issue"), [])
        with self.assertRaisesRegex(store.StoreError, "read-only"):
            desk.create(REPO, "issue", "Refused")
        self.assertFalse(missing.parent.exists())

    def test_a_read_only_store_reads_an_older_database_without_changing_it(self):
        old = older_database(Path(self.folder.name) / "old.sqlite")
        before = old.read_bytes()
        desk = store.Store(old, read_only=True)
        self.addCleanup(desk.close)
        self.assertEqual(desk.item(REPO, 1)["title"], "Old issue")
        self.assertEqual(desk.version, store.SCHEMA_VERSION - 1)
        self.assertEqual(desk.db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(old.read_bytes(), before)

    def test_a_read_only_store_reads_a_newer_database(self):
        self.store.create(REPO, "issue", "Kept")
        self.store.db.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
        desk = store.Store(self.path, read_only=True)
        self.addCleanup(desk.close)
        self.assertEqual(desk.item(REPO, 1)["title"], "Kept")

    def test_each_reading_command_leaves_an_older_database_unchanged(self):
        import board
        import dispatch
        folder = Path(self.folder.name)
        old = older_database(folder / "old" / "desk.sqlite")
        before = old.read_bytes()
        # The project's checkout sets no test command, so `review checks` reports no check and exits 0 instead of
        # reading the `.agent-desk.json` of the checkout the tests run in.
        (folder / "checkout").mkdir()
        store.PROJECTS.write_text(f"{REPO} {folder / 'checkout'}\n")
        commands = [(store_cli.main, ["issue", "view", "1"]), (store_cli.main, ["issue", "list"]),
                    (store_cli.main, ["review", "view", "2"]), (store_cli.main, ["review", "list"]),
                    (store_cli.main, ["review", "checks", "2"]), (board.main, []), (dispatch.main, ["runs"]),
                    (dispatch.main, ["chore-runs"]), (dispatch.main, ["stop", "agent", "1", "--dry-run"])]
        # `review checks` reads the project's test command from a folder with no config, not the live checkout's.
        with mock.patch.object(store, "DATABASE", old), mock.patch.object(store, "STATE", folder / "old"), \
                mock.patch.object(board, "web_address", lambda: "http://127.0.0.1:7480"), \
                mock.patch.object(store_cli, "checkout_of", lambda repo: str(folder)):
            for command, argv in commands:
                with self.subTest(argv=argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(command(argv), 0, err.getvalue())
                    self.assertEqual(version_of(old), store.SCHEMA_VERSION - 1)
                    self.assertEqual(old.read_bytes(), before)


class RunsTest(StoreCase):
    def test_a_run_goes_from_requested_to_running_to_its_end_with_an_event_at_each_step(self):
        run = self.store.request_run(REPO, "agent", "12", benchmark=True, env={"REPLY_PR": "14"}, path="/src/game")
        self.assertEqual(self.store.run(run)["status"], "requested")
        self.assertEqual(self.store.project_path(REPO), "/src/game")
        self.assertEqual(self.store.request_of(run)["payload"],
                         {"run": run, "kind": "agent", "target": "12", "benchmark": True, "env": {"REPLY_PR": "14"}})
        self.assertTrue(self.store.run_started(run, "agent-desk-agent-org-game-12-1.service"))
        self.assertFalse(self.store.run_started(run, "again.service"))
        self.assertTrue(self.store.run_ended(run, "failed", 3))
        self.assertFalse(self.store.run_ended(run, "lost"))
        row = self.store.run(run)
        self.assertEqual((row["status"], row["exit_code"], row["unit"]),
                         ("failed", 3, "agent-desk-agent-org-game-12-1.service"))
        self.assertEqual(self.events(), [(12, "run-requested"), (None, "run-started"), (None, "run-ended")])
        self.assertEqual(self.store.runs(("requested", "running")), [])

    def test_a_run_ends_only_in_a_known_way(self):
        run = self.store.request_run(REPO, "chore", "audit")
        with self.assertRaises(store.StoreError):
            self.store.run_ended(run, "exploded")
        self.assertIsNone(self.store.request_of(run + 1))


class ItemsTest(StoreCase):
    def test_issues_and_reviews_share_one_sequence_per_project(self):
        self.assertEqual(self.store.create(REPO, "issue", "First"), 1)
        self.assertEqual(self.store.create(REPO, "review", "Second", branch="agent/issue-1"), 2)
        self.assertEqual(self.store.create("org/other", "issue", "Theirs"), 1)

    def test_an_imported_number_is_kept_and_new_items_follow_the_highest(self):
        self.assertEqual(self.store.create(REPO, "issue", "Imported", number=123), 123)
        self.assertEqual(self.store.create(REPO, "issue", "Older", number=7), 7)
        self.assertEqual(self.store.create(REPO, "issue", "New"), 124)
        with self.assertRaisesRegex(store.StoreError, "already has #7"):
            self.store.create(REPO, "issue", "Again", number=7)

    def test_every_write_appends_its_events_in_the_same_transaction(self):
        number = self.store.create(REPO, "issue", "Tracked", labels=["serial:web"])
        last = self.store.last_event()
        self.assertEqual(self.events(), [(number, "item-created"), (number, "label-added")])
        self.store.comment(REPO, number, "A note")
        self.assertEqual(self.events(last), [(number, "comment-added")])
        self.assertGreater(self.store.last_event(), last)

    def test_the_comments_sent_to_the_agent_and_the_newest_answer_read_from_the_events(self):
        number = self.store.create(REPO, "issue", "Sent")
        first = self.store.comment(REPO, number, "Look again", to_agent=True)
        self.store.comment(REPO, number, "A note")
        second = self.store.comment(REPO, number, "And again", to_agent=True)
        sent = self.store.agent_comments()
        self.assertEqual([event["payload"]["comment"] for event in sent], [first, second])
        self.assertEqual([event["payload"]["comment"] for event in self.store.agent_comments(sent[0]["id"])],
                         [second])
        self.assertIsNone(self.store.last_answer())
        self.store.comment_answered(REPO, number, sent[1]["id"], "note", second)
        self.store.comment_answered(REPO, number, sent[0]["id"], "run", first, 4)
        self.assertEqual(self.store.last_answer(), sent[1]["id"])

    def test_a_comment_sent_to_the_agent_always_mentions_it(self):
        number = self.store.create(REPO, "issue", "Mentioned")
        bodies = {
            "Look again": "@agent Look again",
            "Please, @agent, look again": "Please, @agent, look again",
            # A mention inside a word or a path doesn't count, as lib/import_github.py reads one.
            "Mail me@agent.dev": "@agent Mail me@agent.dev",
            "See docs/@agent": "@agent See docs/@agent",
        }
        for body, stored in bodies.items():
            self.assertEqual(self.store.comment_of(self.store.comment(REPO, number, body, to_agent=True))["body"],
                             stored)
        plain = self.store.comment(REPO, number, "A note")
        self.assertEqual((self.store.comment_of(plain)["body"], self.store.comment_of(plain)["to_agent"]),
                         ("A note", 0))
        with self.assertRaisesRegex(store.StoreError, "needs a body"):
            self.store.comment(REPO, number, "  ", to_agent=True)

    def test_a_write_with_events_saves_the_newest_id_beside_the_database(self):
        saved = self.path.with_name(f"{self.path.name}-event")
        self.assertFalse(saved.exists())
        number = self.store.create(REPO, "issue", "Watched")
        self.assertEqual(saved.read_text(encoding="utf-8"), f"{self.store.last_event()}\n")
        self.store.comment(REPO, number, "A note")
        self.assertEqual(saved.read_text(encoding="utf-8"), f"{self.store.last_event()}\n")
        # A write that adds no event leaves the file alone, and a failed save leaves no temporary file behind.
        saved.write_text("stale\n", encoding="utf-8")
        self.store.edit(REPO, number, title="Watched")
        self.assertEqual(saved.read_text(encoding="utf-8"), "stale\n")
        saved.unlink()
        saved.mkdir()
        self.store.comment(REPO, number, "Another note")
        self.assertEqual(sorted(one.name for one in self.path.parent.iterdir() if one.name.startswith(saved.name)),
                         [saved.name, f"{saved.name}.lock"])

    def test_a_save_that_read_an_older_id_never_replaces_a_newer_one(self):
        saved = self.path.with_name(f"{self.path.name}-event")
        number = self.store.create(REPO, "issue", "Raced")
        replace, sources, racer = os.replace, [], []

        def write_newer():
            other = store.Store(self.path)
            try:
                other.comment(REPO, number, "Newer")
            finally:
                other.close()

        def replacing(source, target):
            # The first save to reach its rename lets another thread of this process write and save a newer id, and
            # gives it time to finish before renaming its own.
            sources.append(Path(source).name)
            if not racer:
                racer.append(threading.Thread(target=write_newer))
                racer[0].start()
                racer[0].join(0.3)
            replace(source, target)

        with mock.patch.object(store.os, "replace", side_effect=replacing):
            self.store.comment(REPO, number, "Older")
            racer[0].join()
        self.assertEqual(saved.read_text(encoding="utf-8"), f"{self.store.last_event()}\n")
        self.assertEqual(len(set(sources)), 2)

    def test_a_write_that_changes_nothing_adds_no_event(self):
        number = self.store.create(REPO, "issue", "Quiet", labels=["idea"])
        last = self.store.last_event()
        self.store.edit(REPO, number, title="Quiet", add_labels=["idea"], remove_labels=["benchmark"], status="open")
        self.assertEqual(self.events(last), [])

    def test_a_state_label_moves_the_state_and_stays_out_of_the_labels(self):
        number = self.store.create(REPO, "issue", "Ready", labels=["agent-ready", "serial:web"])
        shaped = self.store.item(REPO, number)
        self.assertEqual((shaped["status"], shaped["state"]), ("queued", "OPEN"))
        self.assertEqual(shaped["labels"], [{"name": "serial:web"}])
        self.store.edit(REPO, number, remove_labels=["agent-ready"], add_labels=["in-progress"])
        self.assertEqual(self.store.item(REPO, number)["status"], "in-progress")
        self.store.edit(REPO, number, remove_labels=["waiting"])
        self.assertEqual(self.store.item(REPO, number)["status"], "in-progress")
        self.store.edit(REPO, number, remove_labels=["in-progress"])
        self.assertEqual(self.store.item(REPO, number)["status"], "open")

    def test_closing_and_reopening_keep_the_reason_and_the_time(self):
        number = self.store.create(REPO, "issue", "Done")
        self.store.set_state(REPO, number, "closed", "not-planned")
        shaped = self.store.item(REPO, number)
        self.assertEqual((shaped["state"], shaped["stateReason"]), ("CLOSED", "not-planned"))
        self.assertIsNotNone(shaped["closedAt"])
        self.store.set_state(REPO, number, "open")
        shaped = self.store.item(REPO, number)
        self.assertEqual((shaped["state"], shaped["stateReason"], shaped["closedAt"]), ("OPEN", None, None))

    def test_state_before_names_the_state_an_item_left_when_it_last_moved_to_one(self):
        number = self.store.create(REPO, "issue", "Waits")
        self.assertIsNone(self.store.state_before(REPO, number, "waiting"))
        for state in ("queued", "waiting", "in-progress", "waiting"):
            self.store.set_state(REPO, number, state)
        self.assertEqual(self.store.state_before(REPO, number, "waiting"), "in-progress")
        self.assertEqual(self.store.state_before(REPO, number, "queued"), "open")
        self.store.create("org/other", "issue", "Elsewhere")
        self.assertEqual(self.store.projects(), [REPO, "org/other"])

    def test_an_unknown_state_or_item_is_refused(self):
        number = self.store.create(REPO, "issue", "Here")
        with self.assertRaisesRegex(store.StoreError, "isn't a state"):
            self.store.set_state(REPO, number, "done")
        with self.assertRaisesRegex(store.StoreError, "no item #99"):
            self.store.comment(REPO, 99, "Lost")
        with self.assertRaisesRegex(store.StoreError, "no review #1"):
            self.store.item(REPO, number, "review")

    def test_links_show_from_both_ends(self):
        parent = self.store.create(REPO, "issue", "Parent")
        child = self.store.create(REPO, "issue", "Child")
        blocker = self.store.create(REPO, "issue", "Blocker")
        self.store.link(REPO, child, parent, "parent")
        self.store.link(REPO, blocker, child, "blocks")
        shaped = self.store.item(REPO, child)
        self.assertEqual(shaped["parent"]["number"], parent)
        self.assertEqual([one["number"] for one in shaped["blockedBy"]], [blocker])
        self.assertEqual([one["number"] for one in self.store.item(REPO, parent)["subIssues"]], [child])
        self.assertEqual([one["number"] for one in self.store.item(REPO, blocker)["blocking"]], [child])
        self.store.set_state(REPO, blocker, "closed")
        self.assertEqual(self.store.item(REPO, child)["blockedBy"][0]["state"], "CLOSED")
        self.store.link(REPO, blocker, child, "blocks", add=False)
        self.assertEqual(self.store.item(REPO, child)["blockedBy"], [])

    def test_a_sub_issue_has_one_parent(self):
        first, second, child = (self.store.create(REPO, "issue", title) for title in ("One", "Two", "Child"))
        self.store.link(REPO, child, first, "parent")
        self.store.link(REPO, child, second, "parent")
        self.assertEqual(self.store.item(REPO, child)["parent"]["number"], second)
        self.assertEqual(self.store.item(REPO, first)["subIssues"], [])

    def test_the_author_comes_from_the_environment(self):
        number = self.store.create(REPO, "issue", "Mine")
        with mock.patch.dict(os.environ, {"AGENT_DESK_RUN": "41", "AGENT_DESK_AUTHOR": "desk"}):
            self.store.comment(REPO, number, "From a run")
        with mock.patch.dict(os.environ, {"AGENT_DESK_AUTHOR": "desk"}):
            self.store.comment(REPO, number, "From a script")
        shaped = self.store.item(REPO, number)
        self.assertEqual(shaped["author"], {"login": "you"})
        self.assertEqual([one["author"]["login"] for one in shaped["comments"]], ["agent", "desk"])
        self.assertEqual([event["author"] for event in self.store.events_since()], ["you", "agent", "desk"])

    def test_list_filters_by_state_label_status_and_text(self):
        one = self.store.create(REPO, "issue", "Fix the pane", labels=["serial:board"])
        two = self.store.create(REPO, "issue", "Fix the page", body="100% of rows", labels=["agent-ready"])
        three = self.store.create(REPO, "issue", "Closed one")
        self.store.set_state(REPO, three, "closed")
        self.store.create(REPO, "review", "A review", branch="agent/issue-1")

        def numbers(**filters):
            return [item["number"] for item in self.store.items(REPO, "issue", **filters)]

        self.assertEqual(numbers(), [two, one])
        self.assertEqual(numbers(state="closed"), [three])
        self.assertEqual(numbers(state="all"), [three, two, one])
        self.assertEqual(numbers(labels=["serial:board"]), [one])
        self.assertEqual(numbers(labels=["agent-ready"]), [two])
        self.assertEqual(numbers(status="queued"), [two])
        self.assertEqual(numbers(search="pane"), [one])
        self.assertEqual(numbers(search="100%"), [two])
        self.assertEqual(numbers(search="1_0"), [])
        self.assertEqual(numbers(limit=1), [two])


class ReviewsTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.issue = self.store.create(REPO, "issue", "Add a store", labels=["in-progress"])
        self.review = self.store.create(REPO, "review", "Add the store", branch="agent/issue-1", head_sha="abc",
                                        draft=True)
        self.store.link(REPO, self.review, self.issue, "fixes")

    def test_a_review_prints_the_fields_gh_names(self):
        shaped = self.store.item(REPO, self.review, "review")
        self.assertEqual((shaped["headRefName"], shaped["baseRefName"], shaped["headRefOid"], shaped["isDraft"]),
                         ("agent/issue-1", "main", "abc", True))
        self.assertIsNone(shaped["mergeCommit"])
        self.assertEqual([one["number"] for one in shaped["closingIssuesReferences"]], [self.issue])

    def test_ready_and_a_new_head_each_add_one_event(self):
        last = self.store.last_event()
        self.store.review_update(REPO, self.review, draft=False)
        self.store.review_update(REPO, self.review, draft=False)
        self.store.review_update(REPO, self.review, head_sha="def")
        self.store.review_update(REPO, self.review, head_sha="def")
        self.assertEqual(self.events(last), [(self.review, "review-ready"), (self.review, "review-head-moved")])
        shaped = self.store.item(REPO, self.review)
        self.assertEqual((shaped["isDraft"], shaped["headRefOid"]), (False, "def"))

    def test_a_merge_into_main_closes_the_review_and_the_issues_it_fixes(self):
        self.store.merge(REPO, self.review, "f00d")
        review = self.store.item(REPO, self.review)
        self.assertEqual((review["state"], review["mergeCommit"], review["isDraft"]),
                         ("MERGED", {"oid": "f00d"}, False))
        issue = self.store.item(REPO, self.issue)
        self.assertEqual((issue["state"], issue["stateReason"]), ("CLOSED", "completed"))
        with self.assertRaisesRegex(store.StoreError, "already merged"):
            self.store.merge(REPO, self.review, "beef")
        with self.assertRaisesRegex(store.StoreError, "stays closed"):
            self.store.set_state(REPO, self.review, "open")

    def test_a_merge_into_another_base_leaves_its_issues_open(self):
        review = self.store.create(REPO, "review", "On a branch", branch="agent/issue-9", base="local-desk")
        self.store.link(REPO, review, self.issue, "fixes")
        self.store.merge(REPO, review, "f00d")
        self.assertEqual(self.store.item(REPO, self.issue)["state"], "OPEN")

    def test_a_branch_names_its_newest_open_review(self):
        self.assertEqual(self.store.review_number(REPO, "agent/issue-1"), self.review)
        self.store.set_state(REPO, self.review, "closed")
        again = self.store.create(REPO, "review", "Again", branch="agent/issue-1")
        self.assertEqual(self.store.review_number(REPO, "agent/issue-1"), again)
        self.assertIsNone(self.store.review_number(REPO, "agent/none"))

    def test_list_tells_merged_from_closed_and_filters_by_head(self):
        other = self.store.create(REPO, "review", "Other", branch="agent/other")
        self.store.merge(REPO, self.review, "f00d")
        self.store.set_state(REPO, other, "closed")

        def numbers(**filters):
            return [item["number"] for item in self.store.items(REPO, "review", **filters)]

        self.assertEqual(numbers(state="merged"), [self.review])
        self.assertEqual(numbers(state="closed"), [other])
        self.assertEqual(numbers(state="all", head="agent/other"), [other])
        self.assertEqual(numbers(), [])

    def test_a_line_comment_keeps_its_place(self):
        self.store.comment(REPO, self.review, "Rename this", to_agent=True, path="lib/store.py", line=12,
                           head_sha="abc")
        comment = self.store.item(REPO, self.review)["comments"][0]
        self.assertEqual((comment["path"], comment["line"], comment["headRefOid"], comment["toAgent"]),
                         ("lib/store.py", 12, "abc", True))

    def test_a_line_comment_without_a_head_is_made_on_the_reviews_newest_head(self):
        self.store.review_update(REPO, self.review, head_sha="c0ffee")
        self.store.comment(REPO, self.review, "Older head", path="a.py", line=3)
        self.store.branch_moved(REPO, "agent/issue-1", "beef", "c0ffee")
        self.store.comment(REPO, self.review, "Newer head", path="a.py", line=4)
        self.store.comment(REPO, self.review, "Plain")
        comments = self.store.item(REPO, self.review)["comments"]
        self.assertEqual([one["headRefOid"] for one in comments], ["c0ffee", "beef", None])

    def test_a_line_comment_needs_a_file_and_a_line_on_a_review(self):
        for path, line in (("a.py", None), (None, 3), ("a.py", 0)):
            with self.assertRaises(store.StoreError):
                self.store.comment(REPO, self.review, "Here", path=path, line=line)
        issue = self.store.create(REPO, "issue", "An issue")
        with self.assertRaises(store.StoreError):
            self.store.comment(REPO, issue, "Here", path="a.py", line=1)
        self.assertEqual(self.store.item(REPO, self.review)["comments"], [])

    def test_a_comment_sent_to_the_agent_marks_the_line_comments_it_quotes_and_sends_none_twice(self):
        first = self.store.comment(REPO, self.review, "Rename this", path="a.py", line=3)
        second = self.store.comment(REPO, self.review, "And this", path="a.py", line=9)
        # A comment sent to the agent that quotes nothing, such as an @agent note, leaves the line comments unsent.
        self.store.comment(REPO, self.review, "@agent look", to_agent=True)
        sent = self.store.comment(REPO, self.review, "Please fix", to_agent=True, quotes=[first])

        def sent_in():
            return [row[0] for row in self.store.db.execute(
                "SELECT sent_in FROM comments WHERE path IS NOT NULL ORDER BY id")]

        self.assertEqual(sent_in(), [sent, None])
        last = self.store.last_event()
        for quotes, why in (([first], "already sent"), ([second, sent], "not a line comment"), ([999], "no comment")):
            with self.assertRaisesRegex(store.StoreError, "waiting to be sent", msg=why):
                self.store.comment(REPO, self.review, "Again", to_agent=True, quotes=quotes)
        # A refused send writes nothing, so its line comments stay unsent.
        self.assertEqual((sent_in(), self.store.last_event()), ([sent, None], last))
        other = self.store.create(REPO, "review", "Other", branch="agent/other")
        with self.assertRaisesRegex(store.StoreError, "waiting to be sent"):
            self.store.comment(REPO, other, "Not yours", to_agent=True, quotes=[second])
        with self.assertRaisesRegex(store.StoreError, "--to-agent"):
            self.store.comment(REPO, self.review, "Quietly", quotes=[second])
        status, _, err = self.cli("review", "comment", str(self.review), "--body", "Go", "--to-agent", "--quotes",
                                  str(second))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(sent_in()[1], self.store.item(REPO, self.review)["comments"][-1]["id"])

    def test_a_comment_reads_back_by_its_id(self):
        made = self.store.comment(REPO, self.review, "Send it", to_agent=True)
        found = self.store.comment_of(made)
        self.assertEqual((found["project"], found["number"], found["body"], found["to_agent"]),
                         (REPO, self.review, "@agent Send it", 1))
        self.assertIsNone(self.store.comment_of(made + 1))


class RepoHereTest(StoreCase):
    def test_the_repo_comes_from_the_origin_when_nothing_names_it(self):
        for url in ("git@github.com:org/game.git", "https://github.com/org/game", "ssh://git@gitlab.com/org/game.git",
                    "https://gitlab.example.com/org/game.git/"):
            with mock.patch.dict(os.environ, {"GH_REPO": ""}), mock.patch.object(store, "git", return_value=url):
                self.assertEqual(store.repo_here(), "org/game", url)
        with mock.patch.dict(os.environ, {"GH_REPO": ""}), mock.patch.object(store, "git", return_value=None):
            with self.assertRaisesRegex(store.StoreError, "No project here"):
                store.repo_here()

    def checkout(self, name, origin=None):
        """A new checkout with one commit, whose origin is the path given, or which has no remote."""
        path = Path(self.folder.name) / name
        quiet = {"check": True, "capture_output": True}
        subprocess.run(["git", "init", "-q", "-b", "main", str(path)], **quiet)
        subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
                        "--allow-empty", "-m", "first"], **quiet)
        if origin:
            subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], **quiet)
        return path.resolve()

    def repo_in(self, folder):
        before = os.getcwd()
        os.chdir(folder)
        try:
            with mock.patch.dict(os.environ, {"GH_REPO": ""}):
                return store.repo_here()
        finally:
            os.chdir(before)

    def test_the_projects_file_names_a_checkout_whose_origin_is_a_path_or_missing(self):
        local = self.checkout("local", origin="/srv/git/local.git")
        alone = self.checkout("alone")
        store.PROJECTS.write_text(f"org/other /elsewhere\norg/local {local}\norg/alone {alone}\n")
        self.assertEqual(self.repo_in(local), "org/local")
        self.assertEqual(self.repo_in(alone), "org/alone")
        self.assertEqual(self.repo_in(local / "."), "org/local")

    def test_a_worktree_takes_the_name_its_main_checkout_has_in_the_projects_file(self):
        main = self.checkout("local", origin="/srv/git/local.git")
        worktree = Path(self.folder.name).resolve() / "local-issue-1"
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "-b", "agent/issue-1", str(worktree)],
                       check=True, capture_output=True)
        (worktree / "lib").mkdir()
        store.PROJECTS.write_text(f"org/local {main}\n")
        self.assertEqual(self.repo_in(worktree), "org/local")
        self.assertEqual(self.repo_in(worktree / "lib"), "org/local")

    def test_a_host_origin_wins_over_the_projects_file_and_gh_repo_over_both(self):
        main = self.checkout("game", origin="git@github.com:org/game.git")
        self.assertEqual(self.repo_in(main), "org/game")
        # A project renamed on GitHub keeps its old line until install runs again, and follows its origin meanwhile.
        store.PROJECTS.write_text(f"org/old-name {main}\n")
        self.assertEqual(self.repo_in(main), "org/game")
        before = os.getcwd()
        os.chdir(main)
        self.addCleanup(os.chdir, before)
        with mock.patch.dict(os.environ, {"GH_REPO": "org/named"}):
            self.assertEqual(store.repo_here(), "org/named")

    def test_a_checkout_the_projects_file_lacks_needs_a_host_origin(self):
        for origin in ("/srv/git/local.git", None):
            folder = self.checkout(f"unlisted-{bool(origin)}", origin=origin)
            with self.assertRaisesRegex(store.StoreError, "No project here"):
                self.repo_in(folder)


class BoardTest(StoreCase):
    def test_the_board_lists_open_issues_with_their_states_labels_blockers_and_sub_issues(self):
        blocker = self.store.create(REPO, "issue", "Blocker")
        blocked = self.store.create(REPO, "issue", "Blocked", labels=["waiting", "serial:web"])
        self.store.link(REPO, blocker, blocked, "blocks")
        done = self.store.create(REPO, "issue", "Done")
        self.store.link(REPO, done, blocked, "blocks")
        self.store.set_state(REPO, done, "closed", "completed")
        parent = self.store.create(REPO, "issue", "Parent", labels=["on-hold", "idea"])
        for title in ("First", "Second"):
            self.store.link(REPO, self.store.create(REPO, "issue", title), parent, "parent")
        self.store.set_state(REPO, parent + 1, "closed", "completed")
        board = self.store.board(REPO)
        self.assertEqual(board["repo"], REPO)
        issues = {one["number"]: one for one in board["issues"]}
        self.assertEqual(sorted(issues), [blocker, blocked, parent, parent + 2])
        self.assertEqual((issues[blocked]["status"], issues[blocked]["labels"]), ("waiting", ["serial:web"]))
        self.assertEqual(issues[blocked]["blockedBy"], [{"number": blocker, "state": "OPEN"},
                                                        {"number": done, "state": "CLOSED"}])
        self.assertEqual((issues[parent]["status"], issues[parent]["labels"]), ("on-hold", ["idea"]))
        self.assertEqual(issues[parent]["subIssues"], {"total": 2, "completed": 1})
        self.assertNotIn("subIssues", issues[blocker])
        self.assertNotIn("comments", issues[blocker])

    def test_an_issue_in_progress_carries_its_comments_and_where_its_branch_merged(self):
        working = self.store.create(REPO, "issue", "Stacked", labels=["in-progress"])
        fresh = self.store.create(REPO, "issue", "Fresh", labels=["in-progress"])
        queued = self.store.create(REPO, "issue", "Queued", labels=["agent-ready"])
        self.store.comment(REPO, working, "An agent started on `agent/issue-1`.")
        self.store.comment(REPO, queued, "Not read for the board")
        base = self.store.create(REPO, "review", "The base", branch="agent/issue-40")
        stacked = self.store.create(REPO, "review", "Stacked", branch=f"agent/issue-{working}", base="agent/issue-40")
        self.store.merge(REPO, stacked, "f00d")
        issues = {one["number"]: one for one in self.store.board(REPO)["issues"]}
        self.assertEqual([one["body"] for one in issues[working]["comments"]], ["An agent started on `agent/issue-1`."])
        self.assertEqual(issues[working]["landed"], {"base": "agent/issue-40",
                                                     "basePr": {"number": base, "state": "OPEN"}})
        self.assertEqual((issues[fresh]["comments"], issues[fresh]["landed"]), ([], None))
        self.assertNotIn("comments", issues[queued])

        self.store.merge(REPO, base, "beef")
        landed = {one["number"]: one for one in self.store.board(REPO)["issues"]}[working]["landed"]
        self.assertEqual(landed["basePr"], {"number": base, "state": "MERGED"})

    def test_a_review_carries_its_heads_newest_check_and_whether_it_merges_with_main(self):
        review = self.store.create(REPO, "review", "Checked", branch="agent/issue-9", head_sha="abc")
        conflicting = self.store.create(REPO, "review", "Conflicts", branch="agent/other", head_sha="c1")

        def shown():
            reviews = {one["number"]: one for one in self.store.board(REPO)["reviews"]}
            return reviews[review]["head"], reviews[review]["checks"], reviews[review]["mergeable"]

        self.assertEqual(shown(), ("abc", [], "UNKNOWN"))
        old = self.store.check_queue(REPO, "agent/issue-9", "abc")
        self.store.check_ended(old, "passed")
        self.assertEqual([(one["status"], one["attempt"]) for one in shown()[1]], [("passed", 1)])
        # The branch moved on, so the check of its old head no longer counts.
        self.store.branch_moved(REPO, "agent/issue-9", "def", "abc")
        self.assertEqual(shown(), ("def", [], "UNKNOWN"))
        first = self.store.check_queue(REPO, "agent/issue-9", "def")
        self.assertEqual(shown()[1][0]["status"], "queued")
        # A failed first attempt queues a second, and the failure shows while the second waits and runs.
        self.store.check_started(first, "/logs/first.log")
        self.store.check_ended(first, "failed", rerun=True)
        [second] = self.store.checks(("queued",), REPO, "agent/issue-9")
        for step in ("queued", "running"):
            if step == "running":
                self.store.check_started(second["id"], "/logs/second.log")
            head, checks, mergeable = shown()
            self.assertEqual(([(one["name"], one["status"], one["attempt"], one["log"]) for one in checks], mergeable),
                             ([("test", "failed", 1, "/logs/first.log")], "MERGEABLE"))
        self.store.check_ended(second["id"], "passed")
        self.assertEqual([(one["status"], one["attempt"]) for one in shown()[1]], [("passed", 2)])
        self.assertEqual(shown()[2], "MERGEABLE")

        merge = self.store.check_queue(REPO, "agent/other", "c1")
        self.store.check_started(merge, "/logs/1.log")
        self.store.check_ended(merge, "failed", name="merge")
        reviews = {one["number"]: one for one in self.store.board(REPO)["reviews"]}
        self.assertEqual((reviews[conflicting]["mergeable"], reviews[conflicting]["checks"][0]["log"]),
                         ("CONFLICTING", "/logs/1.log"))
        status, out, _ = self.cli("review", "view", str(conflicting), "--json", "mergeable,checks")
        self.assertEqual((status, json.loads(out)["mergeable"]), (0, "CONFLICTING"))

    def test_a_tried_merge_says_whether_the_head_merges_with_its_base_until_the_branch_moves(self):
        review = self.store.create(REPO, "review", "Tried", branch="agent/issue-9", base="local-desk", head_sha="abc")

        def shown():
            return {one["number"]: one for one in self.store.board(REPO)["reviews"]}[review]["mergeable"]

        self.store.branch_moved(REPO, "agent/issue-9", "def", "abc")
        self.assertEqual(self.store.branch_heads(REPO), {"agent/issue-9": "def"})
        self.assertEqual(self.store.merge_targets(REPO), [{"branch": "agent/issue-9", "review_base": "local-desk",
                                                           "head_sha": "def", "base": None, "base_sha": None,
                                                           "mergeable": None}])
        # A try of a head the branch left records nothing.
        self.assertFalse(self.store.merge_tried(REPO, "agent/issue-9", "abc", "local-desk", "b1", "CONFLICTING"))
        self.assertEqual(shown(), "UNKNOWN")
        # Without any check, the try alone says the head conflicts.
        last = self.store.last_event()
        self.assertTrue(self.store.merge_tried(REPO, "agent/issue-9", "def", "local-desk", "b1", "CONFLICTING"))
        self.assertEqual(shown(), "CONFLICTING")
        self.assertEqual(self.store.item(REPO, review, "review")["mergeable"], "CONFLICTING")
        # The base moved and the answer stayed, so no event tells the board to read again.
        self.store.merge_tried(REPO, "agent/issue-9", "def", "local-desk", "b2", "CONFLICTING")
        self.assertEqual(self.events(last), [(None, "branch-merges")])
        self.assertEqual(self.store.branch(REPO, "agent/issue-9")["base_sha"], "b2")
        # A try against another base, as before the review's base changed, doesn't count.
        self.store.merge_tried(REPO, "agent/issue-9", "def", "main", "m1", "MERGEABLE")
        self.assertEqual(shown(), "UNKNOWN")
        # A new head waits for its own try; a deleted branch leaves `branches`.
        self.store.branch_moved(REPO, "agent/issue-9", "ghi", "def")
        self.assertIsNone(self.store.branch(REPO, "agent/issue-9")["mergeable"])
        self.store.branch_moved(REPO, "agent/issue-9", None, "ghi")
        self.assertEqual((self.store.branch_heads(REPO), self.store.branch(REPO, "agent/issue-9")), ({}, None))

    def test_the_migration_takes_each_branchs_newest_head_from_its_events(self):
        # A database at schema 3, before `branches`, holding the moves the migration reads.
        self.store.close()
        older = sqlite3.connect(Path(self.folder.name) / "older.sqlite")
        for statement in [one for migration in store.MIGRATIONS[:3] for one in migration]:
            older.execute(statement)
        for branch, head in (("agent/a", "a1"), ("agent/b", "b1"), ("agent/a", "a2"), ("agent/b", None)):
            older.execute("INSERT INTO events (project, type, payload, at) VALUES (?, 'branch-moved', ?, 'now')",
                          (REPO, json.dumps({"branch": branch, "head": head, "before": None})))
        older.execute("PRAGMA user_version = 3")
        older.commit()
        older.close()
        self.store = store.Store(Path(self.folder.name) / "older.sqlite")
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.branch_heads(REPO), {"agent/a": "a2"})

    def test_the_migration_marks_line_comments_sent_by_a_send_that_quoted_line_comments_or_on_the_host(self):
        # A database at schema 7, before `sent_in`, where an @agent note came after one line comment and a send that
        # quoted line comments after another, and a third came from the host.
        self.store.close()
        older = sqlite3.connect(Path(self.folder.name) / "older.sqlite")
        for statement in [one for migration in store.MIGRATIONS[:7] for one in migration]:
            older.execute(statement)
        rows = [("Rename this", "a.py", 0, None), ("@agent look", None, 1, None), ("And this", "a.py", 0, None),
                ("Go\n\n**Line comments**\n\n- `a.py` line 3", None, 1, None), ("Later", "a.py", 0, None),
                ("From the host", "a.py", 0, "PRRC_1")]
        for body, path, to_agent, remote in rows:
            older.execute("INSERT INTO comments (project, number, body, path, line, to_agent, created_at, remote_id)"
                          " VALUES (?, 2, ?, ?, ?, ?, 'now', ?)", (REPO, body, path, 3 if path else None, to_agent,
                                                                    remote))
        older.execute("PRAGMA user_version = 7")
        older.commit()
        older.close()
        self.store = store.Store(Path(self.folder.name) / "older.sqlite")
        self.addCleanup(self.store.close)
        found = self.store.db.execute("SELECT body, sent_in FROM comments WHERE path IS NOT NULL ORDER BY id")
        self.assertEqual([tuple(row) for row in found],
                         [("Rename this", 4), ("And this", 4), ("Later", None), ("From the host", 0)])

    def test_the_board_lists_the_three_newest_merges_and_the_live_runs(self):
        merged = []
        for index in range(4):
            number = self.store.create(REPO, "review", f"Merged {index}", branch=f"agent/issue-{index + 20}")
            self.store.merge(REPO, number, "f00d")
            self.store.db.execute("UPDATE items SET closed_at = ? WHERE project = ? AND number = ?",
                                  (f"2026-10-29T0{index}:00:00Z", REPO, number))
            merged.append(number)
        run = self.store.request_run(REPO, "agent", "7")
        board = self.store.board(REPO)
        self.assertEqual([(one["number"], one["mergedAt"]) for one in board["merged"]],
                         [(merged[3], "2026-10-29T03:00:00Z"), (merged[2], "2026-10-29T02:00:00Z"),
                          (merged[1], "2026-10-29T01:00:00Z")])
        self.assertEqual(board["reviews"], [])
        self.assertEqual([(one["id"], one["status"]) for one in board["runs"]], [(run, "requested")])


class CommentEditTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.issue = self.store.create(REPO, "issue", "Paint the fence")

    def test_an_edit_replaces_the_body_marks_it_edited_and_touches_the_item_in_one_write(self):
        made = self.store.comment(REPO, self.issue, "Use blue")
        self.store.db.execute("UPDATE items SET updated_at = 'then' WHERE number = ?", (self.issue,))
        last = self.store.last_event()
        with mock.patch.object(store, "now", return_value="2026-11-14T20:00:00Z"):
            self.store.edit_comment(REPO, made, "Use green")
        [shaped] = self.store.item(REPO, self.issue)["comments"]
        self.assertEqual((shaped["id"], shaped["body"], shaped["lastEditedAt"]), (made, "Use green",
                                                                                   "2026-11-14T20:00:00Z"))
        self.assertEqual(self.store.item(REPO, self.issue)["updatedAt"], "2026-11-14T20:00:00Z")
        [event] = self.store.events_since(last)
        self.assertEqual((event["number"], event["type"], event["payload"], event["author"]),
                         (self.issue, "comment-edited", {"comment": made}, "you"))
        # The same body again writes nothing.
        last = self.store.last_event()
        self.store.edit_comment(REPO, made, "Use green")
        self.assertEqual(self.store.last_event(), last)

    def test_a_comment_never_edited_has_no_edit_time(self):
        self.store.comment(REPO, self.issue, "Use blue")
        self.assertIsNone(self.store.item(REPO, self.issue)["comments"][0]["lastEditedAt"])

    def test_an_edit_refuses_another_writers_comment_one_sent_to_the_agent_an_empty_body_and_an_unknown_id(self):
        with mock.patch.dict(os.environ, {"AGENT_DESK_RUN": "9"}):
            by_agent = self.store.comment(REPO, self.issue, "Done")
        sent = self.store.comment(REPO, self.issue, "Look again", to_agent=True)
        mine = self.store.comment(REPO, self.issue, "A note")
        review = self.store.create(REPO, "review", "Paint it", branch="agent/issue-1")
        line = self.store.comment(REPO, review, "Rename this", path="a.py", line=3)
        self.store.comment(REPO, review, "Go", to_agent=True, quotes=[line])
        elsewhere = self.store.comment("org/other", self.store.create("org/other", "issue", "Other"), "Theirs")
        last = self.store.last_event()
        refusals = [(by_agent, "Changed", "is by agent, and only its writer edits it"),
                    (sent, "Changed", "went to the agent"), (line, "Changed", "went to the agent"),
                    (mine, "  \n", "needs a body"), (9999, "Changed", "has no comment 9999"),
                    (elsewhere, "Changed", f"{REPO} has no comment {elsewhere}")]
        for comment, body, reason in refusals:
            with self.subTest(reason=reason), self.assertRaisesRegex(store.StoreError, reason):
                self.store.edit_comment(REPO, comment, body)
        # An agent edits its own comment, saying what it changed, and not yours.
        with mock.patch.dict(os.environ, {"AGENT_DESK_RUN": "9"}):
            for note in (None, " \n "):
                with self.subTest(note=note), self.assertRaisesRegex(store.StoreError, "An edit by agent says what"):
                    self.store.edit_comment(REPO, by_agent, "Done, and tested", note=note)
            self.store.edit_comment(REPO, by_agent, "Done, and tested", note="Added the tests")
            with self.assertRaisesRegex(store.StoreError, "is by you"):
                self.store.edit_comment(REPO, mine, "Changed")
        self.assertEqual(self.events(last), [(self.issue, "comment-edited")])

    def test_an_edit_note_goes_above_the_closing_lines_agent_desk_signs_a_comment_with(self):
        signed = "## Decisions Needed\n\n### 1. Blue?\n\nText\n\n<sub>Posted by agent-desk on r1</sub>\n<!-- agent-desk -->\n"
        with mock.patch.dict(os.environ, {"AGENT_DESK_RUN": "9"}):
            made = self.store.comment(REPO, self.issue, signed)
            with mock.patch.object(store, "now", return_value="2026-11-14T20:57:09Z"):
                self.store.edit_comment(REPO, made, "Text\n\n<sub>Posted by agent-desk on r1</sub>\n<!-- agent-desk -->\n",
                                        note="Removed decision 1,\n  answered 1A.")
        self.assertEqual(self.store.item(REPO, self.issue)["comments"][0]["body"],
                         "Text\n\n**Edited 2026-11-14 20:57 UTC:** Removed decision 1, answered 1A.\n\n"
                         "<sub>Posted by agent-desk on r1</sub>\n<!-- agent-desk -->\n")
        # Your edit needs no note, and a note you give goes at the end of an unsigned comment.
        mine = self.store.comment(REPO, self.issue, "Use blue")
        self.store.edit_comment(REPO, mine, "Use green")
        self.assertEqual(self.store.item(REPO, self.issue)["comments"][1]["body"], "Use green")
        with mock.patch.object(store, "now", return_value="2026-11-14T21:00:00Z"):
            self.store.edit_comment(REPO, mine, "Use red\n", note="Red, not green")
        self.assertEqual(self.store.item(REPO, self.issue)["comments"][1]["body"],
                         "Use red\n\n**Edited 2026-11-14 21:00 UTC:** Red, not green\n")

    def test_an_unsent_line_comment_takes_an_edit(self):
        review = self.store.create(REPO, "review", "Paint it", branch="agent/issue-1")
        line = self.store.comment(REPO, review, "Rename this", path="a.py", line=3)
        self.store.edit_comment(REPO, line, "Rename this function")
        self.assertEqual(self.store.item(REPO, review)["comments"][0]["body"], "Rename this function")

    def test_the_migration_adds_an_empty_edit_time_to_each_comment(self):
        # A database at the schema before `edited_at`, holding a comment.
        self.store.close()
        older = sqlite3.connect(Path(self.folder.name) / "older.sqlite")
        before = 8
        for statement in [one for migration in store.MIGRATIONS[:before] for one in migration]:
            older.execute(statement)
        older.execute("INSERT INTO items (project, number, kind, title, created_at, updated_at)"
                      " VALUES (?, 1, 'issue', 'Old', 'then', 'then')", (REPO,))
        older.execute("INSERT INTO comments (project, number, author, body, created_at) VALUES (?, 1, 'you', 'Hi',"
                      " 'then')", (REPO,))
        older.execute(f"PRAGMA user_version = {before}")
        older.commit()
        older.close()
        self.store = store.Store(Path(self.folder.name) / "older.sqlite")
        self.addCleanup(self.store.close)
        [comment] = self.store.item(REPO, 1)["comments"]
        self.assertEqual((comment["body"], comment["lastEditedAt"]), ("Hi", None))
        self.store.edit_comment(REPO, comment["id"], "Hello")
        self.assertIsNotNone(self.store.item(REPO, 1)["comments"][0]["lastEditedAt"])


class StartOrderTest(StoreCase):
    """Each project's queued and waiting issues keep one start order."""

    def order(self, project=REPO):
        return [one["number"] for one in self.store.order(project)]

    def test_an_issue_joins_the_end_keeps_its_place_between_queued_and_waiting_and_leaves_on_any_other_state(self):
        for title in ("One", "Two", "Three", "Four"):
            self.store.create(REPO, "issue", title)
        self.store.edit(REPO, 3, add_labels=["agent-ready"])
        self.store.set_state(REPO, 1, "waiting")
        self.store.edit(REPO, 2, add_labels=["agent-ready"])
        self.assertEqual(self.order(), [3, 1, 2])
        # Moving between queued and waiting keeps the place.
        self.store.set_state(REPO, 3, "waiting")
        self.store.set_state(REPO, 1, "queued")
        self.assertEqual(self.order(), [3, 1, 2])
        self.assertEqual([self.store.item(REPO, number)["position"] for number in (1, 2, 3, 4)], [2, 3, 1, None])
        # Starting, closing, and going back to open each leave the order.
        self.store.set_state(REPO, 3, "in-progress")
        self.store.set_state(REPO, 1, "closed", "completed")
        self.store.edit(REPO, 2, remove_labels=["agent-ready"])
        self.assertEqual(self.order(), [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM items WHERE rank IS NOT NULL").fetchone()[0], 0)
        # Queued again, an issue joins the end.
        self.store.edit(REPO, 4, add_labels=["agent-ready"])
        self.store.edit(REPO, 3, status="queued")
        self.store.edit(REPO, 2, add_labels=["agent-ready"])
        self.assertEqual(self.order(), [4, 3, 2])

    def test_each_project_keeps_its_own_order(self):
        self.store.create(REPO, "issue", "Here", labels=["agent-ready"])
        self.store.create("org/other", "issue", "There", labels=["agent-ready"])
        self.store.create(REPO, "issue", "Here too", labels=["agent-ready"])
        self.assertEqual((self.order(), self.order("org/other")), ([1, 2], [1]))
        self.assertEqual(self.store.move("org/other", 1, "top"), 1)
        self.assertEqual(self.order(), [1, 2])

    def test_a_move_goes_up_down_or_to_the_top_with_an_event_and_stops_at_either_end(self):
        for title in ("One", "Two", "Three", "Four"):
            self.store.create(REPO, "issue", title, labels=["agent-ready"])
        last = self.store.last_event()
        self.assertEqual(self.store.move(REPO, 4, "top"), 1)
        self.assertEqual(self.order(), [4, 1, 2, 3])
        self.assertEqual(self.store.move(REPO, 1, "down"), 3)
        self.assertEqual(self.store.move(REPO, 3, "up"), 3)
        self.assertEqual(self.order(), [4, 2, 3, 1])
        self.assertEqual(self.events(last), [(4, "queue-moved"), (1, "queue-moved"), (3, "queue-moved")])
        self.assertEqual(self.store.events_since(last)[0]["payload"], {"to": 1, "was": 4})
        # Past either end, nothing moves and nothing is written.
        last = self.store.last_event()
        self.assertEqual((self.store.move(REPO, 4, "up"), self.store.move(REPO, 4, "top"),
                          self.store.move(REPO, 1, "down")), (1, 1, 4))
        self.assertEqual((self.order(), self.store.last_event()), ([4, 2, 3, 1], last))
        # A later issue still joins after every moved one.
        self.store.create(REPO, "issue", "Five", labels=["agent-ready"])
        self.assertEqual(self.order(), [4, 2, 3, 1, 5])

    def test_a_move_to_a_place_puts_the_issue_there_and_one_past_the_end_puts_it_last(self):
        for title in ("One", "Two", "Three", "Four"):
            self.store.create(REPO, "issue", title, labels=["agent-ready"])
        last = self.store.last_event()
        self.assertEqual(self.store.move(REPO, 1, 3), 3)
        self.assertEqual(self.order(), [2, 3, 1, 4])
        self.assertEqual(self.store.move(REPO, 4, 2), 2)
        self.assertEqual(self.order(), [2, 4, 3, 1])
        self.assertEqual(self.store.events_since(last)[0]["payload"], {"to": 3, "was": 1})
        self.assertEqual(self.store.move(REPO, 2, 99), 4)
        self.assertEqual(self.order(), [4, 3, 1, 2])
        # A move to the place the issue holds writes nothing, and a place below 1 is refused.
        last = self.store.last_event()
        self.assertEqual(self.store.move(REPO, 3, 2), 2)
        self.assertEqual(self.store.last_event(), last)
        for place in (0, -1):
            with self.subTest(place=place), self.assertRaisesRegex(store.StoreError, f"{place} isn't a place"):
                self.store.move(REPO, 3, place)
        self.assertEqual(self.order(), [4, 3, 1, 2])

    def test_a_move_refuses_an_issue_outside_the_order_and_a_move_it_doesnt_know(self):
        self.store.create(REPO, "issue", "Open")
        self.store.create(REPO, "issue", "Queued", labels=["agent-ready"])
        with self.assertRaisesRegex(store.StoreError, "#1 is open, and only a queued or waiting issue"):
            self.store.move(REPO, 1, "top")
        with self.assertRaisesRegex(store.StoreError, "sideways isn't a move"):
            self.store.move(REPO, 2, "sideways")
        with self.assertRaises(store.StoreError):
            self.store.move(REPO, 9, "up")

    def test_an_issue_without_a_rank_follows_the_ranked_ones_and_a_move_or_an_import_settles_it(self):
        for title in ("One", "Two", "Three"):
            self.store.create(REPO, "issue", title, labels=["agent-ready"])
        # A restore writes states without ranks, and leaves a stale rank on an issue that left the order.
        self.store.db.execute("UPDATE items SET rank = NULL WHERE number IN (1, 2)")
        self.store.db.execute("UPDATE items SET rank = 7 WHERE number = 3")
        self.store.db.execute("UPDATE items SET state = 'open' WHERE number = 3")
        self.assertEqual(self.order(), [1, 2])
        self.store.create(REPO, "issue", "Four", labels=["agent-ready"])
        self.assertEqual(self.order(), [4, 1, 2])
        self.assertEqual(self.store.move(REPO, 2, "up"), 2)
        self.assertEqual(self.order(), [4, 2, 1])
        self.assertIsNone(self.store.db.execute("SELECT rank FROM items WHERE number = 3").fetchone()[0])
        # An import that queues an issue by its state gives it a place at the end.
        item = {key: None for key in store_schema.IMPORTED_ITEM}
        item.update(number=5, kind="issue", title="Imported", body="", state="queued", created_at="then",
                    updated_at="then", labels=[])
        self.store.import_items(REPO, [item])
        self.assertEqual(self.order(), [4, 2, 1, 5])

    def test_the_migration_ranks_queued_and_waiting_issues_by_when_they_last_entered_the_order(self):
        # A database at the schema before `rank`: #1 queued first, #3 entered waiting from open next, #2 queued last
        # and moved to waiting since, and #4 queued with no event, as an import leaves it.
        self.store.close()
        older = sqlite3.connect(Path(self.folder.name) / "older.sqlite")
        before = store.RANK_SCHEMA - 1
        for statement in [one for migration in store.MIGRATIONS[:before] for one in migration]:
            older.execute(statement)
        for number, state in ((1, "queued"), (2, "waiting"), (3, "waiting"), (4, "queued"), (5, "open")):
            older.execute("INSERT INTO items (project, number, kind, title, state, created_at, updated_at)"
                          " VALUES (?, ?, 'issue', 'Old', ?, 'then', 'then')", (REPO, number, state))
        for number, state, was in ((1, "queued", "open"), (3, "waiting", "open"), (2, "queued", "open"),
                                   (2, "waiting", "queued"), (5, "queued", "open"), (5, "open", "queued")):
            older.execute("INSERT INTO events (project, number, type, payload, at) VALUES (?, ?, 'state-changed', ?,"
                          " 'then')", (REPO, number, json.dumps({"state": state, "was": was})))
        older.execute(f"PRAGMA user_version = {before}")
        older.commit()
        older.close()
        self.store = store.Store(Path(self.folder.name) / "older.sqlite")
        self.addCleanup(self.store.close)
        self.assertEqual(self.order(), [4, 1, 3, 2])
        ranks = self.store.db.execute("SELECT number, rank FROM items ORDER BY number")
        self.assertEqual([tuple(row) for row in ranks], [(1, 2), (2, 4), (3, 3), (4, 1), (5, None)])


class RawDatabaseTest(StoreCase):
    def test_another_connection_sees_committed_rows_only(self):
        other = sqlite3.connect(self.path)
        self.addCleanup(other.close)
        with self.store.write():
            self.store._project(REPO)
            self.assertEqual(other.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)
        self.assertEqual(other.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
