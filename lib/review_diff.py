"""A review's diff and its size, from git in the project's main checkout, for `agent-desk review diff` and the
`additions`, `deletions`, and `changedFiles` fields of `agent-desk review list --json` and `review view --json`.

A merged review's changes run from where its merge left the base: its merge commit's first parent, or the commit before
a rebase-merge's first. Any other review's run from where its branch left its base, as `git diff base...branch` shows
them, or from the head the review recorded once the branch is gone. web/review.ts reads the same span for the page.
"""

import subprocess

from store import StoreError

FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "-M")
SIZE_FIELDS = ("additions", "deletions", "changedFiles")


def git(checkout, *args):
    """A git command's output in `checkout`, or None when it fails."""
    done = subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True, errors="replace")
    return done.stdout if done.returncode == 0 else None


def commit(checkout, ref):
    """The commit `ref` names in `checkout`, or None."""
    found = git(checkout, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return found.strip() if found else None


def span(checkout, review):
    """
    The commits `git diff` takes for `review`, a row of the store's `reviews` as `Store.review` returns it, or a
    StoreError saying why there are none.
    """
    if review["merged_sha"]:
        merged = commit(checkout, review["merged_sha"])
        if not merged:
            raise StoreError(f"{checkout} has no commit {review['merged_sha']}, the merge of {review['branch']}.")
        rebased = review.get("merged_commits") or 1
        start = rebased > 1 and not commit(checkout, f"{merged}^2") and commit(checkout, f"{merged}~{rebased}")
        return [start or f"{merged}^1", merged]
    base = commit(checkout, review["base"])
    if base is None:
        raise StoreError(f"The base {review['base']} is gone from {checkout}, so the diff is unavailable.")
    head = commit(checkout, f"refs/heads/{review['branch']}") or (review["head_sha"]
                                                                   and commit(checkout, review["head_sha"]))
    if not head:
        raise StoreError(f"The branch {review['branch']} is gone from {checkout}, so its diff is unavailable.")
    return [f"{base}...{head}"]


def diff(checkout, review):
    """The patch of `review`'s changes, as `gh pr diff` prints it."""
    out = git(checkout, "diff", *FLAGS, *span(checkout, review), "--")
    if out is None:
        raise StoreError(f"git couldn't read the diff of {review['branch']} in {checkout}.")
    return out


def size(checkout, review):
    """
    The lines `review` added and deleted and the files it changed, under gh's field names, or None for each when git
    can't read them. A binary file counts as changed with no lines.
    """
    try:
        out = git(checkout, "diff", *FLAGS, "--numstat", "-z", *span(checkout, review), "--")
    except StoreError:
        out = None
    if out is None:
        return dict.fromkeys(SIZE_FIELDS)
    additions = deletions = files = 0
    records = iter(out.split("\0"))
    for record in records:
        if not record:
            continue
        added, deleted, path = record.split("\t", 2)
        if not path:
            # A rename names its old and new paths in the next two records.
            next(records, None)
            next(records, None)
        additions += int(added) if added.isdigit() else 0
        deletions += int(deleted) if deleted.isdigit() else 0
        files += 1
    return {"additions": additions, "deletions": deletions, "changedFiles": files}
