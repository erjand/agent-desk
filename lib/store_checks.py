"""
The store's watched branches and branch checks: the reads of `branches` and `checks`, and the writes that record a
branch's move, its trial merge, and each check from queued to ended. `Store` in `lib/store.py` inherits
`ChecksMixin`, whose methods use the store's `db`, `write`, `_event`, `_now`, `_project`, and `review_number`.
"""

from store_schema import CHECK_ENDINGS, CHECK_FAILURES, UNCHECKED, WAIT_GATES, StoreError


class ChecksMixin:
    """The methods of `Store` that read and write `branches` and `checks`."""

    # Reads.

    def head_checks(self, project, head):
        """The newest check of each name on commit `head`, in the order the names first ran."""
        newest = {}
        for row in self.db.execute("SELECT * FROM checks WHERE project = ? AND head_sha = ? ORDER BY id",
                                   (project, head)).fetchall():
            newest[row["name"]] = dict(row)
        return list(newest.values())

    def branch_heads(self, project):
        """Each watched branch's head, from `branches`, leaving out deleted branches."""
        rows = self.db.execute("SELECT branch, head_sha FROM branches WHERE project = ?", (project,)).fetchall()
        return {row["branch"]: row["head_sha"] for row in rows}

    def branch_head(self, project, branch):
        """A branch's head, from `branches`, or None for a deleted or unwatched branch."""
        row = self.branch(project, branch)
        return row["head_sha"] if row else None

    def branch(self, project, branch):
        """A watched branch's row in `branches`, with its head and whether it merges with its base, or None."""
        row = self.db.execute("SELECT * FROM branches WHERE project = ? AND branch = ?", (project, branch)).fetchone()
        return dict(row) if row else None

    def merge_targets(self, project):
        """
        Each open review's branch, base, and draft flag, with the branch's head when the dispatcher watches it, and the
        base and base head its merge was last tried against with that try's result.
        """
        rows = self.db.execute(
            "SELECT reviews.branch, reviews.base AS review_base, reviews.draft, branches.head_sha, branches.base,"
            " branches.base_sha, branches.mergeable FROM reviews JOIN items ON items.project = reviews.project"
            " AND items.number = reviews.number LEFT JOIN branches ON branches.project = reviews.project"
            " AND branches.branch = reviews.branch WHERE reviews.project = ? AND items.state != 'closed'"
            " ORDER BY reviews.number", (project,)).fetchall()
        return [dict(row) for row in rows]

    def newest_checks(self, project, branch, head):
        """
        The checks of `head` on `branch`, as `--json checks` prints them: the newest attempt, or none while no check of
        the head was queued or each one was superseded or cancelled. A first attempt that failed queued a second, and
        the failure stands until the second ends, so the review shows failing while its retry waits or runs, and
        passes once the retry passes. The head's last result stands the same way while a re-check of a newer base
        waits or runs. Each names the base it merged and that base's commit, None before it merged one, and whether
        it was a re-check.
        """
        if not head:
            return []
        marks = ", ".join("?" * len(UNCHECKED))
        rows = self.db.execute(f"SELECT * FROM checks WHERE project = ? AND branch = ? AND head_sha = ?"
                               f" AND status NOT IN ({marks}) ORDER BY id DESC LIMIT 2",
                               (project, branch, head, *UNCHECKED)).fetchall()
        if not rows:
            return []
        row = dict(rows[0])
        if row["status"] in ("queued", "running") and len(rows) > 1 and (
                rows[1]["status"] in CHECK_FAILURES or (row.get("recheck") and rows[1]["status"] == "passed")):
            row = dict(rows[1])
        return [{"name": row["name"], "status": row["status"], "attempt": row["attempt"], "log": row["log_path"],
                 "startedAt": row["started_at"], "endedAt": row["ended_at"], "base": row.get("base"),
                 "baseSha": row.get("base_sha"), "recheck": bool(row.get("recheck"))}]

    def base_broke(self, project, branch, head):
        """
        Whether the newest check of `head` on `branch` is a re-check that failed, retry included: the head passed its
        check against an older base and fails merged with a newer one, which a fix can adapt it to.
        """
        marks = ", ".join("?" * len(UNCHECKED))
        row = self.db.execute(f"SELECT recheck, status FROM checks WHERE project = ? AND branch = ? AND head_sha = ?"
                              f" AND status NOT IN ({marks}) ORDER BY id DESC LIMIT 1",
                              (project, branch, head, *UNCHECKED)).fetchone()
        return bool(row) and bool(row["recheck"]) and row["status"] in CHECK_FAILURES

    def check(self, check):
        row = self.db.execute("SELECT * FROM checks WHERE id = ?", (check,)).fetchone()
        if row is None:
            raise StoreError(f"There is no check {check}.")
        return dict(row)

    def checks(self, statuses, project=None, branch=None):
        """The checks in any of `statuses`, oldest first, of one project or branch when given."""
        marks = ", ".join("?" * len(statuses))
        rows = self.db.execute(f"SELECT * FROM checks WHERE status IN ({marks}) AND (? IS NULL OR project = ?)"
                               " AND (? IS NULL OR branch = ?) ORDER BY id",
                               (*statuses, project, project, branch, branch)).fetchall()
        return [dict(row) for row in rows]

    def checks_of_head(self, project, branch, head):
        """Every check of `head` on `branch`, oldest first."""
        rows = self.db.execute("SELECT * FROM checks WHERE project = ? AND branch = ? AND head_sha = ? ORDER BY id",
                               (project, branch, head)).fetchall()
        return [dict(row) for row in rows]

    def checked(self, project, branch, head):
        """Whether a check of `head` on `branch` was ever queued."""
        return self.db.execute("SELECT 1 FROM checks WHERE project = ? AND branch = ? AND head_sha = ? LIMIT 1",
                               (project, branch, head)).fetchone() is not None

    # Writes. Each public method is one transaction; the underscored helpers run inside one.

    def branch_moved(self, project, branch, head, before):
        """
        Records that `branch` moved from `before` to `head`, where None is a branch that doesn't exist, and ends the
        branch's queued and running checks of other heads as superseded. Returns the checks it ended that were running,
        whose units the caller stops.
        """
        with self.write():
            self._event(project, None, "branch-moved", branch=branch, head=head, before=before)
            if head is None:
                self.db.execute("DELETE FROM branches WHERE project = ? AND branch = ?", (project, branch))
            else:
                # A new head hasn't been tried against its base yet.
                self.db.execute("INSERT INTO branches (project, branch, head_sha, moved_at) VALUES (?, ?, ?, ?)"
                                " ON CONFLICT (project, branch) DO UPDATE SET head_sha = excluded.head_sha,"
                                " moved_at = excluded.moved_at, base = NULL, base_sha = NULL, mergeable = NULL",
                                (project, branch, head, self._now()))
            left = [dict(row) for row in self.db.execute(
                "SELECT * FROM checks WHERE project = ? AND branch = ? AND head_sha IS NOT ?"
                " AND status IN ('queued', 'running') ORDER BY id", (project, branch, head)).fetchall()]
            for row in left:
                self._check_end(row, "superseded")
        return [row for row in left if row["status"] == "running"]

    def merge_tried(self, project, branch, head, base, base_sha, mergeable):
        """
        Records whether `head` of `branch` merges with `base` at `base_sha`, MERGEABLE or CONFLICTING, with a
        `branch-merges` event when that changes what the board shows. Returns False when the branch moved on from
        `head` meanwhile, which leaves its row alone.
        """
        with self.write():
            row = self.db.execute("SELECT * FROM branches WHERE project = ? AND branch = ?",
                                  (project, branch)).fetchone()
            if row is None or row["head_sha"] != head:
                return False
            self.db.execute("UPDATE branches SET base = ?, base_sha = ?, mergeable = ? WHERE project = ?"
                            " AND branch = ?", (base, base_sha, mergeable, project, branch))
            if (row["base"], row["mergeable"]) != (base, mergeable):
                self._event(project, None, "branch-merges", branch=branch, head=head, base=base, base_sha=base_sha,
                            mergeable=mergeable)
        return True

    def _check_queue(self, project, branch, head, attempt, base=None, base_sha=None, recheck=False):
        check = self.db.execute("INSERT INTO checks (project, branch, number, head_sha, name, status, attempt, base,"
                                " base_sha, recheck) VALUES (?, ?, ?, ?, 'test', 'queued', ?, ?, ?, ?)",
                                (project, branch, self.review_number(project, branch), head, attempt, base, base_sha,
                                 int(recheck))).lastrowid
        extra = {"base": base, "base_sha": base_sha, "recheck": True} if recheck else {}
        self._event(project, None, "check-queued", check=check, branch=branch, head=head, attempt=attempt, **extra)
        return check

    def _check_end(self, row, status, name=None):
        self.db.execute("UPDATE checks SET status = ?, name = ?, ended_at = ?, wait_gate = NULL, wait_reason = NULL"
                        " WHERE id = ?",
                        (status, name or row["name"], self._now(), row["id"]))
        self._event(row["project"], None, "check-ended", check=row["id"], branch=row["branch"], head=row["head_sha"],
                    name=name or row["name"], status=status, attempt=row["attempt"])

    def check_queue(self, project, branch, head, base=None, base_sha=None, recheck=False):
        """
        Queues a check of `head` on `branch`, and returns its id. A re-check names the base and the commit of it to
        merge; any other check merges its base's newest commit when it starts.
        """
        with self.write():
            self._project(project)
            return self._check_queue(project, branch, head, 1, base, base_sha, recheck)

    def base_moved(self, project, branch, base_sha):
        """
        Ends the queued and running re-checks of `branch` that merge another commit of its base than `base_sha` as
        superseded, and returns the ones that were running, whose units the caller stops.
        """
        query = ("SELECT * FROM checks WHERE project = ? AND branch = ? AND recheck = 1 AND base_sha IS NOT ?"
                 " AND status IN ('queued', 'running') ORDER BY id")
        # The dispatcher asks every second, so a branch with nothing to supersede costs a read and no write.
        if self.db.execute(query, (project, branch, base_sha)).fetchone() is None:
            return []
        with self.write():
            left = [dict(row) for row in self.db.execute(query, (project, branch, base_sha)).fetchall()]
            for row in left:
                self._check_end(row, "superseded")
        return [row for row in left if row["status"] == "running"]

    def check_merges(self, check, base, base_sha):
        """Records the base a check merges into its head and that base's commit, as the check starts its merge."""
        with self.write():
            self.db.execute("UPDATE checks SET base = ?, base_sha = ? WHERE id = ?", (base, base_sha, check))

    def check_waits(self, check, gate, reason):
        """
        Records why the dispatcher holds a queued check, as `run_waits` does for a run: `gate`, one of WAIT_GATES, and
        `reason`, which reads after "waits for". Adds no event. Returns False when the check no longer waits.
        """
        if gate not in WAIT_GATES:
            raise StoreError(f"{gate} isn't a gate; a check waits on {', '.join(WAIT_GATES)}.")
        with self.write():
            done = self.db.execute("UPDATE checks SET wait_gate = ?, wait_reason = ? WHERE id = ? AND status = 'queued'",
                                   (gate, reason, check)).rowcount
        return bool(done)

    def check_started(self, check, log_path):
        """Marks a queued check as running with its log, clearing why it waited, and returns False when it no longer
        waits."""
        with self.write():
            row = self.check(check)
            done = self.db.execute("UPDATE checks SET status = 'running', log_path = ?, started_at = ?, wait_gate = NULL,"
                                   " wait_reason = NULL WHERE id = ? AND status = 'queued'",
                                   (str(log_path), self._now(), check)).rowcount
            if done:
                self._event(row["project"], None, "check-started", check=check, branch=row["branch"],
                            head=row["head_sha"])
        return bool(done)

    def check_ended(self, check, status, name=None, rerun=False):
        """
        Records how a queued or running check ended, renamed to `name` when given, as a merge conflict renames it
        `merge`. With `rerun`, a first attempt queues a second of the same head, so the head counts as failed only
        once both fail. Returns False when the check had ended already.
        """
        if status not in CHECK_ENDINGS:
            raise StoreError(f"{status} isn't how a check ends; a check ends {', '.join(CHECK_ENDINGS)}.")
        with self.write():
            row = self.check(check)
            if row["status"] not in ("queued", "running"):
                return False
            self._check_end(row, status, name)
            if rerun and row["attempt"] == 1:
                # A re-check's second attempt merges the same commit of its base; another check's merges the newest.
                carried = (row["base"], row["base_sha"], True) if row["recheck"] else ()
                self._check_queue(row["project"], row["branch"], row["head_sha"], 2, *carried)
        return True
