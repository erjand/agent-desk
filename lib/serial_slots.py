"""Decides which serial issues may work at once, by the serial groups their labels name.

    python3 serial_slots.py holder <issue> <labels>   prints the lowest in-progress issue that shares a serial group
                                                      with the issue, or nothing; stdin holds the open issues
    python3 serial_slots.py release <waiting.json> [<picked>]
                                                      prints the waiting issues to start, one number a line, in the
                                                      project's start order; stdin holds the open issues, and
                                                      <picked>, given for a project in Manual, lists the issues Manual
                                                      lets start

A label named `serial` or `serial:<group>`, such as `serial:audio`, puts an issue in that group, and an issue may sit in
several. Each group has a slot of its own: an issue works only while no other issue in any of its groups holds one, so
serial issues of different groups work side by side. The labels argument separates names with spaces or commas. The open
issues on stdin are one or more `agent-desk issue list --json number,labels,status` lists, one after another, where
`status` is the issue's state in the local store, and the waiting file is one such list of the waiting issues nothing
open blocks, with each one's `position` in the start order. The picked list separates numbers with spaces or commas, and
may be empty, when nothing is picked. lib/dispatch.py calls `holder` and `release` with the store's items directly.
"""

import json
import sys


def groups(labels):
    """The serial groups a list of label names puts an issue in."""
    return {name for name in labels if name == "serial" or (name.startswith("serial:") and len(name) > 7)}


def names(issue):
    """The label names of an issue as the store lists it."""
    return [label["name"] for label in issue.get("labels", [])]


def holder(number, labels, open_issues):
    """The lowest-numbered in-progress issue, other than the given one, that shares one of its serial groups."""
    mine = groups(labels)
    holders = [issue["number"] for issue in open_issues if issue["number"] != number
               and issue.get("status") == "in-progress" and mine & groups(names(issue))]
    return min(holders) if holders else None


def start_order(issue):
    """Where an issue sorts in its project's start order: by its `position`, then, without one, after those with one by
    number."""
    position = issue.get("position")
    return (position is None, position or 0, issue["number"])


def release(waiting, open_issues, picked=None):
    """
    The waiting issues to start, in the project's start order: every one outside the serial groups, and each serial one
    whose groups no issue holds, so a group's slot goes to its first issue in the order. A queued serial issue is on
    its way to a slot, so it counts as holding its groups, and each issue started here takes its groups from the ones
    after it.

    `picked`, given for a project in Manual, holds the numbers of the issues Manual lets start: those picked, and those
    whose run works already. A queued issue outside it waits for Start, so it holds nothing, and a waiting serial issue
    outside it stays waiting, so it can't take its groups' slots and then wait for Start in turn.
    """
    taken = set()
    for issue in open_issues:
        status = issue.get("status")
        if status == "in-progress" or (status == "queued" and (picked is None or issue["number"] in picked)):
            taken |= groups(names(issue))
    started = []
    for issue in sorted(waiting, key=start_order):
        wanted = groups(names(issue))
        if wanted and picked is not None and issue["number"] not in picked:
            continue
        if wanted & taken:
            continue
        taken |= wanted
        started.append(issue["number"])
    return started


def read_issues(text):
    """The issues in one or more JSON lists written one after another, as several store reads print them."""
    decoder, issues, at = json.JSONDecoder(), [], 0
    while True:
        while at < len(text) and text[at].isspace():
            at += 1
        if at >= len(text):
            return issues
        try:
            value, at = decoder.raw_decode(text, at)
        except ValueError:
            return issues
        if isinstance(value, list):
            issues.extend(one for one in value if isinstance(one, dict) and "number" in one)


def main(argv):
    open_issues = read_issues(sys.stdin.read())
    if len(argv) == 4 and argv[1] == "holder" and argv[2].isdigit():
        found = holder(int(argv[2]), argv[3].replace(",", " ").split(), open_issues)
        if found is not None:
            print(found)
        return 0
    if len(argv) in (3, 4) and argv[1] == "release":
        with open(argv[2], encoding="utf-8") as file:
            waiting = json.load(file)
        picked = None
        if len(argv) == 4:
            picked = {int(one) for one in argv[3].replace(",", " ").split() if one.isdigit()}
        for number in release(waiting, open_issues, picked):
            print(number)
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
