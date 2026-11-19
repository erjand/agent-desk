"""Tests for lib/store_gpu.py, the store's records of `agent-desk gpu` calls, and lib/gpu_log.py, which writes them
and prints the waits `agent-desk gpu status` lists."""

import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import gpu_log  # noqa: E402
import store  # noqa: E402


class GpuStoreCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "desk.sqlite"
        self.desk = store.Store(self.path)
        self.addCleanup(self.desk.close)
        self.run_id = self.desk.request_run("org/game", "agent", "12")
        self.desk.run_started(self.run_id, "agent-desk-agent-org-game-12-1")


class TotalsTest(GpuStoreCase):
    def test_a_run_s_totals_add_each_released_call_in_minutes_and_leave_open_ones_out(self):
        shared = self.desk.gpu_asked(self.run_id, "/src/game-issue-12", False, 1000, got=1000)
        waited = self.desk.gpu_asked(self.run_id, "/src/game-issue-12", True, 2000, held_by="game-issue-7")
        open_call = self.desk.gpu_asked(self.run_id, "/src/game-issue-12", False, 3000, got=3000)
        self.assertIsNone(self.desk.run(self.run_id)["gpu_held"])

        self.assertTrue(self.desk.gpu_released(shared, 1120))
        row = self.desk.run(self.run_id)
        self.assertEqual((row["gpu_held"], row["gpu_queued"]), (2.0, 0.0))

        self.desk.gpu_got(waited, 2540)
        self.assertTrue(self.desk.gpu_released(waited, 2600))
        row = self.desk.run(self.run_id)
        self.assertEqual((row["gpu_held"], row["gpu_queued"]), (3.0, 9.0))
        self.assertIsNotNone(open_call)
        self.assertEqual([event["type"] for event in self.desk.events_since()][-2:], ["gpu-used", "gpu-used"])

    def test_a_call_that_stopped_waiting_counts_its_wait_and_no_hold(self):
        call = self.desk.gpu_asked(self.run_id, "/src/game-issue-12", False, 1000, held_by="game-issue-7")
        self.desk.gpu_released(call, 1300)
        row = self.desk.run(self.run_id)
        self.assertEqual((row["gpu_held"], row["gpu_queued"]), (0.0, 5.0))

    def test_a_call_releases_once_and_takes_no_slot_after(self):
        call = self.desk.gpu_asked(self.run_id, "/src/game", False, 1000, got=1000)
        self.assertTrue(self.desk.gpu_released(call, 1060))
        last = self.desk.last_event()
        self.assertFalse(self.desk.gpu_released(call, 2000))
        self.desk.gpu_got(call, 1500)
        self.assertEqual(self.desk.last_event(), last)
        self.assertEqual(self.desk.run(self.run_id)["gpu_held"], 1.0)
        with self.assertRaises(store.StoreError):
            self.desk.gpu_released(call + 1, 2000)

    def test_a_call_outside_a_run_adds_no_totals_and_no_event(self):
        last = self.desk.last_event()
        call = self.desk.gpu_asked(None, "/src/game", False, 1000, got=1000)
        self.assertTrue(self.desk.gpu_released(call, 1060))
        self.assertEqual(self.desk.last_event(), last)
        self.assertIsNone(self.desk.run(self.run_id)["gpu_held"])

    def test_the_board_s_runs_carry_the_totals_once_a_call_ended(self):
        [listed] = self.desk.live_runs("org/game")
        self.assertNotIn("gpu_held", listed)
        call = self.desk.gpu_asked(self.run_id, "/src/game", False, 1000, held_by="game-issue-7")
        self.desk.gpu_got(call, 1030)
        self.desk.gpu_released(call, 1150)
        [listed] = self.desk.live_runs("org/game")
        self.assertEqual((listed["gpu_held"], listed["gpu_queued"]), (2.0, 0.5))


class WaitsTest(GpuStoreCase):
    def test_waits_list_the_released_calls_that_waited_since_a_time(self):
        self.desk.gpu_released(self.desk.gpu_asked(None, "/src/game", False, 500, held_by="old"), 600)
        self.desk.gpu_released(self.desk.gpu_asked(None, "/src/game", False, 1000, got=1000), 1100)
        waited = self.desk.gpu_asked(self.run_id, "/src/game-issue-12", True, 1200, held_by="game-issue-7")
        self.desk.gpu_got(waited, 1290)
        self.desk.gpu_released(waited, 1300)
        self.desk.gpu_asked(None, "/src/game", False, 1250, held_by="game-issue-12")
        self.assertEqual(self.desk.gpu_waits(900), [
            {"run": self.run_id, "checkout": "/src/game-issue-12", "exclusive": True, "held_by": "game-issue-7",
             "asked": 1200.0, "waited": 90.0}])

    def test_a_database_older_than_the_records_reads_as_no_waits(self):
        self.desk.close()
        older = sqlite3.connect(self.path)
        older.execute("DROP TABLE gpu_calls")
        older.execute(f"PRAGMA user_version = {store.GPU_SCHEMA - 1}")
        older.commit()
        older.close()
        self.assertEqual(store.Store(self.path, read_only=True).gpu_waits(0), [])


class GpuLogTest(GpuStoreCase):
    def test_asked_got_and_released_record_a_call_in_the_run_agent_desk_run_names(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(gpu_log.main(["gpu_log.py", "asked", "/src/game", "true", "1000,25", "--held-by",
                                           "game-issue-7"], {"AGENT_DESK_RUN": str(self.run_id)}, self.desk), 0)
        call = int(out.getvalue())
        self.assertEqual(gpu_log.main(["gpu_log.py", "got", str(call), "1060.25"], {}, self.desk), 0)
        self.assertEqual(gpu_log.main(["gpu_log.py", "released", str(call), "1120.25"], {}, self.desk), 0)
        row = self.desk.run(self.run_id)
        self.assertEqual((row["gpu_queued"], row["gpu_held"]), (1.0, 1.0))
        record = self.desk.db.execute("SELECT * FROM gpu_calls WHERE id = ?", (call,)).fetchone()
        self.assertEqual((record["run"], record["exclusive"], record["held_by"], record["asked"]),
                         (self.run_id, 1, "game-issue-7", 1000.25))

    def test_a_call_outside_a_run_records_no_run(self):
        with redirect_stdout(io.StringIO()):
            gpu_log.main(["gpu_log.py", "asked", "/src/game", "false", "1000", "--got", "1000"], {}, self.desk)
        self.assertEqual(self.desk.db.execute("SELECT run, got FROM gpu_calls").fetchone()[:], (None, 1000.0))

    def test_wrong_arguments_print_the_usage(self):
        for args in ([], ["asked", "/src/game"], ["got", "one", "1000"], ["status", "--hours", "0"], ["status", "x"]):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(gpu_log.main(["gpu_log.py", *args], {}, self.desk), 2, args)

    def test_status_lists_each_checkout_s_waits_most_queued_first(self):
        waits = [{"checkout": "/src/game-issue-12", "held_by": "game-issue-7", "waited": 40},
                 {"checkout": "/src/game-issue-9", "held_by": "game-issue-12; next in line: game-issue-4",
                  "waited": 900},
                 {"checkout": "/src/game-issue-12", "held_by": "game-issue-9", "waited": 150}]
        self.assertEqual(gpu_log.status_lines(waits, 24), [
            "GPU waits in the last day, by checkout:",
            "  game-issue-9: 1 wait, 15 min queued, longest 15 min behind game-issue-12; next in line: game-issue-4",
            "  game-issue-12: 2 waits, 3 min queued, longest 2 min behind game-issue-9"])
        self.assertEqual(gpu_log.status_lines([], 6), ["No GPU call waited in the last 6 hours."])


if __name__ == "__main__":
    unittest.main()
