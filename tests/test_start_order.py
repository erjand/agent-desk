"""Tests for a project's start order in agent-desk/lib/store.py, which issues join and leave and how `Store.move`
moves them, and for agent-desk/lib/start_order.py, which sorts it through `Store.sort_order`."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import store  # noqa: E402
import store_schema  # noqa: E402
from test_store import REPO, StoreCase  # noqa: E402


class SortedStartOrderTest(StoreCase):
    """`Store.sort_order` puts the issues that can start now first, then the ones a blocker or a serial slot holds."""

    def order(self):
        return [one["number"] for one in self.store.order(REPO)]

    def issues(self, *specs):
        """Files one issue for each spec of labels, queued unless the labels say otherwise."""
        for labels in specs:
            self.store.create(REPO, "issue", "Issue", labels=list(labels) or ["agent-ready"])

    def sort(self):
        """Sorts the order, and returns whether it changed and the new order."""
        return self.store.sort_order(REPO), self.order()

    def test_without_gates_the_order_goes_by_number_in_one_event_and_a_sorted_order_writes_nothing(self):
        self.issues((), (), (), ())
        self.store.set_state(REPO, 2, "waiting")
        self.store.move(REPO, 4, "top")
        self.store.move(REPO, 3, "top")
        last = self.store.last_event()
        self.assertEqual(self.sort(), (True, [1, 2, 3, 4]))
        self.assertEqual(self.events(last), [(None, "queue-sorted")])
        self.assertEqual(self.store.events_since(last)[0]["payload"], {"order": [1, 2, 3, 4], "was": [3, 4, 1, 2]})
        last = self.store.last_event()
        self.assertEqual(self.sort(), (False, [1, 2, 3, 4]))
        self.assertEqual(self.store.last_event(), last)

    def test_a_queued_blocker_goes_ahead_of_what_it_blocks_down_a_chain_of_three(self):
        self.issues((), (), (), ())
        self.store.link(REPO, 3, 2, "blocks")
        self.store.link(REPO, 2, 1, "blocks")
        self.assertEqual(self.sort(), (True, [3, 4, 2, 1]))

    def test_an_open_blocker_outside_the_queue_sinks_its_issue_and_a_closed_one_does_not(self):
        self.issues(("bug",), (), (), ("bug",), ())
        self.store.link(REPO, 1, 2, "blocks")
        self.store.link(REPO, 4, 5, "blocks")
        self.store.set_state(REPO, 4, "closed", "completed")
        self.store.move(REPO, 5, "top")
        self.assertEqual(self.sort(), (True, [3, 5, 2]))

    def test_the_lowest_issue_of_a_serial_group_stays_startable_and_the_rest_sink(self):
        self.issues(("agent-ready", "serial:audio"), ("agent-ready", "serial:audio"), (),
                    ("agent-ready", "serial:audio"), ("agent-ready", "serial"))
        self.store.move(REPO, 4, "top")
        self.assertEqual(self.sort(), (True, [1, 3, 5, 2, 4]))
        # A blocked first issue holds no slot in the startable tier, so the next of its group takes it.
        self.store.create(REPO, "issue", "Blocker")
        self.store.link(REPO, 6, 1, "blocks")
        self.assertEqual(self.sort(), (True, [2, 3, 5, 1, 4]))

    def test_an_in_progress_issue_holding_a_serial_group_sinks_the_queued_issues_of_that_group(self):
        self.issues(("agent-ready", "serial:audio", "serial:town"), (), ("agent-ready", "serial:town"),
                    ("agent-ready", "serial:terrain"))
        self.store.set_state(REPO, 1, "in-progress")
        self.store.move(REPO, 3, "top")
        self.assertEqual(self.sort(), (True, [2, 4, 3]))

    def test_a_cycle_of_blockers_sorts_by_number_among_its_members_ahead_of_what_it_blocks(self):
        self.issues((), (), (), (), ())
        for blocker, blocked in ((3, 2), (2, 4), (4, 3), (3, 1)):
            self.store.link(REPO, blocker, blocked, "blocks")
        self.assertEqual(self.sort(), (True, [5, 2, 3, 4, 1]))

    def test_after_a_sort_a_new_issue_joins_the_end_and_moves_work_as_before(self):
        self.issues((), (), ())
        self.store.link(REPO, 3, 1, "blocks")
        self.assertEqual(self.sort(), (True, [2, 3, 1]))
        self.issues(())
        self.store.set_state(REPO, 4, "waiting")
        self.assertEqual(self.order(), [2, 3, 1, 4])
        self.assertEqual(self.store.move(REPO, 4, "top"), 1)
        self.assertEqual(self.store.move(REPO, 1, 2), 2)
        self.assertEqual(self.order(), [4, 1, 2, 3])


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

    def test_a_move_goes_up_down_or_to_the_top_or_bottom_with_an_event_and_stops_at_either_end(self):
        for title in ("One", "Two", "Three", "Four"):
            self.store.create(REPO, "issue", title, labels=["agent-ready"])
        last = self.store.last_event()
        self.assertEqual(self.store.move(REPO, 4, "top"), 1)
        self.assertEqual(self.order(), [4, 1, 2, 3])
        self.assertEqual(self.store.move(REPO, 1, "down"), 3)
        self.assertEqual(self.store.move(REPO, 3, "up"), 3)
        self.assertEqual(self.order(), [4, 2, 3, 1])
        self.assertEqual(self.store.move(REPO, 2, "bottom"), 4)
        self.assertEqual(self.store.move(REPO, 2, "top"), 1)
        self.assertEqual(self.store.move(REPO, 4, "bottom"), 4)
        self.assertEqual(self.store.move(REPO, 4, "top"), 1)
        self.assertEqual(self.order(), [4, 2, 3, 1])
        self.assertEqual(self.events(last), [(4, "queue-moved"), (1, "queue-moved"), (3, "queue-moved"),
                                             (2, "queue-moved"), (2, "queue-moved"), (4, "queue-moved"),
                                             (4, "queue-moved")])
        self.assertEqual(self.store.events_since(last)[0]["payload"], {"to": 1, "was": 4})
        self.assertEqual(self.store.events_since(last)[3]["payload"], {"to": 4, "was": 2})
        # Past either end, nothing moves and nothing is written.
        last = self.store.last_event()
        self.assertEqual((self.store.move(REPO, 4, "up"), self.store.move(REPO, 4, "top"),
                          self.store.move(REPO, 1, "down"), self.store.move(REPO, 1, "bottom")), (1, 1, 4, 4))
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


if __name__ == "__main__":
    unittest.main()
