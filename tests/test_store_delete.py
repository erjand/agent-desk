"""Tests for agent-desk/lib/store_delete.py, the delete of an issue filed by mistake, and `agent-desk issue delete`."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hold  # noqa: E402
import store  # noqa: E402
from dispatch_case import DispatchCase  # noqa: E402
from test_store import REPO, StoreCase  # noqa: E402


def git(checkout, *args):
    subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   check=True, capture_output=True)


class DeleteCase(StoreCase):
    """A store whose project has a main checkout in a git repository of its own, which the projects file records."""

    def setUp(self):
        super().setUp()
        self.checkout = Path(self.folder.name) / "game"
        self.checkout.mkdir()
        git(self.checkout, "init", "-q", "-b", "main")
        git(self.checkout, "commit", "-q", "--allow-empty", "-m", "Start")
        (Path(self.folder.name) / "projects").write_text(f"{REPO} {self.checkout}\n")
        self.store.project(REPO, str(self.checkout))
        for patch in (mock.patch.object(hold, "HOLD", Path(self.folder.name) / "hold.json"),
                      mock.patch.object(hold, "LOCK", Path(self.folder.name) / "hold.lock")):
            patch.start()
            self.addCleanup(patch.stop)

    def issue(self, title="Work", **fields):
        return self.store.create(REPO, "issue", title, **fields)

    def refused(self, number, said):
        """Asserts that the delete of `number` refuses, naming `said`, and changes nothing."""
        last = self.store.last_event()
        with self.assertRaises(store.StoreError) as raised:
            self.store.delete_issue(REPO, number)
        self.assertIn(f"#{number} has {said}, so it can't be deleted.", str(raised.exception))
        self.assertIn(f"Close it instead with `agent-desk issue close {number} --reason not-planned`.",
                      str(raised.exception))
        self.assertEqual(self.store.last_event(), last)
        self.assertEqual(self.store.item(REPO, number)["number"], number)


class DeleteTest(DeleteCase):
    def test_a_delete_removes_the_issue_with_its_labels_comments_and_links_both_ways(self):
        parent = self.issue("Parent")
        blocker = self.issue("Blocker")
        number = self.issue("Mistake", labels=["bug", "serial:web"], parent=parent, blocked_by=[blocker])
        blocked = self.issue("Blocked", blocked_by=[number])
        self.store.comment(REPO, number, "A note")
        self.store.comment(REPO, number, "Another")
        kept = self.store.comment(REPO, blocked, "Stays")
        last = self.store.last_event()
        plan = self.store.delete_issue(REPO, number)
        self.assertEqual(plan, {"number": number, "title": "Mistake", "comments": 2, "labels": ["bug", "serial:web"],
                                "links": [("blocks", blocker, False), ("blocks", blocked, True),
                                          ("parent", parent, True)], "requests": []})
        with self.assertRaises(store.StoreError):
            self.store.item(REPO, number)
        for table in ("labels", "comments"):
            self.assertEqual(self.store.db.execute(f"SELECT COUNT(*) FROM {table} WHERE number = ?",
                                                   (number,)).fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM links WHERE from_number = ? OR to_number = ?",
                                               (number, number)).fetchone()[0], 0)
        self.assertEqual(self.store.item(REPO, blocked)["blockedBy"], [])
        self.assertEqual(self.store.item(REPO, parent)["subIssues"], [])
        self.assertEqual(self.store.item(REPO, blocker)["blocking"], [])
        self.assertEqual([one["id"] for one in self.store.item(REPO, blocked)["comments"]], [kept])
        self.assertEqual(self.events(last), [(blocker, "link-removed"), (number, "link-removed"),
                                             (number, "link-removed"), (number, "item-deleted")])
        deleted = self.store.events_since(self.store.last_event() - 1)[0]
        self.assertEqual(deleted["payload"], {"kind": "issue", "title": "Mistake", "comments": 2,
                                              "labels": ["bug", "serial:web"]})
        self.assertNotIn(number, [one["number"] for one in self.store.items(REPO, "issue", state="all")])
        self.assertEqual([one["number"] for one in self.store.board(REPO)["issues"]], [parent, blocker, blocked])

    def test_the_number_is_never_used_again(self):
        first = self.issue()
        number = self.issue()
        self.store.delete_issue(REPO, number)
        self.assertEqual(self.store.db.execute("SELECT next_number FROM projects WHERE slug = ?",
                                               (REPO,)).fetchone()[0], number + 1)
        self.assertEqual(self.issue("Next"), number + 1)
        self.store.delete_issue(REPO, first)
        self.assertEqual(self.issue("After"), number + 2)

    def test_a_delete_cancels_the_agent_requests_waiting_for_the_issue_and_leaves_the_rest(self):
        number = self.issue(labels=["agent-ready"])
        waiting = [self.store.request_run(REPO, "agent", str(number), env={"EVENT": "issues"}),
                   self.store.request_run(REPO, "agent", f"idea-{number}")]
        others = [self.store.request_run(REPO, "agent", str(number + 1)),
                  self.store.request_run(REPO, "chore", "audit"),
                  self.store.request_run("org/other", "agent", str(number))]
        self.assertEqual(self.store.deletion(REPO, number)["requests"], waiting)
        self.assertEqual(self.store.delete_issue(REPO, number)["requests"], waiting)
        self.assertEqual([self.store.run(run)["status"] for run in waiting], ["cancelled", "cancelled"])
        self.assertEqual([self.store.run(run)["status"] for run in others], ["requested"] * 3)

    def test_the_store_refuses_an_issue_with_a_review(self):
        fixed = self.issue()
        review = self.store.create(REPO, "review", "Fix", branch="agent/other", fixes=[fixed])
        self.refused(fixed, f"review #{review}")
        branch = self.issue()
        review = self.store.create(REPO, "review", "Work", branch=f"agent/issue-{branch}")
        self.store.set_state(REPO, review, "closed")
        self.refused(branch, f"review #{review}")

    def test_the_store_refuses_an_issue_with_a_branch_or_a_worktree(self):
        branch = self.issue()
        git(self.checkout, "branch", f"agent/issue-{branch}")
        self.refused(branch, f"the branch agent/issue-{branch}")
        worktree = self.issue()
        beside = Path(f"{self.checkout}-issue-{worktree}")
        git(self.checkout, "worktree", "add", "-q", "-b", f"agent/issue-{worktree}", str(beside))
        self.refused(worktree, f"the branch agent/issue-{worktree}; a worktree at {beside}")
        git(self.checkout, "worktree", "remove", "--force", str(beside))
        git(self.checkout, "branch", "-D", f"agent/issue-{worktree}")
        beside.mkdir()
        self.refused(worktree, f"a worktree at {beside}")

    def test_the_store_refuses_an_issue_an_agent_run_works(self):
        number = self.issue()
        run = self.store.request_run(REPO, "agent", str(number))
        self.store.run_started(run, "agent-desk-agent-game-1")
        waiting = self.store.request_run(REPO, "agent", str(number), env={"REQUEST": "b"})
        self.refused(number, "an agent run working it in agent-desk-agent-game-1")
        self.assertEqual(self.store.run(waiting)["status"], "requested")
        self.store.run_ended(run, "failed")
        self.store.delete_issue(REPO, number)
        self.assertEqual(self.store.run(waiting)["status"], "cancelled")

    def test_the_store_refuses_an_issue_with_sub_issues(self):
        parent = self.issue("Parent")
        subs = [self.issue("One", parent=parent), self.issue("Two", parent=parent)]
        self.store.set_state(REPO, subs[0], "closed")
        self.refused(parent, f"sub-issues #{subs[0]}, #{subs[1]}")

    def test_the_store_names_every_reason_and_refuses_a_review(self):
        parent = self.issue("Parent")
        self.issue("One", parent=parent)
        git(self.checkout, "branch", f"agent/issue-{parent}")
        self.refused(parent, f"the branch agent/issue-{parent}; sub-issue #{parent + 1}")
        review = self.store.create(REPO, "review", "Work", branch="agent/x")
        with self.assertRaises(store.StoreError) as raised:
            self.store.delete_issue(REPO, review)
        self.assertEqual(str(raised.exception), f"{REPO} has no issue #{review}.")


class DeleteCommandTest(DeleteCase):
    def test_without_yes_the_command_prints_what_it_would_remove_and_changes_nothing(self):
        blocker = self.issue("Blocker")
        number = self.issue("Mistake", labels=["bug"], blocked_by=[blocker])
        self.store.comment(REPO, number, "A note")
        self.store.request_run(REPO, "agent", str(number))
        last = self.store.last_event()
        status, out, err = self.cli("issue", "delete", str(number))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, f"Would delete #{number} \"Mistake\" with 1 comment, the label bug, and the link blocked by "
                              f"#{blocker}, cancelling 1 waiting agent request. Nothing brings it back, and its number "
                              "is never used again. Run it again with --yes to delete it.")
        self.assertEqual(self.store.last_event(), last)

    def test_the_command_names_a_duplicate_link_in_either_direction(self):
        original = self.issue("Original")
        number = self.issue("Copy")
        self.store.link(REPO, number, original, "duplicate")
        for one, said in ((number, f"duplicate of #{original}"), (original, f"duplicated by #{number}")):
            self.assertIn(f"the link {said}.", self.cli("issue", "delete", str(one))[1])

    def test_with_yes_the_command_deletes_the_issue_and_drops_its_manual_pick(self):
        number = self.issue("Mistake")
        other = self.issue("Other")
        hold.update(lambda data: data.update(picked=[f"{REPO}#{number}", f"{REPO}#{other}"]))
        self.assertEqual(self.cli("issue", "delete", f"#{number}", "--yes"),
                         (0, f"Deleted #{number} \"Mistake\" with 0 comments.", ""))
        self.assertEqual(hold.status()["picked"], [f"{REPO}#{other}"])
        status, out, err = self.cli("issue", "view", str(number))
        self.assertEqual((status, out), (1, ""))
        self.assertIn(f"has no issue #{number}", err)
        _, out, _ = self.cli("issue", "list", "--state", "all", "--json", "number")
        self.assertEqual(json.loads(out), [{"number": other}])

    def test_the_command_refuses_with_the_store_s_reason(self):
        parent = self.issue("Parent")
        self.issue("One", parent=parent)
        for argv in (("issue", "delete", str(parent)), ("issue", "delete", str(parent), "--yes")):
            status, out, err = self.cli(*argv)
            self.assertEqual((status, out), (1, ""))
            self.assertIn(f"#{parent} has sub-issue #{parent + 1}, so it can't be deleted. Close it instead", err)


class DeleteDispatchTest(DispatchCase):
    def test_deleting_a_blocker_starts_the_issue_it_held(self):
        blocker = self.store.create(REPO, "issue", "Mistake")
        number = self.store.create(REPO, "issue", "Work", labels=["agent-ready"])
        self.store.link(REPO, blocker, number, "blocks")
        run = self.request("agent", str(number), env={"EVENT": "issues"})
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "requested")
        self.store.delete_issue(REPO, blocker)
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "running")

    def test_the_dispatcher_drops_a_deleted_issue_s_request_and_the_wake_asks_for_no_run(self):
        number = self.store.create(REPO, "issue", "Mistake", labels=["agent-ready"])
        run = self.request("agent", str(number), env={"EVENT": "issues"})
        self.hold = {"state": "manual", "picked": []}
        self.dispatcher.tick()
        self.assertEqual(self.status(run), "requested")
        self.store.delete_issue(REPO, number)
        self.dispatcher.tick()
        self.dispatcher.wake()
        self.assertNotIn(run, self.dispatcher.pending)
        self.assertEqual([one for one in self.store.runs(("requested", "running")) if one["target"] == str(number)],
                         [])


if __name__ == "__main__":
    unittest.main()
