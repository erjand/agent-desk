"""Turns Claude Code's stream-json output into a readable log, one line per message or tool call, each led by its time.

lib/agent_run.py logs each headless agent's run with it, so the run's journal reads live, and the web page's Agent Log
tab reads a session's transcript through it, merged by time with the session's beats file when it has one:
    python3 agent_log.py [beats file] < transcript.jsonl
Each beat line, which agent_run.py logs while the stream is quiet, starts with BEAT_MARK ahead of its time, so the web
server can tell it from the agent's own lines.
"""

import json
import sys
from datetime import datetime

# The input field that best names what each tool call does.
SUMMARY_FIELDS = ("command", "file_path", "pattern", "skill", "url", "query", "description", "prompt")
MAX_SUMMARY = 160
# The ASCII record separator, which leads each beat line main() prints. str.splitlines() breaks a line at it, so
# a reader splits the output at newlines alone.
BEAT_MARK = "\x1e"


def summarize(tool_input):
    """The first line of the most telling input field, cut to MAX_SUMMARY characters."""
    for field in SUMMARY_FIELDS:
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            line = value.strip().splitlines()[0]
            return line if len(line) <= MAX_SUMMARY else line[: MAX_SUMMARY - 1] + "…"
    return ""


def describe(event):
    """The log lines for one stream-json event, or none for events the log skips."""
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return [f"Session {event.get('session_id')} on {event.get('model')}"]
    if kind == "assistant":
        lines = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                lines.append(block["text"].strip())
            elif block.get("type") == "tool_use":
                summary = summarize(block.get("input", {}))
                lines.append(f"→ {block.get('name')}" + (f": {summary}" if summary else ""))
        return lines
    if kind == "result":
        minutes = event.get("duration_ms", 0) / 60000
        outcome = "failed" if event.get("is_error") else "finished"
        return [f"Agent {outcome} ({event.get('subtype')}) after {event.get('num_turns')} turns, {minutes:.1f} min"]
    return []


def moment(event):
    """An event's timestamp as an aware local datetime, or None when it has none that parses."""
    at = event.get("timestamp")
    try:
        return datetime.fromisoformat(at).astimezone() if isinstance(at, str) else None
    except ValueError:
        return None


def stamp(event, now=None):
    """An event's local time as HH:MM:SS: a transcript's own timestamp, or now for a live stream's, which has none."""
    return (moment(event) or now or datetime.now()).strftime("%H:%M:%S")


def parse(raw):
    """A line's JSON object, an empty dict for other JSON, or None when the line isn't JSON."""
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else {}


def merged(transcript, beats=()):
    """
    The log's lines for a transcript's lines and a beats file's, in time order, each as (line, is_beat). An event with
    no timestamp takes the one before it, so it keeps its place in the transcript. A beat goes after every event at its
    time or earlier, and ahead of the first one later than it.
    """
    queued = [beat for beat in map(parse, beats) if beat and beat.get("type") == "beat" and moment(beat)]
    queued.sort(key=moment)
    at = None
    for raw in transcript:
        event = parse(raw)
        at = (moment(event) if event else None) or at
        while queued and at is not None and moment(queued[0]) < at:
            beat = queued.pop(0)
            yield f"{stamp(beat)} {beat.get('text', '')}", True
        if event is None:
            yield f"{stamp({})} {raw.rstrip()}", False
            continue
        for line in describe(event):
            yield f"{stamp(event)} {line}", False
    for beat in queued:
        yield f"{stamp(beat)} {beat.get('text', '')}", True


def read_beats(path):
    """A beats file's lines, or none when the session has no beats file."""
    try:
        with open(path, encoding="utf-8") as file:
            return file.readlines()
    except OSError:
        return []


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    beats = read_beats(args[0]) if args else []
    for line, is_beat in merged(sys.stdin, beats):
        print(f"{BEAT_MARK if is_beat else ''}{line}", flush=True)


if __name__ == "__main__":
    main()
