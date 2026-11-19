"""Takes a project off this machine's desk:

    python3 remove.py [--dry-run] <owner/repo>

`agent-desk project remove` runs it. It refuses to start unless the project is on Hold, with no run working or
waiting, no branch check running, and its merge queue empty. A queued branch check, which Hold keeps from starting,
goes with the project's rows. Then it takes these steps in order, printing each, and stops at the first failure with
the step named:

1. store: deletes every row with the project in a table of desk.sqlite with a `project` column, and its `projects`
   row, and writes a `project-removed` event naming it, in one transaction.
2. files: deletes the project's files in the state folder that carry its `<owner>__<repo>` name, as lib/rename.py
   moves them, and the board files the pane once kept in `boards/` under its main checkout's path.
3. hold: drops the project's mode, slot overrides, and Manual picks from hold.json, and its queued chores from
   chore-repeat.json.
4. projects: drops the project's line from ~/.config/agent-desk/projects.
5. backup: deletes the backup repository's `<owner>/<repo>/` folder with `git rm` and commits it, so the backup's
   history still holds the project.

It leaves the main checkout, its worktrees, its branches, and its remote alone, and prints the worktrees besides the
main checkout, so the user can delete them. A rerun after a failure finishes what is left: each step skips what it
finds done. A name that nothing on the desk holds stops the check. --dry-run opens the store read-only, prints each
step it would take, and changes nothing.
"""

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup  # noqa: E402
import chore_repeat  # noqa: E402
import cutover  # noqa: E402
import hold  # noqa: E402
import project_steps  # noqa: E402
import store  # noqa: E402
from project_steps import Failed, listed, project_tables, rows_of, state_paths  # noqa: E402

USAGE = "agent-desk project remove [--dry-run] <owner/repo>"
# Where the pane kept each checkout's board before it read the local store, which nothing writes any more.
BOARDS = store.STATE / "boards"


def board_paths(checkout):
    """The files the pane kept in `boards/` for a main checkout, named for its path."""
    if not checkout:
        return []
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", checkout).strip("_") or "board"
    return [BOARDS / f"{safe}{suffix}" for suffix in (".json", ".lock", ".claim")]


def chore_queue_path():
    return chore_repeat.STATE / "chore-repeat.json"


def queued_chores():
    """Every project's queued chores, as chore-repeat.json holds them, or nothing when it is missing or unreadable."""
    try:
        queued = json.loads(chore_queue_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return queued if isinstance(queued, dict) else {}


def delete(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


class Remove(project_steps.Steps):
    """One project's removal, which prints each step and what it changed."""

    def __init__(self, desk, repo, dry_run=False, say=print):
        super().__init__(desk, dry_run, say)
        self.repo = repo
        self.checkout = None

    def run(self):
        self.say(f"Removing {self.repo}{' (dry run)' if self.dry_run else ''}")
        self.step("check", self.check)
        self.step("store", self.remove_store)
        self.step("files", self.remove_files)
        self.step("hold", self.remove_modes)
        self.step("projects", self.remove_line)
        self.step("backup", self.remove_backup)
        self.worktrees()
        self.say(f"Removed {self.repo}." if not self.dry_run else "Changed nothing.")

    # The check.

    def line(self):
        """The project's main checkout as the projects file names it, or None without a line for it."""
        return next((line[1] if len(line) == 2 else "" for line in listed() if line and line[0] == self.repo), None)

    def check(self):
        db = self.desk.db
        rows = rows_of(db, self.repo)
        line = self.line()
        stored = db.execute("SELECT path FROM projects WHERE slug = ?", (self.repo,)).fetchone()
        self.checkout = line or (stored[0] if stored else None)
        if not rows and line is None:
            if not self.leftovers():
                raise Failed("check", f"neither the store nor {store.PROJECTS} holds {self.repo}.")
            self.note(f"Finishing an earlier removal: the store and {store.PROJECTS} hold no {self.repo}.")
        modes = hold.status()
        # Once the store and the hold steps have run, the project has no mode of its own and nothing left to start.
        held = hold.mode_of(modes, self.repo) == "held" or (not rows and self.repo not in modes.get("projects", {}))
        reasons = project_steps.refusals(self.desk, (self.repo,), held)
        waiting = [row for row in self.desk.runs(("requested",)) if row["project"] == self.repo]
        if waiting:
            reasons.append(f"{cutover.plural(len(waiting), 'run request')} waiting ("
                           + ", ".join(f"{row['kind']} {row['target']} as run {row['id']}" for row in waiting) + ")")
        cancel = " Cancel each waiting request with `agent-desk request --cancel <run>`." if waiting else ""
        self.refuse(reasons, f"Run `agent-desk hold {self.repo}` first, and wait for the runs, the checks, and the "
                    f"merge queue to finish.{cancel}",
                    ready="On Hold, with no run working or waiting, no check working, and the merge queue empty.")
        note = project_steps.queued(self.desk, (self.repo,), "the removal deletes each with the project's rows")
        if note:
            self.note(note)

    def leftovers(self):
        """Whether anything outside the store and the projects file still holds the project, as after a failed run."""
        return (any(path.exists() for path in state_paths(self.repo))
                or bool(self.dropped_modes(hold.status()))
                or self.repo in queued_chores()
                or (backup.DATA / ".git").exists() and (backup.DATA / self.repo).exists())

    # The steps.

    def remove_store(self):
        counts = rows_of(self.desk.db, self.repo)
        if not counts:
            self.note(f"Done: the store holds no {self.repo}.")
            return
        held = ", ".join(f"{table} {count}" for table, count in sorted(counts.items()))
        self.change(f"delete {self.repo} from the store ({held})", f"Deleted {self.repo} from the store ({held})",
                    self.write_store)

    def write_store(self):
        with self.desk.write():
            db = self.desk.db
            for table in project_tables(db):
                db.execute(f"DELETE FROM {table} WHERE project = ?", (self.repo,))
            db.execute("DELETE FROM projects WHERE slug = ?", (self.repo,))
            # The event names no project, so it outlasts the delete and every reader reads every project again.
            self.desk._event(None, None, "project-removed", repo=self.repo)

    def remove_files(self):
        found = [path for path in state_paths(self.repo) + board_paths(self.checkout) if path.exists()]
        if not found:
            self.note(f"Done: the state folder holds nothing of {self.repo}.")
        for path in found:
            where = path.relative_to(store.STATE)
            self.change(f"delete {where}", f"Deleted {where}", lambda path=path: delete(path))

    def remove_modes(self):
        if self.dry_run:
            self.report(self.dropped_modes(hold.status()), hold.HOLD)
        else:
            with hold.locked():
                data = hold.status()
                dropped = self.dropped_modes(data)
                if dropped:
                    hold.save(data)
            self.report(dropped, hold.HOLD)
        path = chore_queue_path()
        if self.repo not in queued_chores() or self.dry_run:
            self.report(["its queued chores"] if self.repo in queued_chores() else [], path)
            return
        with chore_repeat.queues(path) as queued:
            queued.pop(self.repo, None)
        self.report(["its queued chores"], path)

    def report(self, dropped, path):
        if dropped:
            what = ", ".join(dropped)
            self.note(f"Would drop {what} from {path.name}." if self.dry_run else f"Dropped {what} from {path.name}.")
        else:
            self.note(f"Done: {path.name} holds nothing of {self.repo}.")

    def dropped_modes(self, data):
        """Drops the project from hold.json's data, and returns what it dropped."""
        dropped = []
        for field, named in hold.PROJECT_FIELDS.items():
            modes = data.get(field)
            if isinstance(modes, dict) and self.repo in modes:
                del modes[self.repo]
                dropped.append(named)
        picks = [pick for pick in data.get("picked", []) if pick.rpartition("#")[0] != self.repo]
        if picks != data.get("picked", []):
            data["picked"] = picks
            dropped.append("its picks")
        return dropped

    def remove_line(self):
        lines = listed()
        if not any(line and line[0] == self.repo for line in lines):
            self.note(f"Done: {store.PROJECTS} has no line for {self.repo}.")
            return

        def write():
            text = "".join(" ".join(line) + "\n" for line in lines if not (line and line[0] == self.repo))
            temporary = store.PROJECTS.with_name(f".{store.PROJECTS.name}.remove")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(store.PROJECTS)

        self.change(f"drop {self.repo}'s line from {store.PROJECTS}", f"Dropped {self.repo}'s line from "
                    f"{store.PROJECTS}", write)

    def remove_backup(self):
        folder = backup.DATA
        if not (folder / ".git").exists() or not (folder / self.repo).exists():
            self.note(f"Done: {folder} holds no {self.repo}/ folder.")
            return
        self.change(f"delete {folder / self.repo} with git rm and commit it",
                    f"Deleted {self.repo}/ from {folder} and committed it", self.commit_backup)

    def commit_backup(self):
        folder = backup.DATA
        backup.git(folder, "rm", "-r", "--quiet", "--ignore-unmatch", "--", self.repo)
        # git rm leaves the files git never tracked, which the commit would otherwise skip.
        if (folder / self.repo).exists():
            shutil.rmtree(folder / self.repo)
        backup.commit(folder, f"Remove {self.repo}")

    # What it leaves.

    def worktrees(self):
        """Prints the main checkout's other worktrees, which hold the user's code and stay."""
        if not self.checkout or not Path(self.checkout).is_dir():
            return
        done = cutover.git(self.checkout, "worktree", "list", "--porcelain")
        paths = [line.split(" ", 1)[1] for line in done.stdout.splitlines() if line.startswith("worktree ")]
        others = paths[1:] if done.returncode == 0 else []
        if not others:
            self.say(f"Left the main checkout at {self.checkout}, which has no other worktrees.")
            return
        self.say(f"Left the main checkout at {self.checkout} and its worktrees, which you can delete with "
                 "`git worktree remove <path>`:")
        for path in others:
            self.say(f"    {path}")


def main(argv):
    return project_steps.main(argv, USAGE, "agent-desk project remove", "removal", ("repo",),
                              lambda desk, dry_run, repo: Remove(desk, repo, dry_run).run())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
