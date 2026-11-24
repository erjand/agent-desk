"""Tests for agent-desk/lib/store_cli.py, the command line behind agent-desk issue, agent-desk review, and
agent-desk label."""

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hold  # noqa: E402
import store  # noqa: E402
from test_store import REPO, StoreCase  # noqa: E402


class CommandTest(StoreCase):
    def setUp(self):
        super().setUp()
        # A close reads and writes lib/hold.py's picks, which stay in the test's folder.
        for patch in (mock.patch.object(hold, "HOLD", Path(self.folder.name) / "hold.json"),
                      mock.patch.object(hold, "LOCK", Path(self.folder.name) / "hold.lock")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_an_issue_goes_from_create_to_close_through_the_commands(self):
        self.assertEqual(self.cli("issue", "create", "--title", "Blocker"), (0, "1", ""))
        self.assertEqual(self.cli("issue", "create", "--title", "Add a store", "--body", "Why", "--label", "idea",
                                  "--blocked-by", "1"), (0, "2", ""))
        self.assertEqual(self.cli("issue", "comment", "2", "--body", "A note")[0], 0)
        self.assertEqual(self.cli("issue", "edit", "#2", "--add-label", "agent-ready", "--remove-label", "idea")[0], 0)
        status, out, _ = self.cli("issue", "view", "2", "--json", "number,status,labels,comments,blockedBy")
        shaped = json.loads(out)
        self.assertEqual((status, shaped["status"], shaped["labels"]), (0, "queued", []))
        self.assertEqual(shaped["comments"][0]["body"], "A note")
        self.assertEqual(shaped["blockedBy"][0]["number"], 1)
        self.assertEqual(self.cli("issue", "close", "2", "--reason", "not-planned")[0], 0)
        _, out, _ = self.cli("issue", "list", "--state", "closed", "--json", "number,stateReason")
        self.assertEqual(json.loads(out), [{"number": 2, "stateReason": "not-planned"}])

    def test_queue_order_prints_the_start_order_and_moves_an_issue_in_it(self):
        for title in ("Paint", "Sand", "Oil"):
            self.cli("issue", "create", "--title", title, "--label", "agent-ready")
        self.cli("issue", "create", "--title", "Open")
        self.cli("issue", "edit", "2", "--status", "waiting")
        self.assertEqual(self.cli("queue", "order"), (0, "1\t#1\tqueued\tPaint\n2\t#2\twaiting\tSand\n"
                                                         "3\t#3\tqueued\tOil", ""))
        self.assertEqual(self.cli("queue", "order", "3", "--top"), (0, "#3 is 1 of 3 in the start order.", ""))
        self.assertEqual(self.cli("queue", "order", "#3", "--down"), (0, "#3 is 2 of 3 in the start order.", ""))
        self.assertEqual(self.cli("queue", "order", "2", "--top")[0], 0)
        self.assertEqual([one["number"] for one in self.store.order(REPO)], [2, 1, 3])
        self.assertEqual(self.cli("queue", "order", "2", "--to", "3"), (0, "#2 is 3 of 3 in the start order.", ""))
        self.assertEqual(self.cli("queue", "order", "2", "--to", "1"), (0, "#2 is 1 of 3 in the start order.", ""))
        self.assertEqual(self.cli("queue", "order", "2", "--bottom"), (0, "#2 is 3 of 3 in the start order.", ""))
        self.assertEqual(self.cli("queue", "order", "2", "--top"), (0, "#2 is 1 of 3 in the start order.", ""))
        status, out, err = self.cli("queue", "order", "2", "--to", "0")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("0 isn't a place", err)
        self.assertEqual(json.loads(self.cli("issue", "view", "3", "--json", "position")[1]), {"position": 3})
        status, out, err = self.cli("queue", "order", "4", "--top")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("#4 is open, and only a queued or waiting issue", err)
        for argv in (("3",), ("--up",), ("3", "--up", "--down"), ("3", "--to", "2", "--top"), ("3", "--to", "two"),
                     ("3", "--top", "--bottom"), ("--bottom",)):
            with self.subTest(argv=argv), mock.patch("sys.stderr", io.StringIO()):
                try:
                    status = self.cli("queue", "order", *argv)[0]
                except SystemExit as stopped:
                    status = stopped.code
                self.assertNotEqual(status, 0)
        self.assertEqual([one["number"] for one in self.store.order(REPO)], [2, 1, 3])

    def test_queue_order_sort_sorts_and_prints_the_order_and_refuses_an_issue_or_a_move_with_it(self):
        for title in ("Paint", "Sand", "Oil"):
            self.cli("issue", "create", "--title", title, "--label", "agent-ready")
        self.cli("issue", "edit", "1", "--add-blocked-by", "3")
        self.cli("queue", "order", "2", "--top")
        last = self.store.last_event()
        self.assertEqual(self.cli("queue", "order", "--sort"), (0, "1\t#2\tqueued\tSand\n2\t#3\tqueued\tOil\n"
                                                                   "3\t#1\tqueued\tPaint", ""))
        self.assertEqual(self.events(last), [(None, "queue-sorted")])
        status, out, err = self.cli("queue", "order", "3", "--sort")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("--sort sorts the whole order, so it takes no issue", err)
        for argv in (("--sort", "--top"), ("3", "--to", "1", "--sort")):
            with self.subTest(argv=argv), mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    self.cli("queue", "order", *argv)
                self.assertNotEqual(stopped.exception.code, 0)
        self.assertEqual(self.store.last_event(), last + 1)

    def test_list_order_prints_the_order_and_moves_an_issue_in_it(self):
        for title in ("Paint", "Sand", "Oil", "Queued"):
            self.cli("issue", "create", "--title", title)
        self.cli("issue", "edit", "2", "--status", "needs-design")
        self.cli("issue", "edit", "4", "--add-label", "agent-ready")
        self.assertEqual(self.cli("list", "order"), (0, "1\t#2\tneeds-design\tSand\n2\t#3\topen\tOil\n"
                                                        "3\t#1\topen\tPaint", ""))
        last = self.store.last_event()
        self.assertEqual(self.cli("list", "order", "1", "--top"), (0, "#1 is 1 of 3 in the list order.", ""))
        self.assertEqual(self.cli("list", "order", "#1", "--after", "#3"), (0, "#1 is 3 of 3 in the list order.", ""))
        self.assertEqual(self.cli("list", "order", "3", "--before", "2"), (0, "#3 is 1 of 3 in the list order.", ""))
        self.assertEqual(self.cli("list", "order", "3", "--bottom"), (0, "#3 is 3 of 3 in the list order.", ""))
        self.assertEqual([event["type"] for event in self.store.events_since(last)], ["list-moved"] * 4)
        self.assertEqual([one["number"] for one in self.store.list_order(REPO)], [2, 1, 3])
        for argv, message in ((("4", "--top"), "#4 is queued, so the start order places it"),
                              (("1", "--before", "1"), "#1 can't move next to itself"),
                              (("9", "--top"), "has no issue #9")):
            with self.subTest(argv=argv):
                status, out, err = self.cli("list", "order", *argv)
                self.assertEqual((status, out), (1, ""))
                self.assertIn(message, err)
        for argv in (("3",), ("--top",), ("3", "--top", "--bottom"), ("3", "--before", "2", "--after", "1"),
                     ("3", "--before", "two"), ("3", "--reset", "1", "2"), ("--reset",)):
            with self.subTest(argv=argv), mock.patch("sys.stderr", io.StringIO()):
                try:
                    status = self.cli("list", "order", *argv)[0]
                except SystemExit as stopped:
                    status = stopped.code
                self.assertNotEqual(status, 0)
        self.assertEqual([one["number"] for one in self.store.list_order(REPO)], [2, 1, 3])

    def test_list_order_reset_deals_the_places_out_by_number_and_prints_the_issues_it_reset(self):
        for title in ("Paint", "Sand", "Oil", "Wax"):
            self.cli("issue", "create", "--title", title)
        self.cli("list", "order", "1", "--top")
        self.cli("list", "order", "2", "--before", "4")
        last = self.store.last_event()
        self.assertEqual(self.cli("list", "order", "--reset", "1", "#2", "3"),
                         (0, "1\t#3\topen\tOil\n2\t#2\topen\tSand\n4\t#1\topen\tPaint", ""))
        # #4 keeps its own place between them.
        self.assertEqual([one["number"] for one in self.store.list_order(REPO)], [3, 2, 4, 1])
        self.assertEqual([event["type"] for event in self.store.events_since(last)], ["list-reset"])
        last = self.store.last_event()
        self.assertEqual(self.cli("list", "order", "--reset", "3", "1")[0], 0)
        self.assertEqual(self.store.last_event(), last)

    def test_a_close_posts_its_comment_in_the_same_write_so_a_refused_close_leaves_none(self):
        self.cli("issue", "create", "--title", "One")
        self.cli("issue", "create", "--title", "Two")
        last = self.store.last_event()
        with mock.patch.object(sys, "stdin", io.StringIO("Audits skip generated files.\n")):
            self.assertEqual(self.cli("issue", "close", "1", "--reason", "not-planned", "--comment-file", "-")[0], 0)
        shaped = self.store.item(REPO, 1)
        self.assertEqual((shaped["state"], [one["body"] for one in shaped["comments"]]),
                         ("CLOSED", ["Audits skip generated files.\n"]))
        self.assertEqual(self.events(last), [(1, "comment-added"), (1, "state-changed")])
        with mock.patch.object(store.Store, "_set_state", side_effect=store.StoreError("The close failed.")):
            self.assertEqual(self.cli("issue", "close", "2", "--comment", "Done.")[0], 1)
        shaped = self.store.item(REPO, 2)
        self.assertEqual((shaped["state"], shaped["comments"]), ("OPEN", []))

    def test_a_duplicate_close_comments_links_the_original_and_sets_the_reason(self):
        for title in ("Original", "Copy", "Second copy"):
            self.cli("issue", "create", "--title", title)
        self.assertEqual(self.cli("issue", "close", "2", "--duplicate-of", "#1", "--comment", "Same bug.")[0], 0)
        shaped = self.store.item(REPO, 2)
        self.assertEqual((shaped["state"], shaped["stateReason"], shaped["duplicateOf"]["number"]),
                         ("CLOSED", "duplicate", 1))
        self.assertEqual([one["body"] for one in shaped["comments"]], ["Duplicate of #1", "Same bug."])
        self.assertIn("duplicate of: #1", self.cli("issue", "view", "2")[1])
        self.assertEqual(self.cli("issue", "close", "3", "--reason", "duplicate", "--duplicate-of", "1")[0], 0)
        self.assertEqual(self.store.item(REPO, 3)["stateReason"], "duplicate")
        # A reopened issue is no longer a duplicate; a close for another reason links nothing.
        self.assertEqual(self.cli("issue", "reopen", "2")[0], 0)
        self.assertIsNone(self.store.item(REPO, 2)["duplicateOf"])
        self.assertEqual(self.cli("issue", "close", "2")[0], 0)
        shaped = self.store.item(REPO, 2)
        self.assertEqual((shaped["stateReason"], shaped["duplicateOf"]), ("completed", None))

    def test_a_duplicate_close_refuses_a_missing_original_itself_or_a_clashing_reason_and_writes_nothing(self):
        self.cli("issue", "create", "--title", "Copy")
        self.cli("review", "create", "--branch", "agent/issue-1", "--title", "A review")
        waiting = self.store.request_run(REPO, "agent", "1")
        last = self.store.last_event()
        for argv, said in ((("--duplicate-of", "9"), f"{REPO} has no issue #9."),
                           (("--duplicate-of", "2"), f"{REPO} has no issue #2."),
                           (("--duplicate-of", "1"), "#1 can't be a duplicate of itself."),
                           (("--reason", "duplicate"), "takes --duplicate-of"),
                           (("--reason", "not-planned", "--duplicate-of", "2"), "can't go with --reason not-planned")):
            with self.subTest(argv=argv):
                status, out, err = self.cli("issue", "close", "1", *argv, "--comment", "Same bug.")
                self.assertEqual((status, out), (1, ""))
                self.assertIn(said, err)
        shaped = self.store.item(REPO, 1)
        self.assertEqual((shaped["state"], shaped["comments"], shaped["duplicateOf"]), ("OPEN", [], None))
        self.assertEqual((self.events(last), self.store.run(waiting)["status"]), ([], "requested"))
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            self.cli("issue", "close", "1", "--duplicate-of", "one")

    def test_a_close_for_any_reason_cancels_the_waiting_agent_requests_and_leaves_a_working_run(self):
        for title in ("Original", "Done", "Dropped", "Copy"):
            self.cli("issue", "create", "--title", title, "--label", "agent-ready")
        working = self.store.request_run(REPO, "agent", "2")
        self.store.run_started(working, "agent-desk-agent-me-repo-2-1.service")
        waiting = {number: self.store.request_run(REPO, "agent", str(number)) for number in (2, 3, 4)}
        kept = self.store.request_run(REPO, "agent", "1")
        for argv in (("2",), ("3", "--reason", "not-planned"), ("4", "--duplicate-of", "1")):
            self.assertEqual(self.cli("issue", "close", *argv)[0], 0)
        self.assertEqual([self.store.run(run)["status"] for run in waiting.values()], ["cancelled"] * 3)
        self.assertEqual((self.store.run(working)["status"], self.store.run(kept)["status"]), ("running", "requested"))

    def test_a_close_drops_the_issue_s_manual_pick_and_keeps_the_others(self):
        for title in ("Picked", "Also picked"):
            self.cli("issue", "create", "--title", title, "--label", "agent-ready")
        hold.update(lambda data: data.update(state="manual", picked=[f"{REPO}#1", f"{REPO}#2", "me/other#1"]))
        self.assertEqual(self.cli("issue", "close", "1", "--reason", "not-planned")[0], 0)
        self.assertEqual(hold.status()["picked"], [f"{REPO}#2", "me/other#1"])
        # A close the store refuses keeps the pick.
        self.assertEqual(self.cli("issue", "close", "2", "--duplicate-of", "2")[0], 1)
        self.assertEqual(hold.status()["picked"], [f"{REPO}#2", "me/other#1"])

    def test_json_keeps_only_the_fields_asked_for_and_names_an_unknown_one(self):
        self.cli("issue", "create", "--title", "One")
        self.assertEqual(json.loads(self.cli("issue", "view", "1", "--json", "number,title")[1]),
                         {"number": 1, "title": "One"})
        status, out, err = self.cli("issue", "view", "1", "--json", "number,headRefName")
        self.assertEqual((status, out), (1, ""))
        self.assertIn("Unknown JSON field: headRefName", err)

    def test_a_body_comes_from_a_file_or_the_standard_input(self):
        text = Path(self.folder.name, "body.md")
        text.write_text("From a file\n")
        self.cli("issue", "create", "--title", "One", "--body-file", str(text))
        with mock.patch.object(sys, "stdin", io.StringIO("From a pipe\n")):
            self.cli("issue", "comment", "1", "--body-file", "-")
        shaped = self.store.item(REPO, 1)
        self.assertEqual((shaped["body"], shaped["comments"][0]["body"]), ("From a file\n", "From a pipe\n"))

    def test_a_review_goes_from_create_to_merge_by_its_branch(self):
        self.cli("issue", "create", "--title", "Add a store")
        self.assertEqual(self.cli("review", "create", "--branch", "agent/issue-1", "--title", "Add the store",
                                  "--draft", "--fixes", "1"), (0, "2", ""))
        self.assertEqual(self.cli("review", "ready", "agent/issue-1")[0], 0)
        self.assertEqual(self.cli("review", "comment", "2", "--body", "Rename", "--path", "a.py", "--line", "3",
                                  "--to-agent")[0], 0)
        _, out, _ = self.cli("review", "list", "--head", "agent/issue-1", "--json", "number,isDraft,comments")
        listed = json.loads(out)
        self.assertEqual((listed[0]["number"], listed[0]["isDraft"]), (2, False))
        self.assertTrue(listed[0]["comments"][0]["toAgent"])
        self.assertEqual(self.cli("review", "merge", "2", "--sha", "f00d")[0], 0)
        self.assertEqual(json.loads(self.cli("issue", "view", "1", "--json", "state")[1]), {"state": "CLOSED"})
        self.assertEqual(json.loads(self.cli("review", "view", "2", "--json", "state")[1]), {"state": "MERGED"})

    def test_the_inbox_buttons_commands_change_what_the_board_reads_before_they_return(self):
        # The pane and the web page keep no move for these buttons, so the read right after each command must show it.
        self.store.comment_answered(None, None, self.store.last_event(), "start")
        for title in ("Queue me", "Design me", "Hold me", "Close me", "Fix me"):
            self.cli("issue", "create", "--title", title)
        self.cli("issue", "edit", "2", "--add-label", "needs-design")
        self.cli("issue", "edit", "3", "--add-label", "on-hold")
        self.cli("review", "create", "--branch", "agent/issue-5", "--title", "Fix it", "--draft", "--fixes", "5")
        board = self.store.board(REPO)
        self.assertEqual([(one["number"], one["status"]) for one in board["issues"]],
                         [(1, "open"), (2, "needs-design"), (3, "on-hold"), (4, "open"), (5, "open")])
        self.cli("issue", "edit", "1", "--add-label", "agent-ready")
        self.cli("issue", "edit", "2", "--remove-label", "needs-design")
        self.cli("issue", "edit", "3", "--remove-label", "on-hold")
        self.cli("issue", "close", "4", "--reason", "completed")
        self.cli("review", "ready", "6")
        board = self.store.board(REPO)
        self.assertEqual([(one["number"], one["status"]) for one in board["issues"]],
                         [(1, "queued"), (2, "open"), (3, "open"), (5, "open")])
        self.assertEqual([one["draft"] for one in board["reviews"]], [False])

        # Fix's comment asks for no run until the dispatcher answers its event, so the board lists its review as asked.
        last = self.store.last_event()
        self.assertEqual(self.cli("review", "comment", "6", "--body", "Fix the conflicts", "--to-agent")[0], 0)
        self.assertEqual(self.events(last), [(6, "comment-added")])
        board = self.store.board(REPO)
        self.assertEqual((board["runs"], board["asked"]), ([], [6]))
        # The dispatcher's answer, written with the run's request, takes the review off the list.
        added = self.store.last_event()
        comment = self.store.agent_comments(last)[0]["payload"]["comment"]
        run = self.store.request_run(REPO, "agent", "5", env={"EVENT": "issue_comment", "REPLY_PR": "6"})
        self.store.comment_answered(REPO, 6, added, "run", comment, run)
        board = self.store.board(REPO)
        self.assertEqual(([one["status"] for one in board["runs"]], board["asked"]), (["requested"], []))

    def test_edit_replaces_a_comments_body_by_the_id_view_prints(self):
        self.cli("issue", "create", "--title", "One")
        self.cli("issue", "comment", "1", "--body", "Use blue")
        status, out, _ = self.cli("issue", "view", "1")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"\n-- you \(comment 1\) at \S+Z\nUse blue")
        last = self.store.last_event()
        with mock.patch.object(sys, "stdin", io.StringIO("Use green\n")):
            self.assertEqual(self.cli("issue", "comment", "1", "--edit", "1", "--body-file", "-"), (0, "", ""))
        self.assertEqual(self.events(last), [(1, "comment-edited")])
        _, out, _ = self.cli("issue", "view", "1")
        self.assertRegex(out, r"\n-- you \(comment 1\) at \S+Z, edited \S+Z\nUse green")
        self.assertEqual(json.loads(self.cli("issue", "view", "1", "--json", "comments")[1])["comments"][0]["body"],
                         "Use green\n")

    def test_an_agents_edit_needs_a_note_which_the_comment_shows(self):
        self.cli("issue", "create", "--title", "One")
        with mock.patch.dict(os.environ, {"AGENT_DESK_RUN": "9"}):
            self.cli("issue", "comment", "1", "--body", "Pick one")
            self.assertEqual(self.cli("issue", "comment", "1", "--edit", "1", "--body", "Picked"),
                             (1, "", "agent-desk: An edit by agent says what it changed with a note, which the "
                                     "comment then shows."))
            self.assertEqual(self.cli("issue", "comment", "1", "--edit", "1", "--body", "Picked", "--note",
                                      "Removed the answered decision"), (0, "", ""))
        _, out, _ = self.cli("issue", "view", "1")
        self.assertRegex(out, r"\n-- agent \(comment 1\) at \S+Z, edited \S+Z\nPicked\n\n\*\*Edited "
                              r"\d{4}-\d\d-\d\d \d\d:\d\d UTC:\*\* Removed the answered decision$")

    def test_edit_refuses_the_flags_of_a_new_comment_and_a_comment_on_another_item(self):
        self.cli("issue", "create", "--title", "One")
        self.cli("issue", "create", "--title", "Two")
        self.cli("issue", "comment", "1", "--body", "Use blue")
        self.cli("review", "create", "--branch", "agent/issue-1", "--title", "Paint")
        last = self.store.last_event()
        for flags, named in ((["--to-agent"], "--to-agent"), (["--quotes", "1"], "--quotes")):
            with self.subTest(flags=flags):
                self.assertEqual(self.cli("issue", "comment", "1", "--edit", "1", "--body", "B", *flags),
                                 (1, "", f"agent-desk: --edit changes a comment's body only, so it can't go with "
                                         f"{named}."))
        self.assertEqual(self.cli("review", "comment", "3", "--edit", "1", "--body", "B", "--path", "a.py",
                                  "--line", "2")[2],
                         "agent-desk: --edit changes a comment's body only, so it can't go with --path, --line.")
        self.assertEqual(self.cli("issue", "comment", "2", "--edit", "1", "--body", "B"),
                         (1, "", "agent-desk: Comment 1 is on #1, not #2."))
        self.assertEqual(self.cli("issue", "comment", "1", "--edit", "1"),
                         (1, "", "agent-desk: A comment needs a body."))
        self.assertEqual(self.cli("issue", "comment", "1", "--body", "B", "--note", "Why"),
                         (1, "", "agent-desk: --note says what an --edit changed, so it goes only with --edit."))
        self.assertEqual(self.store.last_event(), last)

    def test_a_create_whose_link_fails_files_nothing(self):
        self.cli("issue", "create", "--title", "Parent")
        last = self.store.last_event()
        status, _, err = self.cli("issue", "create", "--title", "Child", "--label", "serial", "--parent", "1",
                                  "--blocked-by", "9")
        self.assertEqual((status, err), (1, "agent-desk: org/game has no item #9."))
        self.assertEqual((self.store.last_event(), json.loads(self.cli("issue", "list", "--json", "number")[1])),
                         (last, [{"number": 1}]))
        self.assertEqual(self.cli("issue", "create", "--title", "Child", "--parent", "1", "--blocked-by", "1"),
                         (0, "2", ""))
        child = self.store.item(REPO, 2)
        self.assertEqual((child["parent"]["number"], [one["number"] for one in child["blockedBy"]]), (1, [1]))

    def test_an_issue_command_refuses_a_review_and_a_missing_item(self):
        self.cli("review", "create", "--branch", "agent/x", "--title", "A review")
        status, _, err = self.cli("issue", "close", "1")
        self.assertEqual((status, err), (1, "agent-desk: org/game has no issue #1."))
        self.assertEqual(self.cli("review", "view", "agent/none")[0], 1)
        self.assertEqual(self.store.item(REPO, 1)["state"], "OPEN")

    def test_the_plain_output_reads_without_json(self):
        self.cli("issue", "create", "--title", "Add a store", "--body", "Why", "--label", "idea")
        self.cli("issue", "comment", "1", "--body", "A note")
        out = self.cli("issue", "view", "1")[1]
        self.assertIn("#1 Add a store\nOPEN (open)  labels: idea\n\nWhy\n\n-- you (comment 1) at ", out)
        self.assertEqual(self.cli("issue", "list")[1], "1\topen\tAdd a store\tidea")

    def test_a_label_takes_a_color_that_the_list_prints(self):
        last = self.store.last_event()
        self.assertEqual(self.cli("label", "edit", "serial:web", "--color", "#5319E7"), (0, "", ""))
        self.assertEqual(self.cli("label", "create", "bug", "-c", "d73a4a"), (0, "", ""))
        self.assertEqual(self.cli("label", "edit", "bug", "--color", "00ff00"), (0, "", ""))
        self.assertEqual(self.cli("label", "list"), (0, "bug\t#00ff00\nserial:web\t#5319e7", ""))
        self.assertEqual(self.store.label_colors("org/other"), {})
        self.assertEqual(self.events(last), [(None, "label-colored")] * 3)
        self.assertFalse(self.store.set_label_color(REPO, "bug", "00FF00"))
        self.assertEqual(self.store.last_event(), last + 3)

    def test_a_label_color_must_be_six_hex_digits(self):
        for color in ("red", "#abc", "12345g"):
            status, _, err = self.cli("label", "edit", "bug", "--color", color)
            self.assertEqual(status, 1, color)
            self.assertIn("isn't a color", err)
        self.assertEqual(self.store.label_colors(REPO), {})

    def test_the_project_takes_its_checkout_from_the_projects_file(self):
        store.PROJECTS.write_text(f"org/other /elsewhere\n{REPO} /home/me/game\n")
        self.cli("issue", "create", "--title", "One")
        row = self.store.db.execute("SELECT path, next_number FROM projects WHERE slug = ?", (REPO,)).fetchone()
        self.assertEqual((row["path"], row["next_number"]), ("/home/me/game", 2))


    def test_project_list_prints_each_project_and_its_checkout_in_the_files_order(self):
        store.PROJECTS.write_text(f"org/other /elsewhere\n{REPO} /home/me/my game\nno-checkout\n")
        self.assertEqual(self.cli("project", "list"), (0, f"org/other /elsewhere\n{REPO} /home/me/my game", ""))
        status, out, err = self.cli("project", "list", "--json")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(json.loads(out), [{"repo": "org/other", "checkout": "/elsewhere"},
                                           {"repo": REPO, "checkout": "/home/me/my game"}])

    def test_project_list_of_a_missing_or_empty_projects_file_prints_nothing_but_an_empty_json_list(self):
        self.assertFalse(store.PROJECTS.exists())
        self.assertEqual(self.cli("project", "list"), (0, "", ""))
        self.assertEqual(self.cli("project", "list", "--json"), (0, "[]", ""))
        store.PROJECTS.write_text("")
        self.assertEqual(self.cli("project", "list"), (0, "", ""))

    def test_project_list_runs_from_bin_agent_desk_without_opening_a_store(self):
        config = Path(self.folder.name) / "config"
        (config / "agent-desk").mkdir(parents=True)
        (config / "agent-desk" / "projects").write_text(f"{REPO} /home/me/game\n")
        state = Path(self.folder.name) / "untouched-state"
        env = dict(os.environ, XDG_CONFIG_HOME=str(config), XDG_STATE_HOME=str(state))
        done = subprocess.run([str(LIB.parent / "bin" / "agent-desk"), "project", "list"], env=env,
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, f"{REPO} /home/me/game\n", ""))
        self.assertFalse(state.exists())


class ReviewDiffTest(StoreCase):
    """`review diff` and the size fields, read from git in a main checkout the projects file records."""

    def setUp(self):
        super().setUp()
        root = Path(self.folder.name)
        self.checkout = root / "repo"
        (root / "gitconfig").write_text("[user]\n\tname = Tester\n\temail = t@example.com\n"
                                        "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(root / "gitconfig"),
                                             "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        self.addCleanup(patch.stop)
        self.git("init", "-q", str(self.checkout), here=False)
        (self.checkout / "keep.txt").write_text("one\ntwo\n")
        self.git("add", "keep.txt")
        self.git("commit", "-q", "-m", "start")
        store.PROJECTS.write_text(f"{REPO} {self.checkout}\n")
        # `review create` records the branch's head from git where it runs.
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.checkout)

    def git(self, *args, here=True):
        return subprocess.run(["git", *(("-C", str(self.checkout)) if here else ()), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def branch(self, name, **files):
        """Commits `files` on a new branch `name` cut from main, and returns to main."""
        self.git("checkout", "-q", "-b", name, "main")
        for path, text in files.items():
            (self.checkout / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        self.git("checkout", "-q", "main")

    def test_an_open_review_diffs_its_branch_against_where_it_left_its_base(self):
        self.branch("agent/issue-1", **{"keep.txt": "one\n2\nthree\n", "new.txt": "a\n"})
        # A later commit on main stays out of the review's diff, as gh pr diff leaves it out.
        (self.checkout / "main.txt").write_text("main\n")
        self.git("add", "main.txt")
        self.git("commit", "-q", "-m", "main moves")
        self.cli("review", "create", "--branch", "agent/issue-1", "--title", "One")
        status, out, _ = self.cli("review", "diff", "agent/issue-1")
        self.assertEqual(status, 0)
        self.assertIn("+++ b/new.txt", out)
        self.assertIn("-two\n+2\n+three", out)
        self.assertNotIn("main.txt", out)
        shaped = json.loads(self.cli("review", "view", "1", "--json", "mergedAt,additions,deletions,changedFiles")[1])
        self.assertEqual(shaped, {"mergedAt": None, "additions": 3, "deletions": 1, "changedFiles": 2})

    def test_a_merged_review_diffs_its_merge_commit_and_lists_by_merge_time(self):
        self.branch("agent/issue-1", **{"one.txt": "a\nb\n"})
        self.branch("agent/issue-2", **{"two.txt": "c\n"})
        for number, branch in ((1, "agent/issue-1"), (2, "agent/issue-2")):
            self.cli("review", "create", "--branch", branch, "--title", f"Review {number}")
            self.git("merge", "-q", "--no-ff", "-m", f"Merge {branch}", branch)
            # The branch moves on after its merge, which the merged review's diff leaves out.
            self.git("branch", "-f", branch, "main~1")
            with mock.patch.object(store, "now", return_value=f"2026-11-1{number}T12:00:00Z"):
                self.assertEqual(self.cli("review", "merge", str(number), "--sha", self.git("rev-parse", "main"))[0],
                                 0)
        status, out, _ = self.cli("review", "diff", "1")
        self.assertEqual(status, 0)
        self.assertIn("+++ b/one.txt", out)
        self.assertNotIn("two.txt", out)

        status, out, _ = self.cli("review", "list", "--merged-since", "2026-11-12T00:00:00Z", "--json",
                                  "number,mergedAt,additions,deletions,changedFiles")
        self.assertEqual((status, json.loads(out)), (0, [{"number": 2, "mergedAt": "2026-11-12T12:00:00Z",
                                                          "additions": 1, "deletions": 0, "changedFiles": 1}]))
        self.assertEqual(json.loads(self.cli("review", "list", "--merged-since", "2026-11-11", "--json", "number")[1]),
                         [{"number": 2}, {"number": 1}])
        # --since still filters on creation, which both reviews passed.
        self.assertEqual(json.loads(self.cli("review", "list", "--state", "merged", "--since", "2026-11-12",
                                             "--json", "number")[1]), [{"number": 2}, {"number": 1}])
        status, _, err = self.cli("review", "list", "--merged-since", "2026-11-11", "--state", "open")
        self.assertEqual(status, 1)
        self.assertIn("can't go with --state open", err)

    def test_a_review_whose_branch_is_gone_diffs_its_recorded_head_or_says_why_it_cant(self):
        self.branch("agent/issue-1", **{"one.txt": "a\n"})
        self.cli("review", "create", "--branch", "agent/issue-1", "--title", "One")
        self.cli("review", "create", "--branch", "agent/issue-9", "--title", "Never pushed")
        self.git("branch", "-D", "agent/issue-1")
        self.assertIn("+++ b/one.txt", self.cli("review", "diff", "1")[1])
        status, _, err = self.cli("review", "diff", "2")
        self.assertEqual(status, 1)
        self.assertIn("The branch agent/issue-9 is gone", err)
        self.assertEqual(json.loads(self.cli("review", "view", "2", "--json", "additions,changedFiles")[1]),
                         {"additions": None, "changedFiles": None})


class ScriptTest(StoreCase):
    def test_store_py_run_as_a_script_still_runs_the_command_line(self):
        """A web server started before the command line moved runs lib/store.py, which hands it to store_cli.py."""
        env = {**os.environ, "XDG_STATE_HOME": self.folder.name}

        def run(script, *argv):
            return subprocess.run([sys.executable, "-B", str(LIB / script), *argv], env=env, capture_output=True,
                                  text=True, check=True).stdout.strip()

        self.assertEqual(run("store.py", "issue", "create", "--title", "One"), "1")
        self.assertEqual(json.loads(run("store_cli.py", "issue", "view", "1", "--json", "title")), {"title": "One"})


if __name__ == "__main__":
    unittest.main()
