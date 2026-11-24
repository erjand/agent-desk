"""Checks the machine and the current project for what agent-desk needs, and says how to fix what's missing:

    python3 doctor.py <agent-desk checkout> [<owner/repo> <main checkout> <current checkout>]

It reports each check as ok, warn, or fail, with a fix under each warning and failure, and exits 1 when any check fails.
The machine checks cover the commands agent-desk runs, the git identity the agents commit with, cgroup v2, `agent-desk`
on PATH, the Claude Code plugin and its skills, that the state folder is private to its owner, that nothing is left of
the GitHub API budget queue, the GitHub response cache, or the failure write-ups' ledger, the local store's database,
the dispatcher, the backup, chore, and merge timers, the web page's service, the agentdesk.slice file that caps the
runs' memory, lingering, the account the desk's Claude processes bill, with its API key's file on `api` and a monthly
API budget that is on, and that the weekly usage limit, while on, reads the plan's usage. Given a project, it also
checks that its .agent-desk.json parses, warns when it sets no `test` command, and checks that
~/.config/agent-desk/projects and the store's projects table record its main checkout, and that each of its chores names
a skill Claude Code finds. For a project whose `host` is `github`, it warns when `gh` can't reach GitHub, which releases
and `agent-desk import` need, and when GitHub has an open PR from an `agent/*` branch, which the desk never sees. A
project is a checkout with a .agent-desk.json.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import account
import budget
import chore_skills
import desk_path
import run_units
import hold
import store
import usage

Check = namedtuple("Check", "status what fix", defaults=("",))
# The hosts a project publishes its releases to, which the config's `host` names. `none` publishes only the tag.
HOSTS = ("github", "none")
SLOT_KINDS = tuple(run_units.DEFAULT_SLOTS)
LIMIT_KINDS = tuple(run_units.LIMIT_MINUTES)
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "agent-desk"
NODE_MAJOR = 24
# Each command agent-desk runs, and where it comes from.
COMMANDS = {
    "git": "install git",
    "claude": "install Claude Code from https://claude.com/claude-code",
    "python3": "install Python 3",
    "node": f"install Node {NODE_MAJOR} or newer",
    "flock": "install util-linux",
    "loginctl": "install systemd",
    "busctl": "install systemd",
    "systemctl": "install systemd",
}
# The systemd user units agent-desk writes, what each does, and the command that writes and starts it.
UNITS = [
    ("agent-desk-chores.timer", "the chore timer", "agent-desk chores on"),
    ("agent-desk-merges.timer", "the merge queue timer", "agent-desk chores on"),
    ("agent-desk-web.service", "the web page", "agent-desk web on"),
    ("agent-desk-backup.timer", "the backup timer", "agent-desk backup install"),
    ("agent-desk-dispatch.service", "the dispatcher", "agent-desk dispatch install"),
]
# What agent-desk wrote while it worked through GitHub and writes no more: the API budget's queue and last start in the
# state folder, the failure write-ups' ledger there, and the cache of GitHub's answers.
STATE_LEFTOVERS = ("budget-queue", "budget-last-start", "failures")
CACHE_LEFTOVERS = ("gh",)
# What a project whose host is github needs gh for, and where gh comes from.
GH_NEEDED = "releases and agent-desk import need it"
GH_INSTALL = "install the GitHub CLI from https://cli.github.com"


def run(*command, env=None):
    """A command's exit status and stripped output, with status 127 when it isn't installed."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, str(error)
    return done.returncode, done.stdout.strip()


def user_env():
    """The environment with XDG_RUNTIME_DIR set, which systemctl --user and busctl --user need to reach the user's
    manager."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def lookup(config, path):
    value = config
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def config_problems(text):
    """What's wrong with a .agent-desk.json's text, as lines; none when it parses and has the keys agent-desk needs."""
    try:
        config = json.loads(text)
    except ValueError as error:
        return [f"it isn't valid JSON: {error}"]
    if not isinstance(config, dict):
        return ["it isn't a JSON object"]
    problems = []
    if "runners" in config:
        problems.append("runners is gone, since the dispatcher starts every run: set how many runs of each kind start at "
                        "once under slots, and move runners.env to env")
    env = config.get("env")
    if env is not None and (not isinstance(env, dict) or not all(isinstance(value, str) for value in env.values())):
        problems.append("env must be an object of variable names to strings")
    every = lookup(config, "chores.every")
    if every is not None:
        if not isinstance(every, dict):
            problems.append("chores.every must be an object of skill names to days")
        else:
            for skill, days in every.items():
                if isinstance(days, bool) or not isinstance(days, (int, float)) or days <= 0:
                    problems.append(f"chores.every.{skill} must be a number of days above 0")
    labels = lookup(config, "chores.issueLabels")
    if labels is not None:
        if not isinstance(labels, dict):
            problems.append("chores.issueLabels must be an object of skill names to labels")
        else:
            for skill, label in labels.items():
                if not isinstance(label, str):
                    problems.append(f'chores.issueLabels.{skill} must be a label, or "" for a chore that opens reviews')
    slots = lookup(config, "slots")
    if slots is not None:
        if not isinstance(slots, dict):
            problems.append("slots must be an object of run kinds to counts")
        else:
            for kind, count in slots.items():
                if kind not in SLOT_KINDS:
                    problems.append(f"slots.{kind} isn't a slot; the slots are {', '.join(SLOT_KINDS)}")
                elif isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    problems.append(f"slots.{kind} must be a whole number of runs, 0 or more")
    host = config.get("host")
    if host is not None and host not in HOSTS:
        problems.append(f"host must be one of {', '.join(HOSTS)}")
    test = config.get("test")
    if test is not None and not isinstance(test, str):
        problems.append('test must be the command that tests a branch, such as "just test", or "" for none')
    minutes = config.get("testMinutes")
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0):
        problems.append("testMinutes must be a whole number of minutes above 0")
    recheck = config.get("testOnBaseMove")
    if recheck is not None and not isinstance(recheck, bool):
        problems.append("testOnBaseMove must be true or false")
    limits = config.get("limitMinutes")
    if limits is not None:
        if not isinstance(limits, dict):
            problems.append("limitMinutes must be an object of run kinds to minutes")
        else:
            for kind, value in limits.items():
                if kind not in LIMIT_KINDS:
                    problems.append(f"limitMinutes.{kind} isn't a run kind; the kinds are {', '.join(LIMIT_KINDS)}")
                elif isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    problems.append(f"limitMinutes.{kind} must be a whole number of minutes above 0")
    shrink = lookup(config, "resources.shrinkClaims")
    if shrink is not None and not isinstance(shrink, bool):
        problems.append("resources.shrinkClaims must be true or false")
    for key in ("worktree.setup", "worktree.teardown"):
        value = lookup(config, key)
        if value is not None and not isinstance(value, str):
            problems.append(f'{key} must be a command run with bash -c, or "" for none')
    for key in ("chores.paused", "worktree.seeds", "sync.skipWhile"):
        value = lookup(config, key)
        if value is not None and not isinstance(value, list):
            problems.append(f"{key} must be a list")
    return problems


def read_version(root):
    """The version in an agent-desk checkout's VERSION file."""
    return Path(root, "VERSION").read_text().strip()


def node_major(version):
    match = re.match(r"v?(\d+)\.", version)
    return int(match.group(1)) if match else 0


def plugin_folders(checkout, home):
    """The agent-desk plugin folders Claude Code loads in the checkout, found as lib/chore_skills.py finds plugins."""
    folders = [folder.resolve() for name, folder in chore_skills.plugin_dirs(checkout, home) if name == "agent-desk"]
    return list(dict.fromkeys(folders))


def installed_folders(home):
    """The folders of the agent-desk plugins installed from a marketplace, which `claude plugin update` refreshes."""
    installed = chore_skills.read_json(home / "plugins" / "installed_plugins.json")
    listed = installed.get("plugins") if isinstance(installed, dict) else None
    return {Path(install["installPath"]).resolve() for key, installs in (listed or {}).items()
            if key.split("@")[0] == "agent-desk" and isinstance(installs, list)
            for install in installs if isinstance(install, dict) and install.get("installPath")}


def plugin_version(folder):
    manifest = chore_skills.read_json(Path(folder) / ".claude-plugin" / "plugin.json")
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return version if isinstance(version, str) else None


def missing_skills(folder, names):
    """The skills of `names` the plugin folder lacks, or whose SKILL.md names another skill."""
    missing = []
    for name in names:
        try:
            text = (Path(folder) / "skills" / name / "SKILL.md").read_text()
        except OSError:
            missing.append(name)
            continue
        if not re.search(rf"^name:\s*{re.escape(name)}\s*$", text, re.MULTILINE):
            missing.append(name)
    return missing


def check_commands():
    checks = []
    for command, fix in COMMANDS.items():
        if shutil.which(command) is None:
            checks.append(Check("fail", f"{command} isn't on PATH", fix))
            continue
        if command == "node":
            _, version = run("node", "--version")
            checks.append(Check("ok", f"node {version}") if node_major(version) >= NODE_MAJOR else
                          Check("fail", f"node {version or '(no version)'} is older than {NODE_MAJOR}",
                                COMMANDS["node"]))
        elif command == "systemctl":
            status, _ = run("systemctl", "--user", "show-environment", env=user_env())
            checks.append(Check("ok", "systemctl --user reaches the user's manager") if status == 0 else
                          Check("fail", "systemctl --user can't reach the user's manager",
                                "log in to a session with a systemd user manager, or enable lingering"))
        else:
            checks.append(Check("ok", command))
            if command == "git":
                checks.append(check_git_identity())
    return checks


def check_git_identity():
    """Whether git has a user.name and user.email for the account, which every agent commits its work with. It reads
    them from `/`, outside any repository, so an identity one project's .git/config sets doesn't count."""
    values = {key: run("git", "-C", "/", "config", "--get", f"user.{key}")[1] for key in ("name", "email")}
    missing = [key for key, value in values.items() if not value]
    if not missing:
        return Check("ok", f"git commits as {values['name']} <{values['email']}>")
    examples = {"name": '"Your Name"', "email": "you@example.com"}
    return Check("fail", f"git has no {' or '.join('user.' + key for key in missing)} for this account, so the "
                 "agents can't commit their work",
                 "; ".join(f"git config --global user.{key} {examples[key]}" for key in missing))


def check_cgroup():
    if Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        return Check("ok", "cgroup v2 at /sys/fs/cgroup")
    return Check("fail", "/sys/fs/cgroup isn't cgroup v2, which the runs' memory limits and lib/memory.py read",
                 "boot with systemd.unified_cgroup_hierarchy=1")


def check_path(root):
    own = Path(root, "bin", "agent-desk").resolve()
    command = desk_path.found(os.environ.get("PATH", ""))
    if command is None:
        return Check("fail", "agent-desk isn't on PATH, and the runs and the pane run it from there",
                     f"{root}/install.sh")
    if Path(command).resolve() != own:
        return Check("warn", f"agent-desk on PATH ({command}) runs {Path(command).resolve().parents[1]}, not {root}",
                     f"{root}/install.sh, if this checkout should be the live one")
    return Check("ok", f"agent-desk on PATH runs {root}")


def check_plugin(root, checkout):
    home = chore_skills.config_dir()
    folders = plugin_folders(checkout, home)
    install = f"{root}/install.sh"
    if not folders:
        return [Check("fail", "Claude Code loads no agent-desk plugin, so the pane and its skills are missing",
                      install)]
    if len(folders) > 1:
        return [Check("fail", "Claude Code loads more than one agent-desk plugin, and two can't load together: "
                      + ", ".join(map(str, folders)), install)]
    folder = folders[0]
    own = Path(root, "inbox").resolve()
    update = "claude plugin update agent-desk"
    market = folder in installed_folders(home)
    if folder == own:
        checks = [Check("ok", f"the agent-desk plugin loads from {folder}")]
    elif market:
        # A plugin from a marketplace loads from Claude Code's plugin cache, while the command runs this checkout.
        version, ours = plugin_version(folder), read_version(root)
        checks = [Check("ok", f"the agent-desk plugin loads from its marketplace install at {folder}")
                  if version == ours else
                  Check("warn", f"the installed agent-desk plugin is version {version or 'unknown'}, and this "
                        f"checkout is {ours}", f"{update}, or check out the plugin's version here")]
    else:
        checks = [Check("warn", f"the agent-desk plugin loads from {folder}, not {own}",
                        f"{install}, if this checkout should be the live one")]
    names = sorted(path.parent.name for path in Path(own, "skills").glob("*/SKILL.md"))
    missing = missing_skills(folder, names)
    fix = update if market else f"git -C {folder} pull"
    checks.append(Check("fail", f"the plugin lacks these skills: {', '.join(missing)}", fix)
                  if missing else Check("ok", f"its skills resolve: {', '.join(names)}"))
    return checks


def check_services(root):
    env = user_env()
    checks = []
    for unit, what, command in UNITS:
        _, state = run("systemctl", "--user", "is-active", unit, env=env)
        if state != "active":
            checks.append(Check("warn", f"{what} ({unit}) is {state or 'unknown'}", command))
            continue
        checks.append(Check("ok", f"{what} ({unit}) is active"))
        _, exec_start = run("systemctl", "--user", "show", "-p", "ExecStart", "--value",
                            unit.replace(".timer", ".service"), env=env)
        if exec_start and f"{root}/" not in exec_start:
            checks.append(Check("warn", f"{what} runs another checkout", f"{root}/install.sh"))
    return checks


def check_slice(units=None):
    """Whether agentdesk.slice holds what `agent-desk dispatch install` writes, so the runs share its MemoryHigh."""
    path = Path(units or run_units.UNITS) / run_units.SLICE
    fix = "agent-desk dispatch install"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Check("warn", f"{path} is missing, so the runs share no memory ceiling and can push your apps to swap",
                     fix)
    want = run_units.slice_memory_high()
    if text == run_units.slice_text():
        return Check("ok", f"{run_units.SLICE} caps the runs at MemoryHigh={want}")
    have = next((line.split("=", 1)[1].strip() for line in text.splitlines()
                 if line.strip().startswith("MemoryHigh=")), None)
    if have != want:
        return Check("warn", f"{run_units.SLICE} sets MemoryHigh={have} where install writes {want}" if have else
                     f"{run_units.SLICE} sets no MemoryHigh where install writes {want}", fix)
    return Check("warn", f"{path} differs from what install writes", fix)


def check_linger():
    uid = str(os.getuid())
    _, sessions = run("loginctl", "list-sessions", "--no-legend")
    for line in sessions.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] != uid:
            continue
        _, kind = run("loginctl", "show-session", parts[0], "-p", "Class", "-p", "Type", "--value")
        # loginctl prints the values in its own order, not the order asked for.
        if set(kind.split()) in ({"user", "wayland"}, {"user", "x11"}):
            return Check("ok", "a desktop session keeps the user's services running")
    _, linger = run("loginctl", "show-user", uid, "-p", "Linger", "--value")
    if linger == "yes":
        return Check("ok", "lingering keeps the user's services running without a desktop session")
    return Check("warn", "no desktop session and no lingering, so the timers and web page stop when you log out",
                 f"sudo loginctl enable-linger {os.environ.get('USER') or uid}")


def store_projects(path):
    """The projects the store's database at `path` registers, by slug, with each one's main checkout; empty when the
    database doesn't exist yet, and None when it can't be read. Reads without creating or migrating it."""
    if not Path(path).exists():
        return {}
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return dict(db.execute("SELECT slug, path FROM projects").fetchall())
        finally:
            db.close()
    except sqlite3.Error:
        return None


def check_state(folder):
    """Whether the state folder, which holds the store, check logs, snapshots, and screenshots, is private to its
    owner."""
    folder = Path(folder)
    if not folder.is_dir():
        return Check("ok", f"the state folder {folder} doesn't exist yet, and the store creates it private")
    mode = folder.stat().st_mode & 0o777
    if mode & 0o077:
        return Check("warn", f"the state folder {folder} has mode {mode:04o}, so other accounts can read the store, "
                     "check logs, snapshots, and screenshots", f"chmod 700 {folder}")
    return Check("ok", f"the state folder {folder} is private to its owner")


def check_leftovers(state, cache):
    """Whether anything is left of what agent-desk wrote while it worked through GitHub, and how to delete it."""
    left = [path for folder, names in ((Path(state), STATE_LEFTOVERS), (Path(cache), CACHE_LEFTOVERS))
            for path in (folder / name for name in names) if path.exists() or path.is_symlink()]
    if not left:
        return Check("ok", "nothing is left of the GitHub API budget queue, response cache, or failure ledger")
    return Check("warn", "agent-desk no longer uses these, left from when it worked through GitHub: "
                 + ", ".join(map(str, left)), "rm -r " + " ".join(map(str, left)))


def check_account(state=None, path=None):
    """What the desk's Claude processes bill, and on `api` whether the key's file is there, holds a key, and is
    private to its owner."""
    state = hold.status() if state is None else state
    path = path or account.KEY
    chosen = account.current(state)
    if chosen == "subscription":
        return Check("ok", "the account is subscription: the desk's runs and Chat bill the Claude plan")
    problem = account.key_problem(path)
    if problem:
        why, fix = problem
        return Check("fail", f"the account is api, but {why}", fix)
    return Check("ok", f"the account is api: the desk's runs and Chat bill the API key ending in "
                 f"{account.ending(account.read_key(path))}")


def check_budget(state=None, desk=None, now=None):
    """On `api`, whether the API budget is on, since without one nothing stops the runs' spend on the key."""
    state = hold.status() if state is None else state
    if account.current(state) != "api":
        return Check("ok", "the API budget waits for the api account")
    limit = budget.budget_of(state)
    if limit is None or not limit["on"]:
        return Check("warn", "the account is api with no API budget on, so nothing holds new runs however much the "
                     "key spends", "agent-desk account budget <dollars>")
    try:
        held = budget.holds(state, desk, now)
    except (store.StoreError, sqlite3.Error) as problem:
        return Check("warn", f"the API budget of {budget.dollars(limit['dollars'])} a month can't read the month's "
                     f"spend from the store, so runs start: {problem}", "see the store check above")
    return Check("ok", f"the API budget of {budget.dollars(limit['dollars'])} a month is on"
                 + (f", and holds new runs: {held}" if held else ""))


def check_usage(state=None, now=None, get=None):
    """Whether the weekly usage limit, while on, reads the plan's usage, since it lets runs start when it can't."""
    state = hold.status() if state is None else state
    limit = usage.limit_of(state)
    if limit is None or not limit["on"]:
        return Check("ok", "the weekly usage limit is off")
    if account.current(state) != "subscription":
        return Check("ok", f"the weekly usage limit of {limit['percent']}% waits for the subscription, since the "
                     "account is api")
    reason, problem = usage.check(state, now, get)
    if problem:
        return Check("warn", f"the weekly usage limit of {limit['percent']}% can't read usage, so runs start: "
                     f"{problem}",
                     "run claude and sign in again, or turn the limit off with: agent-desk usage-limit off")
    return Check("ok", f"the weekly usage limit of {limit['percent']}% reads usage"
                 + (f", and holds new runs: {reason}" if reason else ""))


def check_store(path):
    """Whether the store's database opens, passes SQLite's quick check, and holds a schema this code can write."""
    if not Path(path).exists():
        return Check("warn", f"the local store has no database at {path} yet",
                     "agent-desk install, from a project's main checkout")
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            verdict = db.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            db.close()
    except sqlite3.Error as error:
        return Check("fail", f"the local store at {path} can't be read: {error}",
                     "agent-desk restore, from the newest backup")
    if verdict != "ok":
        return Check("fail", f"the local store at {path} fails SQLite's quick check: {verdict}",
                     "agent-desk restore, from the newest backup")
    if version > store.SCHEMA_VERSION:
        return Check("fail", f"the local store at {path} has schema {version}, newer than this checkout's "
                     f"{store.SCHEMA_VERSION}, so this checkout's commands can't write it",
                     "pull the agent-desk checkout that agent-desk on PATH runs")
    return Check("ok", f"the local store at {path} opens")


def check_github(repo):
    """
    The checks of a project whose host is github: gh can reach GitHub, which releases and imports need, and GitHub has
    no open PR from an agent/* branch, which an agent or a habit opened there in place of a review in the store.
    """
    if shutil.which("gh") is None:
        return [Check("warn", f"gh isn't on PATH, and {GH_NEEDED}", GH_INSTALL)]
    status, _ = run("gh", "auth", "status")
    if status != 0:
        return [Check("warn", f"gh isn't logged in, and {GH_NEEDED}", "gh auth login")]
    checks = [Check("ok", "gh is logged in, for releases and imports")]
    status, out = run("gh", "pr", "list", "-R", repo, "--state", "open", "--limit", "100", "--json",
                      "number,headRefName")
    try:
        prs = json.loads(out) if status == 0 else None
    except ValueError:
        prs = None
    if prs is None:
        return checks + [Check("warn", f"gh couldn't list {repo}'s open PRs on GitHub", "gh auth status")]
    strays = [pr["number"] for pr in prs if str(pr.get("headRefName", "")).startswith("agent/")]
    if strays:
        names = ", ".join(f"#{number}" for number in strays)
        checks.append(Check("warn", f"GitHub has open PRs from agent/* branches, which the desk never sees: {names}",
                            "open a review with agent-desk review create --branch <branch>, then close the PR on "
                            "GitHub"))
    else:
        checks.append(Check("ok", "GitHub has no open PR from an agent/* branch"))
    return checks


def check_test(config_path, config):
    """Warns when the project sets no `test` command, which leaves its reviews without branch checks."""
    command = (config.get("test") or "").strip()
    if command:
        return Check("ok", f"{config_path} sets test, which each branch check runs: {command}")
    return Check("warn", f"{config_path} sets no test command, so no branch check runs and its reviews merge untested",
                 f'set "test" in {config_path} to the command that tests a branch, such as "just test"')


def check_project(root, repo, main, here):
    config_path = Path(here, ".agent-desk.json")
    try:
        text = config_path.read_text()
    except OSError:
        return [Check("fail", f"{config_path} is missing", "agent-desk install")]
    problems = config_problems(text)
    if problems:
        return [Check("fail", f"{config_path}: {problem}", f"edit {config_path}") for problem in problems]
    config = json.loads(text)
    checks = [Check("ok", f"{config_path} parses"), check_test(config_path, config)]

    projects = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-desk" / "projects"
    try:
        recorded = dict(line.split(None, 1) for line in projects.read_text().splitlines() if len(line.split()) == 2)
    except OSError:
        recorded = {}
    if repo not in recorded:
        checks.append(Check("fail", f"{projects} doesn't record {repo}", f"agent-desk install, from {main}"))
    elif Path(recorded[repo].strip()).resolve() != Path(main).resolve():
        checks.append(Check("fail", f"{projects} records {repo} at {recorded[repo].strip()}, not {main}",
                            f"agent-desk install, from {main}"))
    else:
        checks.append(Check("ok", f"{projects} records {repo} at {main}"))

    # A store that can't be read fails the machine's store check, which says how to fix it.
    registered = store_projects(store.DATABASE)
    if registered is None:
        pass
    elif repo not in registered:
        checks.append(Check("fail", f"the local store doesn't register {repo}", f"agent-desk install, from {main}"))
    elif not registered[repo] or Path(registered[repo]).resolve() != Path(main).resolve():
        checks.append(Check("fail", f"the local store registers {repo} at {registered[repo] or 'no checkout'}, not "
                            f"{main}", f"agent-desk install, from {main}"))
    else:
        checks.append(Check("ok", f"the local store registers {repo} at {main}"))

    if config.get("host", "github") == "github":
        checks += check_github(repo)

    for skill in lookup(config, "chores.every") or {}:
        if not chore_skills.resolves(skill, main):
            checks.append(Check("warn", f"Claude Code finds no skill named {skill}, so its chore fails each run",
                                "install the skill or take it out of chores.every; docs/reference.md's Chores "
                                "section says where chore skills come from"))
    return checks


def report(title, checks):
    print(title)
    for check in checks:
        print(f"{check.status:<8}{check.what}")
        if check.fix and check.status != "ok":
            print(f"{'':8}fix: {check.fix}")
    print()


def main(argv):
    if len(argv) not in (1, 4):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    root = Path(argv[0]).resolve()
    checkout = argv[3] if len(argv) == 4 else root
    machine = [*check_commands(), check_cgroup(), check_path(root), *check_plugin(root, checkout),
               check_state(store.STATE), check_leftovers(store.STATE, CACHE), check_store(store.DATABASE),
               check_account(), check_budget(), check_usage()]
    if shutil.which("systemctl") and shutil.which("loginctl"):
        machine += [*check_services(root), check_slice(), check_linger()]
    report("Machine", machine)
    checks = machine
    if len(argv) == 4:
        repo, main_checkout, here = argv[1:]
        if Path(here, ".agent-desk.json").is_file():
            project = check_project(root, repo, main_checkout, here)
            report(f"Project {repo} at {here}", project)
            checks = machine + project
        else:
            print(f"{here} has no .agent-desk.json; run agent-desk install there to set it up.")
    else:
        print("Run agent-desk doctor from a project's checkout to check the project too.")
    failed = sum(check.status == "fail" for check in checks)
    warned = sum(check.status == "warn" for check in checks)
    print(f"{failed} failed, {warned} warnings.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
