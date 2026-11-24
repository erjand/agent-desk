"""Tests for desk_end_leftovers in lib/common.sh, which ends the processes a run's unit left in a worktree. The unit the
tests name is the cgroup the test itself runs in, which the processes it starts share."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"


def own_cgroup():
    """The last part of this process's cgroup v2 path, or None on a machine without one."""
    try:
        lines = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError:
        return None
    path = next((line[3:] for line in lines if line.startswith("0::")), "")
    return path.rstrip("/").rsplit("/", 1)[-1] or None


UNIT = own_cgroup()


@unittest.skipUnless(UNIT, "needs a cgroup v2 path below the root")
class EndLeftoversTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.worktree = self.root / "desk-issue-7"
        (self.worktree / "build").mkdir(parents=True)
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()

    def start(self, cwd):
        """A process left running in `cwd`, ended when the test ends if desk_end_leftovers left it."""
        process = subprocess.Popen(["sleep", "60"], cwd=cwd)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process

    def end_leftovers(self, unit, cwd=None):
        # The helpers alone: sourcing all of common.sh needs a project's checkout, which the test folder isn't. The
        # processes the tests leave end at TERM, so the wait before KILL can be short.
        env = {key: value for key, value in os.environ.items() if key != "AGENT_DESK_UNIT"} | {
            "DESK_HELPERS_ONLY": "1", "AGENT_DESK_LEFTOVER_GRACE": "0.1"}
        if unit is not None:
            env["AGENT_DESK_UNIT"] = unit
        result = subprocess.run(["bash", "-c", f'source "{LIB}/common.sh" && desk_end_leftovers "$1"', "end",
                                 str(self.worktree)], cwd=cwd or self.root, env=env, capture_output=True, text=True,
                                timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_it_ends_the_units_processes_in_the_worktree_and_leaves_the_rest(self):
        inside, nested, outside = self.start(self.worktree), self.start(self.worktree / "build"), self.start(self.elsewhere)
        out = self.end_leftovers(UNIT)
        self.assertEqual(out, f"Ending 2 leftover processes in {self.worktree}.\n")
        self.assertIsNotNone(inside.poll())
        self.assertIsNotNone(nested.poll())
        self.assertIsNone(outside.poll())

    def test_a_process_of_another_unit_keeps_running(self):
        # A session someone resumed in the worktree runs in their own terminal's cgroup, not the run's unit.
        process = self.start(self.worktree)
        self.assertEqual(self.end_leftovers(f"agent-desk-agent-o-r-7-1.service-{os.getpid()}"), "")
        self.assertIsNone(process.poll())

    def test_outside_a_unit_it_ends_nothing(self):
        process = self.start(self.worktree)
        self.assertEqual(self.end_leftovers(None), "")
        self.assertEqual(self.end_leftovers(""), "")
        self.assertIsNone(process.poll())

    def test_the_calling_script_keeps_running_in_the_worktree(self):
        self.assertEqual(self.end_leftovers(UNIT, cwd=self.worktree), "")


if __name__ == "__main__":
    unittest.main()
