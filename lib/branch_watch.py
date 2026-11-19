"""
The dispatcher's branch checks: BranchWatch reads each project's agent/* heads, records each move, queues a check of
a branch's newest head, and starts each queued check as lib/checks.py's unit. lib/dispatch.py's Dispatcher ticks it on
each wake and passes itself as the host, whose desk, running runs, mode, systemd, say, and desk_lib the watch uses, so
this module never imports lib/dispatch.py.
"""

import collections
import os
import sys

import checks
import hold
import memory
from config import read as read_config
from run_units import CARRIED_ENV, checkout_of, projects, quoted, run_env, slots_of, unit_command

# How long a branch's head stands still before its check starts, unless the run working the branch ends first.
SETTLE_SECONDS = 60
# How often the dispatcher reads each project's agent/* heads. One `git for-each-ref` costs about half a millisecond
# of CPU, so five projects read on every 200 ms wake would take about 1% of a core.
REFS_SECONDS = 1


def run_branch(desk, request):
    """The branch a run works: agent/issue-<n> for an agent run on issue n, none for a discussion of an idea, else the
    head branch of the PR it targets, when the store knows it."""
    target = request["target"]
    if request["kind"] == "agent" and target.isdigit():
        return f"agent/issue-{target}"
    if request["kind"] == "agent" and target.startswith("idea-"):
        return None
    if request["kind"] in ("agent", "fix"):
        return desk.review_branch(request["project"], int(target.removeprefix("pr-")))
    return None


class BranchWatch:
    """
    Notices each move of a project's agent/* heads and checks the newest head of each branch: it records the move as
    a `branch-moved` event, which supersedes the branch's checks of older heads, and queues a check once the run
    working the branch ends or the head stands still for SETTLE_SECONDS. A queued check starts in a `check` slot as
    lib/checks.py's unit. Whenever an open review's head or its base moves, it also tries the head against the base
    with `git merge-tree` and records whether it merges, whether or not the project has a `test` command.
    """

    def __init__(self, dispatcher):
        self.host = dispatcher
        self.desk = dispatcher.desk
        # Each (project, branch)'s head as last recorded, and the heads that wait to settle, with when they moved and
        # whether a run worked their branch since.
        self.heads = {}
        self.settling = {}
        self.read = None
        # The (head, base head) each (project, branch) was last tried at, so a pair git couldn't try isn't tried again.
        self.tried = {}

    def rebuild(self, live):
        """
        Takes the heads the store recorded, so a move while the dispatcher was down counts on the first read, and sets
        each recorded head without a check settling from now, so a restart inside a head's settle window keeps its
        check. settle() still skips a head its base holds or whose project has no `test` command.
        """
        now = self.host.clock()
        self.heads, self.settling, self.read = {}, {}, None
        for project, checkout in projects():
            for branch, head in self.desk.branch_heads(project).items():
                self.heads[(project, branch)] = head
                if not self.desk.checked(project, branch, head):
                    self.settling[(project, branch)] = {"head": head, "since": now, "worked": False,
                                                        "checkout": checkout}
        if live is not None:
            self.reconcile(live)

    def tick(self, now):
        """Reads the heads every REFS_SECONDS, queues the settled ones, and starts queued checks. Returns whether a
        head moved."""
        if self.read is not None and now - self.read < REFS_SECONDS:
            return False
        self.read = now
        moved = False
        listed = projects()
        for project, checkout in listed:
            refs = checks.read_refs(checkout, {one["review_base"] for one in self.desk.merge_targets(project)})
            if refs is None:
                continue
            heads, bases = refs
            known = {branch: head for (owner, branch), head in self.heads.items() if owner == project}
            for branch in sorted(set(known) | set(heads)):
                if heads.get(branch) != known.get(branch):
                    self.moved(project, checkout, branch, heads.get(branch), known.get(branch), now)
                    moved = True
            self.try_merges(project, checkout, bases)
        self.settle(now)
        self.start_queued(dict(listed))
        return moved

    def moved(self, project, checkout, branch, head, before, now):
        """Records a move, where a head of None is a deleted branch, and stops the checks it supersedes."""
        for row in self.desk.branch_moved(project, branch, head, before):
            unit = checks.unit_name(project, row["id"])
            self.host.say(f"Check {row['id']} of {branch} at {row['head_sha'][:12]} is superseded; stopping {unit}.")
            self.host.systemd(["systemctl", "--user", "stop", "--no-block", unit])
        key = (project, branch)
        self.settling.pop(key, None)
        if head is None:
            self.heads.pop(key, None)
            return
        self.heads[key] = head
        self.settling[key] = {"head": head, "since": now, "worked": False, "checkout": checkout}

    def try_merges(self, project, checkout, bases):
        """
        Tries each open review's head against its base's head in `bases`, unless the store holds the answer for that
        pair already, and records whether it merges.
        """
        for one in self.desk.merge_targets(project):
            base, head, base_sha = one["review_base"], one["head_sha"], bases.get(one["review_base"])
            key, pair = (project, one["branch"]), (head, base, base_sha)
            if head is None or base_sha is None or self.tried.get(key) == pair or (
                    one["mergeable"] and (one["base"], one["base_sha"]) == (base, base_sha)):
                continue
            self.tried[key] = pair
            mergeable = checks.merges_with(checkout, head, base_sha)
            if mergeable is None:
                self.host.say(f"git couldn't try {one['branch']} at {head[:12]} against {base}; it waits for a move.")
            elif self.desk.merge_tried(project, one["branch"], head, base, base_sha, mergeable):
                if mergeable != one["mergeable"]:
                    self.host.say(f"{one['branch']} at {head[:12]} {mergeable.lower()} with {base} at "
                                  f"{base_sha[:12]} in {project}.")

    def settle(self, now):
        """Queues a check of each head whose branch's run ended, or that stood still for SETTLE_SECONDS."""
        if not self.settling:
            return
        worked = self.worked()
        for key, entry in list(self.settling.items()):
            busy = key in worked
            entry["worked"] |= busy
            if now - entry["since"] < SETTLE_SECONDS and (busy or not entry["worked"]):
                continue
            self.settling.pop(key)
            project, branch = key
            head, checkout = entry["head"], entry["checkout"]
            # A project without a `test` command gets no check, and the head's own base holding it leaves nothing to
            # test.
            if (not checks.test_command(read_config(checkout)) or self.desk.checked(project, branch, head)
                    or checks.contains(checkout, head, checks.base_of(self.desk, project, branch))):
                continue
            check = self.desk.check_queue(project, branch, head)
            self.host.say(f"Queued check {check} of {branch} at {head[:12]} in {project}.")

    def worked(self):
        """The (project, branch) of each run working now."""
        return {(request["project"], run_branch(self.desk, request)) for request in self.host.running.values()}

    def start_queued(self, checkouts):
        """
        Starts each queued check, oldest first, while its project has a free `check` slot. A held project starts only
        the checks of branches a run still works, so the runs Hold lets go on can finish. Records why each check left
        waiting waits, as the dispatcher records a request's.
        """
        queued = self.desk.checks(("queued",))
        if not queued:
            return
        running = collections.Counter(row["project"] for row in self.desk.checks(("running",)))
        state, worked = self.host.mode(), self.worked()
        for row in queued:
            project = row["project"]
            checkout = checkouts.get(project) or checkout_of(self.desk, project)
            config = read_config(checkout) if checkout else {}
            mode = hold.mode_of(state, project)
            if not checks.test_command(config):
                self.host.say(f"Check {row['id']} ends as cancelled: {project} has no test command.")
                self.desk.check_ended(row["id"], "cancelled")
            elif mode == "held" and (project, row["branch"]) not in worked:
                self.note(row, "mode", *hold.MODE_WAITS[mode])
            elif running[project] >= (slots := slots_of(config, hold.slot_override(state, project))["check"]):
                self.note(row, "slot", "a free check slot", f"its project's {slots} check slots are busy")
            elif self.start(row, checkout, config):
                running[project] += 1

    def note(self, row, gate, reason, said):
        """Records why a queued check waits, and says it, when the store holds another gate or reason for it."""
        if (row.get("wait_gate"), row.get("wait_reason")) == (gate, reason):
            return
        if self.desk.check_waits(row["id"], gate, reason):
            self.host.say(f"Check {row['id']} of {row['branch']} waits: {said}.")

    def start(self, row, checkout, config):
        """Starts a queued check's unit, and returns whether it started."""
        check, project = row["id"], row["project"]
        unit = checks.unit_name(project, check)
        if not self.desk.check_started(check, checks.log_path(project, check)):
            return False
        script = self.host.desk_lib / "checks.py"
        handler = " ".join(quoted(part) for part in (sys.executable, "-B", script, "stopped", check, checkout))
        env = {name: os.environ[name] for name in CARRIED_ENV if os.environ.get(name)}
        env.update(run_env(config))
        env.update({"AGENT_DESK_UNIT": unit, "GH_REPO": project})
        done = self.host.systemd(unit_command(
            unit, f"agent-desk check of {row['branch']} at {row['head_sha'][:12]} in {project}", checkout,
            checks.test_minutes(config), memory.default_gb("agentMemoryHigh"), handler, env,
            [sys.executable, "-B", script, "run", check, checkout]))
        if done.returncode != 0:
            self.host.say(f"Check {check} failed to start: {(done.stderr or done.stdout).strip()}")
            self.desk.check_ended(check, "failed")
            return False
        self.host.say(f"Started check {check} of {row['branch']} at {row['head_sha'][:12]} in {unit}.")
        return True

    def reconcile(self, live):
        """Ends a running check whose unit vanished without recording its end, and removes its scratch worktree."""
        for row in self.desk.checks(("running",)):
            if checks.unit_name(row["project"], row["id"]) in live:
                continue
            self.host.say(f"Check {row['id']}'s unit is gone without recording its end.")
            self.desk.check_ended(row["id"], "lost")
            checkout = checkout_of(self.desk, row["project"])
            if checkout:
                checks.remove_worktree(checkout, checks.work_path(row["project"], row["id"]))
