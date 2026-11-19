"""
The store's schema: the migrations that build its database, the values its status columns take, the columns an
import copies, the way it writes a time, and the error it raises. `lib/store.py` imports each name back, so
`store.MIGRATIONS` and the rest read as before, and the store's mixins in `lib/store_import.py`,
`lib/store_checks.py`, `lib/store_runs.py`, and `lib/store_gpu.py` import them from here without importing the store.
"""

import calendar
import time

STATUSES = ("open", "queued", "in-progress", "waiting", "needs-design", "on-hold", "closed")
# The GitHub labels that named a workflow state, and the state each one stands for.
STATUS_LABELS = {"agent-ready": "queued", "in-progress": "in-progress", "waiting": "waiting",
                 "needs-design": "needs-design", "on-hold": "on-hold"}
# The workflow states whose issues hold a place in their project's start order, `items.rank`.
ORDER_STATES = ("queued", "waiting")
LINK_KINDS = ("parent", "blocks", "fixes", "duplicate")
# A run waits as `requested` and works as `running`, then ends one of these ways. `stopped` is a run that ended early
# because its agent ended its turn while a task it started still ran, `lost` a unit that vanished without recording its
# end, and `ignored` a request an agent wrote.
RUN_ENDINGS = ("succeeded", "stopped", "failed", "cancelled", "timed-out", "lost", "ignored")
# A check waits as `queued` and works as `running`, then ends one of these ways. `superseded` is a check whose head its
# branch left behind, and `cancelled` one whose project has no `test` command any more.
CHECK_ENDINGS = ("passed", "failed", "timed-out", "superseded", "cancelled", "lost")

# The gates that hold a requested run or a queued check, in the order the dispatcher tries them: the desk's mode, the
# weekly usage limit, its issue on hold, an open blocker, another issue's serial slot, a free slot of the run's kind,
# its target's lock, and spare memory.
WAIT_GATES = ("mode", "usage", "hold", "blocker", "serial", "slot", "lock", "memory")

# The ends of a check that leave its head unchecked: a newer head took its place, or the project stopped testing.
UNCHECKED = ("superseded", "cancelled")
# The ends of a check that fail its head.
CHECK_FAILURES = ("failed", "timed-out", "lost")

# The columns of `items` and `reviews` an import copies from the host.
IMPORTED_ITEM = ("kind", "title", "body", "state", "state_reason", "author", "created_at", "updated_at", "closed_at")
IMPORTED_REVIEW = ("branch", "base", "head_sha", "draft", "merged_sha", "merged_commits")
IMPORTED_COMMENT = ("number", "author", "body", "path", "line", "head_sha", "to_agent", "created_at")
# The columns of `runs` that hold what a run's agent reported, as `run_result` writes them and `import_run` fills them.
RUN_RESULT = ("model", "effort", "minutes", "turns", "cost", "review", "labels", "start")

# One list of statements per schema version, applied in order. Append a list to change the schema; never edit one that
# has shipped.
MIGRATIONS = [
    [
        """CREATE TABLE projects (
            slug TEXT PRIMARY KEY, path TEXT, next_number INTEGER NOT NULL DEFAULT 1, mode TEXT)""",
        """CREATE TABLE items (
            project TEXT NOT NULL, number INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'open', state_reason TEXT, author TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT,
            PRIMARY KEY (project, number))""",
        "CREATE INDEX items_state ON items (project, kind, state)",
        """CREATE TABLE labels (
            project TEXT NOT NULL, number INTEGER NOT NULL, name TEXT NOT NULL,
            PRIMARY KEY (project, number, name))""",
        "CREATE INDEX labels_name ON labels (project, name)",
        """CREATE TABLE links (
            project TEXT NOT NULL, from_number INTEGER NOT NULL, to_number INTEGER NOT NULL, kind TEXT NOT NULL,
            PRIMARY KEY (project, from_number, to_number, kind))""",
        "CREATE INDEX links_to ON links (project, to_number, kind)",
        """CREATE TABLE reviews (
            project TEXT NOT NULL, number INTEGER NOT NULL, branch TEXT NOT NULL, base TEXT NOT NULL,
            head_sha TEXT, draft INTEGER NOT NULL DEFAULT 0, merged_sha TEXT,
            PRIMARY KEY (project, number))""",
        "CREATE INDEX reviews_branch ON reviews (project, branch)",
        """CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, number INTEGER NOT NULL, author TEXT,
            body TEXT NOT NULL, path TEXT, line INTEGER, head_sha TEXT, to_agent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL)""",
        "CREATE INDEX comments_item ON comments (project, number)",
        """CREATE TABLE checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, number INTEGER NOT NULL,
            head_sha TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, log_path TEXT, started_at TEXT,
            ended_at TEXT)""",
        "CREATE INDEX checks_item ON checks (project, number, head_sha)",
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, kind TEXT NOT NULL, target TEXT, unit TEXT,
            status TEXT NOT NULL, session TEXT, started_at TEXT, ended_at TEXT, exit_code INTEGER)""",
        "CREATE INDEX runs_status ON runs (status)",
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, number INTEGER, type TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}', author TEXT, at TEXT NOT NULL)""",
        """CREATE TABLE assets (
            hash TEXT PRIMARY KEY, project TEXT NOT NULL, number INTEGER, path TEXT NOT NULL)""",
    ],
    [
        # The id a comment has on the host it was imported from, so a rerun of the import finds it again.
        "ALTER TABLE comments ADD COLUMN remote_id TEXT",
        "CREATE UNIQUE INDEX comments_remote ON comments (project, remote_id) WHERE remote_id IS NOT NULL",
    ],
    [
        # A branch check names its branch, which may have no review yet, and its attempt. Nothing wrote the first
        # `checks` table, so it is made again.
        "DROP TABLE checks",
        """CREATE TABLE checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, branch TEXT NOT NULL, number INTEGER,
            head_sha TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
            log_path TEXT, started_at TEXT, ended_at TEXT)""",
        "CREATE INDEX checks_branch ON checks (project, branch, head_sha)",
        "CREATE INDEX checks_status ON checks (status)",
    ],
    [
        # Each watched branch's head, as its newest `branch-moved` event set it, and whether that head merges with its
        # review's base at `base_sha`: MERGEABLE, CONFLICTING, or NULL until the dispatcher tries it.
        """CREATE TABLE branches (
            project TEXT NOT NULL, branch TEXT NOT NULL, head_sha TEXT NOT NULL, moved_at TEXT NOT NULL,
            base TEXT, base_sha TEXT, mergeable TEXT,
            PRIMARY KEY (project, branch))""",
        """INSERT INTO branches (project, branch, head_sha, moved_at)
            SELECT project, json_extract(payload, '$.branch'), json_extract(payload, '$.head'), at FROM events
            WHERE id IN (SELECT MAX(id) FROM events WHERE type = 'branch-moved'
                         GROUP BY project, json_extract(payload, '$.branch'))
            AND json_extract(payload, '$.head') IS NOT NULL""",
    ],
    [
        # What a run's agent reported when it finished, which lib/run_log.py records: the model and effort it ran
        # with, its length in minutes, its turns, its cost in dollars at API list prices, the review it opened or
        # updated, an agent run's issue labels as a JSON list, and why it started.
        "ALTER TABLE runs ADD COLUMN model TEXT",
        "ALTER TABLE runs ADD COLUMN effort TEXT",
        "ALTER TABLE runs ADD COLUMN minutes REAL",
        "ALTER TABLE runs ADD COLUMN turns INTEGER",
        "ALTER TABLE runs ADD COLUMN cost REAL",
        "ALTER TABLE runs ADD COLUMN review INTEGER",
        "ALTER TABLE runs ADD COLUMN labels TEXT",
        "ALTER TABLE runs ADD COLUMN start TEXT",
        "CREATE INDEX runs_review ON runs (project, review)",
    ],
    [
        # How many commits a rebase-merge wrote onto its base, ending at merged_sha; NULL when the merge wrote one
        # commit, such as a merge commit or a squash, whose diff git finds from merged_sha alone.
        "ALTER TABLE reviews ADD COLUMN merged_commits INTEGER",
    ],
    [
        # Each label's color on the host, as six hex digits, which `agent-desk import` copies and the web page draws.
        """CREATE TABLE label_colors (
            project TEXT NOT NULL, name TEXT NOT NULL, color TEXT NOT NULL,
            PRIMARY KEY (project, name))""",
    ],
    [
        # The id of the comment sent to the agent that quoted a line comment, which Send to agent writes with that
        # comment; 0 for a line comment imported from a host, where the agent read it, and NULL until one is sent.
        # A local line comment counts as sent by the first later comment that quoted line comments, as Send to
        # agent's does under its `**Line comments**` heading.
        "ALTER TABLE comments ADD COLUMN sent_in INTEGER",
        "UPDATE comments SET sent_in = 0 WHERE path IS NOT NULL AND remote_id IS NOT NULL",
        """UPDATE comments SET sent_in = (
            SELECT MIN(sent.id) FROM comments AS sent WHERE sent.project = comments.project
            AND sent.number = comments.number AND sent.to_agent = 1 AND sent.id > comments.id
            AND sent.body LIKE '%**Line comments**%')
            WHERE path IS NOT NULL AND remote_id IS NULL""",
    ],
    [
        # When a comment's author last edited it, as `Store.edit_comment` writes it; NULL for a comment never edited.
        "ALTER TABLE comments ADD COLUMN edited_at TEXT",
    ],
    [
        # An issue's place in its project's start order while it is queued or waiting, lowest first, and NULL in
        # every other state. The issues queued or waiting already join in the order they last entered either state,
        # by their newest `state-changed` event from outside both, then by number.
        "ALTER TABLE items ADD COLUMN rank INTEGER",
        """UPDATE items SET rank = (
            SELECT ordered.place FROM (
                SELECT entered.project, entered.number,
                    ROW_NUMBER() OVER (PARTITION BY entered.project ORDER BY entered.joined, entered.number) AS place
                FROM (SELECT one.project, one.number, COALESCE((
                    SELECT MAX(events.id) FROM events WHERE events.project = one.project
                    AND events.number = one.number AND events.type = 'state-changed'
                    AND json_extract(events.payload, '$.state') IN ('queued', 'waiting')
                    AND COALESCE(json_extract(events.payload, '$.was'), '') NOT IN ('queued', 'waiting')), 0) AS joined
                    FROM items AS one WHERE one.kind = 'issue' AND one.state IN ('queued', 'waiting')) AS entered
            ) AS ordered WHERE ordered.project = items.project AND ordered.number = items.number)
            WHERE kind = 'issue' AND state IN ('queued', 'waiting')""",
    ],
    [
        # Why the dispatcher holds a requested run or a queued check, which the board shows on its card: the gate, one
        # of WAIT_GATES, and a short reason that reads after "waits for". NULL while nothing holds it, and cleared when
        # it starts or ends. Writing them adds no event, so a reason that changes never rebuilds the board.
        "ALTER TABLE runs ADD COLUMN wait_gate TEXT",
        "ALTER TABLE runs ADD COLUMN wait_reason TEXT",
        "ALTER TABLE checks ADD COLUMN wait_gate TEXT",
        "ALTER TABLE checks ADD COLUMN wait_reason TEXT",
    ],
    [
        # Each `agent-desk gpu` call, as lib/gpu_log.py records it: the run AGENT_DESK_RUN named, or NULL outside a
        # run, the folder it ran in, whether it held every slot, which checkouts held the slots when it had to wait,
        # and when it asked for its slots, got them, and released them, in epoch seconds with their fractions. `got`
        # stays NULL for a call that ended while it waited, and `released` for one killed outright, which no total
        # counts. A run's `gpu_queued` and `gpu_held` sum its released calls in minutes, NULL while it made none.
        """CREATE TABLE gpu_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run INTEGER, checkout TEXT NOT NULL,
            exclusive INTEGER NOT NULL DEFAULT 0, held_by TEXT, asked REAL NOT NULL, got REAL, released REAL)""",
        "CREATE INDEX gpu_calls_run ON gpu_calls (run)",
        "CREATE INDEX gpu_calls_asked ON gpu_calls (asked)",
        "ALTER TABLE runs ADD COLUMN gpu_queued REAL",
        "ALTER TABLE runs ADD COLUMN gpu_held REAL",
    ],
]
SCHEMA_VERSION = len(MIGRATIONS)
# The first schema with `items.rank`. A read-only store on an older database lists the start order by number.
RANK_SCHEMA = 10
# The first schema with `gpu_calls`. A read-only store on an older database lists no GPU waits.
GPU_SCHEMA = 12


class StoreError(Exception):
    """A problem the command line reports in one line, such as an item that doesn't exist."""


def store_time(epoch):
    """Epoch seconds as the store writes a time."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def epoch_of(written):
    """A time as the store writes it, in epoch seconds."""
    return calendar.timegm(time.strptime(written, "%Y-%m-%dT%H:%M:%SZ"))
