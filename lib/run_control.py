"""The run verbs of `python3 dispatch.py`: `stop` ends a target's runs, `cancel` ends one waiting run, `unqueue` sends
a queued issue back to open, `pause` stops the agent run on an issue and holds the issue, `unpause` queues a held issue
again at the top, and `chore-runs` lists each chore's newest run. `cancel_waiting` ends an issue's waiting agent
requests, as `agent-desk issue close` does."""

import calendar
import time

import chore_rename
import hold
import store
import store_delete
from run_units import systemd


def stop_runs(desk, project, kind, target, dry=False, run=systemd):
    """
    Ends the runs on a target: stops the unit of each one working, whose exit handler records it as cancelled, and
    drops each request still waiting. An issue that waited for its dropped request goes back as unpark says, so the
    dispatcher's wake never asks for it again. Returns the units it stopped, or
    with `dry` the units it would stop.
    """
    units, dropped = [], False
    for row in desk.runs(("requested", "running")):
        if (row["project"], row["kind"], row["target"]) != (project, kind, target):
            continue
        if row["status"] == "running" and row["unit"]:
            units.append(row["unit"])
            if not dry:
                run(["systemctl", "--user", "stop", "--no-block", row["unit"]])
        elif not dry:
            dropped |= desk.run_ended(row["id"], "cancelled")
    if dropped:
        unpark(desk, project, kind, target)
    return units


def unpark(desk, project, kind, target):
    """
    Puts an issue that waited for an agent request just dropped back to the state it had before it waited, or to open
    when that was queued or unknown, so the dispatcher's wake never asks for it again.
    """
    if kind != "agent" or not target.isdigit():
        return
    try:
        if desk.item(project, int(target), "issue")["status"] == "waiting":
            before = desk.state_before(project, int(target), "waiting")
            desk.set_state(project, int(target), "open" if before in (None, "queued") else before)
    except store.StoreError:
        pass


def cancel_run(desk, run):
    """
    Ends one run that still waits as cancelled, leaving every other run alone, and returns what it did. Its issue goes
    back as unpark says, unless another request of its target waits or works. Raises StoreError for a run that works
    or has ended.
    """
    row = desk.run(run)
    if not desk.run_ended(run, "cancelled", waiting=True):
        row = desk.run(run)
        if row["status"] == "running":
            raise store.StoreError(f"Run {run} works now in {row['unit']}, and only a run that waits cancels; "
                                   f"`python3 lib/dispatch.py stop {row['kind']} {row['target']}` stops it.")
        raise store.StoreError(f"Run {run} ended already as {row['status']}, so there is nothing to cancel.")
    same = (row["project"], row["kind"], row["target"])
    if not any((one["project"], one["kind"], one["target"]) == same for one in desk.runs(("requested", "running"))):
        unpark(desk, *same)
    return f"Cancelled run {run}, which waited to work {row['kind']} {row['target']} in {row['project']}."


def cancel_waiting(desk, project, number):
    """
    Ends each agent request that still waits for issue `number`, a fresh start, a reply, or an idea's discussion, as
    cancelled, and leaves a run that already works the issue alone. Returns how many it ended. A caller that changes
    the issue's state runs it inside the same write, so the dispatcher's wake never starts a request left behind.
    """
    with desk.write():
        waiting = [row["id"] for row in desk.runs(("requested",))
                   if row["project"] == project and store_delete.agent_target(row, number)]
        for run in waiting:
            desk.run_ended(run, "cancelled", waiting=True)
    return len(waiting)


PAUSE_NOTE = ("Paused: agent-desk stopped the agent run and put this issue on hold, so no run starts it until Resume. "
              "Its worktree, commits, and session stay, and Resume queues it at the top of the start order, where its "
              "next run takes up the session.")


def pause_issue(desk, project, number, run=systemd):
    """
    Pauses the agent run on an issue: in one write it moves the issue to on-hold, ends each agent request that waits
    for it, and notes the pause, then stops the unit of each agent run that works it. The run's exit handler finds the
    issue on hold and leaves it there, and posts that the run was cancelled, so the next run takes up its session.
    Raises StoreError, changing nothing, when no agent run works the issue. Returns what it did.
    """
    with desk.write():
        working = [row for row in desk.runs(("running",))
                   if row["project"] == project and row["kind"] == "agent" and row["target"] == str(number)]
        if not working:
            raise store.StoreError(f"No agent run works #{number} in {project}, so there is nothing to pause.")
        cancel_waiting(desk, project, number)
        desk.set_state(project, number, "on-hold")
        desk.comment(project, number, PAUSE_NOTE)
    for row in working:
        run(["systemctl", "--user", "stop", "--no-block", row["unit"]])
    return f"Paused #{number}: stopped {', '.join(row['unit'] for row in working)} and put it on hold."


def unpause_issue(desk, project, number):
    """
    Queues an issue on hold again at the top of its project's start order, which the dispatcher's wake asks a run for,
    as Resume after Pause does. Raises StoreError, changing nothing, for an issue that isn't on hold. Returns what it
    did.
    """
    with desk.write():
        status = desk.item(project, number, "issue")["status"]
        if status != "on-hold":
            raise store.StoreError(f"#{number} is {status}, not on hold, so there is nothing to resume.")
        desk.set_state(project, number, "queued")
        desk.move(project, number, "top")
    return f"Queued #{number} at the top of the start order."


def unqueue_issue(desk, project, number, spend=None):
    """
    Sends a queued or waiting issue back to open, as the board's Move to Open does in Queued: in one write it ends each
    agent request that waits for the issue as cancelled and sets the issue to open, so the dispatcher's wake never asks
    for it again, and then drops its Manual pick, so its next Agent-Ready waits for Start. Raises StoreError, changing
    nothing, when an agent run works the issue or the issue is in neither state. Returns what it did.
    """
    with desk.write():
        item = desk.item(project, number, "issue")
        mine = [row for row in desk.runs(("requested", "running"))
                if (row["project"], row["kind"], row["target"]) == (project, "agent", str(number))]
        working = next((row for row in mine if row["status"] == "running"), None)
        if working:
            raise store.StoreError(f"Couldn't move #{number} to Open: its run started in {working['unit']}.")
        if item["status"] not in ("queued", "waiting"):
            raise store.StoreError(f"Couldn't move #{number} to Open: it is {item['status']}, not queued or waiting.")
        count = cancel_waiting(desk, project, number)
        desk.set_state(project, number, "open")
    (spend or hold.spend)(number, project)
    cancelled = f", cancelling {count} waiting run{'s' if count != 1 else ''}" if count else ""
    return f"Moved #{number} to Open{cancelled}."


def chore_runs(desk, project):
    """
    Each chore's newest run as lib/chores.sh reads it, one line per chore: the skill, when the run started as epoch
    seconds, and its status. The run working wins over a newer request that waits behind it, and a run that hasn't
    started counts from its request. A chore `agent-desk chores rename` renamed, and that has no runs of its own since,
    shows its new name's newest run too, so the schedule counts from it while the config still lists the old name.
    """
    newest = {}
    for row in desk.chore_runs(project):
        if newest.get(row["target"], {}).get("status") != "running":
            newest[row["target"]] = row
    for old, new in reversed(chore_rename.renames(desk, project)):
        if old not in newest and new in newest:
            newest[old] = newest[new]
    lines = []
    for skill, row in newest.items():
        at = calendar.timegm(time.strptime(row["started_at"] or row["requested_at"], "%Y-%m-%dT%H:%M:%SZ"))
        lines.append(f"{skill}\t{at}\t{row['status']}")
    return lines
