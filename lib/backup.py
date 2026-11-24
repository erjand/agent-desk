"""Backs the local store up to a git repository and to nightly snapshots, and restores a database from either.

    python3 backup.py                    writes every item as JSON into the backup repository, commits, and pushes
    python3 backup.py snapshot           copies the database to snapshots/<date>.sqlite and prunes old snapshots
    python3 backup.py tick               what the timer runs: the backup, then a snapshot if today has none
    python3 backup.py install            writes and enables agent-desk-backup.timer
    python3 backup.py restore --from <repository | snapshot> --to <state folder>

The backup repository is AGENT_DESK_DATA, or ~/dev/repo/agent-desk-data, and the first backup creates it when the
folder is missing or empty. Each item becomes `<owner>/<repo>/<number>.json`, beside a `project.json` and a
`label_colors.json` that maps each label's name to its color, all written with sorted keys so an unchanged item or set
of colors leaves its file as it was. A backup never deletes a file, so a run against an empty database commits
nothing. It pushes to `origin`, or the repository's only remote, at most once an hour; a push that fails prints a
warning, and the next run tries again. Before a push to github.com, `gh repo view` reads the repository's visibility,
and the backup skips the push when the repository is public or `gh` can't say, since the store holds every project's
issues; the next run asks again. The export leaves out runs, events, checks, and assets, which the snapshots in the
state folder keep, and each issue's places in the start order and the list order. A restore from the repository gives
each issue its place in the list order by number, the highest first.

Each run saves how it went in `backup.json` beside the database, which the board reads: when it ran, the backup
remote or null, when the newest backup commit was made and the last push went, whether a commit waits for the push,
and the error of a push or a backup that failed.

A restore writes `desk.sqlite` in the state folder it is given and refuses to replace one there.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import store

DATA = Path(os.environ.get("AGENT_DESK_DATA") or Path.home() / "dev" / "repo" / "agent-desk-data")
SNAPSHOTS = store.STATE / "snapshots"
UNITS = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
PUSH_EVERY = 3600
KEEP_DAYS = 7
PUSH_TIMEOUT = 120
GH_TIMEOUT = 30
# A github.com URL in the https, ssh, or scp form git takes, with the owner and repository it names.
GITHUB_URL = re.compile(r"^(?:(?:https?|ssh|git)://(?:[^@/]+@)?github\.com(?::\d+)?/|(?:[^@/:]+@)?github\.com:)"
                        r"(?P<slug>[^/]+/[^/]+?)(?:\.git)?/?$", re.IGNORECASE)
ON_GITHUB = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?github\.com[:/]", re.IGNORECASE)
SNAPSHOT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.sqlite$")
# The columns each table's rows carry into the export, leaving out the ones the file's place already names.
ITEM_COLUMNS = ("number", "kind", "title", "body", "state", "state_reason", "author", "created_at", "updated_at",
                "closed_at")
REVIEW_COLUMNS = ("branch", "base", "head_sha", "draft", "merged_sha", "merged_commits")
COMMENT_COLUMNS = ("id", "author", "body", "path", "line", "head_sha", "to_agent", "created_at", "remote_id",
                   "sent_in")


class BackupError(Exception):
    """A problem the command line reports in one line."""


def count(number, noun):
    """A number and its noun, made plural unless the number is 1."""
    return f"{number} {noun}{'' if number == 1 else 's'}"


def dump(value):
    """JSON with sorted keys and a closing newline, so the same value always writes the same bytes."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# The export.

def export(db):
    """Every project, its label colors, and its items, as {relative path: file text}, read in one transaction."""
    files = {}
    db.execute("BEGIN")
    try:
        for project in db.execute("SELECT * FROM projects ORDER BY slug").fetchall():
            files[f"{project['slug']}/project.json"] = dump(
                {"path": project["path"], "next_number": project["next_number"], "mode": project["mode"]})
            files[f"{project['slug']}/label_colors.json"] = dump(dict(db.execute(
                "SELECT name, color FROM label_colors WHERE project = ?", (project["slug"],)).fetchall()))
        for row in db.execute("SELECT * FROM items ORDER BY project, number").fetchall():
            key = (row["project"], row["number"])
            item = {column: row[column] for column in ITEM_COLUMNS}
            item["labels"] = [one["name"] for one in db.execute(
                "SELECT name FROM labels WHERE project = ? AND number = ? ORDER BY name", key)]
            item["links"] = [{"kind": one["kind"], "to": one["to_number"]} for one in db.execute(
                "SELECT kind, to_number FROM links WHERE project = ? AND from_number = ? ORDER BY kind, to_number",
                key)]
            # Every comment carries sent_in, even when empty, since a restore counts a line comment without the key
            # as sent.
            item["comments"] = [{column: one[column] for column in COMMENT_COLUMNS} for one in db.execute(
                "SELECT * FROM comments WHERE project = ? AND number = ? ORDER BY id", key)]
            review = db.execute("SELECT * FROM reviews WHERE project = ? AND number = ?", key).fetchone()
            # merged_commits, which only a rebase-merge sets, stays out when empty, so earlier backups' files stand.
            item["review"] = {column: review[column] for column in REVIEW_COLUMNS
                              if column != "merged_commits" or review[column] is not None} if review else None
            files[f"{row['project']}/{row['number']}.json"] = dump(item)
    finally:
        db.execute("COMMIT")
    return files


def write_files(folder, files):
    """Writes each file whose text differs from what the folder holds, and returns how many it wrote."""
    written = 0
    for name, text in files.items():
        path = folder / name
        try:
            if path.read_text() == text:
                continue
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written += 1
    return written


# Git.

def git(folder, *args, check=True, timeout=None):
    """A git command's completed process in the folder, with prompts for credentials turned off."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, env=env,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        done = subprocess.CompletedProcess(args, 124, "", f"git {args[0]} took longer than {timeout} seconds")
    if check and done.returncode != 0:
        raise BackupError(f"git {' '.join(args)} failed in {folder}: {done.stderr.strip()}")
    return done


def value(folder, *args):
    """A git command's trimmed output, or None when it fails."""
    done = git(folder, *args, check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def ready(folder):
    """Makes the folder a git repository when it is missing or empty, and refuses any other folder that isn't one.
    Returns whether it made one."""
    if (folder / ".git").exists():
        return False
    if folder.exists() and any(folder.iterdir()):
        raise BackupError(f"{folder} holds files but isn't a git repository. Point AGENT_DESK_DATA at a repository, "
                          "or at a missing or empty folder.")
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init", "--quiet")
    return True


def commit(folder, message):
    """Commits every change in the repository, and returns whether there was one."""
    git(folder, "add", "--all")
    if git(folder, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    # A machine with no git identity still commits its backups.
    identity = [] if value(folder, "config", "user.email") else ["-c", "user.name=agent-desk",
                                                                 "-c", "user.email=agent-desk@localhost"]
    git(folder, *identity, "commit", "--quiet", "--message", message)
    return True


def remote_of(folder):
    """The remote a backup pushes to: origin, or the repository's only remote, or None."""
    remotes = (value(folder, "remote") or "").split()
    if "origin" in remotes:
        return "origin"
    return remotes[0] if len(remotes) == 1 else None


def github_repository(url):
    """The `owner/repo` a github.com URL names, "" for a github.com URL that names none, or None for another host."""
    match = GITHUB_URL.match(url)
    if match:
        return match.group("slug")
    return "" if ON_GITHUB.match(url) else None


def visibility(slug):
    """A GitHub repository's visibility as `gh` reports it, such as PUBLIC or PRIVATE, and None, or None and why `gh`
    couldn't read it."""
    try:
        done = subprocess.run(["gh", "repo", "view", slug, "--json", "visibility"], capture_output=True, text=True,
                              timeout=GH_TIMEOUT)
    except FileNotFoundError:
        return None, "gh isn't installed"
    except subprocess.TimeoutExpired:
        return None, f"gh repo view took longer than {GH_TIMEOUT} seconds"
    if done.returncode != 0:
        return None, (done.stderr.strip().splitlines() or [f"gh repo view exited with status {done.returncode}"])[-1]
    try:
        found = json.loads(done.stdout).get("visibility")
    except (ValueError, AttributeError):
        found = None
    if not isinstance(found, str) or not found:
        return None, "gh repo view printed no visibility"
    return found.upper(), None


def refusal(folder, remote):
    """Why the backup won't push to the remote, or None when every URL it pushes to is private on GitHub or on
    another host."""
    urls = (value(folder, "remote", "get-url", "--push", "--all", remote) or "").splitlines()
    for url in urls:
        slug = github_repository(url.strip())
        if slug is None:
            continue
        if not slug:
            return f"{remote} pushes to {url}, which names no GitHub repository, so the backup doesn't push"
        found, why = visibility(slug)
        if why:
            return (f"the backup can't tell whether {remote}, the GitHub repository {slug}, is private, so it doesn't "
                    f"push, and the next backup asks again: {why}")
        if found == "PUBLIC":
            return (f"{remote} is the public GitHub repository {slug}, and the backup pushes only to a private "
                    "repository, so it doesn't push")
    return None


def push(folder, clock):
    """Pushes the branch when it holds commits the last push didn't, and an hour has passed since that push. Returns
    what happened, as a line to print, or None when there was nothing to do, and why a push failed, or None."""
    remote = remote_of(folder)
    head = value(folder, "rev-parse", "--verify", "--quiet", "HEAD")
    if remote is None or head is None or head == value(folder, "config", "agent-desk.pushed-sha"):
        return None, None
    last = int(value(folder, "config", "agent-desk.pushed-at") or 0)
    if clock - last < PUSH_EVERY:
        wait = time.strftime("%H:%M", time.localtime(last + PUSH_EVERY))
        return f"The push to {remote} waits until {wait}, an hour after the last one.", None
    branch = value(folder, "symbolic-ref", "--short", "HEAD")
    if branch is None:
        why = f"{folder} has no branch checked out, so the backup doesn't push"
        return f"agent-desk: warning: {why}.", why
    why = refusal(folder, remote)
    if why:
        return f"agent-desk: warning: {why}.", why
    done = git(folder, "push", "--quiet", remote, f"HEAD:refs/heads/{branch}", check=False, timeout=PUSH_TIMEOUT)
    if done.returncode != 0:
        reason = (done.stderr.strip().splitlines() or ["no reason given"])[-1]
        return (f"agent-desk: warning: the push to {remote} failed, and the next backup tries again: {reason}",
                f"the push to {remote} failed: {reason}")
    git(folder, "config", "agent-desk.pushed-sha", head)
    git(folder, "config", "agent-desk.pushed-at", str(int(clock)))
    return f"Pushed {head[:7]} to {remote}.", None


# What the board shows.

def state_path(database):
    """Where a backup of `database` saves how it went, beside the database."""
    return Path(database).parent / "backup.json"


def saved(database):
    """How the last backup of `database` went, as `backup.json` holds it, or None before the first."""
    try:
        state = json.loads(state_path(database).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def save(database, state):
    path = state_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".new")
    partial.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def record(database, folder, clock, error):
    """Saves how a backup into `folder` went: its remote, its newest commit and last push, and why a push failed."""
    remote = remote_of(folder)
    head = value(folder, "rev-parse", "--verify", "--quiet", "HEAD")
    committed = value(folder, "log", "-1", "--format=%ct")
    pushed = value(folder, "config", "agent-desk.pushed-at")
    state = {"at": int(clock), "remote": remote,
             "waiting": remote is not None and head is not None
             and head != value(folder, "config", "agent-desk.pushed-sha")}
    if committed and committed.isdigit():
        state["committedAt"] = int(committed)
    if pushed and pushed.isdigit():
        state["pushedAt"] = int(pushed)
    if error:
        state["error"] = error
    save(database, state)


def backup(database, folder, clock=None):
    """Exports the database into the repository, commits any change, and pushes when due. Returns the lines to
    print."""
    clock = time.time() if clock is None else clock
    if not database.exists():
        return [f"{database} doesn't exist, so there is nothing to back up."]
    lines = [f"Created the backup repository {folder}."] if ready(folder) else []
    desk = store.Store(database)
    try:
        files = export(desk.db)
    finally:
        desk.close()
    written = write_files(folder, files)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(clock))
    if commit(folder, f"Back up the store at {when}\n\n{count(written, 'file')} changed."):
        lines.append(f"Committed {count(written, 'changed file')} to {folder}.")
    else:
        lines.append(f"No item changed since the last backup in {folder}.")
    pushed, error = push(folder, clock)
    if pushed:
        lines.append(pushed)
    record(database, folder, clock, error)
    return lines


# Snapshots.

def snapshot(database, folder, today=None):
    """Copies the database to `<today>.sqlite` in the folder, replacing one taken earlier today, and deletes the
    snapshots older than seven days. Returns the lines to print."""
    today = today or date.today()
    if not database.exists():
        return [f"{database} doesn't exist, so there is nothing to snapshot."]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{today.isoformat()}.sqlite"
    partial = target.with_name(target.name + ".partial")
    partial.unlink(missing_ok=True)
    desk = store.Store(database)
    try:
        # VACUUM INTO reads one consistent view of a database other processes keep writing.
        desk.db.execute("VACUUM INTO ?", (str(partial),))
    finally:
        desk.close()
    os.replace(partial, target)
    lines = [f"Wrote {target}."]
    oldest = today - timedelta(days=KEEP_DAYS - 1)
    for path in sorted(folder.iterdir()):
        match = SNAPSHOT_NAME.match(path.name)
        if match and date.fromisoformat(match.group(1)) < oldest:
            path.unlink()
            lines.append(f"Deleted {path}, older than {KEEP_DAYS} days.")
    return lines


def tick(database, data, snapshots, clock=None, today=None):
    """The backup, then today's snapshot if there isn't one yet, each run when the other fails."""
    lines, failed = [], False
    try:
        lines += backup(database, data, clock)
    except (BackupError, sqlite3.Error, OSError) as problem:
        lines.append(f"agent-desk: the backup failed: {problem}")
        failed = True
        # The board shows the failure beside what the last backup that worked saved.
        try:
            save(database, {**(saved(database) or {}), "at": int(time.time() if clock is None else clock),
                            "error": f"the backup failed: {problem}"})
        except OSError:
            pass
    today = today or date.today()
    if not (snapshots / f"{today.isoformat()}.sqlite").exists():
        try:
            lines += snapshot(database, snapshots, today)
        except (sqlite3.Error, OSError) as problem:
            lines.append(f"agent-desk: the snapshot failed: {problem}")
            failed = True
    return lines, failed


# Restore.

def read_only(path):
    """A connection that reads a database file and writes nothing beside it."""
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def counts(database):
    """Each project's issue and review counts in a database, as {project: (issues, reviews)}."""
    db = read_only(database)
    try:
        rows = db.execute("SELECT project, SUM(kind = 'issue'), SUM(kind = 'review') FROM items GROUP BY project"
                          " ORDER BY project").fetchall()
    finally:
        db.close()
    return {project: (issues, reviews) for project, issues, reviews in rows}


def load(folder, db):
    """Inserts every project, label color, and item a backup repository holds into an empty database's connection."""
    projects = sorted({path.parent for path in folder.glob("*/*/*.json")})
    db.execute("BEGIN IMMEDIATE")
    try:
        for place in projects:
            slug = f"{place.parent.name}/{place.name}"
            meta = place / "project.json"
            project = json.loads(meta.read_text()) if meta.exists() else {}
            db.execute("INSERT INTO projects (slug, path, next_number, mode) VALUES (?, ?, ?, ?)",
                       (slug, project.get("path"), project.get("next_number", 1), project.get("mode")))
            # A backup written before label colors restores without them, and the page hashes each label's color.
            colors = place / "label_colors.json"
            if colors.exists():
                db.executemany("INSERT INTO label_colors (project, name, color) VALUES (?, ?, ?)",
                               [(slug, *pair) for pair in sorted(json.loads(colors.read_text()).items())])
            for path in sorted(place.glob("*.json"), key=lambda one: one.name):
                if not path.stem.isdigit():
                    continue
                item = json.loads(path.read_text())
                db.execute(f"INSERT INTO items (project, {', '.join(ITEM_COLUMNS)})"
                           f" VALUES (?{', ?' * len(ITEM_COLUMNS)})",
                           (slug, *(item[column] for column in ITEM_COLUMNS)))
                number = item["number"]
                for name in item["labels"]:
                    db.execute("INSERT INTO labels (project, number, name) VALUES (?, ?, ?)", (slug, number, name))
                for link in item["links"]:
                    db.execute("INSERT INTO links (project, from_number, to_number, kind) VALUES (?, ?, ?, ?)",
                               (slug, number, link["to"], link["kind"]))
                # A backup written before comments had a remote_id restores them without one. One written before
                # every comment carried sent_in left it out of each line comment not yet sent and of some that were,
                # so a line comment without it restores as sent, with 0 as an import's are, and the next Send to
                # agent repeats none of them.
                for comment in item["comments"]:
                    if comment.get("path") is not None and "sent_in" not in comment:
                        comment = {**comment, "sent_in": 0}
                    db.execute(f"INSERT INTO comments (project, number, {', '.join(COMMENT_COLUMNS)})"
                               f" VALUES (?, ?{', ?' * len(COMMENT_COLUMNS)})",
                               (slug, number, *(comment.get(column) for column in COMMENT_COLUMNS)))
                # A backup written before reviews had merged_commits restores them without it.
                if item["review"]:
                    db.execute(f"INSERT INTO reviews (project, number, {', '.join(REVIEW_COLUMNS)})"
                               f" VALUES (?, ?{', ?' * len(REVIEW_COLUMNS)})",
                               (slug, number, *(item["review"].get(column) for column in REVIEW_COLUMNS)))
            # The export leaves the list order out, so each issue takes its place by number, the highest first.
            db.execute("UPDATE items SET list_rank = number WHERE project = ? AND kind = 'issue'", (slug,))
            # A project.json from before the newest item, or none at all, still leaves new numbers past every item.
            db.execute("UPDATE projects SET next_number = MAX(next_number, COALESCE((SELECT MAX(number) + 1"
                       " FROM items WHERE project = ?), 1)) WHERE slug = ?", (slug, slug))
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def restore(source, state):
    """Builds `desk.sqlite` in the state folder from a backup repository or a snapshot, and returns the lines to
    print, one per project with its item counts."""
    target = state / "desk.sqlite"
    if target.exists():
        raise BackupError(f"{target} already exists, and a restore never replaces a database. Restore into an empty "
                          "folder, or move the database aside first.")
    if not source.exists():
        raise BackupError(f"{source} doesn't exist.")
    state.mkdir(parents=True, exist_ok=True)
    partial = state / "desk.sqlite.partial"
    partial.unlink(missing_ok=True)
    try:
        if source.is_dir():
            kind = "backup repository"
        else:
            from_db, to_db = read_only(source), sqlite3.connect(partial)
            try:
                from_db.backup(to_db)
            finally:
                from_db.close()
                to_db.close()
            kind = "snapshot"
        # The store brings an older snapshot up to this code's schema. Leaving WAL mode folds the write-ahead log into
        # the file before the rename, and the next process to open the database turns WAL mode back on.
        desk = store.Store(partial)
        try:
            if kind == "backup repository":
                load(source, desk.db)
            desk.db.execute("PRAGMA journal_mode = DELETE")
        finally:
            desk.close()
        os.replace(partial, target)
    except BaseException:
        for leftover in (partial, Path(f"{partial}-wal"), Path(f"{partial}-shm")):
            leftover.unlink(missing_ok=True)
        raise
    found = counts(target)
    lines = [f"Restored {target} from the {kind} {source}."]
    lines += [f"{project}: {count(issues + reviews, 'item')} ({count(issues, 'issue')}, {count(reviews, 'review')})"
              for project, (issues, reviews) in found.items()]
    if not found:
        lines.append("It holds no items.")
    return lines


# The timer.

def install(units, command):
    """Writes the backup service and its timer, then enables and starts the timer."""
    units.mkdir(parents=True, exist_ok=True)
    (units / "agent-desk-backup.service").write_text("\n".join([
        "[Unit]", "Description=Back up the agent-desk store to its git repository, and snapshot it nightly", "",
        "[Service]", f'Environment="AGENT_DESK_INSTALLED={Path(command).parents[1]}"', "Type=oneshot",
        f'Environment="AGENT_DESK_DATA={DATA}"', f"ExecStart={command} backup tick",
        ""]))
    (units / "agent-desk-backup.timer").write_text("\n".join([
        "[Unit]", "Description=Back up the agent-desk store every 15 minutes", "", "[Timer]", "OnStartupSec=5min",
        "OnUnitActiveSec=15min", "AccuracySec=1min", "", "[Install]", "WantedBy=timers.target", ""]))
    for args in (["daemon-reload"], ["enable", "--now", "agent-desk-backup.timer"]):
        done = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
        if done.returncode != 0:
            raise BackupError(f"systemctl --user {' '.join(args)} failed: {done.stderr.strip()}")
    return [f"The backup timer is on, backing up to {DATA}. Its log: journalctl --user -u agent-desk-backup"]


# The command line.

def parser():
    top = argparse.ArgumentParser(prog="agent-desk backup", description="Back up the local store, or restore it.")
    verbs = top.add_subparsers(dest="verb")
    verbs.add_parser("snapshot")
    verbs.add_parser("tick")
    verbs.add_parser("install")
    again = verbs.add_parser("restore")
    again.add_argument("--from", dest="source", required=True, type=Path,
                       help="a backup repository, or a snapshot's .sqlite file")
    again.add_argument("--to", dest="state", required=True, type=Path,
                       help="the state folder to write desk.sqlite in; it must not hold one")
    return top


def main(argv):
    args = parser().parse_args(argv)
    failed = False
    try:
        if args.verb is None:
            lines = backup(store.DATABASE, DATA)
        elif args.verb == "snapshot":
            lines = snapshot(store.DATABASE, SNAPSHOTS)
        elif args.verb == "tick":
            lines, failed = tick(store.DATABASE, DATA, SNAPSHOTS)
        elif args.verb == "install":
            command = Path(__file__).resolve().parents[1] / "bin" / "agent-desk"
            lines = install(UNITS, command)
        else:
            lines = restore(args.source.expanduser().absolute(), args.state.expanduser().absolute())
    except (BackupError, store.StoreError, sqlite3.Error, OSError) as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    for line in lines:
        # Problems and warnings go to the standard error; a warning alone leaves the exit status 0.
        print(line, file=sys.stderr if line.startswith("agent-desk:") else sys.stdout)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
