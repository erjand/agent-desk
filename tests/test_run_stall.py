"""Tests for lib/run_stall.py, the rule that flags a run stalled on a command that left the CPU idle. The runs that
exercise it end to end, through lib/agent_run.py, are in tests/test_agent_run.py."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

import run_stall  # noqa: E402

TICKS = run_stall.CLOCK_TICKS
AFTER, WINDOW = 300, 120


def call(waited, tool="Bash", summary="cargo test --workspace", key="toolu_1"):
    return {"id": key, "tool": tool, "summary": summary, "waited": waited}


class WatchTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name) / "stalls"
        patch = mock.patch.object(run_stall, "folder", lambda: self.folder)
        patch.start()
        self.addCleanup(patch.stop)
        env = mock.patch.dict(os.environ, {"AGENT_DESK_UNIT": "", "AGENT_DESK_RUN": "41"})
        env.start()
        self.addCleanup(env.stop)
        self.logged, self.told = [], []
        self.watch = run_stall.Watch(after=AFTER, window=WINDOW, log=self.logged.append, tell=self.told.append)
        self.ticks = 0

    def check(self, at, waiting, spent=0, desk=False):
        """Checks the watch at `at` seconds, after the run's one process spent `spent` seconds of CPU since the last
        check."""
        self.ticks += spent * TICKS
        self.watch.check(at, waiting, {(9, "1"): self.ticks}, desk)

    def saved(self):
        return run_stall.read(41)

    def test_a_call_that_waits_five_minutes_on_an_idle_command_stalls_and_notifies_once(self):
        for at in range(0, 300, 15):
            self.check(at, call(at + 10))
        self.assertIsNone(self.saved(), "a call that has waited under five minutes doesn't stall")
        for at in range(300, 600, 15):
            self.check(at, call(at + 10), spent=0.01)
        self.assertEqual(self.saved()["tool"], "Bash")
        self.assertEqual(self.saved()["summary"], "cargo test --workspace")
        self.assertEqual(self.told, ["run 41: stalled 5 min in Bash: cargo test --workspace"])
        self.assertEqual(self.logged, ["Stalled: 5 min in Bash: cargo test --workspace, with the run's processes "
                                       "under 1% of a CPU for 120 s."])

    def test_the_stall_names_when_its_call_started(self):
        self.check(0, call(400))
        self.check(WINDOW, call(400 + WINDOW))
        since = datetime.fromisoformat(self.saved()["since"].replace("Z", "+00:00")).timestamp()
        self.assertAlmostEqual(since, time.time() - 400 - WINDOW, delta=2)

    def test_the_mark_clears_when_the_call_returns_and_a_new_stall_notifies_again(self):
        self.check(0, call(400))
        self.check(WINDOW, call(400 + WINDOW))
        self.assertIsNotNone(self.saved())
        self.check(WINDOW + 15, None)
        self.assertIsNone(self.saved())
        self.assertFalse((self.folder / "41.json").exists())
        self.check(WINDOW + 30, call(400, key="toolu_2", summary="make"))
        self.assertIsNotNone(self.saved())
        self.assertEqual(self.told[-1], "run 41: stalled 6 min in Bash: make")
        self.assertEqual(len(self.told), 2)

    def test_a_call_that_wakes_and_stalls_again_notifies_once(self):
        self.check(0, call(400))
        self.check(WINDOW, call(400 + WINDOW))
        self.check(WINDOW + 15, call(400 + WINDOW + 15), spent=WINDOW * 0.5)
        self.assertIsNone(self.saved(), "a command that uses the CPU again clears the mark")
        for at in range(WINDOW + 30, 4 * WINDOW, 15):
            self.check(at, call(400 + at))
        self.assertIsNotNone(self.saved())
        self.assertEqual(len(self.told), 1)

    def test_a_command_that_keeps_one_percent_of_a_cpu_busy_never_stalls(self):
        for at in range(0, 3 * WINDOW, 15):
            self.check(at, call(400 + at), spent=15 * 0.011)
        self.assertIsNone(self.saved())
        self.assertEqual(self.told, [])

    def test_a_busy_command_that_just_ended_reads_busy_until_its_call_returns(self):
        for at in range(0, 300, 15):
            self.ticks += 15 * TICKS
            self.watch.check(at, call(400 + at), {(5, "1"): self.ticks}, False)
        # The build exits, and claude -p has yet to report its call's result.
        self.watch.check(300, call(700), {(9, "1"): 0}, False)
        self.assertIsNone(self.saved())

    def test_a_wait_on_the_desk_or_a_subagent_never_stalls(self):
        for name, waiting, desk in [("agent-desk gpu", call(400), True), ("an Agent call", call(400, "Agent"), False),
                                    ("a Task call", call(400, "Task"), False), ("no call", None, False)]:
            with self.subTest(name):
                self.check(0, waiting, desk=desk)
                self.check(3 * WINDOW, waiting, desk=desk)
                self.assertIsNone(self.saved())
                self.assertEqual(self.told, [])

    def test_the_rule_waits_for_a_whole_window_of_samples(self):
        self.check(0, call(1000))
        self.check(WINDOW - 1, call(1000))
        self.assertIsNone(self.saved())
        self.check(WINDOW, call(1000))
        self.assertIsNotNone(self.saved())

    def test_a_run_without_an_id_logs_and_notifies_but_saves_nothing(self):
        with mock.patch.dict(os.environ, {"AGENT_DESK_RUN": ""}):
            watch = run_stall.Watch(after=AFTER, window=WINDOW, log=self.logged.append, tell=self.told.append)
            watch.check(0, call(400), {}, False)
            watch.check(WINDOW, call(400), {}, False)
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.told, ["a run: stalled 6 min in Bash: cargo test --workspace"])


class SampleTest(unittest.TestCase):
    def test_an_agent_desk_command_is_one_that_runs_a_script_from_the_desks_lib(self):
        self.assertTrue(run_stall.desk_command(["bash", str(LIB / "gpu.sh"), "--", "godot"]))
        self.assertTrue(run_stall.desk_command(["python3", "-B", str(LIB / "store_cli.py"), "review", "checks"]))
        self.assertFalse(run_stall.desk_command(["bash", "/elsewhere/lib/gpu.sh", "--", "godot"]))
        self.assertFalse(run_stall.desk_command(["cargo", "test", str(LIB / "gpu.sh")]))
        self.assertFalse(run_stall.desk_command([]))

    def test_a_process_counts_the_cpu_of_the_children_it_reaped(self):
        # The shell's child spins for a moment and exits before the sample, as a test runner's short tests do.
        shell = subprocess.Popen(["bash", "-c", f'"{sys.executable}" -c "'
                                 'import time\nend = time.time() + 0.3\nwhile time.time() < end: pass"; sleep 30'])
        self.addCleanup(shell.wait)
        self.addCleanup(shell.kill)
        time.sleep(0.6)
        ticks, desk = run_stall.sample([shell.pid])
        self.assertGreaterEqual(sum(ticks.values()), 0.2 * TICKS)
        self.assertFalse(desk)

    def test_the_sample_skips_claude_and_finds_agent_desk_gpu_waiting_on_the_lock(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.assertEqual(run_stall.sample([sleeper.pid], skip={sleeper.pid}), ({}, False))
        self.assertEqual(run_stall.sample([sleeper.pid])[1], False)
        runtime = tempfile.mkdtemp()
        env = {**os.environ, "XDG_RUNTIME_DIR": runtime, "XDG_STATE_HOME": tempfile.mkdtemp(),
               "AGENT_DESK_GPU_SLOTS": "1", "AGENT_DESK_RUN": ""}
        holder = subprocess.Popen(["bash", str(LIB / "gpu.sh"), "--", "sleep", "30"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.terminate)
        # The waiter starts only once the holder has its slot, or a loaded machine lets the waiter take it first.
        held = Path(runtime) / "agent-desk" / f"gpu.held.{holder.pid}"
        deadline = time.monotonic() + 20
        while not held.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(held.exists(), "the first call holds the GPU slot")
        waiter = subprocess.Popen(["bash", str(LIB / "gpu.sh"), "--", "true"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(waiter.wait)
        self.addCleanup(waiter.terminate)
        time.sleep(0.5)
        self.assertIsNone(waiter.poll(), "the second call waits on the GPU lock")
        self.assertTrue(run_stall.sample([waiter.pid])[1])


class ReadTest(unittest.TestCase):
    def test_read_gives_the_saved_stall_or_none(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(run_stall, "folder", lambda: Path(folder)):
            self.assertIsNone(run_stall.read(3))
            run_stall.path_of(3).write_text(json.dumps({"since": "2026-11-10T11:48:00Z", "tool": "Bash"}))
            self.assertEqual(run_stall.read(3), {"since": "2026-11-10T11:48:00Z", "tool": "Bash", "summary": ""})


if __name__ == "__main__":
    unittest.main()
