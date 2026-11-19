"""Moves a project to a new owner/repo name in the local store and on this machine:

    python3 rename.py [--dry-run] <old owner/repo> <new owner/repo>

`agent-desk project rename` runs it once the project has a new name on its host, or before, since `repo_here` in
lib/store.py reads the name from the main checkout's `origin` and a command would otherwise find an empty project. It
refuses to start unless the project is on Hold, with no run working, no branch check queued or
running, and its merge queue empty. It refuses when the new name already belongs to another project in the store or
in ~/.config/agent-desk/projects. Then it takes these steps in order, printing each, and stops at the first failure
with the step named:

1. store: rewrites the name in every table of desk.sqlite with a `project` column and in `projects.slug`, moves each
   `checks.log_path` and `assets.path` that names the project's `<owner>__<repo>` folder to the new folder, and writes
   a `project-renamed` event naming both, in one transaction.
2. files: moves the project's files in the state folder that carry its `<owner>__<repo>` name: its folders in
   `chores/`, `checks/`, and `assets/`, and its files and locks in `fixes/`, `merges/`, and `pushes/`.
3. hold: renames the project's mode in hold.json and its Manual picks (`owner/repo#n`), and its queued chores in
   chore-repeat.json.
4. projects: changes the project's line in ~/.config/agent-desk/projects.
5. remote: points the main checkout's `origin` at the new name with `git remote set-url` when it is a host address
   naming the old name, so the checkout and its worktrees resolve to the new name at once. A path, or no origin, stays.
6. backup: moves the backup repository's `<owner>/<repo>/` folder to the new name with `git mv` and commits the move.
   A file a backup already wrote under the new name replaces the old one, since that backup read the renamed store.

`boards/` is keyed by the checkout's path and `claims/` by process, so they stay. The history in usage.tsv and
runs.jsonl keeps the name each line was written under. The systemd units that name the project end
with their runs, which the refusal waits out.

A rerun after a failure finishes what is left: each step skips what it finds done, and the store's `project-renamed`
event tells a rerun that the new name in the store is this project's. --dry-run opens the store read-only, prints each
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
# A host address, as `repo_here` in lib/store.py reads one: the part before owner/repo, owner/repo, and any `.git`.
HOST_URL = re.compile(r"((?:[^/:@]+@[^:]+:|[a-z+]+://[^/]+/))(.+?)((?:\.git)?/?)")
USAGE = "agent-desk project rename [--dry-run] <old owner/repo> <new owner/repo>"


def moved_path(path, old, new):
    """A stored path inside the old name's `<owner>__<repo>` folder, moved to the new name's, or None for another."""
    if not path:
        return None
    place = Path(path)
    if place.parent.name != store.project_key(old):
        return None
    return str(place.parent.with_name(store.project_key(new)) / place.name)


def renamed(db, old, new):
    """Whether the store records this rename, so the new name it holds is this project's."""
    return db.execute("SELECT 1 FROM events WHERE type = 'project-renamed' AND json_extract(payload, '$.from') = ? "
                      "AND json_extract(payload, '$.to') = ?", (old, new)).fetchone() is not None


def host_url(url, repo):
    """The parts of a host address naming the project, or None for a path, another project, or no origin."""
    match = HOST_URL.fullmatch(url or "")
    if match and match.group(2).lower() == repo.lower():
        return match.groups()
    return None


def move(old, new):
    """
    Moves a file or folder to its new name, merging a folder into one already there and dropping an old lock or a file
    the new one matches. Raises OSError on a file that differs at both names.
    """
    if not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return
    if old.is_dir() and new.is_dir():
        for child in sorted(old.iterdir()):
            move(child, new / child.name)
        old.rmdir()
        return
    if old.is_file() and new.is_file() and (old.suffix == ".lock" or old.read_bytes() == new.read_bytes()):
        old.unlink()
        return
    raise OSError(f"both {old} and {new} exist and differ. Keep the one you want at {new}, delete {old}, and rerun.")


class Rename(project_steps.Steps):
    """One project's rename, which prints each step and what it changed."""

    def __init__(self, desk, old, new, dry_run=False, say=print):
        super().__init__(desk, dry_run, say)
        self.old, self.new = old, new

    def run(self):
        self.say(f"Renaming {self.old} to {self.new}{' (dry run)' if self.dry_run else ''}")
        self.step("check", self.check)
        self.step("store", self.rename_store)
        self.step("files", self.move_files)
        self.step("hold", self.rename_modes)
        self.step("projects", self.rename_line)
        self.step("remote", self.point_remote)
        self.step("backup", self.move_backup)
        self.say(f"Renamed {self.old} to {self.new}." if not self.dry_run else "Changed nothing.")

    # The check.

    def check(self):
        db = self.desk.db
        old_rows, new_rows = rows_of(db, self.old), rows_of(db, self.new)
        done = renamed(db, self.old, self.new)
        if old_rows and new_rows:
            raise Failed("check", f"the store holds both {self.old} and {self.new}, so {self.new} names another "
                         "project.")
        if new_rows and not done:
            raise Failed("check", f"{self.new} already names a project in the store.")
        if not old_rows and not new_rows:
            raise Failed("check", f"the store holds no project {self.old}.")
        lines = dict(line for line in listed() if len(line) == 2)
        if self.new in lines and (self.old in lines or old_rows):
            raise Failed("check", f"{store.PROJECTS} already names {self.new}, at {lines[self.new]}.")
        # A rerun finds the store and hold.json under the new name.
        self.refuse(project_steps.refusals(self.desk, (self.old, self.new)),
                    f"Run `agent-desk hold {self.old}` first, and wait for the runs, the checks, and the merge queue "
                    "to finish.")

    # The steps.

    def rename_store(self):
        db = self.desk.db
        counts = rows_of(db, self.old)
        if not counts:
            self.note(f"Done: the store holds {self.new}.")
            return
        held = ", ".join(f"{table} {count}" for table, count in sorted(counts.items()))
        self.change(f"rename {self.old} to {self.new} in the store ({held})",
                    f"Renamed {self.old} to {self.new} in the store ({held})", self.write_store)

    def write_store(self):
        old, new = self.old, self.new
        with self.desk.write():
            db = self.desk.db
            for table, column, ident in (("checks", "log_path", "id"), ("assets", "path", "hash")):
                rows = db.execute(f"SELECT {ident}, {column} FROM {table} WHERE project = ?", (old,)).fetchall()
                for row in rows:
                    path = moved_path(row[column], old, new)
                    if path:
                        db.execute(f"UPDATE {table} SET {column} = ? WHERE {ident} = ?", (path, row[ident]))
            for table in project_tables(db):
                db.execute(f"UPDATE {table} SET project = ? WHERE project = ?", (new, old))
            db.execute("UPDATE projects SET slug = ? WHERE slug = ?", (new, old))
            self.desk._event(new, None, "project-renamed", **{"from": old, "to": new})

    def move_files(self):
        pairs = [(old, new) for old, new in zip(state_paths(self.old), state_paths(self.new)) if old.exists()]
        if not pairs:
            self.note("Done: the state folder holds nothing under the old name.")
        for old, new in pairs:
            where = old.relative_to(store.STATE)
            self.change(f"move {where} to {new.relative_to(store.STATE)}",
                        f"Moved {where} to {new.relative_to(store.STATE)}", lambda old=old, new=new: move(old, new))

    def rename_modes(self):
        if self.dry_run:
            self.report(self.renamed_modes(hold.status()), hold.HOLD)
        else:
            with hold.locked():
                data = hold.status()
                changed = self.renamed_modes(data)
                if changed:
                    hold.save(data)
            self.report(changed, hold.HOLD)
        path = chore_repeat.STATE / "chore-repeat.json"
        try:
            queued = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            queued = {}
        if self.old not in queued or self.dry_run:
            self.report(["its queued chores"] if self.old in queued else [], path)
            return
        with chore_repeat.queues(path) as queued:
            if self.old in queued:
                queued[self.new] = {**queued.pop(self.old), **queued.get(self.new, {})}
        self.report(["its queued chores"], path)

    def report(self, changed, path):
        if changed:
            what = ", ".join(changed)
            self.note(f"Would rename {what} in {path.name}." if self.dry_run else f"Renamed {what} in {path.name}.")
        else:
            self.note(f"Done: {path.name} holds nothing under the old name.")

    def renamed_modes(self, data):
        """Renames the project in hold.json's data, and returns what it renamed."""
        old, new, changed = self.old, self.new, []
        for field in ("projects", "before_projects"):
            modes = data.get(field)
            if isinstance(modes, dict) and old in modes:
                modes[new] = modes.pop(old)
                changed.append("its mode" if field == "projects" else "its mode before Hold")
        picks = [f"{new}#{pick.split('#', 1)[1]}" if pick.split("#", 1)[0] == old else pick
                 for pick in data["picked"]]
        if picks != data["picked"]:
            data["picked"] = picks
            changed.append("its picks")
        return changed

    def rename_line(self):
        lines = listed()
        if not any(line[0] == self.old for line in lines if line):
            self.note(f"Done: {store.PROJECTS} has no line for {self.old}.")
            return

        def write():
            text = "".join(" ".join([self.new, *line[1:]] if line and line[0] == self.old else line) + "\n"
                           for line in lines)
            temporary = store.PROJECTS.with_name(f".{store.PROJECTS.name}.rename")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(store.PROJECTS)

        self.change(f"change {self.old}'s line in {store.PROJECTS} to {self.new}",
                    f"Changed {self.old}'s line in {store.PROJECTS} to {self.new}", write)

    def checkout(self):
        lines = dict(line for line in listed() if len(line) == 2)
        return lines.get(self.old) or lines.get(self.new)

    def point_remote(self):
        checkout = self.checkout()
        if not checkout:
            self.note(f"Skipped: {store.PROJECTS} names no main checkout of {self.new}.")
            return
        url = cutover.origin_of(checkout)
        parts = host_url(url, self.old)
        if not parts:
            self.note(f"Done: origin is {url}." if host_url(url, self.new) else
                      f"Kept: origin of {checkout} is {url or 'missing'}, which is no host address naming {self.old}.")
            return
        prefix, _, suffix = parts
        target = f"{prefix}{self.new}{suffix}"

        def set_url():
            done = cutover.git(checkout, "remote", "set-url", "origin", target)
            if done.returncode != 0:
                raise OSError(f"git remote set-url failed in {checkout}: {done.stderr.strip()}")

        self.change(f"point origin of {checkout} at {target}", f"Pointed origin of {checkout} at {target}", set_url)

    def move_backup(self):
        folder = backup.DATA
        old = folder / self.old
        if not (folder / ".git").exists() or not old.exists():
            self.note(f"Done: {folder} holds no {self.old}/ folder.")
            return
        self.change(f"move {old} to {folder / self.new} with git mv and commit it",
                    f"Moved {self.old}/ to {self.new}/ in {folder} and committed it", self.commit_backup)

    def commit_backup(self):
        folder = backup.DATA
        old, new = folder / self.old, folder / self.new
        new.parent.mkdir(parents=True, exist_ok=True)
        if not new.exists():
            backup.git(folder, "mv", self.old, self.new)
        else:
            # A backup that ran after the store step wrote the new folder from the renamed store, so its files win.
            # The commit stages the moves and deletions, and git records each moved file as a rename.
            for path in sorted(item for item in old.rglob("*") if item.is_file()):
                target = new / path.relative_to(old)
                if target.exists():
                    path.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    path.rename(target)
            shutil.rmtree(old)
        backup.commit(folder, f"Rename {self.old} to {self.new}")


def main(argv):
    return project_steps.main(argv, USAGE, "agent-desk project rename", "rename", ("old", "new"),
                              lambda desk, dry_run, old, new: Rename(desk, old, new, dry_run).run())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
