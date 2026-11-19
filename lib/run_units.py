"""The systemd units the dispatcher starts: where agent-desk's code lives, each project's slots and run environment, its
main checkout, and the systemd-run command that starts a unit. lib/dispatch.py and lib/branch_watch.py share
them, and the slice that holds every unit, which `python3 run_units.py slice` prints as `agent-desk dispatch install`
writes it."""

import os
import subprocess
import sys
from pathlib import Path

import memory
import store

LIB = Path(__file__).resolve().parent
AGENT_DESK = LIB.parent / "bin" / "agent-desk"
# A project's slots when its config leaves them out. `benchmark` caps the agent runs a `benchmark` issue may hold at
# once.
DEFAULT_SLOTS = {"agent": 3, "chore": 1, "fix": 1, "check": 2, "benchmark": 2}
# Each kind's time limit in minutes when the project's `limitMinutes` leaves it out. `benchmark` is the limit of an
# agent run of an issue labelled `benchmark`.
LIMIT_MINUTES = {"agent": 180, "benchmark": 300, "chore": 120, "fix": 90}
# The CPU and I/O weights of each unit and of the slice that holds them.
WEIGHTS = ("CPUWeight=20", "IOWeight=20")
# The priority of each unit: below the user's apps.
PRIORITY = ("Nice=10", *WEIGHTS)
# The slice every unit runs in, so runs that grow reclaim memory from each other before the kernel swaps out the
# user's apps. It sets no MemoryMax, so the kernel slows a run that grows rather than killing its processes. systemd
# reads each dash in a slice's name as a level, so the name has none: the slice sits beside app.slice, where its
# weights compete with the user's apps.
SLICE = "agentdesk.slice"
UNITS = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
# How long the exit handler, which may release an issue on GitHub, has after the run stops.
STOP_SECONDS = 180
# The dispatcher's own environment that a unit carries over, so a desk with its own state folder keeps it, and a run of
# the installed checkout may migrate the store.
CARRIED_ENV = ("PATH", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "AGENT_DESK_INSTALLED")


def slots_of(config, override=None):
    """The project's `slots` over the defaults, and over both the counts `agent-desk slots` set for it, which
    lib/hold.py's slot_override reads."""
    slots = dict(DEFAULT_SLOTS)
    configured = config.get("slots")
    if isinstance(configured, dict):
        slots.update({key: value for key, value in configured.items() if isinstance(value, int) and value >= 0})
    slots.update({key: value for key, value in (override or {}).items()
                  if key in DEFAULT_SLOTS and isinstance(value, int) and not isinstance(value, bool) and value >= 0})
    return slots


def limit_minutes(config, kind):
    """A run's time limit in minutes: the project's `limitMinutes` for `kind`, or LIMIT_MINUTES when it sets no whole
    number above 0."""
    limits = config.get("limitMinutes")
    value = limits.get(kind) if isinstance(limits, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else LIMIT_MINUTES[kind]


def run_env(config):
    """The environment the project's `env` gives its runs, with a leading ~/ read as the home folder."""
    pairs = config.get("env") or {}
    if not isinstance(pairs, dict):
        return {}
    home = str(Path.home())
    return {str(key): (f"{home}/{value[2:]}" if str(value).startswith("~/") else str(value))
            for key, value in pairs.items()}


def quoted(argument):
    """One argument of a systemd command line."""
    return '"' + str(argument).replace("\\", "\\\\").replace('"', '\\"') + '"'


def projects():
    """Each project in the projects file agent-desk install writes, with its main checkout."""
    try:
        lines = store.PROJECTS.read_text().splitlines()
    except OSError:
        return []
    return [(parts[0], parts[1]) for parts in (line.split(None, 1) for line in lines) if len(parts) == 2]


def slice_memory_high():
    """The slice's MemoryHigh, as the share of physical memory systemd reads it as."""
    return f"{memory.SHARES['sliceMemoryHigh'] * 100:g}%"


def slice_text():
    """The agentdesk.slice file `agent-desk dispatch install` writes and `agent-desk doctor` checks."""
    return "\n".join(["[Unit]", "Description=agent-desk runs, which share a memory ceiling", "", "[Slice]",
                      f"MemoryHigh={slice_memory_high()}", *WEIGHTS, ""])


def unit_command(unit, description, checkout, minutes, high, handler, env, command):
    """The systemd-run command that starts a unit in the slice with its limit, priority, memory ceiling, exit handler,
    and environment."""
    args = ["systemd-run", "--user", "--unit", unit, "--slice", SLICE, "--collect", "--quiet", "--description",
            description,
            "--working-directory", str(checkout), "-p", f"RuntimeMaxSec={minutes}min"]
    for setting in (*PRIORITY, f"MemoryHigh={high}G", f"TimeoutStopSec={STOP_SECONDS}", f"ExecStopPost={handler}"):
        args += ["-p", setting]
    for name, value in env.items():
        args += ["--setenv", f"{name}={value}"]
    return args + ["--", *map(str, command)]


def checkout_of(desk, project):
    """The project's main checkout, from the projects file agent-desk install writes, or the store."""
    try:
        for line in store.PROJECTS.read_text().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0] == project:
                return parts[1]
    except OSError:
        pass
    return desk.project_path(project)


def systemd(args):
    """Runs a systemctl or systemd-run command for the user's manager."""
    return subprocess.run(args, capture_output=True, text=True)


if __name__ == "__main__":
    if sys.argv[1:] != ["slice"]:
        sys.exit("usage: python3 run_units.py slice")
    sys.stdout.write(slice_text())
