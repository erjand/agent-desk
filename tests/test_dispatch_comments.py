"""Tests for agent-desk/lib/dispatch.py's runs that comments sent to the agent ask for."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch  # noqa: E402
import import_github  # noqa: E402
import store  # noqa: E402
from dispatch_case import REPO, DispatchCase  # noqa: E402
from test_import import FakeGitHub, recorded  # noqa: E402


class ToAgentTest(DispatchCase):
    """A comment sent to the agent asks for the run the agent workflow's @agent asked for."""

    def send(self, number, body="Please look again", agent=False, **line):
        environ = {"AGENT_DESK_RUN": "9"} if agent else {}
        with mock.patch.dict(os.environ, environ):
            return self.store.comment(REPO, number, body, to_agent=True, **line)

    def requests(self):
        return [(event["payload"]["target"], event["payload"]["benchmark"], event["payload"]["env"])
                for event in self.store.events_since(0) if event["type"] == "run-requested"]

    def test_an_issue_comment_starts_the_issues_agent(self):
        issue = self.store.create(REPO, "issue", "Paint the fence", labels=["benchmark"])
        self.send(issue)
        self.dispatcher.tick()
        self.assertEqual(self.requests(), [(str(issue), True, {"EVENT": "issue_comment"})])
        self.assertEqual(self.systemd.commands(), [["work-issue", str(issue)]])

    def test_a_comment_on_an_idea_asks_for_a_discussion_that_holds_the_issues_target(self):
        idea = self.store.create(REPO, "issue", "Maybe a map", labels=["idea", "benchmark", "serial:web"])
        self.send(idea, "@agent what would this take?")
        self.dispatcher.tick()
        # A discussion runs under its own limit, whatever the benchmark label says.
        self.assertEqual(self.requests(), [(f"idea-{idea}", False, {})])
        self.assertEqual(self.systemd.commands(), [["discuss-idea", str(idea)]])
        self.assertIn(f"RuntimeMaxSec={dispatch.DISCUSS_MINUTES}min", self.systemd.properties())
        self.assertEqual(self.memory.asked[-1][1], "discuss")
        # It takes an agent slot and the issue's target, and its serial label holds nothing back.
        self.assertEqual(dispatch.lock_of({"project": REPO, "kind": "agent", "target": f"idea-{idea}"}),
                         (REPO, f"agent-{idea}"))
        self.assertEqual(self.store.item(REPO, idea, "issue")["status"], "open")
        # A second comment while it works waits behind it, so two discussions of one idea never run at once.
        self.send(idea, "And the legend?")
        self.dispatcher.tick()
        self.assertEqual(len(self.systemd.started), 1)
        self.assertTrue(any(f"another run holds agent-{idea}" in line for line in self.said), self.said)

    def test_a_comment_on_an_idea_an_agent_works_asks_for_a_work_issue_run(self):
        queued = self.store.create(REPO, "issue", "Queued idea", labels=["idea"])
        self.store.set_state(REPO, queued, "in-progress")
        self.send(queued)
        self.dispatcher.tick()
        self.assertEqual(self.requests(), [(str(queued), False, {"EVENT": "issue_comment"})])
        self.assertEqual(self.systemd.commands(), [["work-issue", str(queued)]])

    def test_a_review_from_an_issue_branch_works_the_issue_and_replies_on_the_review(self):
        issue = self.store.create(REPO, "issue", "Paint the fence", labels=["benchmark"])
        review = self.store.create(REPO, "review", "Paint it", branch=f"agent/issue-{issue}")
        self.send(review, "Use blue", path="fence.txt", line=3)
        self.dispatcher.tick()
        self.assertEqual(self.requests(), [(str(issue), True, {"EVENT": "issue_comment", "REPLY_PR": str(review)})])
        self.assertEqual(self.systemd.commands(), [["work-issue", str(issue)]])
        self.assertEqual(self.systemd.env()["REPLY_PR"], str(review))

    def test_a_review_from_another_agent_branch_goes_back_to_that_review_with_the_comment(self):
        review = self.store.create(REPO, "review", "Trim comments", branch="agent/chore-comment-one-3")
        self.send(review, "Keep the license header.")
        self.dispatcher.tick()
        # The store puts the mention in front of a comment sent to the agent, so the request carries it.
        self.assertEqual(self.requests(), [(f"pr-{review}", False, {"REQUEST": "@agent Keep the license header."})])
        self.assertEqual(self.systemd.commands(), [["work-pr", str(review)]])

    def test_a_review_from_a_branch_no_agent_works_gets_a_note_and_no_run(self):
        review = self.store.create(REPO, "review", "By hand", branch="feature/fence")
        self.send(review)
        self.dispatcher.tick()
        self.assertEqual(self.requests(), [])
        note = self.store.item(REPO, review)["comments"][-1]
        self.assertEqual(note["author"]["login"], "you")
        self.assertIn("this one is from `feature/fence`", note["body"])
        self.assertFalse(note["toAgent"])

    def test_a_comment_an_agent_sent_or_one_not_sent_to_the_agent_asks_for_nothing(self):
        issue = self.store.create(REPO, "issue", "Paint the fence")
        made = self.send(issue, agent=True)
        self.store.comment(REPO, issue, "Just a note")
        self.dispatcher.tick()
        self.assertEqual(self.requests(), [])
        self.assertIn(f"Ignored comment {made} on #{issue} in {REPO}: an agent sent it to the agent.", self.said)

    def test_an_edit_of_a_comment_asks_for_nothing_even_when_it_adds_the_mention(self):
        issue = self.store.create(REPO, "issue", "Paint the fence")
        made = self.store.comment(REPO, issue, "Just a note")
        self.dispatcher.tick()
        self.store.edit_comment(REPO, made, "@agent paint it blue")
        self.dispatcher.tick()
        self.assertEqual(self.requests(), [])
        self.assertFalse(any(event["payload"].get("comment") == made for event in self.store.events_since(0)
                             if event["type"] == "comment-answered"))
        self.assertEqual(self.store.agent_comments(), [])
        self.assertEqual(self.store.board(REPO)["asked"], [])

    def answers(self):
        return [(event["payload"]["comment"], event["payload"]["answer"])
                for event in self.store.events_since(0) if event["type"] == "comment-answered"]

    def test_each_answer_is_recorded_with_the_comments_event(self):
        issue = self.store.create(REPO, "issue", "Paint the fence")
        review = self.store.create(REPO, "review", "By hand", branch="feature/fence")
        ran, noted, ignored = self.send(issue), self.send(review), self.send(issue, agent=True)
        self.dispatcher.tick()
        self.assertEqual(self.answers(), [(None, "start"), (ran, "run"), (noted, "note"), (ignored, "ignored")])
        [asked] = [event for event in self.store.events_since(0) if event["type"] == "run-requested"]
        answered = [event["payload"] for event in self.store.events_since(0) if event["type"] == "comment-answered"]
        self.assertEqual(answered[1]["run"], asked["payload"]["run"])
        self.assertEqual([one["added"] for one in answered[1:]],
                         [event["id"] for event in self.store.agent_comments()])

    def test_the_board_lists_each_open_review_asked_until_the_dispatcher_answers_its_comment(self):
        issue = self.store.create(REPO, "issue", "Paint the fence")
        mine = self.store.create(REPO, "review", "Paint it", branch=f"agent/issue-{issue}")
        by_hand = self.store.create(REPO, "review", "By hand", branch="feature/fence")
        by_agent = self.store.create(REPO, "review", "Trim comments", branch="agent/chore-comment-one-3")
        closed = self.store.create(REPO, "review", "Dropped", branch="agent/issue-99")
        self.send(issue)
        self.send(mine)
        self.send(mine, "And the gate")
        self.send(by_hand)
        self.send(by_agent, agent=True)
        self.send(closed)
        self.store.set_state(REPO, closed, "closed")
        # An issue's comment and an agent's own leave no review asked, and a closed review leaves the board.
        self.assertEqual(self.store.board(REPO)["asked"], [mine, by_hand])
        # The answers, a run for the agent branch's review and a note for the other, take both off the list.
        self.dispatcher.tick()
        self.assertEqual(self.store.board(REPO)["asked"], [])
        self.send(by_hand)
        self.assertEqual(self.store.board(REPO)["asked"], [by_hand])

    def test_a_comment_sent_while_the_dispatcher_is_down_asks_for_its_run_on_restart(self):
        issue = self.store.create(REPO, "issue", "Paint the fence")
        self.send(issue)
        self.dispatcher.tick()
        # The dispatcher stops, and a comment comes for another issue before it starts again.
        later = self.store.create(REPO, "issue", "Mend the gate")
        self.send(later)
        restarted = self.new_dispatcher()
        self.assertEqual([target for target, _, _ in self.requests()], [str(issue), str(later)])
        restarted.tick()
        self.time += dispatch.RECONCILE_SECONDS
        restarted.tick()
        # Another restart asks for neither again.
        self.new_dispatcher().tick()
        self.assertEqual(len(self.requests()), 2)

    def test_a_comment_the_tick_and_the_reconcile_both_see_asks_for_one_run(self):
        issue = self.store.create(REPO, "issue", "Paint the fence")
        self.send(issue)
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(self.requests(), [(str(issue), False, {"EVENT": "issue_comment"})])

    def test_an_answer_that_fails_to_write_is_tried_again_in_order_on_the_next_reconcile(self):
        first = self.store.create(REPO, "issue", "Paint the fence")
        second = self.store.create(REPO, "issue", "Mend the gate")
        self.send(first)
        with mock.patch.object(self.store, "request_run", side_effect=store.StoreError("busy")):
            with self.assertRaises(store.StoreError):
                self.dispatcher.tick()
        self.assertEqual(self.requests(), [])
        self.send(second)
        self.dispatcher.tick()
        self.assertEqual([target for target, _, _ in self.requests()], [str(first), str(second)])
        self.time += dispatch.RECONCILE_SECONDS
        self.dispatcher.tick()
        self.assertEqual(len(self.requests()), 2)

    def test_the_first_start_on_a_store_with_no_answer_asks_for_nothing_for_older_comments(self):
        issue = self.store.create(REPO, "issue", "Paint the fence")
        self.store.db.execute("DELETE FROM events WHERE type = 'comment-answered'")
        self.send(issue)
        restarted = self.new_dispatcher()
        restarted.tick()
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.answers(), [(None, "start")])
        # Its record holds through the next restart, so a comment sent while it is down still asks for a run.
        self.send(issue, "Use blue")
        self.new_dispatcher()
        self.assertEqual(self.requests(), [(str(issue), False, {"EVENT": "issue_comment"})])
        self.assertEqual(self.answers()[-1][1], "run")

    def import_github(self):
        """Imports the recorded GitHub project, whose issue #2 has a comment sent to the agent."""
        with mock.patch.object(import_github, "paginate", FakeGitHub(recorded())):
            self.assertIsNone(import_github.Importer(self.store, REPO).run())
        self.assertTrue(any(comment["toAgent"] for comment in self.store.item(REPO, 2)["comments"]))

    def test_an_import_of_comments_sent_to_the_agent_asks_for_no_run_on_the_first_start(self):
        self.import_github()
        self.new_dispatcher().tick()
        self.assertEqual(self.requests(), [])

    def test_an_import_of_comments_sent_to_the_agent_asks_for_no_run_after_a_restart(self):
        self.dispatcher.tick()
        self.import_github()
        self.dispatcher.tick()
        self.new_dispatcher().tick()
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.answers(), [(None, "start")])


if __name__ == "__main__":
    unittest.main()
