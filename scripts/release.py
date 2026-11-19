#!/usr/bin/env python3
"""Cuts an agent-desk release as a local review, and publishes it once the review merges.

    just release [major|minor|patch|X.Y.Z]
    python3 scripts/release.py [--dry-run] [major|minor|patch|X.Y.Z]
    python3 scripts/release.py publish

A release folds the entries waiting in changelog.d/ into a `## [X.Y.Z] - <date>` section of CHANGELOG.md, deletes
them, and sets VERSION and the plugin's plugin.json to the new version. Without a bump, the entries pick the version:
below 1.0.0, a **Breaking** or **Workflows** entry bumps the minor number and anything else the patch number; from
1.0.0 on, **Breaking** bumps the major number, **Workflows** the minor, and anything else the patch.

The script runs from a clean checkout of main that matches origin. It commits the release on agent/release-X.Y.Z,
opens a review of that branch titled `Release X.Y.Z` in the local store with `agent-desk review create`, whose body is
the new section, and switches back to main. When a step fails, it drops the release branch and its changes and
switches back to main, so a rerun starts over. It refuses when changelog.d holds no entries or `agent-desk review list`
shows a release review already open. --dry-run prints the version and the section, skips the checks of the checkout
and the store, and changes nothing.

`publish`, which the push queue (lib/pushes.py) runs in agent-desk's main checkout once it pushes a main whose VERSION
changed, tags vX.Y.Z in local git at main's commit that last changed VERSION, pushes the tag to origin, and runs the
release step of the config's `host`: for `github`, `gh release create` with the version's CHANGELOG.md section as
notes, and for `none`, nothing. Last, when the checkout has a `public` remote, it starts scripts/publish.py for the tag
in a systemd user unit of its own, which waits on the hosted test and pushes the release to the public repository with
its log in the journal, and returns without waiting for it. Without the remote it says it skipped that step. It
refuses unless the checkout is on main with no uncommitted changes to tracked files and origin's main holds that
commit, and it refuses when the tag already names another commit. Each step skips what an earlier run finished, so
after a failed push, release step, or public publish the tag stays, and running `just release publish` again finishes
the rest.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
BREAKING = "**Breaking**:"
WORKFLOWS = "**Workflows**:"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
BRANCH_PREFIX = "agent/release-"
PLUGIN = Path("inbox/.claude-plugin/plugin.json")
CONFIG = ".agent-desk.json"
HOSTS = ("github", "none")
PUBLIC_REMOTE = "public"
# The variables the public publish's unit takes from the caller, so it finds git, gh, and the ssh agent.
PUBLIC_ENV = ("PATH", "SSH_AUTH_SOCK")


class ReleaseError(Exception):
    """A reason the release stops, which the script prints."""


def gh(root, args, input=None):
    """gh's output for `args` run in `root`, with `input` on its stdin."""
    return subprocess.run(["gh", *args], cwd=root, input=input, capture_output=True, text=True, check=True).stdout


def desk(root, args, input=None):
    """The output of `agent-desk <args>` run in `root`, so the store finds the project from its origin."""
    return subprocess.run(["agent-desk", *args], cwd=root, input=input, capture_output=True, text=True,
                          check=True).stdout


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def natural(name):
    """A sort key that puts issue-9.md before issue-10.md."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def entry_files(root):
    """The pending entry files in changelog.d, in issue order."""
    folder = Path(root, "changelog.d")
    return sorted((path for path in folder.glob("*.md") if path.name != "README.md"), key=lambda p: natural(p.name))


def parse(name, text):
    """The items of one entry file by section, each item its lines joined with newlines."""
    sections = {}
    current = None
    item = None
    for number, line in enumerate(text.splitlines(), 1):
        heading = re.match(r"^### (\S.*)$", line)
        if not line.strip():
            item = None
        elif heading:
            current = heading.group(1).strip()
            if current not in SECTIONS:
                raise ReleaseError(f"{name}:{number}: `### {current}` is not a Keep a Changelog section")
            sections.setdefault(current, [])
            item = None
        elif line.startswith("- ") and current:
            sections[current].append(line)
            item = len(sections[current]) - 1
        elif line.startswith("  ") and item is not None:
            sections[current][item] += "\n" + line
        else:
            raise ReleaseError(f"{name}:{number}: changelog.d/README.md describes the lines an entry file holds")
    return sections


def gather(files):
    """Every file's items, merged by section in Keep a Changelog's order and in file order within each."""
    merged = {section: [] for section in SECTIONS}
    for path in files:
        for section, items in parse(path.name, path.read_text()).items():
            merged[section].extend(items)
    return {section: items for section, items in merged.items() if items}


def parse_version(text):
    match = SEMVER.match(text.strip())
    if not match:
        raise ReleaseError(f"`{text.strip()}` isn't a version of the form X.Y.Z")
    return tuple(int(number) for number in match.groups())


def bumped(current, part):
    major, minor, patch = parse_version(current)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def markers(items):
    """The markers among the items' leading text: Breaking, Workflows, both, or neither."""
    found = set()
    for item in items:
        text = item[2:]
        if text.startswith(BREAKING):
            found.add(BREAKING)
            text = text[len(BREAKING):].lstrip()
        if text.startswith(WORKFLOWS):
            found.add(WORKFLOWS)
    return found


def choose_version(current, sections, bump=None):
    """The release's version: `bump` as a part or a version when given, or the one the entries' markers pick."""
    if bump in ("major", "minor", "patch"):
        return bumped(current, bump)
    if bump:
        if parse_version(bump) <= parse_version(current):
            raise ReleaseError(f"{bump} isn't above the current version {current}")
        return bump
    found = markers([item for items in sections.values() for item in items])
    if parse_version(current)[0] == 0:
        return bumped(current, "minor" if found else "patch")
    if BREAKING in found:
        return bumped(current, "major")
    return bumped(current, "minor" if WORKFLOWS in found else "patch")


def section_text(version, day, sections):
    """The changelog section for the release, ending in a newline."""
    parts = [f"## [{version}] - {day}"]
    for section, items in sections.items():
        parts.append(f"### {section}\n\n" + "\n".join(items))
    return "\n\n".join(parts) + "\n"


def fold(changelog, current, version, section):
    """`changelog` with `section` above the newest release and the version's compare link beside the others."""
    newest = re.search(r"^## \[\d+\.\d+\.\d+\]", changelog, re.MULTILINE)
    if not newest:
        raise ReleaseError("CHANGELOG.md has no release section to put the new one above")
    changelog = changelog[:newest.start()] + section + "\n" + changelog[newest.start():]
    unreleased = re.search(r"^\[Unreleased\]: (\S+)/compare/\S+\.\.\.HEAD$", changelog, re.MULTILINE)
    if not unreleased:
        raise ReleaseError("CHANGELOG.md has no `[Unreleased]: <repo>/compare/v<version>...HEAD` link")
    base = unreleased.group(1)
    links = f"[Unreleased]: {base}/compare/v{version}...HEAD\n[{version}]: {base}/compare/v{current}...v{version}"
    return changelog[:unreleased.start()] + links + changelog[unreleased.end():]


def set_plugin_version(text, version):
    """plugin.json's text with its version set to `version`, keeping its layout."""
    updated, count = re.subn(r'^(  "version": ")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1,
                             flags=re.MULTILINE)
    if not count or json.loads(updated).get("version") != version:
        raise ReleaseError(f"{PLUGIN} has no top-level version to set")
    return updated


def release_notes(changelog, version):
    """The body of the version's CHANGELOG.md section, without its heading."""
    heading = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", changelog, re.MULTILINE)
    if not heading:
        raise ReleaseError(f"CHANGELOG.md has no section for {version}")
    rest = changelog[heading.end():]
    end = re.search(r"^(## |\[[^\]]+\]: )", rest, re.MULTILINE)
    return (rest[:end.start()] if end else rest).strip() + "\n"


def checkout_problem(root):
    """Why the checkout can't cut a release, or None when it is a clean main that matches origin."""
    if git(root, "status", "--porcelain"):
        return "The checkout has uncommitted changes. Commit or remove them, then run the release again."
    branch = git(root, "branch", "--show-current").strip()
    if branch != "main":
        return f"Run the release from main; the checkout is on {branch or 'a detached HEAD'}."
    git(root, "fetch", "-q", "origin", "main")
    if git(root, "rev-parse", "HEAD") != git(root, "rev-parse", "FETCH_HEAD"):
        return "main differs from origin's main. Pull or push it, then run the release again."
    return None


def open_release_review(root):
    """The number of an open release review in the local store, or None."""
    listed = json.loads(desk(root, ["review", "list", "--state", "open", "--limit", "100", "--json",
                                    "number,headRefName"]))
    for review in listed:
        if review["headRefName"].startswith(BRANCH_PREFIX):
            return review["number"]
    return None


def plan(root, bump, today):
    """The release's version, its changelog section, and the entry files it folds."""
    files = entry_files(root)
    if not files:
        raise ReleaseError("Nothing to release: changelog.d holds no entries.")
    current = Path(root, "VERSION").read_text().strip()
    sections = gather(files)
    version = choose_version(current, sections, bump)
    return current, version, section_text(version, today, sections), files


def cut(root, bump, today):
    """Opens the release review and prints its number."""
    current, version, section, files = plan(root, bump, today)
    problem = checkout_problem(root)
    if problem:
        raise ReleaseError(problem)
    review = open_release_review(root)
    if review:
        raise ReleaseError(f"Release review #{review} is already open. Merge or close it first.")
    branch = f"{BRANCH_PREFIX}{version}"
    git(root, "switch", "-q", "-c", branch)
    opened = False
    try:
        changelog = Path(root, "CHANGELOG.md")
        changelog.write_text(fold(changelog.read_text(), current, version, section))
        Path(root, "VERSION").write_text(version + "\n")
        plugin = Path(root, PLUGIN)
        plugin.write_text(set_plugin_version(plugin.read_text(), version))
        git(root, "rm", "-q", *(str(path.relative_to(root)) for path in files))
        git(root, "add", "CHANGELOG.md", "VERSION", str(PLUGIN))
        git(root, "commit", "-q", "-m", f"Release {version}")
        number = desk(root, ["review", "create", "--branch", branch, "--base", "main", "--title",
                             f"Release {version}", "--body-file", "-"], input=section).strip()
        opened = True
    finally:
        # checkout_problem found the checkout clean, so the reset discards only the release's own changes, and a
        # rerun starts over from main.
        if not opened:
            git(root, "reset", "-q", "--hard")
        git(root, "switch", "-q", "main")
        if not opened:
            git(root, "branch", "-q", "-D", branch)
    print(f"Opened review #{number} of {branch} for {version}.")


def host_of(root):
    """The config's `host`, which picks the publish step: github when the config leaves it out."""
    try:
        config = json.loads(Path(root, CONFIG).read_text())
    except FileNotFoundError:
        config = {}
    except ValueError as error:
        raise ReleaseError(f"{CONFIG} isn't valid JSON: {error}")
    host = config.get("host", "github") if isinstance(config, dict) else "github"
    if host not in HOSTS:
        raise ReleaseError(f"{CONFIG} names the host {host!r}; the hosts are {', '.join(HOSTS)}.")
    return host


def tag_commit(root, tag):
    """The commit `tag` names in local git, or None when it doesn't exist."""
    found = subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
                           capture_output=True, text=True)
    return found.stdout.strip() if found.returncode == 0 else None


def has_origin(root):
    return "origin" in git(root, "remote").split()


def github_release(root, tag, version):
    """Creates the GitHub Release of a pushed tag, with the version's changelog section as notes, unless it exists."""
    try:
        gh(root, ["release", "view", tag, "--json", "tagName"])
        print(f"The GitHub Release for {tag} already exists.")
        return
    except subprocess.CalledProcessError as error:
        if "release not found" not in (error.stderr or ""):
            raise
    notes = release_notes(Path(root, "CHANGELOG.md").read_text(), version)
    gh(root, ["release", "create", tag, "--verify-tag", "--title", f"agent-desk {version}", "--notes-file", "-"],
       input=notes)
    print(f"Created the GitHub Release for {tag}.")


RELEASE_STEPS = {"github": github_release, "none": None}


def start_public_publish(root, tag):
    """Starts scripts/publish.py for `tag` in a systemd user unit of its own and returns the unit's name. systemd
    refuses a second unit of the name while the first runs, so one tag publishes once at a time."""
    unit = f"agent-desk-publish-{tag}"
    args = ["systemd-run", "--user", "--unit", unit, "--collect", "--quiet", "--description",
            f"Publish agent-desk {tag} to the public repository", "--working-directory", str(root)]
    for name in PUBLIC_ENV:
        if name in os.environ:
            args += ["--setenv", f"{name}={os.environ[name]}"]
    subprocess.run([*args, "--", sys.executable, "-B", str(Path(root, "scripts", "publish.py")), tag],
                   capture_output=True, text=True, check=True)
    return unit


def publish_problem(root):
    """Why the checkout can't publish, or None when it is on main with no uncommitted changes to tracked files."""
    branch = git(root, "branch", "--show-current").strip()
    if branch != "main":
        return f"Publish from main; the checkout is on {branch or 'a detached HEAD'}."
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        return "The checkout has uncommitted changes. Commit or remove them, then run `just release publish` again."
    return None


def merged_on_origin(root, sha):
    """Whether origin's main holds `sha`, after a fetch that may fail and leave the last one's main."""
    subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main"],
                   capture_output=True, text=True)
    return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", sha, "refs/remotes/origin/main"],
                          capture_output=True, text=True).returncode == 0


def publish(root):
    """Tags the version in VERSION, pushes the tag, runs the host's release step, and starts the public publish,
    skipping what's done.

    It tags only main's commit that last changed VERSION, and only once origin's main holds it, so a release branch
    or an unpushed main never publishes a release that didn't merge."""
    host = host_of(root)
    problem = publish_problem(root)
    if problem:
        raise ReleaseError(problem)
    version = Path(root, "VERSION").read_text().strip()
    tag = f"v{version}"
    sha = git(root, "log", "-1", "--first-parent", "--format=%H", "--", "VERSION").strip()
    origin = has_origin(root)
    if origin and not merged_on_origin(root, sha):
        raise ReleaseError(f"origin's main doesn't hold {sha[:12]}, which set VERSION to {version}. Push main or "
                           "merge the release, then run `just release publish` again.")
    tagged = tag_commit(root, tag)
    if tagged and tagged != sha:
        raise ReleaseError(f"{tag} already exists at {tagged[:12]}, but VERSION became {version} at {sha[:12]}. "
                           "Check the tag, delete it if it is wrong, and run `just release publish` again.")
    if tagged:
        print(f"{tag} already exists at {sha[:12]}.")
    else:
        git(root, "tag", tag, sha)
        print(f"Tagged {tag} at {sha[:12]}.")
    rerun = "The tag stays; run `just release publish` again to finish."
    if origin:
        try:
            git(root, "push", "-q", "origin", f"refs/tags/{tag}")
        except subprocess.CalledProcessError as error:
            raise ReleaseError(f"Pushing {tag} to origin failed: {(error.stderr or '').strip()}\n{rerun}")
        print(f"Pushed {tag} to origin.")
    step = RELEASE_STEPS[host]
    if step:
        try:
            step(root, tag, version)
        except subprocess.CalledProcessError as error:
            raise ReleaseError(f"The {host} release step for {tag} failed: {(error.stderr or '').strip()}\n{rerun}")
    print(f"Published {tag}.")
    if PUBLIC_REMOTE not in git(root, "remote").split():
        print(f"Skipped the public publish: the checkout has no `{PUBLIC_REMOTE}` remote.")
        return
    try:
        unit = start_public_publish(root, tag)
    except (OSError, subprocess.CalledProcessError) as error:
        why = (getattr(error, "stderr", None) or str(error)).strip()
        raise ReleaseError(f"Starting the public publish of {tag} failed: {why}\n{rerun}")
    print(f"Publishing {tag} to the public repository in {unit}; follow it with `journalctl --user -u {unit} -f`.")


def main(argv, root=ROOT, today=None):
    parser = argparse.ArgumentParser(prog="scripts/release.py", description=__doc__.strip().splitlines()[0])
    parser.add_argument("bump", nargs="?",
                        help="major, minor, patch, or X.Y.Z, which the entries pick when left out; or publish, to "
                             "tag VERSION, push the tag, run the host's release step, and start the public publish")
    parser.add_argument("--dry-run", action="store_true", help="print the version and section, and change nothing")
    args = parser.parse_args(argv)
    if args.bump and args.bump not in ("major", "minor", "patch", "publish") and not SEMVER.match(args.bump):
        parser.error(f"the argument is major, minor, patch, a version X.Y.Z, or publish, not {args.bump}")
    if args.bump == "publish" and args.dry_run:
        parser.error("publish has no dry run")
    today = today or datetime.date.today().isoformat()
    try:
        if args.bump == "publish":
            publish(root)
        elif args.dry_run:
            _, version, section, _ = plan(root, args.bump, today)
            print(f"Version: {version}\n\n{section}", end="")
        else:
            cut(root, args.bump, today)
    except ReleaseError as error:
        print(error, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"`{' '.join(error.cmd)}` failed: {(error.stderr or '').strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
