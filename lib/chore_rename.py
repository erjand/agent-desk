"""Renames a chore in a project: its runs in the store, its ledger, and its name in the project's .agent-desk.json.

    python3 chore_rename.py <old> <new> [-R <owner/repo> | --all]

`agent-desk chores rename` runs it, such as after a chore's skill took a new name. In each project it refuses when
<new> already has runs or a config entry, or when <old> has neither, and says which. Otherwise it takes these steps in
order, printing each, and stops the project at the first failure:

1. config: when the main checkout's config names <old> in `chores.every`, `chores.paused`, or `models.chores`, it
   renames each through the schedule review the web page's schedule controls use, on `agent/chore-schedule`, with
   `node web/chore-rename.ts`, and prints the review's number. An open schedule review gains a commit. The main
   checkout keeps its config until the review merges.
2. runs: it rewrites the target of the project's chore runs of <old> in the store's `runs` table to <new> and writes a
   `chore-renamed` event naming both, in one transaction.
3. ledger: it moves the chore's ledger, `chores/<owner>__<repo>/<old>.jsonl` in the state folder, to `<new>.jsonl`,
   which lib/chore_ledger.py reads for the chore's brief.

Until the review merges, the main checkout's config lists <old>, whose runs now carry the new name. `renames` gives
the store's `chore-renamed` events, which `python3 dispatch.py chore-runs` follows to report <new>'s newest run for
<old> as well, so the schedule never takes <old> for a chore that never ran and starts it at once.

Without -R it renames in the project of the current checkout. --all renames in every project in
~/.config/agent-desk/projects whose config names <old>, and skips the rest with a line saying so. It exits 1 when any
project refused or failed.
"""

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chore_ledger  # noqa: E402
import config  # noqa: E402
import store  # noqa: E402

# A chore's name, as lib/dispatch.py takes one in a request.
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
REVIEW_SCRIPT = Path(__file__).resolve().parents[1] / "web" / "chore-rename.ts"


class Stopped(Exception):
    """Why a project's rename stopped: a refusal before any step, or the review step's failure."""


def renames(desk, project):
    """The project's chore renames in the order they happened, as (old, new) pairs from its `chore-renamed` events."""
    rows = desk.db.execute("SELECT json_extract(payload, '$.from'), json_extract(payload, '$.to') FROM events"
                           " WHERE project = ? AND type = 'chore-renamed' ORDER BY id", (project,)).fetchall()
    return [(old, new) for old, new in rows if isinstance(old, str) and isinstance(new, str)]


def named(settings, skill):
    """Where a config names a chore: `chores.every`, `chores.paused`, or `models.chores`, each it names it in."""
    chores, models = settings.get("chores"), settings.get("models")
    chores = chores if isinstance(chores, dict) else {}
    every, paused = chores.get("every"), chores.get("paused")
    by_model = models.get("chores") if isinstance(models, dict) else None
    places = (("chores.every", isinstance(every, dict) and skill in every),
              ("chores.paused", isinstance(paused, list) and skill in paused),
              ("models.chores", isinstance(by_model, dict) and skill in by_model))
    return [place for place, found in places if found]


def run_count(desk, project, skill):
    return desk.db.execute("SELECT COUNT(*) FROM runs WHERE project = ? AND kind = 'chore' AND target = ?",
                           (project, skill)).fetchone()[0]


def plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def check(desk, project, settings, old, new):
    """Where the config names <old>, or Stopped saying what refuses the rename."""
    new_runs, new_places = run_count(desk, project, new), named(settings, new)
    if new_runs or new_places:
        held = [plural(new_runs, "run")] if new_runs else []
        held += [f"a config entry in {', '.join(new_places)}"] if new_places else []
        raise Stopped(f"refused, since {new} already has {' and '.join(held)}; nothing changed.")
    old_places = named(settings, old)
    if not (old_places or run_count(desk, project, old)):
        raise Stopped(f"refused, since {old} has no runs and no config entry; nothing changed.")
    return old_places


def node_review(project, checkout, old, new):
    """Renames the chore in the config through the schedule review, and returns the review's number."""
    node = shutil.which("node")
    if not node:
        raise Stopped("the config's review needs Node 24 or newer on PATH; nothing changed.")
    done = subprocess.run([node, str(REVIEW_SCRIPT), project, checkout, old, new], cwd=checkout,
                          capture_output=True, text=True)
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise Stopped(f"the config's review failed, so nothing else changed: {said[-1] if said else 'no output'}")
    return int(done.stdout.strip().splitlines()[-1])


def move_runs(desk, project, old, new):
    """Gives the project's chore runs of <old> the name <new>, with a `chore-renamed` event, in one transaction."""
    with desk.write():
        moved = desk.db.execute("UPDATE runs SET target = ? WHERE project = ? AND kind = 'chore' AND target = ?",
                                (new, project, old)).rowcount
        desk._event(project, None, "chore-renamed", **{"from": old, "to": new, "runs": moved})
    return moved


def move_ledger(project, old, new, ledgers):
    """Moves the chore's ledger to its new name, and says what it did."""
    folder = ledgers / store.project_key(project)
    source, target = folder / f"{old}.jsonl", folder / f"{new}.jsonl"
    if not source.exists():
        return f"{old} has no ledger to move."
    if target.exists():
        return f"{target} exists already, so {source} stays."
    source.rename(target)
    return f"moved {source.name} to {target.name}."


def rename(desk, project, checkout, old, new, review, ledgers, say):
    """Renames the chore in one project, printing each step through `say`; raises Stopped when it can't."""
    places = check(desk, project, config.read(checkout) if checkout else {}, old, new)
    if places:
        say(f"- config: opened or updated review #{review(project, checkout, old, new)}, which renames {old} to {new} "
            f"in {', '.join(places)}.")
    else:
        say(f"- config: names no {old}, so no review.")
    say(f"- runs: moved {plural(move_runs(desk, project, old, new), 'run')} of {old} to {new}.")
    say(f"- ledger: {move_ledger(project, old, new, ledgers)}")


def projects(path):
    """Each registered project and its main checkout, from the projects file."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [tuple(parts) for parts in (line.split(None, 1) for line in lines) if len(parts) == 2]


def parser():
    top = argparse.ArgumentParser(prog="agent-desk chores rename",
                                  description="Rename a chore's runs, ledger, and config entries in a project.")
    top.add_argument("old")
    top.add_argument("new")
    where = top.add_mutually_exclusive_group()
    where.add_argument("--repo", "-R", help="the project, owner/repo; the current checkout's by default")
    where.add_argument("--all", action="store_true", help="every registered project whose config names <old>")
    return top


def main(argv, desk=None, review=node_review, projects_file=None, ledgers=None, out=print):
    args = parser().parse_args(argv)
    if not NAME.fullmatch(args.new):
        print(f"agent-desk: a chore's name is lowercase letters, digits, and dashes, not {args.new}.", file=sys.stderr)
        return 2
    if args.old == args.new:
        print("agent-desk: the old and new names are the same.", file=sys.stderr)
        return 2
    projects_file = store.PROJECTS if projects_file is None else projects_file
    ledgers = chore_ledger.STATE if ledgers is None else ledgers
    try:
        if args.all:
            targets = []
            for project, checkout in projects(projects_file):
                if named(config.read(checkout), args.old):
                    targets.append((project, checkout))
                else:
                    out(f"{project}: skipped, since its config doesn't name {args.old}.")
        else:
            project = args.repo or store.repo_here()
            targets = [(project, dict(projects(projects_file)).get(project) or store.checkout_of(project))]
        desk = desk or store.Store()
    except (store.StoreError, sqlite3.Error) as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    failed = False
    for project, checkout in targets:
        out(f"{project}: renaming {args.old} to {args.new}")
        try:
            rename(desk, project, checkout, args.old, args.new, review, ledgers, out)
        except Stopped as problem:
            out(f"{project}: {problem}")
            failed = True
        except (store.StoreError, sqlite3.Error, OSError) as problem:
            out(f"{project}: failed: {problem}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
