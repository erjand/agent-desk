"""Keeps every project's issues, reviews, comments, runs, and events in one SQLite database on this machine.

The database is `desk.sqlite` in agent-desk's state folder, in WAL mode with a 5-second busy timeout, so the scripts,
the web server, and each pane read and write it at once. These rules hold for every table:

- Issues and reviews share one number sequence per project, as on GitHub. An item created with a number, as an import
  does, keeps it, and the next new item takes the number after the highest it ever held, so a deleted one never returns.
- An issue's workflow state lives in `items.state`: open, queued, in-progress, waiting, needs-design, on-hold, or
  closed. The labels that named those states on GitHub (`agent-ready` for queued, and the rest by their own names) are
  accepted by `--add-label`, `--remove-label`, and `--label` and move or match the state. `--json status` prints it,
  while `state` prints OPEN, CLOSED, or MERGED as gh does.
- Each project keeps one start order of its issues that are queued or waiting, in `items.rank`, which the dispatcher
  starts fresh starts and hands serial slots by. An issue joins the end when it enters either state, keeps its place
  as it moves between them, and leaves the order when it moves to any other state. `Store.move` moves one up, down,
  or to the top, with a `queue-moved` event. `--json position` prints an issue's place, from 1, or null outside it.
- Every write goes through `Store.write`, which opens with BEGIN IMMEDIATE and appends its `events` rows in the same
  transaction. The busy timeout doesn't cover a read transaction that upgrades to a write, so no write starts as a
  read. A reader learns of every change from `events.id` alone. An import is the one exception: it writes its pages
  without events and ends with one `imported` event for the whole run.
- Once a write that added events commits, it saves the newest event id in `desk.sqlite-event` beside the database, so
  a reader that can't open SQLite, such as the inbox pane, learns of a change by reading that small file. Each save
  holds a lock on `desk.sqlite-event.lock` while it reads the id and replaces the file, so the file's id never goes
  back.
- `PRAGMA user_version` holds the schema's version. A process whose code is older than the database reads it and
  refuses to write, and `write` checks it again inside each transaction for a process that outlives an update.
- Only the installed checkout's code, the one `agent-desk` on PATH runs or AGENT_DESK_INSTALLED names, creates or
  migrates the live database in the account's own `~/.local/state`. Code from another checkout opens a database of its
  own, under its own XDG_STATE_HOME or at a path it names, or stops with an error. A store opened read-only, as the
  commands that only read open it, never creates, migrates, or writes its database.
- A run is a row in `runs`: `agent-desk request` adds it as `requested` with a `run-requested` event, the dispatcher
  (`lib/dispatch.py`) moves it to `running`, and its unit's exit handler records how it ended.
- A branch check is a row in `checks`: the dispatcher records each move of an `agent/*` head as a `branch-moved` event
  and queues a check of the head, `lib/checks.py` runs it, and a first attempt that fails queues the second with it.
- A review's end, its `review-merged` event or the `state-changed` event that closes it unmerged, asks the dispatcher
  for a cleanup. The dispatcher records each cleanup it starts as a `cleanup-started` event naming the end, so after a
  restart it starts the cleanups of the ends since the last one.
- A watched branch is a row in `branches` holding its newest head, which each `branch-moved` event sets. The
  dispatcher tries each open review's head against its base with `git merge-tree` whenever either moves, and records
  whether it merges there, with a `branch-merges` event when the answer changes.
- Each event and comment carries its author: `agent` when AGENT_DESK_RUN is set, as it is inside a run, `desk` when
  AGENT_DESK_AUTHOR says so, and `you` otherwise.
- A review's line comment holds its file's path, its line in the new file, and the head it was made on, which
  defaults to the review's head as the store last saw it, and `sent_in`, the comment sent to the agent that quoted
  it, which Send to agent writes in the same transaction. A comment with `to_agent` set asks the dispatcher for an
  agent run on its issue or review, unless an agent wrote it. The dispatcher records its answer to each one as a
  `comment-answered` event whose `added` names the comment's event, in the transaction that asks for the run, so
  after a restart it answers the comments since the last one.

`Store` inherits its import, branch and check, run, GPU call, delete, and board methods from `lib/store_import.py`,
`lib/store_checks.py`, `lib/store_runs.py`, `lib/store_gpu.py`, `lib/store_delete.py`, and `lib/store_board.py`. The
migrations, status values, and time format live in `lib/store_schema.py`, whose names this module imports back, so
every caller reads them from `store`.
"""

import fcntl
import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from store_board import BoardMixin, mergeable_of, outer_state
from store_checks import ChecksMixin
from store_delete import DeleteMixin
from store_gpu import GpuMixin
from store_import import ImportMixin
from store_runs import FAILED_SECONDS, RunsMixin  # noqa: F401
from store_schema import (CHECK_FAILURES, GPU_SCHEMA, LINK_KINDS, MIGRATIONS, ORDER_STATES,  # noqa: F401
                          RANK_SCHEMA, SCHEMA_VERSION, STATUS_LABELS, STATUSES, StoreError, epoch_of, store_time)

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk"
DATABASE = STATE / "desk.sqlite"
# The database the installed agent-desk keeps when XDG_STATE_HOME is unset, in the account's own home whatever HOME
# says, which only the installed checkout's code creates or migrates.
LIVE = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "agent-desk" / "desk.sqlite"
# The agent-desk checkout this code runs from.
CHECKOUT = Path(__file__).resolve().parents[1]
PROJECTS = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-desk" / "projects"
BUSY_MS = 5000

ISSUE_FIELDS = ("number", "kind", "title", "body", "state", "status", "stateReason", "author", "labels", "comments",
                "createdAt", "updatedAt", "closedAt", "parent", "subIssues", "blockedBy", "blocking", "position",
                "duplicateOf")
REVIEW_FIELDS = ISSUE_FIELDS + ("headRefName", "baseRefName", "headRefOid", "isDraft", "mergeCommit",
                                "closingIssuesReferences", "checks", "mergeable")
# A mention that sends the agent to work, as lib/import_github.py and web/review.ts read one. A comment sent to the
# agent without one gets `@agent ` put in front.
MENTION = re.compile(r"(?<![\w/])@agent\b")


def project_key(repo):
    """The `<owner>__<repo>` name a project's files and folders carry under the state, cache, and assets folders."""
    return repo.replace("/", "__")


def now():
    """The time as the store writes it: UTC to the second, as GitHub prints it."""
    return store_time(time.time())


def author():
    """Who is writing: a run's agent, the desk's own scripts, or the user."""
    if os.environ.get("AGENT_DESK_RUN"):
        return "agent"
    return "desk" if os.environ.get("AGENT_DESK_AUTHOR") == "desk" else "you"


SIGNATURE = re.compile(r"\n*<sub>Posted by agent-desk[^\n]*</sub>\s*(?:<!-- agent-desk -->\s*)?\Z")


def with_edit_note(body, line):
    """`body` with `line` added as its own paragraph above the closing lines agent-desk signs a comment with."""
    signature = SIGNATURE.search(body)
    end = signature.start() if signature else len(body.rstrip())
    tail = "\n\n" + body[end:].lstrip("\n") if signature else "\n"
    return f"{body[:end].rstrip()}\n\n{line}{tail}"


def installed_checkouts(environ=None):
    """
    The checkouts that count as installed: the one `agent-desk` on PATH runs, and the one AGENT_DESK_INSTALLED names,
    which the systemd units whose PATH lacks the command set.
    """
    environ = os.environ if environ is None else environ
    found = set()
    command = shutil.which("agent-desk", path=environ.get("PATH", os.defpath))
    if command:
        found.add(Path(command).resolve().parents[1])
    if environ.get("AGENT_DESK_INSTALLED"):
        found.add(Path(environ["AGENT_DESK_INSTALLED"]).resolve())
    return found


def change_refused(path, change, checkout=None, live=None, environ=None):
    """
    Why this code may not make `change`, such as creating or migrating, to the database at `path`, or None when it
    may. Only the installed checkout's code changes the live database; code run from a worktree changes a database
    of its own, such as one under its own XDG_STATE_HOME, or one a test names.
    """
    checkout = CHECKOUT if checkout is None else checkout
    live = LIVE if live is None else live
    if Path(path).resolve() != Path(live).resolve():
        return None
    installed = installed_checkouts(environ)
    if checkout in installed:
        return None
    named = ", ".join(sorted(map(str, installed))) or "none, since agent-desk isn't on PATH"
    return (f"{path} {change}, and only the installed agent-desk ({named}) changes it, not the code in {checkout}. "
            "Set XDG_STATE_HOME to a folder of its own to run this checkout's code.")


def make_private(folder):
    """
    Creates `folder` with mode 0700, or narrows it to 0700 when group or others can enter it. The folder's mode guards
    everything under it, so the files inside keep the umask's modes. Folders above it keep their own modes.
    """
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.stat().st_mode & 0o077:
        folder.chmod(0o700)


class Store(ImportMixin, ChecksMixin, RunsMixin, GpuMixin, DeleteMixin, BoardMixin):
    """
    One connection to the database. Reads call the query methods directly; every write runs inside `write`. A store
    opened with `read_only` opens the file read-only and never creates, migrates, or writes it, and reads a database
    that doesn't exist yet as an empty one.
    """

    def __init__(self, path=None, read_only=False):
        self.path = Path(path) if path else DATABASE
        self.read_only = read_only
        # Whether the open write transaction added an event, which `write` saves the id of once it commits.
        self.evented = False
        if read_only:
            self._open_read_only()
            return
        if not self.path.exists():
            refused = change_refused(self.path, "doesn't exist yet")
            if refused:
                raise StoreError(refused)
        if self.path.parent.resolve() == STATE.resolve():
            make_private(STATE)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit, so the only transactions are the ones `write` opens.
        self.db = sqlite3.connect(self.path, timeout=BUSY_MS / 1000, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute(f"PRAGMA busy_timeout = {BUSY_MS}")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            refused = change_refused(self.path, f"has schema {version}, older than this code's {SCHEMA_VERSION}")
            if refused:
                self.db.close()
                raise StoreError(refused)
        self.db.execute("PRAGMA journal_mode = WAL")
        self.version = self._migrate()

    def _open_read_only(self):
        """Opens the database read-only, or an empty one in memory at this code's schema when the file is missing."""
        exists = self.path.exists()
        target = f"{self.path.resolve().as_uri()}?mode=ro" if exists else ":memory:"
        self.db = sqlite3.connect(target, uri=exists, timeout=BUSY_MS / 1000, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute(f"PRAGMA busy_timeout = {BUSY_MS}")
        self.version = self.db.execute("PRAGMA user_version").fetchone()[0] if exists else self._migrate()

    def close(self):
        self.db.close()

    def _migrate(self):
        """Brings an older database up to this code's schema, and returns the version the database holds."""
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return version
        self.db.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have migrated while this one waited for the lock.
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            for index in range(version, SCHEMA_VERSION):
                for statement in MIGRATIONS[index]:
                    self.db.execute(statement)
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return SCHEMA_VERSION

    def schema_newer(self):
        """Whether a process running newer code has moved the database past this code's schema since it opened."""
        self.version = max(self.version, self.db.execute("PRAGMA user_version").fetchone()[0])
        return self.version > SCHEMA_VERSION

    @contextmanager
    def write(self):
        """
        A write transaction. It takes the write lock at once, and commits its rows and their events together. It reads
        the schema's version inside the lock, so a long-running process stops writing once newer code migrates. A
        write inside another joins it, so a caller can commit several public writes together.
        """
        if self.read_only:
            raise StoreError(f"{self.path} is open read-only here, so this command can't write it.")
        if self.db.in_transaction:
            yield self
            return
        self.db.execute("BEGIN IMMEDIATE")
        if self.schema_newer():
            self.db.execute("ROLLBACK")
            raise StoreError(f"{self.path} has schema {self.version}, newer than this agent-desk's {SCHEMA_VERSION}, "
                             "so this process only reads it. Update agent-desk, or restart the service running old "
                             "code.")
        self.evented = False
        try:
            yield self
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        if self.evented:
            self.save_last_event()

    def event_path(self):
        """The file beside the database that holds the newest event id as the last write saved it."""
        return self.path.with_name(f"{self.path.name}-event")

    def save_last_event(self):
        """
        Saves the newest event id in `event_path`, through a rename, so a reader never sees half a number. It reads the
        id under a lock that every save takes, so a save that read a lower id never replaces a later one. The write has
        committed by now, so a failure here only delays a reader until the next write.
        """
        target = self.event_path()
        temporary = None
        try:
            with open(target.with_name(f"{target.name}.lock"), "a", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                handle, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.")
                temporary = Path(name)
                with os.fdopen(handle, "w", encoding="utf-8") as file:
                    file.write(f"{self.last_event()}\n")
                os.replace(temporary, target)
        except OSError:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    # Reads.

    def last_event(self):
        """The newest event's id, which moves whenever anything in the store changes."""
        return self.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def events_since(self, last=0, limit=1000):
        rows = self.db.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (last, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def _row(self, project, number, kind=None):
        row = self.db.execute("SELECT * FROM items WHERE project = ? AND number = ?", (project, number)).fetchone()
        if row is None or (kind and row["kind"] != kind):
            raise StoreError(f"{project} has no {kind or 'item'} #{number}.")
        return row

    def _labels(self, project, number):
        rows = self.db.execute("SELECT name FROM labels WHERE project = ? AND number = ? ORDER BY name",
                               (project, number)).fetchall()
        return [row["name"] for row in rows]

    def _linked(self, project, number, kind, outward):
        """The items a link of `kind` joins to this one: those it points at, or those that point at it."""
        here, there = ("from_number", "to_number") if outward else ("to_number", "from_number")
        rows = self.db.execute(
            f"SELECT items.number, items.title, items.state, items.kind FROM links JOIN items"
            f" ON items.project = links.project AND items.number = links.{there}"
            f" WHERE links.project = ? AND links.{here} = ? AND links.kind = ? ORDER BY items.number",
            (project, number, kind)).fetchall()
        return [{"number": row["number"], "title": row["title"], "state": outer_state(row, None)} for row in rows]

    def review_number(self, project, branch):
        """The newest review of a branch, open ones first, or None."""
        row = self.db.execute(
            "SELECT reviews.number FROM reviews JOIN items ON items.project = reviews.project"
            " AND items.number = reviews.number WHERE reviews.project = ? AND reviews.branch = ?"
            " ORDER BY items.state = 'closed', reviews.number DESC LIMIT 1", (project, branch)).fetchone()
        return row["number"] if row else None

    def review_base(self, project, branch):
        """The base of the branch's newest open review, or None when it has none."""
        row = self.db.execute(
            "SELECT reviews.base FROM reviews JOIN items ON items.project = reviews.project"
            " AND items.number = reviews.number WHERE reviews.project = ? AND reviews.branch = ?"
            " AND items.state != 'closed' ORDER BY reviews.number DESC LIMIT 1", (project, branch)).fetchone()
        return row["base"] if row else None

    def item(self, project, number, kind=None):
        """An issue or review as `--json` prints it, with every field."""
        row = self._row(project, number, kind)
        review = self.db.execute("SELECT * FROM reviews WHERE project = ? AND number = ?",
                                 (project, number)).fetchone()
        comments = self.db.execute("SELECT * FROM comments WHERE project = ? AND number = ? ORDER BY id",
                                   (project, number)).fetchall()
        shaped = {
            "number": row["number"], "kind": row["kind"], "title": row["title"], "body": row["body"],
            "state": outer_state(row, review), "status": row["state"], "stateReason": row["state_reason"],
            "author": {"login": row["author"]}, "labels": [{"name": name} for name in self._labels(project, number)],
            "comments": [{"id": one["id"], "author": {"login": one["author"]}, "body": one["body"],
                          "createdAt": one["created_at"], "path": one["path"], "line": one["line"],
                          "headRefOid": one["head_sha"], "toAgent": bool(one["to_agent"]),
                          "lastEditedAt": one["edited_at"]} for one in comments],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"], "closedAt": row["closed_at"],
            "parent": next(iter(self._linked(project, number, "parent", outward=True)), None),
            "duplicateOf": next(iter(self._linked(project, number, "duplicate", outward=True)), None),
            "subIssues": self._linked(project, number, "parent", outward=False),
            "blockedBy": self._linked(project, number, "blocks", outward=False),
            "blocking": self._linked(project, number, "blocks", outward=True),
            "position": self.position(project, number) if row["state"] in ORDER_STATES else None,
        }
        if review is not None:
            branch = self.branch(project, review["branch"])
            head = (branch or {}).get("head_sha") or review["head_sha"]
            checks = self.newest_checks(project, review["branch"], head)
            shaped.update({
                "headRefName": review["branch"], "baseRefName": review["base"], "headRefOid": review["head_sha"],
                "isDraft": bool(review["draft"]),
                "mergeCommit": {"oid": review["merged_sha"]} if review["merged_sha"] else None,
                "closingIssuesReferences": self._linked(project, number, "fixes", outward=True),
                "checks": checks, "mergeable": mergeable_of(checks, branch, head, review["base"]),
            })
        return shaped

    def items(self, project, kind, state="open", status=None, labels=(), search=None, head=None, base=None,
              limit=30, head_prefix=None, since=None):
        """
        The issues or reviews that match every filter, newest first, as `item` shapes them. `head` matches a review's
        branch whole, and `head_prefix` its start. `since` keeps the items created at or after a time as the store
        writes it, or a date.
        """
        where, values = ["items.project = ?", "items.kind = ?"], [project, kind]
        for label in labels:
            if label in STATUS_LABELS:
                status = STATUS_LABELS[label]
            else:
                where.append("EXISTS (SELECT 1 FROM labels WHERE labels.project = items.project"
                             " AND labels.number = items.number AND labels.name = ?)")
                values.append(label)
        if status:
            where.append("items.state = ?")
            values.append(status)
        if state == "open":
            where.append("items.state != 'closed'")
        elif state == "closed":
            where.append("items.state = 'closed'" + (" AND reviews.merged_sha IS NULL" if kind == "review" else ""))
        elif state == "merged":
            where.append("reviews.merged_sha IS NOT NULL")
        if search:
            where.append("(items.title LIKE ? ESCAPE '\\' OR items.body LIKE ? ESCAPE '\\')")
            pattern = "%" + re.sub(r"([%_\\])", r"\\\1", search) + "%"
            values += [pattern, pattern]
        if head:
            where.append("reviews.branch = ?")
            values.append(head)
        if head_prefix:
            where.append("reviews.branch LIKE ? ESCAPE '\\'")
            values.append(re.sub(r"([%_\\])", r"\\\1", head_prefix) + "%")
        if base:
            where.append("reviews.base = ?")
            values.append(base)
        if since:
            where.append("items.created_at >= ?")
            values.append(since)
        rows = self.db.execute(
            "SELECT items.number FROM items LEFT JOIN reviews ON reviews.project = items.project"
            f" AND reviews.number = items.number WHERE {' AND '.join(where)} ORDER BY items.number DESC LIMIT ?",
            (*values, limit)).fetchall()
        return [self.item(project, row["number"]) for row in rows]

    def order(self, project):
        """
        A project's queued and waiting issues in their start order, first first, each with its number, title, and
        status. An issue that holds no rank yet, as one restored from a backup, follows the ranked ones by number, and
        a read-only store on a database older than the ranks lists them all by number.
        """
        ranked = "rank IS NULL, rank, " if self.version >= RANK_SCHEMA else ""
        rows = self.db.execute(
            "SELECT number, title, state FROM items WHERE project = ? AND kind = 'issue' AND state IN"
            f" ({', '.join('?' * len(ORDER_STATES))}) ORDER BY {ranked}number",
            (project, *ORDER_STATES)).fetchall()
        return [{"number": row["number"], "title": row["title"], "status": row["state"]} for row in rows]

    def position(self, project, number):
        """An issue's place in its project's start order, from 1, or None when it isn't queued or waiting."""
        numbers = [one["number"] for one in self.order(project)]
        return numbers.index(number) + 1 if number in numbers else None

    def state_before(self, project, number, state):
        """The state an item had when it last moved to `state`, or None when it never moved there."""
        row = self.db.execute("SELECT payload FROM events WHERE project = ? AND number = ? AND type = 'state-changed'"
                              " AND json_extract(payload, '$.state') = ? ORDER BY id DESC LIMIT 1",
                              (project, number, state)).fetchone()
        return json.loads(row["payload"])["was"] if row else None

    def queued_at(self, project, number):
        """The id of the event that last moved an issue to queued, or 0 when no event did, as for an imported one."""
        row = self.db.execute("SELECT MAX(id) AS id FROM events WHERE project = ? AND number = ?"
                              " AND type = 'state-changed' AND json_extract(payload, '$.state') = 'queued'",
                              (project, number)).fetchone()
        return row["id"] or 0

    def unasked_queued(self, project):
        """A project's queued issues that no agent run was asked for since they last moved to queued, oldest first."""
        rows = self.db.execute(
            "SELECT number FROM items WHERE project = ? AND kind = 'issue' AND state = 'queued' AND NOT EXISTS ("
            "SELECT 1 FROM events AS asked WHERE asked.project = items.project AND asked.number = items.number"
            " AND asked.type = 'run-requested' AND json_extract(asked.payload, '$.kind') = 'agent'"
            " AND asked.id > COALESCE((SELECT MAX(moved.id) FROM events AS moved WHERE moved.project = items.project"
            " AND moved.number = items.number AND moved.type = 'state-changed'"
            " AND json_extract(moved.payload, '$.state') = 'queued'), 0)) ORDER BY number", (project,)).fetchall()
        return [row["number"] for row in rows]

    def projects(self):
        """Every project the store holds, by slug."""
        return [row["slug"] for row in self.db.execute("SELECT slug FROM projects ORDER BY slug").fetchall()]

    def project_path(self, project):
        row = self.db.execute("SELECT path FROM projects WHERE slug = ?", (project,)).fetchone()
        return row["path"] if row else None

    def review_branch(self, project, number):
        """The head branch of review `number`, or None."""
        row = self.db.execute("SELECT branch FROM reviews WHERE project = ? AND number = ?",
                              (project, number)).fetchone()
        return row["branch"] if row else None

    def review_merged(self, project, number):
        """Whether review `number` merged."""
        row = self.db.execute("SELECT merged_sha FROM reviews WHERE project = ? AND number = ?",
                              (project, number)).fetchone()
        return bool(row and row["merged_sha"])

    def review_ends(self, after=0):
        """The events after `after` that ended a review, oldest first: each `review-merged`, and each `state-changed`
        that closed one."""
        rows = self.db.execute(
            "SELECT events.* FROM events JOIN reviews ON reviews.project = events.project"
            " AND reviews.number = events.number WHERE events.id > ? AND (events.type = 'review-merged'"
            " OR (events.type = 'state-changed' AND json_extract(events.payload, '$.state') = 'closed'))"
            " ORDER BY events.id", (after,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def last_cleanup(self):
        """The id of the newest review end whose cleanup the dispatcher started, or 0."""
        return self.db.execute("SELECT COALESCE(MAX(json_extract(payload, '$.end')), 0) FROM events"
                               " WHERE type = 'cleanup-started'").fetchone()[0]

    def agent_comments(self, after=0):
        """The `comment-added` events after `after` whose comments were sent to the agent, oldest first."""
        rows = self.db.execute("SELECT * FROM events WHERE id > ? AND type = 'comment-added'"
                               " AND json_extract(payload, '$.to_agent') ORDER BY id", (after,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def last_answer(self):
        """The newest `comment-added` event the dispatcher recorded an answer to, or None before its first record."""
        return self.db.execute("SELECT MAX(json_extract(payload, '$.added')) FROM events"
                               " WHERE type = 'comment-answered'").fetchone()[0]

    def asked_reviews(self, project):
        """
        A project's open reviews with a comment sent to the agent that the dispatcher hasn't answered yet, by number:
        those whose `comment-added` event comes after the newest answer, leaving out comments an agent wrote, which the
        dispatcher ignores. The dispatcher answers them oldest first, so the newest answer marks every older one. Before
        its first answer, none waits, since its first start answers only the comments that come after.
        """
        answered = self.last_answer()
        if answered is None:
            return []
        rows = self.db.execute(
            "SELECT DISTINCT events.number FROM events JOIN comments ON comments.id = json_extract(events.payload,"
            " '$.comment') JOIN items ON items.project = events.project AND items.number = events.number"
            " WHERE events.id > ? AND events.project = ? AND events.type = 'comment-added'"
            " AND json_extract(events.payload, '$.to_agent') AND comments.author IS NOT 'agent'"
            " AND items.kind = 'review' AND items.state != 'closed' ORDER BY events.number",
            (answered, project)).fetchall()
        return [row["number"] for row in rows]

    # Writes. Each public method is one transaction; the underscored helpers run inside one.

    def _event(self, project, number, event, **payload):
        self.evented = True
        self.db.execute("INSERT INTO events (project, number, type, payload, author, at) VALUES (?, ?, ?, ?, ?, ?)",
                        (project, number, event, json.dumps(payload, sort_keys=True), author(), now()))

    def _now(self):
        """`now` as this module holds it, so a clock that replaces `store.now` reaches the mixins' writes too."""
        return now()

    def _project(self, project, path=None):
        self.db.execute("INSERT OR IGNORE INTO projects (slug, path) VALUES (?, ?)", (project, path))
        if path:
            self.db.execute("UPDATE projects SET path = ? WHERE slug = ? AND path IS NOT ?", (path, project, path))

    def _touch(self, project, number):
        self.db.execute("UPDATE items SET updated_at = ? WHERE project = ? AND number = ?", (now(), project, number))

    def _set_state(self, project, number, state, reason=None):
        if state not in STATUSES:
            raise StoreError(f"{state} isn't a state; the states are {', '.join(STATUSES)}.")
        row = self._row(project, number)
        if row["state"] == state:
            return
        if state != "closed" and self.db.execute(
                "SELECT 1 FROM reviews WHERE project = ? AND number = ? AND merged_sha IS NOT NULL",
                (project, number)).fetchone():
            raise StoreError(f"Review #{number} merged, so it stays closed.")
        closed = now() if state == "closed" else None
        self.db.execute("UPDATE items SET state = ?, state_reason = ?, closed_at = ?, updated_at = ?"
                        " WHERE project = ? AND number = ?",
                        (state, reason if state == "closed" else None, closed, now(), project, number))
        if state in ORDER_STATES and (row["state"] not in ORDER_STATES or row["rank"] is None):
            self.db.execute("UPDATE items SET rank = ? WHERE project = ? AND number = ?",
                            (self._last_rank(project) + 1, project, number))
        elif state not in ORDER_STATES and row["rank"] is not None:
            self.db.execute("UPDATE items SET rank = NULL WHERE project = ? AND number = ?", (project, number))
        self._event(project, number, "state-changed", state=state, was=row["state"], reason=reason)

    def _last_rank(self, project):
        """The highest rank in a project's start order, or 0 when it is empty."""
        return self.db.execute("SELECT COALESCE(MAX(rank), 0) FROM items WHERE project = ?", (project,)).fetchone()[0]

    def _settle_order(self, project):
        """
        Gives each queued or waiting issue without a rank a place at the end of its project's order, by number, and
        takes the rank off every issue in another state, as after an import or a restore wrote states directly.
        """
        self.db.execute(f"UPDATE items SET rank = NULL WHERE project = ? AND rank IS NOT NULL AND state NOT IN"
                        f" ({', '.join('?' * len(ORDER_STATES))})", (project, *ORDER_STATES))
        unranked = self.db.execute(
            f"SELECT number FROM items WHERE project = ? AND kind = 'issue' AND rank IS NULL AND state IN"
            f" ({', '.join('?' * len(ORDER_STATES))}) ORDER BY number", (project, *ORDER_STATES)).fetchall()
        last = self._last_rank(project)
        for place, row in enumerate(unranked, start=last + 1):
            self.db.execute("UPDATE items SET rank = ? WHERE project = ? AND number = ?",
                            (place, project, row["number"]))

    def _label(self, project, number, name, add):
        """Adds or removes a label. A label that named a workflow state on GitHub moves the state instead."""
        if name in STATUS_LABELS:
            state = STATUS_LABELS[name]
            if add:
                self._set_state(project, number, state)
            elif self._row(project, number)["state"] == state:
                self._set_state(project, number, "open")
            return
        if add:
            done = self.db.execute("INSERT OR IGNORE INTO labels (project, number, name) VALUES (?, ?, ?)",
                                   (project, number, name)).rowcount
        else:
            done = self.db.execute("DELETE FROM labels WHERE project = ? AND number = ? AND name = ?",
                                   (project, number, name)).rowcount
        if done:
            self._event(project, number, "label-added" if add else "label-removed", label=name)

    def _link(self, project, from_number, to_number, kind, add=True):
        if kind not in LINK_KINDS:
            raise StoreError(f"{kind} isn't a link; the links are {', '.join(LINK_KINDS)}.")
        if add:
            self._row(project, from_number)
            self._row(project, to_number)
            if kind in ("parent", "duplicate"):
                self.db.execute("DELETE FROM links WHERE project = ? AND from_number = ? AND kind = ?",
                                (project, from_number, kind))
            done = self.db.execute("INSERT OR IGNORE INTO links (project, from_number, to_number, kind)"
                                   " VALUES (?, ?, ?, ?)", (project, from_number, to_number, kind)).rowcount
        else:
            done = self.db.execute("DELETE FROM links WHERE project = ? AND from_number = ? AND to_number = ?"
                                   " AND kind = ?", (project, from_number, to_number, kind)).rowcount
        if done:
            self._event(project, from_number, "link-added" if add else "link-removed", kind=kind, to=to_number)

    def project(self, project, path=None):
        """Records a project, and its main checkout when given, before anything is filed in it."""
        with self.write():
            self._project(project, path)

    def create(self, project, kind, title, body="", labels=(), number=None, path=None, branch=None, base="main",
               head_sha=None, draft=False, parent=None, blocked_by=(), fixes=()):
        """
        Files an issue or opens a review with its labels and links, and returns its number. An import passes the
        number the item has. A label or link that fails files nothing, so a create never stops halfway.
        """
        if not title.strip():
            raise StoreError("An item needs a title.")
        if kind == "review" and not branch:
            raise StoreError("A review needs a branch.")
        with self.write():
            self._project(project, path)
            if number is None:
                number = self.db.execute("SELECT next_number FROM projects WHERE slug = ?", (project,)).fetchone()[0]
            elif self.db.execute("SELECT 1 FROM items WHERE project = ? AND number = ?",
                                 (project, number)).fetchone():
                raise StoreError(f"{project} already has #{number}.")
            self.db.execute("UPDATE projects SET next_number = MAX(next_number, ?) WHERE slug = ?",
                            (number + 1, project))
            stamp = now()
            self.db.execute("INSERT INTO items (project, number, kind, title, body, author, created_at, updated_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (project, number, kind, title, body, author(), stamp, stamp))
            if kind == "review":
                self.db.execute("INSERT INTO reviews (project, number, branch, base, head_sha, draft)"
                                " VALUES (?, ?, ?, ?, ?, ?)", (project, number, branch, base, head_sha, int(draft)))
            self._event(project, number, "item-created", kind=kind, title=title)
            for label in labels:
                self._label(project, number, label, add=True)
            if parent:
                self._link(project, number, parent, "parent")
            for blocker in blocked_by:
                self._link(project, blocker, number, "blocks")
            for fixed in fixes:
                self._link(project, number, fixed, "fixes")
        return number

    def edit(self, project, number, title=None, body=None, add_labels=(), remove_labels=(), status=None):
        with self.write():
            row = self._row(project, number)
            changed = {key: value for key, value in (("title", title), ("body", body))
                       if value is not None and value != row[key]}
            for key, value in changed.items():
                self.db.execute(f"UPDATE items SET {key} = ? WHERE project = ? AND number = ?",
                                (value, project, number))
            if changed:
                self._event(project, number, "item-edited", fields=sorted(changed))
            for label in remove_labels:
                self._label(project, number, label, add=False)
            for label in add_labels:
                self._label(project, number, label, add=True)
            if status:
                self._set_state(project, number, status)
            self._touch(project, number)

    def set_state(self, project, number, state, reason=None):
        with self.write():
            self._set_state(project, number, state, reason)

    def move(self, project, number, where):
        """
        Moves a queued or waiting issue in its project's start order, `up` one place, `down` one place, to the `top`,
        or to a place given as a number from 1, with a `queue-moved` event naming its places before and after, and
        returns its place, from 1. A place past the end moves it last. A move past either end, or to the place it holds,
        leaves the order as it is and writes nothing. Raises StoreError for an issue in any other state.
        """
        place = isinstance(where, int) and not isinstance(where, bool)
        if place and where < 1:
            raise StoreError(f"{where} isn't a place; the start order's places count from 1.")
        if not place and where not in ("up", "down", "top"):
            raise StoreError(f"{where} isn't a move; the moves are up, down, top, and a place from 1.")
        with self.write():
            row = self._row(project, number, "issue")
            if row["state"] not in ORDER_STATES:
                raise StoreError(f"#{number} is {row['state']}, and only a queued or waiting issue has a place in the "
                                 "start order.")
            self._settle_order(project)
            numbers = [one["number"] for one in self.order(project)]
            was = numbers.index(number)
            to = min(where, len(numbers)) - 1 if place else \
                {"up": max(was - 1, 0), "down": min(was + 1, len(numbers) - 1), "top": 0}[where]
            if to == was:
                return was + 1
            numbers.insert(to, numbers.pop(was))
            for place, one in enumerate(numbers, start=1):
                self.db.execute("UPDATE items SET rank = ? WHERE project = ? AND number = ?", (place, project, one))
            self._event(project, number, "queue-moved", to=to + 1, was=was + 1)
        return to + 1

    def link(self, project, from_number, to_number, kind, add=True):
        """Joins two items: a `parent` link from a sub-issue to its one parent, a `blocks` link from a blocker to what
        it blocks, a `fixes` link from a review to an issue its merge closes, or a `duplicate` link to one original."""
        with self.write():
            self._link(project, from_number, to_number, kind, add)

    def comment(self, project, number, body, to_agent=False, path=None, line=None, head_sha=None, quotes=()):
        """
        Adds a comment, on a line of a file for a review's line comment, and returns its id. A line comment without a
        head is made on the review's head as the store last saw it. A comment sent to the agent always mentions
        `@agent`, which the store puts in front of a body without the mention. It marks the line comments it `quotes`
        as sent by it, and refuses one already sent, so no line comment goes twice.
        """
        if not body.strip():
            raise StoreError("A comment needs a body.")
        if to_agent and not MENTION.search(body):
            body = f"@agent {body}"
        if (path is None) != (line is None) or (line is not None and line < 1):
            raise StoreError("A line comment names a file with --path and a line from 1 with --line.")
        if quotes and (not to_agent or path is not None):
            raise StoreError("Only a comment sent to the agent with --to-agent quotes line comments.")
        with self.write():
            row = self._row(project, number)
            if path is not None and row["kind"] != "review":
                raise StoreError(f"#{number} is an issue, and only a review's diff takes line comments.")
            if path is not None and head_sha is None:
                review = self.db.execute("SELECT branch, head_sha FROM reviews WHERE project = ? AND number = ?",
                                         (project, number)).fetchone()
                head_sha = self.branch_head(project, review["branch"]) or review["head_sha"]
            comment = self.db.execute(
                "INSERT INTO comments (project, number, author, body, path, line, head_sha, to_agent, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project, number, author(), body, path, line, head_sha, int(to_agent), now())).lastrowid
            for quoted in dict.fromkeys(quotes):
                if not self.db.execute("UPDATE comments SET sent_in = ? WHERE id = ? AND project = ? AND number = ?"
                                       " AND path IS NOT NULL AND sent_in IS NULL",
                                       (comment, quoted, project, number)).rowcount:
                    raise StoreError(f"Comment {quoted} isn't a line comment on #{number} waiting to be sent.")
            self._touch(project, number)
            self._event(project, number, "comment-added", comment=comment, to_agent=bool(to_agent))
        return comment

    def comment_of(self, comment):
        """One comment's row, with its project, item, author, and body, or None when no comment has that id."""
        row = self.db.execute("SELECT * FROM comments WHERE id = ?", (comment,)).fetchone()
        return dict(row) if row else None

    def edit_comment(self, project, comment, body, note=None):
        """
        Replaces the body of a comment its writer wrote, marks it edited, and writes a `comment-edited` event, which
        asks the dispatcher for nothing. It refuses another writer's comment, and one sent to the agent, which records
        what the agent read. A body left as it was writes nothing. An edit by the agent or the desk needs a `note`
        saying what changed, and any note goes into the body, so the comment shows it was edited.
        """
        if not body.strip():
            raise StoreError("A comment needs a body.")
        with self.write():
            row = self.db.execute("SELECT * FROM comments WHERE id = ? AND project = ?", (comment, project)).fetchone()
            if row is None:
                raise StoreError(f"{project} has no comment {comment}.")
            if row["author"] != author():
                raise StoreError(f"Comment {comment} is by {row['author'] or 'someone else'}, and only its writer "
                                 "edits it.")
            if row["to_agent"] or row["sent_in"] is not None:
                raise StoreError(f"Comment {comment} went to the agent, so it stays as the agent read it.")
            if row["body"] == body:
                return
            note = " ".join((note or "").split())
            if not note and row["author"] != "you":
                raise StoreError(f"An edit by {row['author']} says what it changed with a note, which the comment "
                                 "then shows.")
            when = now()
            if note:
                body = with_edit_note(body, f"**Edited {when[:10]} {when[11:16]} UTC:** {note}")
            self.db.execute("UPDATE comments SET body = ?, edited_at = ? WHERE id = ?", (body, when, comment))
            self._touch(project, row["number"])
            self._event(project, row["number"], "comment-edited", comment=comment)

    def review_update(self, project, number, head_sha=None, draft=None):
        """Records a review's new head, or that it left or became a draft."""
        with self.write():
            self._row(project, number, "review")
            if head_sha is not None:
                done = self.db.execute("UPDATE reviews SET head_sha = ? WHERE project = ? AND number = ?"
                                       " AND head_sha IS NOT ?", (head_sha, project, number, head_sha)).rowcount
                if done:
                    self._event(project, number, "review-head-moved", head=head_sha)
            if draft is not None:
                done = self.db.execute("UPDATE reviews SET draft = ? WHERE project = ? AND number = ? AND draft != ?",
                                       (int(draft), project, number, int(draft))).rowcount
                if done:
                    self._event(project, number, "review-drafted" if draft else "review-ready")
            self._touch(project, number)

    def merge(self, project, number, sha):
        """Records that a review merged as commit `sha`. A merge into main closes each issue the review fixes."""
        with self.write():
            self._row(project, number, "review")
            review = self.db.execute("SELECT * FROM reviews WHERE project = ? AND number = ?",
                                     (project, number)).fetchone()
            if review["merged_sha"]:
                raise StoreError(f"Review #{number} already merged as {review['merged_sha']}.")
            self.db.execute("UPDATE reviews SET merged_sha = ?, draft = 0 WHERE project = ? AND number = ?",
                            (sha, project, number))
            self._event(project, number, "review-merged", sha=sha, base=review["base"])
            self._set_state(project, number, "closed", "merged")
            if review["base"] == "main":
                for fixed in self._linked(project, number, "fixes", outward=True):
                    self._set_state(project, fixed["number"], "closed", "completed")

    def cleanup_started(self, project, number, end, unit):
        """Records that the dispatcher started the cleanup that event `end`, the end of review `number`, asks for."""
        with self.write():
            self._event(project, number, "cleanup-started", end=end, unit=unit)

    def comment_answered(self, project, number, added, answer, comment=None, run=None):
        """
        Records that the dispatcher answered comment `comment`, sent to the agent on item `number` by event `added`:
        `run` when it asked for run `run`, `note` when it posted the note saying the review's branch has no agent,
        `ignored` when an agent sent it, and `none` when its comment or item is gone. Its first start records `start`
        with the store's newest event and no comment, so it answers only the comments that come after.
        """
        with self.write():
            self._event(project, number, "comment-answered", added=added, comment=comment, answer=answer, run=run)


# The project and checkout a command runs in.

def repo_here():
    """
    The project the command is for, as owner/repo: GH_REPO, the checkout's origin when that is a host address, so a
    project renamed on its host follows the new name, or else the project the projects file records at the checkout
    it runs in or at that checkout's main checkout, as for a project whose origin is a path or that has none.
    """
    repo = os.environ.get("GH_REPO")
    if repo:
        return repo
    url = git("remote", "get-url", "origin")
    match = re.fullmatch(r"(?:[^/:@]+@[^:]+:|[a-z+]+://[^/]+/)(.+?)(?:\.git)?/?", url or "")
    if match:
        return match.group(1)
    repo = listed_here()
    if repo:
        return repo
    raise StoreError("No project here: run this from a checkout agent-desk install recorded or one whose origin is a "
                     "host address, or pass --repo <owner/repo>.")


def listed_here():
    """The project the projects file records at the current checkout or at its main checkout, or None."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    here = {git("rev-parse", "--show-toplevel"), str(Path(common).parent) if common else None} - {None}
    try:
        lines = PROJECTS.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1] in here:
            return parts[0]
    return None


def git(*args):
    """A git command's output in the current directory, or None when it fails."""
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def checkout_of(repo):
    """The project's main checkout: the one agent-desk install recorded, or the one this command runs in."""
    try:
        for line in PROJECTS.read_text().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0] == repo:
                return parts[1]
    except OSError:
        pass
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    return str(Path(common).parent) if common else None


if __name__ == "__main__":
    # The command line lives in store_cli.py; a process started before it moved there, such as a web server still
    # running older code, runs this file with the same arguments.
    import store_cli
    sys.exit(store_cli.main(sys.argv[1:]))
