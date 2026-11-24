"""Finds the merge queue's oldest review when it comes from an agent/<slug> branch and conflicts with main, or when
the queue found that main broke its checks, and asks for an agent to resolve the conflicts or adapt it to main:

    python3 fixes.py status <main checkout> [--json]   shows each review a fix would mend and what its fix waits on,
                                                      and starts nothing
    python3 fixes.py tick <main checkout> [--json]     asks for a fix of the review due one, with
                                                      `agent-desk request fix <n>`

It reads the open reviews, whether each merges with its base, and the runs from the local store, and each branch's
head from git in the main checkout. lib/merges.py marks a queued review's head as broken by main when the review's
checks passed until the queue merged main into it, or until a fix resolved its conflicts with main, and failed since,
retry included. Each merge moves main and can
bring back the conflicts of a review fixed before it, so a review gets its fix only once it is the queue's oldest,
which holds the queue until its fix lands, and main stays where the fix merged it.
A review outside the queue waits for Merge, and one behind others in the queue for its turn, except a ready review
whose re-check failed, which the dispatcher queues after its base moved: main broke its checks, and its fix starts
without waiting for Merge. The fix waits while a run
works the review or its issue or waits to, and while anybody works in its worktree. Each start is noted in
~/.local/state/agent-desk/fixes/<owner>__<repo>.tsv by the review's head and its base's, main or the branch a
`base:<branch>` label set. A review gets one fix for each commit its base reaches, and `fixes.maxTries` in all for one
head: a fix that resolves the conflicts moves the head, and one that fails leaves the review in Needs You once the
tries run out. A fix of what main broke works its head once: a later head whose checks fail leaves the queue. Each
status and tick saves the plan it shows in ~/.local/state/agent-desk/fixes/<owner>__<repo>.plan.json, which
`agent-desk board` reads; a tick with fixes off saves an empty one.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import merges  # noqa: E402
import store  # noqa: E402

STATE = store.STATE / "fixes"
# The command that asks the dispatcher for a run.
DESK = Path(__file__).resolve().parent.parent / "bin" / "agent-desk"
DEFAULTS = {"auto": True, "maxTries": 2}
# Starts older than this drop out of the ledger.
KEEP_SECONDS = 30 * 86400
# An issue's, a chore's, or an interactive session's branch, as agent-desk worktree names them.
AGENT_BRANCH = re.compile(r"^agent/[a-z0-9][a-z0-9-]*$")


def settings(config_path):
    """The project's `fixes` settings over the defaults."""
    try:
        with open(config_path, encoding="utf-8") as handle:
            mine = json.load(handle).get("fixes", {})
    except (OSError, ValueError):
        mine = {}
    return {**DEFAULTS, **(mine if isinstance(mine, dict) else {})}


def ledger_path(repo):
    return STATE / f"{store.project_key(repo)}.tsv"


def plan_path(repo):
    """Where each status or tick saves the plan it showed, which the board reads without asking GitHub."""
    return STATE / f"{store.project_key(repo)}.plan.json"


def save_plan(repo, plan):
    """Saves a plan, a list of {pr, verdict}, in place of the last one."""
    path = plan_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(plan) + "\n", encoding="utf-8")
    temporary.replace(path)


def saved_plan(repo):
    """The plan the last status or tick saved, as a list of {pr, verdict}; empty before the first."""
    try:
        plan = json.loads(plan_path(repo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [one for one in plan if isinstance(one, dict) and isinstance(one.get("pr"), int)
            and isinstance(one.get("verdict"), str)] if isinstance(plan, list) else []


def read_ledger(path, now):
    """Each fix started in the last KEEP_SECONDS, as (started, pr, head, base head)."""
    starts = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return starts
    for line in lines:
        fields = line.split("\t")
        if len(fields) == 4 and fields[0].isdigit() and fields[1].isdigit():
            if now - int(fields[0]) <= KEEP_SECONDS:
                starts.append((int(fields[0]), int(fields[1]), fields[2], fields[3]))
    return starts


def note(path, now, pr, head, base_sha):
    """Adds a start to the ledger, with the head of the PR's base, dropping the ones too old to matter."""
    starts = read_ledger(path, now) + [(now, pr, head, base_sha)]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text("".join(f"{at}\t{number}\t{head}\t{sha}\n" for at, number, head, sha in starts),
                         encoding="utf-8")
    temporary.replace(path)


def base_of(pr):
    """The branch a PR merges into, main when the PR list leaves it out."""
    return pr.get("baseRefName") or "main"


def worktree_of(main, branch):
    """The worktree an agent branch works in: ../<repo>-<slug> beside the main checkout."""
    main = Path(main)
    return main.parent / f"{main.name}-{branch.removeprefix('agent/')}"


def in_use(directory, proc="/proc"):
    """Whether any process works in the directory or below it, such as an agent or a resumed session."""
    directory = str(directory)
    for entry in Path(proc).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        if cwd == directory or cwd.startswith(directory + "/"):
            return True
    return False


def plan(prs, active, starts, bases, chosen, busy=lambda pr: False, queue=(), broken=None):
    """
    What to do about each review from an agent/<slug> branch that conflicts with main or whose checks main broke, as
    (pr, verdict) pairs: verdict is "start", or why the fix waits. `prs` are the open reviews under the names gh gives
    a PR's fields, `active` the numbers of the reviews a run works or waits to, `bases` maps each review's base branch
    to its head, `busy` says whether a review's worktree is in use, `queue` lists the merge queue's reviews, oldest
    first, and `broken` maps a queued review to the head whose checks main broke, as lib/merges.py notes it. A review
    whose `baseBroke` says its head's re-check failed needs no turn in the queue.
    """
    broken = broken or {}
    verdicts = []
    for pr in sorted(prs, key=lambda one: one["number"]):
        branch = pr["headRefName"]
        rechecked = bool(pr.get("baseBroke"))
        mends = pr.get("mergeable") == "CONFLICTING" or broken.get(pr["number"]) == pr["headRefOid"] or rechecked
        if pr.get("isDraft") or not mends:
            continue
        if not AGENT_BRANCH.match(branch):
            continue
        tries = [start for start in starts if start[1] == pr["number"] and start[2] == pr["headRefOid"]]
        if not rechecked and pr["number"] not in queue:
            verdict = "auto-fix on Merge"
        elif not rechecked and pr["number"] != queue[0]:
            verdict = "auto-fix when next to merge"
        elif pr["number"] in active:
            verdict = "agent working or queued"
        elif busy(pr):
            verdict = "worktree in use"
        elif any(start[3] == bases.get(base_of(pr)) for start in tries):
            verdict = f"auto-fix tried this {base_of(pr)}; retries when {base_of(pr)} moves"
        elif len(tries) >= int(chosen["maxTries"]):
            verdict = f"{len(tries)} auto-fixes failed; use Fix"
        else:
            verdict = "start"
        verdicts.append((pr, verdict))
    return verdicts


def active_reviews(desk, repo):
    """
    The numbers of the reviews a run works or waits to: a fix run's, an agent run's on a review, and the review of
    the agent/issue-<n> branch an agent run on issue n works.
    """
    active = set()
    for row in desk.runs(("requested", "running")):
        target = row["target"] or ""
        if row["project"] != repo:
            continue
        if row["kind"] == "fix" and target.isdigit():
            active.add(int(target))
        elif row["kind"] == "agent" and target.removeprefix("pr-").isdigit():
            number = int(target[3:]) if target.startswith("pr-") else \
                desk.review_number(repo, f"agent/issue-{target}")
            if number is not None:
                active.add(number)
    return active


def gather(desk, repo, checkout):
    """
    The open reviews, under the names gh gives a PR's fields, each with its branch's head from git in the main
    checkout and `baseBroke`, whether that head failed its re-check after its base moved, and the head of each of their
    bases.
    """
    prs, bases = [], {}
    for review in desk.items(repo, "review", limit=10000):
        head = merges.rev(checkout, f"refs/heads/{review['headRefName']}")
        if head is None:
            continue
        prs.append({key: review[key] for key in ("number", "title", "headRefName", "baseRefName", "isDraft",
                                                 "mergeable")} | {
            "headRefOid": head, "baseBroke": desk.base_broke(repo, review["headRefName"], head)})
        if base_of(review) not in bases:
            bases[base_of(review)] = merges.rev(checkout, f"refs/heads/{base_of(review)}")
    return prs, bases


def request_fix(repo, number):
    """Asks the dispatcher for a fix run of review `number`, and returns None or why it couldn't."""
    done = subprocess.run([str(DESK), "request", "fix", str(number)], capture_output=True, text=True,
                          env={**os.environ, "GH_REPO": repo}, stdin=subprocess.DEVNULL)
    return None if done.returncode == 0 else (done.stderr or done.stdout).strip() or "agent-desk request failed"


def main(argv):
    as_json = argv[3:] == ["--json"]
    if len(argv) != 3 + as_json or argv[1] not in ("status", "tick"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    mode, checkout = argv[1], argv[2]
    chosen = settings(Path(checkout) / ".agent-desk.json")
    repo = os.environ["GH_REPO"]
    if mode == "tick" and not chosen["auto"]:
        # With fixes off, no PR waits on one, so the board shows none.
        save_plan(repo, [])
        if not as_json:
            print("Fixes are off in .agent-desk.json.")
            return 0
        mode = "status"
    path = ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    shown, status = [], 0
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        # A tick that finds another under way, as the inbox's and the timer's can, starts nothing and shows the plan.
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            mode = "status"
        now = int(time.time())
        items = merges.load(merges.state_path(repo), now)["queue"]
        queue = [item["pr"] for item in items]
        broken = {item["pr"]: item["brokenHead"] for item in items if item.get("brokenHead")}
        desk = store.Store()
        try:
            prs, bases = gather(desk, repo, checkout)
            active = active_reviews(desk, repo)
        finally:
            desk.close()
        verdicts = plan(prs, active, read_ledger(path, now), bases, chosen,
                        busy=lambda pr: in_use(worktree_of(checkout, pr["headRefName"])), queue=queue, broken=broken)
        for pr, verdict in verdicts:
            if mode == "status" or verdict != "start":
                shown.append((pr, "auto-fix due now" if verdict == "start" else verdict))
                continue
            failed = request_fix(repo, pr["number"])
            if failed is None:
                note(path, now, pr["number"], pr["headRefOid"], bases[base_of(pr)])
                shown.append((pr, "auto-fix started"))
            else:
                shown.append((pr, f"auto-fix couldn't start: {failed}"))
                status = 1
    showing = [{"pr": pr["number"], "verdict": verdict} for pr, verdict in shown]
    save_plan(repo, showing)
    if as_json:
        print(json.dumps(showing))
        return 0
    if not shown:
        print("No review from an agent branch conflicts with main or has checks main broke.")
    for pr, verdict in shown:
        print(f"#{pr['number']} {pr['title']}: {verdict}")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
