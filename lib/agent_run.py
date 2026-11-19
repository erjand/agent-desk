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


def log(line):
    print(f"{stamp({})} {line}", flush=True)


class Tasks:
    """
    The background tasks claude -p's stream says still run, in the order they started. Claude Code reports each one
    as a system event: `task_started` when it starts, with the tool call that started it, `task_notification` with its
    status when it ends, and `background_tasks_changed` with every task running then.
    """

    def __init__(self):
        self.running = {}
        self.calls = {}

    def see(self, event):
        kind, subtype = event.get("type"), event.get("subtype")
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self.calls[block.get("id")] = block
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
        if not self.running:
            return ""
        task = list(self.running.values())[-1]
        call = self.calls.get(task.get("tool_use_id")) or {}
        command = (call.get("input") or {}).get("command") if call.get("name") == "Bash" else None
        if isinstance(command, str) and command.strip():
            return code(summarize({"command": command}))
        description = task.get("description")
        return f"the task “{description}”" if isinstance(description, str) and description.strip() else "a task"


def code(text):
    """text as a Markdown code span, fenced with one backtick more than its longest run of them."""
    fence = "`" * (max((len(run) for run in re.findall("`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def copy(source, stream, results, tasks):
    """Copies each line of claude -p's output to the stream file, prints its log lines, keeps each result event, and
    follows the background tasks."""
    for raw in source:
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


def run(command, stream_path, result_grace=RESULT_GRACE, kill_grace=KILL_GRACE):
    """Runs command, logging its stream-json, and returns its status, ending it result_grace seconds after its result."""
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
    if ended:
        return 1 if results[-1].get("is_error") else STOPPED
    return status_of(proc.returncode)


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: agent_run.py <stream file> <command>...")
    sys.exit(run(sys.argv[2:], sys.argv[1]))


if __name__ == "__main__":
    main()
