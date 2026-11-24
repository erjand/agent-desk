"""Runs one branch check in its systemd unit, which lib/dispatch.py starts:

    python3 checks.py run <check> <main checkout>       tests the check's head merged with its base, and records the
                                                        result
    python3 checks.py stopped <check> <main checkout>   the unit's ExecStopPost: records a check the run left running
                                                        as timed out or failed, and removes its scratch worktree

A check makes a detached scratch worktree of the head under ~/.cache/agent-desk/checks/, merges its base into it, copies
the config's `worktree.seeds` from the main checkout and runs its `worktree.setup` there, then runs the config's `test`
command, such as `just test`, and runs its `worktree.teardown` before it removes the worktree. The base is the one
base_of names: the base of the branch's open review, the base of the merge-queue batch that made a merge-queue/ branch,
or else main. It merges the base's newest commit and records it on the check, except that a re-check, which the
dispatcher queues when a ready review's base moves, merges the commit of the base it names. Every step's output goes to the check's log under ~/.local/state/agent-desk/checks/. A merge conflict ends
the check as failed and renamed `merge`, without running the tests, and a setup that fails ends it as failed and renamed
`setup`. A first attempt that fails queues a second of the same head, so a flaky test counts as failed only when it
fails twice; a conflict gets no second attempt, since it would conflict again.

The helpers the dispatcher uses to watch the branches sit here too: read_refs lists a checkout's agent/* heads and the
heads of the reviews' bases with one `git for-each-ref`, contains says whether a head's base holds it already, which
leaves nothing to check, and merges_with tries a head against its base with `git merge-tree`, which writes no worktree.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import merges  # noqa: E402
import store  # noqa: E402
from config import lookup, read as read_config  # noqa: E402

LOGS = store.STATE / "checks"
WORK = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "agent-desk" / "checks"
# The base a check merges into a head without an open review, as the PR's test workflow tests the PR merged with its
# base.
BASE = "main"
BRANCHES = "refs/heads/agent/"
# A check's time limit in minutes when the config sets no `testMinutes`, the limit the PR test workflow had.
TEST_MINUTES = 15
# Copies a seed, and copies again when a build in the main checkout deletes files from it during the copy.
SEED = Path(__file__).resolve().parent / "seed.sh"
# How long the config's worktree.teardown may run before it is stopped, as desk_teardown_seconds in lib/common.sh.
TEARDOWN_SECONDS = 60
# The identity of the scratch merge commit, which never leaves the scratch worktree.
MERGER = ("-c", "user.name=agent-desk", "-c", "user.email=agent-desk@localhost", "-c", "commit.gpgsign=false")


def log_path(project, check):
    return LOGS / store.project_key(project) / f"{check}.log"


def work_path(project, check):
    return WORK / f"{store.project_key(project)}-{check}"


def unit_name(project, check):
    return f"agent-desk-check-{re.sub(r'[^A-Za-z0-9_.-]+', '-', project)}-{check}.service"


def test_command(config):
    """The project's `test` command, or "" when it has none, which turns its checks off."""
    value = config.get("test")
    return value.strip() if isinstance(value, str) else ""


def rechecks_on(config):
    """Whether the project's `testOnBaseMove` asks for a re-check of each ready review whose base moved."""
    return config.get("testOnBaseMove") is True


def test_minutes(config):
    """A check's time limit in minutes: the project's `testMinutes`, or TEST_MINUTES when it sets no whole number
    above 0."""
    value = config.get("testMinutes")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else TEST_MINUTES


def git(checkout, *args, **options):
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True, **options)


def read_refs(checkout, bases=()):
    """
    Each agent/* branch's head in a checkout, by the branch's short name, and the head of each of `bases`, from its
    local branch or else from origin's, by its name; None when git can't read them.
    """
    wanted = {f"refs/{place}/{base}": base for base in bases for place in ("heads", "remotes/origin")}
    done = git(checkout, "for-each-ref", "--format=%(refname) %(objectname)", BRANCHES, *wanted)
    if done.returncode != 0:
        return None
    refs = dict(line.split(" ", 1) for line in done.stdout.splitlines() if " " in line)
    heads = {name.removeprefix("refs/heads/"): sha for name, sha in refs.items() if name.startswith(BRANCHES)}
    found = {}
    for ref, base in wanted.items():
        if ref in refs:
            found.setdefault(base, refs[ref])
    return heads, found


def merges_with(checkout, head, base_sha):
    """
    Whether `head` merges with `base_sha` without conflicts, as MERGEABLE or CONFLICTING, from the exit status of a full
    `git merge-tree`, which writes no worktree; None when git couldn't try, as with a commit it lacks. `--quiet` would
    stop at the first conflict, but git 2.56 exits 0 with it on some merges that conflict.
    """
    done = git(checkout, "merge-tree", "--write-tree", "--no-messages", base_sha, head)
    return {0: "MERGEABLE", 1: "CONFLICTING"}.get(done.returncode)


def base_of(desk, project, branch):
    """
    The base a check of `branch` merges in: for a merge-queue/ branch, the base of the batch the merge queue's state
    names it for, and for any other branch, the base of its open review in the store; main when there is neither.
    """
    if branch.startswith(merges.BATCH_PREFIX):
        batch = merges.load(merges.state_path(project), 0).get("batch") or {}
        base = batch.get("base") if batch.get("branch") == branch else None
    else:
        base = desk.review_base(project, branch)
    return base or BASE


def base_ref(checkout, base):
    """
    The ref of `base` in a checkout: its local branch, or else origin's, as read_refs reads a review's base; `base`
    itself when the checkout has neither, so the git command that uses it says why it can't.
    """
    for ref in (f"refs/heads/{base}", f"refs/remotes/origin/{base}"):
        if git(checkout, "rev-parse", "--quiet", "--verify", f"{ref}^{{commit}}").returncode == 0:
            return ref
    return base


def contains(checkout, head, base=BASE):
    """Whether `base` holds `head` already, as it does once the branch merged or before its first commit."""
    return git(checkout, "merge-base", "--is-ancestor", head, base_ref(checkout, base)).returncode == 0


def teardown(checkout, work, out):
    """
    Runs the main checkout's `worktree.teardown` in a check's worktree before its removal, with its output in `out`, so
    resources the setup made outside the worktree go with it. One that fails or runs past TEARDOWN_SECONDS says so in
    `out` and blocks nothing; the timeout ends every process the teardown started.
    """
    command = lookup(read_config(checkout), "worktree.teardown")
    if not isinstance(command, str) or not command.strip() or not Path(work).is_dir():
        return
    out.write(f"\n== Tear down: {command}\n")
    out.flush()
    try:
        run = subprocess.Popen(["bash", "-c", command], cwd=work, stdout=out, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as error:
        out.write(f"The worktree teardown didn't start: {error}\n")
        return
    try:
        status = run.wait(timeout=TEARDOWN_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(run.pid, signal.SIGKILL)
        run.wait()
        out.write(f"The worktree teardown ran past {TEARDOWN_SECONDS}s and was stopped.\n")
        return
    if status:
        out.write(f"The worktree teardown failed with status {status}.\n")


def remove_worktree(checkout, work, out=None):
    """
    Removes a scratch worktree and git's record of it, whatever state the check left it in, after the config's
    `worktree.teardown`, whose output goes to `out`, or else to standard output.
    """
    teardown(checkout, work, out or sys.stdout)
    git(checkout, "worktree", "remove", "--force", str(work))
    if Path(work).exists():
        shutil.rmtree(work, ignore_errors=True)
    git(checkout, "worktree", "prune")


def step(log, title, args, cwd):
    """Runs one step with its output in the log, and returns whether it succeeded."""
    log.write(f"\n== {title}\n")
    log.flush()
    return subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode == 0


def base_commit(checkout, base):
    """The commit `base` names in a checkout, from its local branch or else origin's; None when it has neither."""
    done = git(checkout, "rev-parse", "--quiet", "--verify", f"{base_ref(checkout, base)}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else None


def run_check(row, checkout, config, log, base=BASE, base_sha=None):
    """
    Tests a check's head merged with `base` in a scratch worktree, writing each step to `log`, and returns the check's
    status and name: passed or failed `test`, or failed `merge` or `setup`. It merges commit `base_sha` of the base
    when given, and else the base's newest commit.
    """
    checkout, work = Path(checkout), work_path(row["project"], row["id"])
    head, test = row["head_sha"], test_command(config)
    at = f" at {base_sha}" if base_sha else ""
    log.write(f"Check {row['id']} of {row['branch']} at {head} merged with {base}{at}, attempt {row['attempt']}"
              f"{', a re-check after the base moved' if row.get('recheck') else ''}.\n")
    work.parent.mkdir(parents=True, exist_ok=True)
    remove_worktree(checkout, work, log)
    try:
        if not step(log, f"Make a scratch worktree at {head[:12]}",
                    ["git", "-C", str(checkout), "worktree", "add", "--detach", str(work), head], checkout):
            return "failed", "setup"
        merge = ["git", *MERGER, "merge", "--no-edit", "--no-verify", base_sha or base_ref(checkout, base)]
        if not step(log, f"Merge {base}", merge, work):
            return "failed", "merge"
        for seed in lookup(config, "worktree.seeds") or []:
            copy = [str(SEED), str(checkout / seed), str(work / seed)]
            if (checkout / seed).exists() and not step(log, f"Copy {seed}", copy, work):
                return "failed", "setup"
        setup = lookup(config, "worktree.setup")
        if isinstance(setup, str) and setup.strip() and not step(log, f"Set up: {setup}", ["bash", "-c", setup], work):
            return "failed", "setup"
        passed = step(log, f"Test: {test}", ["bash", "-c", test], work)
        return ("passed" if passed else "failed"), "test"
    finally:
        remove_worktree(checkout, work, log)


def run_main(desk, check, checkout):
    row = desk.check(check)
    path = log_path(row["project"], check)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as log:
        if row.get("recheck") and row.get("base_sha"):
            base, base_sha = row["base"] or BASE, row["base_sha"]
        else:
            base = base_of(desk, row["project"], row["branch"])
            base_sha = base_commit(checkout, base)
            if base_sha:
                desk.check_merges(check, base, base_sha)
        status, name = run_check(row, checkout, read_config(checkout), log, base, base_sha)
        log.write(f"\nThe check {status}.\n")
    # A merge conflict would conflict again, so only a failed setup or test gets a second attempt.
    desk.check_ended(check, status, name, rerun=status == "failed" and name != "merge")
    print(f"Check {check} of {row['branch']} at {row['head_sha'][:12]}: {name} {status}. Log: {path}")
    return 0 if status == "passed" else 1


def stopped(desk, check, checkout, environ):
    """
    Records the end of a check its run didn't record, from the SERVICE_RESULT and EXIT_CODE systemd gives
    ExecStopPost: a timeout, a stop by hand, or a crash. A check the dispatcher stopped as superseded has its end
    already. Then removes the scratch worktree.
    """
    try:
        if desk.check(check)["status"] == "running":
            result, code = environ.get("SERVICE_RESULT"), environ.get("EXIT_CODE")
            if result == "timeout":
                desk.check_ended(check, "timed-out", rerun=True)
            elif result == "success" and code != "exited":
                desk.check_ended(check, "cancelled")
            else:
                desk.check_ended(check, "failed", rerun=True)
    finally:
        project = desk.check(check)["project"]
        path = log_path(project, check)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as log:
            remove_worktree(checkout, work_path(project, check), log)


def main(argv):
    if len(argv) != 4 or argv[1] not in ("run", "stopped") or not argv[2].isdigit():
        print(__doc__.strip(), file=sys.stderr)
        return 2
    os.environ["AGENT_DESK_AUTHOR"] = "desk"
    try:
        desk = store.Store()
        if argv[1] == "run":
            return run_main(desk, int(argv[2]), argv[3])
        stopped(desk, int(argv[2]), argv[3], os.environ)
    except store.StoreError as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
