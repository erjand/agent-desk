"""Prints the local checks of a review's head, and with --watch waits for them to finish.

    agent-desk review checks <n | branch> [--watch] [--interval <seconds>]

The checks are the store's `checks` rows for the commit the review's branch points at in the project's main checkout,
which every worktree shares, or for the head the review recorded when git can't name one. A head checked more than
once, as a failed first attempt is, counts by its newest row of each name.

The exit status follows `gh pr checks`: 0 when every check passed, 1 when one failed, and 8 while one is queued or
running. A head with no check yet exits 8 too, unless the project's `.agent-desk.json` sets no `test` command, in which
case no check will come and it exits 0. `--watch` prints the checks each time they change and returns once none
waits; when no check of the head appears in NO_CHECK_SECONDS, it says so and exits 1.
"""

import json
import subprocess
import time
from pathlib import Path

PENDING = ("queued", "running")
FAILED = ("failed", "timed-out", "lost")
# A superseded or cancelled check neither passes nor fails its head.
SKIPPED = ("superseded", "cancelled")
EXIT = {"passed": 0, "failed": 1, "pending": 8, "untested": 0}
# A check starts once the branch's head stands still for a minute, so a head that has none after five has no
# dispatcher queueing it.
NO_CHECK_SECONDS = 300
INTERVAL_SECONDS = 10


def head_of(desk, repo, number, checkout):
    """The review's branch and the commit it points at now in the project's main checkout."""
    review = desk.item(repo, number, "review")
    branch = review["headRefName"]
    done = subprocess.run(["git", "-C", checkout, "rev-parse", "--verify", "--quiet",
                           f"refs/heads/{branch}^{{commit}}"], capture_output=True, text=True)
    return branch, (done.stdout.strip() if done.returncode == 0 else None) or review["headRefOid"]


def has_test_command(checkout):
    """Whether the checkout's config names a test command, as the check runner needs one to queue a check."""
    try:
        config = json.loads((Path(checkout) / ".agent-desk.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(config, dict) and bool(str(config.get("test") or "").strip())


def verdict(rows):
    """passed, failed, or pending for a head's checks, or None when it has none that count."""
    statuses = [row["status"] for row in rows if row["status"] not in SKIPPED]
    if any(status in FAILED for status in statuses):
        return "failed"
    if any(status in PENDING for status in statuses):
        return "pending"
    return "passed" if statuses else None


def report(branch, head, rows):
    """The checks as a person reads them: one line each, with its log once it has one."""
    short = (head or "no commit")[:12]
    if not rows:
        return f"No checks of {branch} at {short} yet."
    lines = [f"Checks of {branch} at {short}:"]
    for row in rows:
        log = f"  {row['log_path']}" if row["log_path"] and row["status"] not in PENDING else ""
        lines.append(f"{row['name']}\t{row['status']}{log}")
    return "\n".join(lines)


def state(desk, repo, number, checkout):
    """The review's branch, head, newest checks, and verdict, read afresh."""
    branch, head = head_of(desk, repo, number, checkout)
    rows = desk.head_checks(repo, head) if head else []
    found = verdict(rows)
    if found is None and not has_test_command(checkout):
        found = "untested"
    return branch, head, rows, found


def checks(desk, repo, number, checkout, watch=False, interval=INTERVAL_SECONDS, say=print, clock=time.monotonic,
           sleep=time.sleep):
    """Prints the checks, waiting with `watch` until none is pending, and returns the exit status."""
    started, shown = clock(), None
    while True:
        branch, head, rows, found = state(desk, repo, number, checkout)
        text = report(branch, head, rows)
        if found == "untested":
            text += f"\n{repo} sets no test command in .agent-desk.json, so no check runs."
        if text != shown:
            say(text)
            shown = text
        if found in ("passed", "failed", "untested"):
            return EXIT[found]
        if not watch:
            return EXIT["pending"]
        if found is None and clock() - started >= NO_CHECK_SECONDS:
            say(f"No check of {branch} started in {NO_CHECK_SECONDS // 60} minutes. Check that "
                "agent-desk-dispatch.service is running.")
            return 1
        sleep(interval)
