"""Tests for lib/review_checks.py, which `agent-desk review checks` runs."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import review_checks  # noqa: E402
import store  # noqa: E402
from test_store import REPO, StoreCase  # noqa: E402

BRANCH = "agent/issue-1"


def git(checkout, *args):
    return subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class ReviewChecksTest(StoreCase):
    def setUp(self):
        super().setUp()
        checkout = tempfile.TemporaryDirectory()
        self.addCleanup(checkout.cleanup)
        self.checkout = Path(checkout.name)
        git(self.checkout, "init", "-q", "-b", "main")
        git(self.checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one")
        git(self.checkout, "branch", BRANCH)
        self.head = git(self.checkout, "rev-parse", BRANCH)
        self.config({"test": "just test"})
        store.PROJECTS.write_text(f"{REPO} {self.checkout}\n")
        self.number = self.store.create(REPO, "review", "Add the store", branch=BRANCH, head_sha=self.head)

    def config(self, config):
        (self.checkout / ".agent-desk.json").write_text(json.dumps(config))

    def add_check(self, status, head=None, name="test", log=None):
        """A check row with the columns the `checks` table has, as lib/checks.py writes them."""
        row = {"project": REPO, "number": self.number, "branch": BRANCH, "head_sha": head or self.head, "name": name,
               "status": status, "log_path": log}
        columns = [one[1] for one in self.store.db.execute("PRAGMA table_info(checks)")]
        row = {key: value for key, value in row.items() if key in columns}
        return self.store.db.execute(f"INSERT INTO checks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                                     tuple(row.values())).lastrowid

    def set_status(self, check, status):
        self.store.db.execute("UPDATE checks SET status = ? WHERE id = ?", (status, check))

    def checks(self, watch=False, on_sleep=None, seconds_per_sleep=10):
        """The exit status and the lines printed, with a clock that moves only when the watch sleeps."""
        said, clock = [], [0]

        def sleep(_):
            clock[0] += seconds_per_sleep
            if on_sleep:
                on_sleep(clock[0])

        status = review_checks.checks(self.store, REPO, self.number, str(self.checkout), watch=watch,
                                      say=said.append, clock=lambda: clock[0], sleep=sleep)
        return status, said

    def test_a_head_whose_checks_passed_exits_0_and_names_each_log(self):
        self.add_check("passed", log="/logs/1.log")
        status, said = self.checks()
        self.assertEqual(status, 0)
        self.assertEqual(said, [f"Checks of {BRANCH} at {self.head[:12]}:\ntest\tpassed  /logs/1.log"])

    def test_a_failed_check_exits_1(self):
        self.add_check("passed", name="merge")
        self.add_check("timed-out", log="/logs/2.log")
        self.assertEqual(self.checks()[0], 1)

    def test_a_rerun_counts_by_its_newest_attempt(self):
        self.add_check("failed")
        rerun = self.add_check("running", log="/logs/2.log")
        status, said = self.checks()
        self.assertEqual(status, 8)
        self.assertIn("test\trunning", said[0])
        self.assertNotIn("/logs/2.log", said[0])
        self.set_status(rerun, "passed")
        self.assertEqual(self.checks()[0], 0)

    def test_checks_of_an_older_head_do_not_count(self):
        self.add_check("passed", head="0" * 40)
        git(self.checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "two")
        git(self.checkout, "branch", "-f", BRANCH, "main")
        status, said = self.checks()
        self.assertEqual((status, said), (8, [f"No checks of {BRANCH} at {git(self.checkout, 'rev-parse', BRANCH)[:12]}"
                                              " yet."]))

    def test_the_review_head_stands_in_when_git_has_no_branch(self):
        git(self.checkout, "branch", "-D", BRANCH)
        self.add_check("passed")
        self.assertEqual(self.checks()[0], 0)

    def test_a_superseded_check_leaves_the_head_unchecked(self):
        self.add_check("superseded")
        self.assertEqual(self.checks()[0], 8)

    def test_a_project_without_a_test_command_has_nothing_to_wait_for(self):
        self.config({"test": ""})
        status, said = self.checks(watch=True)
        self.assertEqual(status, 0)
        self.assertIn(f"{REPO} sets no test command", said[0])

    def test_watch_prints_each_change_until_the_checks_finish(self):
        check = self.add_check("queued")
        moves = {10: "running", 30: "passed"}

        def on_sleep(now):
            if now in moves:
                self.set_status(check, moves[now])

        status, said = self.checks(watch=True, on_sleep=on_sleep)
        self.assertEqual(status, 0)
        self.assertEqual([text.splitlines()[-1] for text in said], ["test\tqueued", "test\trunning", "test\tpassed"])

    def test_watch_waits_for_a_check_to_start_and_gives_up_without_one(self):
        status, said = self.checks(watch=True, on_sleep=lambda now: now == 60 and self.add_check("failed"))
        self.assertEqual((status, said[-1]), (1, f"Checks of {BRANCH} at {self.head[:12]}:\ntest\tfailed"))
        self.store.db.execute("DELETE FROM checks")
        status, said = self.checks(watch=True, seconds_per_sleep=100)
        self.assertEqual(status, 1)
        self.assertIn("agent-desk-dispatch.service", said[-1])

    def test_the_command_names_the_review_by_its_branch(self):
        self.add_check("passed")
        status, out, err = self.cli("review", "checks", BRANCH, "--watch")
        self.assertEqual((status, err), (0, ""))
        self.assertIn("test\tpassed", out)
        self.assertEqual(self.cli("review", "checks", "9")[0], 1)


if __name__ == "__main__":
    unittest.main()
