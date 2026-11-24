"""The stand-ins and test bases the tests of agent-desk/lib/dispatch.py share."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import branch_watch  # noqa: E402
import checks  # noqa: E402
import dispatch  # noqa: E402
import store  # noqa: E402

REPO = "org/game"


class FakeSystemd:
    """systemd-run and systemctl for the user's manager: started units stay live until a test stops them."""

    def __init__(self):
        self.started = []
        self.live = set()
        self.pids = {}
        self.fail_start = False
        self.fail_list = False
        self.stopped = []

    def __call__(self, args):
        if args[0] == "systemd-run":
            if self.fail_start:
                return subprocess.CompletedProcess(args, 1, "", "Unit already exists.")
            unit = args[args.index("--unit") + 1]
            self.started.append(args)
            self.live.add(unit)
            self.pids[unit] = 1000 + len(self.started)
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ["systemctl", "--user", "list-units"]:
            if self.fail_list:
                return subprocess.CompletedProcess(args, 1, "", "Failed to connect to bus")
            lines = [f"{unit} loaded active running agent-desk run" for unit in sorted(self.live)]
            lines.append("agent-desk-web.service loaded active running agent-desk web page")
            return subprocess.CompletedProcess(args, 0, "\n".join(lines) + "\n", "")
        if args[:3] == ["systemctl", "--user", "stop"]:
            self.stopped.append(args[-1])
            self.live.discard(args[-1])
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ["systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(args, 0, f"{self.pids.get(args[-1], 0)}\n", "")
        raise AssertionError(f"unexpected command {args}")

    def units(self):
        return [args[args.index("--unit") + 1] for args in self.started]

    def commands(self):
        return [args[args.index("--") + 2:] for args in self.started]

    def properties(self, index=-1):
        args = self.started[index]
        return [args[i + 1] for i, arg in enumerate(args) if arg == "-p"]

    def env(self, index=-1):
        args = self.started[index]
        return dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "--setenv")


class FakeMemory:
    def __init__(self):
        self.ready = True
        # The kinds of run held for memory even while `ready`.
        self.short = set()
        self.reason = "the run needs 6 GB and 8 GB stays free for the user, and 3.0 GB is free"
        self.asked = []
        self.claims = []
        # The shrunk claims lib/memory.py would find, and the unit each process runs in.
        self.shrinking = []
        self.units = {}

    @contextmanager
    def locked(self):
        yield

    def spare(self, repo, kind, labels, waited, config_path):
        self.asked.append((repo, kind, labels))
        ready = self.ready and kind not in self.short
        return ready, 6, None if ready else self.reason

    def claim(self, gb, pid):
        self.claims.append((gb, pid))

    def shrunk(self):
        return self.shrinking

    def unit_of(self, pid):
        return self.units.get(pid)


class DispatchCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.checkout = self.folder / "game"
        self.checkout.mkdir()
        self.configure({"slots": {"agent": 2}})
        (self.folder / "projects").write_text(f"{REPO} {self.checkout}\n")
        env = {key: value for key, value in os.environ.items()
               if key not in ("AGENT_DESK_RUN", "AGENT_DESK_AUTHOR")}
        env["GH_REPO"] = REPO
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(store, "PROJECTS", self.folder / "projects")):
            patch.start()
            self.addCleanup(patch.stop)
        self.store = store.Store(self.folder / "desk.sqlite")
        self.addCleanup(self.store.close)
        self.systemd = FakeSystemd()
        self.memory = FakeMemory()
        self.time = 1_000_000.0
        self.said = []
        self.hold = {"state": "running", "picked": []}
        # The project's sweep ran as the test starts, so a test that sweeps nothing starts no sweep unit.
        (self.folder / "sweeps.json").write_text(json.dumps({REPO: self.time}))
        self.dispatcher = self.new_dispatcher()

    def new_dispatcher(self):
        dispatcher = dispatch.Dispatcher(self.store, run=self.systemd, gate=self.memory, clock=lambda: self.time,
                                         say=self.said.append, mode=lambda: self.hold,
                                         mode_stamp=lambda: json.dumps(self.hold, sort_keys=True),
                                         sweeps=self.folder / "sweeps.json")
        dispatcher.rebuild()
        return dispatcher

    def configure(self, config):
        (self.checkout / ".agent-desk.json").write_text(json.dumps(config))

    def request(self, kind, target, benchmark=False, env=None, agent=False):
        environ = {"AGENT_DESK_RUN": "9"} if agent else {}
        with mock.patch.dict(os.environ, environ):
            return self.store.request_run(REPO, kind, target, benchmark, env)

    def status(self, run):
        return self.store.run(run)["status"]

    def waits(self, run):
        """The gate and reason the store records for why a run waits."""
        row = self.store.run(run)
        return row["wait_gate"], row["wait_reason"]

    def end(self, run, status="succeeded"):
        """The unit's exit handler recording the end, and the unit going away."""
        self.store.run_ended(run, status, 0)
        self.systemd.live.discard(self.store.run(run)["unit"])


class GitCase(DispatchCase):
    """A project whose main checkout is a git repository with a commit on main, and checks that log and work in the
    test's folder."""

    def setUp(self):
        super().setUp()
        for patch in (mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}),
                      mock.patch.object(checks, "LOGS", self.folder / "logs"),
                      mock.patch.object(checks, "WORK", self.folder / "work")):
            patch.start()
            self.addCleanup(patch.stop)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.commit("main", "a.txt", "base\n")

    def git(self, *args):
        done = subprocess.run(["git", "-C", str(self.checkout), *args], capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def commit(self, branch, name, text):
        """Commits `text` to file `name` on `branch`, cut from main when it's new, and returns the new head."""
        if branch != "main":
            known = subprocess.run(["git", "-C", str(self.checkout), "rev-parse", "-q", "--verify",
                                    f"refs/heads/{branch}"], capture_output=True).returncode == 0
            self.git("checkout", "-q", *([branch] if known else ["-b", branch, "main"]))
        (self.checkout / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", f"Change {name}")
        head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return head

    def wait(self, seconds):
        """Lets `seconds` pass a second at a time, with a wake each second."""
        for _ in range(seconds):
            self.time += 1
            self.dispatcher.tick()

    def settle(self):
        """Lets a head that moves now settle: the next wake reads it, and its check starts SETTLE_SECONDS later."""
        self.wait(branch_watch.SETTLE_SECONDS + 1)

    def moves(self):
        return [event["payload"] for event in self.store.events_since(0) if event["type"] == "branch-moved"]
