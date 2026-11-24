"""
The store's list order: one hand-set order of each project's issues, apart from the start order, which the board
lists Open, Blocked, Needs Design, Backlog, and Ideas in. `Store` in `lib/store.py` inherits `ListOrderMixin`, whose
methods use the store's `db`, `version`, `write`, and `_event`.

Each issue holds its place in `items.list_rank`, the higher value listed first, and ties fall to the higher number.
The store doesn't know the board's sections: an issue moves next to another issue, and a reset takes the issues of a
section by number, so one order read through each section's filter gives every section its own order.

- An issue takes the top when it is filed, and whenever a write changes its workflow state, adds or removes its
  `backlog` or `idea` label, or adds or removes a `blocks` link onto it, as `_list_top` sets it.
- The order lists the open issues outside the start order, those neither closed, queued, nor waiting. An issue
  outside it keeps a value, which no read uses until a change of state puts it on top again.
- A move or a reset deals the values the issues it touches hold back out among them in their new order, so every
  other issue keeps its value.
"""

from store_schema import LIST_SCHEMA, ORDER_STATES, StoreError

# The workflow states whose issues stay out of the list order.
UNLISTED = ("closed", *ORDER_STATES)


class ListOrderMixin:
    """The methods of `Store` that read and move a project's list order."""

    def _listed_rows(self, project):
        """The rows of the issues in a project's list order, first first, each with its number, title, state, and
        rank. A read-only store on a database older than the list order lists them by number, the highest first."""
        ranked = "list_rank DESC, " if self.version >= LIST_SCHEMA else ""
        rank = "list_rank" if self.version >= LIST_SCHEMA else "NULL"
        return self.db.execute(
            f"SELECT number, title, state, {rank} AS list_rank FROM items WHERE project = ? AND kind = 'issue'"
            f" AND state NOT IN ({', '.join('?' * len(UNLISTED))}) ORDER BY {ranked}number DESC",
            (project, *UNLISTED)).fetchall()

    def list_order(self, project):
        """A project's open issues outside the start order in their list order, first first, each with its number,
        title, and status."""
        return [{"number": row["number"], "title": row["title"], "status": row["state"]}
                for row in self._listed_rows(project)]

    def _list_top(self, project, number):
        """Puts an issue first in its project's list order. A review holds no place."""
        self.db.execute("UPDATE items SET list_rank = (SELECT COALESCE(MAX(list_rank), 0) + 1 FROM items"
                        " WHERE project = ?) WHERE project = ? AND number = ? AND kind = 'issue'",
                        (project, project, number))

    def _settle_list(self, project):
        """Gives each issue without a place one above the rest, the highest number first, as after an import."""
        for row in self.db.execute("SELECT number FROM items WHERE project = ? AND kind = 'issue'"
                                   " AND list_rank IS NULL ORDER BY number", (project,)).fetchall():
            self._list_top(project, row["number"])

    def _listed(self, project, number):
        """Raises StoreError unless `number` is an open issue outside the start order."""
        row = self.db.execute("SELECT kind, state FROM items WHERE project = ? AND number = ?",
                              (project, number)).fetchone()
        if row is None:
            raise StoreError(f"{project} has no issue #{number}.")
        if row["kind"] != "issue":
            raise StoreError(f"#{number} is a review, and only an issue has a place in the list order.")
        if row["state"] == "closed":
            raise StoreError(f"#{number} is closed, and only an open issue has a place in the list order.")
        if row["state"] in ORDER_STATES:
            raise StoreError(f"#{number} is {row['state']}, so the start order places it; `agent-desk queue order` "
                             "moves it.")

    def _deal(self, project, numbers, ranks):
        """Gives `numbers`, in order, the values `ranks` holds, highest first."""
        for number, rank in zip(numbers, sorted(ranks, reverse=True)):
            self.db.execute("UPDATE items SET list_rank = ? WHERE project = ? AND number = ? AND list_rank IS NOT ?",
                            (rank, project, number, rank))

    def list_move(self, project, number, before=None, after=None, where=None):
        """
        Moves issue `number` in its project's list order right above issue `before`, right below issue `after`, or
        to the `top` or the `bottom`, with a `list-moved` event naming its places before and after, from 1, and
        returns its place and the order's length. A move to the place it holds writes nothing. Raises StoreError for
        a closed, queued, or waiting issue, a review, an item that doesn't exist, or an issue moved next to itself.
        """
        anchor = before if before is not None else after
        if [before, after, where].count(None) != 2 or where not in (None, "top", "bottom"):
            raise StoreError("A list move takes one of before, after, top, or bottom.")
        with self.write():
            self._listed(project, number)
            if anchor is not None:
                if anchor == number:
                    raise StoreError(f"#{number} can't move next to itself.")
                self._listed(project, anchor)
            self._settle_list(project)
            rows = self._listed_rows(project)
            numbers = [row["number"] for row in rows]
            was = numbers.index(number)
            numbers.pop(was)
            to = {"top": 0, "bottom": len(numbers)}.get(where) if where else \
                numbers.index(anchor) + (after is not None)
            numbers.insert(to, number)
            if to != was:
                self._deal(project, numbers, [row["list_rank"] for row in rows])
                self._event(project, number, "list-moved", to=to + 1, was=was + 1)
        return to + 1, len(numbers)

    def list_reset(self, project, numbers):
        """
        Deals the places the issues `numbers` hold in their project's list order back out among them, the highest
        number first, leaving every other issue's place alone, with one `list-reset` event naming the issues in
        their order before and after. Returns whether the order changed; issues in that order already write
        nothing. Raises StoreError as `list_move` does for any issue that holds no place.
        """
        numbers = list(dict.fromkeys(numbers))
        if not numbers:
            raise StoreError("A reset takes the issues whose places it deals out.")
        with self.write():
            for number in numbers:
                self._listed(project, number)
            self._settle_list(project)
            rows = [row for row in self._listed_rows(project) if row["number"] in numbers]
            was = [row["number"] for row in rows]
            order = sorted(numbers, reverse=True)
            if order == was:
                return False
            self._deal(project, order, [row["list_rank"] for row in rows])
            self._event(project, None, "list-reset", order=order, was=was)
        return True
