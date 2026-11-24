"""Prints everything the inbox's board shows of a project, in one read that asks GitHub nothing:

    python3 board.py [--repo <owner/repo>] [--json]

`agent-desk board --json` is the one command the pane and the web page run on each refresh. It prints what
Store.board reads from the local store: the open issues with their workflow states, labels, blockers, sub-issues,
and places in the start order or the list order, the open reviews with their checks and whether they merge with their bases,
each ready one's passed check with `behind`, how many merges its base took since the commit the check merged, the
newest merges, and the runs, each that waits with the gate that holds it and why, and each that works and stalls with `stalled`, as
lib/run_stall.py saves it, with `checks`, the branch checks running now, `waitingChecks`, the queued branch checks the
dispatcher holds with the gate and why, and `slots`, the project's slots of each kind as lib/run_units.py's slots_of
reads them from its `.agent-desk.json` and the overrides `agent-desk slots` sets. Beside those it prints, each from a file another command saved:

- `queue`: the merge queue and the reviews that left it, as the last `agent-desk merges` tick or change saved it,
  with `ahead`, how many commits main holds that origin's main lacks, `push`, the push queue's state, and
  `agentBranches`, how many agent branches match origin and how many wait, which lib/pushes.py reads from git and its
  own saved state;
- `backup`: how the last backup of the store went, as lib/backup.py saved it beside the database, or null before the
  first, the same for every project;
- `fixes`: what each conflicting PR's automatic fix waits on, as the last `agent-desk fixes` status or tick saved it;
- `hold`: the desk's mode and the issues and requests picked for Manual, as `lib/hold.py status` prints them;
- `chores`: the chores table the last `agent-desk chores status` saved, which the chore timer runs every 5 minutes;
- `account`: the account the desk's runs bill, `spent`, the month's spend on the API key from every project's runs and
  the Chat, `budget`, the API budget as `{"on", "dollars"}` or null, and `held`, whether the budget holds new runs, as
  lib/budget.py's summary reads them, the same for every project;

and `at`, the time and zone such as `14:05 +0200`, `machine`, this machine's name, which a session's note names, and
`web`, the address the web page serves at, which the pane's rows link to.
Without --json it lists the open issues and reviews.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup  # noqa: E402
import budget  # noqa: E402
import config  # noqa: E402
import fixes  # noqa: E402
import hold  # noqa: E402
import merges  # noqa: E402
import pushes  # noqa: E402
import run_stall  # noqa: E402
import run_units  # noqa: E402
import store  # noqa: E402


def chores_path(repo):
    """Where `agent-desk chores status` saves the table it prints."""
    return store.STATE / "chores" / store.project_key(repo) / "status.txt"


def saved_chores(repo):
    """The lines of the chores table the last `agent-desk chores status` saved, header first; none before the first."""
    try:
        return [line for line in chores_path(repo).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def hold_status():
    """The desk's mode as `lib/hold.py status` prints it."""
    current = hold.status()
    return {**{key: current.get(key) for key in ("state", "since", "before", "picked")},
            "pickedRuns": current["picked_runs"], "projects": current.get("projects", {})}


def machine():
    """This machine's name, as `uname -n` prints it."""
    return os.uname().nodename


# The port `agent-desk web on` serves the page at when AGENT_DESK_WEB_PORT is unset.
WEB_PORT = 7480


def web_unit():
    """The systemd user unit `agent-desk web on` writes."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / \
        "agent-desk-web.service"


def web_address():
    """
    Where the web page serves: at AGENT_DESK_WEB_PORT, as the server itself reads it, or else at the port the service
    `agent-desk web on` installed names, or else at WEB_PORT.
    """
    port = os.environ.get("AGENT_DESK_WEB_PORT", "")
    if not port.isdigit():
        try:
            found = re.search(r"^Environment=AGENT_DESK_WEB_PORT=(\d+)$", web_unit().read_text(encoding="utf-8"),
                              re.MULTILINE)
        except OSError:
            found = None
        port = found.group(1) if found else str(WEB_PORT)
    return f"http://127.0.0.1:{port}"


def running_checks(desk, repo):
    """The project's branch checks running now, oldest first, each with its branch, its review's number or None, the
    head it checks, and when it started."""
    return [{"branch": row["branch"], "review": row["number"], "head": row["head_sha"], "startedAt": row["started_at"]}
            for row in desk.checks(("running",), repo)]


def waiting_checks(desk, repo):
    """The project's queued branch checks the dispatcher holds, oldest first, each with its branch, its review's number
    or None, the head it checks, the gate that holds it, and why, which reads after "waits for"."""
    return [{"branch": row["branch"], "review": row["number"], "head": row["head_sha"], "gate": row["wait_gate"],
             "reason": row["wait_reason"]} for row in desk.checks(("queued",), repo) if row.get("wait_gate")]


def behind(checkout, older, newer):
    """How many commits `newer` holds on its first-parent line that `older` lacks, as merges into a base land; None when
    git can't count them."""
    done = subprocess.run(["git", "-C", str(checkout), "rev-list", "--count", "--first-parent", f"{older}..{newer}"],
                          capture_output=True, text=True)
    return int(done.stdout) if done.returncode == 0 and done.stdout.strip().isdigit() else None


def with_behind(checkout, reviews):
    """
    The reviews, each ready one whose check passed merged with an older commit of its base than the newest with
    `behind` on that check: how many merges its base took since.
    """
    shaped = []
    for review in reviews:
        check = review["checks"][0] if len(review.get("checks") or []) == 1 else None
        older, newer = (check or {}).get("baseSha"), review.get("baseHead")
        if checkout and check and check["status"] == "passed" and not review["draft"] and older and newer \
                and older != newer and (count := behind(checkout, older, newer)):
            review = {**review, "checks": [{**check, "behind": count}]}
        shaped.append(review)
    return shaped


def with_stalls(runs):
    """The runs, each running one that lib/run_stall.py flags as stalled with its stall as `stalled`."""
    return [{**run, "stalled": stall} if run.get("status") == "running" and (stall := run_stall.read(run["id"]))
            else run for run in runs]


def read(desk, repo, now=None):
    """Everything the board shows of `repo`, as `--json` prints it."""
    now = time.time() if now is None else now
    checkout = desk.project_path(repo)
    origin = pushes.shown(checkout, repo, now) if checkout else None
    board = desk.board(repo, now)
    state = hold.status()
    return {**board, "runs": with_stalls(board.get("runs", [])),
            "reviews": with_behind(checkout, board.get("reviews", [])),
            "slots": run_units.slots_of(config.read(checkout) if checkout else {}, hold.slot_override(state, repo)),
            "checks": running_checks(desk, repo), "waitingChecks": waiting_checks(desk, repo),
            "queue": merges.shown(merges.load(merges.state_path(repo), int(now)), origin),
            "fixes": fixes.saved_plan(repo), "hold": hold_status(), "chores": saved_chores(repo),
            "account": budget.summary(state, desk, now), "backup": backup.saved(store.DATABASE),
            "at": time.strftime("%H:%M %z", time.localtime(now)), "machine": machine(),
            "web": web_address()}


def show(board):
    """The board's open issues and reviews as a person reads them."""
    lines = [f"#{one['number']}\t{one['status']}\t{one['title']}" for one in board["issues"]]
    for one in board["reviews"]:
        checks = ", ".join(f"{check['name']} {check['status']}" for check in one["checks"]) or "no checks"
        lines.append(f"#{one['number']}\t{'draft' if one['draft'] else 'review'}\t{one['title']} ({checks})")
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser(prog="agent-desk board", description="What the inbox's board shows.")
    parser.add_argument("--repo", "-R")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        repo = args.repo or store.repo_here()
        desk = store.Store(read_only=True)
        try:
            board = read(desk, repo)
        finally:
            desk.close()
    except (store.StoreError, sqlite3.Error) as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    out = json.dumps(board) if args.json else show(board)
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
