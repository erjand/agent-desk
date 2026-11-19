"""Tests for agent-desk/lib/chore_repeat.py, which keeps how many more times each chore runs."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import chore_repeat  # noqa: E402

REPO = "org/game"


class RepeatTest(unittest.TestCase):
    def test_a_chore_queued_three_times_runs_twice_more_after_the_first(self):
        data = {}
        chore_repeat.set_times(data, REPO, "audit", "3")
        self.assertEqual(data, {REPO: {"audit": 2}})
        self.assertTrue(chore_repeat.take_next(data, REPO, "audit", 0))
        self.assertTrue(chore_repeat.take_next(data, REPO, "audit", 0))
        self.assertFalse(chore_repeat.take_next(data, REPO, "audit", 0))
        self.assertEqual(data, {REPO: {}})

    def test_a_chore_queued_once_queues_nothing_more(self):
        data = {REPO: {"audit": 4}}
        chore_repeat.set_times(data, REPO, "audit", "1")
        self.assertEqual(data, {REPO: {}})

    def test_a_looping_chore_runs_again_until_stopped_or_a_run_fails(self):
        data = {}
        chore_repeat.set_times(data, REPO, "singleshot-comment-one", "forever")
        self.assertTrue(chore_repeat.take_next(data, REPO, "singleshot-comment-one", 0))
        self.assertTrue(chore_repeat.take_next(data, REPO, "singleshot-comment-one", 0))
        self.assertFalse(chore_repeat.take_next(data, REPO, "singleshot-comment-one", 1))
        self.assertEqual(data, {REPO: {}})

    def test_the_queue_keeps_each_project_apart_and_drops_empty_ones_when_written(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chore-repeat.json"
            with chore_repeat.queues(path) as data:
                chore_repeat.set_times(data, REPO, "audit", "forever")
                chore_repeat.set_times(data, "org/other", "audit", "1")
            with chore_repeat.queues(path) as data:
                self.assertEqual(data, {REPO: {"audit": "forever"}})


if __name__ == "__main__":
    unittest.main()
