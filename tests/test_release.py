"""Tests for agent-desk/lib/release.py, which hands an issue back when its agent run ends without a report."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import release  # noqa: E402
import store  # noqa: E402

REPO = "org/game"
UNIT = "agent-desk-agent-org-game-7-3.service"
SIGNED = "\n\n<sub>Posted by agent-desk in agent-desk-agent-org-game-7-3</sub>"
EARLIER = "agent-desk-agent-org-game-7-2.service"
RUN_VARIABLES = ("EVENT", "REPLY_PR", "RUN_LOG", "AGENT_DESK_UNIT")


def log(unit=UNIT):
    """How a note links the log of `unit`, as release.run_link and desk_run_link word it."""
    return f"(log: `journalctl --user -u {unit}`)"


STARTED = f"An agent started on `agent/issue-7` {log()}. To take over, run `claude --resume x` in `/w`."


def plan(outcome="failure", status="in-progress", comments=(), **options):
    options.setdefault("event", "issues")
    return release.plan("7", outcome, status, list(comments), UNIT, **options)


class PlanTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"AGENT_DESK_UNIT": UNIT})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_failure_before_the_agent_started_puts_agent_ready_back_with_the_last_error(self):
        steps = plan(error="GraphQL: API rate limit already exceeded")
        self.assertTrue(steps["free"])
        self.assertTrue(steps["ready"])
        [(number, body)] = steps["notes"]
        self.assertEqual(number, "7")
        self.assertTrue(body.startswith(f"The agent run failed {log()}. Its last line: `GraphQL: API rate "
                                        "limit already exceeded`"))
        self.assertIn("agent-desk put `agent-ready` back", body)
        # The store records the note's author, so it carries no hidden mark.
        self.assertTrue(body.endswith(SIGNED))

    def test_a_note_outside_a_unit_names_no_unit_and_links_no_log(self):
        os.environ.pop("AGENT_DESK_UNIT")
        self.assertEqual(release.mark(), "\n\n<sub>Posted by agent-desk</sub>")
        self.assertEqual(release.run_link(""), "")

    def test_a_failure_after_the_agent_started_leaves_the_issue_for_the_user(self):
        steps = plan(comments=[STARTED])
        self.assertTrue(steps["free"])
        self.assertFalse(steps["ready"])
        self.assertIn("Label the issue `agent-ready` to run it again.", steps["notes"][0][1])

    def test_a_second_failure_in_a_row_before_the_agent_started_puts_nothing_back(self):
        first = plan()["notes"][0][1]
        steps = plan(comments=[first])
        self.assertFalse(steps["ready"])
        self.assertTrue(steps["free"])

    def test_a_failure_after_an_earlier_run_worked_retries_again(self):
        finished = f"The agent finished: review #8 {log(EARLIER)}."
        self.assertTrue(plan(comments=[plan()["notes"][0][1], finished])["ready"])

    def test_a_run_that_reported_its_outcome_has_nothing_to_release(self):
        for report in ("The agent finished: x", "The agent stopped without opening a PR",
                       "The agent didn't start: the worktree setup, `just build`, failed"):
            self.assertIsNone(plan(comments=[STARTED, f"{report} {log()}."]), report)
        # A report from an earlier run doesn't count for this one.
        self.assertIsNotNone(plan(comments=[f"The agent stopped without opening a PR {log(EARLIER)}."]))

    def test_a_run_without_a_link_takes_no_report_or_start_as_its_own(self):
        comments = ["An agent started on `agent/issue-7`.", "The agent stopped without opening a PR."]
        steps = release.plan("7", "failure", "in-progress", comments, "", event="issues")
        self.assertTrue(steps["free"])
        self.assertTrue(steps["ready"])

    def test_an_issue_that_waits_on_a_decision_has_nothing_to_release(self):
        self.assertIsNone(plan(status="needs-design"))

    def test_an_open_review_keeps_in_progress(self):
        steps = plan(open_pr=True)
        self.assertFalse(steps["free"])
        self.assertFalse(steps["ready"])
        self.assertNotIn("in-progress", steps["notes"][0][1])

    def test_a_run_from_a_comment_or_the_pr_never_retries(self):
        self.assertFalse(plan(event="issue_comment")["ready"])
        steps = plan(reply_pr="8")
        self.assertFalse(steps["ready"])
        self.assertEqual([number for number, _ in steps["notes"]], ["8", "7"])
        self.assertTrue(steps["notes"][1][1].startswith(f"The agent run on feedback from #8 failed {log()}."))

    def test_a_failure_before_the_claim_leaves_the_state(self):
        steps = plan(status="queued")
        self.assertFalse(steps["free"])
        self.assertFalse(steps["ready"])

    def test_a_cancelled_run_takes_in_progress_off_and_says_so_as_before(self):
        steps = plan("cancelled")
        self.assertTrue(steps["free"])
        self.assertFalse(steps["ready"])
        self.assertTrue(steps["notes"][0][1].startswith(f"The agent run was cancelled or timed out {log()}."))
        steps = plan("cancelled", reply_pr="8")
        self.assertEqual(steps["notes"][1][1].split("\n")[0],
                         "The agent run on feedback from #8 was cancelled or timed out.")

    def test_a_paused_run_leaves_its_issue_on_hold_and_notes_it_cut_off(self):
        # Pause moves the issue to on-hold before it stops the unit, so the release keeps the state and its note tells
        # work-issue.sh to take up the session on Resume.
        steps = plan("cancelled", status="on-hold")
        self.assertEqual((steps["free"], steps["ready"]), (False, False))
        self.assertIn("cancelled", steps["notes"][0][1].split("(")[0])

    def test_a_failure_note_never_reads_as_a_cut_off_run(self):
        # work-issue.sh takes up a cut-off run's session when its last note says "cancelled" before its first "(".
        body = plan(error="the job was cancelled")["notes"][0][1]
        self.assertNotIn("cancelled", body.split("(")[0])


class LastLineTest(unittest.TestCase):
    def test_it_takes_the_last_line_with_text_without_colors_or_backticks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as handle:
            handle.write("Model: opus\n\x1b[31mGraphQL: `rate` limit exceeded\x1b[0m\n\n  \n")
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(release.last_line(handle.name), "GraphQL: 'rate' limit exceeded")

    def test_a_long_line_is_cut_and_a_missing_log_gives_nothing(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as handle:
            handle.write("x" * 500)
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(len(release.last_line(handle.name)), release.MAX_ERROR)
        self.assertEqual(release.last_line("/nonexistent.log"), "")
        self.assertEqual(release.last_line(None), "")


class ReleaseTest(unittest.TestCase):
    """release() against a store in a temporary folder."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = {key: value for key, value in os.environ.items() if key != "AGENT_DESK_RUN"}
        env.update({"AGENT_DESK_AUTHOR": "desk", "AGENT_DESK_UNIT": UNIT})
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.desk = store.Store(Path(folder.name) / "desk.sqlite")
        self.addCleanup(self.desk.close)

    def issue(self, status="in-progress", labels=(), comments=()):
        number = self.desk.create(REPO, "issue", "Work", labels=labels)
        self.desk.set_state(REPO, number, status)
        for body in comments:
            self.desk.comment(REPO, number, body)
        return number

    def run_release(self, issue, outcome="failure", **options):
        handed = []
        out = io.StringIO()
        with redirect_stdout(out):
            release.release(self.desk, str(issue), outcome, REPO, UNIT, event=options.pop("event", "issues"),
                            hand_on=lambda: handed.append(True), **options)
        return handed, out.getvalue()

    def notes(self, number):
        return [(one["author"]["login"], one["body"]) for one in self.desk.item(REPO, number)["comments"]]

    def test_a_failure_before_the_agent_started_frees_the_issue_posts_the_note_and_queues_it_again(self):
        issue = self.issue(labels=["benchmark"])
        handed, out = self.run_release(issue, error="boom")
        self.assertEqual(self.desk.item(REPO, issue)["status"], "queued")
        [(author, body)] = self.notes(issue)
        self.assertEqual(author, "desk")
        self.assertTrue(body.startswith(f"The agent run failed (log: `journalctl --user -u {UNIT}`). Its last line: "
                                        "`boom`"))
        self.assertTrue(body.endswith(SIGNED))
        # The issue moved to open before the note, and to queued after it, with a new request for its run.
        states = [event["payload"]["state"] for event in self.desk.events_since()
                  if event["type"] == "state-changed" and event["number"] == issue]
        self.assertEqual(states, ["in-progress", "open", "queued"])
        [run] = self.desk.runs(("requested",))
        self.assertEqual((run["kind"], run["target"]), ("agent", str(issue)))
        asked = self.desk.request_of(run["id"])
        self.assertEqual(asked["payload"]["env"], {"EVENT": "issues"})
        self.assertTrue(asked["payload"]["benchmark"])
        self.assertEqual(asked["author"], "desk")
        self.assertEqual(handed, [True])
        self.assertIn(f"Queued #{issue} again", out)

    def test_a_reported_run_writes_nothing(self):
        issue = self.issue(comments=[f"The agent finished: review #9 (log: `journalctl --user -u {UNIT}`)."])
        handed, out = self.run_release(issue)
        self.assertEqual(self.desk.item(REPO, issue)["status"], "in-progress")
        self.assertEqual(len(self.notes(issue)), 1)
        self.assertEqual(handed, [])
        self.assertIn("nothing to release", out)

    def test_an_issue_that_waits_on_a_decision_writes_nothing(self):
        issue = self.issue(status="needs-design")
        handed, _ = self.run_release(issue, "cancelled")
        self.assertEqual((self.notes(issue), handed), ([], []))

    def test_an_open_review_gets_only_the_note(self):
        issue = self.issue(comments=[f"An agent started on `agent/issue-1` (log: `journalctl --user -u {UNIT}`)."])
        self.desk.create(REPO, "review", "Work", branch=f"agent/issue-{issue}", draft=True)
        handed, _ = self.run_release(issue)
        self.assertEqual(self.desk.item(REPO, issue)["status"], "in-progress")
        self.assertTrue(self.notes(issue)[-1][1].startswith("The agent run failed"))
        self.assertEqual(self.desk.runs(("requested",)), [])
        self.assertEqual(handed, [True])

    def test_a_failure_after_the_agent_started_frees_the_issue_for_the_user(self):
        issue = self.issue(comments=[f"An agent started on `agent/issue-1` (log: `journalctl --user -u {UNIT}`)."])
        self.run_release(issue)
        self.assertEqual(self.desk.item(REPO, issue)["status"], "open")
        self.assertIn("Label the issue `agent-ready` to run it again.", self.notes(issue)[-1][1])
        self.assertEqual(self.desk.runs(("requested",)), [])

    def test_a_cancelled_run_from_the_review_says_so_on_the_review_and_the_issue(self):
        issue = self.issue()
        review = self.desk.create(REPO, "review", "Work", branch=f"agent/issue-{issue}", draft=True)
        self.run_release(issue, "cancelled", event="issue_comment", reply_pr=str(review))
        [(author, body)] = self.notes(review)
        self.assertEqual(author, "desk")
        self.assertEqual(body, f"The agent run was cancelled or timed out (log: `journalctl --user -u {UNIT}`)."
                               f"{SIGNED}")
        self.assertEqual(self.notes(issue), [
            ("desk", f"The agent run on feedback from #{review} was cancelled or timed out.{SIGNED}")])
        self.assertEqual(self.desk.item(REPO, issue)["status"], "in-progress")

    def test_a_discussion_run_only_says_how_it_ended_and_leaves_the_idea_as_it_was(self):
        idea = self.issue(status="open", labels=["idea", "serial:web"],
                          comments=[f"A discussion run started on this idea (log: `journalctl --user -u {UNIT}`)."])
        before = len(self.desk.events_since())
        handed, out = self.run_release(idea, error="boom", event="", discussion=True)
        self.run_release(idea, "cancelled", event="", discussion=True)
        self.assertEqual(self.desk.item(REPO, idea)["status"], "open")
        self.assertEqual([body for _, body in self.notes(idea)[1:]], [
            f"The discussion run failed (log: `journalctl --user -u {UNIT}`). Its last line: `boom`{SIGNED}",
            f"The discussion run was cancelled or timed out (log: `journalctl --user -u {UNIT}`).{SIGNED}",
        ])
        # Nothing moved, no run was asked for, and the idea held no serial slot to hand on.
        self.assertFalse([event for event in self.desk.events_since(before)
                          if event["type"] in ("state-changed", "run-requested")])
        self.assertEqual(handed, [])
        self.assertIn(f"Said on #{idea} how its discussion run ended.", out)

    def test_the_command_writes_as_the_desk_inside_a_run_and_refuses_an_issue_the_store_lacks(self):
        issue = self.issue()
        environ = {"GH_REPO": REPO, "AGENT_DESK_RUN": "3", "XDG_STATE_HOME": str(Path(self.desk.path).parent)}
        with mock.patch.dict(os.environ, environ), mock.patch.object(store, "DATABASE", self.desk.path), \
                mock.patch.object(release, "serial_release", lambda: None), redirect_stdout(io.StringIO()):
            # A run that starts the tests sets these, and release.main would follow them to the run's own PR.
            for name in RUN_VARIABLES:
                os.environ.pop(name, None)
            self.assertEqual(release.main(["release.py", str(issue), "cancelled"]), 0)
            with mock.patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(release.main(["release.py", "40", "cancelled"]), 1)
        self.assertIn("has no issue #40", err.getvalue())
        [(author, body)] = self.notes(issue)
        self.assertEqual(author, "desk")
        self.assertTrue(body.startswith("The agent run was cancelled or timed out"))


if __name__ == "__main__":
    unittest.main()
