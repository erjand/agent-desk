"""Copies a GitHub project's issues, pull requests, comments, labels and their colors, and links into the local store.

    python3 import_github.py <owner/repo>

Every GitHub name lives in this module, which turns what GitHub returns into the store's own records, so an importer
for another host can sit beside it. It reads with `gh api --paginate`: GraphQL for the issues and pull requests with
their labels, links, and review summaries, at a few points a page, and REST for the label colors and the comments of
the whole repository, 100 a call. It writes each page as it arrives, so a read that fails partway leaves what came
before it, and a rerun updates what changed and adds what is new.

A comment that carries `<!-- agent-desk -->`, the hidden mark agent-desk left on what it posted to GitHub, gets the
author `desk` when it opens the way the desk's own scripts' notes do, and `agent` otherwise, since both posted under the
user's account. Other text from the user's account gets `you`, and anyone else's keeps their login.

Only a comment with the author `you` that mentions `@agent` counts as sent to the agent. Another account's `@agent`
comment imports as an ordinary comment, so the board never shows a stranger's text as the user's request.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import store  # noqa: E402

MARK = "<!-- agent-desk -->"
# The openings of the notes lib/'s scripts post, such as work-issue.sh's "An agent started on", serial.sh's "Waiting
# for", and failures.py's "**test failed**". Every other marked comment came from an agent.
DESK_OPENINGS = re.compile(r"(An agent started|The agent |The fix |Merged \S+ into `[^`]+` with no conflicts"
                           r"|Landed on main|This parent issue|Waiting for |Left the merge queue|\*\*[^*\n]+ failed"
                           r"|Changes the chores' schedule)")
# The labels that named a workflow state, in the order that picks one when an issue carries several.
STATE_ORDER = ("in-progress", "needs-design", "waiting", "on-hold", "agent-ready")
PAGE = 100
REASONS = {"COMPLETED": "completed", "NOT_PLANNED": "not-planned", "DUPLICATE": "duplicate"}

ISSUES = """query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body state stateReason createdAt updatedAt closedAt author { login }
        labels(first: 50) { nodes { name } }
        parent { number repository { nameWithOwner } }
        blockedBy(first: 50) { nodes { number repository { nameWithOwner } } }
      }
    }
  }
  viewer { login }
  rateLimit { cost }
}"""

PULLS = """query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $endCursor, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body state createdAt updatedAt closedAt author { login }
        headRefName baseRefName headRefOid isDraft
        mergeCommit { oid message authoredDate parents { totalCount } }
        commits(last: 1) { totalCount nodes { commit { oid message authoredDate } } }
        labels(first: 50) { nodes { name } }
        closingIssuesReferences(first: 50) { nodes { number repository { nameWithOwner } } }
        reviews(first: 100) { nodes { id body state submittedAt author { login } } }
      }
    }
  }
  rateLimit { cost }
}"""


class ReadError(Exception):
    """A read from GitHub that failed, with GitHub's own message."""


def paginate(args):
    """Each page `gh api --paginate` prints, as it arrives."""
    process = subprocess.Popen(["gh", "api", "--paginate", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    decoder, buffer = json.JSONDecoder(), ""
    try:
        for chunk in iter(lambda: process.stdout.read(65536), ""):
            buffer += chunk
            while True:
                buffer = buffer.lstrip()
                try:
                    page, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    break
                buffer = buffer[end:]
                yield page
        error = process.stderr.read().strip()
        if process.wait() != 0:
            raise ReadError(error or f"gh api {args[0]} exited with status {process.returncode}.")
    finally:
        # A write that fails stops reading, and gh stops with it.
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


def graphql(query, repo):
    owner, name = repo.split("/", 1)
    return paginate(["graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"query={query}"])


def rebased(node):
    """How many commits a rebase-merge of pull request `node` wrote onto its base, ending at its merge commit, or None
    when the merge wrote one commit: a merge commit, a squash, or the rebase of a single commit.

    GitHub names no merge method, so the commits tell: a rebase-merge writes the pull request's last commit again with
    its message and author date, or lands that commit itself, and a squash of several commits writes a message and
    date of its own."""
    merge, commits = node["mergeCommit"], node["commits"]
    last = commits["nodes"][0]["commit"] if commits["nodes"] else None
    if last is None or commits["totalCount"] < 2 or merge["parents"]["totalCount"] != 1:
        return None
    if merge["oid"] == last["oid"] or (merge["message"], merge["authoredDate"]) == (last["message"],
                                                                                   last["authoredDate"]):
        return commits["totalCount"]
    return None


class Importer:
    """One import of one repository: it reads GitHub page by page, writes each page, and tallies what changed."""

    def __init__(self, desk, repo):
        self.desk, self.repo = desk, repo
        self.viewer = None
        self.numbers, self.links = set(), []
        self.tally = {"items": [0, 0], "comments": [0, 0], "links": [0, 0]}
        self.points = self.calls = 0

    def run(self):
        """Reads and writes everything. The links and the summary event go in last, even after a failed read."""
        problem = None
        try:
            for page in graphql(ISSUES, self.repo):
                self.viewer = self.viewer or page["data"]["viewer"]["login"]
                self.page_of(page, "issues", self.issue)
            for page in graphql(PULLS, self.repo):
                self.page_of(page, "pullRequests", self.pull)
            self.label_colors()
            for path, convert in ((f"repos/{self.repo}/issues/comments", self.issue_comment),
                                  (f"repos/{self.repo}/pulls/comments", self.line_comment)):
                batch, read = [], 0
                try:
                    # gh joins a REST listing's pages into one array, so --jq prints each comment as its page arrives.
                    for one in paginate([f"{path}?per_page={PAGE}", "--jq", ".[]"]):
                        batch.append(convert(one))
                        read += 1
                        if len(batch) == PAGE:
                            self.comments(batch)
                            batch = []
                finally:
                    self.calls += max(1, -(-read // PAGE))
                    self.comments(batch)
        except ReadError as error:
            problem = str(error)
        added, removed = self.desk.import_links(self.repo, self.numbers, self.links)
        self.tally["links"] = [added, removed]
        self.desk.import_event(self.repo, "github", {**self.counts(), "failed": problem})
        return problem

    def page_of(self, page, connection, convert):
        if page.get("errors"):
            raise ReadError("; ".join(error.get("message", "") for error in page["errors"]))
        self.points += page["data"].get("rateLimit", {}).get("cost", 0)
        items, comments = [], []
        for node in page["data"]["repository"][connection]["nodes"]:
            item, extra = convert(node)
            items.append(item)
            comments += extra
            self.numbers.add(item["number"])
        added, updated = self.desk.import_items(self.repo, items, path=store.checkout_of(self.repo))
        self.tally["items"][0] += added
        self.tally["items"][1] += updated
        self.comments(comments)

    def label_colors(self):
        """Reads every label's color and writes them all once the last page arrives, so a read that fails keeps the
        colors the store had."""
        colors = {}
        try:
            for one in paginate([f"repos/{self.repo}/labels?per_page={PAGE}", "--jq", ".[]"]):
                colors[one["name"]] = one["color"].lower()
        finally:
            self.calls += max(1, -(-len(colors) // PAGE))
        self.desk.import_label_colors(self.repo, colors)

    def comments(self, comments):
        added, updated = self.desk.import_comments(self.repo, comments)
        self.tally["comments"][0] += added
        self.tally["comments"][1] += updated

    def counts(self):
        return {f"{kind}_{what}": self.tally[kind][index] for kind in self.tally
                for index, what in enumerate(("added", "updated" if kind != "links" else "removed"))}

    def summary(self):
        (items, items_changed), (comments, comments_changed), (links, unlinked) = self.tally.values()
        return (f"{self.repo}: {items} items added and {items_changed} updated, {comments} comments added and "
                f"{comments_changed} updated, {links} links added and {unlinked} removed. GitHub cost "
                f"{self.points} GraphQL points and {self.calls} REST calls.")

    # GitHub's records as the store's.

    def author_of(self, user, body):
        login = (user or {}).get("login")
        if login and login == self.viewer:
            if MARK in (body or ""):
                return "desk" if DESK_OPENINGS.match(body.lstrip()) else "agent"
            return "you"
        return login

    def here(self, node):
        """The number of an issue in this repository, or None for one in another."""
        if node and node["repository"]["nameWithOwner"].lower() == self.repo.lower():
            return node["number"]
        return None

    def item(self, node, kind, state, reason):
        labels = [label["name"] for label in node["labels"]["nodes"]]
        return {
            "number": node["number"], "kind": kind, "title": node["title"], "body": node["body"] or "",
            "state": state, "state_reason": reason, "author": self.author_of(node["author"], node["body"]),
            "created_at": node["createdAt"], "updated_at": node["updatedAt"], "closed_at": node["closedAt"],
            "labels": sorted(label for label in labels if label not in store.STATUS_LABELS),
        }

    def issue(self, node):
        labels = {label["name"] for label in node["labels"]["nodes"]}
        if node["state"] == "CLOSED":
            state, reason = "closed", REASONS.get(node["stateReason"], "completed")
        else:
            state = next((store.STATUS_LABELS[name] for name in STATE_ORDER if name in labels), "open")
            reason = None
        number = node["number"]
        parent = self.here(node["parent"])
        if parent is not None:
            self.links.append((number, parent, "parent"))
        for blocker in node["blockedBy"]["nodes"]:
            if self.here(blocker) is not None:
                self.links.append((blocker["number"], number, "blocks"))
        return self.item(node, "issue", state, reason), []

    def pull(self, node):
        merged = node["mergeCommit"]["oid"] if node["state"] == "MERGED" and node["mergeCommit"] else None
        if node["state"] == "OPEN":
            state, reason = "open", None
        else:
            state, reason = "closed", "merged" if node["state"] == "MERGED" else "closed"
        item = self.item(node, "review", state, reason)
        item["review"] = {"branch": node["headRefName"], "base": node["baseRefName"], "head_sha": node["headRefOid"],
                          "draft": node["isDraft"], "merged_sha": merged,
                          "merged_commits": rebased(node) if merged else None}
        for fixed in node["closingIssuesReferences"]["nodes"]:
            if self.here(fixed) is not None:
                self.links.append((node["number"], fixed["number"], "fixes"))
        # A review left as a pending draft has no submittedAt, and an approval without words has no body.
        summaries = [self.comment(review["id"], node["number"], review["author"], review["body"],
                                  review["submittedAt"])
                     for review in node["reviews"]["nodes"] if review["submittedAt"] and review["body"].strip()]
        return item, summaries

    def comment(self, remote_id, number, user, body, created_at, **place):
        author = self.author_of(user, body)
        return {"remote_id": remote_id, "number": number, "author": author, "body": body, "created_at": created_at,
                "to_agent": author == "you" and bool(store.MENTION.search(body)),
                "path": place.get("path"), "line": place.get("line"), "head_sha": place.get("head_sha")}

    def issue_comment(self, one):
        number = int(one["issue_url"].rsplit("/", 1)[1])
        return self.comment(one["node_id"], number, one["user"], one["body"] or "", one["created_at"])

    def line_comment(self, one):
        """A review's line comment, kept on the line and commit GitHub shows it at, or the ones it was written on once
        later commits outdated it."""
        number = int(one["pull_request_url"].rsplit("/", 1)[1])
        current = one.get("line") is not None
        return self.comment(one["node_id"], number, one["user"], one["body"] or "", one["created_at"],
                            path=one["path"], line=one["line"] if current else one.get("original_line"),
                            head_sha=one["commit_id"] if current else one.get("original_commit_id"))


def main(argv):
    if len(argv) != 1 or not re.fullmatch(r"[\w.-]+/[\w.-]+", argv[0]):
        print("usage: agent-desk import <owner/repo>", file=sys.stderr)
        return 2
    importer = None
    try:
        desk = store.Store()
        try:
            importer = Importer(desk, argv[0])
            problem = importer.run()
        finally:
            desk.close()
    except store.StoreError as error:
        if importer:
            print(importer.summary())
        print(f"agent-desk: {error}", file=sys.stderr)
        return 1
    print(importer.summary())
    if problem:
        print(f"agent-desk: the import stopped partway, keeping what it wrote: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
