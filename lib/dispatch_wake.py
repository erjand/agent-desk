"""
The dispatcher's wake and serial slots: the wake that asks for a run of each queued or waiting issue nothing started,
the issues that hold their serial groups' slots, and the move of an issue that waits to `waiting`. `Dispatcher` in
`lib/dispatch.py` inherits `WakeMixin`, whose methods use the dispatcher's `desk`, `mode`, `pending`, `running`,
`say`, and `wake_due`.
"""

import contextlib
import fcntl
from pathlib import Path

import hold
import serial_slots
import store
from run_units import checkout_of

# The states park leaves alone.
PARK_KEEPS = ("waiting", "closed", "on-hold", "needs-design")
# How the dispatcher signs the notes it posts on an issue, as lib/common.sh's desk_mark does.
NOTE_MARK = "\n\n<sub>Posted by agent-desk</sub>"


@contextlib.contextmanager
def serial_lock(checkout):
    """
    Takes the lock lib/serial.sh holds in a project's main checkout without waiting, and yields whether it holds it.
    A checkout with no .git folder has no lock to take.
    """
    try:
        handle = open(Path(checkout) / ".git" / "serial-slot.lock", "a")
    except OSError:
        yield True
        return
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class WakeMixin:
    """The methods of `Dispatcher` that wake queued and waiting issues and read their serial slots."""

    def wake(self):
        """
        Asks for a run of each queued issue that no agent request waits for or works and that no agent run was asked for
        since it last moved to queued, as an agent-ready label added from the command line leaves it. Then asks for a
        run of each waiting issue that no agent request waits for or works, whose blockers have all closed, and whose
        serial groups no issue holds, the first of a group in the project's start order. An issue reaches waiting with
        nothing behind it when `agent-desk serial claim` moves it there, as `agent-desk start` may, or when the
        dispatcher drops a parked request it can't start. The issue moves to queued, so a run that fails to start again
        leaves it there instead of asking each reconcile. Each request carries EVENT=issues, so Manual holds it for
        Start. A project with no checkout here waits until it has one. Each project's pass holds the lock `agent-desk
        serial` holds, so a release and a wake never both ask for one issue; while a release holds it, the next tick
        tries again.
        """
        self.wake_due = False
        asked = [one for one in (*self.pending.values(), *self.running.values())
                 if one["kind"] == "agent" and one["target"].isdigit()]
        for project in self.desk.projects():
            checkout = checkout_of(self.desk, project)
            if checkout:
                with serial_lock(checkout) as locked:
                    if locked:
                        self.wake_project(project, {int(one["target"]) for one in asked if one["project"] == project})
                    else:
                        self.wake_due = True

    def wake_project(self, project, mine):
        """
        Wakes a project's queued and waiting issues, as wake describes, given the issues its agent requests wait for
        or work. A queued issue it asks for holds its serial groups for the waiting ones. In Manual, a request that
        waits for Start holds nothing, and only a picked waiting serial issue starts, so a slot goes to an issue
        someone picked.
        """
        for number in self.desk.unasked_queued(project):
            if number not in mine:
                self.ask_for(project, number)
                self.say(f"Asked for a run of #{number} in {project}, which was queued with no request.")
                mine.add(number)
        waiting = [item for item in self.desk.items(project, "issue", status="waiting", limit=1000)
                   if item["number"] not in mine and all(one["state"] != "OPEN" for one in item["blockedBy"])]
        if not waiting:
            return
        # In Manual, the issues that go ahead: those picked, those whose run works, and those with a request Start
        # picked. The rest of the issues an agent request waits for hold nothing.
        going = None
        state = self.mode()
        if hold.mode_of(state, project) == "manual":
            ours = [(one, run in self.running) for run, one in {**self.pending, **self.running}.items()
                    if one["project"] == project and one["kind"] == "agent" and one["target"].isdigit()]
            going = {int(key[len(project) + 1:]) for key in state["picked"]
                     if key.startswith(f"{project}#") and key[len(project) + 1:].isdigit()}
            going |= {int(one["target"]) for one, works in ours
                      if works or one["id"] in state.get("picked_runs", ())}
        # The issues an agent request waits for count as holding their groups, as those that work do.
        holders = self.desk.items(project, "issue", status="in-progress", limit=1000)
        for number in mine if going is None else mine & going:
            try:
                holders.append({**self.desk.item(project, number, "issue"), "status": "in-progress"})
            except store.StoreError:
                continue
        for number in serial_slots.release(waiting, holders, going):
            self.desk.set_state(project, number, "queued")
            self.ask_for(project, number)
            self.say(f"Asked for a run of #{number} in {project}, which waited with no request.")

    def ask_for(self, project, number):
        """Asks for a fresh start of an issue, as its agent-ready label does, with the benchmark limit its label sets."""
        labels = {label["name"] for label in self.desk.item(project, number, "issue")["labels"]}
        self.desk.request_run(project, "agent", str(number), "benchmark" in labels, {"EVENT": "issues"})

    def slot_holders(self, project):
        """
        The project's issues that hold their serial groups' slots: those in progress, and those a running agent run
        works, which counts from its start, before its run claims the issue.
        """
        holders = self.desk.items(project, "issue", status="in-progress", limit=1000)
        for one in self.running.values():
            if one["project"] == project and one["kind"] == "agent" and one["target"].isdigit():
                try:
                    holders.append({**self.desk.item(project, int(one["target"]), "issue"), "status": "in-progress"})
                except store.StoreError:
                    continue
        return holders

    def park(self, project, item, note):
        """
        Moves an issue to waiting with a note saying why, once. An issue waiting already or closed stays as it is, and
        so does one on-hold or in needs-design, which keeps the state the user gave it.
        """
        if item["status"] in PARK_KEEPS:
            return
        self.desk.set_state(project, item["number"], "waiting")
        self.desk.comment(project, item["number"], note + NOTE_MARK)
        self.say(f"Moved #{item['number']} in {project} to waiting.")
