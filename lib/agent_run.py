"""Runs a headless agent's claude -p, logs it as agent_log.py does, and ends the run at the agent's result.

claude -p stays alive after the agent's final message while a task the agent started in the background, such as a
Bash command, still runs, and the run would hold its runner and its memory claim until the task ends. This script
copies claude -p's stream-json to a file for the run log and prints the readable log agent_log.py makes of it. Once
the stream's result event arrives, it waits RESULT_GRACE seconds for claude -p to exit, then ends claude -p and every
process it started: TERM, then KILL after KILL_GRACE seconds more. Claude Code starts each Bash command in a session of
its own, out of reach of a signal to claude -p's process group, so the script finds the processes by walking its own
descendants, and marks itself their subreaper so that one whose parent exited stays among them. It reaps each of
those orphans as it exits, so the run leaves no zombie behind for a process that checks whether its child is gone.

    python3 agent_run.py <stream file> claude -p ... --output-format stream-json --verbose

Exits with claude -p's status when it exits by itself. When the script ended it, the run stopped early: it exits with
1 if the result says the agent failed (is_error), and with STOPPED if not, so the caller can tell the agent's
unfinished task from a clean finish. The stream names the background tasks claude -p started and ended, so the script
logs the newest one still running when it ends the run, and writes it, as Markdown for a note, to the stream file's
path with `.left` added: a Bash command in a code span, another task by its description, or nothing when the stream
names none. A run that didn't stop early writes no such file. AGENT_DESK_RESULT_GRACE and AGENT_DESK_KILL_GRACE set
the two waits in seconds.

While claude -p writes no stream event for BEAT seconds, as when the agent waits on a long foreground command, the
script logs a beat line every BEAT seconds the silence lasts, so the journal tells a working run from a stuck one:

    Still in Bash for 12 min: timeout 7000 bash -c 'until … · background: just bank-fill … · busy: claude -p ×3 (85% CPU)

It names the newest tool call still waiting on its result and how long it has run, the background tasks still running,
and the three commands under the run that used the most CPU since the last line or the last event. The script also
appends each beat, as a JSON line of its type, UTC timestamp, and text, to a file named by the session id under
agent-desk's state folder, `beats/<session id>`, for the web page's Agent Log tab. AGENT_DESK_BEAT sets the interval in
seconds.
"""

import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_log import describe, stamp, summarize

RESULT_GRACE = float(os.environ.get("AGENT_DESK_RESULT_GRACE", "30"))
KILL_GRACE = float(os.environ.get("AGENT_DESK_KILL_GRACE", "10"))
PR_SET_CHILD_SUBREAPER = 36
# How long the log waits for the rest of the stream once the processes are gone, in case one outside the tree still
# holds claude -p's output open.
DRAIN_WAIT = 5
# How often the script checks on claude -p and reaps the orphans it adopted, in seconds.
POLL = 0.1
# The status of a run the script ended while a task the agent left still ran, after a result that says the agent
# succeeded: EX_TEMPFAIL, since the run can go on from where it stopped. lib/common.sh's desk_stopped and
# lib/run_end.py read it.
STOPPED = 75
# How long claude -p may write no stream event before the script logs what the run waits on, and how often it logs
# that again while the silence lasts, in seconds.
BEAT = float(os.environ.get("AGENT_DESK_BEAT", "300"))
# The longest a command or task description runs in a beat line, so the line stays one line in the journal.
BEAT_SUMMARY = 80
# How many commands a beat line's busy list names.
BUSY_TOP = 3
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
PYTHON = re.compile(r"python[\d.]*")
# The options of `uv run` and of python that take the next argument as their value.
UV_VALUE_OPTIONS = {"--with", "--with-editable", "--with-requirements", "--python", "-p", "--project", "--directory",
                    "--package", "--group", "--only-group", "--no-group", "--extra", "--env-file", "--index",
                    "--default-index", "--index-url", "--extra-index-url"}
PYTHON_VALUE_OPTIONS = {"-W", "-X", "--check-hash-based-pycs"}


def log(line):
    print(f"{stamp({})} {line}", flush=True)


class Tasks:
    """
    What claude -p's stream says still runs: the background tasks, in the order they started, and the tool calls still
    waiting on their results. Claude Code reports each task as a system event: `task_started` when it starts, with the
    tool call that started it, `task_notification` with its status when it ends, and `background_tasks_changed` with
    every task running then. A tool call waits from its `tool_use` block until a `tool_result` block names it. The
    reader thread feeds the events in and the beat thread reads them, so each method holds the lock.
    """

    def __init__(self):
        self.running = {}
        self.calls = {}
        # Each tool call still waiting on its result, by id, with the time the stream named it.
        self.pending = {}
        self.session = None
        # When the stream last wrote a line, or when the run started.
        self.heard = time.monotonic()
        self.lock = threading.Lock()

    def hear(self):
        with self.lock:
            self.heard = time.monotonic()

    def see(self, event):
        with self.lock:
            self._see(event)

    def _see(self, event):
        kind, subtype = event.get("type"), event.get("subtype")
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self.calls[block.get("id")] = block
                    self.pending[block.get("id")] = (block, time.monotonic())
        elif kind == "user":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self.pending.pop(block.get("tool_use_id"), None)
        elif kind == "system" and subtype == "init" and isinstance(event.get("session_id"), str):
            self.session = event["session_id"]
        elif kind == "system" and subtype == "task_started" and event.get("task_id"):
            self.running[event["task_id"]] = event
        elif kind == "system" and subtype == "task_notification":
            self.running.pop(event.get("task_id"), None)
        elif kind == "system" and subtype == "background_tasks_changed" and isinstance(event.get("tasks"), list):
            listed = {task.get("task_id"): task for task in event["tasks"] if isinstance(task, dict)}
            self.running = {task: self.running.get(task, one) for task, one in listed.items()}

    def left(self):
        """The newest task still running, as Markdown: a Bash command's first line in a code span, another task by its
        description, or "" when the stream named none."""
        with self.lock:
            if not self.running:
                return ""
            command, description = self._name(list(self.running.values())[-1])
        if command:
            return code(command)
        return f"the task “{description}”" if description else "a task"

    def _name(self, task):
        """A task's Bash command, cut to one line, and its description, each "" when the stream gave none."""
        call = self.calls.get(task.get("tool_use_id")) or {}
        command = (call.get("input") or {}).get("command") if call.get("name") == "Bash" else None
        command = summarize({"command": command}) if isinstance(command, str) else ""
        description = task.get("description")
        return command, description.strip() if isinstance(description, str) else ""

    def waiting(self):
        """What a beat line says the run waits on: the newest tool call still waiting and how long it has, then each
        background task still running, with commands and descriptions cut to BEAT_SUMMARY characters."""
        with self.lock:
            now = time.monotonic()
            if self.pending:
                block, since = list(self.pending.values())[-1]
                summary = summarize(block.get("input") if isinstance(block.get("input"), dict) else {})
                head = f"Still in {block.get('name') or 'a tool'} for {span(now - since)}"
                head += f": {cut(summary)}" if summary else ""
            else:
                head = f"Quiet for {span(now - self.heard)} with no tool call waiting"
            tasks = [self._name(task) for task in self.running.values()]
        background = [cut(command or description or "a task") for command, description in tasks]
        return head + (f" · background: {', '.join(background)}" if background else "")


def span(seconds):
    """A stretch of time as a beat line gives it: whole seconds under a minute, whole minutes past one."""
    return f"{int(seconds)} s" if seconds < 60 else f"{int(seconds // 60)} min"


def cut(text, limit=BEAT_SUMMARY):
    return text if len(text) <= limit else text[: limit - 1].rstrip() + " …"


def code(text):
    """text as a Markdown code span, fenced with one backtick more than its longest run of them."""
    fence = "`" * (max((len(run) for run in re.findall("`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def copy(source, stream, results, tasks):
    """Copies each line of claude -p's output to the stream file, prints its log lines, keeps each result event, and
    follows the background tasks."""
    for raw in source:
        tasks.hear()
        stream.write(raw)
        stream.flush()
        text = raw.decode("utf-8", "replace")
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            log(text.rstrip())
            continue
        if not isinstance(event, dict):
            continue
        for line in describe(event):
            print(f"{stamp(event)} {line}", flush=True)
        tasks.see(event)
        if event.get("type") == "result":
            results.append(event)


def descendants(root):
    """The live processes below root, found through each process's parent in /proc, skipping zombies."""
    children = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            fields = Path(f"/proc/{entry}/stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields[0] != "Z":
            children.setdefault(int(fields[1]), []).append(int(entry))
    found, todo = [], [root]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


def command_name(argv):
    """The name a beat line's busy list gives a process from its arguments: `python -m <module>` or `python <script>`
    for python, what `uv run` runs for uv, `claude -p` for a headless claude, and the program's name for the rest."""
    if not argv:
        return ""
    base, rest = os.path.basename(argv[0]), argv[1:]
    if base == "uv" and rest[:1] == ["run"]:
        args, at = rest[1:], 0
        while at < len(args) and args[at].startswith("-"):
            if args[at] == "--":
                at += 1
                break
            at += 2 if args[at] in UV_VALUE_OPTIONS else 1
        return command_name(args[at:]) or "uv run"
    if PYTHON.fullmatch(base):
        at = 0
        while at < len(rest):
            arg = rest[at]
            if arg == "-m" and at + 1 < len(rest):
                return f"python -m {rest[at + 1]}"
            if arg.startswith("-m") and len(arg) > 2:
                return f"python -m {arg[2:]}"
            if arg == "-c":
                return "python -c"
            if arg == "-" or not arg.startswith("-"):
                return f"python {os.path.basename(arg)}"
            at += 2 if arg in PYTHON_VALUE_OPTIONS else 1
        return "python"
    if base == "claude" and ("-p" in rest or "--print" in rest):
        return "claude -p"
    return base


def cpu_sample(root):
    """Each live process below root, keyed by its pid and start time so a reused pid counts as a new process, with
    its command's name and the CPU time it has used, in clock ticks."""
    sample = {}
    for pid in descendants(root):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[1].split()
        name = command_name([arg.decode("utf-8", "replace") for arg in argv if arg])
        name = name or stat[stat.find("(") + 1: stat.rfind(")")]
        try:
            sample[(pid, fields[19])] = (name, int(fields[11]) + int(fields[12]))
        except (IndexError, ValueError):
            continue
    return sample


def busy(before, after, seconds):
    """The busy list of a beat line: the BUSY_TOP commands whose processes used the most CPU between two samples taken
    seconds apart, each with how many of its processes did and their share of one CPU, or `busy: nothing`. A process
    the first sample lacks started since, so all its CPU time counts."""
    used, count = {}, {}
    for key, (name, ticks) in after.items():
        spent = ticks - before.get(key, (name, 0))[1]
        if spent > 0:
            used[name] = used.get(name, 0) + spent
            count[name] = count.get(name, 0) + 1
    if not used:
        return "busy: nothing"
    parts = []
    for rank, name in enumerate(sorted(used, key=lambda one: (-used[one], one))[:BUSY_TOP]):
        share = used[name] / CLOCK_TICKS / max(seconds, 0.001) * 100
        share = f"{share:.0f}%" if share >= 0.5 else "<1%"
        times = f" ×{count[name]}" if count[name] > 1 else ""
        parts.append(f"{name}{times} ({share}{' CPU' if rank == 0 else ''})")
    return "busy: " + ", ".join(parts)


def beats_folder():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk" / "beats"


def record_beat(session, text):
    """Appends a beat to its session's beats file, skipping a session id that can't name a file."""
    if not session or not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        return
    moment = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    folder = beats_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / session, "a", encoding="utf-8") as beats:
            beats.write(json.dumps({"type": "beat", "timestamp": moment, "text": text}) + "\n")
    except OSError as error:
        log(f"Could not write the beat to {folder / session}: {error}")


def beat(tasks, interval, stop):
    """Logs a beat line once the stream has been quiet for interval seconds, and another every interval seconds the
    silence lasts, until stop is set. The CPU of the first line counts from the last stream line, as of the next
    check after it, and of each later line from the line before."""
    tick = min(5, interval / 5)
    root = os.getpid()
    heard, sample, sampled, beaten = None, cpu_sample(root), time.monotonic(), 0.0
    while not stop.wait(tick):
        with tasks.lock:
            latest = tasks.heard
        if latest != heard:
            heard, sample, sampled = latest, cpu_sample(root), time.monotonic()
            continue
        now = time.monotonic()
        if now - max(heard, beaten) < interval:
            continue
        current = cpu_sample(root)
        text = f"{tasks.waiting()} · {busy(sample, current, now - sampled)}"
        if stop.is_set():
            return
        log(text)
        record_beat(tasks.session, text)
        sample, sampled, beaten = current, now, now


def reap(proc):
    """Reaps each exited child but claude -p, whose status Popen collects, so an orphan the script adopted as subreaper
    leaves no zombie. waitid with WNOWAIT names the next exited child without collecting it, so the script collects
    only that child and lets Popen collect claude -p."""
    while True:
        try:
            info = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return
        if info is None:
            return
        if info.si_pid == proc.pid:
            if proc.poll() is None:
                return
        else:
            try:
                os.waitpid(info.si_pid, os.WNOHANG)
            except ChildProcessError:
                pass


def wait_until(proc, done, timeout=None):
    """Waits until done() holds or timeout seconds pass, reaping orphans meanwhile, and returns whether done() held."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        reap(proc)
        if done():
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(POLL)


def exited(proc):
    return proc.poll() is not None


def signal_all(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def end_tree(proc, kill_grace):
    """Ends claude -p and every process below this script: TERM, then KILL for any left after kill_grace seconds."""
    signal_all(descendants(os.getpid()), signal.SIGTERM)
    wait_until(proc, lambda: not descendants(os.getpid()), kill_grace)
    signal_all(descendants(os.getpid()), signal.SIGKILL)
    proc.wait()
    reap(proc)


def status_of(code):
    """A process's exit code as a shell reports it, 128 plus the signal's number for one a signal ended."""
    return 128 - code if code < 0 else code


def run(command, stream_path, result_grace=RESULT_GRACE, kill_grace=KILL_GRACE, interval=BEAT):
    """Runs command, logging its stream-json and a beat line for each interval seconds of silence, and returns its
    status, ending it result_grace seconds after its result."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    with open(stream_path, "wb") as stream:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE)
        # A signal meant for the run, such as the unit's stop, reaches claude -p too, which exits and ends the run.
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda number, _frame: proc.send_signal(number))
        results, tasks = [], Tasks()
        reader = threading.Thread(target=copy, args=(proc.stdout, stream, results, tasks), daemon=True)
        reader.start()
        stop = threading.Event()
        threading.Thread(target=beat, args=(tasks, interval, stop), daemon=True).start()
        wait_until(proc, lambda: exited(proc) or results)
        ended = bool(results) and not wait_until(proc, lambda: exited(proc), result_grace)
        if ended:
            left = tasks.left()
            Path(f"{stream_path}.left").write_text(left, encoding="utf-8")
            held = left or "a task the agent left running"
            log(f"claude -p still ran {result_grace:g} s after the agent's result, held by {held}, so the run ends "
                "it and stops early.")
            end_tree(proc, kill_grace)
        wait_until(proc, lambda: not reader.is_alive(), DRAIN_WAIT if ended else None)
        stop.set()
    if ended:
        return 1 if results[-1].get("is_error") else STOPPED
    return status_of(proc.returncode)


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: agent_run.py <stream file> <command>...")
    sys.exit(run(sys.argv[2:], sys.argv[1]))


if __name__ == "__main__":
    main()
