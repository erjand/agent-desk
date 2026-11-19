"""
What `agent-desk project rename` and `agent-desk project remove` share: where a project lives in the store, the state
folder, and the projects file, why a change to it can't start now, and the runner that prints each step and stops at
the first failure with the step named. lib/rename.py and lib/remove.py hold the steps themselves.
"""

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assets  # noqa: E402
import backup  # noqa: E402
import checks  # noqa: E402
import chore_ledger  # noqa: E402
import cutover  # noqa: E402
import fixes  # noqa: E402
import hold  # noqa: E402
import merges  # noqa: E402
import pushes  # noqa: E402
import store  # noqa: E402

NAME = re.compile(r"[\w.-]+/[\w.-]+")


class Failed(Exception):
    """A step that failed, with its name and what went wrong."""

    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


def state_paths(repo):
    """Every file and folder in the state folder named for the project, as each module that writes one names it."""
    key = store.project_key(repo)
    found = [chore_ledger.STATE / key, checks.LOGS / key, assets.folder(repo), fixes.plan_path(repo)]
    for path in (fixes.ledger_path(repo), merges.state_path(repo), pushes.state_path(repo)):
        found += [path, path.with_suffix(".lock")]
    return found


def project_tables(db):
    """The tables with a `project` column, read from the schema so a table added later is covered too."""
    names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return [name for name in names if any(column[1] == "project"
                                          for column in db.execute(f"PRAGMA table_info({name})"))]


def rows_of(db, repo):
    """How many rows each table holds for the project, with `projects` for its slug, leaving out empty tables."""
    counts = {"projects": db.execute("SELECT COUNT(*) FROM projects WHERE slug = ?", (repo,)).fetchone()[0]}
    for table in project_tables(db):
        counts[table] = db.execute(f"SELECT COUNT(*) FROM {table} WHERE project = ?", (repo,)).fetchone()[0]
    return {table: count for table, count in counts.items() if count}


def listed():
    """The projects file's lines, each split into its project and its main checkout."""
    try:
        lines = store.PROJECTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.split(None, 1) for line in lines]


def refusals(desk, names, held=None):
    """
    Why a change to a project can't start now, read for each of `names`, the names a rerun may find the project under:
    its mode, unless `held` says whether it counts as on Hold, its working runs and checks, and its merge queue.
    """
    reasons = []
    modes = hold.status()
    if held is None:
        held = any(hold.mode_of(modes, name) == "held" for name in names)
    if not held:
        mode = hold.mode_of(modes, names[0])
        reasons.append(f"{names[0]} is on {cutover.MODE_NAMES.get(mode, mode)}, not Hold")
    working = [run for run in hold.running() if run["repo"] in names]
    if working:
        reasons.append(f"{cutover.plural(len(working), 'run')} still working ("
                       + ", ".join(f"{run['workflow']} {run['run']}" for run in working) + ")")
    checking = [row for name in names for row in desk.checks(("queued", "running"), project=name)]
    if checking:
        reasons.append(f"{cutover.plural(len(checking), 'branch check')} queued or running ("
                       + ", ".join(row["branch"] for row in checking) + ")")
    for name in names:
        queue = merges.load(merges.state_path(name), time.time())
        if queue["queue"] or queue.get("batch"):
            reasons.append("the merge queue holds " + (", ".join(f"#{one['pr']}" for one in queue["queue"])
                                                       or "a batch under test"))
    return reasons


class Steps:
    """A change to one project, made in named steps, which prints each step and what it changed."""

    def __init__(self, desk, dry_run=False, say=print):
        self.desk, self.dry_run, self.say = desk, dry_run, say

    def step(self, name, action):
        self.say(f"- {name}")
        try:
            action()
        except (store.StoreError, sqlite3.Error, backup.BackupError, OSError) as error:
            raise Failed(name, str(error)) from error

    def note(self, text):
        self.say(f"    {text}")

    def change(self, doing, done, call):
        """Makes one change, or in a dry run says it would."""
        if self.dry_run:
            self.note(f"Would {doing}.")
            return
        call()
        self.note(f"{done}.")

    def refuse(self, reasons, clear, ready="On Hold, with no run or check working and the merge queue empty."):
        """
        Notes `ready` when nothing holds the change back, or in a dry run why it would refuse, or else stops it with
        the reasons and `clear`, the commands that clear them.
        """
        if not reasons:
            self.note(ready)
        elif self.dry_run:
            for reason in reasons:
                self.note(f"Would refuse now: {reason}.")
        else:
            raise Failed("check", "; ".join(reasons) + f". {clear}")


def main(argv, usage, prog, noun, names, act):
    """
    Parses `[--dry-run]` and the project names, opens the store, and runs `act(desk, dry_run, *names)`. Returns the
    exit status: 2 for bad arguments, 1 when the store won't open or a step fails. `noun` names the change in
    the failure's message, such as `rename`.
    """
    parser = argparse.ArgumentParser(prog=prog, usage=usage)
    parser.add_argument("--dry-run", action="store_true")
    for name in names:
        parser.add_argument(name)
    args = parser.parse_args(argv)
    values = [getattr(args, name) for name in names]
    if not all(NAME.fullmatch(value) for value in values) or len(set(values)) < len(values):
        print(f"usage: {usage}", file=sys.stderr)
        return 2
    try:
        desk = cutover.open_store(args.dry_run)
    except (store.StoreError, sqlite3.Error) as error:
        print(f"agent-desk: {error}", file=sys.stderr)
        return 1
    try:
        act(desk, args.dry_run, *values)
    except Failed as failure:
        print(f"agent-desk: the {noun} of {values[0]} stopped at {failure.step}: {failure.message}",
              file=sys.stderr)
        return 1
    finally:
        desk.close()
    return 0
