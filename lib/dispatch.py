"""Starts the runs the store asks for, each as its own systemd user unit, as slots, locks, and memory allow.

    python3 dispatch.py serve                    watches the store and starts each requested run; the
                                                 agent-desk-dispatch.service unit runs this
    python3 dispatch.py request <kind> <target> [--benchmark] [--env NAME=VALUE]... [--repo <owner/repo>]
                                                 asks for a run, and prints its id
    python3 dispatch.py stop <kind> <target> [--dry-run] [--repo <owner/repo>]
                                                 stops the units of the runs working a target, drops its requests
                                                 that wait, and prints each unit it stopped; --dry-run only prints
                                                 the units it would stop
    python3 dispatch.py runs [--repo <owner/repo>]
                                                 prints the project's runs that wait or work, as JSON: each one's id,
                                                 kind, target, status, unit, start, whether it is a benchmark run,
                                                 and for one that started, the account it billed,
                                                 and for a request that waits, the gate that holds it and why;
                                                 and each run that failed, timed out, or was lost in the last day
                                                 whose target no run has asked for since, with its end
    python3 dispatch.py chore-runs [--repo <owner/repo>]
                                                 prints each chore's newest run, one tab-separated line per chore:
                                                 the skill, when it started in epoch seconds, and its status, which
                                                 lib/chores.sh reads for its schedule
    python3 dispatch.py again <run>              asks for a run's kind and target again, and prints the new run's id;
                                                 it picks nothing, so in Manual the run waits again for Start
    python3 dispatch.py cancel <run>             ends one run that still waits as cancelled and leaves every other
                                                 run alone; exits 1 for a run that works or has ended
    python3 dispatch.py unqueue <n> [--repo <owner/repo>]
                                                 sends queued or waiting issue n back to open: cancels each agent
                                                 request that waits for it and drops its Manual pick; exits 1, changing
                                                 nothing, while an agent run works it
    python3 dispatch.py stop-command <n> [--repo <owner/repo>]
                                                 ends the command issue n's agent run waits on and leaves the run
                                                 going, as lib/run_stop.py describes; exits 1 when it ends nothing
    python3 dispatch.py stopped                 records how a unit's run ended; each unit's ExecStopPost runs this

A request names a kind and a target:

    agent <n>        agent-desk work-issue <n>
    agent pr-<n>     agent-desk work-pr <n>, for a PR from an agent/<slug> branch that works no issue
    agent idea-<n>   agent-desk discuss-idea <n>, a discussion of issue n while it is an idea
    fix <pr>         agent-desk fix <pr>
    chore <skill>    agent-desk run-chore <skill>

`--benchmark` gives an agent run the longer limit of a `benchmark` issue, and `--env` passes REPLY_PR, REQUEST, or
EVENT to the run as the agent workflow does. The `check` kind takes no requests: the dispatcher starts the branch
checks itself.

The dispatcher reads the store's newest event id every 200 ms. A request passes its gates in this order: the desk's
mode lets it start, the weekly usage limit and the API budget let it start, its issue isn't on hold, nothing blocks its issue, no other issue holds its issue's
serial slot, the project has a free slot of its kind in `slots` in its .agent-desk.json, and for an issue labelled
`benchmark` or `gpu` a free slot of that label, no other run holds its target,
and lib/memory.py finds the memory spare. Until then it waits, so a held or blocked request never takes a slot, and it
starts when an event clears the gate. While it waits, its `runs` row records the gate and a short reason, as Wait and
note describe, which the board shows on its card. On Hold, every request of the project waits, and starts on
Resume. In Manual, every agent, fix, and chore request waits until Start picks it, by
its run id, or for a fresh start, a request with EVENT=issues for an issue, by its issue, whatever asked for it: an
agent-ready label, Retry or Continue, the wake, a comment sent to the agent, Fix, a chore's Start Now, a repeat of a
chore, or a merge-queue fix. Start on Hold picks too, so Resume into Manual starts what Start picked. While
lib/usage.py's weekly usage limit is on and the plan's usage reads at or over it, every request waits, in every project; the retry every RETRY_SECONDS starts it
once a reading falls under the limit, as after the weekly reset, and Resume never lifts it. While the account is `api` and
lib/budget.py's API budget is on, a month's spend at or over it holds every request the same way, until the month
turns, the budget rises, or the account moves to `subscription`. It ignores a request an
agent wrote, and a request for a target that already has one waiting with the same benchmark limit and environment, so
two presses of a button queue one run; a request behind a working run of its target still waits, since it may carry a
new comment or review. A restart leaves running units alone and rebuilds its view from the `runs` table and the units
systemd lists.

The merge queue waits on its fixes, so a fix request tries to start before every other request, unless an earlier
request holds its target, and lib/memory.py lets it start under more pressure and counts no other run's claim against
it. While a fix waits for memory, every other request waits for it, in every project, even one that waited past
resources.maxWaitMinutes, so a freed slot or freed memory never goes to new work ahead of the fix.

An agent request for an issue waits on the store's links and states, with lib/serial_slots.py's rules, as `agent-desk
serial claim` does. Any request for an issue on hold, such as one a comment sent to the agent asks for, waits until the
issue leaves on-hold, as `agent-desk unpause` moves it to queued; the wake then asks for no other run of it. A fresh
start, one a label, the wake, or `agent-desk serial release` asked for, waits while an
open issue blocks the issue. Any request for an issue with a `serial` or `serial:<group>` label waits while another issue in one
of its groups is in-progress, or works in a running agent run that hasn't claimed it yet. The first time a request
waits on either, the dispatcher moves its issue to `waiting` and says why in a note, unless the issue is on-hold or in
needs-design, which it leaves as they are. A `state-changed`, label, link, or `item-deleted` event, such as a blocker
closing or a serial holder's run moving its issue out of in-progress, makes it try the waiting requests again, oldest
request first, except that the merge queue's fixes go first and the follow-ups a comment sent to the agent asked for
and the requests for in-progress issues next, and fresh starts of queued and waiting issues go in their project's start order, as start_order describes.
On that event, at an agent run's end, and on each reconcile it also asks for a run of each `queued` issue that no run
was asked for since it last moved to queued, as an `agent-ready` label added from the command line leaves it, and of
each `waiting` issue that has no request, once nothing blocks it and its serial slot is free, as the wake in
lib/dispatch_wake.py describes, so every queued or waiting issue has something to start it. In Manual, a waiting
serial issue gets its request only once picked, and a request that waits for Start holds no serial slot. The
Agent-ready button asks for its run as well, so a fresh start the same as a run that already works its issue, asked
for since the issue moved to queued, ends as ignored, and one press starts one run.

Every second it also reads each project's agent/* heads and its open reviews' bases with `git for-each-ref` in its
main checkout, records each move as a `branch-moved` event, and checks the branch's newest head, as BranchWatch in
lib/branch_watch.py describes: lib/checks.py runs the project's `test` command on the head merged with its base in a
unit of its own, in a `check` slot. Whenever a review's head or base moves, `git merge-tree` says whether the head
still merges with the base.

Comments sent to the agent ask for runs, and an agent/* review that ends starts its cleanup, as lib/dispatch_comments.py
describes. Each reconcile also starts `agent-desk sweep` for each project not swept in the last hour, as
lib/dispatch_sweep.py describes.
"""

import argparse
import calendar
import json
import os
import re
import sqlite3
import sys
import time
import typing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import account  # noqa: E402
import budget  # noqa: E402
import hold  # noqa: E402
import memory  # noqa: E402
import serial_slots  # noqa: E402
import store  # noqa: E402
import usage  # noqa: E402
from branch_watch import BranchWatch  # noqa: E402
from config import read as read_config  # noqa: E402
from dispatch_comments import CommentsMixin  # noqa: E402
from dispatch_sweep import SWEEPS, SweepMixin  # noqa: E402
from dispatch_wake import WakeMixin  # noqa: E402
from run_control import cancel_run, chore_runs, pause_issue, stop_runs, unpause_issue, unqueue_issue  # noqa: E402
from run_stop import StopError, stop_command  # noqa: E402
from run_end import stopped  # noqa: E402
from run_units import (CARRIED_ENV, LIB, checkout_of, limit_minutes, quoted, run_env,  # noqa: E402
                       slots_of, systemd, tmp_folder, unit_command)
from store_runs import labelled  # noqa: E402

KINDS = ("agent", "chore", "fix", "check")
# The kinds a request may name; BranchWatch starts the `check` units itself.
REQUESTABLE = ("agent", "chore", "fix")
# A discussion run's time limit: it reads and answers, and changes no code.
DISCUSS_MINUTES = 30
# The share of the machine's memory where the kernel starts to reclaim from a unit of each kind.
MEMORY_HIGH = {"agent": "agentMemoryHigh", "chore": "choreMemoryHigh", "fix": "choreMemoryHigh"}
# The variables a request may pass to its run.
REQUEST_ENV = ("REPLY_PR", "REQUEST", "EVENT")
TARGETS = {"agent": re.compile(r"\d+|pr-\d+|idea-\d+"), "fix": re.compile(r"\d+"),
           "chore": re.compile(r"[a-z0-9][a-z0-9-]*")}
WAKE_SECONDS = 0.2
# A waiting request tries again this often while nothing else changes, since memory frees without an event.
RETRY_SECONDS = 15
# How often the dispatcher compares its running runs with the units systemd lists.
RECONCILE_SECONDS = 30
# How long a failed run stays on the board, as a failed Actions run does.
FAILED_SECONDS = store.FAILED_SECONDS
LIVE_STATES = ("active", "activating", "deactivating", "reloading")
# The store events that may clear a waiting agent request's gate: a blocker closing, a serial holder leaving
# in-progress, a label or link that changes either, or an issue deleted with its labels and links.
GATE_EVENTS = ("state-changed", "label-added", "label-removed", "link-added", "link-removed", "item-deleted")
BLOCKED_NOTE = "Waiting for {blockers} to close. This issue starts on its own once nothing blocks it, from a main that " \
               "has that work."
SERIAL_NOTE = "Waiting for the serial slot. This issue starts on its own when the serial issues ahead of it in its " \
              "serial group finish."
# How far the GB free must move before a request that waits for memory records its reason again, since the figure
# changes on almost every try.
MEMORY_STEP = 0.5
USAGE_WAIT = "weekly usage to fall under its limit"


class Wait(typing.NamedTuple):
    """
    Why a request waits: `gate`, one of store_schema.WAIT_GATES; `reason`, which its card shows after "waits for";
    `said`, as the journal says it; and for a shortfall of memory, the GB free and needed, as lib/memory.py's Shortfall
    carries them.
    """

    gate: str
    reason: str
    said: str
    free: float | None = None
    needed: float | None = None

    def moved(self, before):
        """Whether the wait differs enough from `before`, the one recorded, or None, to record again: its gate or
        reason changed, unless only the GB free moved, by less than MEMORY_STEP."""
        if before is None or before.gate != self.gate:
            return True
        if before.reason == self.reason:
            return False
        if self.free is not None and before.free is not None and self.needed == before.needed:
            return round(abs(self.free - before.free), 2) >= MEMORY_STEP
        return True


def request_from(event):
    """The request a `run-requested` event carries, as the dispatcher keeps it."""
    payload = event["payload"]
    return {"id": payload["run"], "project": event["project"], "kind": payload["kind"], "target": payload["target"],
            "benchmark": bool(payload.get("benchmark")), "env": payload.get("env") or {}, "author": event["author"],
            "at": calendar.timegm(time.strptime(event["at"], "%Y-%m-%dT%H:%M:%SZ")), "event": event["id"]}


def twin_of(request):
    """What makes two requests the same: their project, kind, target, benchmark limit, and environment."""
    return (request["project"], request["kind"], request["target"], request["benchmark"],
            json.dumps(request["env"], sort_keys=True))


def follows_up(request):
    """
    Whether a request is a follow-up: an agent run a comment sent to the agent asked for on an issue or review an
    agent already worked, as ask_agent builds it, by its `issue_comment` event or its `pr-<n>` target. A discussion of
    an idea and a fresh start are none.
    """
    return request["kind"] == "agent" and (request["env"].get("EVENT") == "issue_comment"
                                           or request["target"].startswith("pr-"))


def discusses(request):
    """Whether a request asks for a discussion of an idea, by its `idea-<n>` target."""
    return request["kind"] == "agent" and request["target"].startswith("idea-")


def limit_of(request, config):
    """A request's time limit in minutes, the unit's RuntimeMaxSec: a discussion's DISCUSS_MINUTES, or its kind's, or
    the benchmark limit's, from the project's config."""
    if discusses(request):
        return DISCUSS_MINUTES
    return limit_minutes(config, "benchmark" if request["benchmark"] else request["kind"])


def lock_of(request):
    """The target a run holds while it works, named as the workflows' concurrency groups name it. A discussion of an
    idea holds its issue's, so it never runs beside another discussion or a work-issue run of the issue."""
    if request["kind"] == "chore":
        return request["project"], f"chores-{request['target']}"
    return request["project"], "agent-" + request["target"].removeprefix("pr-").removeprefix("idea-")


def command_of(request):
    """The agent-desk command a run's unit runs."""
    kind, target = request["kind"], request["target"]
    if kind == "agent":
        if target.startswith("idea-"):
            return ["discuss-idea", target.removeprefix("idea-")]
        return ["work-pr", target[3:]] if target.startswith("pr-") else ["work-issue", target]
    return ["fix", target] if kind == "fix" else ["run-chore", target]


def memory_kind(request):
    """The kind lib/memory.py keeps a run's history under, as the run scripts name it."""
    if request["kind"] == "chore":
        return "chore/" + request["target"]
    return "discuss" if discusses(request) else request["kind"]


def unit_name(request):
    """A unit named for the run's kind, project, and target, and its id, so a unit still running its exit handler
    never blocks the next run on the same target."""
    clean = lambda text: re.sub(r"[^A-Za-z0-9_.-]+", "-", text)
    return f"agent-desk-{request['kind']}-{clean(request['project'])}-{clean(request['target'])}-{request['id']}.service"


def picked(request, state):
    """
    Whether Start picked a request for Manual, by lib/hold.py's state: its run id, or for a fresh start, its issue,
    which Start on a queued issue's row picks before the request exists, as the wake makes it for a waiting serial
    issue.
    """
    if request["id"] in state.get("picked_runs", ()):
        return True
    return (labelled(request["kind"], request["target"], request["env"])
            and f"{request['project']}#{request['target']}" in state["picked"])


def hold_stamp():
    """When lib/hold.py last saved its state, or None before it saved any."""
    try:
        return hold.HOLD.stat().st_mtime_ns
    except OSError:
        return None


class MemoryGate:
    """lib/memory.py's check, without waiting, and its claim."""

    locked = staticmethod(memory.claims_locked)
    spare = staticmethod(memory.spare)
    claim = staticmethod(memory.claim)
    shrunk = staticmethod(memory.shrunk)
    unit_of = staticmethod(memory.unit_of)


class Dispatcher(WakeMixin, SweepMixin, CommentsMixin):
    def __init__(self, desk, run=systemd, gate=None, clock=time.time, say=None, desk_lib=LIB, mode=hold.status,
                 mode_stamp=None, usage_check=usage.check, budget_check=budget.holds, sweeps=SWEEPS):
        self.desk = desk
        self.systemd = run
        self.gate = gate or MemoryGate()
        self.clock = clock
        self.say = say or (lambda line: print(line, flush=True))
        self.desk_lib = Path(desk_lib)
        # lib/hold.py's state, and something that changes whenever that state is saved.
        self.mode = mode
        self.mode_stamp = mode_stamp or hold_stamp
        self.stamp = self.mode_stamp()
        # lib/usage.py's check of the weekly usage limit, and why it last said it can't read usage.
        self.usage_check = usage_check
        self.usage_problem = None
        # lib/budget.py's check of the API budget, given hold.json's state and the store.
        self.budget_check = budget_check
        self.last = 0
        self.pending = {}
        self.running = {}
        self.reasons = {}
        # The processes whose shrunk claims the log has named.
        self.shrunk = set()
        self.tried = 0.0
        self.reconciled = 0.0
        # The review ends up to this event id have their cleanups, and the ids of the later ones handled already.
        self.cleaned_to = 0
        self.ended = set()
        # The comments sent to the agent up to this event id have their answers, or None until it reads the store's.
        self.answered_to = None
        self.read_only = False
        self.watch = BranchWatch(self)
        self.wake_due = False
        # Where the sweep records when each project's last one started.
        self.sweeps = sweeps

    # The view of the runs.

    def live_units(self):
        """The agent-desk units systemd has loaded and not yet stopped, or None when systemctl fails."""
        done = self.systemd(["systemctl", "--user", "list-units", "agent-desk-*", "--all", "--plain", "--no-legend"])
        if done.returncode != 0:
            return None
        live = set()
        for line in done.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[2] in LIVE_STATES:
                live.add(fields[0])
        return live

    def rebuild(self):
        """Builds the view from the runs table and the units systemd lists, leaving running units alone."""
        self.last = self.desk.last_event()
        self.pending, self.running, self.reasons = {}, {}, {}
        live = self.live_units()
        for row in self.desk.runs(("requested", "running")):
            event = self.desk.request_of(row["id"])
            if event is None:
                self.say(f"Run {row['id']} has no request, so it ends as failed.")
                self.desk.run_ended(row["id"], "failed")
            elif row["status"] == "requested":
                self.accept(request_from(event))
            elif live is None or row["unit"] in live:
                # Without the list, the run counts as running until the next reconcile can check its unit.
                self.running[row["id"]] = {**request_from(event), "unit": row["unit"]}
            else:
                self.say(f"Run {row['id']}'s unit {row['unit']} is gone without recording its end.")
                self.desk.run_ended(row["id"], "lost")
        self.reconciled = self.clock()
        self.cleaned_to = self.desk.last_cleanup()
        self.ended = set()
        self.catch_up()
        self.answered_to = None
        self.answer_comments()
        self.watch.rebuild(live)
        self.say(f"Dispatcher ready: {len(self.running)} running, {len(self.pending)} waiting.")

    def accept(self, request):
        if request["id"] in self.pending or request["id"] in self.running:
            return
        if request["author"] == "agent":
            self.say(f"Ignored run {request['id']}: an agent asked for it.")
            self.desk.run_ended(request["id"], "ignored")
            return
        self.pending[request["id"]] = request

    def reconcile(self):
        """Starts the cleanups of reviews that ended without one and the sweeps that are due, answers the comments sent
        to the agent that have no answer, and ends a running run whose unit vanished without its exit handler recording
        the end."""
        self.reconciled = self.clock()
        self.catch_up()
        self.sweep()
        self.answer_comments()
        self.note_shrunk()
        live = self.live_units()
        if live is None:
            return
        self.watch.reconcile(live)
        for run, request in list(self.running.items()):
            if request["unit"] in live:
                continue
            self.running.pop(run)
            if self.desk.run(run)["status"] == "running":
                self.say(f"Run {run}'s unit {request['unit']} is gone without recording its end.")
                self.desk.run_ended(run, "lost")

    def note_shrunk(self):
        """Says once for each run when its memory claim shrinks past its settle time, as lib/memory.py decides."""
        runs = {request["unit"]: run for run, request in self.running.items()}
        found = self.gate.shrunk()
        for pid, claimed, counted, peak, minutes in found:
            if pid in self.shrunk:
                continue
            self.shrunk.add(pid)
            unit = self.gate.unit_of(pid)
            name = f"run {runs[unit]}" if unit in runs else f"the run in {unit}" if unit else f"process {pid}"
            self.say(f"The memory claim of {name} shrinks from {claimed:g} GB to {counted:g} GB: its peak so far is "
                     f"{peak:.1f} GB, and it is past its settle time of {minutes:g} min.")
        self.shrunk &= {pid for pid, *_ in found}

    # Each wake.

    def tick(self):
        """Reads the events since the last wake and starts what can start. Returns whether anything changed."""
        if self.desk.schema_newer():
            if not self.read_only:
                self.say(f"The store's schema {self.desk.version} is newer than this dispatcher's "
                         f"{store.SCHEMA_VERSION}, so it starts nothing until it restarts with newer code.")
                self.read_only = True
            return False
        self.read_only = False
        changed = gated = False
        while self.desk.last_event() > self.last:
            events = self.desk.events_since(self.last)
            if not events:
                break
            asked = False
            for event in events:
                self.last = event["id"]
                if event["type"] == "run-requested":
                    self.accept(request_from(event))
                    changed = True
                elif event["type"] == "run-ended":
                    run = event["payload"]["run"]
                    ended = self.running.pop(run, None) or self.pending.pop(run, None)
                    self.reasons.pop(run, None)
                    changed |= ended is not None
                    # An agent run that ends frees its issue's serial slot, and may leave its issue waiting.
                    gated |= ended is not None and ended["kind"] == "agent"
                else:
                    if event["type"] in ("review-merged", "state-changed") and not self.clean(event):
                        self.cleaned_to = min(self.cleaned_to, event["id"] - 1)
                    if event["type"] in GATE_EVENTS:
                        changed = gated = True
                    # A move or sort of a project's start order changes which waiting request takes the next free slot.
                    changed |= event["type"] in ("queue-moved", "queue-sorted")
                    asked |= event["type"] == "comment-added" and bool(event["payload"].get("to_agent"))
            # The runs the comments ask for come back around this loop as their `run-requested` events.
            if asked:
                self.answer_comments()
        # A Resume, a new mode, or a pick for Manual frees requests without an event in the store, and a pick lets the
        # wake start a waiting serial issue in Manual.
        stamp = self.mode_stamp()
        if stamp != self.stamp:
            self.stamp = stamp
            changed = gated = True
        now = self.clock()
        changed |= self.watch.tick(now)
        if now - self.reconciled >= RECONCILE_SECONDS:
            self.reconcile()
            changed = gated = True
        if gated or self.wake_due:
            self.wake()
        if self.pending and (changed or now - self.tried >= RETRY_SECONDS):
            self.tried = now
            self.start_ready()
        return changed

    def start_ready(self):
        """Starts each waiting request whose gates are clear, in start_order. A target's later requests wait behind its
        earliest. A request the same as one that still waits ends as ignored, and so does a fresh start the same as
        a run that works its issue and was asked for since the issue last moved to queued. While a fix the merge queue
        waits on waits for memory, no other request starts in any project, so the memory that frees goes to the fix."""
        held, waiting = {}, {}
        # The first fix that waits for memory, which every request behind it waits for.
        first = None
        for run in self.start_order():
            request = self.pending[run]
            twin, does = waiting.get(twin_of(request)), "waits for"
            if twin is None:
                twin, does = self.working_twin(request), "works"
            if twin is not None:
                self.pending.pop(run)
                self.reasons.pop(run, None)
                self.say(f"Ignored run {run}: run {twin} already {does} {request['kind']} {request['target']} "
                         "with the same environment.")
                self.desk.run_ended(run, "ignored")
                continue
            self.try_start(run, request, held, first)
            if run in self.pending:
                waiting[twin_of(request)] = run
                wait = self.reasons.get(run)
                if first is None and request["kind"] in memory.PRIORITY_KINDS and wait and wait.gate == "memory":
                    first = run

    def start_order(self):
        """
        The waiting requests in the order they try to start: the fixes the merge queue waits on first, then the
        follow-ups, as follows_up decides, and the requests for in-progress issues, such as the resume of a run that
        timed out, then the rest, each oldest first, except that the fresh starts of each project's queued and waiting
        issues trade places among themselves to follow the project's start order, so a free slot goes to the first
        issue in it. Every other request keeps its place by request time. Work the person reviewed or an agent started
        thus finishes before new work starts. A fix or a follow-up stays behind an earlier request that holds its
        target.
        """
        runs = sorted(self.pending)
        places = {}
        for project in {self.pending[run]["project"] for run in runs}:
            places[project] = {one["number"]: place for place, one in enumerate(self.desk.order(project))}

        def place(run):
            request = self.pending[run]
            if not labelled(request["kind"], request["target"], request["env"]):
                return None
            return places[request["project"]].get(int(request["target"]))

        for project in places:
            slots = [index for index, run in enumerate(runs)
                     if self.pending[run]["project"] == project and place(run) is not None]
            for index, run in zip(slots, sorted((runs[index] for index in slots), key=lambda run: (place(run), run))):
                runs[index] = run
        fixes, follow_ups, locks = [], [], set()
        for run in runs:
            request = self.pending[run]
            if lock_of(request) not in locks:
                if request["kind"] in memory.PRIORITY_KINDS:
                    fixes.append(run)
                elif follows_up(request) or self.in_progress(request):
                    follow_ups.append(run)
            locks.add(lock_of(request))
        first = fixes + follow_ups
        return first + [run for run in runs if run not in first]

    def in_progress(self, request):
        """Whether an agent request works an issue in-progress, which holds no place in the start order."""
        target = request["target"]
        if request["kind"] != "agent" or not target.isdigit():
            return False
        try:
            return self.desk.item(request["project"], int(target), "issue")["status"] == "in-progress"
        except store.StoreError:
            return False

    def working_twin(self, request):
        """
        The run that works a fresh start's issue with the same request, asked for since the issue last moved to queued,
        or None. One Agent-ready press asks twice, by its own request and by the wake that its label's event starts, and
        the first may start before the second arrives. A run the issue had before it was queued again, as a failed run
        that lib/release.py queues once more, leaves the new request waiting behind it.
        """
        if not labelled(request["kind"], request["target"], request["env"]):
            return None
        same = [run for run, one in self.running.items() if twin_of(one) == twin_of(request)]
        if not same:
            return None
        queued = self.desk.queued_at(request["project"], int(request["target"]))
        return next((run for run in same if self.running[run]["event"] > queued), None)

    def try_start(self, run, request, held, first=None):
        """Starts a waiting request unless a gate holds it, `held`, the locks of earlier requests that wait with the
        run that waits for each, holds its target, or `first`, a fix that waits for memory, goes ahead of it, and
        records why it waits."""
        lock = lock_of(request)
        if lock in held:
            self.note(run, Wait("lock", f"run {held[lock]} ahead of it",
                                f"run {held[lock]} waits ahead of it for {lock[1]}"))
            return
        checkout = checkout_of(self.desk, request["project"])
        if not checkout:
            self.say(f"Run {run} names {request['project']}, which has no checkout here, so it failed.")
            self.pending.pop(run)
            self.reasons.pop(run, None)
            self.desk.run_ended(run, "failed")
            return
        config = read_config(checkout)
        state = self.mode()
        runner = self.agent_runner(request, config, state)
        wait = self.blocked(request, config, state, runner)
        if wait is None and first is not None and request["kind"] not in memory.PRIORITY_KINDS:
            wait = Wait("memory", f"merge-queue fix run {first} to start",
                        f"merge-queue fix run {first} waits for memory and starts first")
        if wait is None and not self.start(request, checkout, config, state, runner):
            wait = self.reasons.get(run)
        if wait is not None and run in self.pending:
            held[lock] = run
            self.note(run, wait)

    def agent_runner(self, request, config, state):
        """The project's runner for a request that can launch a headless agent."""
        if request["kind"] not in REQUESTABLE:
            return None
        runner = hold.issue_runner_override(state, request["project"]) or config.get("issueRunner", "claude")
        return runner if isinstance(runner, str) and runner else "invalid"

    def held(self, request, runner=None, state=None):
        """
        The Wait the desk's mode, its weekly usage limit, or its API budget keeps a request in, or None: every request
        on Hold, and in Manual every agent, fix, and chore request nobody picked, as picked decides. A branch check
        spends no Claude tokens and takes no agent slot, so Manual lets it start. The Claude usage limit and budget
        also leave Codex runs free to dispatch.
        """
        state = self.mode() if state is None else state
        mode = hold.mode_of(state, request["project"])
        if mode in hold.MODE_WAITS:
            return Wait("mode", *hold.MODE_WAITS[mode])
        if request["kind"] == "check":
            return None
        if mode == "manual" and not picked(request, state):
            return Wait("mode", "Start", "the desk is in Manual and nobody pressed Start on it; Start runs it")
        return None if runner in ("codex", "local") else self.limited(state)

    def limited(self, state):
        """
        The Wait the weekly usage limit or the API budget holds a request that spends Claude tokens in, or None. Says
        once when the limit can't read usage, and so lets runs start.
        """
        reason, problem = self.usage_check(state)
        if problem != self.usage_problem:
            self.usage_problem = problem
            if problem:
                self.say(f"The weekly usage limit can't read usage, so runs start: {problem}.")
        if reason:
            return Wait("usage", USAGE_WAIT, reason)
        spent = self.budget_check(state, self.desk)
        return Wait("usage", spent, spent) if spent else None

    def waits(self, request):
        """
        The Wait an agent request for an issue waits in, or None: the issue on hold, an open issue that blocks a fresh
        start, or another issue that holds one of its serial groups' slots. Moves the issue to waiting the first time
        it waits on another issue.
        """
        target = request["target"]
        if request["kind"] != "agent" or not target.isdigit():
            return None
        project, number = request["project"], int(target)
        try:
            item = self.desk.item(project, number, "issue")
        except store.StoreError:
            return None
        # An issue on hold, as Pause leaves it, starts no run until Resume queues it again.
        if item["status"] == "on-hold":
            return Wait("hold", f"#{number} to resume", f"#{number} is on hold until Resume")
        # Only a fresh start waits on blockers: an @agent request on an issue already worked goes on with its review.
        if request["env"].get("EVENT") == "issues":
            blockers = ", ".join(f"#{one['number']}" for one in item["blockedBy"] if one["state"] == "OPEN")
            if blockers:
                self.park(project, item, BLOCKED_NOTE.format(blockers=blockers))
                return Wait("blocker", f"{blockers} to close", f"it waits for {blockers} to close")
        labels = [label["name"] for label in item["labels"]]
        if serial_slots.groups(labels):
            holder = serial_slots.holder(number, labels, self.slot_holders(project))
            if holder is not None:
                self.park(project, item, SERIAL_NOTE)
                return Wait("serial", f"#{holder}'s serial slot", f"#{holder} holds its serial slot")
        return None

    def blocked(self, request, config, state, runner):
        """The Wait a request can't start yet in, by the desk's mode, the issues it waits on, its slots, and its target,
        or None."""
        wait = self.held(request, runner, state) or self.waits(request)
        if wait:
            return wait
        slots = slots_of(config, hold.slot_override(state, request["project"]))
        same = {run: one for run, one in self.running.items() if one["project"] == request["project"]}
        kind = request["kind"]
        if sum(one["kind"] == kind for one in same.values()) >= slots[kind]:
            return Wait("slot", f"a free {kind} slot", f"its project's {slots[kind]} {kind} slots are busy")
        if request["benchmark"] and sum(one["benchmark"] for one in same.values()) >= slots["benchmark"]:
            return Wait("slot", "a free benchmark slot", f"its project's {slots['benchmark']} benchmark slots are busy")
        gpu = slots["gpu"]
        if gpu is not None and self.on_gpu(request) and sum(map(self.on_gpu, same.values())) >= gpu:
            return Wait("slot", "a free gpu slot", f"its project's {gpu} gpu slots are busy")
        holder = next((run for run, one in same.items() if lock_of(one) == lock_of(request)), None)
        if holder is not None:
            return Wait("lock", f"{same[holder]['kind']} run {holder} to end", f"another run holds {lock_of(request)[1]}")
        return None

    def on_gpu(self, request):
        """Whether a request or a run works an issue labelled `gpu`, by the labels the issue has now, since agents add
        the label while they file or work it."""
        target = request["target"]
        if request["kind"] != "agent" or not target.isdigit():
            return False
        try:
            item = self.desk.item(request["project"], int(target), "issue")
        except store.StoreError:
            return False
        return any(label["name"] == "gpu" for label in item["labels"])

    def note(self, run, wait):
        """Records why a request waits in the store, where the board reads it, and says it, when it moved since the
        last record, as Wait.moved decides."""
        if not wait.moved(self.reasons.get(run)):
            return
        self.reasons[run] = wait
        self.desk.run_waits(run, wait.gate, wait.reason)
        self.say(f"Run {run} waits: {wait.said}.")

    def start(self, request, checkout, config, state, runner):
        """Starts a request's unit once the memory is spare, and returns whether it started."""
        run = request["id"]
        labels = ["benchmark"] if request["benchmark"] else []
        with self.gate.locked():
            ready, gb, reason = self.gate.spare(request["project"], memory_kind(request), labels,
                                                self.clock() - request["at"], str(Path(checkout) / ".agent-desk.json"))
            if not ready:
                self.note(run, Wait("memory", getattr(reason, "short", reason), reason, getattr(reason, "free", None),
                                    getattr(reason, "needed", None)))
                return False
            unit = unit_name(request)
            self.pending.pop(run)
            self.reasons.pop(run, None)
            minutes = limit_of(request, config)
            # Claude runs use the billing choice in effect when they start.
            billed = runner if runner in ("codex", "local") else account.current(state)
            if not self.desk.run_started(run, unit, minutes, billed):
                return False
            done = self.systemd(self.unit_args(request, unit, checkout, config, minutes, billed, runner))
            if done.returncode != 0:
                self.say(f"Run {run} failed to start: {(done.stderr or done.stdout).strip()}")
                self.desk.run_ended(run, "failed")
                return False
            self.running[run] = {**request, "unit": unit}
            pid = self.main_pid(unit)
            if pid:
                self.gate.claim(gb, pid)
        self.say(f"Started run {run} in {unit}: agent-desk {' '.join(command_of(request))} in {request['project']}.")
        return True

    def main_pid(self, unit):
        done = self.systemd(["systemctl", "--user", "show", "-p", "MainPID", "--value", unit])
        text = done.stdout.strip() if done.returncode == 0 else ""
        return int(text) if text.isdigit() and int(text) > 0 else None

    def unit_args(self, request, unit, checkout, config, minutes, billed="subscription", runner=None):
        """The systemd-run command that starts a run under its limit of `minutes`, with its priority, memory ceiling,
        environment, exit handler, and temp folder. The environment names the account the run bills, `billed`, and
        never holds the API key, which `systemctl show` would print."""
        kind = request["kind"]
        high = memory.default_gb(MEMORY_HIGH[kind])
        handler = " ".join(quoted(part) for part in (sys.executable, "-B", self.desk_lib / "dispatch.py", "stopped"))
        env = {name: os.environ[name] for name in CARRIED_ENV if os.environ.get(name)}
        env.update(run_env(config))
        env.update({name: str(value) for name, value in request["env"].items() if name in REQUEST_ENV})
        env.update({"AGENT_DESK_RUN": str(request["id"]), "AGENT_DESK_UNIT": unit, "GH_REPO": request["project"],
                    "AGENT_DESK_ACCOUNT": billed})
        if runner is not None:
            env["AGENT_DESK_ISSUE_RUNNER"] = runner
        return unit_command(unit, f"agent-desk {kind} run {request['target']} in {request['project']}", checkout,
                            minutes, high, handler, env,
                            [self.desk_lib.parent / "bin" / "agent-desk", *command_of(request)],
                            tmp_folder("run", request["id"]))

    def serve(self):
        self.rebuild()
        while True:
            try:
                self.tick()
            except store.StoreError as problem:
                self.say(f"agent-desk: {problem}")
            time.sleep(WAKE_SECONDS)


# The command line.

def parser():
    top = argparse.ArgumentParser(prog="agent-desk", description="Start runs as systemd units.")
    verbs = top.add_subparsers(dest="verb", required=True)
    verbs.add_parser("serve")
    verbs.add_parser("stopped")
    verbs.add_parser("runs").add_argument("--repo", "-R")
    verbs.add_parser("chore-runs").add_argument("--repo", "-R")
    verbs.add_parser("again").add_argument("run", type=int)
    verbs.add_parser("cancel").add_argument("run", type=int)
    for name in ("unqueue", "pause", "unpause", "stop-command"):
        verb = verbs.add_parser(name)
        verb.add_argument("number", type=int)
        verb.add_argument("--repo", "-R")
    stop = verbs.add_parser("stop")
    stop.add_argument("kind", choices=REQUESTABLE)
    stop.add_argument("target")
    stop.add_argument("--dry-run", action="store_true")
    stop.add_argument("--repo", "-R")
    request = verbs.add_parser("request")
    request.add_argument("kind", choices=KINDS)
    request.add_argument("target")
    request.add_argument("--benchmark", action="store_true")
    request.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    request.add_argument("--repo", "-R")
    return top


def request_main(args, desk):
    if args.kind not in REQUESTABLE:
        raise store.StoreError(f"{args.kind} runs take no requests yet; the kinds are {', '.join(REQUESTABLE)}.")
    if not TARGETS[args.kind].fullmatch(args.target):
        forms = {"agent": "an issue number, pr-<number>, or idea-<number>", "fix": "a PR number",
                 "chore": "a skill's name"}
        raise store.StoreError(f"A {args.kind} run's target is {forms[args.kind]}, not {args.target}.")
    if args.benchmark and args.kind != "agent":
        raise store.StoreError("Only an agent run takes --benchmark.")
    env = {}
    for pair in args.env:
        name, _, value = pair.partition("=")
        if name not in REQUEST_ENV:
            raise store.StoreError(f"A request passes only {', '.join(REQUEST_ENV)}, not {name}.")
        env[name] = value
    repo = args.repo or store.repo_here()
    return desk.request_run(repo, args.kind, args.target, args.benchmark, env, path=store.checkout_of(repo))


# The verbs that only read, which open the store read-only, as `stop --dry-run` does.
READING_VERBS = ("runs", "chore-runs", "stop-command")


def main(argv):
    args = parser().parse_args(argv)
    try:
        desk = store.Store(read_only=args.verb in READING_VERBS or getattr(args, "dry_run", False))
    except (store.StoreError, sqlite3.Error) as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    try:
        if args.verb == "serve":
            os.environ.pop("AGENT_DESK_RUN", None)
            os.environ["AGENT_DESK_AUTHOR"] = "desk"
            Dispatcher(desk).serve()
        elif args.verb == "stopped":
            environ = dict(os.environ)
            os.environ.pop("AGENT_DESK_RUN", None)
            os.environ["AGENT_DESK_AUTHOR"] = "desk"
            print(f"The run ended {stopped(desk, environ)}.")
        elif args.verb == "again":
            print(desk.request_again(args.run))
        elif args.verb == "cancel":
            print(cancel_run(desk, args.run))
        elif args.verb == "unqueue":
            print(unqueue_issue(desk, args.repo or store.repo_here(), args.number))
        elif args.verb == "pause":
            print(pause_issue(desk, args.repo or store.repo_here(), args.number))
        elif args.verb == "unpause":
            print(unpause_issue(desk, args.repo or store.repo_here(), args.number))
        elif args.verb == "stop-command":
            print(stop_command(desk, args.repo or store.repo_here(), args.number))
        elif args.verb == "runs":
            print(json.dumps(desk.live_runs(args.repo or store.repo_here())))
        elif args.verb == "chore-runs":
            for line in chore_runs(desk, args.repo or store.repo_here()):
                print(line)
        elif args.verb == "stop":
            for unit in stop_runs(desk, args.repo or store.repo_here(), args.kind, args.target, args.dry_run):
                print(unit)
        else:
            print(request_main(args, desk))
    except (store.StoreError, sqlite3.Error, StopError) as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    finally:
        desk.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
