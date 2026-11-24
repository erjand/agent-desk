"""
The store's runs: the reads of `runs` and the requests that asked for them, and the writes that record a run from
requested through started and ended to the result its agent reported. `Store` in `lib/store.py` inherits `RunsMixin`,
whose methods use the store's `db`, `write`, `_event`, `_now`, and `_project`.
"""

import json
import os
import time

from store_schema import RUN_ENDINGS, WAIT_GATES, StoreError

# How long a run that failed, timed out, or was lost stays on the board, as a failed Actions run did.
FAILED_SECONDS = 24 * 3600


def labelled(kind, target, env):
    """
    Whether an agent request is a fresh start of an issue, as an agent-ready label, Retry or Continue of such a run, and
    the dispatcher's wake ask for it, which Manual holds until someone picks its issue.
    """
    return kind == "agent" and target.isdigit() and env.get("EVENT") == "issues"


class RunsMixin:
    """The methods of `Store` that read and write `runs`."""

    def author_run(self, project):
        """The current run when it belongs to the project and can write posts."""
        run = os.environ.get("AGENT_DESK_RUN", "")
        if not run.isascii() or not run.isdecimal() or len(run) > 18:
            return None
        row = self.db.execute("SELECT id FROM runs WHERE id = ? AND project = ? AND status = 'running'"
                              " AND kind IN ('agent', 'chore', 'fix')",
                              (int(run), project)).fetchone()
        return row["id"] if row else None

    # Reads.

    def run(self, run):
        row = self.db.execute("SELECT * FROM runs WHERE id = ?", (run,)).fetchone()
        if row is None:
            raise StoreError(f"There is no run {run}.")
        return dict(row)

    def runs(self, statuses):
        """The runs in any of `statuses`, oldest first."""
        marks = ", ".join("?" * len(statuses))
        rows = self.db.execute(f"SELECT * FROM runs WHERE status IN ({marks}) ORDER BY id", tuple(statuses)).fetchall()
        return [dict(row) for row in rows]

    def failed_runs(self, project, since):
        """A project's runs that failed, timed out, or were lost since `since`, a time as the store writes it, each
        only while no later run has asked for the same target."""
        rows = self.db.execute(
            "SELECT * FROM runs AS ended WHERE project = ? AND status IN ('failed', 'timed-out', 'lost')"
            " AND ended_at >= ? AND NOT EXISTS (SELECT 1 FROM runs AS later WHERE later.project = ended.project"
            " AND later.kind = ended.kind AND later.target = ended.target AND later.id > ended.id"
            " AND later.status != 'ignored') ORDER BY id", (project, since)).fetchall()
        return [dict(row) for row in rows]

    def live_runs(self, project, now=None):
        """
        The project's runs as the board reads them, oldest first: those that wait for a slot or work in a unit, then
        those that failed, timed out, or were lost within FAILED_SECONDS and that no later run of their target answered.
        Each says whether it is a fresh start, as `labelled` reads it, which Manual holds until Start, one that waits
        names the gate that holds it and why, as `run_waits` recorded them, one that started names the `account` it
        billed, one that works has the `limit` in minutes its unit runs under, as `run_started` recorded it, and one
        whose GPU calls ended has their totals, as `gpu_released` sums them.
        """
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((now or time.time()) - FAILED_SECONDS))
        rows = [row for row in self.runs(("requested", "running")) if row["project"] == project]
        listed = []
        for row in rows + self.failed_runs(project, since):
            asked = self.request_of(row["id"])
            env = (asked["payload"].get("env") if asked else None) or {}
            one = {"id": row["id"], "kind": row["kind"], "target": row["target"], "status": row["status"],
                   "unit": row["unit"], "started_at": row["started_at"], "ended_at": row["ended_at"],
                   "benchmark": bool(asked and asked["payload"].get("benchmark")),
                   "labelled": labelled(row["kind"], row["target"], env)}
            # A read-only store on a database older than the wait columns has none.
            if row["status"] == "requested" and row.get("wait_gate"):
                one.update(gate=row["wait_gate"], reason=row["wait_reason"])
            # The Claude account a run that started billed; a row from before the column, or on an older database,
            # billed the plan.
            if row["status"] != "requested":
                one["account"] = row.get("account") or "subscription"
            # Minutes its GPU calls queued and held their slots, once one ended; none on an older database.
            if row.get("gpu_held") is not None:
                one.update(gpu_queued=row["gpu_queued"], gpu_held=row["gpu_held"])
            # The time limit a working run's unit runs under; none for a run started before its event recorded one.
            limit = self.run_limit(row["id"]) if row["status"] == "running" else None
            if limit is not None:
                one["limit"] = limit
            listed.append(one)
        return listed

    def chore_runs(self, project):
        """
        A project's chore runs the dispatcher took, each with `requested_at`, when its request came, oldest first by
        when each started, or was asked for while it waits. A run imported from the old log has a higher id than the
        newer runs the dispatcher recorded before the import, so the order goes by time.
        """
        rows = self.db.execute(
            "SELECT runs.*, (SELECT at FROM events WHERE type = 'run-requested'"
            " AND json_extract(payload, '$.run') = runs.id) AS requested_at FROM runs"
            " WHERE project = ? AND kind = 'chore' AND status != 'ignored'"
            " ORDER BY COALESCE(started_at, requested_at), id", (project,)).fetchall()
        return [dict(row) for row in rows]

    def run_limit(self, run):
        """The time limit in minutes that a run's `run-started` event recorded, or None."""
        row = self.db.execute("SELECT json_extract(payload, '$.minutes') FROM events WHERE type = 'run-started'"
                              " AND json_extract(payload, '$.run') = ? ORDER BY id LIMIT 1", (run,)).fetchone()
        return row[0] if row and isinstance(row[0], int) else None

    def request_of(self, run):
        """The `run-requested` event that asked for a run, or None."""
        row = self.db.execute("SELECT * FROM events WHERE type = 'run-requested'"
                              " AND json_extract(payload, '$.run') = ? ORDER BY id LIMIT 1", (run,)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    # Writes. Each public method is one transaction.

    def request_run(self, project, kind, target, benchmark=False, env=None, path=None):
        """
        Asks the dispatcher for a run, and returns the run's id. The run waits as `requested`, and its `run-requested`
        event carries what the dispatcher needs to start it.
        """
        with self.write():
            self._project(project, path)
            run = self.db.execute("INSERT INTO runs (project, kind, target, status) VALUES (?, ?, ?, 'requested')",
                                  (project, kind, target)).lastrowid
            number = int(target) if target.isdigit() else None
            self._event(project, number, "run-requested", run=run, kind=kind, target=target, benchmark=benchmark,
                        env=env or {})
        return run

    def request_again(self, run):
        """Asks for a run's kind and target again, with the benchmark limit and environment its request had, and
        returns the new run's id. It picks nothing, so in Manual a run an agent-ready label asked for waits again until
        Start picks its issue."""
        row, asked = self.run(run), self.request_of(run)
        payload = asked["payload"] if asked else {}
        return self.request_run(row["project"], row["kind"], row["target"], bool(payload.get("benchmark")),
                                payload.get("env") or {})

    def run_waits(self, run, gate, reason):
        """
        Records why the dispatcher holds a requested run: `gate`, one of WAIT_GATES, and `reason`, which reads after
        "waits for", as the board shows it. Adds no event, so the board reads it on its next read. Returns False when
        the run no longer waits.
        """
        if gate not in WAIT_GATES:
            raise StoreError(f"{gate} isn't a gate; a run waits on {', '.join(WAIT_GATES)}.")
        with self.write():
            done = self.db.execute("UPDATE runs SET wait_gate = ?, wait_reason = ? WHERE id = ? AND status = 'requested'",
                                   (gate, reason, run)).rowcount
        return bool(done)

    def run_started(self, run, unit, minutes=None, account="subscription"):
        """Marks a requested run as running in `unit`, billing `account`, clearing why it waited, and returns False when
        it no longer waits. Its `run-started` event records `minutes`, the time limit the dispatcher gave its unit,
        when given."""
        with self.write():
            row = self.run(run)
            done = self.db.execute("UPDATE runs SET status = 'running', unit = ?, started_at = ?, account = ?,"
                                   " wait_gate = NULL, wait_reason = NULL WHERE id = ? AND status = 'requested'",
                                   (unit, self._now(), account, run)).rowcount
            if done:
                limit = {} if minutes is None else {"minutes": minutes}
                self._event(row["project"], None, "run-started", run=run, unit=unit, **limit)
        return bool(done)

    def run_ended(self, run, status, exit_code=None, waiting=False):
        """Records how a requested or running run ended, or with `waiting` only a requested one, clearing why it
        waited, and returns False when it had ended already or, with `waiting`, works."""
        if status not in RUN_ENDINGS:
            raise StoreError(f"{status} isn't how a run ends; a run ends {', '.join(RUN_ENDINGS)}.")
        ends = "('requested')" if waiting else "('requested', 'running')"
        with self.write():
            row = self.run(run)
            done = self.db.execute("UPDATE runs SET status = ?, ended_at = ?, exit_code = ?, wait_gate = NULL,"
                                   f" wait_reason = NULL WHERE id = ? AND status IN {ends}",
                                   (status, self._now(), exit_code, run)).rowcount
            if done:
                self._event(row["project"], None, "run-ended", run=run, status=status, exit=exit_code)
        return bool(done)

    def run_session(self, run, session):
        """Records the Claude Code session a run runs, as it starts, so a run cut off before it reports a result still
        names it."""
        with self.write():
            self.run(run)
            self.db.execute("UPDATE runs SET session = ? WHERE id = ?", (session, run))

    def run_result(self, run, model=None, effort=None, minutes=None, turns=None, cost=None, review=None, labels=None,
                   start=None, session=None, reported=None):
        """
        Records what a run's agent reported when it finished, with a `run-result` event that names the review. With
        `reported`, the total cost its Claude Code session reported, it keeps that total and counts the run's `cost` as
        what the total grew by since the last earlier run of the same session that reported one, as lib/budget.py
        counts a Chat turn: a run resumed after one that stopped early counts only its own spend, and one resumed after
        a run cut off before it reported keeps that run's spend too. A run whose session has no earlier report, or
        whose total shrank, counts the whole total. `session` replaces the one `run_session` recorded.
        """
        with self.write():
            row = self.run(run)
            session = session or row["session"]
            if reported is not None:
                before = session and self.db.execute(
                    "SELECT reported FROM runs WHERE session = ? AND id < ? AND reported IS NOT NULL"
                    " ORDER BY id DESC LIMIT 1", (session, run)).fetchone()
                grew = bool(before) and reported >= before[0]
                cost = round(reported - before[0] if grew else reported, 2)
            self.db.execute("UPDATE runs SET model = ?, effort = ?, minutes = ?, turns = ?, cost = ?, review = ?,"
                            " labels = ?, start = ?, session = ?, reported = ? WHERE id = ?",
                            (model, effort, minutes, turns, cost, review,
                             None if labels is None else json.dumps(labels), start, session, reported, run))
            self._event(row["project"], review, "run-result", run=run)

    def run_posting_identity(self, run, model, provider, version):
        """Records the launcher and model once for the posts from this run."""
        with self.write():
            row = self.run(run)
            kept = self.db.execute("UPDATE runs SET posting_model = ?, posting_provider = ?, posting_version = ?"
                                   " WHERE id = ? AND posting_model IS NULL", (model, provider, version, run))
            if kept.rowcount:
                self._event(row["project"], None, "run-posting-identity", run=run)
