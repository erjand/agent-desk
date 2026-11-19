"""Tests for agent-desk/lib/run_units.py: the systemd-run command that starts a unit, and agentdesk.slice, which
`agent-desk dispatch install` writes to hold every unit."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import run_units  # noqa: E402


class UnitCommandTest(unittest.TestCase):
    def command(self):
        return run_units.unit_command("agent-desk-agent-org-game-7-1.service", "a run", "/work/game", 180, 12,
                                      "handler", {"GH_REPO": "org/game"}, ["agent-desk", "work-issue", "7"])

    def properties(self, args):
        return [args[i + 1] for i, arg in enumerate(args) if arg == "-p"]

    def test_the_unit_runs_in_the_slice(self):
        args = self.command()
        self.assertEqual(args[args.index("--slice") + 1], "agentdesk.slice")
        self.assertLess(args.index("--slice"), args.index("--"))

    def test_the_unit_keeps_its_own_memory_ceiling_and_priority_inside_the_slice(self):
        properties = self.properties(self.command())
        for setting in ("MemoryHigh=12G", "Nice=10", "CPUWeight=20", "IOWeight=20", "RuntimeMaxSec=180min"):
            self.assertIn(setting, properties)
        self.assertFalse(any(setting.startswith("MemoryMax=") for setting in properties))


class SliceTest(unittest.TestCase):
    def test_the_slice_caps_the_runs_at_half_of_memory_with_the_units_weights_and_no_hard_limit(self):
        self.assertEqual(run_units.slice_text(),
                         "[Unit]\nDescription=agent-desk runs, which share a memory ceiling\n\n"
                         "[Slice]\nMemoryHigh=50%\nCPUWeight=20\nIOWeight=20\n")

    def test_the_slice_carries_the_units_weights_but_not_nice_which_a_slice_lacks(self):
        settings = run_units.slice_text().split("[Slice]\n", 1)[1].splitlines()
        self.assertEqual(settings[1:], [p for p in run_units.PRIORITY if not p.startswith("Nice=")])
        self.assertNotIn("MemoryMax", run_units.slice_text())

    def test_the_slice_sits_at_the_top_of_the_users_manager_beside_app_slice(self):
        self.assertNotIn("-", run_units.SLICE.removesuffix(".slice"))

    def test_the_slices_share_is_the_one_memory_lists(self):
        run_units.memory.SHARES["sliceMemoryHigh"] = 0.25
        self.addCleanup(run_units.memory.SHARES.__setitem__, "sliceMemoryHigh", 0.5)
        self.assertIn("MemoryHigh=25%\n", run_units.slice_text())

    def test_the_script_prints_the_slice(self):
        done = subprocess.run([sys.executable, "-B", str(ROOT / "lib" / "run_units.py"), "slice"],
                              capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout, run_units.slice_text())


class DispatchInstallTest(unittest.TestCase):
    def test_install_writes_the_slice_before_it_reloads_the_users_manager(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fakes = root / "fakes"
            fakes.mkdir()
            calls = root / "systemctl.log"
            units = root / "config" / "systemd" / "user"
            # The stand-in for systemctl logs whether the slice existed when each call came.
            systemctl = fakes / "systemctl"
            systemctl.write_text(f'#!/bin/sh\n[ -f "{units}/agentdesk.slice" ] && s=slice || s=none\n'
                                 f'echo "$s $*" >>"{calls}"\n')
            systemctl.chmod(0o755)
            env = {**os.environ, "PATH": f"{fakes}:{os.environ['PATH']}", "XDG_CONFIG_HOME": str(root / "config"),
                   "XDG_STATE_HOME": str(root / "state")}
            done = subprocess.run([str(ROOT / "lib" / "dispatch.sh"), "install"], env=env, capture_output=True,
                                  text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual((units / "agentdesk.slice").read_text(), run_units.slice_text())
            self.assertEqual(calls.read_text().splitlines()[0], "slice --user daemon-reload")


if __name__ == "__main__":
    unittest.main()
