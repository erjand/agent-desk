"""Tests for lib/gpu.sh, which runs a command in one of the machine's GPU slots, against a runtime folder of its own."""

import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GPU = ROOT / "lib" / "gpu.sh"
sys.path.insert(0, str(ROOT / "lib"))

import store  # noqa: E402

# What `agent-desk gpu slots` says of the VRAM with the fake nvidia-smi's starting output and no threshold set.
ROOMY = "Slots past the first open with 7 GB of VRAM free, the default; 15.3 GB free now\n"


class GpuCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("AGENT_DESK_GPU_SLOTS", "AGENT_DESK_GPU_LONG_WAIT", "AGENT_DESK_RUN")}
        self.env["XDG_RUNTIME_DIR"] = str(self.runtime)
        # A waiting call tries the slots and reads the VRAM again every tenth of a second, not every second or three.
        self.env["AGENT_DESK_GPU_POLL"] = "0.1"
        # Each test records its calls in a store of its own.
        self.env["XDG_STATE_HOME"] = str(self.root / "state")
        self.database = self.root / "state" / "agent-desk" / "desk.sqlite"
        self.setting = self.root / "state" / "agent-desk" / "gpu-slots"
        self.vram_setting = self.root / "state" / "agent-desk" / "gpu-min-free-vram"
        # A fake nvidia-smi stands first on PATH, so no test reads the machine's own card. It starts with 15.3 GB free.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env["PATH"] = f"{self.bin}:{self.env['PATH']}"
        self.nvidia("1000, 16303\n")

    def nvidia(self, output, status=0):
        """Makes the fake nvidia-smi print output and exit with status, and log its arguments to nvidia.args."""
        fake = self.bin / "nvidia-smi"
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{self.root}/nvidia.args"\nprintf %s \'{output}\'\nexit {status}\n')
        fake.chmod(0o755)

    def gpu(self, *args, slots=None, cwd=None):
        """Runs agent-desk gpu to the end and returns the finished process."""
        return subprocess.run(["bash", str(GPU), *args], cwd=cwd or self.root, env=self.env_with(slots),
                              capture_output=True, text=True, timeout=30)

    def env_with(self, slots):
        env = dict(self.env)
        if slots is not None:
            env["AGENT_DESK_GPU_SLOTS"] = str(slots)
        return env

    def calls(self):
        """The GPU calls the test's store recorded, oldest first."""
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute("SELECT * FROM gpu_calls ORDER BY id")]
        finally:
            db.close()

    def hold(self, name, *flags, slots=None):
        """Starts a command under agent-desk gpu from a checkout called name. The command touches <name>.ran once it
        holds its slot and runs until the test touches <name>.done. Returns the process, whose stderr goes to
        <name>.err."""
        checkout = self.root / name
        checkout.mkdir()
        err = (self.root / f"{name}.err").open("w")
        self.addCleanup(err.close)
        ran, done = self.root / f"{name}.ran", self.root / f"{name}.done"
        script = f'touch "{ran}"; while [ ! -e "{done}" ]; do sleep 0.02; done'
        process = subprocess.Popen(["bash", str(GPU), *flags, "--", "sh", "-c", script], cwd=checkout,
                                   env=self.env_with(slots), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=err)
        self.addCleanup(self.finish, process, done)
        return process

    def finish(self, process, done):
        done.touch()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def release(self, name, process):
        (self.root / f"{name}.done").touch()
        self.assertEqual(process.wait(timeout=10), 0)

    def ran(self, name):
        return (self.root / f"{name}.ran").exists()

    def stderr(self, name):
        return (self.root / f"{name}.err").read_text()

    def until(self, check, what):
        deadline = time.monotonic() + 10
        while not check():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.02)

    def until_running(self, name):
        self.until(lambda: self.ran(name), f"{name} to run")

    def until_waiting(self, name):
        self.until(lambda: "waiting for" in self.stderr(name), f"{name} to wait")

    def held_file(self, process):
        return self.runtime / "agent-desk" / f"gpu.held.{process.pid}"

    def backdate(self, process, seconds):
        """Rewrites the gpu.held.<pid> file of a call that holds its slots so it took them `seconds` ago."""
        path = self.held_file(process)
        self.until(path.exists, f"{path.name} to be written")
        _, exclusive = path.read_text().split()
        path.write_text(f"{int(time.time()) - seconds} {exclusive}\n")


class ExitStatusTest(GpuCase):
    def test_the_command_s_exit_status_and_output_pass_through(self):
        done = self.gpu("--", "sh", "-c", "echo out; echo err >&2; exit 7")
        self.assertEqual(done.returncode, 7)
        self.assertEqual(done.stdout, "out\n")
        self.assertEqual(done.stderr, "err\n")

    def test_a_command_that_succeeds_exits_zero_and_needs_no_dashes(self):
        done = self.gpu("true")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_missing_command_exits_127(self):
        self.assertEqual(self.gpu("--", "agent-desk-no-such-command").returncode, 127)

    def test_the_slot_frees_after_a_failed_command(self):
        self.assertEqual(self.gpu("--", "false").returncode, 1)
        done = self.gpu("--", "true")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stderr, "")

    def test_no_command_or_an_unknown_flag_prints_the_usage(self):
        for args in ((), ("--",), ("--shared", "--", "true")):
            done = self.gpu(*args)
            self.assertEqual(done.returncode, 2, args)
            self.assertIn("usage: agent-desk gpu", done.stderr)

    def test_a_slot_count_that_is_not_a_positive_number_is_refused(self):
        for slots in ("0", "two", "-1", "01"):
            done = self.gpu("--", "true", slots=slots)
            self.assertEqual(done.returncode, 2, slots)
            self.assertIn("AGENT_DESK_GPU_SLOTS", done.stderr)

    def test_an_empty_slot_count_means_one_slot(self):
        self.gpu("--", "true", slots="")
        self.assertEqual(sorted(path.name for path in (self.runtime / "agent-desk").iterdir()),
                         ["gpu.queue", "gpu.slot1"])

    def test_a_setting_file_that_is_not_a_positive_number_is_refused(self):
        self.setting.parent.mkdir(parents=True)
        self.setting.write_text("two\n")
        done = self.gpu("--", "true")
        self.assertEqual(done.returncode, 2)
        self.assertIn(str(self.setting), done.stderr)

    def test_the_locks_live_in_the_runtime_folder(self):
        self.gpu("--", "true", slots=2)
        self.assertEqual(sorted(path.name for path in (self.runtime / "agent-desk").iterdir()),
                         ["gpu.queue", "gpu.slot1", "gpu.slot2"])


class SlotTest(GpuCase):
    def test_one_slot_runs_two_commands_one_after_the_other_and_a_short_wait_says_nothing_more(self):
        first = self.hold("first")
        self.until_running("first")
        second = self.hold("second")
        self.until_waiting("second")
        # The waiting call tries the slot several times, under the 15 minutes before it says more.
        time.sleep(0.3)
        self.assertFalse(self.ran("second"))
        self.assertEqual(self.stderr("second"), "agent-desk gpu: waiting for the GPU slot, held by: first\n")
        self.release("first", first)
        self.until_running("second")
        self.release("second", second)
        self.assertEqual(self.stderr("first"), "")
        self.assertEqual(self.stderr("second"), "agent-desk gpu: waiting for the GPU slot, held by: first\n")

    def test_two_slots_run_two_commands_at_once_and_a_third_waits(self):
        first = self.hold("first", slots=2)
        second = self.hold("second", slots=2)
        self.until_running("first")
        self.until_running("second")
        third = self.hold("third", slots=2)
        self.until_waiting("third")
        self.assertEqual(self.stderr("third"), "agent-desk gpu: waiting for a GPU slot, held by: first second\n")
        self.assertFalse(self.ran("third"))
        self.release("second", second)
        self.until_running("third")
        self.release("first", first)
        self.release("third", third)

    def test_the_command_s_children_do_not_keep_the_slot(self):
        pid_file = self.root / "daemon.pid"
        done = self.gpu("--", "sh", "-c", f'sleep 30 >/dev/null 2>&1 & echo $! > "{pid_file}"')
        self.assertEqual(done.returncode, 0, done.stderr)
        daemon = int(pid_file.read_text())
        self.addCleanup(os.kill, daemon, 9)
        os.kill(daemon, 0)
        after = self.gpu("--", "true")
        self.assertEqual(after.returncode, 0)
        self.assertEqual(after.stderr, "")
        for fd in os.listdir(f"/proc/{daemon}/fd"):
            target = os.readlink(f"/proc/{daemon}/fd/{fd}")
            self.assertNotIn(str(self.runtime), target)


class ExclusiveTest(GpuCase):
    def test_exclusive_waits_for_every_shared_holder(self):
        first = self.hold("first", slots=2)
        second = self.hold("second", slots=2)
        self.until_running("first")
        self.until_running("second")
        bench = self.hold("bench", "--exclusive", slots=2)
        self.until_waiting("bench")
        self.assertEqual(self.stderr("bench"), "agent-desk gpu: waiting for all 2 GPU slots, held by: first second\n")
        self.release("first", first)
        time.sleep(0.2)
        self.assertFalse(self.ran("bench"))
        self.release("second", second)
        self.until_running("bench")
        self.release("bench", bench)
        # The exclusive call's record names who held the slots and how long it queued. The shared calls started
        # together, so their records come in either order, and one may have waited a moment for the other.
        calls = {Path(call["checkout"]).name: call for call in self.calls()}
        self.assertEqual(sorted(calls), ["bench", "first", "second"])
        self.assertEqual([calls[name]["exclusive"] for name in ("first", "second", "bench")], [0, 0, 1])
        self.assertEqual(calls["bench"]["held_by"], "first second")
        self.assertGreaterEqual(calls["bench"]["got"] - calls["bench"]["asked"], 0.2)
        self.assertGreaterEqual(calls["bench"]["got"], calls["second"]["released"])
        self.assertIsNotNone(calls["bench"]["released"])

    def test_exclusive_with_one_slot_waits_while_a_two_slot_caller_holds_slot_2(self):
        first = self.hold("first", slots=2)
        self.until_running("first")
        second = self.hold("second", slots=2)
        self.until_running("second")
        self.release("first", first)
        bench = self.hold("bench", "--exclusive", slots=1)
        self.until_waiting("bench")
        self.assertEqual(self.stderr("bench"), "agent-desk gpu: waiting for all 2 GPU slots, held by: second\n")
        time.sleep(0.2)
        self.assertFalse(self.ran("bench"))
        self.release("second", second)
        self.until_running("bench")
        self.release("bench", bench)

class SettingTest(GpuCase):
    def slots(self, *args, slots=None):
        return self.gpu("slots", *args, slots=slots)

    def test_with_no_setting_the_machine_has_one_slot(self):
        done = self.slots()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"GPU slots: 1, the default\n{ROOMY}slot 1: free\n")

    def test_the_environment_overrides_the_setting_file(self):
        self.assertEqual(self.slots("2").returncode, 0)
        first = self.hold("first", slots=1)
        self.until_running("first")
        second = self.hold("second", slots=1)
        self.until_waiting("second")
        self.assertFalse(self.ran("second"))
        self.release("first", first)
        self.until_running("second")
        self.release("second", second)
        done = self.slots(slots=1)
        self.assertEqual(done.stdout, f"GPU slots: 1, from AGENT_DESK_GPU_SLOTS\n{ROOMY}slot 1: free\n")

    def test_setting_the_count_under_an_override_says_the_override_wins_here(self):
        done = self.slots("3", slots=1)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(self.setting.read_text(), "3\n")
        self.assertIn("AGENT_DESK_GPU_SLOTS=1 overrides the setting", done.stderr)

    def test_slots_names_who_holds_each_slot_and_the_queue(self):
        self.assertEqual(self.slots("2").returncode, 0)
        first = self.hold("first")
        self.until_running("first")
        self.backdate(first, 18 * 60 + 5)
        bench = self.hold("bench", "--exclusive")
        self.until_waiting("bench")
        # The exclusive command takes the slots in order, so it waits for slot 1 before it takes slot 2.
        self.assertEqual(self.slots().stdout,
                         f"GPU slots: 2, set in {self.setting}\n{ROOMY}slot 1: first, shared, 18m\nslot 2: free\n"
                         "next in line: bench\n")
        self.release("first", first)
        self.until_running("bench")
        self.release("bench", bench)

    def test_slots_lists_a_held_slot_past_the_count(self):
        first = self.hold("first", slots=2)
        self.until_running("first")
        second = self.hold("second", slots=2)
        self.until_running("second")
        self.backdate(second, 5 * 60 + 20)
        self.release("first", first)
        self.assertEqual(self.slots().stdout,
                         f"GPU slots: 1, the default\n{ROOMY}slot 1: free\nslot 2: second, shared, 5m, past the count\n")
        self.release("second", second)

    def test_slots_names_every_holder_while_other_processes_take_and_release_locks(self):
        # /proc/locks shifts when a lock elsewhere comes or goes as it is read, and reading it there missed a held slot
        # in about one call in seven while two slots were held and these ran. Four held slots give each call twice the
        # chances to miss one, so 15 calls catch it as often as 30 calls with two.
        names = ("first", "second", "third", "fourth")
        holders = []
        for name in names:
            holders.append(self.hold(name, slots=4))
            self.until_running(name)
        for holder in holders:
            self.backdate(holder, 125)
        for n in range(4):
            self.churn(self.root / f"churn{n}")
        for _ in range(15):
            done = self.slots(slots=4)
            self.assertEqual(done.stdout, "GPU slots: 4, from AGENT_DESK_GPU_SLOTS\n"
                                          "Slots past the first open with 7 GB of VRAM free, the default; 15.3 GB free "
                                          "now\n" + "".join(f"slot {n}: {name}, shared, 2m\n"
                                                            for n, name in enumerate(names, 1)))
        for name, holder in zip(names, holders):
            self.release(name, holder)

    def churn(self, folder):
        """Starts a process that locks 300 files in folder and releases and retakes 100 of them until the test ends."""
        folder.mkdir()
        stop, ready = folder / "stop", folder / "ready"
        script = """
import fcntl, sys
from pathlib import Path
folder = Path(sys.argv[1])
files = [open(folder / str(n), "w") for n in range(300)]
for f in files:
    fcntl.flock(f, fcntl.LOCK_EX)
(folder / "ready").touch()
while not (folder / "stop").exists():
    for f in files[:100]:
        fcntl.flock(f, fcntl.LOCK_UN)
        fcntl.flock(f, fcntl.LOCK_EX)
"""
        process = subprocess.Popen([sys.executable, "-I", "-c", script, str(folder)])
        self.addCleanup(self.finish, process, stop)
        self.until(ready.exists, f"{folder.name} to lock its files")

    def test_a_bad_count_or_extra_arguments_are_refused(self):
        for args in (("0",), ("two",), ("1", "2")):
            done = self.slots(*args)
            self.assertEqual(done.returncode, 2, args)
        self.assertFalse(self.setting.exists())


class VramTest(GpuCase):
    LOW = "10000, 16303\n"  # 6.3 GB free, under the default 7 GB

    def without_nvidia_smi(self):
        """Gives every later call a PATH with the tools agent-desk gpu and the tests run, and no nvidia-smi."""
        tools = self.root / "tools"
        tools.mkdir()
        for name in ("bash", "sh", "flock", "lslocks", "awk", "sort", "paste", "readlink", "basename", "dirname",
                     "mktemp", "mv", "rm", "mkdir", "id", "python3", "timeout", "touch", "sleep", "cat", "true"):
            (tools / name).symlink_to(shutil.which(name))
        self.env["PATH"] = str(tools)

    def test_a_second_slot_opens_with_the_vram_free(self):
        self.nvidia("9303, 16303\n")  # exactly 7 GB free
        first = self.hold("first", slots=2)
        self.until_running("first")
        second = self.hold("second", slots=2)
        self.until_running("second")
        self.assertEqual(self.stderr("second"), "")
        self.assertEqual((self.root / "nvidia.args").read_text(),
                         "--query-gpu=memory.used,memory.total --format=csv,noheader,nounits\n")
        self.release("first", first)
        self.release("second", second)

    def test_slot_1_never_reads_the_vram(self):
        self.nvidia(self.LOW)
        done = self.gpu("--", "true", slots=2)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertFalse((self.root / "nvidia.args").exists())

    def test_a_second_slot_waits_below_the_threshold_until_the_vram_frees(self):
        self.nvidia(self.LOW)
        first = self.hold("first", slots=2)
        self.until_running("first")
        second = self.hold("second", slots=2)
        self.until_waiting("second")
        self.assertEqual(self.stderr("second"), "agent-desk gpu: waiting for VRAM, 6.3 GB free of the 7 GB a second "
                                                "slot needs, or for slot 1, held by: first\n")
        time.sleep(0.3)
        self.assertFalse(self.ran("second"))
        self.nvidia("2000, 16303\n")
        self.until_running("second")
        self.release("first", first)
        self.release("second", second)

    def test_without_nvidia_smi_calls_use_slot_1_only(self):
        self.without_nvidia_smi()
        first = self.hold("first", slots=2)
        self.until_running("first")
        self.assertEqual(self.stderr("first"), "")
        second = self.hold("second", slots=2)
        self.until_waiting("second")
        self.assertEqual(self.stderr("second"), "agent-desk gpu: waiting for slot 1, held by: first; nvidia-smi gives "
                                                "no VRAM reading, so no other slot opens\n")
        time.sleep(0.3)
        self.assertFalse(self.ran("second"))
        self.release("first", first)
        self.until_running("second")
        self.release("second", second)

    def test_a_failed_or_unreadable_reading_gives_no_vram_reading(self):
        # A call reads the VRAM as `slots` does, and without a reading opens slot 1 only, as the test above shows.
        cases = (("1000, 16303\n", 1), ("[N/A], [N/A]\n", 0), ("", 0), ("1000\n", 0), ("1000, 16303, 7\n", 0))
        for output, status in cases:
            with self.subTest(output=output, status=status):
                self.nvidia(output, status)
                done = self.gpu("slots", slots=2)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("nvidia-smi gives no VRAM reading, so only slot 1 opens", done.stdout)

    def test_the_card_with_the_most_memory_counts(self):
        for output, free in (("100, 24000\n10000, 16303\n", "23.9"), ("100, 8000\n10000, 16303\n", "6.3")):
            with self.subTest(output=output):
                self.nvidia(output)
                self.assertIn(f"; {free} GB free now\n", self.gpu("slots", slots=2).stdout)

    def test_exclusive_holds_every_slot_whatever_the_vram(self):
        self.without_nvidia_smi()
        bench = self.hold("bench", "--exclusive", slots=2)
        self.until_running("bench")
        self.assertEqual(self.stderr("bench"), "")
        self.release("bench", bench)

    def test_the_threshold_setting_changes_what_a_second_slot_needs(self):
        self.nvidia(self.LOW)
        done = self.gpu("slots", "--min-free-vram", "6")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.vram_setting.read_text(), "6\n")
        self.assertEqual(done.stdout, "GPU slots: 1, the default\n"
                                      f"Slots past the first open with 6 GB of VRAM free, set in {self.vram_setting}; "
                                      "6.3 GB free now\nslot 1: free\n")
        first = self.hold("first", slots=2)
        self.until_running("first")
        second = self.hold("second", slots=2)
        self.until_running("second")
        self.release("first", first)
        self.release("second", second)

    def test_a_threshold_of_0_opens_every_slot_without_reading_the_vram(self):
        self.without_nvidia_smi()
        done = self.gpu("slots", "2", "--min-free-vram", "0")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.setting.read_text(), "2\n")
        self.assertEqual(done.stdout, f"GPU slots: 2, set in {self.setting}\n"
                                      f"Slots past the first open whatever VRAM is free, set in {self.vram_setting}\n"
                                      "slot 1: free\nslot 2: free\n")
        first = self.hold("first")
        second = self.hold("second")
        self.until_running("first")
        self.until_running("second")

    def test_slots_says_when_nvidia_smi_gives_no_reading(self):
        self.without_nvidia_smi()
        done = self.gpu("slots", "--min-free-vram", "6.5")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[1],
                         f"Slots past the first open with 6.5 GB of VRAM free, set in {self.vram_setting}; nvidia-smi "
                         "gives no VRAM reading, so only slot 1 opens")

    def test_a_bad_threshold_is_refused(self):
        for args in (("--min-free-vram", "seven"), ("--min-free-vram", "-1"), ("--min-free-vram",),
                     ("2", "--min-free-vram", ""), ("0", "--min-free-vram", "6")):
            done = self.gpu("slots", *args)
            self.assertEqual(done.returncode, 2, args)
        self.assertFalse(self.setting.exists())
        self.assertFalse(self.vram_setting.exists())

    def test_a_threshold_file_that_is_not_a_number_is_refused_by_a_shared_call_with_two_slots(self):
        self.vram_setting.parent.mkdir(parents=True)
        self.vram_setting.write_text("lots\n")
        done = self.gpu("--", "true", slots=2)
        self.assertEqual(done.returncode, 2)
        self.assertIn(str(self.vram_setting), done.stderr)
        self.assertEqual(self.gpu("--", "true", slots=1).returncode, 0)
        self.assertEqual(self.gpu("--exclusive", "--", "true", slots=2).returncode, 0)


class HoldTest(GpuCase):
    def slot_lines(self, slots=2):
        return [line for line in self.gpu("status", slots=slots).stdout.splitlines() if line.startswith("slot ")]

    def test_status_names_each_holder_s_mode_and_hold_time_and_marks_long_holds(self):
        first = self.hold("first", slots=2)
        self.until_running("first")
        second = self.hold("second", slots=2)
        self.until_running("second")
        self.until(self.held_file(first).exists, "the hold file")
        self.assertRegex(self.slot_lines()[0], r"^slot 1: first, shared, \ds$")
        self.backdate(first, 29 * 60 + 50)
        self.backdate(second, 30 * 60 + 10)
        self.assertEqual(self.slot_lines(), ["slot 1: first, shared, 29m", "slot 2: second, shared, 30m, long"])
        self.release("first", first)
        self.release("second", second)
        bench = self.hold("bench", "--exclusive", slots=2)
        self.until_running("bench")
        self.backdate(bench, 44 * 60 + 50)
        self.assertEqual(self.slot_lines(), ["slot 1: bench, exclusive, 44m", "slot 2: bench, exclusive, 44m"])
        self.backdate(bench, 45 * 60 + 10)
        self.assertEqual(self.slot_lines()[0], "slot 1: bench, exclusive, 45m, long")
        self.backdate(bench, 2 * 3600 + 5 * 60)
        self.assertEqual(self.slot_lines()[0], "slot 1: bench, exclusive, 2h05m, long")
        self.release("bench", bench)

    def test_the_hold_file_goes_when_the_call_ends_and_a_dead_process_s_file_is_removed(self):
        folder = self.runtime / "agent-desk"
        folder.mkdir()
        stale = folder / "gpu.held.999999999"
        stale.write_text("1 true\n")
        first = self.hold("first")
        self.until_running("first")
        self.until(self.held_file(first).exists, "the hold file")
        self.assertEqual(self.held_file(first).read_text().split()[1], "false")
        self.assertFalse(stale.exists())
        self.release("first", first)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["gpu.queue", "gpu.slot1"])

    def test_a_holder_without_a_hold_file_is_named_alone(self):
        first = self.hold("first")
        self.until_running("first")
        self.until(self.held_file(first).exists, "the hold file")
        self.held_file(first).unlink()
        self.assertEqual(self.slot_lines(slots=1), ["slot 1: first"])
        self.release("first", first)


class LongWaitTest(GpuCase):
    def setUp(self):
        super().setUp()
        self.env["AGENT_DESK_GPU_LONG_WAIT"] = "1"

    def until_still_waiting(self, name, times=1):
        self.until(lambda: self.stderr(name).count("still waiting") >= times, f"{name} to say it still waits")

    def test_each_long_wait_names_the_holders_with_their_hold_times_and_who_is_next(self):
        first = self.hold("first", slots=2)
        self.until_running("first")
        self.backdate(first, 50 * 60)
        bench = self.hold("bench", "--exclusive", slots=2)
        self.until_waiting("bench")
        late = self.hold("late", slots=2)
        self.until_still_waiting("bench")
        self.until_still_waiting("late")
        self.assertRegex(self.stderr("bench").splitlines()[1],
                         r"^agent-desk gpu: still waiting for all 2 GPU slots after \ds, held by: first, shared, 50m,"
                         r" long$")
        # The late call waits on the queue the exclusive one keeps, and bench, still taking its slots, has no hold time.
        self.assertRegex(self.stderr("late").splitlines()[1],
                         r"^agent-desk gpu: still waiting for a GPU slot after \ds, held by: first, shared, 50m, long;"
                         r" next in line: bench$")
        self.release("first", first)
        self.until_running("bench")
        # The late call now waits for the slots bench holds, and says so again at its next long wait.
        self.until_still_waiting("late", times=2)
        self.assertRegex(self.stderr("late").splitlines()[2],
                         r"^agent-desk gpu: still waiting for a GPU slot after \ds, held by: bench, exclusive, \ds$")
        self.release("bench", bench)
        self.until_running("late")
        self.release("late", late)


class QueueTest(GpuCase):
    def test_a_later_shared_command_lines_up_behind_a_waiting_exclusive_one(self):
        first = self.hold("first", slots=2)
        self.until_running("first")
        bench = self.hold("bench", "--exclusive", slots=2)
        self.until_waiting("bench")
        # A slot is free, but the exclusive command keeps the queue until it holds both.
        late = self.hold("late", slots=2)
        self.until_waiting("late")
        self.assertEqual(self.stderr("late"),
                         "agent-desk gpu: waiting for a GPU slot, held by: first; next in line: bench\n")
        self.assertFalse(self.ran("late"))
        self.release("first", first)
        self.until_running("bench")
        time.sleep(0.2)
        self.assertFalse(self.ran("late"))
        self.release("bench", bench)
        self.until_running("late")
        self.release("late", late)


class RecordTest(GpuCase):
    def test_a_shared_call_outside_a_run_records_its_checkout_and_times_with_no_wait(self):
        before = time.time()
        self.assertEqual(self.gpu("--", "sleep", "0.2").returncode, 0)
        [call] = self.calls()
        self.assertEqual((call["run"], call["checkout"], call["exclusive"], call["held_by"]),
                         (None, str(self.root), 0, None))
        self.assertEqual(call["asked"], call["got"])
        self.assertGreaterEqual(call["asked"], before)
        self.assertGreaterEqual(call["released"] - call["got"], 0.2)

    def test_a_call_in_a_run_adds_to_the_run_s_totals_as_it_ends(self):
        self.database.parent.mkdir(parents=True)
        desk = store.Store(self.database)
        self.addCleanup(desk.close)
        run = desk.request_run("org/game", "agent", "12")
        desk.run_started(run, "agent-desk-agent-org-game-12-1")
        self.env["AGENT_DESK_RUN"] = str(run)
        first = self.hold("first")
        self.until_running("first")
        second = self.hold("second")
        self.until_waiting("second")
        time.sleep(0.2)
        self.release("first", first)
        self.until_running("second")
        self.release("second", second)
        row = desk.run(run)
        calls = self.calls()
        self.assertEqual([call["run"] for call in calls], [run, run])
        self.assertAlmostEqual(row["gpu_queued"] * 60, calls[1]["got"] - calls[1]["asked"], places=3)
        self.assertAlmostEqual(row["gpu_held"] * 60, sum(call["released"] - call["got"] for call in calls), places=3)
        self.assertGreaterEqual(row["gpu_queued"] * 60, 0.2)
        self.assertEqual([event["type"] for event in desk.events_since()][-2:], ["gpu-used", "gpu-used"])

    def test_a_call_killed_with_a_signal_records_its_release_and_its_command_runs_on(self):
        process = self.hold("first")
        self.until_running("first")
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=10), -signal.SIGTERM)
        [call] = self.calls()
        self.assertIsNotNone(call["released"])
        # The slot freed while the command runs on, as before.
        self.assertEqual(self.gpu("--", "true").stderr, "")

    def test_a_call_killed_outright_leaves_an_open_record_no_total_or_wait_counts(self):
        self.database.parent.mkdir(parents=True)
        desk = store.Store(self.database)
        self.addCleanup(desk.close)
        run = desk.request_run("org/game", "agent", "12")
        self.env["AGENT_DESK_RUN"] = str(run)
        first = self.hold("first")
        self.until_running("first")
        waiter = self.hold("waiter")
        self.until_waiting("waiter")
        waiter.kill()
        waiter.wait(timeout=10)
        self.release("first", first)
        first_call, killed = self.calls()
        self.assertIsNone(killed["released"])
        row = desk.run(run)
        self.assertAlmostEqual(row["gpu_held"] * 60, first_call["released"] - first_call["got"], places=3)
        self.assertEqual(row["gpu_queued"], 0)
        self.assertEqual(desk.gpu_waits(0), [])

    def test_status_names_the_holders_and_the_day_s_waits_by_checkout(self):
        first = self.hold("first")
        self.until_running("first")
        self.backdate(first, 3 * 60)
        second = self.hold("second")
        self.until_waiting("second")
        done = self.gpu("status")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"GPU slots: 1, the default\n{ROOMY}slot 1: first, shared, 3m\n"
                                      "next in line: second\n"
                                      "No GPU call waited in the last day.\n")
        self.release("first", first)
        self.until_running("second")
        self.release("second", second)
        done = self.gpu("status")
        lines = done.stdout.splitlines()
        self.assertEqual(lines[:4], ["GPU slots: 1, the default", ROOMY.strip(), "slot 1: free",
                                     "GPU waits in the last day, by checkout:"])
        self.assertRegex(lines[4], r"^  second: 1 wait, \d+ s queued, longest \d+ s behind first$")

    def test_a_store_that_refuses_the_record_leaves_the_call_alone(self):
        (self.root / "state").write_text("not a folder")
        done = self.gpu("--", "sh", "-c", "echo out; exit 4")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (4, "out\n", ""))


if __name__ == "__main__":
    unittest.main()
