"""Says which new agent-desk runs the dispatcher starts on this machine:

    python3 hold.py set running | manual | held  Auto, Manual, or holds new runs, for every project
    python3 hold.py set running | manual | held <owner/repo>
                                                 Auto, Manual, or Hold for one project, leaving the others as they are
    python3 hold.py resume                       goes back to Auto or Manual, as the desk was before Hold
    python3 hold.py status                       prints {"state", "since", "before", "picked", "pickedRuns",
                                                 "projects", "usageLimit"} as JSON
    python3 hold.py running                      prints the runs working in the dispatcher's units as JSON
    python3 hold.py start <n> [<owner/repo>]     picks issue n for Manual; on Hold it picks for Resume into Manual
    python3 hold.py start-runs <run>...          picks requests the dispatcher holds, by their ids in the store, for
                                                 Manual; on Hold, for Resume into Manual
    python3 hold.py spend <n>                    drops issue n's pick once its run claims it
    python3 hold.py unpick <n> [<owner/repo>]    drops issue n's pick and those of its agent requests that wait, so
                                                 its run waits for Start again; exits 1 when the issue has no pick
    python3 hold.py unpick --all [<owner/repo>]  drops every pick, or one project's, and prints how many
    python3 hold.py held [fix]                   exits 0 unless GH_REPO's project runs, so the chore timer starts
                                                 nothing, and without fix while lib/usage.py's weekly usage limit
                                                 holds too; with fix, Manual counts as running, so lib/fixes.py asks
                                                 for the merge queue's fixes, which wait in Queued for Start

Auto starts every run. Manual starts an agent, fix, or chore run only once you press Start on its row in Queued, however
it got there: an agent-ready label, Retry, Continue, a comment sent to the agent, Fix, a chore's Start Now, and the merge
queue's fixes each ask for a run that waits for Start, and the chore timer starts nothing. Start on an issue's row
picks the issue, which a fresh start of it needs, and every request its row shows, by id in `picked_runs`. Unpick
drops an issue's pick and those of its agent requests that still wait. A project that goes to Auto, by Auto or by a
Resume into Auto, drops its picks, so a Start pressed before can't start the issue the next time the project is in
Manual; Hold and Manual keep them, so Start on Hold still carries into Manual on Resume. Held, the runs working go on
and no new ones start, though the dispatcher still starts the branch checks of the runs working, so they can finish.
The dispatcher holds each request until its project's mode lets it start, before the request takes a slot or claims
anything, so in Manual a request nobody picked waits there until Start picks it.

Each project can have a mode of its own, kept in `projects` by repository; one without takes the desk's `state`.
Setting a mode for every project drops those, and Hold for every project keeps them in `before_projects`, so Resume
gives each project back its own. Every change to the state reads it afresh under a lock, so two changes at once never
write back a state the other has changed since.
"""

import fcntl
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk"
HOLD = STATE / "hold.json"
LOCK = STATE / "hold.lock"
STATES = ("running", "manual", "held")
# The states new runs start in, which Hold goes back to on Resume.
MODES = ("running", "manual")
# What a request or check that Hold keeps waiting waits for, as its card says it after "waits for", and why, as the
# dispatcher's journal says it.
MODE_WAITS = {"held": ("Resume", "the desk holds new runs")}


def status():
    try:
        data = json.loads(HOLD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("state") not in STATES:
        # The weekly usage limit, which lib/usage.py reads, outlasts a state that went missing.
        data = {"state": "running", "since": None,
                **({"usageLimit": data["usageLimit"]} if "usageLimit" in data else {})}
    # The issues picked for Manual, as owner/repo#n, and the requests, by their run ids in the store.
    data.setdefault("picked", [])
    data.setdefault("picked_runs", [])
    return data


def mode_of(data, repo):
    """The state a project's new runs meet: its own mode or the desk's."""
    return data.get("projects", {}).get(repo, data["state"])


def save(data):
    STATE.mkdir(parents=True, exist_ok=True)
    HOLD.write_text(json.dumps(data) + "\n", encoding="utf-8")


@contextmanager
def locked():
    """Holds the state's lock, so one change at a time reads it and writes it back."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def update(change):
    """Reads the state afresh under its lock, applies `change` to it, saves it, and returns it."""
    with locked():
        data = status()
        change(data)
        save(data)
    return data


# The workflow each kind of dispatcher run stands in for, which names a working run on the desk.
DESK_WORKFLOWS = {"agent": "agent", "fix": "fix", "chore": "chores"}


def running():
    """
    The runs working in the dispatcher's units, each with its project, run id, kind, and unit. None on a machine whose
    store holds no runs yet.
    """
    import store
    desk = store.Store(read_only=True)
    try:
        return [{"repo": row["project"], "run": row["id"], "workflow": DESK_WORKFLOWS.get(row["kind"], row["kind"]),
                 "runner": row["kind"], "unit": row["unit"], "desk": True} for row in desk.runs(("running",))]
    finally:
        desk.close()


def set_state(state, now=None, repo=None):
    """
    Moves every project to a state, or with `repo` that one project. Auto drops the picks of each project it moves.
    """
    def move(data):
        if repo is not None:
            data.setdefault("projects", {})[repo] = state
        else:
            move_every(data)
        drop_auto(data, requests)

    def move_every(data):
        if data["state"] != state:
            data["since"] = None if state == "running" else (now or time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            if state == "held" and data["state"] in MODES:
                data["before"] = data["state"]
                if data.get("projects"):
                    data["before_projects"] = dict(data["projects"])
        data["state"] = state
        data.pop("projects", None)
        if state in MODES:
            data.pop("before", None)
            data.pop("before_projects", None)

    requests = picked_requests()
    update(move)


def resume():
    """
    Goes back to Auto or Manual, whichever the desk was in before Hold, and each project to its own. A project back in
    Auto drops its picks, and one back in Manual keeps those Start made on Hold.
    """
    def back(data):
        before = data.get("before", "running")
        projects = {**data.get("before_projects", {}), **data.get("projects", {})}
        data.update(state=before, since=None if before == "running" else time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        for key in ("before", "before_projects", "projects"):
            data.pop(key, None)
        if projects:
            data["projects"] = projects
        drop_auto(data, requests)

    requests = picked_requests()
    update(back)


def repo_here():
    """The project the checkout this runs in belongs to, as lib/store.py's repo_here finds it, or "" for none."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import store
    try:
        return store.repo_here()
    except store.StoreError:
        return ""


def pick(issue, repo):
    """
    Picks an issue for Manual, so the dispatcher starts its requests until its run claims the issue. On Hold, Resume
    into Manual starts them.
    """
    key = f"{repo}#{issue}"
    update(lambda data: data["picked"].append(key) if key not in data["picked"] else None)


def waiting_requests(path=None):
    """
    The store's runs that still wait, each with its id, project, kind, and target, or None when the store can't be
    read.
    """
    import store
    try:
        desk = store.Store(path, read_only=True)
    except (store.StoreError, sqlite3.Error):
        return None
    try:
        return [{key: row[key] for key in ("id", "project", "kind", "target")} for row in desk.runs(("requested",))]
    except sqlite3.Error:
        return None
    finally:
        desk.close()


def picked_requests():
    """
    The store's runs that wait, as waiting_requests reads them, while Start has picked any request, and none
    otherwise, so a change to the picks reads the store only when a request pick could drop.
    """
    return waiting_requests() if status()["picked_runs"] else []


def waiting_runs(path=None):
    """
    The ids of the store's runs that still wait, or None when the store can't be read, so a pick of a request that
    started or ended can drop.
    """
    requests = waiting_requests(path)
    return None if requests is None else {one["id"] for one in requests}


def start_runs(runs, waiting=None):
    """
    Picks requests the dispatcher holds, by their run ids, so in Manual it starts each, and on Hold, Resume into Manual
    does. Drops the picks of runs that no longer wait, so the list keeps only live picks.
    Prints how many it picked.
    """
    live = waiting_runs() if waiting is None else waiting

    def pick_runs(data):
        kept = [one for one in data["picked_runs"] if live is None or one in live]
        data["picked_runs"] = kept + [one for one in dict.fromkeys(runs) if one not in kept]
    update(pick_runs)
    print(json.dumps({"picked": len(runs)}))


def spend(issue, repo):
    key = f"{repo}#{issue}"
    update(lambda data: data.update(picked=[one for one in data["picked"] if one != key]))


def drop_picks(data, drops, requests):
    """
    Drops each issue pick that `drops` takes, given the pick's project and issue number, and each pick of a request
    among `requests`, the store's runs that wait, that it takes, given the request's project and the issue an agent
    request is for, or else None. Keeps the request picks when `requests` is None, as when the store can't be read.
    Returns how many issue picks it dropped.
    """
    def issue_drops(key):
        repo, _, number = key.rpartition("#")
        return number.isdigit() and drops(repo, int(number))

    def run_drops(run):
        request = waiting.get(run)
        if request is None:
            return False
        target = request["target"]
        return drops(request["project"], int(target) if request["kind"] == "agent" and target.isdigit() else None)

    kept = [key for key in data["picked"] if not issue_drops(key)]
    dropped = len(data["picked"]) - len(kept)
    data["picked"] = kept
    if requests is not None:
        waiting = {one["id"]: one for one in requests}
        data["picked_runs"] = [run for run in data["picked_runs"] if not run_drops(run)]
    return dropped


def drop_auto(data, requests):
    """Drops the picks of each project the state starts every run in, where a pick means nothing."""
    drop_picks(data, lambda repo, issue: mode_of(data, repo) == "running", requests)


def unpick(issue, repo, requests=None):
    """
    Drops an issue's pick and the picks of its agent requests that wait, so in Manual its run waits for Start again.
    False, changing nothing, when the issue has no pick, as after its run claimed it.
    """
    key = f"{repo}#{issue}"
    found = []
    requests = picked_requests() if requests is None else requests

    def drop(data):
        found.append(key in data["picked"])
        if found[0]:
            drop_picks(data, lambda one, number: one == repo and number == issue, requests)
    update(drop)
    return found[0]


def unpick_all(repo=None, requests=None):
    """
    Drops every pick, or with `repo` that project's, of issues and of requests that wait. Prints how many issue picks
    it dropped.
    """
    dropped = []
    requests = picked_requests() if requests is None else requests
    update(lambda data: dropped.append(drop_picks(data, lambda one, issue: repo is None or one == repo, requests)))
    print(json.dumps({"dropped": dropped[0]}))


def main(argv):
    if len(argv) == 3 and argv[1] == "set" and argv[2] in STATES:
        set_state(argv[2])
    elif (len(argv) == 4 and argv[1] == "set" and argv[2] in STATES
          and re.fullmatch(r"[\w.-]+/[\w.-]+", argv[3])):
        set_state(argv[2], repo=argv[3])
    elif len(argv) == 2 and argv[1] == "resume":
        resume()
    elif len(argv) == 2 and argv[1] == "status":
        current = status()
        print(json.dumps({**{key: current.get(key) for key in ("state", "since", "before", "picked")},
                          "pickedRuns": current["picked_runs"], "projects": current.get("projects", {}),
                          "usageLimit": current.get("usageLimit")}))
    elif len(argv) == 2 and argv[1] == "running":
        print(json.dumps(running()))
    elif len(argv) in (3, 4) and argv[1] == "start" and argv[2].isdigit():
        repo = argv[3] if len(argv) == 4 else repo_here()
        if not repo:
            print("No project here: run this from a checkout of a project agent-desk install recorded.", file=sys.stderr)
            return 1
        pick(int(argv[2]), repo)
    elif len(argv) >= 3 and argv[1] == "start-runs" and all(one.isdigit() for one in argv[2:]):
        start_runs([int(one) for one in argv[2:]])
    elif len(argv) in (3, 4) and argv[1] == "unpick" and argv[2] == "--all" and (
            len(argv) == 3 or re.fullmatch(r"[\w.-]+/[\w.-]+", argv[3])):
        unpick_all(argv[3] if len(argv) == 4 else None)
    elif len(argv) in (3, 4) and argv[1] == "unpick" and argv[2].isdigit():
        repo = argv[3] if len(argv) == 4 else repo_here()
        if not repo:
            print("No project here: run this from a checkout of a project agent-desk install recorded.", file=sys.stderr)
            return 1
        if not unpick(int(argv[2]), repo):
            print(f"#{argv[2]} in {repo} has no pick: its run claimed it, or Move to Open or Auto dropped it.",
                  file=sys.stderr)
            return 1
    elif len(argv) == 3 and argv[1] == "spend" and argv[2].isdigit():
        repo = os.environ.get("GH_REPO", "")
        if repo:
            spend(int(argv[2]), repo)
    elif len(argv) in (2, 3) and argv[1] == "held" and argv[2:] in ([], ["fix"]):
        current = status()
        repo = os.environ.get("GH_REPO", "")
        state = mode_of(current, repo) if repo else current["state"]
        if state != "running" and not (state == "manual" and argv[2:] == ["fix"]):
            return 0
        if argv[2:] == []:
            import usage
            return 0 if usage.holds(current) else 1
        return 1
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
