"""Tests for scripts/release.py, which cuts an agent-desk release as a local review and publishes it after the merge.
They run against a temporary repository whose origin is a local bare one, with `agent-desk` and gh replaced, so nothing
reaches the store or GitHub."""

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# lib/release.py is a different module that tests/test_release.py imports as `release`, so this one gets its own name.
spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

TODAY = "2026-11-16"
SKIPPED = "Skipped the public publish: the checkout has no `public` remote.\n"
CHANGELOG = """# Changelog

Intro.

## [Unreleased]

The changes since the last release sit in `changelog.d/`.

## [0.1.0] - 2026-11-11

### Added

- The first release. (#1)

[Unreleased]: https://github.com/o/r/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/o/r/releases/tag/v0.1.0
"""
PLUGIN = """{
  "name": "agent-desk",
  "version": "0.1.0",
  "author": {
    "name": "someone"
  }
}
"""
ENTRIES = {
    "issue-10.md": "### Fixed\n\n- A fix that wraps\n  onto a second line. (#10)\n\n### Added\n\n- A command. (#10)\n",
    "issue-9.md": "### Changed\n\n- **Workflows**: A workflow change. (#9)\n\n### Added\n\n- A pane row. (#9)\n",
    "issue-11.md": "### Security\n\n- A safer default. (#11)\n\n### Removed\n\n- An old flag. (#11)\n",
}
SECTION = """## [0.2.0] - 2026-11-16

### Added

- A pane row. (#9)
- A command. (#10)

### Changed

- **Workflows**: A workflow change. (#9)

### Removed

- An old flag. (#11)

### Fixed

- A fix that wraps
  onto a second line. (#10)

### Security

- A safer default. (#11)
"""


def items(*texts):
    return {"Added": [f"- {text}" for text in texts]}


class ChooseVersionTest(unittest.TestCase):
    def test_below_one_breaking_and_workflows_bump_the_minor_number(self):
        self.assertEqual(release.choose_version("0.3.4", items("**Breaking**: A label changed. (#1)")), "0.4.0")
        self.assertEqual(release.choose_version("0.3.4", items("**Workflows**: A job changed. (#1)")), "0.4.0")
        self.assertEqual(release.choose_version("0.3.4", items("**Breaking**: **Workflows**: Both. (#1)")), "0.4.0")

    def test_below_one_anything_else_bumps_the_patch_number(self):
        self.assertEqual(release.choose_version("0.3.4", items("A command. (#1)", "Another. (#2)")), "0.3.5")

    def test_from_one_on_each_marker_bumps_its_own_number(self):
        self.assertEqual(release.choose_version("1.2.3", items("A. (#1)", "**Breaking**: B. (#2)")), "2.0.0")
        self.assertEqual(release.choose_version("1.2.3", items("**Breaking**: **Workflows**: B. (#2)")), "2.0.0")
        self.assertEqual(release.choose_version("1.2.3", items("A. (#1)", "**Workflows**: W. (#2)")), "1.3.0")
        self.assertEqual(release.choose_version("1.2.3", items("A. (#1)")), "1.2.4")

    def test_a_marker_counts_only_at_an_items_start(self):
        self.assertEqual(release.choose_version("1.2.3", items("Mentions **Breaking**: later. (#1)")), "1.2.4")

    def test_an_argument_overrides_the_entries(self):
        breaking = items("**Breaking**: B. (#1)")
        self.assertEqual(release.choose_version("1.2.3", breaking, "patch"), "1.2.4")
        self.assertEqual(release.choose_version("1.2.3", items("A. (#1)"), "major"), "2.0.0")
        self.assertEqual(release.choose_version("0.9.1", items("A. (#1)"), "minor"), "0.10.0")
        self.assertEqual(release.choose_version("0.9.1", breaking, "1.0.0"), "1.0.0")

    def test_a_named_version_must_be_above_the_current_one(self):
        with self.assertRaisesRegex(release.ReleaseError, "isn't above"):
            release.choose_version("1.2.3", items("A. (#1)"), "1.2.3")


class FoldTest(unittest.TestCase):
    def folder(self, entries):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(folder)], check=True))
        (folder / "changelog.d").mkdir()
        (folder / "changelog.d" / "README.md").write_text("# Pending entries\n\n- Not an entry.\n")
        for name, text in entries.items():
            (folder / "changelog.d" / name).write_text(text)
        return folder

    def test_items_merge_under_each_heading_in_keep_a_changelog_order(self):
        sections = release.gather(release.entry_files(self.folder(ENTRIES)))
        self.assertEqual(list(sections), ["Added", "Changed", "Removed", "Fixed", "Security"])
        self.assertEqual(release.section_text("0.2.0", TODAY, sections), SECTION)

    def test_an_unknown_heading_stops_the_release(self):
        folder = self.folder({"issue-5.md": "### Improved\n\n- A change. (#5)\n"})
        with self.assertRaisesRegex(release.ReleaseError, "Improved"):
            release.gather(release.entry_files(folder))

    def test_the_section_goes_under_unreleased_with_its_compare_link(self):
        folded = release.fold(CHANGELOG, "0.1.0", "0.2.0", SECTION)
        unreleased = folded.index("## [Unreleased]")
        self.assertLess(unreleased, folded.index("## [0.2.0] - 2026-11-16"))
        self.assertLess(folded.index("## [0.2.0]"), folded.index("## [0.1.0]"))
        self.assertIn("- A safer default. (#11)\n\n## [0.1.0] - 2026-11-11\n", folded)
        self.assertTrue(folded.endswith(
            "[Unreleased]: https://github.com/o/r/compare/v0.2.0...HEAD\n"
            "[0.2.0]: https://github.com/o/r/compare/v0.1.0...v0.2.0\n"
            "[0.1.0]: https://github.com/o/r/releases/tag/v0.1.0\n"))

    def test_the_plugin_manifest_takes_the_version_and_keeps_its_layout(self):
        self.assertEqual(release.set_plugin_version(PLUGIN, "0.2.0"), PLUGIN.replace('"0.1.0"', '"0.2.0"'))

    def test_release_notes_are_the_versions_section_without_heading_or_links(self):
        folded = release.fold(CHANGELOG, "0.1.0", "0.2.0", SECTION)
        self.assertEqual(release.release_notes(folded, "0.2.0"), SECTION.split("\n", 2)[2])
        self.assertEqual(release.release_notes(folded, "0.1.0"), "### Added\n\n- The first release. (#1)\n")
        with self.assertRaisesRegex(release.ReleaseError, "no section for 0.3.0"):
            release.release_notes(folded, "0.3.0")


def run(*args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout


class Desk:
    """A stand-in for `agent-desk review` that records its calls and lists the reviews in `reviews`."""

    def __init__(self, reviews=()):
        self.reviews = list(reviews)
        self.calls = []

    def __call__(self, root, args, input=None):
        self.calls.append((args, input, run("git", "rev-parse", "--verify", "-q", args[3], cwd=root).strip()
                           if args[:2] == ["review", "create"] else None))
        if args[:2] == ["review", "list"]:
            return json.dumps(self.reviews)
        if args[:2] == ["review", "create"]:
            return "99\n"
        raise AssertionError(f"unexpected agent-desk call: {args}")

    def created(self):
        return [call for call in self.calls if call[0][1] == "create"]


class GitHub:
    """A stand-in for gh that records its calls, knows the releases in `releases`, and fails a call whose first two
    arguments are in `failing`."""

    def __init__(self, releases=(), failing=()):
        self.releases = list(releases)
        self.failing = list(failing)
        self.calls = []

    def __call__(self, root, args, input=None):
        self.calls.append((args, input))
        if args[:2] in self.failing:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502: Bad Gateway\n")
        if args[:2] == ["release", "view"]:
            if args[2] in self.releases:
                return "{}"
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="release not found\n")
        if args[:2] == ["release", "create"]:
            self.releases.append(args[2])
            return ""
        raise AssertionError(f"unexpected gh call: {args}")

    def created(self):
        return [call for call in self.calls if call[0][1] == "create"]


class ScriptTest(unittest.TestCase):
    """Runs the script's main against a repository with a local bare origin."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.origin = Path(temp.name, "origin.git")
        self.root = Path(temp.name, "work")
        run("git", "init", "-q", "--bare", "-b", "main", str(self.origin), cwd=temp.name)
        run("git", "init", "-q", "-b", "main", str(self.root), cwd=temp.name)
        (self.root / "changelog.d").mkdir()
        (self.root / "changelog.d" / "README.md").write_text("# Pending entries\n")
        for name, text in ENTRIES.items():
            (self.root / "changelog.d" / name).write_text(text)
        (self.root / "CHANGELOG.md").write_text(CHANGELOG)
        (self.root / "VERSION").write_text("0.1.0\n")
        (self.root / release.PLUGIN).parent.mkdir(parents=True)
        (self.root / release.PLUGIN).write_text(PLUGIN)
        run("git", "add", "-A", cwd=self.root)
        run("git", "commit", "-q", "-m", "Start", cwd=self.root)
        run("git", "remote", "add", "origin", str(self.origin), cwd=self.root)
        run("git", "push", "-q", "-u", "origin", "main", cwd=self.root)
        self.desk = Desk()
        self.github = GitHub()
        for name, fake in (("desk", self.desk), ("gh", self.github)):
            patcher = mock.patch.object(release, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = release.main(list(argv), root=self.root, today=TODAY)
        return status, out.getvalue(), err.getvalue()

    def files(self):
        return {path.relative_to(self.root).as_posix(): path.read_text()
                for path in self.root.rglob("*") if path.is_file() and ".git" not in path.parts}

    def empty_the_folder(self):
        for name in ENTRIES:
            run("git", "rm", "-q", f"changelog.d/{name}", cwd=self.root)
        run("git", "commit", "-q", "-m", "Released", cwd=self.root)
        run("git", "push", "-q", cwd=self.root)

    def test_a_release_opens_a_local_review_of_its_branch_and_returns_to_main(self):
        status, out, err = self.main()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "Opened review #99 of agent/release-0.2.0 for 0.2.0.\n")
        head = run("git", "rev-parse", "agent/release-0.2.0", cwd=self.root).strip()
        self.assertEqual(self.desk.created(), [(["review", "create", "--branch", "agent/release-0.2.0", "--base",
                                                 "main", "--title", "Release 0.2.0", "--body-file", "-"], SECTION,
                                                head)])
        self.assertEqual(run("git", "branch", "--show-current", cwd=self.root), "main\n")
        self.assertEqual(run("git", "status", "--porcelain", cwd=self.root), "")
        self.assertEqual(run("git", "branch", "-r", cwd=self.root).split(), ["origin/main"])
        self.assertEqual(run("git", "log", "-1", "--format=%s", "agent/release-0.2.0", cwd=self.root),
                         "Release 0.2.0\n")
        show = lambda path: run("git", "show", f"agent/release-0.2.0:{path}", cwd=self.root)  # noqa: E731
        self.assertEqual(show("VERSION"), "0.2.0\n")
        self.assertEqual(json.loads(show(release.PLUGIN.as_posix()))["version"], "0.2.0")
        self.assertEqual(show("CHANGELOG.md"), release.fold(CHANGELOG, "0.1.0", "0.2.0", SECTION))
        listed = run("git", "ls-tree", "--name-only", "agent/release-0.2.0", "changelog.d/", cwd=self.root)
        self.assertEqual(listed, "changelog.d/README.md\n")
        self.assertEqual(self.github.calls, [])

    def test_an_argument_picks_the_version(self):
        status, out, _ = self.main("1.0.0")
        self.assertEqual((status, out), (0, "Opened review #99 of agent/release-1.0.0 for 1.0.0.\n"))
        self.assertEqual(self.desk.created()[0][0][7], "Release 1.0.0")

    def test_a_dry_run_prints_the_version_and_section_and_changes_nothing(self):
        before = self.files()
        status, out, err = self.main("--dry-run")
        self.assertEqual((status, out, err), (0, f"Version: 0.2.0\n\n{SECTION}", ""))
        self.assertEqual(self.files(), before)
        self.assertEqual(run("git", "branch", "--list", cwd=self.root), "* main\n")
        self.assertEqual(self.desk.calls, [])

    def test_an_empty_folder_stops_the_release(self):
        self.empty_the_folder()
        status, out, err = self.main()
        self.assertEqual((status, out), (1, ""))
        self.assertIn("changelog.d holds no entries", err)
        self.assertEqual(self.desk.calls, [])

    def test_an_open_release_review_stops_the_release(self):
        self.desk.reviews = [{"number": 7, "headRefName": "agent/issue-7"},
                             {"number": 98, "headRefName": "agent/release-0.2.0"}]
        status, _, err = self.main()
        self.assertEqual(status, 1)
        self.assertIn("Release review #98 is already open", err)
        self.assertEqual(self.desk.created(), [])
        self.assertEqual(run("git", "branch", "--list", cwd=self.root), "* main\n")

    def test_a_failed_review_create_still_returns_to_main(self):
        def failing(root, args, input=None):
            if args[:2] == ["review", "list"]:
                return "[]"
            raise subprocess.CalledProcessError(1, ["agent-desk", *args], stderr="agent-desk: no project here\n")
        with mock.patch.object(release, "desk", failing):
            status, _, err = self.main()
        self.assertEqual(status, 1)
        self.assertIn("no project here", err)
        self.assert_back_on_a_clean_main()
        status, out, _ = self.main()
        self.assertEqual((status, out), (0, "Opened review #99 of agent/release-0.2.0 for 0.2.0.\n"))

    def assert_back_on_a_clean_main(self):
        """The checkout is back on main as the release found it, with no release branch left."""
        self.assertEqual(run("git", "branch", "--list", cwd=self.root), "* main\n")
        self.assertEqual(run("git", "status", "--porcelain", cwd=self.root), "")
        self.assertEqual((self.root / "VERSION").read_text(), "0.1.0\n")
        self.assertEqual(sorted(path.name for path in (self.root / "changelog.d").glob("issue-*.md")), sorted(ENTRIES))

    def test_a_cut_that_fails_after_writing_version_returns_to_a_clean_main_and_publishes_nothing(self):
        def failing(text, version):
            raise release.ReleaseError("plugin.json has no top-level version to set")
        with mock.patch.object(release, "set_plugin_version", failing):
            status, _, err = self.main()
        self.assertEqual(status, 1)
        self.assertIn("no top-level version", err)
        self.assert_back_on_a_clean_main()
        self.assertEqual(self.desk.created(), [])
        # publish finds main as it was, so it can only tag the version main already holds.
        start = run("git", "rev-parse", "HEAD", cwd=self.root).strip()
        status, out, _ = self.main("publish")
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith(f"Tagged v0.1.0 at {start[:12]}.\n"), out)
        self.assertIsNone(release.tag_commit(self.root, "v0.2.0"))
        self.assertEqual(self.origin_tag("v0.2.0"), "")

    def test_uncommitted_changes_stop_the_release(self):
        (self.root / "VERSION").write_text("0.1.1\n")
        status, _, err = self.main()
        self.assertEqual(status, 1)
        self.assertIn("uncommitted changes", err)
        self.assertEqual(self.desk.calls, [])

    def test_a_branch_other_than_main_stops_the_release(self):
        run("git", "switch", "-q", "-c", "elsewhere", cwd=self.root)
        status, _, err = self.main()
        self.assertEqual(status, 1)
        self.assertIn("the checkout is on elsewhere", err)

    def test_a_main_behind_origin_stops_the_release(self):
        clone = self.root.parent / "clone"
        run("git", "clone", "-q", str(self.origin), str(clone), cwd=self.root.parent)
        (clone / "VERSION").write_text("0.1.1\n")
        run("git", "commit", "-q", "-am", "Elsewhere", cwd=clone)
        run("git", "push", "-q", cwd=clone)
        status, _, err = self.main()
        self.assertEqual(status, 1)
        self.assertIn("differs from origin's main", err)
        self.assertEqual(self.desk.calls, [])

    def merge_release(self, host=None):
        """Lands a 0.2.0 release on main through a merge commit, with a commit after it, as the merge queue would."""
        if host:
            (self.root / ".agent-desk.json").write_text(json.dumps({"host": host}))
            run("git", "add", ".agent-desk.json", cwd=self.root)
            run("git", "commit", "-q", "-m", "Host", cwd=self.root)
        run("git", "switch", "-q", "-c", "agent/release-0.2.0", cwd=self.root)
        (self.root / "CHANGELOG.md").write_text(release.fold(CHANGELOG, "0.1.0", "0.2.0", SECTION))
        (self.root / "VERSION").write_text("0.2.0\n")
        run("git", "commit", "-q", "-am", "Release 0.2.0", cwd=self.root)
        run("git", "switch", "-q", "main", cwd=self.root)
        run("git", "merge", "-q", "--no-ff", "-m", "Merge the release", "agent/release-0.2.0", cwd=self.root)
        merge = run("git", "rev-parse", "HEAD", cwd=self.root).strip()
        (self.root / "later.txt").write_text("later\n")
        run("git", "add", "later.txt", cwd=self.root)
        run("git", "commit", "-q", "-m", "Later", cwd=self.root)
        run("git", "push", "-q", "origin", "main", cwd=self.root)
        return merge

    def origin_tag(self, tag):
        return run("git", "tag", "--list", tag, "--format=%(objectname)", cwd=self.origin).strip()

    def test_publish_tags_the_merge_pushes_it_and_creates_the_github_release(self):
        merge = self.merge_release()
        status, out, err = self.main("publish")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, f"Tagged v0.2.0 at {merge[:12]}.\nPushed v0.2.0 to origin.\n"
                              f"Created the GitHub Release for v0.2.0.\nPublished v0.2.0.\n{SKIPPED}")
        self.assertEqual(self.origin_tag("v0.2.0"), merge)
        self.assertEqual(self.github.created(), [(["release", "create", "v0.2.0", "--verify-tag", "--title",
                                                   "agent-desk 0.2.0", "--notes-file", "-"],
                                                  SECTION.split("\n", 2)[2])])

    def test_publish_with_the_github_host_named_creates_the_release(self):
        self.merge_release("github")
        status, _, _ = self.main("publish")
        self.assertEqual(status, 0)
        self.assertEqual(len(self.github.created()), 1)

    def test_publish_with_no_host_only_tags_and_pushes(self):
        merge = self.merge_release("none")
        status, out, err = self.main("publish")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, f"Tagged v0.2.0 at {merge[:12]}.\nPushed v0.2.0 to origin.\nPublished v0.2.0.\n"
                              f"{SKIPPED}")
        self.assertEqual(self.origin_tag("v0.2.0"), merge)
        self.assertEqual(self.github.calls, [])

    def test_publish_without_an_origin_tags_locally(self):
        self.merge_release("none")
        run("git", "remote", "remove", "origin", cwd=self.root)
        status, out, _ = self.main("publish")
        self.assertEqual(status, 0)
        self.assertNotIn("Pushed", out)
        self.assertTrue(release.tag_commit(self.root, "v0.2.0"))

    def test_an_unknown_host_stops_publish_before_it_tags(self):
        self.merge_release("gitlab")
        status, _, err = self.main("publish")
        self.assertEqual(status, 1)
        self.assertIn("names the host 'gitlab'; the hosts are github, none", err)
        self.assertIsNone(release.tag_commit(self.root, "v0.2.0"))

    def test_publish_of_an_existing_tag_skips_what_is_done(self):
        run("git", "tag", "v0.1.0", cwd=self.root)
        run("git", "push", "-q", "origin", "v0.1.0", cwd=self.root)
        self.github.releases = ["v0.1.0"]
        sha = run("git", "rev-parse", "HEAD", cwd=self.root).strip()
        status, out, _ = self.main("publish")
        self.assertEqual(status, 0)
        self.assertEqual(out, f"v0.1.0 already exists at {sha[:12]}.\nPushed v0.1.0 to origin.\n"
                              f"The GitHub Release for v0.1.0 already exists.\nPublished v0.1.0.\n{SKIPPED}")
        self.assertEqual(self.github.created(), [])

    def test_a_failed_release_step_keeps_the_tag_and_a_rerun_finishes(self):
        merge = self.merge_release()
        self.github.failing = [["release", "create"]]
        status, out, err = self.main("publish")
        self.assertEqual(status, 1)
        self.assertNotIn("Published", out)
        self.assertIn("The github release step for v0.2.0 failed: HTTP 502: Bad Gateway", err)
        self.assertIn("run `just release publish` again", err)
        self.assertEqual(release.tag_commit(self.root, "v0.2.0"), merge)
        self.assertEqual(self.origin_tag("v0.2.0"), merge)
        self.github.failing = []
        status, out, err = self.main("publish")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, f"v0.2.0 already exists at {merge[:12]}.\nPushed v0.2.0 to origin.\n"
                              f"Created the GitHub Release for v0.2.0.\nPublished v0.2.0.\n{SKIPPED}")
        self.assertEqual(self.github.releases, ["v0.2.0"])

    def test_a_failed_push_keeps_the_tag_and_a_rerun_finishes(self):
        merge = self.merge_release()
        run("git", "remote", "set-url", "origin", str(self.root.parent / "missing.git"), cwd=self.root)
        status, _, err = self.main("publish")
        self.assertEqual(status, 1)
        self.assertIn("Pushing v0.2.0 to origin failed", err)
        self.assertEqual(release.tag_commit(self.root, "v0.2.0"), merge)
        self.assertEqual(self.github.calls, [])
        run("git", "remote", "set-url", "origin", str(self.origin), cwd=self.root)
        status, out, _ = self.main("publish")
        self.assertEqual(status, 0)
        self.assertIn("Published v0.2.0.", out)
        self.assertEqual(self.origin_tag("v0.2.0"), merge)
        self.assertEqual(len(self.github.created()), 1)

    def test_publish_with_a_public_remote_starts_the_public_publish_last(self):
        self.merge_release()
        run("git", "remote", "add", "public", str(self.root.parent / "public.git"), cwd=self.root)
        started = []

        def start(root, tag):
            self.assertEqual(self.github.releases, [tag])
            started.append((root, tag))
            return f"agent-desk-publish-{tag}"
        with mock.patch.object(release, "start_public_publish", start):
            status, out, err = self.main("publish")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(started, [(self.root, "v0.2.0")])
        self.assertTrue(out.endswith("Published v0.2.0.\nPublishing v0.2.0 to the public repository in "
                                     "agent-desk-publish-v0.2.0; follow it with "
                                     "`journalctl --user -u agent-desk-publish-v0.2.0 -f`.\n"), out)

    def test_a_public_publish_that_fails_to_start_keeps_the_earlier_steps(self):
        merge = self.merge_release()
        run("git", "remote", "add", "public", str(self.root.parent / "public.git"), cwd=self.root)
        failed = subprocess.CalledProcessError(1, ["systemd-run"], stderr="Unit agent-desk-publish-v0.2.0.service "
                                                                          "was already loaded.\n")
        with mock.patch.object(release, "start_public_publish", side_effect=failed):
            status, out, err = self.main("publish")
        self.assertEqual(status, 1)
        self.assertIn("Published v0.2.0.", out)
        self.assertIn("Starting the public publish of v0.2.0 failed: Unit agent-desk-publish-v0.2.0.service was "
                      "already loaded.\nThe tag stays; run `just release publish` again to finish.", err)
        self.assertEqual(self.origin_tag("v0.2.0"), merge)
        self.assertEqual(self.github.releases, ["v0.2.0"])

    def test_the_public_publish_runs_in_a_unit_of_its_own(self):
        with mock.patch.object(release.subprocess, "run") as started, \
                mock.patch.dict(os.environ, {"PATH": "/usr/bin", "SSH_AUTH_SOCK": "/run/agent"}):
            self.assertEqual(release.start_public_publish(self.root, "v0.2.0"), "agent-desk-publish-v0.2.0")
        args = started.call_args.args[0]
        self.assertEqual(args[:4], ["systemd-run", "--user", "--unit", "agent-desk-publish-v0.2.0"])
        self.assertIn("--collect", args)
        self.assertEqual(args[args.index("--working-directory") + 1], str(self.root))
        self.assertIn("PATH=/usr/bin", args)
        self.assertIn("SSH_AUTH_SOCK=/run/agent", args)
        self.assertEqual(args[args.index("--") + 1:],
                         [release.sys.executable, "-B", str(self.root / "scripts" / "publish.py"), "v0.2.0"])

    def assert_publish_refused(self, message):
        status, out, err = self.main("publish")
        self.assertEqual((status, out), (1, ""))
        self.assertIn(message, err)
        self.assertEqual(run("git", "tag", "--list", cwd=self.root), "")
        self.assertEqual(run("git", "tag", "--list", cwd=self.origin), "")
        self.assertEqual(self.github.calls, [])

    def test_publish_on_an_unmerged_release_branch_is_refused(self):
        self.assertEqual(self.main()[0], 0)
        run("git", "switch", "-q", "agent/release-0.2.0", cwd=self.root)
        self.assert_publish_refused("Publish from main; the checkout is on agent/release-0.2.0.")

    def test_publish_with_uncommitted_changes_is_refused(self):
        (self.root / "VERSION").write_text("0.2.0\n")
        self.assert_publish_refused("The checkout has uncommitted changes.")

    def test_publish_of_a_version_origins_main_lacks_is_refused(self):
        (self.root / "VERSION").write_text("0.2.0\n")
        run("git", "commit", "-q", "-am", "Unpushed", cwd=self.root)
        sha = run("git", "rev-parse", "HEAD", cwd=self.root).strip()
        self.assert_publish_refused(f"origin's main doesn't hold {sha[:12]}, which set VERSION to 0.2.0.")

    def test_publish_refuses_a_tag_that_names_another_commit(self):
        merge = self.merge_release()
        run("git", "tag", "v0.2.0", f"{merge}^1", cwd=self.root)
        status, out, err = self.main("publish")
        self.assertEqual((status, out), (1, ""))
        self.assertIn(f"v0.2.0 already exists at {run('git', 'rev-parse', f'{merge}^1', cwd=self.root)[:12]}, "
                      f"but VERSION became 0.2.0 at {merge[:12]}", err)
        self.assertEqual(self.origin_tag("v0.2.0"), "")
        self.assertEqual(self.github.calls, [])

    def test_a_gh_that_cannot_say_whether_the_release_exists_fails_the_step(self):
        merge = self.merge_release()
        self.github.failing = [["release", "view"]]
        status, out, err = self.main("publish")
        self.assertEqual(status, 1)
        self.assertNotIn("Published", out)
        self.assertIn("The github release step for v0.2.0 failed: HTTP 502: Bad Gateway", err)
        self.assertEqual(self.github.created(), [])
        self.assertEqual(self.origin_tag("v0.2.0"), merge)

    def test_a_bump_that_is_not_a_part_or_version_is_refused(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            release.main(["huge"], root=self.root, today=TODAY)
        self.assertEqual(stopped.exception.code, 2)

    def test_publish_has_no_dry_run(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            release.main(["--dry-run", "publish"], root=self.root, today=TODAY)
        self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
