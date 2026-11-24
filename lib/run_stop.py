"""
Stops the command an agent run waits on and leaves the run going, as `agent-desk stop-command <n>` asks.

The command finds the agent run working issue n and asks it, through a file under agent-desk's state folder,
`stops/<run id>-<token>.ask`. lib/agent_run.py checks for an ask every ASK_POLL seconds while it runs, and answers in
`stops/<run id>-<token>.json` with what it ended, which the command prints. An ask older than ASK_WAIT seconds goes
unanswered, so a command that gave up never stops a call that waits later.

The run ends the processes of the newest tool call still waiting on its result. Claude Code starts each Bash command in
a session of its own, under claude -p, with the command quoted in its shell's arguments, so the call's processes are
the session whose leader runs that command, the newest such leader when two do, and every process below them. A
process in the session whose parent exited stays in it under agent_run.py, the run's subreaper. When no leader's
arguments hold the command, the call's session is the newest one that started since the call did, give or take
SESSION_SLACK seconds. A background task started by an earlier call runs in an older session, and claude -p's own
process sits in agent_run.py's session, so neither is ever among them.

For Codex, the run requires a process under the CLI whose arguments match the waiting command and whose start time
fits the call. It stops that process and its descendants even if they share the CLI's session.

The run writes a line to the end of the command's output file first, so the agent reads that the user stopped it, then
sends TERM to every process, and KILL to any left after TERM_GRACE seconds. claude -p hands the agent the command's
output with its failed status, and the agent goes on. The run's log gets a line saying the user stopped the command.
"""

import json
import os
import re
import secrets
import shlex
import signal
import stat
import time
from datetime import datetime
from pathlib import Path

# How often a run checks for an ask, and how long the command waits for the answer, in seconds.
ASK_POLL = 0.5
ASK_WAIT = 20.0
TERM_GRACE = float(os.environ.get("AGENT_DESK_TERM_GRACE", "5"))
# How long before the call's start its command's session may have started, since claude -p may start the command a
# moment before the stream names the call.
SESSION_SLACK = 2.0
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
SHELLS = ("bash", "dash", "sh", "zsh", "fish", "ksh")


class StopError(Exception):
    pass


def folder():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk" / "stops"


# The command's side.

def ask(run, wait=ASK_WAIT):
    """
    Asks run `run` to stop the command it waits on and returns its answer, a dict with `ended`, the names of the
    processes it ended, and `text`, what it did. Raises StopError when the run doesn't answer within `wait` seconds.
    """
    token = secrets.token_hex(8)
    base = folder() / f"{run}-{token}"
    folder().mkdir(parents=True, exist_ok=True)
    base.with_suffix(".tmp").write_text("{}", encoding="utf-8")
    base.with_suffix(".tmp").replace(base.with_suffix(".ask"))
    answer = base.with_suffix(".json")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            said = json.loads(answer.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(ASK_POLL / 5)
            continue
        answer.unlink(missing_ok=True)
        return said
    base.with_suffix(".ask").unlink(missing_ok=True)
    raise StopError(f"Run {run} didn't answer within {wait:g} s. A run that started before agent-desk could stop a "
                    "command never answers; `agent-desk pause` stops the whole run.")


def stop_command(desk, project, number, wait=ASK_WAIT):
    """
    Stops the command the agent run working issue `number` in `project` waits on. Returns what it ended, and raises
    StopError when no agent run works the issue, the run waits on no command, or it doesn't answer.
    """
    working = [row for row in desk.runs(("running",))
               if row["project"] == project and row["kind"] == "agent" and row["target"] == str(number)]
    if not working:
        raise StopError(f"No agent run works #{number} in {project}, so there is no command to stop.")
    said = ask(working[0]["id"], wait)
    if not said.get("ended"):
        raise StopError(str(said.get("text") or "The run stopped nothing."))
    return str(said["text"])


# The run's side.

def proc(pid):
    """A process's parent, session, start time in seconds since boot, short name, and arguments, or None once gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    fields = raw.rsplit(")", 1)[1].split()
    if fields[0] == "Z":
        return None
    argv = [arg.decode("utf-8", "replace") for arg in cmdline.removesuffix(b"\0").split(b"\0")] if cmdline else []
    return {"pid": pid, "ppid": int(fields[1]), "sid": int(fields[3]), "start": int(fields[19]) / CLOCK_TICKS,
            "name": raw[raw.find("(") + 1: raw.rfind(")")], "argv": argv}


def processes():
    """Every live process, by pid."""
    found = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit() and (one := proc(int(entry))):
            found[one["pid"]] = one
    return found


def below(table, root):
    """The pids of the processes below root in table."""
    children = {}
    for one in table.values():
        children.setdefault(one["ppid"], []).append(one["pid"])
    found, todo = [], [root]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


def uptime():
    return float(Path("/proc/uptime").read_text().split()[0])


def runs_command(one, command):
    """Whether a process's arguments hold a Bash call's command, as given or quoted for a shell as Claude Code does."""
    quoted = "'" + command.replace("'", "'\"'\"'") + "'"
    return any(command in arg or quoted in arg or shlex.quote(command) in arg for arg in one["argv"][1:])


def call_session(table, root, skip, command, waited):
    """
    The session leader of the waiting call's command among the processes below root but those in skip: the newest that
    runs `command`, or with none, the newest that started since the call, which has waited `waited` seconds. None when
    neither is there.
    """
    leaders = [table[pid] for pid in below(table, root)
               if pid not in skip and table[pid]["sid"] == pid]
    if command:
        named = [one for one in leaders if runs_command(one, command)]
        if named:
            return max(named, key=lambda one: one["start"])
    began = uptime() - waited - SESSION_SLACK
    recent = [one for one in leaders if one["start"] >= began]
    return max(recent, key=lambda one: one["start"]) if recent else None


def codex_shell(table, cli, skip, command, waited):
    """Finds one recent shell or the simple command that replaced it."""
    if not command:
        return None
    try:
        display = shlex.split(command)
    except ValueError:
        display = []
    shells, script = SHELLS, command
    if len(display) == 3 and os.path.basename(display[0]) in SHELLS and display[1] in ("-lc", "-c"):
        shells, script = (os.path.basename(display[0]),), display[2]
    # A shell can exec a simple command in place. Match its full argv only when the script needs no expansion.
    try:
        simple = shlex.split(script) if not re.search(r"[|&;<>()\r\n$`*?\[\]{}~#]", script) else []
    except ValueError:
        simple = []
    began = uptime() - waited - SESSION_SLACK
    matches, execs = [], []
    for pid in below(table, cli):
        if pid in skip or table[pid]["start"] < began:
            continue
        argv = table[pid]["argv"]
        # Codex may source a shell snapshot and exec the same script with -c instead of -lc.
        if (len(argv) == 3 and os.path.basename(argv[0]) in shells
                and argv[1] in ("-c", "-lc") and argv[2] == script):
            matches.append(table[pid])
        elif simple and argv == simple:
            execs.append(table[pid])
    matches = matches or execs
    return matches[0] if len(matches) == 1 else None


def call_tree(table, root, leader, skip):
    """The pids of a session's processes below root, and every process below them, but those in skip."""
    inside = {pid for pid in below(table, root) if table[pid]["sid"] == leader}
    for pid in list(inside):
        inside.update(below(table, pid))
    return inside - set(skip) - {os.getpid()}


def gone(pid, start):
    one = proc(pid)
    return one is None or one["start"] != start


def tell(leader, text):
    """Appends text to the end of a command's output file, when its output goes to a regular file, as Claude Code's
    does, so the agent reads it with the output."""
    path = f"/proc/{leader}/fd/1"
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NONBLOCK)
    except OSError:
        return
    try:
        os.write(handle, f"\n{text}\n".encode())
    except OSError:
        pass
    finally:
        os.close(handle)


def named(names):
    """Process names as a list says them, such as `bash, test-bin ×2`, in the order each first ended."""
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{name} ×{count}" if count > 1 else name for name, count in counts.items())


def span(seconds):
    return f"{int(seconds)} s" if seconds < 60 else f"{int(seconds // 60)} min"


def stop(call, root, skip=(), grace=TERM_GRACE, codex_pid=None):
    """
    Ends the processes of `call`, the newest waiting tool call as agent_run.Tasks.call gives it, with `command` its
    whole Bash command or None, among the processes below root but those in skip. Returns the names of the processes
    it ended and what it did, in words for the log and the command.
    """
    if call is None:
        return [], "The run waits on no tool call, so nothing was stopped."
    what = f"{call['tool']}" + (f": {call['summary']}" if call["summary"] else "")
    table = processes()
    if codex_pid is None:
        leader = call_session(table, root, skip, call.get("command"), call["waited"])
    else:
        leader = codex_shell(table, codex_pid, skip, call.get("command"), call["waited"])
    if leader is None:
        if codex_pid is not None:
            return [], f"The run waits on {what}. Could not identify one matching Codex shell."
        return [], f"The run waits on {what}, which runs no command to stop, so nothing was stopped."
    pid = leader["pid"]
    if gone(pid, leader["start"]):
        return [], f"The command in {what} already ended."
    tell(pid, f"agent-desk: the user stopped this command after {span(call['waited'])}, so it failed. Go on "
         "without its result, and say in your review that the user stopped it.")

    def members(snapshot):
        if leader["sid"] == pid:
            return call_tree(snapshot, root, pid, skip)
        shell = snapshot.get(pid)
        return {pid, *below(snapshot, pid)} - set(skip) if shell and shell["start"] == leader["start"] else set()

    targets = {target: table[target] for target in members(table)}
    ended, killed = [], []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        deadline = time.monotonic() + (grace if sig == signal.SIGTERM else 1)
        live = {pid: one for pid, one in targets.items() if not gone(pid, one["start"])}
        for pid in live:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        while live and time.monotonic() < deadline:
            time.sleep(0.05)
            fresh = processes()
            for pid in members(fresh):
                if pid not in targets:
                    targets[pid] = live[pid] = fresh[pid]
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
            for pid, one in list(live.items()):
                if gone(pid, one["start"]):
                    (ended if sig == signal.SIGTERM else killed).append(one["name"])
                    del live[pid]
        if not live:
            break
    names = ended + killed
    text = f"The user stopped the command the run waited on for {span(call['waited'])} in {what}"
    text += f", ending {named(ended)}" if ended else ""
    text += f", and killed {named(killed)}, which outlived TERM" if killed else ""
    return names, text + ". The agent goes on with its failure."


def answer(run, call, root, skip, say, done, grace=TERM_GRACE, codex_pid=None):
    """
    Answers each ask for `run` until `done` is set: stops the waiting call, which `call()` gives, logs what it did
    through `say`, and writes the answer the command reads.
    """
    pattern = f"{run}-*.ask"
    while not done.wait(ASK_POLL):
        for path in sorted(folder().glob(pattern)):
            try:
                age = time.time() - path.stat().st_mtime
                path.unlink()
            except OSError:
                continue
            if age > ASK_WAIT:
                continue
            names, text = stop(call(), root, skip, grace, codex_pid)
            if names:
                say(text)
            reply = path.with_suffix(".json")
            try:
                reply.with_suffix(".part").write_text(json.dumps({"ended": names, "text": text,
                                                                  "at": datetime.now().astimezone().isoformat()}),
                                                      encoding="utf-8")
                reply.with_suffix(".part").replace(reply)
            except OSError:
                pass
