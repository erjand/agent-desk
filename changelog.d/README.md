# Pending changelog entries

Each PR that changes what an agent-desk user sees or does adds one file here with its changelog entries. The next
release folds the files into `CHANGELOG.md` and deletes them, so PRs never edit the same `[Unreleased]` section.

## Name

Name the file after the PR's branch slug, the part after `agent/`: `issue-52.md` for `agent/issue-52`. The slug is
known before the PR has a number. A branch outside `agent/` uses its last path segment, such as `fix-labels.md` for
`erjand/fix-labels`. Names use lowercase letters, digits, dots, and hyphens, and end in `.md`.

## Contents

A file holds one or more of the six [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) sections, each at most
once, with list items under each:

```markdown
### Added

- `agent-desk doctor` checks that each runner's service can reach GitHub. (#52)

### Changed

- **Breaking**: **Workflows**: The agent workflow reads its time limit from `.agent-desk.json` instead of the
  `AGENT_DESK_TIMEOUT` variable. (#52)
```

- The headings are `### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, and `### Security`.
  Nothing else goes in the file: no title, no prose between sections, and no other heading levels.
- Each item starts with `- ` and reads as the released changelog would print it, ending with the issue number in
  parentheses as `CHANGELOG.md` does. An item wraps onto lines indented by two spaces.
- An item that changed the workflow templates in `workflows/` starts with `**Workflows**:`. An item that breaks
  something a user relies on, such as a command, a config key, or a label, starts with `**Breaking**:`. An item with
  both puts `**Breaking**:` first. The markers appear nowhere else, and a release reads them to pick its version.

`tests/test_changelog_d.py` checks every file here apart from this README.
