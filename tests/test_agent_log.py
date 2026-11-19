"""Tests for the pure functions in agent-desk/lib/agent_log.py."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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



def at(event, timestamp):
    return json.dumps({**event, "timestamp": timestamp}) + "\n"


def beat(timestamp, text):
    return json.dumps({"type": "beat", "timestamp": timestamp, "text": text}) + "\n"


def local(timestamp):
    return agent_log.stamp({"timestamp": timestamp})


class MergeTests(unittest.TestCase):
    TRANSCRIPT = [at(TOOL_CALL, "2026-11-19T15:00:00.000Z"), at(TOOL_RESULT, "2026-11-19T15:25:00.000Z"),
                  at(TEXT, "2026-11-19T15:25:01.000Z")]

    def test_beats_go_between_the_transcript_lines_by_time(self):
        beats = [beat("2026-11-19T15:05:00.000Z", "Still in Bash for 5 min: sleep 1500 · busy: nothing"),
                 beat("2026-11-19T15:10:00.000Z", "Still in Bash for 10 min: sleep 1500 · busy: nothing")]
        self.assertEqual(list(agent_log.merged(self.TRANSCRIPT, beats)), [
            (f"{local('2026-11-19T15:00:00Z')} → Bash: echo hello", False),
            (f"{local('2026-11-19T15:05:00Z')} Still in Bash for 5 min: sleep 1500 · busy: nothing", True),
            (f"{local('2026-11-19T15:10:00Z')} Still in Bash for 10 min: sleep 1500 · busy: nothing", True),
            (f"{local('2026-11-19T15:25:01Z')} done", False),
        ])

    def test_a_beat_after_the_last_event_ends_the_log(self):
        lines = list(agent_log.merged(self.TRANSCRIPT[:1], [beat("2026-11-19T15:05:00.000Z", "Quiet for 5 min")]))
        self.assertEqual([is_beat for _, is_beat in lines], [False, True])

    def test_an_event_without_a_timestamp_keeps_its_place_after_the_one_before(self):
        transcript = [self.TRANSCRIPT[0], json.dumps(TEXT) + "\n", self.TRANSCRIPT[2]]
        lines = list(agent_log.merged(transcript, [beat("2026-11-19T15:05:00.000Z", "Quiet for 5 min")]))
        self.assertEqual([is_beat for _, is_beat in lines], [False, False, True, False])

    def test_a_line_that_is_not_a_beat_is_left_out_of_the_beats(self):
        beats = ["not json\n", json.dumps({"type": "other", "timestamp": "2026-11-19T15:05:00Z"}) + "\n",
                 json.dumps({"type": "beat", "text": "no time"}) + "\n"]
        self.assertEqual(list(agent_log.merged(self.TRANSCRIPT, beats)), list(agent_log.merged(self.TRANSCRIPT)))


class MainTests(unittest.TestCase):
    def run_main(self, transcript, args):
        out = io.StringIO()
        stdin, sys.stdin = sys.stdin, io.StringIO("".join(transcript))
        try:
            with redirect_stdout(out):
                agent_log.main(args)
        finally:
            sys.stdin = stdin
        return out.getvalue().split("\n")[:-1]

    def test_a_beat_line_starts_with_the_mark_ahead_of_its_time(self):
        with tempfile.NamedTemporaryFile("w", suffix=".beats") as beats:
            beats.write(beat("2026-11-19T15:05:00.000Z", "Quiet for 5 min with no tool call waiting · busy: nothing"))
            beats.flush()
            lines = self.run_main(MergeTests.TRANSCRIPT, [beats.name])
        self.assertEqual(lines[1], f"{agent_log.BEAT_MARK}{local('2026-11-19T15:05:00Z')} Quiet for 5 min with no tool "
                                   "call waiting · busy: nothing")
        self.assertFalse(any(line.startswith(agent_log.BEAT_MARK) for line in lines[:1] + lines[2:]))

    def test_a_session_with_no_beats_file_reads_as_its_transcript_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = self.run_main(MergeTests.TRANSCRIPT, [str(Path(folder) / "none")])
        self.assertEqual(missing, self.run_main(MergeTests.TRANSCRIPT, []))
        self.assertEqual(len(missing), 2)


if __name__ == "__main__":
    unittest.main()
