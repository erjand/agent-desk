"""Tests for lib/worktree.sh: the seed copy that lib/seed.sh makes when it creates a worktree, and
`agent-desk worktree --teardown <slug>`, which it runs for a caller about to remove a worktree itself, such as the web
page undoing a failed schedule change. Each runs the script against a real main checkout and a worktree beside it in a
temporary folder."""

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work_scripts import SIGNED, UNIT, Desk, git  # noqa: E402

VANISHED = "cp: cannot stat '{0}/gone': No such file or directory"


def fake_cp(folder, failures, error=VANISHED, between=""):
    """
    Puts a `cp` in `folder`, which the caller puts first on PATH, that runs the real one and then, on each of its first
    `failures` calls, reports `error` with the source in place of {0} and exits 1, as GNU cp does when a file of the
    source vanished while the rest copied. `between` runs after the first call, as a build changing the seed before the
    next pass. Each call logs its arguments on a line of `folder`/cp.log, which the function returns.
    """
    calls = folder / "cp.log"
    script = folder / "cp"
    script.write_text(f"""#!/bin/bash
echo "$*" >>"{calls}"
"{shutil.which("cp")}" "$@" || exit
count=$(wc -l <"{calls}")
if ((count == 1)); then
    {between or ":"}
fi
if ((count <= {failures})); then
    from=${{@: -2:1}}
    echo "{error.replace("{0}", "$from")}" >&2
    exit 1
fi
""")
    script.chmod(0o755)
    return calls


class SeedTest(Desk):
    """A seed copy that a build in the main checkout changes under it."""

    def setUp(self):
        super().setUp()
        config = json.loads((self.main / ".agent-desk.json").read_text())
        config["worktree"] = {"seeds": ["target"], "setup": f'touch built; echo set up >>"{self.root}/setups"'}
        (self.main / ".agent-desk.json").write_text(json.dumps(config))
        (self.main / "target" / "debug").mkdir(parents=True)
        (self.main / "target" / "debug" / "app").write_text("built\n")
        self.dest = self.worktree("issue-5")
        self.failure = self.root / "failure"

    def make(self):
        return self.run_lib("worktree.sh", "issue-5", AGENT_DESK_SETUP_FAILURE=str(self.failure))

    def branches(self):
        return git("branch", "--format=%(refname:short)", cwd=self.main).split()

    def test_a_copy_that_meets_a_vanished_file_copies_again_and_goes_on_to_the_setup(self):
        calls = fake_cp(self.root / "fakes", 1, between=f'echo late >"{self.main}/target/debug/late"')
        done = self.make()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Pass 1 of 3 met files gone from", done.stderr)
        passes = calls.read_text().splitlines()
        self.assertEqual(len(passes), 2)
        self.assertNotIn("--update", passes[0])
        self.assertIn("--update", passes[1])
        self.assertEqual((self.dest / "target" / "debug" / "app").read_text(), "built\n")
        self.assertEqual((self.dest / "target" / "debug" / "late").read_text(), "late\n")
        self.assertFalse((self.dest / "target" / "target").exists())
        self.assertTrue((self.dest / "built").exists())
        self.assertFalse(self.failure.exists())

    def test_a_copy_that_meets_vanished_files_on_every_pass_removes_the_worktree_and_its_branch(self):
        calls = fake_cp(self.root / "fakes", 3)
        done = self.make()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(len(calls.read_text().splitlines()), 3)
        self.assertIn(f"The copy of the seed target failed, so {self.dest} is removed.", done.stderr)
        self.assertFalse(self.dest.exists())
        self.assertNotIn("agent/issue-5", self.branches())
        self.assertFalse((self.root / "setups").exists())
        source = self.main / "target"
        self.assertEqual(self.failure.read_text(), f"the copy of the seed `target` from `{source}` failed: "
                                                   f"`{VANISHED.format(source)}`\n")
        # The next run seeds and sets up again.
        (self.root / "fakes" / "cp").unlink()
        done = self.make()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "setups").read_text(), "set up\n")

    def test_a_copy_that_fails_for_another_reason_stops_at_the_first_pass(self):
        calls = fake_cp(self.root / "fakes", 3, error="cp: error writing '{0}/app': No space left on device")
        done = self.make()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(len(calls.read_text().splitlines()), 1)
        self.assertNotIn("copies again", done.stderr)
        self.assertFalse(self.dest.exists())

    def test_a_failed_setup_names_its_command(self):
        config = json.loads((self.main / ".agent-desk.json").read_text())
        config["worktree"]["setup"] = "exit 1"
        (self.main / ".agent-desk.json").write_text(json.dumps(config))
        done = self.make()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(f"The worktree setup failed: exit 1, so {self.dest} is removed.", done.stderr)
        self.assertEqual(self.failure.read_text(), "the worktree setup, `exit 1`, failed\n")
        self.assertFalse(self.dest.exists())

    def test_the_run_comments_the_step_that_failed(self):
        fake_cp(self.root / "fakes", 3)
        issue = self.issue(status="queued")
        done = self.run_lib("work-issue.sh", str(issue), EVENT="issues")
        self.assertEqual(done.returncode, 1, done.stderr)
        source = self.main / "target"
        self.assertEqual(self.notes(issue)[-1], (
            "desk", f"The agent didn't start: the copy of the seed `target` from `{source}` failed: "
                    f"`{VANISHED.format(source)}` (log: `journalctl --user -u {UNIT}`).{SIGNED}"))
        self.assertEqual(self.item(issue)["status"], "open")
        self.assertFalse(self.worktree(f"issue-{issue}").exists())
        self.assertEqual(self.claude_args(), [])


class TeardownTest(Desk):
    def setUp(self):
        super().setUp()
        self.dest = self.worktree("chore-schedule")
        git("worktree", "add", "-q", "-b", "agent/chore-schedule", str(self.dest), "main", cwd=self.main)

    def configure(self, teardown):
        (self.main / ".agent-desk.json").write_text(json.dumps({"worktree": {"teardown": teardown}}))

    def test_the_teardown_runs_in_the_worktree_and_leaves_its_removal_to_the_caller(self):
        self.configure(f'pwd >"{self.root}/torn"; echo dropped the db')
        done = self.run_lib("worktree.sh", "--teardown", "chore-schedule")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "torn").read_text(), f"{self.dest}\n")
        self.assertIn("dropped the db", done.stdout)
        self.assertTrue(self.dest.exists())

    def test_a_failed_teardown_says_why_and_exits_0(self):
        self.configure("exit 3")
        done = self.run_lib("worktree.sh", "--teardown", "chore-schedule")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("The worktree teardown failed with status 3: exit 3", done.stdout)

    def test_a_missing_worktree_or_teardown_runs_nothing(self):
        self.configure(f'touch "{self.root}/torn"')
        done = self.run_lib("worktree.sh", "--teardown", "elsewhere")
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.configure("")
        done = self.run_lib("worktree.sh", "--teardown", "chore-schedule")
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.assertFalse((self.root / "torn").exists())

    def test_a_slug_outside_the_pattern_is_refused(self):
        done = self.run_lib("worktree.sh", "--teardown", "../desk")
        self.assertEqual(done.returncode, 2)
        self.assertIn("The slug takes lowercase letters", done.stderr)


if __name__ == "__main__":
    unittest.main()
