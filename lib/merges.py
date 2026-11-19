"""Merges the reviews queued with Merge in order, each tested against the main it lands on, in local git:

    python3 merges.py status <main checkout> [--json]   shows the queue, the reviews that left it, how far main is
                                                       ahead of origin, and the push queue, and changes nothing
    python3 merges.py add <main checkout> <n>...        queues reviews to merge, for the next tick, and refuses
                                                       inside an agent run
    python3 merges.py remove <main checkout> <n>...     takes reviews out of the queue
    python3 merges.py tick <main checkout> [--json]     moves the queue on, has lib/pushes.py push what it merged,
                                                       then shows the queue

The queue reads each review's branch, draft flag, and checks from the local store, and each branch's head from git in
the main checkout. It merges, tests, and lands reviews with no GitHub call. A review's check, which lib/checks.py runs,
tests its head merged with main as main stood when the check started, so two reviews that each pass alone can break
main together.

A tick first tries a batch: when the oldest queued reviews, up to BATCH of them, each passed their own checks and merge
cleanly, it merges them in queue order onto a merge-queue/ branch cut from main, with `git merge-tree`, which writes no
worktree, queues a check of the branch's head, and waits. Once the check passes, it fast-forwards main to the branch,
which merges the reviews exactly as tested, and goes on. A failed batch halves the next one, so a review that breaks
main ends up tested alone; a batch whose main, or a member's branch, moved underneath it starts again. Otherwise the
tick takes the oldest queued review alone. When the review's head lacks main's newest commit, the tick merges main into
the branch, inside the branch's worktree when it has one, and with `git merge-tree` when it has none, and queues a check
of the new head. A worktree that can't take the merge, as with uncommitted changes or an untracked file where main adds
one, keeps its review waiting with a note that names the cause, and the tick goes on to the reviews behind it. Once a
check of a head that holds main passes, the tick fast-forwards main to a merge commit of the review, whose files match
the tested head, and goes on to the next. A review from an agent branch that conflicts with main, or whose checks passed
until the tick merged main into it and fail since, holds the queue while lib/fixes.py has an agent resolve its conflicts
or adapt it to main, for up to FIX_WAIT. A review whose checks fail, that no fix mends, or that can't take main leaves
the queue, says why in a comment on the review, and Needs You shows why for a day, or until its branch moves. A project
without a `test` command has no checks, so its reviews merge once they hold main, one at a time.

Each fast-forward holds the main checkout's lock, .git/agent-desk-main.lock, which a release also takes. When a worktree
has the base branch checked out, as the main checkout has main, the tick moves it with `git merge --ff-only`, and never
while it holds uncommitted changes to a tracked file the merge changes: the queue stops there, and the board says why,
until the changes are committed or undone. Once main moves, the tick records each review merged with its merge commit.
Before it decides anything for a review, the tick checks whether the review's base already holds its head, as after a
tick that moved main and died before saving the queue, or a store that couldn't record the merge, and records the review
merged with the commit already there, so no review merges twice. After the tick lets go of the queue, it restarts the
dispatcher and the web page's server when the merge changed their code in agent-desk's own checkout, runs the config's
`sync.run` in the main checkout unless a program in `sync.skipWhile` runs there, and has lib/pushes.py push main to
origin.

The queue lives in ~/.local/state/agent-desk/merges/<owner>__<repo>.json. A tick that finds another tick under way
changes nothing and shows the queue as that one left it. Each review lands on its own base, main for most, and "main"
above means that base: a review that targets another branch, as an issue labelled base:<branch> asks, compares with,
takes in, and merges into that branch, and a batch holds only reviews that share the oldest one's base.
"""

import ast
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
import fixes  # noqa: E402
import pushes  # noqa: E402
import store  # noqa: E402
from config import lookup, read as read_config  # noqa: E402

STATE = store.STATE / "merges"
# How long Needs You shows why a review left the queue.
KEEP_DROPPED = 86400
# How long a review that conflicts with main, or whose checks main broke, holds the queue for an auto-fix to mend it.
FIX_WAIT = 3600
# What decide gives fix_step to mend: a conflict with main, or checks that passed until main merged in.
CONFLICTS = "conflicts with main"
BROKE = "main broke its checks"
# Ends every note agent-desk writes on a review, as lib/common.sh's desk_mark does.
DESK_MARK = "\n\n<sub>Posted by agent-desk</sub>"
# The most reviews one batch tests together.
BATCH = 4
# Where batches are tested; nothing else writes there, and each batch's branch is deleted when it ends.
BATCH_PREFIX = "merge-queue/"
# The checks that wait or work, and those that passed, as the store's `checks` rows name them.
PENDING = ("queued", "running")
PASSED = "passed"
# The module the dispatcher's serve process starts from; dispatcher_code finds the lib/ files it runs.
DISPATCHER = "dispatch"
DISPATCH_UNIT = "agent-desk-dispatch.service"
# The folders the web page's server runs from, whose change in agent-desk's own checkout restarts it.
WEB_CODE = ("web/", "inbox/hooks/")
WEB_UNIT = "agent-desk-web.service"


class GitError(Exception):
    """A git command that failed where the queue can't go on, which leaves the queue as it stands for the next tick."""


def state_path(repo):
    return STATE / f"{store.project_key(repo)}.json"


def load(path, now):
    """
    The queue, oldest first, the reviews that left it in the last KEEP_DROPPED seconds, the batch under test if any,
    and how many reviews the next batch may take.
    """
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    queue = [item for item in state.get("queue", []) if isinstance(item, dict) and isinstance(item.get("pr"), int)]
    dropped = [one for one in state.get("dropped", []) if isinstance(one, dict) and isinstance(one.get("pr"), int)
               and now - int(one.get("at", 0)) <= KEEP_DROPPED]
    loaded = {"queue": queue, "dropped": dropped}
    if isinstance(state.get("limit"), int):
        loaded["limit"] = state["limit"]
    if isinstance(state.get("batch"), dict):
        loaded["batch"] = state["batch"]
    return loaded


def save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


# Git in the main checkout.

def git(where, *args, check=True):
    """Runs git in `where`, raising GitError with the first line git printed when it fails and `check` is set."""
    done = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and done.returncode != 0:
        said = next((line for line in (done.stderr + done.stdout).splitlines() if line.strip()), "no output")
        raise GitError(f"git {args[0]} failed: {said}")
    return done


def rev(where, ref):
    """The commit `ref` names, or None when there is none."""
    done = git(where, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def holds(checkout, head, commit):
    """Whether `head` holds `commit`."""
    return git(checkout, "merge-base", "--is-ancestor", commit, head, check=False).returncode == 0


def names(where, *args):
    """The file names a `git diff --name-only` with `args` prints."""
    return {name for name in git(where, "diff", "--name-only", "-z", *args).stdout.split("\0") if name}


def listing(files):
    """Up to three file names, and how many more there are."""
    files = sorted(files)
    return ", ".join(files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")


def landed_as(checkout, base_sha, head):
    """
    The commit that brought `head` into a base that holds it at `base_sha`: the oldest of the base's commits with `head`
    as a parent, as each merge commit the queue makes has, or the head itself when the base took it as it was.
    """
    listed = git(checkout, "rev-list", "--topo-order", "--reverse", "--parents", base_sha, f"^{head}")
    for line in listed.stdout.splitlines():
        commit, *parents = line.split()
        if head in parents:
            return commit
    return head


def worktree_of(checkout, branch):
    """The folder of the worktree that has `branch` checked out, or None when none has or its folder is gone."""
    folder = None
    for line in git(checkout, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            folder = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}" and folder is not None and folder.is_dir():
            return folder
    return None


def merge_commit(checkout, base_sha, head, message):
    """
    A merge commit of `head` onto `base_sha`, made with `git merge-tree`, which writes no worktree, or None when they
    conflict. A head that holds `base_sha` gives a commit with the head's own files.
    """
    done = git(checkout, "merge-tree", "--write-tree", "--no-messages", base_sha, head, check=False)
    if done.returncode == 1:
        return None
    if done.returncode != 0:
        raise GitError(f"git merge-tree failed: {done.stderr.strip() or 'no output'}")
    tree = done.stdout.split()[0]
    return git(checkout, "commit-tree", tree, "-p", base_sha, "-p", head, "-m", message).stdout.strip()


def review_message(number, pr):
    return f"Merge review #{number} from {pr['headRefName']}\n\n{pr['title']}"


def take_main(checkout, branch, head, base, base_sha):
    """
    Merges `base` at `base_sha` into `branch` at `head`, in the branch's worktree when it has one, and returns
    (new head, None), or (None, why) when it couldn't: CONFLICTS, or why the branch's worktree can't take the merge,
    such as uncommitted changes or an untracked file where main adds one.
    """
    message = f"Merge {base} into {branch}"
    folder = worktree_of(checkout, branch)
    if folder is None:
        made = merge_commit(checkout, head, base_sha, message)
        if made is None:
            return None, CONFLICTS
        git(checkout, "update-ref", "-m", f"agent-desk merge queue: {message}", f"refs/heads/{branch}", made, head)
        return made, None
    if git(folder, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return None, f"its worktree {folder} has uncommitted changes"
    # The files main added since the branch left it, which git won't write over an untracked file.
    untracked = set(git(folder, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")) - {""}
    clash = untracked & names(checkout, "--no-renames", "--diff-filter=A", f"{head}...{base_sha}")
    if clash:
        return None, f"its worktree {folder} has untracked {listing(clash)}, which merging {base} would overwrite"
    done = git(folder, "merge", "--no-edit", "--no-verify", "-m", message, base_sha, check=False)
    if done.returncode != 0:
        conflicted = names(folder, "--diff-filter=U")
        git(folder, "merge", "--abort", check=False)
        if checks.merges_with(checkout, head, base_sha) == "CONFLICTING":
            return None, CONFLICTS
        if conflicted:
            said = f"{base} conflicts in {listing(conflicted)}"
        else:
            said = next((line for line in (done.stderr + done.stdout).splitlines() if line.strip()), "no output")
        return None, f"git couldn't merge {base} in its worktree {folder}: {said}"
    return rev(folder, "HEAD"), None


def lock_path(checkout):
    """The main checkout's lock, which lib/pushes.py also holds while it publishes a release."""
    return Path(checkout) / ".git" / "agent-desk-main.lock"


@contextmanager
def main_lock(checkout):
    with open(lock_path(checkout), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def land(checkout, base, old, new):
    """
    Fast-forwards `base` from `old` to `new` under the main checkout's lock, moving the worktree that has it checked
    out. Returns None once it moved, or (kind, why, stop) when it didn't: "stopped" for a worktree with uncommitted
    changes to a file the merge changes, or an untracked file where the merge adds one, which waits for the person, with
    the stop as stop_of gives it, or "moved", with no stop, for a base that moved or a fast-forward git refused, which
    the queue tries afresh.
    """
    with main_lock(checkout):
        if rev(checkout, f"refs/heads/{base}") != old:
            return "moved", f"{base} moved", None
        folder = worktree_of(checkout, base)
        if folder is None:
            git(checkout, "update-ref", "-m", "agent-desk merge queue", f"refs/heads/{base}", new, old)
            return None
        # Files git would refuse to overwrite: tracked ones with changes, and untracked ones the merge adds.
        untracked = git(folder, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
        clash = names(checkout, old, new) & (names(folder, "HEAD") | set(untracked))
        if clash:
            stop = stop_of(checkout, folder, clash)
            return "stopped", f"stopped: {stop['where']} has uncommitted changes to {stop['files']}, which the merge " \
                "changes", stop
        done = git(folder, "merge", "--ff-only", "-q", new, check=False)
        if done.returncode != 0:
            said = next((line for line in done.stderr.splitlines() if line.strip()), "no output")
            return "moved", f"git couldn't fast-forward {base}: {said}", None
    return None


def stop_of(checkout, folder, files):
    """
    What the board shows of a stop: where the changes are, as "the main checkout" or the worktree's folder name, and
    the files, as listing gives them.
    """
    where = "the main checkout" if Path(folder).resolve() == Path(checkout).resolve() else Path(folder).name
    return {"where": where, "files": listing(files)}


# What the queue reads.

def checks_of(desk, repo, branch, head):
    """
    The checks of `head` on `branch`: a queued or running one when the head has one, as a first attempt's failure
    queues a second, and else the store's newest attempt.
    """
    pending = [row for row in desk.checks(PENDING, repo, branch) if row["head_sha"] == head]
    if pending:
        return [{"name": row["name"], "status": row["status"], "attempt": row["attempt"]} for row in pending[-1:]]
    return desk.newest_checks(repo, branch, head)


def gather(desk, repo, checkout, number, mains):
    """
    The review as decide takes it, its head's checks, whether its head holds its base's newest commit, and that commit.
    An open review whose base already holds its head carries the commit that brought it in as `landedAs`, as after a
    tick that moved the base but didn't live to record the review. `mains` caches each base's newest commit across the
    reviews of one step.
    """
    try:
        review = desk.item(repo, number, "review")
    except store.StoreError:
        return {"state": "GONE"}, [], False, None
    base, branch = review["baseRefName"], review["headRefName"]
    if base not in mains:
        mains[base] = rev(checkout, f"refs/heads/{base}")
    main_sha = mains[base]
    head = rev(checkout, f"refs/heads/{branch}")
    pr = {"state": review["state"], "isDraft": review["isDraft"], "headRefName": branch, "baseRefName": base,
          "title": review["title"], "headRefOid": head}
    if pr["state"] != "OPEN" or head is None or main_sha is None:
        return pr, [], False, main_sha
    if holds(checkout, main_sha, head):
        pr["landedAs"] = landed_as(checkout, main_sha, head)
        return pr, [], True, main_sha
    has_main = holds(checkout, head, main_sha)
    pr["mergeable"] = "MERGEABLE" if has_main else (checks.merges_with(checkout, head, main_sha) or "UNKNOWN")
    return pr, checks_of(desk, repo, branch, head), has_main, main_sha


def sight(desk, repo, checkout, number, seen):
    """gather, once per review per step: `seen` holds this step's answers, and its "mains" entry each base's head."""
    if number not in seen:
        seen[number] = gather(desk, repo, checkout, number, seen.setdefault("mains", {}))
    return seen[number]


# What the queue decides.

def decide(pr, found, has_main, main_sha, item, testing=True):
    """
    The next step for the queue's oldest review, as (step, why): step is "drop", "wait", "check", "update", "fix",
    "merge", or "landed", and why says what a dropped review or a waiting one waits on. "landed" records as merged a
    review its base already holds, "fix" leaves to fix_step a review that conflicts with main (CONFLICTS), or whose
    checks fail since the queue merged main into it after they passed (BROKE), and "check" queues a check of a head that
    has none. `found` are its head's checks, `has_main` says whether the head holds main's newest commit `main_sha`,
    `testing` whether the project has a `test` command, and `item` is the review's place in the queue, which notes the
    main merged into it last, whether its checks had passed then, and the head a fix of what main broke started from.
    """
    if pr["state"] == "GONE":
        return "drop", "it's no longer in the store"
    if pr["state"] == "MERGED":
        return "drop", None
    if pr["state"] == "CLOSED":
        return "drop", "closed"
    if pr.get("landedAs"):
        return "landed", None
    if pr.get("isDraft"):
        return "drop", "converted to draft"
    if not pr.get("headRefOid"):
        return "drop", f"its branch {pr['headRefName']} is gone"
    if main_sha is None:
        return "drop", f"the main checkout has no {pr['baseRefName']} branch"
    if pr.get("mergeable") == "CONFLICTING":
        return "fix", CONFLICTS
    if pr.get("mergeable") != "MERGEABLE":
        return "wait", "git couldn't try the merge"
    # A fix of what main broke works this head, so main moving meanwhile doesn't merge in under it.
    if item.get("passed") and item.get("brokenHead") == pr["headRefOid"]:
        return "fix", BROKE
    if not has_main:
        if item.get("main") == main_sha:
            return "drop", "main merge didn't land"
        return "update", None
    if any(one["status"] == "queued" for one in found):
        return "wait", "checks starting"
    if any(one["status"] == "running" for one in found):
        return "wait", "checks running"
    if not found:
        return ("check", None) if testing else ("merge", None)
    if any(one["status"] in store.CHECK_FAILURES for one in found):
        if item.get("passed"):
            # A fix moves the head, so failed checks on a head after the one it mended are the fix's.
            if "brokenHead" in item:
                return "drop", f"{BROKE}; auto-fix couldn't make them pass"
            return "fix", BROKE
        return "drop", "checks failed"
    return "merge", None


def fix_step(pr, number, starts, active, main_sha, item, now, chosen, cause=CONFLICTS):
    """
    The next step for a queued review that conflicts with main, or whose checks main broke, as decide gives it with
    its `cause`: wait while lib/fixes.py will fix it or a fix works it, and drop it once a fix fails, after FIX_WAIT, or
    when no fix will come. `starts` are the fixes ledger's, `active` the reviews a run works or waits to, and `item`
    notes the head the queue first saw need a fix, and when. A fix that resolves conflicts moves the head, which may
    conflict again later and waits afresh; a fix of what main broke gets one wait, since decide drops the review once a
    later head fails.
    """
    broke = cause == BROKE
    if not chosen["auto"] or not fixes.AGENT_BRANCH.match(pr["headRefName"]):
        return "drop", "checks failed" if broke else cause
    if broke:
        item.setdefault("brokenHead", pr["headRefOid"])
        since = int(item.setdefault("brokenAt", now))
    else:
        # A head the fix's merge of main makes comes from the fix, not from the queue's merge of main.
        item.pop("passed", None)
        if item.get("conflictHead") != pr["headRefOid"]:
            item.update(conflictHead=pr["headRefOid"], conflictAt=now)
        since = int(item["conflictAt"])
    tries = [start for start in starts if start[1] == number and start[2] == pr["headRefOid"]]
    if number not in active and (any(start[3] == main_sha for start in tries) or len(tries) >= int(chosen["maxTries"])):
        return "drop", f"{cause}; auto-fix couldn't {'make them pass' if broke else 'resolve them'}"
    if now - since >= FIX_WAIT:
        return "drop", f"{cause}; no auto-fix landed in {FIX_WAIT // 60} minutes"
    return "wait", f"{cause}; auto-fix {'adapting it to main' if broke else 'resolving'}"


def batchable(pr, found):
    """Whether a queued review may join a batch: open, not yet in its base, mergeable, and its newest checks passed."""
    return (pr["state"] == "OPEN" and not pr.get("isDraft") and not pr.get("landedAs")
            and pr.get("mergeable") == "MERGEABLE" and bool(found)
            and all(one["status"] == PASSED for one in found))


def choose_batch(sights, head_step):
    """
    The queued reviews to test together, oldest first: the run of batchable reviews at the head of `sights`, the
    (number, pr, checks) of the oldest queued reviews up to the batch limit, that share the oldest one's base. Empty
    when fewer than two qualify, or when the oldest is ready to merge alone (`head_step` is decide's step for it).
    """
    if head_step == "merge":
        return []
    members = []
    for number, pr, found in sights:
        if not batchable(pr, found) or pr["baseRefName"] != sights[0][1]["baseRefName"]:
            break
        members.append((number, pr))
    return members if len(members) > 1 else []


def batch_step(batch, queue, main_sha, found, heads):
    """
    The next step for the batch under test, as (step, why): "land" once the check of the batch's head passed, "wait"
    while it waits or runs, "fail" when it fails or was cancelled, and "abandon" when the queue's head, main, or a
    member's branch moved since the batch began. `found` are the checks of the batch's head, and `heads` each member's
    branch head now, in the batch's order.
    """
    if [item["pr"] for item in queue[:len(batch["prs"])]] != batch["prs"]:
        return "abandon", "the queue changed"
    if main_sha != batch["main"]:
        return "abandon", "main moved"
    tested = batch.get("heads") or []
    if len(tested) != len(batch["prs"]) or list(heads) != tested:
        moved = [f"#{n}" for n, was, now in zip(batch["prs"], tested, heads) if was != now] or ["a member"]
        return "abandon", f"{', '.join(moved)} moved"
    if not found:
        return "fail", "check never ran"
    if any(one["status"] == "queued" for one in found):
        return "wait", "checks starting"
    if any(one["status"] == "running" for one in found):
        return "wait", "checks running"
    if any(one["status"] != PASSED for one in found):
        return "fail", "checks failed"
    return "land", None


# How the queue moves on.

class Tick:
    """One tick's view of the project: the store, the main checkout, and what landed, for after the queue's lock."""

    def __init__(self, desk, repo, checkout, now, say=print, chosen=None, testing=None):
        self.desk, self.repo, self.checkout, self.now, self.say = desk, repo, Path(checkout), now, say
        self.chosen = chosen or fixes.DEFAULTS
        config = read_config(checkout)
        self.testing = bool(checks.test_command(config)) if testing is None else testing
        # Each landing, as (base, old head, new head).
        self.landed = []

    def record(self, number, sha):
        """
        Records the review merged as `sha`, and returns None, or why the store couldn't, which leaves the review queued
        for a later tick to find in its base and record.
        """
        try:
            self.desk.merge(self.repo, number, sha)
        except store.StoreError as problem:
            self.say(f"#{number}: merged as {sha[:12]}, but the store couldn't record it: {problem}")
            return f"merged as {sha[:7]}; the store couldn't record it: {problem}"
        self.say(f"#{number}: merged.")
        return None

    def comment(self, number, body):
        """A note on a review from the desk; a store that can't take it changes nothing else."""
        try:
            with desk_author():
                self.desk.comment(self.repo, number, body + DESK_MARK)
        except store.StoreError:
            pass


@contextmanager
def desk_author():
    """Writes as the desk, as lib/common.sh's desk_store does."""
    saved = {name: os.environ.get(name) for name in ("AGENT_DESK_RUN", "AGENT_DESK_AUTHOR")}
    os.environ.pop("AGENT_DESK_RUN", None)
    os.environ["AGENT_DESK_AUTHOR"] = "desk"
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def tick(at, state):
    """
    Moves the queue on until it waits: tests the oldest reviews as a batch when it can, and otherwise drops, merges,
    or merges main into its oldest review. A review whose worktree can't take main waits there, and the queue goes on
    to the reviews behind it.
    """
    queue = state["queue"]
    # A stop lasts until a tick finds the queue can move: each tick that lands on the same changes marks it again.
    for item in queue:
        item.pop("stopped", None)
    # The reviews that wait on their own worktree this tick, which the queue goes past.
    aside = set()
    while any(item["pr"] not in aside for item in queue):
        if "batch" in state:
            if batch_tick(at, state):
                continue
            return
        # A step that merges moves main, so each step looks afresh. A batch takes the queue's oldest reviews, so none
        # starts past a review set aside.
        seen = {}
        if (not aside and start_batch(at, state, seen)) or not step_one(at, state, seen, aside):
            return
    if "batch" in state:
        end_batch(at, state)


def start_batch(at, state, seen):
    """
    Starts testing the oldest queued reviews together when choose_batch finds two or more, and returns whether it did.
    It looks past the oldest review only when that one could join a batch, and stops at the first that could not.
    """
    queue = state["queue"]
    limit = min(BATCH, int(state.get("limit", BATCH)))
    if not at.testing or limit < 2 or len(queue) < 2:
        return False
    sights, head = [], None
    for item in queue[:limit]:
        pr, found, has_main, main_sha = sight(at.desk, at.repo, at.checkout, item["pr"], seen)
        if pr["state"] != "OPEN" or main_sha is None:
            break
        if head is None:
            head = (decide(pr, found, has_main, main_sha, item, at.testing)[0], main_sha, pr["baseRefName"])
        elif pr["baseRefName"] != head[2]:
            # A batch lands on one base, so a review for another ends it.
            break
        sights.append((item["pr"], pr, found))
        if not batchable(pr, found) or head[0] == "merge":
            break
    if head is None:
        return False
    members = choose_batch(sights, head[0])
    if not members:
        return False
    _, main_sha, base = head
    sha, prs, merges, heads, branches = main_sha, [], [], [], []
    for number, pr in members:
        made = merge_commit(at.checkout, sha, pr["headRefOid"], review_message(number, pr))
        if made is None:
            break
        sha = made
        prs.append(number)
        merges.append(made)
        heads.append(pr["headRefOid"])
        branches.append(pr["headRefName"])
    if len(prs) < 2:
        return False
    branch = f"{BATCH_PREFIX}{at.now}"
    git(at.checkout, "update-ref", f"refs/heads/{branch}", sha)
    check = at.desk.check_queue(at.repo, branch, sha)
    state["batch"] = {"prs": prs, "merges": merges, "heads": heads, "branches": branches, "branch": branch, "sha": sha,
                      "main": main_sha, "base": base, "at": at.now, "check": check}
    note_batch(state, "checks starting")
    at.say(f"{', '.join(f'#{n}' for n in prs)}: testing together on {branch}.")
    return True


def note_batch(state, why):
    prs = state["batch"]["prs"]
    for item in state["queue"][:len(prs)]:
        others = ", ".join(f"#{n}" for n in prs if n != item["pr"])
        item["note"] = f"batch with {others}: {why}"


def end_batch(at, state):
    """Deletes the batch's branch and ends a check of it that still waits or runs."""
    batch = state.pop("batch")
    if any(row["head_sha"] == batch["sha"] for row in at.desk.checks(PENDING, at.repo, batch["branch"])):
        for row in at.desk.branch_moved(at.repo, batch["branch"], None, batch["sha"]):
            try:
                subprocess.run(["systemctl", "--user", "stop", "--no-block", checks.unit_name(at.repo, row["id"])],
                               capture_output=True)
            except OSError:
                pass
    git(at.checkout, "update-ref", "-d", f"refs/heads/{batch['branch']}", check=False)


def batch_tick(at, state):
    """Moves the batch under test on, and returns whether the queue may go on to its next step this tick."""
    batch = state["batch"]
    main_sha = rev(at.checkout, f"refs/heads/{batch['base']}")
    heads = [rev(at.checkout, f"refs/heads/{branch}") for branch in batch.get("branches", [])]
    step, why = batch_step(batch, state["queue"], main_sha, checks_of(at.desk, at.repo, batch["branch"], batch["sha"]),
                           heads)
    if step == "wait":
        note_batch(state, why)
        return False
    if step == "land":
        problem = land(at.checkout, batch["base"], batch["main"], batch["sha"])
        if problem is None:
            at.landed.append((batch["base"], batch["main"], batch["sha"]))
            members = state["queue"][:len(batch["prs"])]
            unrecorded = []
            for item, sha in zip(members, batch["merges"]):
                why = at.record(item["pr"], sha)
                if why:
                    item["note"] = why
                    unrecorded.append(item)
            state["queue"][:len(members)] = unrecorded
            state.pop("limit", None)
            end_batch(at, state)
            return not unrecorded
        kind, why, stop = problem
        if kind == "stopped":
            note_batch(state, why)
            for item in state["queue"][:len(batch["prs"])]:
                item["stopped"] = stop
            return False
        step = "abandon"
    listed = ", ".join(f"#{n}" for n in batch["prs"])
    if step == "fail":
        state["limit"] = max(1, len(batch["prs"]) // 2)
        at.say(f"{listed}: the batch's {why}, so the next batch takes {state['limit']}.")
    else:
        at.say(f"{listed}: the batch starts again, since {why}.")
    for item in state["queue"]:
        item.pop("note", None)
    end_batch(at, state)
    return True


def step_one(at, state, seen, aside):
    """
    Moves the oldest queued review not in `aside` on alone, and returns whether the queue may go on to the next. A
    review whose worktree can't take main joins `aside`.
    """
    queue = state["queue"]
    item = next(item for item in queue if item["pr"] not in aside)
    item.pop("aside", None)
    number = item["pr"]
    pr, found, has_main, main_sha = sight(at.desk, at.repo, at.checkout, number, seen)
    step, why = decide(pr, found, has_main, main_sha, item, at.testing)
    if step == "landed":
        # A tick that moved the base and died before it saved the queue, or a store that couldn't record the merge.
        made = pr["landedAs"]
        if made != pr["headRefOid"]:
            at.landed.append((pr["baseRefName"], rev(at.checkout, f"{made}^1"), made))
        why = at.record(number, made)
        if why:
            item["note"] = why
            return False
        queue.remove(item)
        state.pop("limit", None)
        return True
    if step == "check":
        at.desk.check_queue(at.repo, pr["headRefName"], pr["headRefOid"])
        item["note"] = "checks starting"
        return False
    if step == "update":
        new, why = take_main(at.checkout, pr["headRefName"], pr["headRefOid"], pr["baseRefName"], main_sha)
        if new is not None:
            # Checks that passed before main merged in and fail after it are main's doing, which a fix can mend.
            passed = bool(found) and all(one["status"] == PASSED for one in found)
            for key in ("brokenHead", "brokenAt"):
                item.pop(key, None)
            item.update(main=main_sha, updatedAt=at.now, passed=passed,
                        note="merged main; checks starting" if at.testing else "merged main")
            if at.testing:
                at.desk.check_queue(at.repo, pr["headRefName"], new)
            at.say(f"#{number}: merged {pr['baseRefName']} into {pr['headRefName']}.")
            # Without checks to wait for, the merged head lands on the next step.
            return not at.testing
        if why != CONFLICTS:
            item.update(note=why, aside=True)
            aside.add(number)
            at.say(f"#{number}: waits, and the queue goes on: {why}.")
            return True
        step = "fix"
    if step == "fix":
        step, why = fix_step(pr, number, fixes.read_ledger(fixes.ledger_path(at.repo), at.now),
                             fixes.active_reviews(at.desk, at.repo), main_sha, item, at.now, at.chosen, why)
    if step == "merge":
        made = merge_commit(at.checkout, main_sha, pr["headRefOid"], review_message(number, pr))
        if made is None:
            raise GitError(f"#{number} holds {pr['baseRefName']} yet git couldn't merge it")
        problem = land(at.checkout, pr["baseRefName"], main_sha, made)
        if problem is None:
            at.landed.append((pr["baseRefName"], main_sha, made))
            # A merge, as a passed batch does, ends a failed batch's search, so the next tick batches again.
            state.pop("limit", None)
            why = at.record(number, made)
            if why:
                item["note"] = why
                return False
            queue.remove(item)
            return True
        step, why = "wait", problem[1]
        if problem[2] is not None:
            item["stopped"] = problem[2]
    if step == "wait":
        item["note"] = why
        return False
    queue.remove(item)
    # A review that leaves ends a failed batch's search for the review that broke it, so batches grow again.
    state.pop("limit", None)
    if why:
        state["dropped"] = [one for one in state["dropped"] if one["pr"] != number]
        state["dropped"].append({"pr": number, "reason": why, "at": at.now, "head": pr.get("headRefOid")})
        at.say(f"#{number}: left the queue: {why}.")
        at.comment(number, f"Left the merge queue on {(pr.get('headRefOid') or '')[:7]}: {why}.")
    else:
        at.say(f"#{number}: merged.")
    return True


# After the queue's lock: what a landing on main starts.

def running_in(folder, programs):
    """The first of `programs` running with `folder` as its working directory, or None."""
    for name in programs:
        if not isinstance(name, str) or not name.strip():
            continue
        found = subprocess.run(["pgrep", name], capture_output=True, text=True)
        for pid in found.stdout.split():
            try:
                if os.readlink(f"/proc/{pid}/cwd") == str(folder):
                    return name
            except OSError:
                continue
    return None


def runs_from(files, code):
    """Whether any of `files` is one of `code`'s files, or lies in one of its folders, which end in a slash."""
    return any(name == part or (part.endswith("/") and name.startswith(part)) for name in files for part in code)


def dispatcher_code(checkout):
    """
    The files the dispatcher runs from in `checkout`: lib/dispatch.py and each lib/ module it imports, directly or
    through other lib/ modules, as its source in the checkout names them.
    """
    lib = Path(checkout) / "lib"
    seen, todo = set(), [DISPATCHER]
    while todo:
        name = todo.pop()
        path = lib / f"{name}.py"
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        try:
            tree = ast.parse(path.read_text(), str(path))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                todo.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                todo.append(node.module.split(".")[0])
    return tuple(sorted({f"lib/{name}.py" for name in seen | {DISPATCHER}}))


def services(checkout):
    """
    Each service a landing in `checkout` restarts, the files or folders it runs from, and the command that installs it
    again.
    """
    return ((DISPATCH_UNIT, dispatcher_code(checkout), "agent-desk dispatch install"),
            (WEB_UNIT, WEB_CODE, "agent-desk web on"))


def after_landing(checkout, landed, say, desk_root=None, run=subprocess.run):
    """
    Once main moved in the main checkout itself: restarts each active service whose code the merges changed in
    agent-desk's own checkout, and runs the config's sync.run there unless a program in sync.skipWhile runs there.
    """
    checkout = Path(checkout).resolve()
    moved = [(old, new) for base, old, new in landed if worktree_of(checkout, base) == checkout]
    if not moved:
        return
    old, new = moved[0][0], moved[-1][1]
    desk_root = Path(desk_root or Path(__file__).resolve().parent.parent).resolve()
    changed = names(checkout, old, new) if checkout == desk_root else set()
    for unit, code, install in services(checkout) if changed else ():
        if not runs_from(changed, code):
            continue
        active = run(["systemctl", "--user", "--quiet", "is-active", unit], capture_output=True)
        if active.returncode == 0:
            restarted = run(["systemctl", "--user", "restart", unit], capture_output=True)
            say(f"Restarted {unit}." if restarted.returncode == 0 else f"Couldn't restart {unit}; run {install}.")
    config = read_config(checkout)
    sync = lookup(config, "sync.run")
    if not isinstance(sync, str) or not sync.strip():
        return
    skip = lookup(config, "sync.skipWhile")
    busy = running_in(checkout, skip if isinstance(skip, list) else [])
    if busy:
        say(f"Skipped the sync: {busy} is running in {checkout}.")
        return
    # flock -o keeps the lock from the build servers and games the sync leaves running.
    done = run(["flock", "-o", str(lock_path(checkout)), "bash", "-c", sync], cwd=checkout, stdin=subprocess.DEVNULL)
    say(f"Ran the sync in {checkout}." if done.returncode == 0 else f"The sync in {checkout} failed.")


# What the board and the command show.

def shown(state, origin=None):
    """
    The queue as the inbox reads it: each queued review with what it waits on, and the stop that holds it when the queue
    stopped on uncommitted changes to land it; why each dropped review left and the head it had then; and with
    `origin`, how far main is ahead of origin and the push queue's state, as lib/pushes.py's shown gives them.
    """
    queue, ahead = [], None
    batched = set(state.get("batch", {}).get("prs", []))
    for item in state["queue"]:
        note = item.get("note") or "next to merge"
        # A review set aside waits on its own worktree, and the queue goes past it.
        if ahead is not None and item["pr"] not in batched and not item.get("aside"):
            note = f"after #{ahead}"
        if not item.get("aside"):
            ahead = item["pr"]
        one = {"pr": item["pr"], "note": note}
        if item.get("stopped"):
            one["stopped"] = item["stopped"]
        queue.append(one)
    view = {"queue": queue, "dropped": [{"pr": one["pr"], "reason": one["reason"], "head": one.get("head")}
                                        for one in state["dropped"]]}
    if origin is not None:
        view.update(origin)
    return view


def report(view, as_json):
    if as_json:
        print(json.dumps(view))
        return
    if not view["queue"] and not view["dropped"]:
        print("No review waits to merge.")
    for one in view["queue"]:
        print(f"#{one['pr']} merging: {one['note']}")
    for one in view["dropped"]:
        print(f"#{one['pr']} left the queue: {one['reason']}")
    line = pushes.describe(view)
    if line:
        print(line)


def main(argv):
    args = [arg for arg in argv[1:] if arg != "--json"]
    as_json = "--json" in argv
    modes = ("status", "add", "remove", "tick")
    if len(args) < 2 or args[0] not in modes or (args[0] in ("add", "remove")) != (len(args) > 2) \
            or not all(arg.isdigit() for arg in args[2:]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    mode, checkout, numbers = args[0], args[1], [int(arg) for arg in args[2:]]
    if mode == "add" and store.author() == "agent":
        # Merging stays the person's choice. Like the dispatcher's check of a request's author, this stops a run from
        # queuing a merge by mistake, and a run that clears AGENT_DESK_RUN gets past it.
        print("An agent run can't queue a merge: merging a review is yours to choose, from the page, the pane, or your "
              "own terminal.", file=sys.stderr)
        return 1
    repo = os.environ["GH_REPO"]
    path = state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    say = (lambda line: print(line, file=sys.stderr)) if as_json else print
    at = None
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if mode in ("status", "tick") else 0))
        except BlockingIOError:
            report(shown(load(path, int(time.time())), pushes.shown(checkout, repo)), as_json)
            return 0
        now = int(time.time())
        state = load(path, now)
        if mode == "add":
            for number in numbers:
                state["dropped"] = [one for one in state["dropped"] if one["pr"] != number]
                if any(item["pr"] == number for item in state["queue"]):
                    say(f"#{number} is queued already.")
                else:
                    state["queue"].append({"pr": number, "added": now})
                    say(f"#{number}: queued to merge.")
        elif mode == "remove":
            state["queue"] = [item for item in state["queue"] if item["pr"] not in numbers]
        elif mode == "tick" and state["queue"]:
            desk = store.Store()
            try:
                at = Tick(desk, repo, checkout, now, say, fixes.settings(Path(checkout) / ".agent-desk.json"))
                tick(at, state)
            except (GitError, store.StoreError) as problem:
                # A tick git or the store can't finish leaves the queue as it stands for the next one, and says why.
                say(f"The queue waits: {problem}")
                if state["queue"]:
                    state["queue"][0]["note"] = f"waits: {problem}"
            finally:
                desk.close()
        if mode != "status":
            save(path, state)
    if at is not None and at.landed:
        after_landing(checkout, at.landed, say)
    if mode == "tick":
        pushes.run_tick(checkout, repo, say)
    report(shown(state, pushes.shown(checkout, repo)), as_json)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
