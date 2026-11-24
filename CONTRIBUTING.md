# Contributing

agent-desk develops in a private repository, where agents work its issues through agent-desk itself and the maintainer
reviews and merges their work. The public repository receives each release as one snapshot commit, as
[Releasing](docs/reference.md#releasing) describes.

## Outside pull requests

Open pull requests against the public repository. The maintainer ports each accepted one into the private repository
by hand, and the change reaches the public repository with the next release's snapshot. Report a security problem
privately, as [SECURITY.md](SECURITY.md) describes, and not in a pull request.

## How a change reaches main

1. **Issue.** Each change starts as an issue in agent-desk's local store, with a Goal, a Done When list, and Notes.
   Agents file issues with the `/agent-desk:create-issue` skill, and the maintainer triages them.
2. **Agent run.** The maintainer labels the issue `agent-ready`. An agent works it in `../agent-desk-issue-<n>` on
   `agent/issue-<n>` with the `/agent-desk:work-issue` skill, and opens a draft review at its first commit.
3. **Check.** The desk's branch check runs `just test` on the branch merged with `main`. The agent marks the review
   ready once it passes.
4. **Review.** The maintainer reads the review and sends the agent back with comments, or presses Merge.
5. **Merge.** The merge queue tests the review merged with the newest `main` and lands it as a merge commit named
   `Merge review #<n> from <branch>`.

## Development setup

Clone the repository and run `./install.sh` from the clone, without the marketplace plugin. It links `agent-desk` on
PATH to the clone and adds the clone's `inbox` folder to `CLAUDE_CODE_PLUGIN_DIRS`, so the pane and the skills load
from the code you edit. That clone becomes live: the command, the systemd units, and the pane in every Claude Code
session run it. Make changes in a worktree beside it, such as one from `agent-desk worktree <slug>`, and keep the
clone on `main`.

Code from a worktree refuses to open the live store. Before you run `bin/agent-desk` from a worktree, set
`XDG_STATE_HOME` to a new temporary folder, so it works on a store of its own.

To see a change to the web page, run `just demo [port]`. It serves made-up cards across three demo projects, from a new
temporary folder with its own store and projects file, on port 7481 or the one you give. Take screenshots from the
demo or from agent-desk's own project, never from a copy of the live store, which holds every project's issues.

## Tests

Run every suite from a checkout:

```bash
just test
```

The `test` recipe runs the Python tests in `tests/` through `python3 -B scripts/run_tests.py`, the pane's tests
through `claude plugin test inbox`, and the web page's through `node --test web/*.test.ts`. It then runs
`just shellcheck`, which checks `bin/agent-desk` and `lib/*.sh` with `shellcheck`, and `just gitleaks`, which fails on
a secret that `gitleaks git` finds in the history or `gitleaks dir` finds in the tree, printing each finding with its
value redacted. You need `shellcheck` installed. Where `gitleaks` isn't installed, `just gitleaks` says so and skips
the scan. `.gitleaks.toml` and `.gitleaksignore`, when they exist, allowlist false positives.

`scripts/run_tests.py` splits the Python tests into one part for each test class, with a large class split further,
and runs the parts in worker processes, as many as the CPU count up to 8, or `--jobs N`. A passing module prints one
line; a failing part prints unittest's whole output. The run ends with the count of tests run and the wall time. Each
test runs in its own process beside others, so give it its own temporary folder and port instead of a fixed path under
`/tmp` or a fixed port. Run one or more modules, or one test, by hand:

```bash
python3 -B scripts/run_tests.py test_store test_board
python3 -B -m unittest discover -s tests -p test_store.py
python3 -B -m unittest discover -s tests -p test_serial.py -k test_a_closed_issue_stays_closed
```

Add tests beside the ones for the module you change:

- `tests/test_<module>.py` for the scripts in `lib/`.
- The pane's tests in `inbox/hooks/`: `inbox.test.tsx` and `buttons.test.tsx`, which share the fixtures in
  `pane.fixtures.ts`, and the board's logic in `board.test.ts`, `queued.test.ts`, `moves.test.ts`, `actions.test.ts`,
  `notes.test.ts`, `units.test.ts`, and `load.test.ts`, which share the store and host in `board.fixtures.ts`.
- `web/<module>.test.ts` for the web page and its server.

The desk's branch checks run `just test` on every `agent/*` branch, as `.agent-desk.json`'s `test` sets it. The
public repository runs the same suites on GitHub-hosted runners for every pull request and every push to `main`,
through `public/.github/workflows/test.yml`, which each release's snapshot carries there along with the rest of
`public/`. The private repository's `test-hosted` workflow runs those steps on a GitHub-hosted runner only when the
public publish starts it at a release's tag, or from its Run workflow button, since hosted minutes on a private
repository count against a monthly allowance. It first swaps the checkout for the tree the release publishes, so it
tests what the public repository receives. The hosted workflows leave gitleaks out, so the secret scan runs in
`just test` and the branch checks. `tests/test_ci.py` checks that the two workflows run the same steps apart from that
swap and that `just test` runs their `shellcheck` command and the gitleaks scan, so change their steps together.

## Conventions

- **Script shape.** Every executable script in `lib/` and `bin/agent-desk` keeps its work in a `<name>_main`
  function called as `<name>_main "$@"; exit`, so a running job never reads a file replaced under it.
- **Changelog.** A pull request that changes a command, the pane, the web page, the config, or a skill's behavior
  adds a file in `changelog.d/` named after its branch slug, such as `changelog.d/issue-52.md` for `agent/issue-52`, in
  the format [`changelog.d/README.md`](changelog.d/README.md) describes. Mark an item that breaks a command, config
  key, or label a user relies on with `**Breaking**:`. A pull request that changes only tests, refactors, or docs adds
  no file. The release folds the files into `CHANGELOG.md`, so leave that file alone.
- **Docs.** [`README.md`](README.md) takes a new user from install to a first review.
  [`docs/reference.md`](docs/reference.md) describes how each part works; update the statement your change makes
  wrong. Each skill's `SKILL.md` under `inbox/skills/` is its own spec. Write in the present tense, with concrete
  facts and active verbs.
- **Commits.** Write the subject as one imperative sentence that names the change, capitalized, with no type prefix
  and no trailing period.
