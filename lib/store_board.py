"""
The store's board: the one read that gives the inbox's board everything it shows of a project, and the helpers that
shape an item's state and a review's merge as gh prints them. `Store` in `lib/store.py` inherits `BoardMixin`, whose
methods use the store's `db`, `_row`, `order`, `list_order`, `review_number`, `newest_checks`, `live_runs`, and
`asked_reviews`.
`lib/store.py` imports `mergeable_of` and `outer_state` back for its own reads.
"""

# How many of the newest merged reviews the board shows.
MERGED_ROWS = 3


def mergeable_of(checks, branch=None, head=None, base=None):
    """
    Whether a review's head merges with its base, as gh's `mergeable` reads: the dispatcher's last try of `head`
    against `base`, from the branch's row in `branches`, or without one, the head's checks: CONFLICTING once its check
    failed to merge main, MERGEABLE once one got past the merge, and UNKNOWN before.
    """
    if branch and branch.get("mergeable") and branch["head_sha"] == head and branch["base"] == base:
        return branch["mergeable"]
    if any(one["name"] == "merge" and one["status"] == "failed" for one in checks):
        return "CONFLICTING"
    ended = [one for one in checks if one["status"] not in ("queued", "running")]
    return "MERGEABLE" if any(one["name"] != "merge" for one in ended) else "UNKNOWN"


def outer_state(row, review):
    """The state gh prints for an item: OPEN, CLOSED, or MERGED for a review that merged."""
    if review is not None and review["merged_sha"]:
        return "MERGED"
    return "CLOSED" if row["state"] == "closed" else "OPEN"


class BoardMixin:
    """The methods of `Store` that read a project's board."""

    def board(self, project, now=None):
        """
        Everything the inbox's board shows of a project, in one read, as `board --json` prints it: the open issues with
        their workflow states, labels, blockers, sub-issues, and places in the start order or the list order, from 1,
        and for each in progress its comments and where its agent/issue-<n> branch last merged; the open reviews with
        their heads' checks and their bases' newest commits as the dispatcher last saw them; the newest merged reviews; the runs live_runs lists; and the reviews asked_reviews lists,
        whose agent runs the dispatcher hasn't asked for yet.
        """
        issues = self.db.execute("SELECT * FROM items WHERE project = ? AND kind = 'issue' AND state != 'closed'"
                                 " ORDER BY number", (project,)).fetchall()
        labels, blockers, subs, comments = {}, {}, {}, {}
        for row in self.db.execute("SELECT number, name FROM labels WHERE project = ? ORDER BY name", (project,)):
            labels.setdefault(row["number"], []).append(row["name"])
        for row in self.db.execute(
                "SELECT links.kind, links.to_number, items.number, items.state FROM links JOIN items"
                " ON items.project = links.project AND items.number = links.from_number"
                " WHERE links.project = ? AND links.kind IN ('blocks', 'parent') ORDER BY items.number", (project,)):
            if row["kind"] == "blocks":
                blockers.setdefault(row["to_number"], []).append({"number": row["number"],
                                                                  "state": outer_state(row, None)})
            else:
                total, done = subs.get(row["to_number"], (0, 0))
                subs[row["to_number"]] = (total + 1, done + (row["state"] == "closed"))
        places = {one["number"]: index + 1 for index, one in enumerate(self.order(project))}
        listed = {one["number"]: index + 1 for index, one in enumerate(self.list_order(project))}
        working = [row["number"] for row in issues if row["state"] == "in-progress"]
        for row in self.db.execute(f"SELECT number, body, created_at FROM comments WHERE project = ?"
                                   f" AND number IN ({', '.join('?' * len(working))}) ORDER BY id",
                                   (project, *working)):
            comments.setdefault(row["number"], []).append({"body": row["body"], "createdAt": row["created_at"]})
        shaped = []
        for row in issues:
            number = row["number"]
            one = {"number": number, "title": row["title"], "status": row["state"], "labels": labels.get(number, []),
                   "createdAt": row["created_at"], "updatedAt": row["updated_at"],
                   "blockedBy": blockers.get(number, [])}
            if number in places:
                one["position"] = places[number]
            if number in listed:
                one["listPosition"] = listed[number]
            if number in subs:
                one["subIssues"] = {"total": subs[number][0], "completed": subs[number][1]}
            if row["state"] == "in-progress":
                one.update(comments=comments.get(number, []), landed=self._landed(project, number))
            shaped.append(one)
        branches = {row["branch"]: dict(row) for row in
                    self.db.execute("SELECT * FROM branches WHERE project = ?", (project,))}
        reviews = []
        for row in self.db.execute(
                "SELECT items.number, items.title, items.created_at, reviews.branch, reviews.base, reviews.head_sha,"
                " reviews.draft FROM items JOIN reviews ON reviews.project = items.project"
                " AND reviews.number = items.number WHERE items.project = ? AND items.kind = 'review'"
                " AND items.state != 'closed' ORDER BY items.number", (project,)):
            branch = branches.get(row["branch"])
            head = (branch or {}).get("head_sha") or row["head_sha"]
            checks = self.newest_checks(project, row["branch"], head)
            # The base's newest commit, as the dispatcher last tried the head against it.
            base_head = branch["base_sha"] if branch and branch["base"] == row["base"] else None
            reviews.append({"number": row["number"], "title": row["title"], "branch": row["branch"],
                            "base": row["base"], "head": head, "draft": bool(row["draft"]),
                            "createdAt": row["created_at"], "checks": checks,
                            "mergeable": mergeable_of(checks, branch, head, row["base"]), "baseHead": base_head})
        merged = [{"number": row["number"], "title": row["title"], "branch": row["branch"], "base": row["base"],
                   "mergedAt": row["closed_at"]} for row in self.db.execute(
            "SELECT items.number, items.title, items.closed_at, reviews.branch, reviews.base FROM items JOIN reviews"
            " ON reviews.project = items.project AND reviews.number = items.number WHERE items.project = ?"
            " AND reviews.merged_sha IS NOT NULL ORDER BY items.closed_at DESC, items.number DESC LIMIT ?",
            (project, MERGED_ROWS))]
        return {"repo": project, "issues": shaped, "reviews": reviews, "merged": merged,
                "runs": self.live_runs(project, now), "asked": self.asked_reviews(project)}

    def _landed(self, project, number):
        """
        Where issue `number`'s agent/issue-<n> branch last merged, with the state of the base branch's own review when
        it has one, as a stacked review merges into another agent branch; None when the branch never merged.
        """
        row = self.db.execute("SELECT base FROM reviews WHERE project = ? AND branch = ? AND merged_sha IS NOT NULL"
                              " ORDER BY number DESC LIMIT 1", (project, f"agent/issue-{number}")).fetchone()
        if row is None:
            return None
        base = self.review_number(project, row["base"])
        if base is None:
            return {"base": row["base"], "basePr": None}
        review = self.db.execute("SELECT * FROM reviews WHERE project = ? AND number = ?", (project, base)).fetchone()
        return {"base": row["base"], "basePr": {"number": base, "state": outer_state(self._row(project, base), review)}}
