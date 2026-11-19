"""The command line of agent-desk's store: `agent-desk issue`, `agent-desk review`, `agent-desk label`, `agent-desk
queue`, and the `project add` that `agent-desk install` runs, each a read or write of lib/store.py's database.

    python3 store_cli.py issue create --title <t> [--body <b> | --body-file <f>] [--label <l>]... [--parent <n>]
                                      [--blocked-by <n>]...          prints the new issue's number
    python3 store_cli.py issue view <n> [--json <fields>]
    python3 store_cli.py issue list [--state open|closed|all] [--status <s>] [--label <l>]... [--search <text>]
                                    [--since <time>] [--limit <n>] [--json <fields>]
    python3 store_cli.py issue comment <n> --body <b> | --body-file <f> [--to-agent | --edit <comment> [--note <n>]]
    python3 store_cli.py issue edit <n> [--title <t>] [--body <b>] [--add-label <l>]... [--remove-label <l>]...
                                    [--status <s>] [--parent <n>] [--add-blocked-by <n>]... [--remove-blocked-by <n>]...
    python3 store_cli.py issue close <n> [--reason completed|not-planned | --duplicate-of <m>]
                                     [--comment <c> | --comment-file <f>]
                                                                     closes issue n, cancelling each agent request
                                                                     that waits for it and dropping its Manual
                                                                     pick; --duplicate-of closes it as
                                                                     a duplicate of issue m, commenting `Duplicate
                                                                     of #m` before the comment
    python3 store_cli.py issue reopen <n>
    python3 store_cli.py issue delete <n> [--yes]                    prints what deleting issue n removes, or with
                                                                     --yes deletes it
    python3 store_cli.py review create --branch <b> --title <t> [--base main] [--body <b>] [--draft] [--fixes <n>]...
                                       [--label <l>]...              prints the new review's number
    python3 store_cli.py review view <n | branch> [--json <fields>]
    python3 store_cli.py review list [--state open|closed|merged|all] [--head <branch>] [--base <branch>]
                                     [--label <l>]... [--since <time>] [--limit <n>] [--json <fields>]
    python3 store_cli.py review comment <n> --body <b> [--path <file> --line <n> [--head-sha <commit>]]
                                       [--to-agent [--quotes <comment>]...] [--edit <comment> [--note <n>]]
    python3 store_cli.py review edit <n> [--title <t>] [--body <b>] [--add-label <l>]... [--remove-label <l>]...
    python3 store_cli.py review ready <n>
    python3 store_cli.py review checks <n | branch> [--watch] [--interval <s>]   lib/review_checks.py prints and waits
    python3 store_cli.py review close <n>
    python3 store_cli.py review reopen <n>
    python3 store_cli.py review merge <n> --sha <commit>             records the merge and closes the issues it fixes
    python3 store_cli.py label edit <name> --color <hex>             gives a label its color; `create` does the same
    python3 store_cli.py label list                                  prints each label's name and color
    python3 store_cli.py queue order [<n> --up | --down | --top | --to <place>]
                                                                     prints the start order of the queued and
                                                                     waiting issues, or moves issue n in it, --to
                                                                     a place from 1
    python3 store_cli.py project add <owner/repo> <main checkout>    records a project and its main checkout, as
                                                                     `agent-desk install` does

The verbs and flags follow the gh commands the scripts call today, and `--json` prints the fields gh names the same
way (`labels[].name`, `comments[].body`, `headRefName`), so a script pipes the output to jq as it passed `--jq`. The
project is `--repo`, or GH_REPO, or the origin of the checkout the command runs in.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import hold
import review_checks
from run_control import cancel_waiting
from store import (ISSUE_FIELDS, REVIEW_FIELDS, STATUSES, Store, StoreError, checkout_of, git,
                   repo_here)
from store_delete import described


def body_of(args):
    """The text from --body, or from --body-file, where - reads the standard input."""
    if getattr(args, "body_file", None):
        return sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text()
    return args.body


def closing_comment(args):
    """The comment an issue's close posts, from --comment or --comment-file, or None without one."""
    if getattr(args, "comment_file", None):
        return sys.stdin.read() if args.comment_file == "-" else Path(args.comment_file).read_text()
    return getattr(args, "comment", None)


def pick(shaped, fields, known):
    """The fields `--json` names from one shaped item, as gh keeps only the fields asked for."""
    names = [name for name in fields.split(",") if name]
    unknown = [name for name in names if name not in known]
    if unknown:
        raise StoreError(f"Unknown JSON field{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}. The fields are "
                         f"{', '.join(known)}.")
    return {name: shaped.get(name) for name in names}


def show(shaped):
    """One item as a person reads it."""
    labels = ", ".join(label["name"] for label in shaped["labels"])
    lines = [f"#{shaped['number']} {shaped['title']}", f"{shaped['state']} ({shaped['status']})"
             + (f"  labels: {labels}" if labels else "")]
    if "headRefName" in shaped:
        draft = " (draft)" if shaped["isDraft"] else ""
        lines.append(f"{shaped['headRefName']} into {shaped['baseRefName']}{draft}")
    for name, title in (("blockedBy", "blocked by"), ("subIssues", "sub-issues")):
        if shaped[name]:
            lines.append(f"{title}: " + ", ".join(f"#{one['number']}" for one in shaped[name]))
    if shaped["duplicateOf"]:
        lines.append(f"duplicate of: #{shaped['duplicateOf']['number']}")
    lines += ["", shaped["body"].rstrip()]
    for comment in shaped["comments"]:
        place = f" on {comment['path']}:{comment['line']}" if comment["path"] else ""
        edited = f", edited {comment['lastEditedAt']}" if comment["lastEditedAt"] else ""
        lines += ["", f"-- {comment['author']['login']} (comment {comment['id']}) at {comment['createdAt']}{edited}"
                  f"{place}", comment["body"].rstrip()]
    return "\n".join(lines)


def parser():
    top = argparse.ArgumentParser(prog="agent-desk", description="Issues, reviews, and labels in the local store.")
    kinds = top.add_subparsers(dest="kind", required=True)
    for kind in ("issue", "review"):
        verbs = kinds.add_parser(kind).add_subparsers(dest="verb", required=True)

        def verb(name, number=True):
            one = verbs.add_parser(name)
            one.add_argument("--repo", "-R")
            if number:
                one.add_argument("number")
            return one

        create = verb("create", number=False)
        create.add_argument("--title", "-t", required=True)
        create.add_argument("--body", "-b", default="")
        create.add_argument("--body-file", "-F")
        create.add_argument("--label", "-l", action="append", default=[])
        view = verb("view")
        view.add_argument("--json")
        view.add_argument("--comments", action="store_true", help="accepted for gh's sake; comments always print")
        listing = verb("list", number=False)
        listing.add_argument("--state", "-s", default="open",
                             choices=("open", "closed", "all") + (("merged",) if kind == "review" else ()))
        listing.add_argument("--label", "-l", action="append", default=[])
        listing.add_argument("--limit", "-L", type=int, default=30)
        listing.add_argument("--since", help="the items created at or after a time, such as 2026-11-12T09:30:00Z")
        listing.add_argument("--json")
        comment = verb("comment")
        comment.add_argument("--body", "-b", default="")
        comment.add_argument("--body-file", "-F")
        comment.add_argument("--to-agent", action="store_true")
        comment.add_argument("--quotes", type=int, action="append", default=[],
                             help="a line comment this comment sent to the agent carries, which then counts as sent")
        comment.add_argument("--edit", type=int, metavar="COMMENT",
                             help="replaces the body of a comment you wrote, by the id `view` prints, in place of a "
                                  "new comment")
        comment.add_argument("--note", help="what an --edit changed, which the comment then shows; an edit by the "
                                            "agent needs one")
        edit = verb("edit")
        edit.add_argument("--title", "-t")
        edit.add_argument("--body", "-b")
        edit.add_argument("--body-file", "-F")
        edit.add_argument("--add-label", action="append", default=[])
        edit.add_argument("--remove-label", action="append", default=[])
        verb("reopen")
        close = verb("close")
        if kind == "issue":
            verb("delete").add_argument("--yes", "-y", action="store_true",
                                        help="deletes the issue; without it the command only prints what it removes")
            create.add_argument("--parent", type=int)
            create.add_argument("--blocked-by", type=int, action="append", default=[])
            listing.add_argument("--status", choices=STATUSES)
            listing.add_argument("--search", "-S")
            edit.add_argument("--status", choices=STATUSES)
            edit.add_argument("--parent", type=int)
            edit.add_argument("--add-blocked-by", type=int, action="append", default=[])
            edit.add_argument("--remove-blocked-by", type=int, action="append", default=[])
            close.add_argument("--reason", "-r", choices=("completed", "not-planned", "duplicate"),
                               help="why it closed: completed, the default, not-planned, or duplicate with "
                                    "--duplicate-of")
            close.add_argument("--duplicate-of", type=issue_number, metavar="NUMBER",
                               help="the original issue this one duplicates, which closes it as a duplicate")
            close.add_argument("--comment", "-c")
            close.add_argument("--comment-file", help="the comment from a file, where - reads the standard input")
        else:
            create.add_argument("--branch", "--head", "-H", required=True)
            create.add_argument("--base", "-B", default="main")
            create.add_argument("--draft", "-d", action="store_true")
            create.add_argument("--fixes", type=int, action="append", default=[])
            listing.add_argument("--head", "-H")
            listing.add_argument("--base", "-B")
            comment.add_argument("--path")
            comment.add_argument("--line", type=int)
            comment.add_argument("--head-sha")
            verb("ready")
            checks = verb("checks")
            checks.add_argument("--watch", action="store_true")
            checks.add_argument("--interval", "-i", type=float)
            verb("merge").add_argument("--sha", required=True)
    labels = kinds.add_parser("label").add_subparsers(dest="verb", required=True)
    # create is gh's verb for a new label; here a label exists once an item carries it, so both set its color.
    color = labels.add_parser("edit", aliases=["create"])
    color.add_argument("--repo", "-R")
    color.add_argument("name")
    color.add_argument("--color", "-c", required=True, help="six hex digits, such as d73a4a")
    labels.add_parser("list").add_argument("--repo", "-R")
    order = kinds.add_parser("queue").add_subparsers(dest="verb", required=True).add_parser("order")
    order.add_argument("--repo", "-R")
    order.add_argument("number", nargs="?")
    moves = order.add_mutually_exclusive_group()
    for move in ("up", "down", "top"):
        moves.add_argument(f"--{move}", dest="move", action="store_const", const=move)
    moves.add_argument("--to", dest="move", type=int, metavar="PLACE", help="the place from 1 to move the issue to")
    add = kinds.add_parser("project").add_subparsers(dest="verb", required=True).add_parser("add")
    add.add_argument("repo")
    add.add_argument("path")
    return top


def number_of(store, repo, args):
    """The item a command names: a number, with or without #, or for a review its branch."""
    text = args.number.lstrip("#")
    if text.isdigit():
        return int(text)
    if args.kind == "review":
        number = store.review_number(repo, args.number)
        if number is not None:
            return number
    raise StoreError(f"{repo} has no {args.kind} {args.number}.")


def run(args, store):
    """Carries out one parsed command, and returns what it prints."""
    repo = args.repo or repo_here()
    kind, verb = args.kind, args.verb
    if kind == "label":
        if verb == "list":
            return "\n".join(f"{name}\t#{color}" for name, color in store.label_colors(repo).items())
        store.set_label_color(repo, args.name, args.color)
        return ""
    if kind == "queue":
        return queue_order(store, repo, args)
    known = ISSUE_FIELDS if kind == "issue" else REVIEW_FIELDS
    if verb == "create":
        if kind == "issue":
            fields = {"parent": args.parent, "blocked_by": args.blocked_by}
        else:
            fields = {"branch": args.branch, "base": args.base, "draft": args.draft, "fixes": args.fixes,
                      "head_sha": git("rev-parse", "--verify", "--quiet", f"refs/heads/{args.branch}")}
        return str(store.create(repo, kind, args.title, body_of(args), args.label, path=checkout_of(repo), **fields))
    if verb == "list":
        found = store.items(repo, kind, state=args.state, status=getattr(args, "status", None), labels=args.label,
                            search=getattr(args, "search", None), head=getattr(args, "head", None),
                            base=getattr(args, "base", None), limit=args.limit, since=args.since)
        if args.json is not None:
            return json.dumps([pick(one, args.json, known) for one in found])
        return "\n".join("\t".join([str(one["number"]), one["status"], one["title"],
                                    ", ".join(label["name"] for label in one["labels"])]) for one in found)
    number = number_of(store, repo, args)
    if verb == "delete":
        return delete(store, repo, number, args.yes)
    if verb == "view":
        shaped = store.item(repo, number, kind)
        return json.dumps(pick(shaped, args.json, known)) if args.json is not None else show(shaped)
    store.item(repo, number, kind)
    if verb == "comment" and args.note is not None and args.edit is None:
        raise StoreError("--note says what an --edit changed, so it goes only with --edit.")
    if verb == "comment" and args.edit is not None:
        clashing = [flag for flag, given in (("--to-agent", args.to_agent), ("--quotes", args.quotes),
                                             ("--path", getattr(args, "path", None)),
                                             ("--line", getattr(args, "line", None)),
                                             ("--head-sha", getattr(args, "head_sha", None))) if given]
        if clashing:
            raise StoreError(f"--edit changes a comment's body only, so it can't go with {', '.join(clashing)}.")
        comment = store.comment_of(args.edit)
        if comment is not None and comment["project"] == repo and comment["number"] != number:
            raise StoreError(f"Comment {args.edit} is on #{comment['number']}, not #{number}.")
        store.edit_comment(repo, args.edit, body_of(args), note=args.note)
    elif verb == "comment":
        store.comment(repo, number, body_of(args), to_agent=args.to_agent, path=getattr(args, "path", None),
                      line=getattr(args, "line", None), head_sha=getattr(args, "head_sha", None), quotes=args.quotes)
    elif verb == "edit":
        body = body_of(args) if args.body_file else args.body
        store.edit(repo, number, args.title, body, args.add_label, args.remove_label, getattr(args, "status", None))
        if kind == "issue":
            if args.parent:
                store.link(repo, number, args.parent, "parent")
            for blocker in args.add_blocked_by:
                store.link(repo, blocker, number, "blocks")
            for blocker in args.remove_blocked_by:
                store.link(repo, blocker, number, "blocks", add=False)
    elif verb == "close" and kind == "issue":
        close_issue(store, repo, number, args)
    elif verb == "close":
        store.set_state(repo, number, "closed", "closed")
    elif verb == "reopen":
        with store.write():
            # A reopened issue is no longer a duplicate, so a later close names its original again.
            original = store.item(repo, number)["duplicateOf"]
            if original is not None:
                store.link(repo, number, original["number"], "duplicate", add=False)
            store.set_state(repo, number, "open")
    elif verb == "ready":
        store.review_update(repo, number, draft=False)
    elif verb == "merge":
        store.merge(repo, number, args.sha)
    return ""


def issue_number(text):
    """An issue's number from a flag, with or without #."""
    if not text.lstrip("#").isdigit():
        raise argparse.ArgumentTypeError(f"{text} isn't an issue number.")
    return int(text.lstrip("#"))


def close_issue(store, repo, number, args):
    """
    Closes issue `number` in one write, so a close that fails leaves nothing behind: ends each agent request that still
    waits for it as cancelled, comments `Duplicate of #<m>` and links it to issue m when it closes as a duplicate,
    posts the closing comment, and sets it closed with its reason. Then drops its Manual pick, so a reopened issue
    waits for Start again. A run that already works the issue keeps working.
    Raises StoreError, writing nothing, for a duplicate of itself or of an issue the project doesn't have.
    """
    original = args.duplicate_of
    reason = args.reason or ("duplicate" if original is not None else "completed")
    if reason == "duplicate" and original is None:
        raise StoreError(f"Closing #{number} as a duplicate takes --duplicate-of, the issue it duplicates.")
    if reason != "duplicate" and original is not None:
        raise StoreError(f"--duplicate-of closes #{number} as a duplicate, so it can't go with --reason {reason}.")
    if original == number:
        raise StoreError(f"#{number} can't be a duplicate of itself.")
    comment = closing_comment(args)
    with store.write():
        if original is not None:
            store.item(repo, original, "issue")
            store.comment(repo, number, f"Duplicate of #{original}")
            store.link(repo, number, original, "duplicate")
        if comment is not None:
            store.comment(repo, number, comment)
        cancel_waiting(store, repo, number)
        store.set_state(repo, number, "closed", reason)
    if f"{repo}#{number}" in hold.status()["picked"]:
        hold.spend(number, repo)


def delete(store, repo, number, yes):
    """
    Deletes issue `number` with `yes`, as Store.delete_issue does, and drops its Manual pick; without `yes` it changes
    nothing. Returns what it removed, or would remove.
    """
    checkout = checkout_of(repo)
    if not yes:
        return f"Would delete {described(store.deletion(repo, number, checkout))}. Nothing brings it back, and its " \
               "number is never used again. Run it again with --yes to delete it."
    plan = store.delete_issue(repo, number, checkout)
    if f"{repo}#{number}" in hold.status()["picked"]:
        hold.spend(number, repo)
    return f"Deleted {described(plan)}."


def queue_order(store, repo, args):
    """
    The project's start order, one issue a line with its place, number, status, and title; or with a number and a
    move, moves the issue and says where it went.
    """
    if (args.number is None) != (args.move is None):
        raise StoreError("queue order takes an issue with --up, --down, --top, or --to, or none to print the order.")
    if args.move is None:
        return "\n".join(f"{place}\t#{one['number']}\t{one['status']}\t{one['title']}"
                         for place, one in enumerate(store.order(repo), start=1))
    text = args.number.lstrip("#")
    if not text.isdigit():
        raise StoreError(f"{repo} has no issue {args.number}.")
    place = store.move(repo, int(text), args.move)
    return f"#{text} is {place} of {len(store.order(repo))} in the start order."


# The verbs that only read, which open the store read-only.
READING_VERBS = ("view", "list", "checks")


def main(argv):
    args = parser().parse_args(argv)
    try:
        reads = args.verb in READING_VERBS or (args.kind == "queue" and args.move is None) \
            or (args.verb == "delete" and not args.yes)
        store = Store(read_only=args.kind != "project" and reads)
        try:
            if args.kind == "project":
                store.project(args.repo, str(Path(args.path).resolve()))
                return 0
            if getattr(args, "verb", None) == "checks":
                repo = args.repo or repo_here()
                return review_checks.checks(store, repo, number_of(store, repo, args), checkout_of(repo) or ".",
                                            watch=args.watch,
                                            interval=args.interval or review_checks.INTERVAL_SECONDS)
            out = run(args, store)
        finally:
            store.close()
    except (StoreError, sqlite3.Error) as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
