"""Records what an agent-desk run's agent reported: the model and effort it ran with, how long it took, and its review.

    python3 run_log.py record <model> <effort> <stream> [review] [--labels <a,b>] [--start new|resumed|feedback]
    python3 run_log.py import [<path>]

The run is the store's run that AGENT_DESK_RUN names, as the dispatcher sets it in every unit it starts; a script run
by hand has none, and records nothing. The run's length, turns, and cost come from the `result` event that ends its
stream-json. An agent run also records its issue's labels when it started, which pick its model and effort, and why it
started: `new` on a fresh branch, `resumed` taking up a session a time limit cut off or that stopped early, or
`feedback` returning to a branch with a new request. Each result lands in the run's row of the store's `runs` table,
where the web page reads how each chore's speed and merged reviews change with its model and effort, and what each
merged review cost. How the run ended, such as `stopped` for one that stopped early, is lib/run_end.py's to record.

`import` copies the runs in the log agent-desk kept before the store, by default runs.jsonl in its state folder, into
the `runs` table as finished runs, so the page counts them too. A line names its project in `repo`, its chore by the
skill as agent-desk named it in branches then, without a singleshot- prefix, its review in `pr`, its exit status in
`status`, and when it ended in `at`. A chore takes its skill's name now, after any `agent-desk chores rename`. The
store may hold a line's run already, as the dispatcher recorded runs while the log still ran; the line then fills in
what that run's row lacks. An import run again adds nothing.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chore_rename  # noqa: E402
import config  # noqa: E402
import store as stores  # noqa: E402
from store import Store, StoreError  # noqa: E402

# Where agent-desk logged each run, one JSON object a line, before the store's `runs` table took over.
OLD_LOG = stores.STATE / "runs.jsonl"
KINDS = ("agent", "chore", "fix")


def result_of(stream_text):
    """The run's length in minutes, turns, and cost in dollars from its stream-json's last `result` event, or Nones."""
    found = {}
    for line in stream_text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            found = event
    minutes = found.get("duration_ms")
    return {"minutes": round(minutes / 60000, 1) if isinstance(minutes, (int, float)) else None,
            "turns": found.get("num_turns") if isinstance(found.get("num_turns"), int) else None,
            "cost": round(found["total_cost_usd"], 2) if isinstance(found.get("total_cost_usd"), (int, float))
            else None}


STARTS = ("new", "resumed", "feedback")


def options(args):
    """Splits the `--labels` and `--start` options from the positional arguments, as (positional, labels, start)."""
    positional, labels, start = [], None, None
    args = list(args)
    while args:
        arg = args.pop(0)
        if arg in ("--labels", "--start") and args:
            value = args.pop(0)
            if arg == "--labels":
                labels = [label for label in value.split(",") if label]
            elif value in STARTS:
                start = value
        else:
            positional.append(arg)
    return positional, labels, start


def checkout_of(desk, project):
    """A project's main checkout, as the store or agent-desk install recorded it, or None."""
    if desk.project_path(project):
        return desk.project_path(project)
    try:
        lines = stores.PROJECTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return next((parts[1] for parts in (line.split(None, 1) for line in lines)
                 if len(parts) == 2 and parts[0] == project), None)


def chore_skill(name, checkout, renames=()):
    """
    The chore's skill for the name the old log gave it. The log dropped a singleshot- prefix, which agent-desk no
    longer does, so the name matches a skill of the project's .agent-desk.json, or one `agent-desk chores rename`
    renamed since, with that prefix or without. A renamed skill then follows `renames`, (old, new) pairs in order, to
    its name now. The name itself when no skill matches.
    """
    chores = config.read(checkout).get("chores") if checkout else None
    listed = []
    if isinstance(chores, dict):
        listed = [*(chores.get("every") or {}), *(chores.get("paused") or [])]
    listed += [old for old, _ in renames]
    skill = next((one for one in listed if isinstance(one, str) and one in (name, f"singleshot-{name}")), name)
    newer, seen = dict(renames), set()
    while skill in newer and skill not in seen:
        seen.add(skill)
        skill = newer[skill]
    return skill


def old_run(line):
    """
    An old log line as the run `Store.import_run` writes, or None for a line it can't read: the project, kind, target,
    when it ended and started, its status and exit code, and its result. The line's `at` is when the run ended, and
    its minutes, when it reported them, set when it started.
    """
    try:
        run = json.loads(line)
        ended = datetime.strptime(run["at"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError, KeyError):
        return None
    if not (isinstance(run, dict) and isinstance(run.get("repo"), str) and run.get("kind") in KINDS
            and isinstance(run.get("name"), str) and run["name"]):
        return None

    def number(key):
        value = run.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def text(key):
        return run.get(key) if isinstance(run.get(key), str) and run.get(key) else None

    minutes, status, labels = number("minutes"), run.get("status"), run.get("labels")
    exit_code = status if isinstance(status, int) and not isinstance(status, bool) else None
    return {"project": run["repo"], "kind": run["kind"], "target": run["name"], "ended": stores.store_time(ended),
            "started": stores.store_time(ended - round(minutes * 60) if minutes is not None else ended),
            "status": "succeeded" if exit_code == 0 else "failed", "exit_code": exit_code,
            "result": {"model": text("model"), "effort": text("effort"), "minutes": minutes,
                       "turns": run["turns"] if isinstance(run.get("turns"), int) else None, "cost": number("cost"),
                       "review": run["pr"] if isinstance(run.get("pr"), int) else None,
                       "labels": [label for label in labels if isinstance(label, str)]
                       if isinstance(labels, list) else None,
                       "start": run.get("start") if run.get("start") in STARTS else None}}


def import_log(desk, path):
    """
    Copies the old log's runs into the store in one transaction, with one `imported` event for each project whose
    runs it added or filled in, and returns how many lines it added, filled in, held already, and couldn't read.
    """
    counts = {"added": 0, "filled": 0, "held": 0, "unread": 0}
    try:
        lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return counts
    by_project, skills, taken = {}, {}, set()
    with desk.write():
        for line in lines:
            run = old_run(line)
            if run is None:
                counts["unread"] += 1
                continue
            project = run["project"]
            if run["kind"] == "chore":
                if project not in skills:
                    skills[project] = checkout_of(desk, project), chore_rename.renames(desk, project)
                run["target"] = chore_skill(run["target"], *skills[project])
            done = desk.import_run(project, run["kind"], run["target"], run["ended"], run["started"], run["status"],
                                   run["exit_code"], run["result"], taken)
            counts[done or "held"] += 1
            if done:
                project_counts = by_project.setdefault(project, {"added": 0, "filled": 0})
                project_counts[done] += 1
        for project, project_counts in sorted(by_project.items()):
            desk.import_event(project, "runs.jsonl", project_counts)
    return counts


def main(argv, environ=os.environ, store=None):
    if len(argv) in (2, 3) and argv[1] == "import":
        path = Path(argv[2]) if len(argv) == 3 else OLD_LOG
        if not path.is_file():
            print(f"There is no old run log at {path}.")
            return 0
        store = store or Store()
        try:
            counts = import_log(store, path)
        except StoreError as error:
            print(f"agent-desk: {error}", file=sys.stderr)
            return 1
        print(f"Imported {path}: added {counts['added']} runs, filled in {counts['filled']} the store held, and "
              f"skipped {counts['held']} it held already and {counts['unread']} lines it couldn't read.")
        return 0
    args, labels, start = options(argv[2:])
    if len(argv) < 2 or argv[1] != "record" or len(args) not in (3, 4):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    run = environ.get("AGENT_DESK_RUN", "")
    if not run.isdigit():
        return 0
    model, effort, stream = args[:3]
    review = args[3] if len(args) == 4 else ""
    try:
        text = Path(stream).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    store = store or Store()
    try:
        store.run_result(int(run), model=model or None, effort=effort or None, **result_of(text),
                         review=int(review) if review.isdigit() else None, labels=labels, start=start)
    except StoreError as error:
        print(f"agent-desk: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
