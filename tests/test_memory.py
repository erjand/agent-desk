"""Tests for agent-desk/lib/memory.py, which holds a run until the machine has the memory for it."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import memory  # noqa: E402

SETTINGS = {**memory.DEFAULTS, "reserveGB": 8, "needGB": {"agent": 10, "chore": 4}, "labelGB": {"assets": 14}}


def runs(count, peak, kind="agent", labels=(), repo="org/game", minutes=None):
    return [(repo, kind, set(labels), peak, minutes)] * count


def timed(minutes, labels=(), kind="agent"):
    """One run of each of `minutes` to peak."""
    return [("org/game", kind, set(labels), 2.0, value) for value in minutes]


class NeedTest(unittest.TestCase):
    def test_without_enough_history_the_config_sets_the_need_raised_by_a_heavy_label(self):
        self.assertEqual(memory.need("org/game", "agent", [], memory.DEFAULTS, []), 6)
        self.assertEqual(memory.need("org/game", "agent", [], SETTINGS, runs(4, 3.0)), 10)
        self.assertEqual(memory.need("org/game", "agent", ["assets", "bug"], SETTINGS, []), 14)
        self.assertEqual(memory.need("org/game", "chore/audit", [], SETTINGS, []), 4)

    def test_enough_runs_of_a_kind_set_the_need_from_their_peaks_with_headroom(self):
        history = runs(9, 3.0) + runs(1, 5.0) + runs(5, 9.0, repo="org/other") + runs(5, 1.0, kind="chore/audit")
        self.assertEqual(memory.need("org/game", "agent", [], SETTINGS, history), 6.0)
        self.assertEqual(memory.need("org/game", "chore/audit", [], SETTINGS, history), 1.2)

    def test_a_label_with_runs_of_its_own_raises_the_need_and_one_without_keeps_its_configured_need(self):
        history = runs(6, 2.0) + runs(3, 7.0, labels=["blender"])
        self.assertEqual(memory.need("org/game", "agent", ["blender"], SETTINGS, history), 8.4)
        self.assertEqual(memory.need("org/game", "agent", ["assets"], SETTINGS, runs(6, 2.0)), 14)


class SettleTest(unittest.TestCase):
    def test_too_few_timed_runs_learn_no_settle_time(self):
        history = timed(range(1, 20)) + runs(50, 2.0) + timed(range(1, 30), kind="fix")
        self.assertIsNone(memory.settle("org/game", "agent", [], history))

    def test_enough_timed_runs_set_it_at_their_95th_percentile_times_one_and_a_half(self):
        # 20 runs that peaked after 1 to 20 minutes: the 95th percentile is 20, so 30 minutes.
        self.assertEqual(memory.settle("org/game", "agent", [], timed(range(1, 21))), 30)
        self.assertEqual(memory.settle("org/game", "agent", [], timed([4] * 19 + [20]) + runs(9, 2.0)), 30)
        self.assertIsNone(memory.settle("org/other", "agent", [], timed(range(1, 21))))

    def test_a_label_with_enough_timed_runs_of_its_own_can_only_lengthen_it(self):
        history = timed([10] * 200) + timed([30] * 10, labels=["blender"]) + timed([2] * 10, labels=["docs"])
        self.assertEqual(memory.settle("org/game", "agent", [], history), 15)
        self.assertEqual(memory.settle("org/game", "agent", ["blender", "bug"], history), 45)
        self.assertEqual(memory.settle("org/game", "agent", ["docs"], history), 15)
        few = timed([10] * 200) + timed([30] * 9, labels=["blender"])
        self.assertEqual(memory.settle("org/game", "agent", ["blender"], few), 15)

    def test_a_learned_settle_time_past_45_minutes_stops_at_45(self):
        # Runs that peaked after 98 minutes learn 147 minutes, and the cap takes it to 45.
        self.assertEqual(memory.MAX_SETTLE_MINUTES, 45)
        self.assertEqual(memory.settle("org/game", "agent", [], timed([98] * 20)), 45)
        self.assertEqual(memory.settle("org/game", "agent", ["blender"],
                                       timed([10] * 20) + timed([40] * 10, labels=["blender"])), 45)
        self.assertEqual(memory.settle("org/game", "agent", [], timed([29] * 20)), 43.5)


def meminfo(folder, total_kb):
    path = Path(folder) / "meminfo"
    path.write_text(f"MemTotal:       {total_kb} kB\nMemFree:        1024 kB\nMemAvailable:   2048 kB\n")
    return path


GB_KB = 1024**2


class DefaultsTest(unittest.TestCase):
    def test_the_scaled_defaults_take_their_share_of_16_64_and_128_gb(self):
        expected = {16: (2, 3, 3, 2), 64: (8, 12, 12, 8), 128: (16, 24, 24, 16)}
        for total, shares in expected.items():
            names = ["reserveGB", "minFreeGB", "agentMemoryHigh", "choreMemoryHigh"]
            self.assertEqual(tuple(memory.default_gb(name, total) for name in names), shares, total)

    def test_they_round_up_from_the_memtotal_a_machine_reports_short_of_its_installed_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            # The MemTotal of this repository's 64 GB workstation, 60.2 GB, and of typical 16 and 128 GB machines.
            for total_kb, reserve, min_free in [(63121632, 8, 12), (16049540, 2, 3), (131590000, 16, 24)]:
                path = meminfo(folder, total_kb)
                with mock.patch.object(memory, "MEMINFO", str(path)):
                    self.assertEqual((memory.default_gb("reserveGB"), memory.default_gb("minFreeGB")),
                                     (reserve, min_free), total_kb)

    def test_a_tiny_machine_still_gets_one_gb(self):
        self.assertEqual(memory.default_gb("reserveGB", 2), 1)

    def test_an_unset_reserve_scales_with_the_machine_and_a_set_one_stays(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / ".agent-desk.json"
            config.write_text('{"resources": {"needGB": {"agent": 9}}}')
            env = {"DESK_CONFIG": str(config)}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(memory, "MEMINFO", str(meminfo(folder, 16 * GB_KB))):
                settings = memory.resources()
                self.assertEqual(settings["reserveGB"], 2)
                self.assertEqual(settings["needGB"], {"agent": 9, "chore": 3, "fix": 3, "discuss": 2})
                config.write_text('{"resources": {"reserveGB": 5}}')
                self.assertEqual(memory.resources()["reserveGB"], 5)
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(memory, "MEMINFO", str(meminfo(folder, 128 * GB_KB))):
                config.write_text("{}")
                self.assertEqual(memory.resources()["reserveGB"], 16)
                self.assertEqual(memory.resources()["needGB"], {"agent": 6, "chore": 3, "fix": 3, "discuss": 2})


class DefaultCommandTest(unittest.TestCase):
    def test_the_command_prints_a_default_in_whole_gb(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(memory, "MEMINFO", str(meminfo(folder, 64 * GB_KB))):
                out = io.StringIO()
                with redirect_stdout(out):
                    memory.main(["memory.py", "default", "minFreeGB"])
        self.assertEqual(out.getvalue(), "12\n")


def pressure_file(folder, avg60, name="pressure"):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"some avg10=0.00 avg60={avg60:.2f} avg300=0.00 total=100\n"
                    f"full avg10=0.00 avg60=0.00 avg300=0.00 total=50\n")
    return path


class PressureTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.user = self.root / "user@1000.service"
        self.machine = pressure_file(self.root, 37.03)
        for patch in (mock.patch.object(memory, "USER_CGROUP", str(self.user)),
                      mock.patch.object(memory, "PRESSURE", str(self.machine)),
                      mock.patch.object(memory, "CLAIMS", self.root / "c"),
                      mock.patch.object(memory, "available_gb", lambda: 60.0)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_run_stalled_at_its_own_memory_high_leaves_your_apps_unpressed_so_runs_start(self):
        pressure_file(self.user, 37.03, "agentdesk.slice/memory.pressure")
        pressure_file(self.user, 0.0, "app.slice/memory.pressure")
        pressure_file(self.user, 0.0, "session.slice/memory.pressure")
        self.assertEqual(memory.pressure(), 0.0)
        self.assertIsNone(memory.held_by(10, SETTINGS))

    def test_it_reads_the_larger_share_of_the_last_minute_your_apps_stalled(self):
        pressure_file(self.user, 3.5, "app.slice/memory.pressure")
        pressure_file(self.user, 12.34, "session.slice/memory.pressure")
        self.assertEqual(memory.pressure(), 12.34)
        held = memory.held_by(10, SETTINGS)
        self.assertEqual(held, "memory pressure is 12.34%, over 5%")
        (self.user / "session.slice" / "memory.pressure").unlink()
        self.assertEqual(memory.pressure(), 3.5)

    def test_without_your_apps_cgroups_it_reads_the_machines_pressure(self):
        self.assertEqual(memory.pressure(), 37.03)

    def test_a_kernel_without_any_pressure_file_counts_as_no_pressure(self):
        self.machine.unlink()
        self.assertEqual(memory.pressure(), 0.0)
        self.machine.write_text("some avg10=\n")
        self.assertEqual(memory.pressure(), 0.0)

    def test_the_config_sets_the_most_pressure_a_run_starts_under(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / ".agent-desk.json"
            config.write_text("{}")
            self.assertEqual(memory.resources(config)["maxMemoryPressure"], 5)
            config.write_text('{"resources": {"maxMemoryPressure": 20}}')
            self.assertEqual(memory.resources(config)["maxMemoryPressure"], 20)


class ClaimsTest(unittest.TestCase):
    def test_a_claim_counts_until_its_process_ends_however_old_it_is(self):
        with tempfile.TemporaryDirectory() as folder:
            claims = Path(folder)
            now = time.time()
            (claims / "live").write_text(f"10 {now - 60} {os.getpid()}")
            (claims / "old").write_text(f"6 {now - 11 * 60} {os.getpid()}")
            (claims / "gone").write_text(f"4 {now} 999999999")
            self.assertEqual(memory.claimed_gb(claims, used=lambda pid: 0.0), 16)
            self.assertEqual(sorted(path.name for path in claims.iterdir()), ["live", "old"])
            (claims / str(os.getpid())).write_text(f"6 {now - 35 * 60} {os.getpid()}")
            self.assertEqual(memory.claim_of(os.getpid(), claims), 6)

    def test_a_claim_counts_only_the_memory_its_run_has_not_taken_up_yet(self):
        with tempfile.TemporaryDirectory() as folder:
            claims = Path(folder)
            now = time.time()
            (claims / "agent").write_text(f"10 {now} {os.getpid()}")
            self.assertEqual(memory.claimed_gb(claims, used=lambda pid: 3.5), 6.5)
            self.assertEqual(memory.claimed_gb(claims, used=lambda pid: 12.0), 0)

    def test_the_claims_command_prints_each_live_claim_with_its_unit_and_leaves_ended_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            claims = Path(folder) / "claims"
            claims.mkdir()
            now = time.time()
            (claims / "agent").write_text(f"9.8 {now} {os.getpid()} - {Path(folder) / 'peak'}\n")
            (claims / "gone").write_text(f"4 {now} 999999999\n")
            with mock.patch.object(memory, "CLAIMS", claims), \
                    mock.patch.object(memory, "used_gb", lambda pid: 0.25), \
                    mock.patch.object(memory, "unit_of", lambda pid: "agent-desk-agent-me-fence-12-7.service"):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(memory.main(["memory.py", "claims"]), 0)
            self.assertEqual(json.loads(out.getvalue()), [
                {"pid": os.getpid(), "unit": "agent-desk-agent-me-fence-12-7.service", "claimed": 9.8, "counted": 9.8,
                 "peak": None, "settle": None, "used": 0.25, "held": 9.55}])
            # The view reads only, so the ended run's claim stays for `wait` to delete.
            self.assertEqual(sorted(path.name for path in claims.iterdir()), ["agent", "gone"])

    def test_the_claims_figures_add_up_to_what_the_gate_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            claims = Path(folder)
            now = time.time()
            (claims / "a").write_text(f"10 {now} {os.getpid()}")
            (claims / "b").write_text(f"3 {now} {os.getppid()}")
            used = lambda pid: 4.0
            figures = memory.claim_figures(claims, used)
            self.assertEqual(sorted(one["held"] for one in figures), [0.0, 6.0])
            self.assertEqual(memory.claimed_gb(claims, used), 6.0)


class ShrinkTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.claims = Path(folder.name) / "claims"
        self.claims.mkdir()
        self.peak = Path(folder.name) / "peak file"
        self.now = time.time()

    def claim(self, gb, settle, peak_gb, minutes_in):
        """A claim of `gb` with a settle time, for a run `minutes_in` minutes past its start that peaked at `peak_gb`."""
        (self.claims / "run").write_text(f"{gb} {self.now} {os.getpid()} {settle} {self.peak}\n")
        started = self.now - minutes_in * 60
        self.peak.write_text(f"{int(peak_gb * memory.GIB)} 60 {started}")

    def claimed(self):
        return memory.claimed_gb(self.claims, used=lambda pid: 0.0)

    def test_a_claim_counts_in_full_before_its_settle_time_and_shrinks_after(self):
        self.claim(9.7, 30, 0.6, 29)
        self.assertEqual(self.claimed(), 9.7)
        self.assertEqual(memory.shrunk(self.claims), [])
        self.claim(9.7, 30, 0.6, 31)
        self.assertEqual(self.claimed(), 2.6)
        self.assertEqual(memory.shrunk(self.claims), [(os.getpid(), 9.7, 2.6, 0.6, 30)])

    def test_the_shrunk_claim_keeps_room_above_the_peak_so_far(self):
        # Plus 2 GB for a small peak, and half again for a large one.
        self.claim(20, 30, 1.0, 45)
        self.assertEqual(self.claimed(), 3.0)
        self.claim(20, 30, 6.0, 45)
        self.assertEqual(self.claimed(), 9.0)

    def test_the_shrunk_claim_never_counts_more_than_the_claim(self):
        self.claim(9.7, 30, 8.0, 45)
        self.assertEqual(self.claimed(), 9.7)
        self.assertEqual(memory.shrunk(self.claims), [])

    def test_memory_the_run_holds_still_counts_against_the_shrunk_claim(self):
        self.claim(9.7, 30, 3.0, 45)
        self.assertEqual(memory.claimed_gb(self.claims, used=lambda pid: 1.0), 4.0)

    def test_a_claim_without_a_settle_time_or_a_peak_yet_counts_in_full(self):
        self.claim(9.7, "-", 0.6, 300)
        self.assertEqual(self.claimed(), 9.7)
        self.claim(9.7, 30, 0.6, 300)
        self.peak.write_text("")
        self.assertEqual(self.claimed(), 9.7)
        self.peak.unlink()
        self.assertEqual(self.claimed(), 9.7)
        self.assertEqual(memory.claim_of(os.getpid(), self.claims), None)
        (self.claims / str(os.getpid())).write_text(f"6 {self.now} {os.getpid()} 30 {self.peak}\n")
        self.assertEqual(memory.claim_of(os.getpid(), self.claims), 6)


class GateTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name)
        self.pressure = pressure_file(self.state, 0.0)
        for patch in (mock.patch.object(memory, "STATE", self.state),
                      mock.patch.object(memory, "CLAIMS", self.state / "c"),
                      mock.patch.object(memory, "PRESSURE", str(self.pressure)),
                      mock.patch.object(memory, "USER_CGROUP", str(self.state / "no-user-manager")),
                      mock.patch.object(memory, "resources", lambda config_path=None: SETTINGS),
                      mock.patch.object(memory, "history", lambda: []),
                      mock.patch.object(memory, "used_gb", lambda pid: 0.0)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_run_starts_when_free_memory_covers_its_need_and_the_reserve_or_it_waited_too_long(self):
        short = "the run needs 10 GB and 8 GB stays free for the user, and 17.0 GB is free"
        with mock.patch.object(memory, "available_gb", lambda: 17.0):
            self.assertEqual(memory.spare("org/game", "agent", [], 0), (False, 10, short))
            self.assertEqual(memory.spare("org/game", "agent", [], 121 * 60), (True, 10, short))
            reason = memory.spare("org/game", "agent", [], 0)[2]
            self.assertEqual((reason.short, reason.free, reason.needed), ("memory: 17.0 GB free of 18 GB needed", 17.0, 18))
        with mock.patch.object(memory, "available_gb", lambda: 18.0):
            with memory.claims_locked():
                self.assertEqual(memory.spare("org/game", "agent", [], 0), (True, 10, None))
                memory.claim(10, os.getpid())
            self.assertEqual(memory.spare("org/game", "agent", [], 0)[:2], (False, 10))

    def test_memory_pressure_over_the_most_holds_a_run_however_much_memory_is_free(self):
        held = "memory pressure is 12%, over 5%"
        with mock.patch.object(memory, "available_gb", lambda: 60.0):
            pressure_file(self.state, 12.0)
            self.assertEqual(memory.spare("org/game", "agent", [], 0), (False, 10, held))
            self.assertEqual(memory.spare("org/game", "agent", [], 121 * 60), (True, 10, held))
            reason = memory.spare("org/game", "agent", [], 0)[2]
            self.assertEqual((reason.short, reason.free), ("memory pressure under 5%, now 12%", None))
            pressure_file(self.state, 5.0)
            self.assertEqual(memory.spare("org/game", "agent", [], 0), (True, 10, None))
            self.pressure.unlink()
            self.assertEqual(memory.spare("org/game", "agent", [], 0), (True, 10, None))

    def test_a_fix_counts_no_other_runs_claim_and_starts_under_more_pressure(self):
        with mock.patch.object(memory, "available_gb", lambda: 18.0):
            memory.claim(10, os.getpid())
            self.assertEqual(memory.spare("org/game", "agent", [], 0)[:2], (False, 10))
            self.assertEqual(memory.spare("org/game", "fix", [], 0), (True, 6, None))
            pressure_file(self.state, 12.0)
            self.assertEqual(memory.spare("org/game", "agent", [], 0)[2], "memory pressure is 12%, over 5%")
            self.assertEqual(memory.spare("org/game", "fix", [], 0), (True, 6, None))
            pressure_file(self.state, 16.0)
            reason = memory.spare("org/game", "fix", [], 0)[2]
            self.assertEqual((reason, reason.short), ("memory pressure is 16%, over 15%",
                                                      "memory pressure under 15%, now 16%"))
        # The reserve still stays free for the user.
        pressure_file(self.state, 0.0)
        with mock.patch.object(memory, "available_gb", lambda: 13.0):
            self.assertEqual(memory.spare("org/game", "fix", [], 0)[:2], (False, 6))

    def test_a_run_whose_unit_the_dispatcher_claimed_for_starts_without_waiting(self):
        memory.claim(10, os.getpid())
        out = io.StringIO()
        with mock.patch.object(memory, "available_gb", lambda: 0.0), redirect_stdout(out):
            memory.wait("org/game", "agent", [], os.getpid())
        self.assertIn("Starting with the 10 GB the dispatcher claimed.", out.getvalue())
        self.assertIsNone(memory.claim_of(999999999))

    def test_the_run_adds_its_settle_time_and_peak_file_to_the_dispatchers_claim(self):
        memory.claim(10, os.getpid())
        out = io.StringIO()
        with mock.patch.object(memory, "history", lambda: timed(range(1, 21))), redirect_stdout(out):
            memory.wait("org/game", "agent", [], os.getpid(), "/tmp/the peak")
        gb, pid, minutes, peak_file = memory.read_claim(self.state / "c" / str(os.getpid()))
        self.assertEqual((gb, pid, minutes, peak_file), (10, os.getpid(), 30, Path("/tmp/the peak")))
        self.assertIn("The claim may shrink after 30 min, when nearly every past run of its kind had reached its peak.",
                      out.getvalue())


class HistoryTest(unittest.TestCase):
    def test_a_finished_run_joins_the_history_in_gigabytes_with_the_minutes_to_its_peak(self):
        with tempfile.TemporaryDirectory() as folder:
            peak = Path(folder) / "peak"
            peak.write_text(f"{3 * memory.GIB} 750 {time.time()}")
            path = Path(folder) / "usage.tsv"
            memory.record("org/game", "agent", "bug,assets", peak, path)
            peak.write_text(f"0 0 {time.time()}")
            memory.record("org/game", "agent", "", peak, path)
            self.assertEqual(memory.history(path), [("org/game", "agent", {"bug", "assets"}, 3.0, 12.5)])
            self.assertTrue(path.read_text().endswith("\t3.00\t12.5\n"))

    def test_rows_and_peak_files_from_before_the_minutes_read_without_them(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.tsv"
            path.write_text("2026-11-01T10:00:00\torg/game\tagent\tbug\t3.80\n")
            peak = Path(folder) / "peak"
            peak.write_text(str(2 * memory.GIB))
            memory.record("org/game", "fix", "", peak, path)
            self.assertEqual(memory.history(path), [("org/game", "agent", {"bug"}, 3.8, None),
                                                    ("org/game", "fix", set(), 2.0, None)])
            self.assertIsNone(memory.settle("org/game", "agent", [], memory.history(path) * 30))


class SampleTest(unittest.TestCase):
    def test_the_peak_file_keeps_the_peak_and_the_seconds_to_it_through_a_fall(self):
        readings = iter([1 * memory.GIB, 5 * memory.GIB, 2 * memory.GIB])
        clock = iter([1000.0, 1005.0, 1010.0, 1015.0])
        with tempfile.TemporaryDirectory() as folder:
            peak = Path(folder) / "peak"

            def anon(_):
                try:
                    return next(readings)
                except StopIteration:
                    raise OSError("the cgroup is gone") from None

            with mock.patch.object(memory, "cgroup_folder", lambda: Path(folder)), \
                    mock.patch.object(memory, "anon_bytes", anon), \
                    mock.patch.object(memory.time, "time", lambda: next(clock)), \
                    mock.patch.object(memory.time, "sleep", lambda _: None):
                memory.sample(peak)
            self.assertEqual(peak.read_text(), f"{5 * memory.GIB} 10 1000")
            self.assertEqual(memory.read_peak(peak), (5 * memory.GIB, 10.0, 1000.0))


class WaitTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name)
        self.pressure = pressure_file(self.state, 0.0)
        for patch in (mock.patch.object(memory, "STATE", self.state),
                      mock.patch.object(memory, "CLAIMS", self.state / "c"),
                      mock.patch.object(memory, "PRESSURE", str(self.pressure)),
                      mock.patch.object(memory, "USER_CGROUP", str(self.state / "no-user-manager")),
                      mock.patch.object(memory, "POLL_SECONDS", 0),
                      mock.patch.object(memory, "resources", lambda: SETTINGS),
                      mock.patch.object(memory, "history", lambda: []),
                      mock.patch.object(memory, "used_gb", lambda pid: 0.0)):
            patch.start()
            self.addCleanup(patch.stop)

    def wait(self):
        out = io.StringIO()
        with redirect_stdout(out):
            memory.wait("org/game", "agent", [], os.getpid())
        return out.getvalue()

    def test_a_run_waits_while_free_memory_less_claims_falls_short_then_claims_its_need(self):
        free = iter([15.0, 25.0])
        with mock.patch.object(memory, "available_gb", lambda: next(free)):
            out = self.wait()
        self.assertIn("Waiting for memory: the run needs 10 GB and 8 GB stays free for the user, and 15.0 GB is free.",
                      out)
        self.assertEqual((self.state / "c" / str(os.getpid())).read_text().split()[0], "10")
        self.assertFalse((self.state / "waiting").exists())

    def test_a_run_waits_while_memory_pressure_is_high_and_starts_once_it_falls(self):
        readings = iter([30.0, 12.0, 1.5])
        with mock.patch.object(memory, "available_gb", lambda: 60.0), \
                mock.patch.object(memory, "pressure", lambda: next(readings)):
            out = self.wait()
        self.assertIn("Waiting for memory: memory pressure is 30%, over 5%.", out)
        self.assertIn("Starting with 10 GB claimed.", out)
        self.assertEqual((self.state / "c" / str(os.getpid())).read_text().split()[0], "10")

    def test_its_claim_names_its_settle_time_and_peak_file_unless_the_project_turns_shrinking_off(self):
        with mock.patch.object(memory, "available_gb", lambda: 60.0), \
                mock.patch.object(memory, "history", lambda: timed(range(1, 21))):
            with redirect_stdout(io.StringIO()):
                memory.wait("org/game", "agent", [], os.getpid(), "/tmp/peak")
            self.assertEqual(memory.read_claim(self.state / "c" / str(os.getpid()))[2:],
                             (30, Path("/tmp/peak")))
            with mock.patch.object(memory, "resources", lambda: {**SETTINGS, "shrinkClaims": False}), \
                    redirect_stdout(io.StringIO()) as out:
                memory.wait("org/game", "agent", [], 999999999, "/tmp/peak")
            self.assertEqual(memory.read_claim(self.state / "c" / "999999999")[2:], (None, Path("/tmp/peak")))
            self.assertNotIn("may shrink", out.getvalue())

    def test_a_run_held_too_long_starts_and_says_what_held_it(self):
        pressure_file(self.state, 40.0)
        settings = {**SETTINGS, "maxWaitMinutes": -1}
        with mock.patch.object(memory, "available_gb", lambda: 60.0), \
                mock.patch.object(memory, "resources", lambda: settings):
            out = self.wait()
        self.assertIn("Starting after 0 min, though memory pressure is 40%, over 5%.", out)
        self.assertTrue((self.state / "c" / str(os.getpid())).exists())


if __name__ == "__main__":
    unittest.main()
