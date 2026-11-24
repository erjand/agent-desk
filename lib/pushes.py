"""Pushes main to origin once the merge queue moves it, and the agent branches with it, best effort:

    python3 pushes.py status <main checkout> [--json]   shows how far main is ahead of origin and how its push went
    python3 pushes.py tick <main checkout> [--json]     pushes main and the agent branches that moved, as each
                                                       `agent-desk merges tick` does
    python3 pushes.py push <main checkout> <agent/slug> pushes one agent branch that moved, as the cleanup after its
                                                       review closes does, and exits 1 when the push fails

Only the desk writes main on origin, so main moves on this machine first and origin follows. A project whose config sets
`host` to `none` pushes nothing. When main holds commits that origin's main lacked at its last fetch, a tick fetches
origin's main into refs/remotes/origin/main, then pushes main if it is still ahead, with `git push origin main`, which
never forces. A fetch or push that fails for any reason but a rejection, such as a network or server error while the
host is down, waits RETRY seconds before the next try, twice as long after each failure up to MAX_WAIT, so an outage
delays the push and nothing else. Git runs without asking for a password or an ssh passphrase, and each fetch and push
has a time limit, so a prompt or a hung network can't stall the timer. When origin's main holds commits main lacks, or a
hook on origin refuses the push, the tick stops: the board shows the notice until main moves, as when the person merges
origin's commits into main by hand, since the queue never pulls or rebases. A push that brings a change of VERSION to
origin from agent-desk's own main checkout publishes the release with scripts/release.py, which tags only a commit
origin's main holds.

After main, a tick pushes each agent/* branch whose head differs from origin's copy, in one `git push` that never
forces, and skips a branch whose head the local main holds, as a run's branch does until its agent commits, so a run
that commits nothing leaves no branch on origin. It notes when it last sent one, and the branches that failed with
git's reason. A failed branch push blocks nothing, and a tick whose main push failed leaves the branches for the next.
The state lives in ~/.local/state/agent-desk/pushes/<owner>__<repo>.json.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import store  # noqa: E402
from config import read as read_config  # noqa: E402

STATE = store.STATE / "pushes"
# The wait after a push's first failure, doubled after each one that follows, and the longest wait.
RETRY = 30
MAX_WAIT = 1800
# How long one push or fetch may take before it counts as failed, as when the network hangs.
PUSH_SECONDS = 120
FETCH_SECONDS = 30
# Ssh that fails at once where it would ask for a passphrase or a host key, and gives up on a host that doesn't answer.
SSH = "ssh -o BatchMode=yes -o ConnectTimeout=20"
# What git prints for a push origin refused: its branch moved, or a hook on it declined.
REJECTED = ("[rejected]", "non-fast-forward", "fetch first", "[remote rejected]")
BRANCHES = "refs/heads/agent/"
ORIGIN_MAIN = "refs/remotes/origin/main"


def state_path(repo):
    return STATE / f"{store.project_key(repo)}.json"


def load(path):
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(checkout, *args, timeout=None):
    """Runs git in the checkout without asking for credentials; a run past `timeout` seconds counts as failed."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND") or SSH}
    try:
        return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"git {args[0]} took longer than {timeout} seconds")


def rev(checkout, ref):
    done = git(checkout, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else None


def has_origin(checkout):
    return "origin" in git(checkout, "remote").stdout.split()


def pushes_to_host(checkout):
    """Whether the project has an origin to push to and a config whose `host` isn't `none`."""
    return read_config(checkout).get("host") != "none" and has_origin(checkout)


def fetch(checkout):
    """
    Updates refs/remotes/origin/main from origin, and returns None, or why it couldn't, as while the host is down. An
    origin without a main has nothing to fetch, and the push creates it.
    """
    done = git(checkout, "fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/main:{ORIGIN_MAIN}",
               timeout=FETCH_SECONDS)
    if done.returncode == 0:
        return None
    if "couldn't find remote ref" in done.stderr:
        git(checkout, "update-ref", "-d", ORIGIN_MAIN)
        return None
    return what_failed(done)


def ahead(checkout):
    """How many commits main holds that origin's main, as last fetched or pushed, lacks."""
    if rev(checkout, "refs/heads/main") is None:
        return 0
    span = "refs/heads/main" if rev(checkout, ORIGIN_MAIN) is None else f"{ORIGIN_MAIN}..refs/heads/main"
    done = git(checkout, "rev-list", "--count", span)
    return int(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip().isdigit() else 0


def what_failed(done):
    """The line that says why a push failed, from its porcelain output or its errors."""
    lines = [line.strip() for line in (done.stdout + "\n" + done.stderr).splitlines() if line.strip()]
    for line in lines:
        if line.startswith("!"):
            return line.split("\t")[-1]
    return next((line for line in lines if line.startswith(("fatal:", "error:"))), lines[0] if lines else "git failed")


def rejected(done):
    return any(mark in done.stdout + done.stderr for mark in REJECTED)


def wait_after(failures):
    return min(MAX_WAIT, RETRY * 2 ** max(0, failures - 1))


def releases(checkout, before, after, desk_root):
    """Whether the push from `before` to `after` changed VERSION in agent-desk's own main checkout."""
    if Path(checkout).resolve() != Path(desk_root).resolve() or before is None:
        return False
    return bool(git(checkout, "diff", "--name-only", before, after, "--", "VERSION").stdout.strip())


def failed(state, now, why, say):
    """Notes a fetch or push that failed, and when to try again."""
    failures = int(state.get("failures", 0)) + 1
    state.update(failures=failures, retryAt=now + wait_after(failures), error=why)
    say(f"Pushing main failed ({why}); try {failures + 1} in {wait_after(failures)} seconds.")
    return "failing"


def refused(state, main, now, why, say):
    """Holds main's push while origin refuses it, until main moves."""
    for key in ("failures", "retryAt", "error"):
        state.pop(key, None)
    held = state.get("rejected")
    if not (isinstance(held, dict) and held.get("main") == main and held.get("error") == why):
        state["rejected"] = {"main": main, "error": why, "at": now}
        say(f"Origin refused main ({why}); merge origin's main into main by hand, and the next tick pushes it.")
    return "rejected"


def push_main(checkout, state, now, say):
    """
    Fetches origin's main once main is ahead of the copy last fetched, then pushes main when it is still ahead and no
    wait or rejection holds it. Returns how main stands: "ok" once origin holds it, "rejected" while origin's main has
    commits main lacks or origin's refusal holds it, or "failing" while a failed fetch or push waits to try again.
    """
    main = rev(checkout, "refs/heads/main")
    # Main that origin's main held when last fetched has nothing to push, and an outage then shows nothing.
    if main is None or not ahead(checkout):
        for key in ("failures", "retryAt", "error", "rejected"):
            state.pop(key, None)
        return "ok"
    if now < int(state.get("retryAt", 0)):
        return "failing"
    why = fetch(checkout)
    if why is not None:
        return failed(state, now, f"couldn't fetch origin: {why}", say)
    before = rev(checkout, ORIGIN_MAIN)
    if not ahead(checkout):
        for key in ("failures", "retryAt", "error", "rejected"):
            state.pop(key, None)
        return "ok"
    if before is not None and git(checkout, "merge-base", "--is-ancestor", before, main).returncode != 0:
        count = git(checkout, "rev-list", "--count", f"{main}..{before}").stdout.strip()
        return refused(state, main, now, f"origin's main has {count} commit{'s' if count != '1' else ''} main lacks",
                       say)
    held = state.get("rejected")
    if isinstance(held, dict) and held.get("main") == main:
        return "rejected"
    state.pop("rejected", None)
    done = git(checkout, "push", "--porcelain", "origin", "refs/heads/main:refs/heads/main", timeout=PUSH_SECONDS)
    if done.returncode == 0:
        for key in ("failures", "retryAt", "error"):
            state.pop(key, None)
        state["pushedAt"] = now
        say(f"Pushed main to origin at {main[:12]}.")
        state["pushed"] = {"before": before, "after": main}
        return "ok"
    why = what_failed(done)
    if rejected(done):
        return refused(state, main, now, why, say)
    return failed(state, now, why, say)


def agent_branches(checkout):
    """
    Each local agent/* branch to keep on origin, and whether its head matches origin's copy as last fetched or pushed.
    A branch whose head the local main holds, as a run's branch before its agent commits, has nothing to push, so it
    counts only while origin's copy matches it.
    """
    listed = git(checkout, "for-each-ref", "--format=%(refname) %(objectname)", BRANCHES,
                 "refs/remotes/origin/agent/")
    refs = dict(line.split(" ", 1) for line in listed.stdout.splitlines() if " " in line)
    held = set(git(checkout, "for-each-ref", "--format=%(refname)", "--merged", "refs/heads/main",
                   BRANCHES).stdout.split()) if rev(checkout, "refs/heads/main") else set()
    matches = {name: refs.get("refs/remotes/origin/" + name.removeprefix("refs/heads/")) == sha
               for name, sha in refs.items() if name.startswith(BRANCHES)}
    return {name.removeprefix("refs/heads/"): same for name, same in matches.items() if same or name not in held}


def send(checkout, names):
    """Pushes agent branches in one `git push` that never forces, and returns git's result and the ones it failed."""
    done = git(checkout, "push", "--porcelain", "origin", *(f"refs/heads/{name}:refs/heads/{name}" for name in names),
               timeout=PUSH_SECONDS)
    failed = [name for name in names if any(line.startswith("!") and f"refs/heads/{name}:" in line
                                            for line in done.stdout.splitlines())]
    if done.returncode != 0 and not failed:
        failed = list(names)
    return done, failed


def push_branch(checkout, branch, say=print):
    """
    Pushes one agent branch whose head differs from origin's copy, as a tick would, so origin holds its commits before
    the cleanup after its review closes judges them. Returns False when the push failed, as with origin out of reach,
    and True when origin holds the branch or the project pushes nothing. It writes no state, and the next tick finds
    the branch pushed.
    """
    if not pushes_to_host(checkout) or agent_branches(checkout).get(branch, True):
        return True
    done, failed = send(checkout, [branch])
    if failed:
        say(f"Couldn't push {branch} to origin: {what_failed(done)}")
        return False
    say(f"Pushed {branch} to origin.")
    return True


def push_branches(checkout, state, say, now=None):
    """
    Pushes each agent/* branch whose head differs from origin's copy and holds a commit the local main lacks, and notes
    when a push last sent one, and the ones that failed with why.
    """
    now = int(time.time() if now is None else now)
    moved = sorted(name for name, matches in agent_branches(checkout).items() if not matches)
    if not moved:
        state.pop("branches", None)
        state.pop("branchesError", None)
        return
    done, failed = send(checkout, moved)
    if len(failed) < len(moved):
        state["branchesPushedAt"] = now
    if failed:
        state["branches"] = failed
        state["branchesError"] = what_failed(done)
        say(f"Couldn't push {', '.join(failed)} to origin: {state['branchesError']}")
    else:
        state.pop("branches", None)
        state.pop("branchesError", None)


def tick(checkout, state, now, say=print, desk_root=None, run=subprocess.run):
    """Pushes main, publishes a release it brought, then pushes the agent branches, and returns the state."""
    if not pushes_to_host(checkout):
        state.clear()
        return state
    state.pop("pushed", None)
    standing = push_main(checkout, state, now, say)
    pushed = state.pop("pushed", None)
    desk_root = desk_root or Path(__file__).resolve().parent.parent
    if pushed and releases(checkout, pushed["before"], pushed["after"], desk_root):
        lock = Path(checkout) / ".git" / "agent-desk-main.lock"
        done = run(["flock", "-o", str(lock), sys.executable, "-B", "scripts/release.py", "publish"],
                   cwd=checkout, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            say(f"Couldn't publish {(Path(checkout) / 'VERSION').read_text().strip()}; run `just release "
                f"publish` in {checkout} to finish.")
    # A main push that fails, as through an outage, would fail for the branches too.
    if standing != "failing":
        push_branches(checkout, state, say, now)
    return state


def shown(checkout, repo, now=None):
    """
    How far main is ahead of origin and the push queue's state, as the board and `agent-desk merges --json` read
    them: `ahead`, and `push`, whose `state` is "ok", "retrying" with the `error` and when it tries again, "rejected"
    with origin's `error`, or "none" for a checkout without origin or a project whose `host` is `none`, with
    `pushedAt`, when main last went to origin, and `branches`, the agent branches the last push failed to send. Beside
    them, `agentBranches` counts the agent branches that match origin's copy and those that wait for a push, from git,
    with when a push last sent one and git's `error` for the ones that failed.
    """
    now = int(time.time() if now is None else now)
    if not pushes_to_host(checkout):
        return {"ahead": 0, "push": {"state": "none"}}
    state = load(state_path(repo))
    main = rev(checkout, "refs/heads/main")
    held = state.get("rejected")
    if isinstance(held, dict) and held.get("main") == main:
        push = {"state": "rejected", "error": held.get("error", "")}
    elif state.get("failures"):
        push = {"state": "retrying", "error": state.get("error", ""), "failures": state["failures"],
                "retryIn": max(0, int(state.get("retryAt", now)) - now)}
    else:
        push = {"state": "ok"}
    if state.get("pushedAt"):
        push["pushedAt"] = int(state["pushedAt"])
    if state.get("branches"):
        push["branches"] = list(state["branches"])
    matches = agent_branches(checkout).values()
    branches = {"pushed": sum(matches), "waiting": sum(not one for one in matches)}
    if state.get("branchesPushedAt"):
        branches["pushedAt"] = int(state["branchesPushedAt"])
    if state.get("branches"):
        branches["failed"] = list(state["branches"])
        branches["error"] = state.get("branchesError", "")
    return {"ahead": ahead(checkout), "push": push, "agentBranches": branches}


def describe(view):
    """One line on main's push for a person, or "" when main matches origin and nothing failed."""
    count, push = view.get("ahead", 0), view.get("push") or {}
    line = f"main is {count} commit{'s' if count != 1 else ''} ahead of origin" if count else ""
    if push.get("state") == "rejected":
        line += f"{'; ' if line else ''}origin refused the push ({push.get('error')}): merge origin's main by hand"
    elif push.get("state") == "retrying":
        line += f"{'; ' if line else ''}push failed ({push.get('error')}), retrying in {push.get('retryIn', 0)}s"
    if push.get("branches"):
        line += f"{'; ' if line else ''}couldn't push {', '.join(push['branches'])}"
    return line[:1].upper() + line[1:] + "." if line else ""


def run_tick(checkout, repo, say=print):
    """A tick under the push queue's lock; a tick that finds another under way leaves the push to it."""
    path = state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        save(path, tick(checkout, load(path), int(time.time()), say))


def main(argv):
    as_json = "--json" in argv
    args = [arg for arg in argv[1:] if arg != "--json"]
    if len(args) == 3 and args[0] == "push" and args[2].startswith("agent/"):
        return 0 if push_branch(args[1], args[2]) else 1
    if len(args) != 2 or args[0] not in ("status", "tick"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    mode, checkout = args
    repo = os.environ["GH_REPO"]
    if mode == "tick":
        run_tick(checkout, repo, (lambda line: print(line, file=sys.stderr)) if as_json else print)
    view = shown(checkout, repo)
    if as_json:
        print(json.dumps(view))
    else:
        print(describe(view) or "main matches origin.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
