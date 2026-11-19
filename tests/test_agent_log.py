"""Tests for the pure functions in agent-desk/lib/agent_log.py."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import agent_log  # noqa: E402

# Events as Claude Code 2.1.309 printed them for `claude -p --output-format stream-json --verbose`, trimmed to the
# fields the log reads.
INIT = {"type": "system", "subtype": "init", "session_id": "0c95f28e-639a-4179-a7be-f041e4927c6e",
        "model": "claude-opus-5-5"}
TOOL_CALL = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "toolu_01ScscH1V8HCZRiDDNqHJGWS", "name": "Bash",
     "input": {"command": "echo hello", "description": "Print hello"}}]}}
TOOL_RESULT = {"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "toolu_01ScscH1V8HCZRiDDNqHJGWS", "content": "hello", "is_error": False}]}}
TEXT = {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "duration_ms": 3663,
          "result": "done"}


class DescribeTests(unittest.TestCase):
    def test_the_session_start_names_the_session_and_model(self):
        self.assertEqual(agent_log.describe(INIT),
                         ["Session 0c95f28e-639a-4179-a7be-f041e4927c6e on claude-opus-5-5"])

    def test_a_tool_call_shows_the_tool_and_its_command(self):
        self.assertEqual(agent_log.describe(TOOL_CALL), ["→ Bash: echo hello"])

    def test_tool_results_are_left_out(self):
        self.assertEqual(agent_log.describe(TOOL_RESULT), [])

    def test_assistant_text_is_shown(self):
        self.assertEqual(agent_log.describe(TEXT), ["done"])

    def test_the_result_reports_the_outcome_turns_and_minutes(self):
        self.assertEqual(agent_log.describe(RESULT), ["Agent finished (success) after 2 turns, 0.1 min"])

    def test_unknown_events_are_left_out(self):
        self.assertEqual(agent_log.describe({"type": "rate_limit_event"}), [])


class StampTests(unittest.TestCase):
    def test_a_transcript_event_shows_its_own_time_in_local_time(self):
        at = datetime(2026, 11, 1, 14, 0, 2, 768000, tzinfo=timezone.utc)
        self.assertEqual(agent_log.stamp({"timestamp": "2026-11-01T14:00:02.768Z"}),
                         at.astimezone().strftime("%H:%M:%S"))

    def test_a_live_event_without_a_timestamp_shows_now(self):
        self.assertEqual(agent_log.stamp(TEXT, now=datetime(2026, 11, 1, 8, 5, 9)), "08:05:09")

    def test_a_malformed_timestamp_shows_now(self):
        self.assertEqual(agent_log.stamp({"timestamp": "soon"}, now=datetime(2026, 11, 1, 8, 5, 9)), "08:05:09")


class SummarizeTests(unittest.TestCase):
    def test_only_the_first_line_of_a_multiline_command_shows(self):
        self.assertEqual(agent_log.summarize({"command": "git add -A\ngit commit"}), "git add -A")

    def test_a_long_summary_is_cut_with_an_ellipsis(self):
        summary = agent_log.summarize({"command": "x" * 500})
        self.assertEqual(len(summary), agent_log.MAX_SUMMARY)
        self.assertTrue(summary.endswith("…"))

    def test_a_tool_without_a_known_field_has_no_summary(self):
        self.assertEqual(agent_log.summarize({"todos": []}), "")


if __name__ == "__main__":
    unittest.main()
