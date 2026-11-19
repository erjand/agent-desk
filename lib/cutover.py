"""Moves a project's issues and reviews from GitHub to the local store for good, or back:

    python3 cutover.py [--dry-run] [--issues-off] <owner/repo>   cuts over one project
    python3 cutover.py [--dry-run] [--issues-off] --all  cuts over every project in ~/.config/agent-desk/projects
    python3 cutover.py --undo [--dry-run] <owner/repo>   undoes a project's cutover; --rollback does the same

The project's main checkout comes from the projects file alone, and its `origin` must name the repository given. A
cutover refuses to start unless the project is on Hold, with no run working and its merge queue empty. Then it takes
these steps in order, printing each, and stops at the first failure with the step named:

1. import: imports the project from GitHub one last time, as `agent-desk import` does.
2. reviews: checks that every open GitHub PR from one of the project's own `agent/` branches has an open review in the
   store and its branch in the main checkout, holding every commit of the PR's head. It fetches a branch the checkout
   lacks, and stops when a local branch lacks commits GitHub holds. Other PRs, such as a person's, a bot's, or a
   fork's, stay open on GitHub.
3. statuses: checks that each open issue GitHub labels with a workflow state has that state in the store, and counts
   the open issues by state.
4. record: records the cutover in the store. The store is the project's tracker from here on.
5. agents: adds the line naming `agent-desk issue` and `agent-desk review` to the Agent desk section of the project's
   AGENTS.md or CLAUDE.md and commits it, when the main checkout is on `main` with no merge, rebase, or cherry-pick in
   progress and nothing staged. Otherwise, or when the commit fails, such as on a pre-commit hook that rejects it, it
   leaves the file as it was and prints the line for a person to add.
6. workflows: disables the agent, agent-cleanup, chores, fix, and test workflows on GitHub.
7. pulls: comments on each open GitHub PR from an `agent/` branch with the number of its open local review, and
   closes it. Every other open PR stays on GitHub and is listed for a person.
8. issues: leaves Issues as they are on GitHub, so the issues there stay readable and links to them keep working.
   With --issues-off it turns Issues off on the GitHub repository, which hides them without deleting them.

The GitHub changes come last, so a failure before them leaves GitHub as it was, and a project whose `host` is `none`
skips every step that reads or changes GitHub. Every step can run twice. A rerun after the record skips the import and
its checks, since the store is the tracker by then, and the steps after the record skip what they find done, such as a
disabled workflow or Issues already off; a PR whose pointer went up before its close failed gets no second pointer. The
record and each GitHub change are `cutover` events in the store. Each change records its intent before it calls GitHub
and its result after, so a rerun records a change a killed run made without its result, and the undo reverses every
change the cutover intended that GitHub shows as made. The cutover leaves the project's `host` as it is, since a project that pushes `main` to GitHub keeps
`github`.

--dry-run opens the store read-only, reads GitHub, prints each step it would take, and changes nothing. It refuses to
read a store whose schema is older than the code's, which only the installed agent-desk migrates. After the last
project, the cutover restarts the dispatcher when its service runs, says how to restart the web page and the backup
timer on the new code, and prints a summary: what changed on GitHub, what the store holds, and what is left for a
person.

The undo turns Issues back on when the cutover turned them off, enables the workflows the cutover disabled, reopens the
PRs it closed, and lists the issues and reviews filed in the store since the cutover, which need refiling on GitHub by
hand. It records the undo, so a later cutover starts over.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import textwrap
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import hold  # noqa: E402
import import_github  # noqa: E402
import merges  # noqa: E402
import store  # noqa: E402

# The workflow files agent-desk wrote into a project, which the dispatcher and the branch checks replace.
WORKFLOWS = ("agent.yml", "agent-cleanup.yml", "chores.yml", "fix.yml", "test.yml")
# The mark on the pointer a cutover leaves on a PR, which a rerun looks for before it posts another.
POINTER = "<!-- agent-desk-cutover -->"
SIGNATURE = "<sub>Posted by agent-desk</sub>\n<!-- agent-desk -->"
# The Tracker line as agent-desk's own AGENTS.md wraps it, at 120 columns with a two-space hanging indent, so a
# project that lints Markdown line length takes the commit.
TRACKER = ("- **Tracker.** Issues and reviews live in agent-desk's local store. Read and write them with "
           "`agent-desk issue`\n"
           "  (`view`, `list`, `create`, `comment`, `edit`, `close`) and `agent-desk review` (`create`, `view`, "
           "`list`,\n"
           "  `comment`, `edit`, `checks`, `ready`), which take the flags `gh issue` and `gh pr` take, and never with "
           "`gh issue`,\n"
           "  `gh pr`, or `gh api`.")
SECTION = re.compile(r"#{1,6}\s+agent[ -]desk\s*$", re.IGNORECASE)
HEADING = re.compile(r"(#{1,6})\s")
MODE_NAMES = {"running": "Auto", "manual": "Manual"}
# The files git keeps in a checkout's git folder while each kind of operation waits to finish.
IN_PROGRESS = (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"), ("rebase-apply", "a rebase"),
               ("CHERRY_PICK_HEAD", "a cherry-pick"), ("REVERT_HEAD", "a revert"))
# Each service the cutover's new code runs in, and the command that turns it on when it is off.
SERVICES = (("agent-desk-dispatch.service", "agent-desk dispatch install"),
            ("agent-desk-web.service", "agent-desk web on"),
            ("agent-desk-backup.timer", "agent-desk backup install"))
# argparse adds the "usage: " prefix itself, so the manual message adds it in its own print.
USAGE = "agent-desk cutover [--dry-run] [--issues-off] <owner/repo> | --all\n       agent-desk cutover --undo [--dry-run] <owner/repo>"


class Failed(Exception):
    """A step that failed, with its name and what went wrong."""

    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


class GitHubError(Exception):
    """A gh command that failed, with gh's own message."""


def gh(*args):
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True)
    except FileNotFoundError as error:
        raise GitHubError("gh is not installed.") from error
    if done.returncode != 0:
        raise GitHubError((done.stderr or done.stdout).strip() or f"gh {' '.join(args[:3])} exited with status "
                          f"{done.returncode}.")
    return done.stdout


def gh_each(path, jq=".[]"):
    """Each object a paginated REST listing holds."""
    return [json.loads(line) for line in gh("api", "--paginate", path, "--jq", jq).splitlines() if line.strip()]


def git(checkout, *args):
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)


def origin_of(checkout):
    """The checkout's `origin` URL as configured, or an empty string when it has none."""
    return git(checkout, "config", "--get", "remote.origin.url").stdout.strip()


def names(url, repo):
    """Whether a remote URL, such as git@github.com:owner/repo.git or a path, ends in the repository's owner/repo."""
    url = url.rstrip("/").removesuffix(".git")
    return [part.lower() for part in re.split(r"[/:]", url)[-2:]] == repo.lower().split("/")


def schema_on_disk(path):
    """
    The schema version in a SQLite file's header, read without opening the database, which in WAL mode would leave
    its -wal and -shm files beside it. None when the file is missing or isn't a database.
    """
    try:
        with open(path, "rb") as file:
            header = file.read(100)
    except OSError:
        return None
    return int.from_bytes(header[60:64], "big") if header.startswith(b"SQLite format 3\0") else None


def projects():
    """Each project in ~/.config/agent-desk/projects with its main checkout, in the file's order."""
    try:
        lines = store.PROJECTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [tuple(line.split(None, 1)) for line in lines if len(line.split(None, 1)) == 2]


def refusals(repo, now=None):
    """
    Why a cutover of `repo` can't start now: its mode, its working runs, and its merge queue.
    """
    reasons = []
    mode = hold.mode_of(hold.status(), repo)
    if mode != "held":
        reasons.append(f"{repo} is on {MODE_NAMES.get(mode, mode)}, not Hold")
    working = [run for run in hold.running() if run["repo"] == repo]
    if working:
        reasons.append(f"{plural(len(working), 'run')} still working ("
                       + ", ".join(f"{run['workflow']} {run['run']}" for run in working) + ")")
    queue = merges.load(merges.state_path(repo), now or time.time())
    if queue["queue"] or queue.get("batch"):
        reasons.append("the merge queue holds " + (", ".join(f"#{one['pr']}" for one in queue["queue"])
                                                   or "a batch under test"))
    return reasons


def agent_desk_section(checkout):
    """The AGENTS.md or CLAUDE.md with an Agent desk section, its lines, and the bounds of the section's lines, or
    None."""
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = Path(checkout) / name
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            continue
        for start, line in enumerate(lines):
            if SECTION.match(line):
                level = len(HEADING.match(line).group(1))
                end = next((index for index in range(start + 1, len(lines))
                            if (heading := HEADING.match(lines[index])) and len(heading.group(1)) <= level),
                           len(lines))
                return path, lines, start, end
    return None


def unready(checkout):
    """Why the cutover can't commit to the main checkout, or None when it is on main, with no merge, rebase,
    cherry-pick, or revert in progress and nothing staged."""
    branch = git(checkout, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch != "main":
        return f"{checkout} is on {branch or 'a detached HEAD'}, not main"
    for name, operation in IN_PROGRESS:
        marker = git(checkout, "rev-parse", "--git-path", name).stdout.strip()
        if marker and (Path(checkout) / marker).exists():
            return f"{checkout} has {operation} in progress"
    if git(checkout, "diff", "--cached", "--quiet").returncode == 1:
        return f"{checkout} has staged changes"
    return None


def with_tracker(lines, start, end):
    """The file's lines with TRACKER after the last line of the section from `start` to `end`."""
    last = end
    while last > start + 1 and not lines[last - 1].strip():
        last -= 1
    before = lines[:last]
    if not before[-1].endswith("\n"):
        before[-1] += "\n"
    added = ["\n", TRACKER + "\n"] if last == start + 1 else [TRACKER + "\n"]
    return before + added + lines[last:]


class Cutover:
    """One project's cutover or undo, which prints each step and gathers what changed and what is left."""

    def __init__(self, desk, repo, checkout, dry_run=False, say=print, hide_issues=False):
        self.desk, self.repo, self.checkout, self.dry_run, self.say = desk, repo, Path(checkout), dry_run, say
        self.hide_issues = hide_issues
        self.github = config.read(checkout).get("host", "github") != "none"
        self.changed, self.left = [], []

    def steps(self, *names):
        """The project's recorded cutover steps with one of these names, oldest first."""
        return [one for one in self.desk.cutover_steps(self.repo) if one["step"] in names]

    def step(self, name, action):
        self.say(f"- {name}")
        try:
            action()
        except (GitHubError, import_github.ReadError, store.StoreError, OSError) as error:
            raise Failed(name, str(error)) from error

    def note(self, text):
        self.say(f"    {text}")

    def change(self, doing, done, call):
        """Changes GitHub with `call`, or in a dry run says it would."""
        if self.dry_run:
            self.note(f"Would {doing}.")
            self.changed.append(f"Would {doing}")
            return
        call()
        self.note(f"{done}.")
        self.changed.append(done)

    # The cutover.

    def run(self):
        self.say(f"Cutting over {self.repo} in {self.checkout}{' (dry run)' if self.dry_run else ''}")
        self.step("check", self.check)
        recorded = self.recorded()
        if recorded:
            self.say(f"- import, reviews, statuses, record: done; the store has been the tracker since "
                     f"{recorded['at']}")
        else:
            if self.github:
                self.step("import", self.import_once)
                self.step("reviews", self.check_reviews)
                self.step("statuses", self.check_statuses)
            self.step("record", self.record)
        self.step("agents", self.agents_line)
        if not self.github:
            self.say("- workflows, pulls, issues: skipped, since the project's host is none")
            return
        self.step("workflows", self.disable_workflows)
        self.step("pulls", self.close_pulls)
        self.step("issues", self.issues_off)

    def recorded(self):
        return next((step for step in self.desk.cutover_steps(self.repo) if step["step"] == "recorded"), None)

    def check_checkout(self):
        """Stops unless the main checkout from the projects file is a checkout of the repository given."""
        if not (self.checkout / ".git").exists():
            raise Failed("check", f"{self.checkout}, which {store.PROJECTS} names as {self.repo}'s main checkout, "
                         "isn't a git checkout.")
        url = origin_of(self.checkout)
        if url and not names(url, self.repo):
            raise Failed("check", f"the origin of {self.checkout} is {url}, which isn't {self.repo}. Fix the line "
                         f"for {self.repo} in {store.PROJECTS}.")
        if not url and self.github:
            raise Failed("check", f"{self.checkout} has no origin, so it can't be checked against {self.repo}.")

    def check(self):
        self.check_checkout()
        reasons = refusals(self.repo)
        if not reasons:
            self.note("On Hold, with no run working and the merge queue empty.")
        elif self.dry_run:
            for reason in reasons:
                self.note(f"Would refuse now: {reason}.")
        else:
            raise Failed("check", "; ".join(reasons) + f". Run `agent-desk hold {self.repo}` first, and wait for "
                         "the runs and the merge queue to finish.")

    def import_once(self):
        if self.dry_run:
            self.note(f"Would import {self.repo} from GitHub, as `agent-desk import {self.repo}` does.")
            return
        importer = import_github.Importer(self.desk, self.repo)
        problem = importer.run()
        self.note(importer.summary())
        if problem:
            raise Failed("import", problem)

    def open_pulls(self):
        return gh_each(f"repos/{self.repo}/pulls?state=open&per_page=100")

    def review_of(self, pull):
        """The open review in the store that stands for a GitHub PR, or None."""
        number = self.desk.review_number(self.repo, pull["head"]["ref"])
        if number is None or self.desk.item(self.repo, number)["status"] == "closed":
            return None
        return number

    def moves(self, pull):
        """
        Why a GitHub PR stays open on GitHub rather than moving to the store, or None for a PR from one of the
        project's own `agent/` branches, which moves.
        """
        source = (pull["head"].get("repo") or {}).get("full_name") or ""
        if source.lower() != self.repo.lower():
            return f"its branch sits in {'the fork ' + source if source else 'a deleted fork'}"
        if not pull["head"]["ref"].startswith("agent/"):
            return "it comes from a branch outside agent/"
        return None

    def head_of(self, branch):
        done = git(self.checkout, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
        return done.stdout.strip() if done.returncode == 0 else None

    def checked_out_at(self, branch):
        """The worktree that has the branch checked out, or None."""
        listed = git(self.checkout, "worktree", "list", "--porcelain").stdout
        for block in listed.split("\n\n"):
            lines = block.splitlines()
            if f"branch refs/heads/{branch}" in lines and lines[0].startswith("worktree "):
                return lines[0].removeprefix("worktree ")
        return None

    def check_branch(self, number, branch, sha):
        """
        Fetches a PR's branch the checkout lacks, and returns what stops the cutover when the local branch lacks
        commits of the PR's head on GitHub, or None.
        """
        local = self.head_of(branch)
        if local is None and self.dry_run:
            self.note(f"Would fetch {branch} for PR #{number}.")
            return None
        if local is None:
            fetched = git(self.checkout, "fetch", "--quiet", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
            if fetched.returncode != 0:
                return f"PR #{number}'s branch {branch} isn't in {self.checkout}, and fetching it failed: " \
                       f"{fetched.stderr.strip()}"
            self.note(f"Fetched {branch} for PR #{number}.")
            local = self.head_of(branch)
        if local == sha or (sha and git(self.checkout, "merge-base", "--is-ancestor", sha, local).returncode == 0):
            self.note(f"{branch} here holds PR #{number}'s head{'' if local == sha else ' and more'}.")
            return None
        where = self.checked_out_at(branch)
        fix = (f"Git can't fetch into a branch that is checked out, and {branch} is checked out in {where}: run "
               f"`git -C {where} pull --ff-only origin {branch}` there" if where else
               f"Run `git -C {self.checkout} fetch origin {branch}:{branch}`")
        problem = (f"PR #{number}'s head on GitHub, {(sha or 'unknown')[:12]}, has commits {branch} here "
                   f"({local[:12]}) lacks. {fix}, or merge the two by hand if they diverged, and rerun")
        if self.dry_run:
            self.note(f"Would stop: {problem}.")
            return None
        return problem

    def check_reviews(self):
        pulls, problems = self.open_pulls(), []
        if not pulls:
            self.note("GitHub has no open PR.")
        for pull in pulls:
            number, branch = pull["number"], pull["head"]["ref"]
            stays = self.moves(pull)
            if stays:
                self.note(f"PR #{number} from {branch} stays open on GitHub, since {stays}.")
                continue
            problem = self.check_branch(number, branch, pull["head"].get("sha"))
            if problem:
                problems.append(problem)
            review = self.review_of(pull)
            if self.dry_run:
                self.note(f"PR #{number} from {branch} becomes review #{review or number}.")
            elif review is None:
                problems.append(f"PR #{number} from {branch} has no open review in the store")
            else:
                self.note(f"PR #{number} from {branch} is review #{review}.")
        if problems:
            raise Failed("reviews", "; ".join(problems) + ".")

    def check_statuses(self):
        issues = [one for one in gh_each(f"repos/{self.repo}/issues?state=open&per_page=100")
                  if "pull_request" not in one]
        wrong, expected_counts = [], {}
        for issue in issues:
            names = {label["name"] for label in issue["labels"]}
            expected = next((store.STATUS_LABELS[name] for name in import_github.STATE_ORDER if name in names), "open")
            expected_counts[expected] = expected_counts.get(expected, 0) + 1
            if expected == "open" or self.dry_run:
                continue
            try:
                status = self.desk.item(self.repo, issue["number"], "issue")["status"]
            except store.StoreError:
                status = "missing"
            if status != expected:
                wrong.append(f"#{issue['number']} is {status}, not {expected}")
        if wrong:
            raise Failed("statuses", "GitHub's labels and the store disagree: " + "; ".join(wrong) + ".")
        counts = expected_counts if self.dry_run else self.open_counts()
        self.note(("GitHub's open issues would take these states: " if self.dry_run else "Open issues by state: ")
                  + (", ".join(f"{state} {count}" for state, count in sorted(counts.items())) or "none") + ".")

    def open_counts(self):
        """How many open issues the store holds in each state."""
        rows = self.desk.db.execute("SELECT state, COUNT(*) AS count FROM items WHERE project = ? AND kind = 'issue'"
                                    " AND state != 'closed' GROUP BY state", (self.repo,)).fetchall()
        return {row["state"]: row["count"] for row in rows}

    def record(self):
        if self.dry_run:
            self.note("Would record the cutover in the store, which becomes the project's tracker.")
            return
        self.desk.cutover_step(self.repo, "recorded", host="github" if self.github else "none")
        self.note(f"Recorded the cutover; the store is {self.repo}'s tracker from now on.")

    def agents_line(self):
        found = agent_desk_section(self.checkout)
        if found is None:
            self.note("AGENTS.md and CLAUDE.md have no Agent desk section, so the line is left for you.")
            self.leave_line(f"{self.repo}'s AGENTS.md")
            return
        path, lines, start, end = found
        if "`agent-desk issue`" in "".join(lines[start:end]):
            self.note(f"{path.name}'s Agent desk section already names `agent-desk issue`.")
            return
        if git(self.checkout, "status", "--porcelain", "--", path.name).stdout.strip():
            why = f"{path.name} has uncommitted changes"
        else:
            why = unready(self.checkout)
        if why:
            self.note(f"{why}, so the line is left for you.")
            self.leave_line(path)
            return
        if self.dry_run:
            self.note(f"Would add the line naming `agent-desk issue` and `agent-desk review` to {path} and commit "
                      "it.")
            return
        before = path.read_bytes()
        path.write_text("".join(with_tracker(lines, start, end)), encoding="utf-8")
        done = git(self.checkout, "commit", "--quiet", "-m",
                   "Name agent-desk issue and review as the tracker's commands", "--", path.name)
        if done.returncode != 0:
            # The file goes back as it was, so a rerun doesn't take the uncommitted line for one already there.
            path.write_bytes(before)
            self.note(f"git couldn't commit the line added to {path}, so the file is back as it was and the line is "
                      f"left for you: {(done.stderr or done.stdout).strip()}")
            self.leave_line(path)
            return
        self.note(f"Added the line naming `agent-desk issue` and `agent-desk review` to {path} and committed it.")

    def leave_line(self, where):
        self.left.append(f"Add these lines to the Agent desk section of {where}:\n{textwrap.indent(TRACKER, '  ')}")

    def workflows(self):
        return [one for one in gh_each(f"repos/{self.repo}/actions/workflows?per_page=100", ".workflows[]")
                if Path(one["path"]).name in WORKFLOWS]

    def unrecorded(self, intent, done, key):
        """The steps whose intent is recorded without the result that follows it, by `key`."""
        finished = {one[key] for one in self.steps(done)}
        return {one[key]: one for one in self.steps(intent) if one[key] not in finished}

    def disable_workflows(self):
        workflows = self.workflows()
        unrecorded = {} if self.dry_run else self.unrecorded("workflow-disabling", "workflow-disabled", "workflow")
        if not workflows:
            self.note("GitHub has none of the desk's workflows.")
        for one in workflows:
            if one["state"] != "active" and one["id"] in unrecorded:
                self.desk.cutover_step(self.repo, "workflow-disabled", workflow=one["id"], path=one["path"])
                self.note(f"{one['path']} was disabled by a cutover that stopped before recording it; recorded it.")
                self.changed.append(f"Disabled {one['path']}")
                continue
            if one["state"] != "active":
                self.note(f"{one['path']} is off already ({one['state']}).")
                continue

            def disable(one=one):
                self.desk.cutover_step(self.repo, "workflow-disabling", workflow=one["id"], path=one["path"])
                gh("api", "-X", "PUT", f"repos/{self.repo}/actions/workflows/{one['id']}/disable")
                self.desk.cutover_step(self.repo, "workflow-disabled", workflow=one["id"], path=one["path"])

            self.change(f"disable {one['path']}", f"Disabled {one['path']}", disable)

    def pull_state(self, number):
        return gh("api", f"repos/{self.repo}/pulls/{number}", "--jq", ".state").strip()

    def close_pulls(self):
        if not self.dry_run:
            for number, one in self.unrecorded("pr-closing", "pr-closed", "pr").items():
                if self.pull_state(number) == "closed":
                    self.desk.cutover_step(self.repo, "pr-closed", pr=number, review=one["review"])
                    self.note(f"PR #{number} was closed by a cutover that stopped before recording it; recorded it.")
                    self.changed.append(f"Closed PR #{number} with a pointer to review #{one['review']}")
        pulls = self.open_pulls()
        if not pulls:
            self.note("GitHub has no open PR.")
        for pull in pulls:
            number, branch = pull["number"], pull["head"]["ref"]
            stays = self.moves(pull)
            review = None if stays else self.review_of(pull)
            if review is None and not stays and self.dry_run:
                # The dry run imported nothing, and the import keeps a PR's number for its review.
                review = number
            if review is None:
                why = stays or "the store has no open review of it"
                self.note(f"PR #{number} from {branch} stays open on GitHub, since {why}.")
                self.left.append(f"PR #{number} of {self.repo}, from {branch}, stays open on GitHub, since {why}. "
                                 "Finish or close it there.")
                continue

            def close(number=number, branch=branch, review=review):
                self.desk.cutover_step(self.repo, "pr-closing", pr=number, review=review)
                bodies = [one.get("body") or "" for one in
                          gh_each(f"repos/{self.repo}/issues/{number}/comments?per_page=100")]
                if not any(POINTER in body for body in bodies):
                    gh("api", "-X", "POST", f"repos/{self.repo}/issues/{number}/comments", "-f",
                       f"body={pointer(review, branch)}")
                gh("api", "-X", "PATCH", f"repos/{self.repo}/pulls/{number}", "-f", "state=closed")
                self.desk.cutover_step(self.repo, "pr-closed", pr=number, review=review)

            self.change(f"close PR #{number} with a pointer to review #{review}",
                        f"Closed PR #{number} with a pointer to review #{review}", close)

    def issues_on(self):
        return gh("api", f"repos/{self.repo}", "--jq", ".has_issues").strip() == "true"

    def issues_off(self):
        if not self.hide_issues:
            self.note("Left Issues as they are on GitHub. The desk reads only the store, so file new issues with "
                      "`agent-desk issue create`; `--issues-off` hides GitHub's.")
            return
        if not self.issues_on():
            if not self.dry_run and self.steps("issues-turning-off") and not self.steps("issues-off"):
                self.desk.cutover_step(self.repo, "issues-off")
                self.note("Issues were turned off by a cutover that stopped before recording it; recorded it.")
                self.changed.append("Turned Issues off")
                return
            self.note("Issues are off already.")
            return

        def turn_off():
            self.desk.cutover_step(self.repo, "issues-turning-off")
            gh("api", "-X", "PATCH", f"repos/{self.repo}", "-F", "has_issues=false")
            self.desk.cutover_step(self.repo, "issues-off")

        self.change("turn Issues off", "Turned Issues off", turn_off)

    # The undo.

    def undo(self):
        self.say(f"Undoing the cutover of {self.repo}{' (dry run)' if self.dry_run else ''}")
        self.step("check", self.check_checkout)
        recorded = self.recorded()
        if recorded is None:
            raise Failed("undo", f"{self.repo} has no cutover to undo.")
        if self.github:
            self.step("issues", self.issues_back)
            self.step("workflows", self.workflows_back)
            self.step("pulls", self.pulls_back)
        self.step("local items", lambda: self.local_items(recorded["at"]))
        self.step("record", self.record_undo)
        if self.github:
            self.left.append(f"{self.repo}'s workflows need self-hosted runners and the agent-desk command they call, "
                             "which this agent-desk no longer has. Check out its `pre-local-desk` tag, and install "
                             "the runners with its `agent-desk runners install`.")

    def issues_back(self):
        if not self.steps("issues-turning-off", "issues-off"):
            self.note("The cutover didn't turn Issues off, so they stay as they are.")
            return
        if self.issues_on():
            self.note("Issues are on already.")
            return
        self.change("turn Issues back on", "Turned Issues back on",
                    lambda: gh("api", "-X", "PATCH", f"repos/{self.repo}", "-F", "has_issues=true"))

    def workflows_back(self):
        disabled = {one["workflow"]: one["path"] for one in self.steps("workflow-disabling", "workflow-disabled")}
        if not disabled:
            self.note("The cutover disabled no workflow.")
            return
        states = {one["id"]: one["state"] for one in self.workflows()}
        for workflow, path in disabled.items():
            if states.get(workflow) == "active":
                self.note(f"{path} is on already.")
                continue
            self.change(f"enable {path}", f"Enabled {path}",
                        lambda workflow=workflow: gh("api", "-X", "PUT",
                                                    f"repos/{self.repo}/actions/workflows/{workflow}/enable"))

    def pulls_back(self):
        closed = list(dict.fromkeys(one["pr"] for one in self.steps("pr-closing", "pr-closed")))
        if not closed:
            self.note("The cutover closed no PR.")
        for number in closed:
            try:
                if self.pull_state(number) == "open":
                    self.note(f"PR #{number} is open already.")
                    continue
                self.change(f"reopen PR #{number}", f"Reopened PR #{number}",
                            lambda number=number: gh("api", "-X", "PATCH", f"repos/{self.repo}/pulls/{number}",
                                                     "-f", "state=open"))
            except GitHubError as error:
                self.note(f"Couldn't reopen PR #{number}: {error}")
                self.left.append(f"Reopen PR #{number} of {self.repo} on GitHub by hand: {error}")

    def local_items(self, since):
        found = [one for kind in ("issue", "review")
                 for one in self.desk.items(self.repo, kind, state="all", since=since, limit=-1)]
        if not found:
            self.note(f"Nothing was filed in the store since the cutover at {since}.")
            return
        for one in sorted(found, key=lambda one: one["number"]):
            self.note(f"#{one['number']} {one['kind']}, {one['status']}: {one['title']}")
        self.left.append(
            f"Refile on GitHub the {plural(len(found), 'item')} filed in {self.repo}'s store since {since}, which "
            f"`agent-desk issue list --state all --since {since}` and `agent-desk review list --state all --since "
            f"{since}` list. A refiled item takes a new GitHub number, so note the new number on any item a commit "
            "cites by its local one.")

    def record_undo(self):
        if self.dry_run:
            self.note("Would record the undo, so GitHub is the project's tracker again.")
            return
        self.desk.cutover_step(self.repo, "undone")
        self.note(f"Recorded the undo; GitHub is {self.repo}'s tracker again.")

    def holdings(self):
        """What the store holds of the project, in one line."""
        counts = self.open_counts()
        reviews = self.desk.db.execute("SELECT COUNT(*) FROM items WHERE project = ? AND kind = 'review'"
                                       " AND state != 'closed'", (self.repo,)).fetchone()[0]
        recorded = self.recorded()
        states = ", ".join(f"{state} {count}" for state, count in sorted(counts.items()))
        return (f"{plural(sum(counts.values()), 'open issue')} ({states or 'none'}) and "
                f"{plural(reviews, 'open review')}; "
                + (f"the store has been the tracker since {recorded['at']}." if recorded
                   else "GitHub is the tracker."))


def plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


def pointer(review, branch):
    return (f"Moved to the local desk: this PR continues as review #{review} in agent-desk's local store, from "
            f"`{branch}`. The branch stays on GitHub, and `agent-desk cutover --undo` reopens this PR.\n\n"
            f"{POINTER}\n{SIGNATURE}")


def services(dry_run, say, left, run=subprocess.run):
    """Restarts the dispatcher on the new code when its service runs, and leaves the web page and the backup timer,
    which a person restarts, in `left`."""
    for unit, turn_on in SERVICES:
        try:
            active = run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0
        except FileNotFoundError:
            active = False
        restart = f"systemctl --user restart {unit}"
        if not active:
            left.append(f"{unit} isn't running. Turn it on with `{turn_on}`"
                        + (", or restart the `agent-desk web run` in your terminal." if "web" in unit else "."))
        elif unit.startswith("agent-desk-dispatch") and not dry_run:
            done = run(restart.split(), capture_output=True, text=True)
            if done.returncode == 0:
                say(f"Restarted {unit} on the new code.")
            else:
                left.append(f"Restarting {unit} failed: {(done.stderr or '').strip()}. Run `{restart}`.")
        else:
            left.append(f"Restart {unit} on the new code with `{restart}`.")


def summary(cutovers, left, say=print):
    say("\nSummary")
    changed = [f"{one.repo}: {line}" for one in cutovers for line in one.changed]
    say("GitHub:" + ("" if changed else " nothing changed."))
    for line in changed:
        say(f"- {line}")
    say("The store:")
    for one in cutovers:
        say(f"- {one.repo}: {one.holdings()}")
    left = [line for one in cutovers for line in one.left] + left
    say("Left for you:" + ("" if left else " nothing."))
    for line in left:
        say(f"- {line}")


def open_store(dry_run):
    """
    The store, or for a dry run the store opened read-only, which refuses a database whose schema is older than this
    code's, since only the installed agent-desk migrates it. The header check comes first, so a refused database is
    never opened.
    """
    path = store.DATABASE
    older = f"{path} has schema {{}}, older than this code's {store.SCHEMA_VERSION}, and a dry run never migrates " \
            "it. Run the dry run from the installed agent-desk once it has migrated the store, or against a copy of " \
            "the state folder named by XDG_STATE_HOME."
    if not dry_run:
        return store.Store()
    on_disk = schema_on_disk(path)
    if on_disk is not None and on_disk < store.SCHEMA_VERSION and not Path(f"{path}-wal").exists():
        raise store.StoreError(older.format(on_disk))
    desk = store.Store(read_only=True)
    if desk.version < store.SCHEMA_VERSION:
        desk.close()
        raise store.StoreError(older.format(desk.version))
    return desk


def main(argv):
    parser = argparse.ArgumentParser(prog="agent-desk cutover", usage=USAGE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--undo", "--rollback", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--issues-off", action="store_true")
    parser.add_argument("repo", nargs="?")
    args = parser.parse_args(argv)
    if (args.all == bool(args.repo) or (args.undo and (args.all or args.issues_off))
            or (args.repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo))):
        print(f"usage: {USAGE}", file=sys.stderr)
        return 2
    if args.all:
        targets = projects()
        if not targets:
            print(f"agent-desk: {store.PROJECTS} lists no project.", file=sys.stderr)
            return 2
    else:
        checkout = dict(projects()).get(args.repo)
        if not checkout:
            print(f"agent-desk: {store.PROJECTS} names no main checkout of {args.repo}; run `agent-desk install` in "
                  "it.", file=sys.stderr)
            return 2
        targets = [(args.repo, checkout)]
    cutovers, left, status = [], [], 0
    try:
        desk = open_store(args.dry_run)
    except (store.StoreError, sqlite3.Error) as error:
        print(f"agent-desk: {error}", file=sys.stderr)
        return 1
    try:
        for repo, checkout in targets:
            one = Cutover(desk, repo, checkout, args.dry_run, hide_issues=args.issues_off)
            cutovers.append(one)
            try:
                one.undo() if args.undo else one.run()
            except Failed as failure:
                print(f"agent-desk: {'the undo' if args.undo else 'the cutover'} of {repo} stopped at "
                      f"{failure.step}: {failure.message}", file=sys.stderr)
                status = 1
                break
        if not args.undo and status == 0:
            services(args.dry_run, print, left)
        summary(cutovers, left)
    finally:
        desk.close()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
