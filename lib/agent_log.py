"""Turns Claude Code's stream-json output into a readable log, one line per message or tool call, each led by its time.

A tool call's line names its tool and what it does: a Bash call its description and its command's first line. A call
that ran LONG_CALL seconds or longer, or failed, logs a second line when its result arrives, with how long it took and,
for a failure, the first line of its error:

    14:02:11 → Bash: Run the GPU tests · cargo test -p gpu --lib
    14:16:40 ← Bash 14 min, failed: error: test failed, to rerun pass `-p gpu --lib`

lib/agent_run.py logs each headless agent's run with it, so the run's journal reads live, and the web page's Agent Log
tab reads a session's transcript through it, merged by time with the session's beats file when it has one:
    python3 agent_log.py [beats file] < transcript.jsonl
The beats file holds the lines agent_run.py adds to the log: a beat, logged while the stream is quiet, a crash, for a
process under the run that dumped core, and a stop, for a command the user stopped. Each beat line starts with BEAT_MARK
ahead of its time, so the web server can tell it from the agent's own lines and mute it; a crash or a stop line goes in
plain.
"""

import json
import re
import sys
from datetime import datetime

# The input field that best names what each tool call does.
SUMMARY_FIELDS = ("command", "file_path", "pattern", "skill", "url", "query", "description", "prompt")
MAX_SUMMARY = 160
# The longest error line a failed call's result line quotes.
MAX_ERROR = 80
# How long a call runs, in seconds, before its result logs a line of its own when it succeeds.
LONG_CALL = 10
# A heredoc's start, `<<WORD`, `<<-'WORD'`, or `<<"WORD"`, which a here-string's `<<<` is not.
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([\w.-]+))")
# What a line after a heredoc may start with before its program: the `)"` that closes a `$(cat <<EOF` and the `&&`,
# `||`, `;`, or `|` that chains the next command.
CLOSER = re.compile(r"^\s*[)}][)}\"'`\s]*(?:&&|\|\||[;|&])?\s*")
# The line a failed Bash call's output starts with, and the first line of its output that reads as an error.
EXIT_LINE = re.compile(r"^Exit code (\d+)$")
ERROR_LINE = re.compile(r"^\s*(?:error|fatal)\b\S*:", re.IGNORECASE)
# The ASCII record separator, which leads each beat line main() prints. str.splitlines() breaks a line at it, so
# a reader splits the output at newlines alone.
BEAT_MARK = "\x1e"


def clip(line, limit=MAX_SUMMARY):
    return line if len(line) <= limit else line[: limit - 1] + "…"


def first_line(text):
    return text.strip().splitlines()[0]


def command_line(command):
    """
    The line of a command that best names what it runs: its first, unless that line opens a heredoc, as
    `python3 - <<'EOF'` does. Then it is the first line after the heredoc's end that runs a program, past a `)"` that
    closes the heredoc's `$(...)` and a `&&` that chains it, or the first line when no such line follows.
    """
    lines = command.strip().splitlines()
    ends = [(dash, next(word for word in words if word)) for dash, *words in HEREDOC.findall(lines[0])]
    if not ends:
        return lines[0]
    for line in lines[1:]:
        if ends:
            dash, word = ends[0]
            if (line.lstrip("\t") if dash else line).strip() == word:
                ends.pop(0)
            continue
        rest = CLOSER.sub("", line, count=1).strip()
        if rest and not rest.startswith("#"):
            return rest
    return lines[0]


def summarize(tool_input):
    """
    What a tool call does, on one line cut to MAX_SUMMARY characters. A command shows its description, when it has
    one, and its first line, or else the line command_line picks. Any other call shows the first line of its most
    telling input field.
    """
    command, description = tool_input.get("command"), tool_input.get("description")
    if isinstance(command, str) and command.strip():
        if isinstance(description, str) and description.strip():
            return clip(f"{first_line(description)} · {first_line(command)}")
        return clip(command_line(command))
    for field in SUMMARY_FIELDS:
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            return clip(first_line(value))
    return ""


def span(seconds):
    """A stretch of time as the log gives it: whole seconds under a minute, whole minutes past one."""
    return f"{int(seconds)} s" if seconds < 60 else f"{int(seconds // 60)} min"


def result_text(block):
    """A tool_result block's text: its content, a string or a list of text parts."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and isinstance(part.get("text"), str))
    return ""


def error_line(text):
    """
    The line of a failed call's result that names its error. A Bash call's output follows an `Exit code <n>` line, and
    its error is the first line that starts `error` or `fatal` and a colon, as cargo's, git's, and rustc's do, or else
    its last line, where a Python traceback names its exception, or `exit code <n>` when it printed nothing. Another
    tool's error is its first line, without the `<tool_use_error>` tag that wraps it.
    """
    text = re.sub(r"</?tool_use_error>", "", text)
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    exited = EXIT_LINE.match(lines[0])
    if not exited:
        return clip(lines[0], MAX_ERROR)
    output = lines[1:]
    line = next((line for line in output if ERROR_LINE.match(line)), output[-1] if output else None)
    return clip(line or f"exit code {exited.group(1)}", MAX_ERROR)


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


class Calls:
    """
    The tool calls a stream started and hasn't seen the results of, by id, each with its tool's name and when it
    started, so each result can log how long its call took. A result arrives as a `user` event whose `tool_result`
    block names its call's id.
    """

    def __init__(self):
        self.started = {}

    def describe(self, event, at):
        """The log lines for one event that came at `at`, a datetime or None when the time is unknown: describe()'s,
        and a line for each result of a call that ran LONG_CALL seconds or longer, or failed."""
        lines = describe(event)
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                self.started[block.get("id")] = (block.get("name") or "a tool", at)
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                call = self.started.pop(block.get("tool_use_id"), None)
                line = call and finished(*call, at, block)
                lines += [line] if line else []
        return lines


def finished(name, began, ended, block):
    """The line a call's result logs: `← <tool> <time>`, with `, failed: <error>` for a failure, or None for a call
    that succeeded in under LONG_CALL seconds or whose time is unknown."""
    seconds = (ended - began).total_seconds() if began and ended else None
    failed = block.get("is_error") is True
    if not failed and (seconds is None or seconds < LONG_CALL):
        return None
    line = f"← {name}" + (f" {span(max(seconds, 0))}" if seconds is not None else "")
    if failed:
        error = error_line(result_text(block))
        line += f", failed: {error}" if error else ", failed"
    return line


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
    no timestamp takes the one before it, so it keeps its place in the transcript. A line from the beats file, a beat
    a crash, or a stop, goes after every event at its time or earlier, and ahead of the first one later than it.
    """
    queued = [beat for beat in map(parse, beats) if beat and beat.get("type") in ("beat", "crash", "stop") and moment(beat)]
    queued.sort(key=moment)
    calls, at = Calls(), None
    for raw in transcript:
        event = parse(raw)
        at = (moment(event) if event else None) or at
        while queued and at is not None and moment(queued[0]) < at:
            beat = queued.pop(0)
            yield f"{stamp(beat)} {beat.get('text', '')}", beat["type"] == "beat"
        if event is None:
            yield f"{stamp({})} {raw.rstrip()}", False
            continue
        for line in calls.describe(event, at):
            yield f"{stamp(event)} {line}", False
    for beat in queued:
        yield f"{stamp(beat)} {beat.get('text', '')}", beat["type"] == "beat"


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
