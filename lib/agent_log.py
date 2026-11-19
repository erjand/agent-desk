"""Turns Claude Code's stream-json output into a readable log, one line per message or tool call, each led by its time.

lib/agent_run.py logs each headless agent's run with it, so the run's journal reads live, and the web page's Agent Log
tab reads a session's transcript through it:
    python3 agent_log.py < transcript.jsonl
"""

import json
import sys
from datetime import datetime

# The input field that best names what each tool call does.
SUMMARY_FIELDS = ("command", "file_path", "pattern", "skill", "url", "query", "description", "prompt")
MAX_SUMMARY = 160


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


def stamp(event, now=None):
    """An event's local time as HH:MM:SS: a transcript's own timestamp, or now for a live stream's, which has none."""
    at = event.get("timestamp")
    try:
        moment = datetime.fromisoformat(at).astimezone() if isinstance(at, str) else None
    except ValueError:
        moment = None
    return (moment or now or datetime.now()).strftime("%H:%M:%S")


def main():
    for raw in sys.stdin:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            print(f"{stamp({})} {raw.rstrip()}", flush=True)
            continue
        if not isinstance(event, dict):
            continue
        for line in describe(event):
            print(f"{stamp(event)} {line}", flush=True)


if __name__ == "__main__":
    main()
