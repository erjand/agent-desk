"""
The store's imports from a host: items, comments, links, runs from the old log, and label colors, with the label
color writes and the cutover's steps beside them. `Store` in `lib/store.py` inherits `ImportMixin`, whose methods
use the store's `db`, `write`, `_event`, `_project`, `_labels`, `_settle_order`, `_list_top`, and
`_settle_list`.
"""

import json
import re

from store_schema import IMPORTED_COMMENT, IMPORTED_ITEM, IMPORTED_REVIEW, RUN_RESULT, StoreError, epoch_of, store_time

# How far, in seconds before and after, a run's end in the store may lie from the end an old log line gives for the
# same run: the log wrote its line a few seconds before the unit ended.
RUN_MATCH_SECONDS = (-60, 120)


class ImportMixin:
    """The methods of `Store` that import from a host, with label colors and cutover steps."""

    # Imports. Each page of records an import reads goes in one transaction without events, and `import_event` writes
    # one event for the whole run, so an import of a thousand items doesn't flood the readers of `events`.

    def import_items(self, project, items, path=None):
        """Writes items read from a host with their numbers, and returns how many it added and how many changed.

        Each item holds the IMPORTED_ITEM columns, its `labels`, and for a review a `review` with the IMPORTED_REVIEW
        columns. The import's labels replace the item's, and its state replaces the workflow state, which settles the
        project's start order as `_settle_order` does. An issue whose state the import changes takes the top of the
        list order, and a new one a place above the rest by number, as `_settle_list` gives it."""
        added = updated = 0
        with self.write():
            self._project(project, path)
            for item in items:
                number = item["number"]
                row = self.db.execute("SELECT * FROM items WHERE project = ? AND number = ?",
                                      (project, number)).fetchone()
                values = [item[key] for key in IMPORTED_ITEM]
                if row is None:
                    self.db.execute(f"INSERT INTO items (project, number, {', '.join(IMPORTED_ITEM)})"
                                    f" VALUES (?, ?{', ?' * len(IMPORTED_ITEM)})", (project, number, *values))
                    changed = False
                    added += 1
                else:
                    changed = [row[key] for key in IMPORTED_ITEM] != values
                    if changed:
                        self.db.execute(f"UPDATE items SET {', '.join(f'{key} = ?' for key in IMPORTED_ITEM)}"
                                        " WHERE project = ? AND number = ?", (*values, project, number))
                    if row["state"] != item["state"]:
                        self._list_top(project, number)
                labels = set(item["labels"])
                held = set(self._labels(project, number))
                if labels != held:
                    self.db.execute("DELETE FROM labels WHERE project = ? AND number = ?", (project, number))
                    self.db.executemany("INSERT INTO labels (project, number, name) VALUES (?, ?, ?)",
                                        [(project, number, name) for name in sorted(labels)])
                    changed = True
                if item.get("review"):
                    review = {**item["review"], "draft": int(item["review"]["draft"])}
                    held = self.db.execute("SELECT * FROM reviews WHERE project = ? AND number = ?",
                                           (project, number)).fetchone()
                    if held is None or any(held[key] != review[key] for key in IMPORTED_REVIEW):
                        columns = ", ".join(IMPORTED_REVIEW)
                        self.db.execute(f"INSERT OR REPLACE INTO reviews (project, number, {columns})"
                                        f" VALUES (?, ?{', ?' * len(IMPORTED_REVIEW)})",
                                        (project, number, *(review[key] for key in IMPORTED_REVIEW)))
                        changed = True
                if row is not None and changed:
                    updated += 1
            if items:
                self.db.execute("UPDATE projects SET next_number = MAX(next_number, ?) WHERE slug = ?",
                                (max(item["number"] for item in items) + 1, project))
            # The import writes states directly, so the start order gains the issues it queued and loses the rest.
            self._settle_order(project)
            self._settle_list(project)
        return added, updated

    def import_comments(self, project, comments):
        """Writes comments read from a host, each found again by its `remote_id`, and returns how many it added and
        how many changed. A comment on an item the store lacks waits for the next import."""
        added = updated = 0
        with self.write():
            for comment in comments:
                if not self.db.execute("SELECT 1 FROM items WHERE project = ? AND number = ?",
                                       (project, comment["number"])).fetchone():
                    continue
                values = [int(comment[key]) if key == "to_agent" else comment[key] for key in IMPORTED_COMMENT]
                row = self.db.execute("SELECT * FROM comments WHERE project = ? AND remote_id = ?",
                                      (project, comment["remote_id"])).fetchone()
                if row is None:
                    # The agent read a line comment on the host, so Send to agent leaves it out.
                    self.db.execute(f"INSERT INTO comments (project, remote_id, sent_in, {', '.join(IMPORTED_COMMENT)})"
                                    f" VALUES (?, ?, ?{', ?' * len(IMPORTED_COMMENT)})",
                                    (project, comment["remote_id"], None if comment["path"] is None else 0, *values))
                    added += 1
                elif [row[key] for key in IMPORTED_COMMENT] != values:
                    self.db.execute(f"UPDATE comments SET {', '.join(f'{key} = ?' for key in IMPORTED_COMMENT)}"
                                    " WHERE id = ?", (*values, row["id"]))
                    updated += 1
        return added, updated

    def import_links(self, project, numbers, links):
        """Makes the links of the imported items match the host's, and returns how many it added and removed.

        `links` holds (from, to, kind) triples. An item in `numbers` owns its parent link, the `blocks` links into it,
        and as a review its `fixes` links, and the import replaces those. A link to an item the store lacks waits for
        the next import."""
        added = removed = 0
        with self.write():
            present = {row[0] for row in self.db.execute("SELECT number FROM items WHERE project = ?", (project,))}
            wanted = {link for link in links if link[0] in present and link[1] in present}
            rows = self.db.execute("SELECT from_number, to_number, kind FROM links WHERE project = ?",
                                   (project,)).fetchall()
            held = {tuple(row) for row in rows}
            for link in held - wanted:
                owner = link[1] if link[2] == "blocks" else link[0]
                if owner in numbers:
                    self.db.execute("DELETE FROM links WHERE project = ? AND from_number = ? AND to_number = ?"
                                    " AND kind = ?", (project, *link))
                    removed += 1
            for link in wanted - held:
                self.db.execute("INSERT INTO links (project, from_number, to_number, kind) VALUES (?, ?, ?, ?)",
                                (project, *link))
                added += 1
        return added, removed

    def import_run(self, project, kind, target, ended, started, status, exit_code, result, taken):
        """
        Writes a finished run from the log the desk kept before the store, and returns `added`, `filled`, or None when
        the store held it already. `ended` and `started` are times as the store writes them, and `result` holds the
        RUN_RESULT columns, with `labels` as a list.

        The dispatcher recorded runs in the store while the old log still ran, so the store may hold the line's run:
        the ended run of the same target whose end lies closest to the line's, within RUN_MATCH_SECONDS, that the
        import hasn't matched to another line. `taken` holds the ids the import matched, and gains this one. The line
        fills the result columns the run's row lacks, so an import run again changes nothing.
        """
        values = {**result, "labels": None if result.get("labels") is None else json.dumps(result["labels"])}
        end = epoch_of(ended)
        early, late = (store_time(end + offset) for offset in RUN_MATCH_SECONDS)
        with self.write():
            rows = self.db.execute("SELECT * FROM runs WHERE project = ? AND kind = ? AND target = ?"
                                   " AND ended_at BETWEEN ? AND ?"
                                   " AND status NOT IN ('requested', 'running', 'ignored')",
                                   (project, kind, target, early, late)).fetchall()
            rows = [row for row in rows if row["id"] not in taken]
            if not rows:
                columns = ("project", "kind", "target", "status", "started_at", "ended_at", "exit_code") + RUN_RESULT
                taken.add(self.db.execute(
                    f"INSERT INTO runs ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                    (project, kind, target, status, started, ended, exit_code,
                     *(values[key] for key in RUN_RESULT))).lastrowid)
                return "added"
            row = min(rows, key=lambda one: (abs(epoch_of(one["ended_at"]) - end), one["id"]))
            taken.add(row["id"])
            missing = {key: values[key] for key in RUN_RESULT if row[key] is None and values[key] is not None}
            if missing:
                self.db.execute(f"UPDATE runs SET {', '.join(f'{key} = ?' for key in missing)} WHERE id = ?",
                                (*missing.values(), row["id"]))
        return "filled" if missing else None

    def import_label_colors(self, project, colors):
        """Makes a project's label colors the host's, from each label's name to its six hex digits, and returns
        whether any changed."""
        with self.write():
            held = dict(self.db.execute("SELECT name, color FROM label_colors WHERE project = ?",
                                        (project,)).fetchall())
            if held == colors:
                return False
            self.db.execute("DELETE FROM label_colors WHERE project = ?", (project,))
            self.db.executemany("INSERT INTO label_colors (project, name, color) VALUES (?, ?, ?)",
                                [(project, name, color) for name, color in sorted(colors.items())])
        return True

    def label_colors(self, project):
        """A project's label colors, from each label's name to its six hex digits."""
        return dict(self.db.execute("SELECT name, color FROM label_colors WHERE project = ? ORDER BY name",
                                    (project,)).fetchall())

    def set_label_color(self, project, name, color):
        """Gives a label in a project a color, as six hex digits with or without a #, and returns whether it changed.
        The next import replaces it with the host's colors."""
        color = color.removeprefix("#").lower()
        if not name.strip():
            raise StoreError("A label needs a name.")
        if not re.fullmatch(r"[0-9a-f]{6}", color):
            raise StoreError(f"{color} isn't a color; give six hex digits, such as d73a4a.")
        with self.write():
            self._project(project)
            if self.db.execute("SELECT 1 FROM label_colors WHERE project = ? AND name = ? AND color = ?",
                               (project, name, color)).fetchone():
                return False
            self.db.execute("INSERT INTO label_colors (project, name, color) VALUES (?, ?, ?)"
                            " ON CONFLICT (project, name) DO UPDATE SET color = excluded.color", (project, name, color))
            self._event(project, None, "label-colored", label=name, color=color)
        return True

    def cutover_steps(self, project):
        """
        The steps `lib/cutover.py` recorded for a project since its last undo, oldest first, each a `cutover` event's
        payload with its number and time. Empty when the project never cut over, or its last cutover was undone.
        """
        rows = self.db.execute("SELECT number, payload, at FROM events WHERE project = ? AND type = 'cutover'"
                               " ORDER BY id", (project,)).fetchall()
        steps = []
        for row in rows:
            step = {**json.loads(row["payload"]), "number": row["number"], "at": row["at"]}
            steps = [] if step["step"] == "undone" else steps + [step]
        return steps

    def cutover_step(self, project, step, number=None, **payload):
        """Records one step of a cutover, or `undone` for its undo, as a `cutover` event."""
        with self.write():
            self._event(project, number, "cutover", step=step, **payload)

    def import_event(self, project, host, counts):
        """The one event an import writes, once it ends, with what it added and changed and any failure."""
        with self.write():
            self._event(project, None, "imported", host=host, **counts)
