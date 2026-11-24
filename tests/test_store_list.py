"""Tests for a project's list order in agent-desk/lib/store_list.py: where issues arrive in it, how `Store.list_move`
and `Store.list_reset` change it, and how the migration and a read-only store read it."""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import store  # noqa: E402
import store_schema  # noqa: E402
from test_store import REPO, StoreCase  # noqa: E402

OTHER = "org/tool"


class ListOrderCase(StoreCase):
    def order(self, project=REPO):
        return [one["number"] for one in self.store.list_order(project)]

    def issues(self, count, project=REPO):
        """Files `count` open issues, which list the newest first."""
        return [self.store.create(project, "issue", f"Issue {index}") for index in range(count)]


class ArrivalTest(ListOrderCase):
    """An issue takes the top when it is filed and when its state, its backlog or idea label, or a blocker changes."""

    def test_a_new_issue_takes_the_top_and_a_review_takes_no_place(self):
        self.issues(2)
        review = self.store.create(REPO, "review", "A review", branch="agent/issue-1")
        self.store.list_move(REPO, 2, where="bottom")
        self.issues(1)
        self.assertEqual(self.order(), [4, 1, 2])
        self.assertIsNone(self.store.db.execute("SELECT list_rank FROM items WHERE number = ?",
                                                (review,)).fetchone()[0])

    def test_each_change_of_state_puts_the_issue_first_in_the_section_it_enters(self):
        self.issues(4)
        # Queued and back to Open, as a queued issue sent back does.
        self.store.edit(REPO, 1, add_labels=["agent-ready"])
        self.assertEqual(self.order(), [4, 3, 2])
        self.store.edit(REPO, 1, remove_labels=["agent-ready"])
        self.assertEqual(self.order(), [1, 4, 3, 2])
        # Needs Design, on hold, and back to Open, as Unblock does.
        for number, state in ((2, "needs-design"), (3, "on-hold"), (2, "open")):
            self.store.set_state(REPO, number, state)
        self.assertEqual(self.order(), [2, 3, 1, 4])
        # Closed and reopened.
        self.store.set_state(REPO, 4, "closed", "completed")
        self.assertEqual(self.order(), [2, 3, 1])
        self.store.set_state(REPO, 4, "open")
        self.assertEqual(self.order(), [4, 2, 3, 1])

    def test_adding_or_removing_backlog_or_idea_puts_the_issue_first_and_another_label_leaves_it(self):
        self.issues(4)
        self.store.edit(REPO, 1, add_labels=["backlog"])
        self.store.edit(REPO, 2, add_labels=["idea"])
        self.assertEqual(self.order(), [2, 1, 4, 3])
        self.store.edit(REPO, 1, remove_labels=["backlog"])
        self.assertEqual(self.order(), [1, 2, 4, 3])
        self.store.edit(REPO, 3, add_labels=["bug"])
        self.store.edit(REPO, 2, remove_labels=["idea"], add_labels=["serial:web"])
        self.assertEqual(self.order(), [2, 1, 4, 3])
        # A label the issue never held writes nothing, and leaves its place.
        last = self.store.last_event()
        self.store.edit(REPO, 4, remove_labels=["backlog"])
        self.assertEqual(self.order(), [2, 1, 4, 3])
        self.assertEqual([event["type"] for event in self.store.events_since(last)], [])

    def test_a_blocks_link_onto_an_issue_puts_it_first_and_its_blocker_closing_leaves_it(self):
        self.issues(4)
        self.store.link(REPO, 4, 1, "blocks")
        self.assertEqual(self.order(), [1, 4, 3, 2])
        self.store.link(REPO, 1, 2, "parent")
        self.assertEqual(self.order(), [1, 4, 3, 2])
        self.store.list_move(REPO, 1, where="bottom")
        self.store.set_state(REPO, 4, "closed", "completed")
        self.assertEqual(self.order(), [3, 2, 1])
        self.store.link(REPO, 4, 1, "blocks", add=False)
        self.assertEqual(self.order(), [1, 3, 2])
        # An issue filed blocked takes the top once for its filing and again for the link.
        self.store.create(REPO, "issue", "Blocked", blocked_by=[3])
        self.assertEqual(self.order(), [5, 1, 3, 2])

    def test_an_import_gives_a_new_issue_a_place_by_number_and_an_issue_whose_state_it_changes_the_top(self):
        self.issues(2)
        self.store.list_move(REPO, 1, where="top")
        items = []
        for number, state in ((1, "open"), (2, "needs-design"), (5, "open"), (4, "open")):
            item = {key: None for key in store_schema.IMPORTED_ITEM}
            item.update(number=number, kind="issue", title=f"Issue {number}", body="", state=state,
                        created_at="then", updated_at="then", labels=[])
            items.append(item)
        items[0]["title"] = "Issue 0"
        self.store.import_items(REPO, items)
        self.assertEqual(self.order(), [5, 4, 2, 1])


class MoveTest(ListOrderCase):
    def test_a_move_goes_above_or_below_another_issue_or_to_the_top_or_bottom_with_an_event(self):
        self.issues(5)
        self.assertEqual(self.order(), [5, 4, 3, 2, 1])
        last = self.store.last_event()
        self.assertEqual(self.store.list_move(REPO, 1, before=4), (2, 5))
        self.assertEqual(self.order(), [5, 1, 4, 3, 2])
        self.assertEqual(self.store.list_move(REPO, 5, after=3), (4, 5))
        self.assertEqual(self.order(), [1, 4, 3, 5, 2])
        self.assertEqual(self.store.list_move(REPO, 2, where="top"), (1, 5))
        self.assertEqual(self.store.list_move(REPO, 4, where="bottom"), (5, 5))
        self.assertEqual(self.order(), [2, 1, 3, 5, 4])
        events = self.store.events_since(last)
        self.assertEqual([(one["number"], one["type"], one["payload"]) for one in events], [
            (1, "list-moved", {"to": 2, "was": 5}), (5, "list-moved", {"to": 4, "was": 1}),
            (2, "list-moved", {"to": 1, "was": 5}), (4, "list-moved", {"to": 5, "was": 3})])

    def test_a_move_to_the_place_the_issue_holds_writes_nothing(self):
        self.issues(3)
        last = self.store.last_event()
        self.assertEqual(self.store.list_move(REPO, 3, where="top"), (1, 3))
        self.assertEqual(self.store.list_move(REPO, 1, where="bottom"), (3, 3))
        self.assertEqual(self.store.list_move(REPO, 3, before=2), (1, 3))
        self.assertEqual(self.store.list_move(REPO, 1, after=2), (3, 3))
        self.assertEqual(self.store.last_event(), last)

    def test_a_move_counts_only_open_issues_outside_the_start_order_and_leaves_the_rest_alone(self):
        self.issues(5)
        self.issues(2, OTHER)
        self.store.edit(REPO, 4, add_labels=["agent-ready"])
        self.store.set_state(REPO, 2, "closed", "completed")
        ranks = dict(self.store.db.execute("SELECT number, list_rank FROM items WHERE project = ? AND number IN"
                                           " (2, 4)", (REPO,)).fetchall())
        self.assertEqual(self.order(), [5, 3, 1])
        self.assertEqual(self.store.list_move(REPO, 1, before=5), (1, 3))
        self.assertEqual(self.order(), [1, 5, 3])
        self.assertEqual(self.store.list_move(REPO, 5, where="bottom"), (3, 3))
        self.assertEqual(self.order(), [1, 3, 5])
        self.assertEqual(dict(self.store.db.execute("SELECT number, list_rank FROM items WHERE project = ? AND number"
                                                    " IN (2, 4)", (REPO,)).fetchall()), ranks)
        self.assertEqual(self.order(OTHER), [2, 1])

    def test_a_move_refuses_an_issue_without_a_place_a_review_a_missing_one_and_itself(self):
        self.issues(4)
        self.store.create(REPO, "review", "A review", branch="agent/issue-1")
        self.store.set_state(REPO, 2, "closed", "completed")
        self.store.set_state(REPO, 3, "waiting")
        last = self.store.last_event()
        refusals = [((2,), {"where": "top"}, "#2 is closed, and only an open issue"),
                    ((1,), {"before": 2}, "#2 is closed"),
                    ((3,), {"where": "bottom"}, "#3 is waiting, so the start order places it"),
                    ((5,), {"where": "top"}, "#5 is a review"),
                    ((1,), {"after": 5}, "#5 is a review"),
                    ((9,), {"where": "top"}, f"{REPO} has no issue #9"),
                    ((1,), {"before": 9}, f"{REPO} has no issue #9"),
                    ((1,), {"before": 1}, "#1 can't move next to itself"),
                    ((1,), {}, "takes one of before, after, top, or bottom"),
                    ((1,), {"before": 4, "where": "top"}, "takes one of before, after, top, or bottom"),
                    ((1,), {"where": "up"}, "takes one of before, after, top, or bottom")]
        for args, kwargs, message in refusals:
            with self.subTest(args=args, kwargs=kwargs), self.assertRaisesRegex(store.StoreError, message):
                self.store.list_move(REPO, *args, **kwargs)
        self.assertEqual(self.store.last_event(), last)


class ResetTest(ListOrderCase):
    def test_a_reset_deals_the_issues_places_out_by_number_and_leaves_every_other_place(self):
        self.issues(6)
        for number, where in ((2, "top"), (5, "bottom"), (1, "top")):
            self.store.list_move(REPO, number, where=where)
        self.assertEqual(self.order(), [1, 2, 6, 4, 3, 5])
        last = self.store.last_event()
        self.assertTrue(self.store.list_reset(REPO, [1, 5, 2, 4, 5]))
        self.assertEqual(self.order(), [5, 4, 6, 2, 3, 1])
        events = self.store.events_since(last)
        self.assertEqual([(one["number"], one["type"], one["payload"]) for one in events],
                         [(None, "list-reset", {"order": [5, 4, 2, 1], "was": [1, 2, 4, 5]})])
        last = self.store.last_event()
        self.assertFalse(self.store.list_reset(REPO, [2, 4, 1, 5]))
        self.assertEqual(self.store.last_event(), last)

    def test_a_reset_refuses_an_issue_without_a_place_and_writes_nothing(self):
        self.issues(3)
        self.store.list_move(REPO, 1, where="top")
        self.store.set_state(REPO, 2, "queued")
        last = self.store.last_event()
        for numbers, message in (([1, 2], "#2 is queued"), ([1, 9], "has no issue #9"), ([], "takes the issues")):
            with self.subTest(numbers=numbers), self.assertRaisesRegex(store.StoreError, message):
                self.store.list_reset(REPO, numbers)
        self.assertEqual(self.store.last_event(), last)
        self.assertEqual(self.order(), [1, 3])


class MigrationTest(ListOrderCase):
    def older(self):
        """A database at the schema before the list order, with issues 1, 2, 3, and 5 and review 4."""
        self.store.close()
        path = Path(self.folder.name) / "older.sqlite"
        older = sqlite3.connect(path)
        before = store_schema.LIST_SCHEMA - 1
        for statement in [one for migration in store.MIGRATIONS[:before] for one in migration]:
            older.execute(statement)
        older.execute("INSERT INTO projects (slug, next_number) VALUES (?, 6)", (REPO,))
        for number, kind, state in ((1, "issue", "open"), (2, "issue", "needs-design"), (3, "issue", "closed"),
                                    (4, "review", "open"), (5, "issue", "on-hold")):
            older.execute("INSERT INTO items (project, number, kind, title, state, created_at, updated_at)"
                          " VALUES (?, ?, ?, 'Old', ?, 'then', 'then')", (REPO, number, kind, state))
        older.execute(f"PRAGMA user_version = {before}")
        older.commit()
        older.close()
        return path

    def test_the_migration_gives_every_issue_its_number_so_each_section_lists_as_before(self):
        self.store = store.Store(self.older())
        self.addCleanup(self.store.close)
        self.assertEqual(self.order(), [5, 2, 1])
        ranks = self.store.db.execute("SELECT number, list_rank FROM items ORDER BY number")
        self.assertEqual([tuple(row) for row in ranks], [(1, 1), (2, 2), (3, 3), (4, None), (5, 5)])
        # A new issue takes the top past every number.
        self.assertEqual(self.store.create(REPO, "issue", "New"), 6)
        self.assertEqual(self.order(), [6, 5, 2, 1])

    def test_a_read_only_store_on_an_older_database_lists_its_issues_by_number_highest_first(self):
        path = self.older()
        before = path.read_bytes()
        self.store = store.Store(path, read_only=True)
        self.addCleanup(self.store.close)
        self.assertEqual(self.order(), [5, 2, 1])
        board = self.store.board(REPO)
        self.assertEqual({one["number"]: one["listPosition"] for one in board["issues"]}, {1: 3, 2: 2, 5: 1})
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
