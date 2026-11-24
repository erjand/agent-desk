"""Tests for the pending changelog entries in changelog.d/, whose format changelog.d/README.md describes."""

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
MARKER = "**Breaking**:"
# Released items in CHANGELOG.md carry this marker, which named the workflow templates agent-desk no longer has.
RETIRED = "**Workflows**"
NAME = re.compile(r"^[a-z0-9][a-z0-9.-]*\.md$")
LEADING_MARKER = re.compile(r"^\*\*Breaking\*\*:\s*")


def stray_markers(text, where):
    """A problem for each marker in `text`, which holds an item's text after its leading marker."""
    found = []
    if MARKER.rstrip(":") in text:
        found.append(f"{where}: {MARKER} belongs only at an item's start")
    if RETIRED in text:
        found.append(f"{where}: {RETIRED} is retired, since agent-desk has no workflow templates; mark a breaking "
                     f"change with {MARKER} alone")
    return found


def problems(name, text):
    """Every way the file `name` with contents `text` breaks the changelog.d format."""
    found = []
    if not NAME.match(name):
        found.append(f"{name}: name it after the branch slug in lowercase, such as issue-52.md")
    seen = []
    items = {}
    in_item = False
    for number, line in enumerate(text.splitlines(), 1):
        where = f"{name}:{number}"
        if not line.strip():
            in_item = False
            continue
        heading = re.match(r"^### (\S.*)$", line)
        if heading:
            section = heading.group(1).strip()
            if section not in SECTIONS:
                found.append(f"{where}: `### {section}` is not one of the six sections: {', '.join(SECTIONS)}")
            elif section in seen:
                found.append(f"{where}: `### {section}` appears twice")
            seen.append(section)
            items[len(seen)] = 0
            in_item = False
        elif line.startswith("- "):
            if not seen:
                found.append(f"{where}: the item comes before any section heading")
            else:
                items[len(seen)] += 1
            in_item = True
            rest = LEADING_MARKER.sub("", line[2:])
            if not rest.strip():
                found.append(f"{where}: the item has no text")
            found.extend(stray_markers(rest, where))
        elif in_item and re.match(r"^  \S", line):
            found.extend(stray_markers(line, where))
        else:
            found.append(f"{where}: only `### Section` headings, `- ` items, and items' wrapped lines indented by two "
                         "spaces belong here")
    if not seen:
        found.append(f"{name}: the file has no section heading")
    for index, section in enumerate(seen, 1):
        if not items[index]:
            found.append(f"{name}: `### {section}` has no items")
    return found


def folder_problems(folder):
    """Problems with every file in `folder` apart from its README."""
    found = []
    for path in sorted(Path(folder).iterdir()):
        if path.name == "README.md":
            continue
        if not path.is_file():
            found.append(f"{path.name}: only entry files belong in changelog.d")
            continue
        found.extend(problems(path.name, path.read_text()))
    return found


GOOD = """### Added

- `agent-desk doctor` checks each runner's service. (#52)
- **Breaking**: `agent-desk doctor` names its version in a summary, which wraps
  onto a second line. (#52)

### Fixed

- **Breaking**: `agent-desk install` drops the old `AGENT_DESK_TIMEOUT` variable. (#52)
"""


class ChangelogFolderTest(unittest.TestCase):
    def test_every_pending_entry_follows_the_format(self):
        self.assertEqual(folder_problems(ROOT / "changelog.d"), [])

    def test_a_malformed_file_in_a_folder_fails_the_check(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "README.md").write_text("# Anything goes in the README\n")
            Path(folder, "issue-52.md").write_text(GOOD)
            self.assertEqual(folder_problems(folder), [])
            Path(folder, "issue-53.md").write_text("### Improved\n\n- A change. (#53)\n")
            self.assertEqual(folder_problems(folder),
                             ["issue-53.md:1: `### Improved` is not one of the six sections: Added, Changed, "
                              "Deprecated, Removed, Fixed, Security"])


class ProblemsTest(unittest.TestCase):
    def assertProblem(self, text, fragment, name="issue-52.md"):
        found = problems(name, text)
        self.assertTrue(any(fragment in problem for problem in found), found)

    def test_a_file_with_every_section_and_the_marker_passes(self):
        text = "".join(f"### {section}\n\n- **Breaking**: A {section.lower()} change. (#52)\n\n"
                       for section in SECTIONS)
        self.assertEqual(problems("issue-52.md", text), [])
        self.assertEqual(problems("chore-tidy-2026.11.11.md", GOOD), [])

    def test_the_name_is_a_lowercase_slug_ending_in_md(self):
        self.assertProblem(GOOD, "branch slug", name="Issue-52.md")
        self.assertProblem(GOOD, "branch slug", name="issue-52.txt")
        self.assertProblem(GOOD, "branch slug", name="agent/issue-52.md")

    def test_only_the_six_sections_count_as_headings(self):
        self.assertProblem("### Improved\n\n- A change. (#52)\n", "not one of the six sections")
        self.assertProblem("## Added\n\n- A change. (#52)\n", "belong here")
        self.assertProblem("# Issue 52\n\n### Added\n\n- A change. (#52)\n", "belong here")

    def test_a_section_appears_once_and_holds_items(self):
        self.assertProblem("### Added\n\n- One. (#52)\n\n### Added\n\n- Two. (#52)\n", "appears twice")
        self.assertProblem("### Added\n\n### Fixed\n\n- A fix. (#52)\n", "`### Added` has no items")
        self.assertProblem("", "no section heading")

    def test_prose_and_stray_items_fail(self):
        self.assertProblem("This PR adds a check.\n\n### Added\n\n- A check. (#52)\n", "belong here")
        self.assertProblem("- A check. (#52)\n", "before any section heading")
        self.assertProblem("### Added\n\n* A check. (#52)\n", "belong here")
        self.assertProblem("### Added\n\n- A check\n\n  that wraps after a blank line. (#52)\n", "belong here")
        self.assertProblem("### Added\n\n-  \n", "no text")

    def test_the_marker_belongs_only_at_an_items_start(self):
        self.assertProblem("### Changed\n\n- The pane changed. **Breaking**: too. (#52)\n", "only at an item's start")
        self.assertProblem("### Changed\n\n- The pane changed, and\n  **Breaking**: so did a label. (#52)\n",
                           "only at an item's start")
        self.assertProblem("### Changed\n\n- **Breaking**:\n", "no text")

    def test_the_retired_workflows_marker_fails_anywhere(self):
        self.assertProblem("### Changed\n\n- **Workflows**: A job changed. (#52)\n", "is retired")
        self.assertProblem("### Changed\n\n- **Breaking**: **Workflows**: A label changed. (#52)\n", "is retired")
        self.assertProblem("### Changed\n\n- A label changed, and\n  **Workflows**: a job. (#52)\n", "is retired")


if __name__ == "__main__":
    unittest.main()
