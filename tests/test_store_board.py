"""Tests for agent-desk/lib/store_board.py, the store's board read, through `Store.board` and the branch and merge
reads it shows."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import store  # noqa: E402
from test_store import REPO, StoreCase  # noqa: E402


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
        # A queued or waiting issue holds a place in the start order, and every other open issue one in the list order.
        self.assertEqual({number: (one.get("position"), one.get("listPosition")) for number, one in issues.items()},
                         {blocker: (None, 3), blocked: (1, None), parent: (None, 2), parent + 2: (None, 1)})

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

    def test_a_heads_result_stands_while_a_re_check_of_a_newer_base_waits_and_runs_and_then_gives_way(self):
        review = self.store.create(REPO, "review", "Checked", branch="agent/issue-9", head_sha="abc")
        self.store.branch_moved(REPO, "agent/issue-9", "abc", None)
        self.store.merge_tried(REPO, "agent/issue-9", "abc", "main", "m2", "MERGEABLE")

        def shown():
            [one] = [one for one in self.store.board(REPO)["reviews"] if one["number"] == review]
            return [(c["status"], c["baseSha"], c["recheck"]) for c in one["checks"]], one["baseHead"]

        first = self.store.check_queue(REPO, "agent/issue-9", "abc")
        self.store.check_started(first, "/logs/1.log")
        self.store.check_merges(first, "main", "m1")
        self.store.check_ended(first, "passed")
        self.assertEqual(shown(), ([("passed", "m1", False)], "m2"))
        again = self.store.check_queue(REPO, "agent/issue-9", "abc", "main", "m2", recheck=True)
        self.assertEqual(shown()[0], [("passed", "m1", False)])
        self.store.check_started(again, "/logs/2.log")
        self.assertEqual(shown()[0], [("passed", "m1", False)])
        self.store.check_ended(again, "failed")
        self.assertEqual(shown()[0], [("failed", "m2", True)])
        self.assertTrue(self.store.base_broke(REPO, "agent/issue-9", "abc"))
        # A newer commit of the base supersedes a re-check of an older one, and the result before it shows again.
        newer = self.store.check_queue(REPO, "agent/issue-9", "abc", "main", "m3", recheck=True)
        self.assertEqual(self.store.base_moved(REPO, "agent/issue-9", "m3"), [])
        self.assertEqual(self.store.base_moved(REPO, "agent/issue-9", "m4"), [])
        self.assertEqual(self.store.check(newer)["status"], "superseded")
        self.assertEqual(shown()[0], [("failed", "m2", True)])

    def test_a_tried_merge_says_whether_the_head_merges_with_its_base_until_the_branch_moves(self):
        review = self.store.create(REPO, "review", "Tried", branch="agent/issue-9", base="local-desk", head_sha="abc")

        def shown():
            return {one["number"]: one for one in self.store.board(REPO)["reviews"]}[review]["mergeable"]

        self.store.branch_moved(REPO, "agent/issue-9", "def", "abc")
        self.assertEqual(self.store.branch_heads(REPO), {"agent/issue-9": "def"})
        self.assertEqual(self.store.merge_targets(REPO), [{"branch": "agent/issue-9", "review_base": "local-desk",
                                                           "draft": 0, "head_sha": "def", "base": None, "base_sha": None,
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


if __name__ == "__main__":
    unittest.main()
