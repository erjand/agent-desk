"""Reads a project's .agent-desk.json for the shell scripts, which have no JSON parser of their own.

    python3 config.py <file> <key.path> [default]   prints a string or number, each item of a list on its own line,
                                                    or each entry of an object as KEY=VALUE on its own line
    python3 config.py <file> --chores               prints each chore as "<skill> <days>", in the order listed, with
                                                    " paused" after a chore listed in chores.paused
    python3 config.py <file> --issue-label <skill>  prints the label a chore files its issues under, from
                                                    chores.issueLabels over CHORE_ISSUE_LABELS, failing for a chore
                                                    that files none
"""

import json
import sys
from pathlib import Path

# The label each shared chore files its issues under, which the chore timer counts to hold the chore back. A project's
# chores.issueLabels adds to these or changes them.
CHORE_ISSUE_LABELS = {"audit": "audit", "security-audit": "security"}


def read(checkout):
    """A checkout's .agent-desk.json as an object, or an empty one when it is missing or isn't an object."""
    try:
        with open(Path(checkout) / ".agent-desk.json", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def lookup(config, path):
    """The value at a dotted path, or None where any part is missing."""
    value = config
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def issue_label(config, skill):
    """The label a chore files its issues under, from chores.issueLabels over CHORE_ISSUE_LABELS, or None for a chore
    that opens reviews instead."""
    labels = lookup(config, "chores.issueLabels")
    labels = {**CHORE_ISSUE_LABELS, **(labels if isinstance(labels, dict) else {})}
    label = labels.get(skill)
    return label if isinstance(label, str) and label.strip() else None


def render(value):
    """The lines a value prints as: one per list item, one KEY=VALUE per object entry, or the value itself."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [f"{key}={item}" for key, item in value.items()]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    return [str(value)]


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        config = json.load(handle)
    if argv[2] == "--chores":
        paused = lookup(config, "chores.paused")
        paused = set(paused) if isinstance(paused, list) else set()
        for skill, days in (lookup(config, "chores.every") or {}).items():
            print(skill, days, *(["paused"] if skill in paused else []))
        return 0
    if argv[2] == "--issue-label":
        label = issue_label(config, argv[3]) if len(argv) > 3 else None
        if label is None:
            return 1
        print(label)
        return 0
    value = lookup(config, argv[2])
    if value is None:
        if len(argv) > 3:
            print(argv[3])
            return 0
        return 1
    print("\n".join(render(value)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
