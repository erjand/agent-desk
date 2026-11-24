"""
The dispatcher's sweep: on each reconcile, it starts `agent-desk sweep`, which lib/sweep.sh describes, for each project
whose last sweep started at least SWEEP_SECONDS ago, in a unit of its own as a cleanup runs. It records when each
project's sweep started in SWEEPS, so a dispatcher restart, such as the one each merge into agent-desk's main causes,
starts none early. A sweep still running when the next comes due keeps its unit's name, so systemd starts no second
one. `Dispatcher` in `lib/dispatch.py` inherits `SweepMixin`, whose methods use the dispatcher's `clock`, `desk_lib`,
`say`, `systemd`, and `sweeps`.
"""

import json
import os
import re
from pathlib import Path

import store
from run_units import CARRIED_ENV, PRIORITY, SLICE, projects

# How often each project's sweep runs.
SWEEP_SECONDS = 3600
# A sweep's time limit.
SWEEP_MINUTES = 15
# When each project's newest sweep started, as epoch seconds by project.
SWEEPS = store.STATE / "sweeps.json"


def sweep_unit(project):
    """The unit of a project's sweep, the same for each one, so two never run at once."""
    return f"agent-desk-sweep-{re.sub(r'[^A-Za-z0-9_.-]+', '-', project)}.service"


class SweepMixin:
    def read_sweeps(self):
        try:
            found = json.loads(Path(self.sweeps).read_text())
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def sweep(self):
        """Starts the sweep of each project with a checkout here whose last sweep started SWEEP_SECONDS ago or more.
        A sweep that fails to start waits SWEEP_SECONDS too, so it says so once an hour at most."""
        swept = self.read_sweeps()
        now = self.clock()
        due = [(project, checkout) for project, checkout in projects()
               if Path(checkout).is_dir() and now - swept.get(project, 0) >= SWEEP_SECONDS]
        if not due:
            return
        for project, checkout in due:
            swept[project] = now
            self.start_sweep(project, checkout)
        path = Path(self.sweeps)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(swept, sort_keys=True) + "\n")
        except OSError as problem:
            self.say(f"Couldn't record the sweeps in {path}: {problem}")

    def start_sweep(self, project, checkout):
        unit = sweep_unit(project)
        env = {name: os.environ[name] for name in CARRIED_ENV if os.environ.get(name)}
        env["GH_REPO"] = project
        args = ["systemd-run", "--user", "--unit", unit, "--slice", SLICE, "--collect", "--quiet", "--description",
                f"agent-desk sweep of finished worktrees in {project}", "--working-directory", str(checkout),
                "-p", f"RuntimeMaxSec={SWEEP_MINUTES}min"]
        for setting in PRIORITY:
            args += ["-p", setting]
        for name, value in env.items():
            args += ["--setenv", f"{name}={value}"]
        args += ["--", str(self.desk_lib.parent / "bin" / "agent-desk"), "sweep"]
        done = self.systemd(args)
        if done.returncode != 0:
            self.say(f"The sweep of {project} failed to start: {(done.stderr or done.stdout).strip()}")
            return
        self.say(f"Started the sweep of {project} in {unit}.")
