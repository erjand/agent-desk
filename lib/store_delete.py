"""
The store's delete of an issue filed by mistake. `Store` in `lib/store.py` inherits `DeleteMixin`, whose methods use the
store's `db`, `write`, `_row`, `_labels`, `_link`, `_event`, `runs`, `run_ended`, and `project_path`.

An issue with work behind it stays: one a review fixes or that has a review on its `agent/issue-<n>` branch, one with
that branch or its worktree in the project's main checkout, one an agent run works, and one with sub-issues. Their
history explains code that landed or may land, so they close instead. A delete cancels the agent requests that still
wait for the issue, removes its labels, comments, and links in both directions, and the issue, and writes an
`item-deleted` event, all in one transaction. `projects.next_number` stays where it is, so the number never names
another issue.
"""

import subprocess
from pathlib import Path

from store_schema import StoreError


def agent_target(run, number):
    """Whether a run is an agent's on issue `number`: a fresh start or reply, or an idea's discussion."""
    return run["kind"] == "agent" and run["target"] in (str(number), f"idea-{number}")


def work_on_disk(checkout, number):
    """
    What the main checkout at `checkout` holds of issue `number`'s work: its `agent/issue-<n>` branch and its worktree,
    `<checkout>-issue-<n>` beside it or any worktree on that branch, each as a phrase that reads after "it has".
    """
    if not checkout or not Path(checkout).is_dir():
        return []
    branch = f"agent/issue-{number}"
    found = []
    shown = subprocess.run(["git", "-C", checkout, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                           capture_output=True, text=True)
    if shown.returncode == 0:
        found.append(f"the branch {branch}")
    listed = subprocess.run(["git", "-C", checkout, "worktree", "list", "--porcelain"], capture_output=True, text=True)
    on_branch = f"branch refs/heads/{branch}" in listed.stdout.splitlines()
    beside = Path(f"{checkout.rstrip('/')}-issue-{number}")
    if on_branch or beside.exists():
        found.append(f"a worktree at {beside}" if beside.exists() else f"a worktree on {branch}")
    return found


class DeleteMixin:
    """The methods of `Store` that delete an issue."""

    def delete_refusals(self, project, number, checkout=None):
        """
        Why issue `number` can't be deleted, one phrase a reason that reads after "it has", or an empty list when it
        can. `checkout` is the project's main checkout, and defaults to the one the store records. Raises StoreError
        for an item that isn't an issue.
        """
        self._row(project, number, "issue")
        found = []
        reviews = {row["number"] for row in self.db.execute(
            "SELECT links.from_number AS number FROM links JOIN items ON items.project = links.project"
            " AND items.number = links.from_number WHERE links.project = ? AND links.to_number = ?"
            " AND links.kind = 'fixes' AND items.kind = 'review'", (project, number))}
        reviews |= {row["number"] for row in self.db.execute(
            "SELECT number FROM reviews WHERE project = ? AND branch = ?", (project, f"agent/issue-{number}"))}
        if reviews:
            found.append(", ".join(f"review #{one}" for one in sorted(reviews)))
        found += work_on_disk(checkout or self.project_path(project), number)
        units = [run["unit"] or f"run {run['id']}" for run in self.runs(("running",))
                 if run["project"] == project and agent_target(run, number)]
        if units:
            found.append(f"an agent run working it in {', '.join(units)}")
        subs = [row["from_number"] for row in self.db.execute(
            "SELECT from_number FROM links WHERE project = ? AND to_number = ? AND kind = 'parent'"
            " ORDER BY from_number", (project, number))]
        if subs:
            found.append(f"sub-issue{'s' if len(subs) > 1 else ''} {', '.join(f'#{one}' for one in subs)}")
        return found

    def deletion(self, project, number, checkout=None):
        """
        What deleting issue `number` removes: its title, its comment count, its labels, its links as `(kind, other
        number, outward)`, and the ids of the agent requests that wait for it. Raises StoreError, naming why and saying
        to close the issue instead, for an issue `delete_refusals` refuses.
        """
        refusals = self.delete_refusals(project, number, checkout)
        if refusals:
            raise StoreError(f"#{number} has {'; '.join(refusals)}, so it can't be deleted. Close it instead with "
                             f"`agent-desk issue close {number} --reason not-planned`.")
        row = self._row(project, number, "issue")
        comments = self.db.execute("SELECT COUNT(*) FROM comments WHERE project = ? AND number = ?",
                                   (project, number)).fetchone()[0]
        links = [(link["kind"], link["to_number"], True) if link["from_number"] == number
                 else (link["kind"], link["from_number"], False) for link in self.db.execute(
            "SELECT * FROM links WHERE project = ? AND (from_number = ? OR to_number = ?)"
            " ORDER BY kind, from_number, to_number", (project, number, number))]
        requests = [run["id"] for run in self.runs(("requested",))
                    if run["project"] == project and agent_target(run, number)]
        return {"number": number, "title": row["title"], "comments": comments, "labels": self._labels(project, number),
                "links": links, "requests": requests}

    def delete_issue(self, project, number, checkout=None):
        """
        Deletes issue `number` in one write, as the module's docstring says, and returns what it removed as `deletion`
        shapes it. Raises StoreError, changing nothing, for an issue `deletion` refuses.
        """
        with self.write():
            plan = self.deletion(project, number, checkout)
            for run in plan["requests"]:
                self.run_ended(run, "cancelled", waiting=True)
            for kind, other, outward in plan["links"]:
                self._link(project, *((number, other) if outward else (other, number)), kind, add=False)
            for table in ("labels", "comments", "items"):
                self.db.execute(f"DELETE FROM {table} WHERE project = ? AND number = ?", (project, number))
            self._event(project, number, "item-deleted", kind="issue", title=plan["title"],
                        comments=plan["comments"], labels=plan["labels"])
        return plan


def listed(words):
    """Words joined as a sentence lists them: `a`, `a and b`, or `a, b, and c`."""
    return words[0] if len(words) == 1 else f"{words[0]} and {words[1]}" if len(words) == 2 \
        else f"{', '.join(words[:-1])}, and {words[-1]}"


def described(plan):
    """What a deletion removes as one phrase: the issue, its comments, labels, and links, and the requests it ends."""
    count = plan["comments"]
    parts = [f"{count} comment{'s' if count != 1 else ''}"]
    if plan["labels"]:
        parts.append(f"the label{'s' if len(plan['labels']) > 1 else ''} {listed(plan['labels'])}")
    if plan["links"]:
        names = {("parent", True): "parent", ("blocks", True): "blocks", ("blocks", False): "blocked by",
                 ("duplicate", True): "duplicate of", ("duplicate", False): "duplicated by"}
        parts.append(f"the link{'s' if len(plan['links']) > 1 else ''} " + listed(
            [f"{names.get((kind, outward), kind)} #{other}" for kind, other, outward in plan["links"]]))
    said = f"#{plan['number']} \"{plan['title']}\" with {listed(parts)}"
    requests = len(plan["requests"])
    if requests:
        said += f", cancelling {requests} waiting agent request{'s' if requests != 1 else ''}"
    return said
