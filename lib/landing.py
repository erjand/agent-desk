"""What lib/merges.py starts once a tick moved main in the main checkout itself, after it lets go of the queue's lock.

In agent-desk's own checkout, after_landing writes current bytecode for lib/, which no `python3 -B` script writes, and
restarts each running service whose code the merges changed. It then runs the config's `sync.run` in the main
checkout, unless a program in `sync.skipWhile` runs there. lib/merges.py passes in the git helpers it reads the
checkout with, so this module imports nothing from it.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

from config import lookup, read as read_config

# The module the dispatcher's serve process starts from; dispatcher_code finds the lib/ files it runs.
DISPATCHER = "dispatch"
DISPATCH_UNIT = "agent-desk-dispatch.service"
# The folders the web page's server runs from, whose change in agent-desk's own checkout restarts it.
WEB_CODE = ("web/", "inbox/hooks/")
WEB_UNIT = "agent-desk-web.service"
# How long, in seconds, the main checkout's lib/ gets to compile after a landing.
COMPILE_WAIT = 120


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


def compile_lib(lib, say, run=subprocess.run):
    """
    Writes current bytecode for each module in `lib` to its __pycache__, which every `python3 -B` the desk starts reads
    and none writes. compileall skips a module whose bytecode is current and replaces a file atomically, so a script
    starting meanwhile reads old bytecode or new. A failure says one line and lets the caller go on.
    """
    try:
        done = run([sys.executable, "-m", "compileall", "-q", "-l", str(lib)], capture_output=True, text=True,
                   stdin=subprocess.DEVNULL, timeout=COMPILE_WAIT)
    except (OSError, subprocess.SubprocessError) as problem:
        say(f"Couldn't compile {lib}: {problem}")
        return
    if done.returncode != 0:
        said = next((line for line in (done.stdout + done.stderr).splitlines() if line.strip()), "no output")
        say(f"Couldn't compile {lib}: {said.strip().removeprefix('*** ')}")


def after_landing(checkout, landed, say, names, worktree_of, lock, desk_root=None, run=subprocess.run):
    """
    Once main moved in the main checkout itself: in agent-desk's own checkout, writes lib/'s bytecode and restarts each
    active service whose code the merges changed, and in any project runs the config's sync.run there unless a program
    in sync.skipWhile runs there.
    `landed` holds each landing as (base, old, new). `names` and `worktree_of` are lib/merges.py's git helpers of those
    names, and `lock` is the main checkout's lock, which the sync holds.
    """
    checkout = Path(checkout).resolve()
    moved = [(old, new) for base, old, new in landed if worktree_of(checkout, base) == checkout]
    if not moved:
        return
    old, new = moved[0][0], moved[-1][1]
    desk_root = Path(desk_root or Path(__file__).resolve().parent.parent).resolve()
    changed = names(checkout, old, new) if checkout == desk_root else set()
    if checkout == desk_root:
        compile_lib(checkout / "lib", say, run)
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
    done = run(["flock", "-o", str(lock), "bash", "-c", sync], cwd=checkout, stdin=subprocess.DEVNULL)
    say(f"Ran the sync in {checkout}." if done.returncode == 0 else f"The sync in {checkout} failed.")
