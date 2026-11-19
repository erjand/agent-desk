"""Tests for lib/start.sh, which starts an interactive session's work on an issue. Each runs the script against a
temporary store and a real origin and main checkout, as a session outside any dispatcher run."""

import fcntl
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work_scripts import REPO, Desk, git  # noqa: E402

SESSION = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


class StartCase(Desk):
    def setUp(self):
        super().setUp()
        # A session starts the work by hand, outside any unit the dispatcher started.
        self.env.update({"AGENT_DESK_RUN": "", "AGENT_DESK_UNIT": "", "CLAUDE_PID": "", "CLAUDE_CODE_SESSION_ID": ""})

    def start(self, number, **env):
        return self.run_lib("start.sh", str(number), **env)

    def configure(self, **config):
        (self.main / ".agent-desk.json").write_text(json.dumps(config))

    def branches(self):
        return git("branch", "--format=%(refname:short)", cwd=self.main).splitlines()


class StartTest(StartCase):
    def test_a_start_claims_the_issue_makes_its_worktree_from_main_and_names_the_session(self):
        number = self.issue(status="queued")
        done = self.start(number, CLAUDE_PID="4242", CLAUDE_CODE_SESSION_ID=SESSION)
        self.assertEqual(done.returncode, 0, done.stderr)
        dest = self.worktree(f"issue-{number}")
        self.assertEqual(done.stdout.splitlines()[-1], f"Working #{number} in {dest}")
        self.assertEqual(self.item(number)["status"], "in-progress")
        self.assertEqual(git("branch", "--show-current", cwd=dest), f"agent/issue-{number}")
        self.assertEqual(git("rev-parse", "HEAD", cwd=dest), git("rev-parse", "main", cwd=self.main))
        [(author, body)] = self.notes(number)
        self.assertEqual(author, "desk")
        self.assertTrue(body.startswith(
            f"An agent started on `agent/issue-{number}` in a local session (pid 4242 on `"), body)
        self.assertIn(f"`). To take over, run `claude --resume {SESSION}` in `{dest}`.", body)

    def test_a_start_outside_claude_code_posts_the_note_without_a_process_or_a_resume_command(self):
        number = self.issue(status="open")
        done = self.start(number)
        self.assertEqual(done.returncode, 0, done.stderr)
        [(_, body)] = self.notes(number)
        self.assertTrue(body.startswith(f"An agent started on `agent/issue-{number}` in a local session.\n\n"), body)
        self.assertNotIn("pid", body)
        self.assertNotIn("--resume", body)

    def test_a_base_label_cuts_the_worktree_from_that_branch(self):
        git("checkout", "-q", "-b", "local-desk", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", "on local-desk", cwd=self.main)
        git("checkout", "-q", "main", cwd=self.main)
        number = self.issue(status="queued", labels=["base:local-desk"])
        done = self.start(number)
        self.assertEqual(done.returncode, 0, done.stderr)
        dest = self.worktree(f"issue-{number}")
        self.assertEqual(git("rev-parse", "HEAD", cwd=dest), git("rev-parse", "local-desk", cwd=self.main))

    def test_a_second_start_continues_the_worktree_it_made(self):
        self.configure(worktree={"setup": "echo set up >>../setups"})
        number = self.issue(status="queued")
        self.assertEqual(self.start(number).returncode, 0)
        dest = self.worktree(f"issue-{number}")
        git("commit", "-q", "--allow-empty", "-m", "session work", cwd=dest)
        kept = git("rev-parse", "HEAD", cwd=dest)
        done = self.start(number)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=dest), kept)
        # The second start skips the setup, which ran once for the worktree the first one made.
        self.assertEqual((self.root / "setups").read_text(), "set up\n")
        self.assertEqual(len(self.notes(number)), 2)


class RefusedStartTest(StartCase):
    def test_a_start_while_a_headless_run_holds_the_issue_changes_nothing(self):
        number = self.issue(status="queued")
        with open(self.main / ".git" / f"agent-issue-{number}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            done = self.start(number)
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"An agent run is working #{number} now", done.stderr)
        self.assertEqual(self.item(number)["status"], "queued")
        self.assertFalse(self.worktree(f"issue-{number}").exists())
        self.assertEqual(self.notes(number), [])

    def test_a_serial_issue_behind_another_waits_with_the_claims_status_and_no_worktree(self):
        holder = self.issue(status="in-progress", labels=["serial:audio"])
        number = self.issue(status="queued", labels=["serial:audio"])
        done = self.start(number)
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn(f"#{number} waits for #{holder}.", done.stdout)
        self.assertEqual(self.item(number)["status"], "waiting")
        self.assertFalse(self.worktree(f"issue-{number}").exists())

    def test_a_base_the_main_checkout_lacks_gives_the_claim_back(self):
        number = self.issue(status="queued", labels=["base:missing"])
        done = self.start(number)
        self.assertEqual(done.returncode, 1)
        self.assertIn("no local branch missing", done.stderr)
        self.assertEqual(self.item(number)["status"], "open")
        self.assertFalse(self.worktree(f"issue-{number}").exists())
        self.assertNotIn(f"agent/issue-{number}", self.branches())
        self.assertEqual(self.notes(number), [])

    def test_a_failed_setup_gives_the_claim_back_and_leaves_no_worktree(self):
        self.configure(worktree={"setup": "exit 1"})
        number = self.issue(status="queued")
        done = self.start(number)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.item(number)["status"], "open")
        self.assertFalse(self.worktree(f"issue-{number}").exists())
        self.assertEqual(self.notes(number), [])

    def test_a_failed_setup_keeps_the_issue_in_progress_while_its_branch_has_an_open_review(self):
        self.configure(worktree={"setup": "exit 1"})
        number = self.issue(status="queued")
        self.review(f"agent/issue-{number}", fixes=number)
        done = self.start(number)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.item(number)["status"], "in-progress")
        self.assertFalse(self.worktree(f"issue-{number}").exists())


if __name__ == "__main__":
    unittest.main()
