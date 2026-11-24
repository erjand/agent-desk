"""Tests for desk_in_use and desk_scratch_remove in lib/common.sh, which find a process working in a worktree and say
which one kept it."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
# One process that waits, so the worktree holds no child of it that desk_in_use could find first.
WAIT = [sys.executable, "-c", "import time; time.sleep(60)"]


def own_cgroup():
    """The last part of this process's cgroup v2 path, or None on a machine without one."""
    try:
        lines = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError:
        return None
    path = next((line[3:] for line in lines if line.startswith("0::")), "")
    return path.rstrip("/").rsplit("/", 1)[-1] or None


class InUseTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.main = self.root / "desk"
        self.worktree = self.root / "desk-scratch"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.main)], check=True)
        subprocess.run(["git", "-C", str(self.main), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "start"], check=True)
        subprocess.run(["git", "-C", str(self.main), "worktree", "add", "-q", "--detach", str(self.worktree)],
                       check=True)
        (self.worktree / "build").mkdir()

    def start(self, cwd, *args):
        """A process left running in `cwd` with `args` on its command line, ended when the test ends."""
        process = subprocess.Popen([*WAIT, *args], cwd=cwd)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process

    def helpers(self, script, *args):
        # The helpers alone: sourcing all of common.sh needs a project's checkout, which the test folder isn't.
        env = os.environ | {"DESK_HELPERS_ONLY": "1"}
        result = subprocess.run(["bash", "-c", f'set -euo pipefail; source "{LIB}/common.sh"; main="$1"; {script}',
                                 "helpers", str(self.main), *args], cwd=self.root, env=env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def in_use_by(self, directory):
        return self.helpers('if desk_in_use "$2"; then echo "$desk_in_use_by"; else echo free; fi',
                            str(directory)).rstrip("\n")

    def report(self, process, command):
        cgroup = own_cgroup()
        return f"{process.pid} {command}" + (f" ({cgroup})" if cgroup else "")

    def command(self, *args):
        return " ".join([*WAIT, *args])

    def test_it_names_the_process_its_command_line_and_its_cgroup(self):
        process = self.start(self.worktree / "build", "watcher")
        self.assertEqual(self.in_use_by(self.worktree), self.report(process, self.command("watcher")))

    def test_it_cuts_a_long_command_line(self):
        process = self.start(self.worktree, "x" * 300)
        cut = self.command("x" * 300)[:119] + "…"
        self.assertEqual(self.in_use_by(self.worktree), self.report(process, cut))

    def test_a_folder_nobody_works_in_is_free(self):
        self.start(self.root)
        self.assertEqual(self.in_use_by(self.worktree), "free")

    def test_a_process_that_ended_is_reported_by_its_pid_alone(self):
        self.assertEqual(self.helpers('desk_process_report 999999999'), "999999999\n")

    def test_scratch_remove_keeps_a_worktree_in_use_and_names_the_process(self):
        process = self.start(self.worktree, "holder")
        out = self.helpers('desk_scratch_remove "$2"', str(self.worktree))
        self.assertEqual(out, f"Kept {self.worktree}: a process is working in it: "
                              f"{self.report(process, self.command('holder'))}.\n")
        self.assertTrue(self.worktree.is_dir())

    def test_scratch_remove_removes_a_worktree_nobody_works_in(self):
        self.assertEqual(self.helpers('desk_scratch_remove "$2"', str(self.worktree)), "")
        self.assertFalse(self.worktree.exists())


if __name__ == "__main__":
    unittest.main()
