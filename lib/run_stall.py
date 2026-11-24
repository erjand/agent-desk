"""
Flags a headless run stalled on a command that has stopped using the CPU, for the board and a desktop notification.

lib/agent_run.py checks a run every SAMPLE seconds with Watch.check. A run stalls once the newest tool call still
waiting on its result has waited STALL_AFTER seconds, 5 minutes, and the processes under the run used under STALL_CPU
percent of one CPU, 1%, over the last STALL_WINDOW seconds, 2 minutes. The watch sums the CPU between each pair of
samples, so a build that just ended still reads as busy until claude -p reports its call's result, and a process's CPU
counts the children it reaped, so a test runner whose short-lived children each end between two samples does too.
Three kinds of wait never stall:

- claude -p's own process, which writes a heartbeat every 30 seconds a call runs, counts for none of the CPU.
- A call to the Agent or Task tool waits on a subagent, which may think for minutes without using the CPU here.
- A run with an agent-desk command among its processes, one that runs a script from this folder, waits on the desk:
  `agent-desk gpu` waiting on the GPU lock or holding a GPU slot for a game or a render that leaves the CPU idle, or
  `agent-desk review checks --watch` waiting on a branch check that runs in a unit of its own.

While a run stalls, the watch writes `stalls/<run id>.json` under agent-desk's state folder, naming the call's tool,
the first line of its input, and when it started, and removes the file once the call returns, its processes use the
CPU again, or the run ends. lib/board.py's read adds the file to the running run as `stalled`, and the pane and the web
page show it on the run's card as `stalled 12 min in Bash: cargo test …`. The first time a call stalls, the watch logs
a line and sends a desktop notification through notify-send, so a call that stalls, wakes, and stalls again notifies
once. AGENT_DESK_STALL_AFTER and AGENT_DESK_STALL_WINDOW set the two waits in seconds, and AGENT_DESK_RUN names the run.
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

STALL_AFTER = float(os.environ.get("AGENT_DESK_STALL_AFTER", "300"))
STALL_WINDOW = float(os.environ.get("AGENT_DESK_STALL_WINDOW", "120"))
STALL_CPU = 1.0
# How often the watch samples the run's processes, in seconds: a tenth of the window, and at most every 15 seconds.
SAMPLE = min(15.0, STALL_WINDOW / 10)
# The tools whose call waits on a subagent instead of a command.
SUBAGENT_TOOLS = {"Agent", "Task"}
LIB = Path(__file__).resolve().parent
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


def folder():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk" / "stalls"


def path_of(run):
    return folder() / f"{run}.json"


def read(run):
    """The stall a running run's watch saved, with `since`, `tool`, and `summary`, or None while it doesn't stall."""
    try:
        saved = json.loads(path_of(run).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or not isinstance(saved.get("since"), str) or not isinstance(saved.get("tool"), str):
        return None
    return {"since": saved["since"], "tool": saved["tool"], "summary": str(saved.get("summary") or "")}


def desk_command(argv):
    """
    Whether a process's arguments run a script from agent-desk's lib folder, as every `agent-desk` command does: the
    first argument after the interpreter that isn't an option, such as `-B`, names the script.
    """
    script = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
    return script.endswith((".sh", ".py")) and Path(os.path.realpath(script)).parent == LIB


def sample(pids, skip=()):
    """
    The CPU time each process in pids but those in skip has used, its own and its reaped children's, in clock ticks,
    keyed by its pid and start time, and whether one runs an agent-desk command.
    """
    ticks, desk = {}, False
    for pid in pids:
        if pid in skip:
            continue
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            argv = [arg.decode("utf-8", "replace") for arg in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")]
            ticks[(pid, fields[19])] = sum(int(field) for field in fields[11:15])
        except (OSError, IndexError, ValueError):
            continue
        desk = desk or desk_command(argv)
    return ticks, desk


def used(before, after):
    """The ticks the processes in `after` used since `before`; a process `before` lacks started since."""
    return sum(max(0, spent - before.get(key, 0)) for key, spent in after.items())


def moment(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def text(tool, summary, waited):
    """A stall as the card and the notification say it, such as `stalled 12 min in Bash: cargo test …`."""
    return f"stalled {int(waited // 60)} min in {tool}" + (f": {summary}" if summary else "")


def notify(body):
    """Sends a desktop notification, skipping it where notify-send is missing or no desktop answers."""
    try:
        subprocess.run(["notify-send", "--app-name=agent-desk", "An agent run stalled", body],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def run_name():
    """The run as its unit's description names it, such as `agent run 612 in owner/repo`, or else by its id."""
    unit = os.environ.get("AGENT_DESK_UNIT")
    if unit:
        try:
            done = subprocess.run(["systemctl", "--user", "show", "-p", "Description", "--value", unit],
                                  capture_output=True, text=True, timeout=10, check=False)
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip().removeprefix("agent-desk ")
        except (OSError, subprocess.SubprocessError):
            pass
    run = os.environ.get("AGENT_DESK_RUN")
    return f"run {run}" if run else "a run"


class Watch:
    """
    The stall rule over one run's samples. check takes the newest call still waiting, a sample's ticks, and whether an
    agent-desk command runs, and writes, clears, and notifies as the rule says.
    """

    def __init__(self, run=None, after=STALL_AFTER, window=STALL_WINDOW, log=print, tell=notify):
        self.run = run if run is not None else os.environ.get("AGENT_DESK_RUN") or None
        self.after, self.window, self.log, self.tell = after, window, log, tell
        # The samples, oldest first, from the newest one at least a window old.
        self.samples = []
        # The id of the call the saved stall names, and the calls already logged and notified.
        self.flagged = None
        self.told = set()

    def idle(self, now, ticks):
        """
        Whether the processes used under STALL_CPU percent of one CPU over at least the last window, summed between
        each pair of samples, so a process that exited keeps the CPU it used while it ran.
        """
        spent = used(self.samples[-1][1], ticks) if self.samples else 0
        self.samples.append((now, ticks, spent))
        while len(self.samples) > 1 and now - self.samples[1][0] >= self.window:
            self.samples.pop(0)
        then = self.samples[0][0]
        if now - then < self.window:
            return False
        return sum(one[2] for one in self.samples[1:]) / CLOCK_TICKS / (now - then) * 100 < STALL_CPU

    def check(self, now, call, ticks, desk):
        """
        Applies the rule at `now`, in monotonic seconds: `call` is the newest call still waiting as Tasks.call gives
        it, a dict of its `id`, `tool`, `summary`, and `waited` seconds, or None.
        """
        idle = self.idle(now, ticks)
        if call is None or call["tool"] in SUBAGENT_TOOLS or desk or not idle or call["waited"] < self.after:
            self.clear()
            return
        if self.flagged != call["id"]:
            self.save(call)
        if call["id"] not in self.told:
            self.told.add(call["id"])
            said = text(call["tool"], call["summary"], call["waited"])
            self.log(f"Stalled: {said[len('stalled '):]}, with the run's processes under {STALL_CPU:g}% of a CPU "
                     f"for {int(self.window)} s.")
            self.tell(f"{run_name()}: {said}")

    def save(self, call):
        self.flagged = call["id"]
        if self.run is None:
            return
        since = datetime.now(timezone.utc).timestamp() - call["waited"]
        try:
            folder().mkdir(parents=True, exist_ok=True)
            path = path_of(self.run)
            path.with_suffix(".tmp").write_text(json.dumps({"since": moment(since), "tool": call["tool"],
                                                            "summary": call["summary"]}), encoding="utf-8")
            path.with_suffix(".tmp").replace(path)
        except OSError as error:
            self.log(f"Could not save the stall to {path_of(self.run)}: {error}")

    def clear(self):
        if self.flagged is None:
            return
        self.flagged = None
        if self.run is not None:
            try:
                path_of(self.run).unlink(missing_ok=True)
            except OSError:
                pass
