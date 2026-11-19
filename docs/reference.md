# agent-desk reference

This document describes how each part of agent-desk works, for when you need more than the
[README](../README.md) gives. [CONTRIBUTING.md](../CONTRIBUTING.md) covers the tests and how a change lands, and
[SECURITY.md](../SECURITY.md) covers who can start a run and what an agent can reach.

## Contents

- [Commands](#commands)
- [The config](#the-config)
- [Installing and upgrading](#installing-and-upgrading)
- [Issue lifecycle](#issue-lifecycle)
- [Auto, Manual, and Hold](#auto-manual-and-hold), with [the weekly usage limit](#the-weekly-usage-limit)
- [Merge queue](#merge-queue), with [pushing main](#pushing-main)
- [Conflict fixes](#conflict-fixes)
- [Chores](#chores) and [chore history](#chore-history)
- [Memory](#memory) and [GPU slots](#gpu-slots)
- [The pane](#the-pane)
- [The web page](#the-web-page)
- [The local store](#the-local-store), with [importing from GitHub](#importing-from-github), [cutover](#cutover),
  [renaming a project](#renaming-a-project), and [the board's read](#the-boards-read)
- [The dispatcher](#the-dispatcher), with [discussion runs](#discussion-runs), [parent checks](#parent-checks), and
  [branch checks](#branch-checks)
- [Backup and restore](#backup-and-restore)
- [Releasing](#releasing)


## Commands

`agent-desk <command>`, run from a checkout of the project. The runs and the pane run `agent-desk` from PATH,
which `./install.sh` links to this checkout's `bin/agent-desk` (see [Install](../README.md#install)):

| Command | What it does |
| --- | --- |
| `--version` | Prints the version in the checkout's `VERSION` file |
| `install [<owner/repo>]` | Writes a starter config, records the main checkout in `~/.config/agent-desk/projects`, and registers the project in the local store, under the name given when the config's `host` is `none` |
| `doctor` | Checks the machine and the current project for what agent-desk needs, says how to fix each problem, and exits non-zero when a check fails |
| `work-issue <n>` | Works issue n with a headless agent, as the dispatcher does |
| `discuss-idea <n>` | Has a headless agent answer the newest comment on idea n from a throwaway worktree of `main`, changing no code, as the dispatcher's discussion run does |
| `work-pr <pr>` | Sends a headless agent back to a review of an `agent/<slug>` branch other than an issue's, with the request in `REQUEST`, as the dispatcher does |
| `start <n>` | Claims issue n for an interactive session, creates or continues its worktree, and posts the session for the pane |
| `serial claim <n>`, `serial release` | Takes or hands on the slot that `serial` issues share |
| `worktree <slug> [base]` | Creates `../<repo>-<slug>` on `agent/<slug>` from the local `<base>` branch, or `main`, seeded and set up |
| `clean <agent/slug> <merged>` | Cleans up after an agent review merges or closes, as the dispatcher does |
| `release <n> <cancelled\|failure> [--discussion]` | Hands issue n back after its run ended without a report, as the dispatcher's exit handler does; with `--discussion`, for a discussion of an idea, only says how the run ended |
| `chores [status]`, `chores tick` | Shows the project's chores, or starts the most overdue one |
| `chores on`, `chores off`, `chores tick-all`, `chores merges-all` | Turns the chore and merge timers on or off, or ticks every project's chores, or its merge and push queues |
| `chores rename <old> <new> [-R <owner/repo> \| --all]` | Moves a chore's runs and ledger to a new skill name and renames it in `.agent-desk.json` through the schedule review, printing the review's number, in the current project, the one named, or every project whose config names it, as [Chores](#chores) describes |
| `fixes [status]`, `fixes tick` | Shows the reviews that conflict with main and what each fix waits on, or asks for the fixes due |
| `merges [status]`, `merges add <n>...`, `merges remove <n>...`, `merges tick` | Shows, adds to, or takes reviews out of the merge queue, or moves it on and pushes main |
| `pushes [status]`, `pushes tick` | Shows how far `main` is ahead of `origin`, or pushes `main` and the agent branches that moved |
| `fix <n>` | Merges its base into a review and has a headless agent resolve the conflicts, as a `fix` run does |
| `chore <skill> [n \| forever \| stop]` | Asks the dispatcher for a chore run now; with `n` or `forever` it runs again each time a run ends, until a run fails or `stop` |
| `auto`, `manual`, `hold` [`<owner/repo>`] | Starts every new run, starts agent, fix, and chore runs only when you press Start, or starts no new agent or chore run, in every project or in the one named |
| `unpick <n> [<owner/repo>]`, `unpick --all [<owner/repo>]` | Drops issue n's Manual pick, in the current project or the one named, so its run waits for Start again, and exits 1 when the issue has no pick; with `--all`, drops every project's picks, or the one named, as [Auto, Manual, and Hold](#auto-manual-and-hold) describes |
| `pause <n>`, `unpause <n>` [`-R <owner/repo>`] | Stops the agent run on issue n and puts the issue on hold, or queues an issue on hold again at the top of the start order, as [the dispatcher](#the-dispatcher) describes |
| `resume` | Goes back to Auto or Manual, whichever the desk was in before Hold |
| `slots [<owner/repo>] [<kind>=<n>... \| --reset [<kind>...]]` | Prints each project's [slots](#the-config), or the one named, with each kind's count, its config's, and whether an override sets it; with `<kind>=<n>`, overrides the current project's or the named one's slots of each kind; with `--reset`, drops its overrides, or those of the kinds named |
| `usage-limit [<0-100> \| on \| off]` | Shows the [weekly usage limit](#the-weekly-usage-limit) and the last reading of the plan's usage, sets the limit's percentage and turns it on, or turns it on or off, for every project on the machine |
| `shots [--comment] <review> <picture>...` | Saves screenshots for a review in the state folder and prints the Markdown that shows them on the web page; with `--comment`, adds it to the review as a comment |
| `gpu [--exclusive] -- <command> [args...]` | Runs a command while it holds one of the machine's [GPU slots](#gpu-slots), or with `--exclusive` all of them, and exits with its status |
| `gpu slots [<n>] [--min-free-vram <GB>]` | Prints the machine's [GPU slot](#gpu-slots) count, the free VRAM a slot past the first needs, and who holds each slot, or sets the count or the VRAM for every later `gpu` call |
| `gpu status` | Prints what `gpu slots` prints, then the last day's waits for a GPU slot by checkout |
| `issue create`, `view`, `list`, `comment`, `edit`, `close`, `reopen`, `delete` | Files and changes issues in the [local store](#the-local-store), with the flags `gh issue` takes; `comment <n> --edit <comment> --body-file <file> [--note <text>]` replaces the body of a comment you wrote, by the id `view` prints, adding the note on what changed, which an agent's edit needs; `delete <n>` prints what [deleting](#the-local-store) an issue filed by mistake removes, and `delete <n> --yes` deletes it |
| `review create`, `view`, `list`, `comment`, `edit`, `checks`, `ready`, `close`, `reopen`, `merge` | Opens and changes reviews of a branch in the local store, with the flags `gh pr` takes; `comment <n> --edit <comment>` edits a comment you wrote, as on `issue`; `checks --watch` waits for the local checks of the branch's newest commit |
| `label edit <name> --color <hex>`, `label list` | Gives a label in the local store the color the pane and the page draw it in, or lists the colors; `label create` is the same as `edit`, as `gh label create` names it |
| `board [--json]` | Prints what the inbox's board shows of the project without asking GitHub: its open issues and reviews, or with `--json` everything the pane and the page build their rows from |
| `cutover [--dry-run] [--issues-off] <owner/repo>`, `cutover --all` | Makes the local store the tracker of a project, or of every project, imports it one last time, and disables its workflows and closes its PRs on GitHub, leaving its Issues on unless you pass `--issues-off`; see [Cutover](#cutover) |
| `cutover --undo [--dry-run] <owner/repo>` | Turns a project's Issues, workflows, and PRs back on, and lists what was filed in the store since its cutover |
| `project rename [--dry-run] <owner/repo> <new owner/repo>` | Moves a project held on Hold to a new name in the store, the state folder, the projects file, its checkout's `origin`, and the backup repository; see [Renaming a project](#renaming-a-project) |
| `project remove [--dry-run] <owner/repo>` | Takes a project held on Hold off this machine's desk: its rows in the store, its files in the state folder, its mode, its line in the projects file, and its backup folder, leaving its checkout and worktrees; see [Removing a project](#removing-a-project) |
| `import <owner/repo>` | Copies a GitHub project's issues, PRs, comments, labels and their colors, sub-issues, and blockers into the [local store](#the-local-store) with their numbers, and on a rerun copies only what changed |
| `backup`, `backup snapshot`, `backup install` | Commits every item in the local store to the [backup repository](#backup-and-restore), copies the database to a dated snapshot, or turns on the timer that does both |
| `restore --from <repository \| snapshot> --to <state folder>` | Rebuilds the store's database from the backup repository or a snapshot, in a folder that holds none |
| `dispatch [install \| off \| status \| serve]` | Turns on or off the [dispatcher](#the-dispatcher), the systemd user service that starts requested runs as their own units in `agentdesk.slice`, or runs it in the terminal |
| `request <agent\|chore\|fix> <target> [--benchmark]` | Asks the dispatcher for a run of issue n, PR `pr-<n>`, a discussion of idea `idea-<n>`, a fix of a PR, or a chore skill, and prints its id |
| `web [on \| off \| status \| run [<owner/repo>...]]` | Serves every registered project's inbox to a browser, as a systemd user service or in the terminal, where `run` can name the projects to serve |

## The config

`.agent-desk.json` in the main checkout:

```json
{
  "title": "Lantern Game",
  "host": "github",
  "test": "just test",
  "testMinutes": 30,
  "limitMinutes": { "agent": 240, "benchmark": 360 },
  "env": { "GODOT": "~/bin/godot" },
  "worktree": {
    "seeds": [".godot/imported", "art/audio"],
    "setup": "just build && { just import || just import; }"
  },
  "sync": { "run": "just build && just import >/dev/null 2>&1", "skipWhile": ["godot"] },
  "chores": {
    "every": { "audit": 3, "add-tests": 3 },
    "gapHours": 18,
    "maxAuditIssues": 5,
    "minFreeGB": 12
  },
  "resources": {
    "reserveGB": 8,
    "needGB": { "agent": 6, "chore": 3, "fix": 3 },
    "labelGB": { "assets": 14 },
    "maxMemoryPressure": 5,
    "fixMemoryPressure": 15,
    "maxWaitMinutes": 120,
    "shrinkClaims": true
  },
  "presence": "desktop"
}
```

- `title` names the project in the pane's `/agent-desk` command; the folder's name stands in without it.
- `host` names where the project's releases publish: `github`, which `agent-desk install` writes into a new config, or
  `none` for a project without a remote host. agent-desk's own `just release publish` reads it, as
  [Releasing](#releasing) describes, and takes `github` when the key is missing. A project whose `host` is `none` may
  have an origin that is a path on disk, or no remote, and `agent-desk install <owner>/<repo>` names it.
- `test` is the command that tests a branch, such as `just test`, which the dispatcher's
  [branch checks](#branch-checks) run on each `agent/*` branch's newest head. `agent-desk install` writes `""`, which
  turns the checks off, so the project's reviews merge untested, and `agent-desk doctor` warns. It runs with `bash -c`,
  so one line can chain steps with `&&`.
- `testMinutes` is a check's time limit in whole minutes, by default 15. It covers the whole check: copying
  `worktree.seeds`, `worktree.setup`, and `test`.
- `limitMinutes` sets the time limit of each kind of run in whole minutes, the `RuntimeMaxSec` of its unit. Its keys
  are `agent`, by default 180, `benchmark`, by default 300, for an agent run of an issue labelled `benchmark`, `chore`,
  by default 120, and `fix`, by default 90. A key left out keeps its default. A discussion of an idea keeps its own 30
  minutes.
- `env` maps environment variables the project's builds need to their values, with `~/` for the home folder. The
  dispatcher gives them to each run and branch check of the project. `agent-desk doctor` fails a config that sets
  `runners`, and says to move `runners.env` here.
- `slots` caps how many runs of each kind the [dispatcher](#the-dispatcher) runs at once in the project, such as
  `{"agent": 3, "chore": 1, "fix": 1, "check": 2}`, which are also the defaults. `benchmark`, by default 2, caps how
  many of the agent runs may be on `benchmark` issues. A count of 0 starts none of that kind.
  `agent-desk slots <owner/repo> agent=1` overrides a kind's count on this machine without editing the config, above
  or below it, and `lib/hold.py` keeps the override in `hold.json` under `slots`, where mode changes and Resume leave
  it. The dispatcher, the [branch checks](#branch-checks), and the board read the override at the next pass. A lower
  count stops no run: the runs past it keep working, and the kind starts none until its busy count drops below the
  count. A count equal to the config's drops that kind's override, and `agent-desk slots --reset` drops them all. The
  memory gate still holds a run the machine lacks memory for.
- `worktree.seeds` lists paths copied from the main checkout into each new worktree, such as caches and generated or
  downloaded folders git ignores. `worktree.setup` runs in the new worktree.
- `sync.run` runs in the main checkout after the merge queue fast-forwards it, unless a program in `sync.skipWhile`
  runs there.
- `chores.every` maps each chore skill, one you install as [Chores](#chores) describes, to the days between its runs. At most one chore starts in `gapHours`, a
  chore that files issues waits while `maxAuditIssues` of them are open, and no chore starts while the machine has less
  than `minFreeGB` of memory available, by default 18.75% of the machine's memory, as [Memory](#memory) describes.
- `chores.issueLabels` maps each chore that files issues instead of opening a review to the label its issues carry,
  which the timer counts against `maxAuditIssues`. The defaults map `audit` to `audit` and `security-audit` to
  `security`, and the map adds to them or changes them, such as `{"triage": "bug"}`. A chore mapped to `""` drops its
  default and waits on its open review like any other chore. `agent-desk doctor` reports a value that isn't a string.
- `chores.paused` lists chore skills from `chores.every` that the timer never starts, such as
  `["add-tests"]`. A paused chore keeps its days and its row in `agent-desk chores status`, the pane,
  and the web page, with `paused` as its next run, and `agent-desk chore <skill>` or the Run button still starts it.
  Take it off the list to resume it. A name missing from `chores.every` is ignored. The web page's Edit, Pause, and
  Resume buttons change `chores.every` and `chores.paused` through a PR.
- `fixes` tunes the conflict fixes: `auto` (default `true`) lets the inbox and the timer start them, and `maxTries`
  (default 2) is how many fixes one PR head gets.
- `resources` keeps agent, chore, and fix runs from piling onto a machine short of memory, as [Memory](#memory)
  describes. Each key falls back to the default shown, except `reserveGB`, which defaults to 12.5% of the machine's
  memory. `maxMemoryPressure` is the memory pressure your apps feel, in percent, above which no run starts, read from
  the `app.slice` and `session.slice` cgroups as the **Pressure** check describes. `fixMemoryPressure`, by default
  15, is the higher limit for a conflict fix, which the merge queue waits on. `shrinkClaims`, by default `true`,
  lets a run's claim shrink once the run is past its settle time; `false` keeps every claim in full for the run's
  whole life. `agent-desk doctor` reports a `shrinkClaims` that isn't `true` or `false`.
- `presence` says when chores and conflict fixes may start. `"desktop"`, the default, starts them only while your
  desktop session is unlocked, so they never start on a machine without a Wayland or X11 session.
  `"always"` starts them whether or not anyone is at the machine, as on a headless server. The chore timer is a
  systemd user timer, so on a machine you don't stay logged in to it runs only with lingering enabled:
  `sudo loginctl enable-linger $USER`. `agent-desk doctor` warns when it isn't.
- `models` picks the Claude model and effort each run uses, through `lib/models.py`. A run starts from its kind's
  setting, `agent`, `chore`, or `fix`; a chore's own entry under `chores`, by its skill's name, overrides it, and so
  does each of an agent issue's labels under `labels`. Efforts are `low`, `medium`, `high`, `xhigh`, and `max`. agent-desk's own defaults run agents on Opus at high effort, chores on Opus at medium, the
  `trim-comments` chore on Sonnet, `add-tests` on Opus, the `audit` on Opus at high effort, and fixes on Sonnet at
  medium. The three chore defaults apply only to a project that installs and lists those chores, as [Chores](#chores)
  describes. Whatever stays unset falls back to your own Claude Code settings. Branch checks run the `test` command
  and never start Claude. For example:

  ```json
  "models": {
    "agent": { "model": "opus" },
    "chores": { "add-tests": { "effort": "high" } },
    "labels": { "needs-design": { "effort": "max" } }
  }
  ```

## Installing and upgrading

agent-desk comes in two parts. The Claude Code plugin in `inbox/` holds the pane and the `/agent-desk:*` skills. The
`agent-desk` command, the scripts in `lib/` it runs, and the web page come from a clone of this repository, since the
systemd user units need a fixed path to them, and Claude Code's plugin cache keeps each plugin version in a folder of
its own. The README's [Install](../README.md#install) gives the commands.

The repository's `.claude-plugin/marketplace.json` lists the `agent-desk` plugin with its source at `./inbox`, and the
plugin's `inbox/.claude-plugin/plugin.json` carries the release's version. Keep the plugin and the clone at the same
release: `agent-desk doctor` warns when the plugin's version differs from the clone's `VERSION`.

The agents commit their work with the account's git identity. `agent-desk doctor` reads `user.name` and `user.email`
outside any repository, from the account's and the system's git config, and fails in its Machine section when
either is empty, with the `git config --global` commands that set them. An identity that only one project's
`.git/config` sets doesn't count, since the check runs before doctor knows the project.

`./install.sh` links `~/.local/bin/agent-desk` to the checkout's `bin/agent-desk`, moving anything else there into
`~/.local/state/agent-desk/backups/`, and says so when `~/.local/bin` is missing from PATH. When `~/.claude/settings.json`
enables the marketplace plugin, it leaves `CLAUDE_CODE_PLUGIN_DIRS` alone. Otherwise it adds the checkout's `inbox`
folder to `CLAUDE_CODE_PLUGIN_DIRS` there, which loads the pane and the `/agent-desk:*` skills in every Claude Code
session, as a development clone needs. It edits the file as JSON through any symlink, leaves its other settings alone,
and puts the folder in place of an entry naming another checkout's `inbox`, since two copies of the plugin can't load
together. With the marketplace plugin enabled too, it says so and names the entry to remove, or the plugin to
uninstall. When the systemd user units that `agent-desk chores on` and `agent-desk web on` write exist, it points them
at the checkout, keeping their port and whether they are on, and restarts the web page if it runs. Run it again after
moving the checkout; a run with nothing to change changes nothing.

The dispatcher and the chore timer find the main checkout through `~/.config/agent-desk/projects`, so moving a
checkout means running `install` again from its new place. `install` warns when the `agent-desk` on PATH runs another
checkout than the one `install` came from, and when the dispatcher isn't running.

agent-desk numbers its releases in `VERSION`, which `agent-desk --version` prints and the plugin's
`inbox/.claude-plugin/plugin.json` matches. `CHANGELOG.md` lists what each release changed, and marks the entries that
break a command, config key, or label you rely on with **Breaking**. Changes merged since the last release wait in
`changelog.d/`, one file per pull request, until the next release folds them into `CHANGELOG.md`. The README's
[Upgrade](../README.md#upgrade) gives the steps.

The timers and the runs they start run the checkout itself, so a pull reaches them at once. The dispatcher and the
web page's server load their code when they start. When the [merge queue](#merge-queue) lands a change to that code in
agent-desk's own checkout, it restarts each service that runs. After a pull, restart them yourself with
`agent-desk dispatch install` and `agent-desk web on`. Stop a server started with `agent-desk web run` and start it
again; its page's header says when it runs older code than the checkout holds. A Claude Code session keeps the pane's
code it opened with, so open a new session to load a new pane. The store migrates its database to the new schema on
the first command from this checkout that opens it.

A desk set up when agent-desk worked through GitHub needs two more steps. Remove `runners` from each project's
`.agent-desk.json`, and move its `runners.env` to a top-level `env`: `agent-desk doctor` fails the project until you
do, and the runs go without those variables. Then delete the GitHub API budget queue, the GitHub response cache, and
the failure write-ups' ledger, which `agent-desk doctor` lists with the `rm -r` command that deletes them.

## Issue lifecycle

1. You label an issue `agent-ready`, and the [dispatcher](#the-dispatcher) on this machine starts
   `agent-desk work-issue <n>` as a systemd unit once the desk's mode lets it, no open issue blocks the issue, no
   other issue holds its serial slot, the project has a free agent slot, and the machine has the memory. A request
   that waits on these takes no slot. Labelling an issue `agent-ready` from any place starts its run: the
   Agent-ready button asks for it, and for a label added any other way, such as `agent-desk issue edit <n>
   --add-label agent-ready`, the dispatcher sees the issue move to `queued` and asks for the run itself. Either way,
   one label starts one run. The issue waits in Queued, as `queued`, or as `waiting` while a blocker or its serial
   slot holds it, until its run starts. Until then, Move to Open on its row sends it back: it cancels the requests
   that wait for the issue, sets it `open`, and drops its Manual pick, so nothing starts it until you label it again.
   The project's queued and waiting issues keep one start order: an issue joins its end when it enters Queued, and
   when a free agent slot or serial slot can go to several, the dispatcher gives it to the first in that order whose
   gates are clear. Move Up, Move Down, and Move to Top on its row, dragging its card on the web page, or
   `agent-desk queue order <n> --up | --down | --top | --to <place>`, change its place, and `agent-desk queue order`
   prints the order.
2. The run moves the issue to `in-progress` in the local store, and the agent works in `../<repo>-issue-<n>` on `agent/issue-<n>` with
   the `/agent-desk:work-issue` skill and opens a review whose description starts with a plain-language summary of
   what it changes for you and why. `agent-desk work-issue` reads the issue, its labels, its comments, and its review
   from the [local store](#the-local-store), and writes its notes and state changes there: a note when the agent
   starts, with the command that resumes its session, and one when it finishes, which names the review on both the
   issue and the review.
3. A comment on the review that mentions `@agent` sends the agent back to the same branch, and the run's notes go on
   the review as well as the issue.
4. When the review merges or closes, the dispatcher sees its event in the store and runs `agent-desk clean` in a
   unit of its own. The cleanup stops the issue's
   unfinished agent runs, each after a note on the issue that the agent stopped, and removes the worktree and branch.
   It keeps a branch whose commits neither `origin` nor the review's base holds, such as one never pushed, since it
   holds the only copy of the work.
   A merge into `main` closes the issue with a "Landed on main" note and closes the issues of reviews stacked on it;
   the [merge queue](#merge-queue) moved the main checkout and ran the project's sync command when it merged the
   review. Any other merge or close moves the issue
   from `in-progress` back to `open`, except a merge into another agent branch, which keeps it in progress.

Chores, conflict fixes, and discussions of ideas start the same way, so every agent, chore, fix, and discussion run
is a unit on this machine. A run's notes name its unit, and `journalctl --user -u <unit>` prints its log. Turn the
dispatcher on with `agent-desk dispatch install`.

An issue labelled `base:<branch>`, such as `base:local-desk`, works from that branch in place of `main`: its worktree
branches from the main checkout's local `<branch>`, a later run merges the local `<branch>` into it, its PR targets the
branch, and the merge queue and conflict fixes merge into it. Runs read only local branches and fetch nothing, so they
start with origin out of reach. Use the label to gather a larger change on a branch before it lands on `main`. Create
the label in the project when its first issue needs it, since `agent-desk install` creates none. A base the main
checkout has no local branch for, or two base labels, stop the run with a comment that names the problem. A review closes the issues it fixes
only when it merges into `main`, so an issue whose review merges into another base stays open, moved back to `open`,
until you close it, as when the base branch lands on `main`.

An agent that needs a decision labels the issue `needs-design` and asks on the issue, under a `## Decisions Needed`
heading that the `/agent-desk:work-issue` and `/agent-desk:create-issue` skills also put on any issue, PR, or comment
awaiting your answer. Each decision is a numbered question heading, such as `### 1. Should the banner pause combat?`,
over lettered options with the agent's pick marked ✅, so you can answer with a short reply such as `1B`, `1: B`, or
`1 B, 2 A`, or in words. The agent records each answer in the issue's Notes and removes that decision, keeping the
others' numbers. Once you settle them, Designed on
the issue's Needs Design card, in the pane or on the page, takes the label off and moves the issue to Open. A run
that claims the issue also takes `needs-design` off, so answering with an `@agent` comment, or labelling the issue
`agent-ready` without pressing Designed, lets the run end like any other. Labelling a parent issue that has
open sub-issues starts no agent: agent-desk moves it back to Open and lists the open sub-issues to label instead.
Labelling one whose sub-issues have all closed starts a [parent check](#parent-checks) in place of a full run: an
agent confirms the parent's Done When against its closed sub-issues and closes it, or asks what is left under
`## Decisions Needed` and moves it to `needs-design`. An `@agent` comment on a parent still starts a full run, to
change the split. Issues
labelled `serial` work one at a time: the rest wait in the `waiting` state and start on their own when the one ahead
finishes. A label `serial:<group>`, such as `serial:audio` or `serial:terrain`, puts an issue in a serial group of its
own: issues of one group work one at a time, while issues of different groups, and plain `serial` ones, work side by
side. An issue with several such labels waits until every one of its groups is free. Create each group's label in the
project when its first issue needs it. Mark an issue blocked by another, and you can label it `agent-ready` before its
blocker is done: its request waits at the dispatcher, which moves the issue to `waiting` with a note, and the issue
starts on its own once its blockers close, from a main that has their work.

The dispatcher reads blockers and serial groups from the local store: a `blocks` link names an issue's blocker, the
labels name its groups, and an issue holds its groups' slots while it is `in-progress`, which lasts until its review
merges or closes. An issue whose run the dispatcher has started holds them from the start, before the run claims it.
When the dispatcher or `agent-desk serial release` picks which waiting issues start, a `queued` issue holds its groups
too, since it is on its way to the slot, unless the project is in Manual and nobody picked it, as
[Manual](#auto-manual-and-hold) describes.
Closing a blocker, or a serial holder leaving `in-progress`, writes a `state-changed` event, and the dispatcher tries
its waiting requests again from that event, with no timer: the fresh starts of queued and waiting issues in the
project's start order, and every other request by request time. A serial group's slot goes to the group's first issue
in the start order whose run can go, not to its lowest number. An issue that waits with no request
behind it, such as one `agent-desk start` found blocked or one whose parked request the dispatcher couldn't start, gets
one from the dispatcher on that same event, an agent run's end, or its 30-second reconcile, once its blockers have
closed and its serial slot is free, the first of each serial group in the start order. The dispatcher leaves an issue `on-hold` or
in `needs-design` in that state while its request waits. Label an issue `on-hold` to park it until you take the label
off: no agent run starts an issue on hold, not even one a comment sent to the agent asks for, until it leaves the
state.

An agent run ends at its agent's result, the final message `claude -p` reports when the agent's last turn finishes.
A task the agent left running in the background, such as a Bash command waiting on checks, keeps `claude -p` alive
past that point, holding the run's slot and its memory claim, so `lib/agent_run.py`, which every agent, fix, chore,
and discussion run goes through, waits up to 30 seconds for `claude -p` to exit, then ends it and every process
it started, TERM first and KILL 10 seconds later. It adopts each process whose parent exits during the run, so that
process stays among the ones it ends, and reaps it once it exits, so a run leaves no zombie for a test that checks a
killed process is gone. A run it ended this way stopped early, with its task unfinished: its log names the newest
background task still running, such as the Bash command, and the run fails when the result's `is_error` says the
agent failed, and otherwise exits with status 75, which the store records as `stopped`. An agent run on an issue then
posts on the issue, and on its review when there is one, that the agent stopped early and which command the run
stopped, keeps its worktree, and leaves the issue in progress, so Needs You shows it as stopped with Take Over and
Continue. The next run on the issue takes up the same session, as after a time limit, and its prompt tells the agent
to run that command again in the foreground and to end its turn only once it returns. A fix, a chore, an agent sent
back to a review, a parent check, or a discussion that stopped early says so the same way on its review or issue, and
a chore that stopped early asks for no queued next run. Runs forbid scheduled wakeups, cron jobs, and Monitors, which
would keep the agent waiting past its last turn the same way.

A run's journal shows what a quiet run waits on. While `claude -p` writes no stream event for 5 minutes, as when the
agent waits on a long foreground command, `lib/agent_run.py` logs a beat line, and another every 5 minutes the silence
lasts:

```text
Still in Bash for 12 min: timeout 7000 bash -c 'until grep -q … · background: just bank-fill algebra-1 … · busy: claude -p ×3 (85% CPU), python -m quiz.bank_fill (4%)
```

The line names the newest tool call still waiting on its result and how long it has run, each background task still
running, and the three commands under the run that used the most CPU since the line before, grouped by command, with
`python -m <module>` and `uv run …` named by what they run, or `busy: nothing`. A line that starts `Quiet for` means no
tool call is waiting. Each beat also goes, as a JSON line of `type` `beat`, a UTC `timestamp`, and its `text`, to
`~/.local/state/agent-desk/beats/<session id>`, which stays with the session. `AGENT_DESK_BEAT` sets the interval in
seconds.

An agent can wait on one command in the foreground for up to 2 hours, such as a long benchmark or a full test suite.
Claude Code moves a command to the background when it reaches its timeout, and caps that timeout at 10 minutes unless
`BASH_MAX_TIMEOUT_MS` raises it, so `lib/common.sh` starts every run's `claude -p` with `BASH_MAX_TIMEOUT_MS` set to
7200000. It also sets `BASH_DEFAULT_TIMEOUT_MS`, the timeout of a command the agent gives none, to 900000, 15 minutes,
in place of Claude Code's 2, so a test suite runs in the foreground to its end instead of in the background behind a
wait loop the agent writes. A value you export keeps its own, and one in the `env` block of Claude Code's settings
replaces both.

An agent run also ends at its unit's time limit: 3 hours, or 5 for an issue labelled `benchmark`, whose timed runs wait
for the GPU to themselves. A project changes these limits, and those of chores and fixes, with
[`limitMinutes`](#the-config). The project's `slots.benchmark`, by default 2, caps how many agent runs work `benchmark`
issues at once, so their long runs leave the other slots free for short issues. Agents open a draft review at their
first commit, and each commit after reaches it through the repository every worktree shares, so work survives the limit.
When a run is cut off or stops early, the next run on the issue, from Continue, Agent-ready, or `@agent`, takes
up the same Claude Code session with everything it knew, as the notes on the issue and its review show. Every run that continues a
worktree an earlier run left merges the local `main`, or its base, into it first, so the agent goes on from what landed
since; it leaves a worktree with uncommitted changes or conflicts unmerged and says so. A project that keeps its ideas
in issues labels them `idea`, which keeps them out of agent runs until you press Move to Open on one. That button takes
the label off and moves the issue to Open, where Agent-ready queues it like any other. Until then you can talk an idea
over with an agent: Send to Agent in its comment box, or a comment that mentions `@agent`, starts a discussion run,
which reads the idea, your comment, and the code, and answers on the issue with research, options, or a sharper scope.
It makes no branch, commit, or review, and the issue stays in Ideas.

Maintenance chores, skills you install such as the optional `refactor`, `add-tests`, and `trim-comments` skills or a
project's own `audit` that [Chores](#chores) describes, run in chore slots of their own while you work: a systemd
user timer starts the most overdue one when your screen is unlocked, a chore slot is free, and the machine has memory
to spare.

A comment that mentions `@agent` on a review of any other `agent/<slug>` branch, such as a chore's
`agent/chore-<name>-<time>` or one an interactive session named, sends an agent back to that branch and its worktree,
`../<repo>-<slug>`, with the comment, through `agent-desk work-pr <pr>`, which reads the branch from the review in
the local store and posts its notes there. The agent reads the review with `agent-desk review view` and answers with
`agent-desk review comment`. A chore's PR runs with that chore's model and memory need; any other runs as an agent.
The run stops when someone works in the worktree. The Fix button posts such a comment.

The same timer resolves conflicts with main before you reach them. When a review of an `agent/<slug>` branch
conflicts with main, it asks the dispatcher for a fix run, which merges main into the branch and has an agent resolve
the conflicts, described under [Conflict fixes](#conflict-fixes).

## Auto, Manual, and Hold

The page's header switches the desk between three modes, as do `agent-desk auto`, `manual`, and `hold`. Auto starts
every run as a slot frees up.

The [dispatcher](#the-dispatcher) holds each request the mode keeps from starting: it checks the mode before any other
gate and leaves the request waiting, so a held request takes no slot and claims nothing, and starts the moment the mode
lets it. A run never waits or ends at its start, since the run scripts keep no gate of their own.

Each project can have a mode of its own, such as Auto for one project while another stays in Manual. On a project's
tab, the header's buttons show and change that project's mode, as does `agent-desk auto <owner/repo>`. On All, they
set every project to one mode and drop the projects' own; while the modes differ, no button shows pressed. The
pressed button takes its mode's color, the one the project tabs name it in: green for Auto, blue for Manual, and amber
for Hold. Each project's tab names its mode at all times. The dispatcher, the chore timer, and the merge queue's
conflict fixes each follow their project's mode. Hold on All keeps the projects' own modes, and Resume, beside it, or
`agent-desk resume`, gives them back. Beside the mode buttons, the header shows the
[weekly usage limit](#the-weekly-usage-limit), one more reason to hold new runs, which reads and acts the same on
every tab.

Manual starts an agent, fix, or chore run only after you press Start, or `s` on the page, on its row in Queued,
whatever asked for it. Agent-ready on an Open or Blocked issue, Retry, Continue on a stopped issue, a comment sent to
the agent, the Fix button, a chore's Start Now, and the merge queue's conflict fixes each ask for a run and pick
nothing. The dispatcher holds every request nobody picked before it takes a slot or claims anything, including one made
before you switched, and its row waits in Queued with a Start button and the note `waits for Start`: an issue's row for
a fresh start, the review's row with `back to its PR; waits for Start` for a run that goes back to its review, the
review's row with `fixing conflicts; waits for Start` for a fix, and `chore: <skill>` for a chore. Only branch checks
and cleanups go on, since they take no agent slot and spend no Claude tokens, and the chore timer starts nothing.

Start picks what its row shows, and only Start picks. On an issue's row, it picks the issue, which its fresh start
needs, including one the dispatcher or `agent-desk serial release` makes later for a waiting issue. On any row, it
picks each request the row stands for by its run id, so Start on a review's row starts the runs going back to that
review and no other issue, and two comments sent before Start start with one press, the second after the first. A
request asked for after Start, such as a comment sent while the first run works, waits for Start again. Move to Open,
or `n` on the page, on any queued or waiting issue's row in Queued, sends the issue back to Open after a confirm and
drops its pick, so its next Agent-ready waits for Start again; once its run has started, the button moves nothing and
says so. An issue's pick lasts until its run claims it, so a picked issue that waits on a blocker or a serial slot
still starts when the gate clears, and a run that fails after its claim waits for Start again on Retry. A request's
pick lasts until it starts, and Start drops the picks of requests that no longer wait. Switching to Auto starts every
run waiting for Start.

A picked queued or waiting issue says so first in its row's note, such as `picked; waits for a free agent slot`,
`picked; waiting on #506`, or on Hold `picked; starts on Resume`, and its row shows Unpick, or `z` on the page, in
place of Start. Unpick drops the issue's pick, and the picks of its agent requests that still wait, with no confirm.
The issue and its request stay in Queued, and the row shows Start and its `waits for Start` note at once. When the
issue's run claimed it since the board's read, `lib/hold.py` refuses, the row goes back, and the read after shows
where the issue went. `agent-desk unpick <n>` does the same from a shell, and `agent-desk unpick --all
[<owner/repo>]` drops every pick, or one project's. A project that goes to Auto, by Auto on its tab or on All, or by
a Resume into Auto, drops its picks, since Auto starts every run anyway. A Start pressed days ago then can't start
an issue the next time the project is in Manual. Manual and Hold keep the picks, so a Start on Hold still
starts its issue on a Resume into Manual.

In Manual, a serial group's slot goes to an issue you picked. A queued serial issue that waits for Start holds no
slot, and the dispatcher and `agent-desk serial release` start a waiting serial issue only once you pick it, the first
picked one of each group in the start order first, so an issue nobody picked can't take the slot and then stop at Start. A waiting serial
issue nobody picked has a Start button in Queued, and its note names the issue ahead of it, such as `waiting on #506,
which waits for Start`, or reads `waits for Start` when its group is free. Start on a queued issue after a picked one
took the slot leaves it waiting behind that issue. Outside the serial groups, and in Auto, waiting issues start as
before.

Hold lets the agent, chore, and fix runs working now finish and starts no new ones: each new request waits at the
dispatcher, before it takes a slot or claims anything, and starts on Resume, while the chore timer starts nothing.
A [branch check](#branch-checks) of a branch a working run works still starts on Hold, in the project's `check` slots,
so a run that waits for its check can finish; a check of any other branch waits for Resume. A check that waits so
holds back neither [a rename](#renaming-a-project) nor [a removal](#removing-a-project), which wait only for running
checks.
Each queued issue and request nobody picked shows Start on Hold too. Start then picks and starts nothing, and an
issue's row reads `picked; starts on Resume`, and a request's row `starts on Resume`, so Resume into Manual starts what
you picked. Resume goes back to Auto or Manual, whichever the desk was in, which starts the requests held meanwhile.
`lib/hold.py` keeps the state in `~/.local/state/agent-desk/hold.json`.

Hold before you suspend the machine. A suspend pauses the units: a run's time limit counts monotonic time, which stops
while the machine sleeps, so the runs working carry on when it wakes, and Claude Code retries a request cut off
mid-response.

### The weekly usage limit

The weekly usage limit holds new runs while your Claude plan's 7-day usage is at or over a percentage you set. One
limit covers every project on the machine, whatever each project's mode.

```bash
agent-desk usage-limit        # whether the limit is on, its percentage, and the last reading with its reset time
agent-desk usage-limit 80     # hold new runs at 80% of the week's usage, and turn the limit on
agent-desk usage-limit off    # turn it off, keeping 80% for `on`
```

The web page's header shows the same setting beside the mode buttons. While the limit is on, it reads the week's
usage from the last reading, rounded down, and the limit, such as `Week 41% / 80%`, and takes the Hold button's amber at
or over the limit, with a tooltip that names the limit and when the week resets. While the reading doesn't count, as after a failed
fetch, it reads `Week: can't read`, in the error tone, and its tooltip says why. While the limit is off, a small
Usage Limit button takes its place. Either opens a popover with an on/off switch and the percentage, which waits while
the limit is off and offers 80% before one was ever set. Changing either saves it at once through `lib/usage.py`, as
the command does, so turning the limit on reads usage when the last reading is due. The popover refuses a percentage
that isn't a whole number from 0 to 100 before it saves. Escape closes it and drops what you typed; a click outside
saves a valid percentage you typed. One limit covers every project, so the control reads and acts the same on All
and on a project's tab.

While usage reads at or over the limit, the dispatcher holds each new agent, chore, and fix request the way Hold
does, before it takes a slot or claims anything, and the chore timer starts nothing. The runs working go on, and the
branch checks, which spend no Claude tokens, start as before. The limit is one more reason to hold a request: Auto,
Manual, and Hold work as before, and Resume never lifts it. Once a reading falls under the limit, as after the
weekly reset, the dispatcher starts the held requests on its own, in the order it starts waiting requests.

`lib/usage.py` reads the usage from the endpoint Claude Code's `/usage` reads, with the sign-in in
`~/.claude/.credentials.json`, which spends no tokens. It fetches only while the limit is on, at most every five
minutes and once at the weekly reset, and keeps the reading in `usage.json` in the state folder, so the dispatcher,
the chore timer, and the command share one fetch. `lib/hold.py` keeps the limit in `hold.json` as `usageLimit`, which
`hold.py status` prints. The endpoint is undocumented, and a fetch also fails offline or on an expired sign-in. A
failed fetch keeps the last reading, which holds runs for up to an hour, or until its window resets. After that the
limit lets runs start, and the command, the dispatcher's log, and `agent-desk doctor` say it can't read usage.

## Merge queue

A review's check tests its head merged with main as main stood when the check started, so two reviews that each pass
alone can break main together. agent-desk keeps its own queue, which merges, tests, and lands reviews in local git with
no GitHub call. Merge in the pane or on the web page adds the review to it, as `agent-desk merges add <n>` does from
your terminal. `merges add` refuses inside an agent run, where `AGENT_DESK_RUN` is set, so an agent can't queue its
own review to merge; [SECURITY.md](../SECURITY.md) explains the stamp. The merge timer runs `agent-desk merges tick`
every minute while reviews wait, as the chore timer does every 5 minutes. A refresh of the pane or the page ticks
nothing: it shows the queue as the last tick saved it. The tick reads each review's branch, draft flag, and
checks from the [local store](#the-local-store), and each branch's head from git in the main checkout. The checks are
the [branch checks](#branch-checks) the dispatcher runs, and the queue queues one itself for a head that has none.

A tick first tries a batch. When the oldest queued reviews, up to four, each passed their own checks and merge
cleanly, it merges them in queue order onto a `merge-queue/` branch cut from main with `git merge-tree`, which writes
no worktree, and queues a check of the branch's head:

- Once that check passes, it fast-forwards main to the branch, so the reviews land exactly as tested, in one step,
  and the store records each one merged with its own merge commit.
- A failed check halves the next batch, down to one review at a time, so the review that broke main ends up tested
  alone and leaves as below; once a review merges or leaves, batches grow again.
- A batch whose main moved, whose reviews left the queue, or one of whose branches took a commit since the batch merged
  it, starts again and ends its check, so a commit the batch never tested can't be cleaned up as merged. Each batch
  deletes its branch when it ends.

Otherwise the tick works the oldest queued review alone:

- When the review's head lacks main's newest commit, it merges main into the branch: inside the branch's worktree
  when it has one, and with `git merge-tree` when it has none. It queues a check of the new head and waits for it. A
  worktree that can't take the merge, as with uncommitted changes or an untracked file where main adds one, keeps its
  review waiting with a note that names the cause, and the tick goes on to the reviews behind it. The review tries
  again on the next tick. A merge that conflicts in the worktree while `git merge-tree` finds none waits the same way,
  with a note naming the conflicting files, such as "main conflicts in notes.md".
- Once a check of a head that holds main passes, it fast-forwards main to a merge commit of the review, named `Merge
  review #<n> from <branch>`, whose files match the tested head. The store records the review merged with that
  commit, and the tick goes on to the next review, which now needs main merged in too.
- A project without a `test` command has no checks, so its reviews merge once they hold main, one at a time.
- A head whose check failed, retry included, leaves the queue, unless main broke it as the next item describes.
- When the tick merges main into a review, it notes whether the review's checks had passed. A review from an
  `agent/<slug>` branch whose checks passed then and fail on the merge holds the queue while an auto-fix adapts it to
  what main changed, described under [Conflict fixes](#conflict-fixes). The review's check runs again on the head the
  fix makes and merges it once it passes. The review leaves the queue, saying the auto-fix couldn't make its checks
  pass, once a fix fails, once `fixes.maxTries` fixes tried the head, after an hour without one, or when the fix's head
  fails too. A review whose checks had failed before main merged in, a review from another branch, and a project with
  `fixes.auto` off leave with `checks failed`.
- A review from an `agent/<slug>` branch that conflicts with main holds the queue while an auto-fix resolves its
  conflicts, described under [Conflict fixes](#conflict-fixes), so main stays where the fix merged it. The review
  leaves the queue once a fix tries this head against main's current commit and fails, once `fixes.maxTries` fixes
  tried this head, or after an hour without one; a review from another branch, or a project with `fixes.auto` off,
  leaves at once.
- A review that leaves the queue, as above or because it becomes a draft, closes, or loses its branch, gets a note
  from the tick that says why, and Needs You shows why for a day, or until its branch moves. Merge queues it again.

Every fast-forward holds the main checkout's lock, `.git/agent-desk-main.lock`, which a release also takes. When a
worktree has the base branch checked out, as the main checkout has `main`, the tick moves it with `git merge
--ff-only`, and leaves your uncommitted changes to other files in place. When the checkout holds uncommitted changes
to a tracked file the merge changes, or an untracked file where the merge adds one, the queue stops until you commit,
stash, or undo them. The review stays first, and the board warns: Needs You leads with a row in the warning tone, which
the web page shows among its header's notices, such as `demo: merge queue stopped: the main checkout has uncommitted
changes to README.md, which #275 changes. Commit, stash, or undo them.` The review's card in Merging takes a `stopped`
badge and icon, and a note in the warning tone that starts with the files, such as `README.md uncommitted in the main
checkout`, and each review behind it notes that it waits, as `after #275`. The project's tab counts the warning row
with its Needs You cards. The warning lasts until a tick finds the queue can move. Before it decides anything for a review, the
tick checks whether the review's base already holds its head, as after a tick that moved `main` and stopped before it
saved the queue, or a store that couldn't record the merge. It then records the review merged with the commit already
there, so no review merges twice. Once `main` moves in the main checkout, the
tick restarts the dispatcher when the merge changed `lib/dispatch.py` or a `lib/` module it imports, directly or
through other `lib/` modules, in agent-desk's own checkout, and the web page's service when it changed anything
under `web/` or `inbox/hooks/` there, each only while its service runs. It then runs the config's `sync.run` there, unless a program
in `sync.skipWhile` runs there.

The web page lists the queue in Merging, above Needs You, and the pane lists it under Needs You. A review whose
auto-fix run or branch check the [dispatcher](#the-dispatcher) holds names the gate in its note, in place of `auto-fix
resolving` or `checks starting`, such as `conflicts with main; auto-fix waits for a free fix slot`.

So a batch of queued reviews takes one run of its checks, and every review lands on a main its checks saw. The queue
lives in `~/.local/state/agent-desk/merges/<owner>__<repo>.json`; `agent-desk merges` lists it, and `agent-desk merges
remove <n>` takes a review out. Each review lands on its own base, `main` for most; a review that targets another
branch, as an issue labelled `base:<branch>` asks, compares with, takes in, and merges into that branch.

### Pushing main

Only the desk writes `main` on `origin`, so `main` moves on this machine first and `origin` follows. A project whose
`host` is `none` pushes nothing. After each merges tick, and every minute from the merge timer, `agent-desk pushes
tick` looks for commits on `main` that `origin`'s `main` lacked when last fetched. When it finds some, it fetches
`origin`'s `main` and pushes `main` if it is still ahead, with a `git push` that never forces. Git runs with no
password or ssh passphrase prompt and a time limit on each fetch and push, so nothing hangs the timer. A fetch or push
that fails for a network or server reason, as while GitHub is down, waits 30 seconds before the next try, twice as
long after each failure up to 30 minutes, so an outage delays the push and nothing else. When `origin`'s `main` holds
commits `main` lacks, or a hook there refuses the push, the tick stops: Merging shows the notice until `main` moves. Merge `origin`'s commits into `main` by hand, and the next
tick pushes; the queue never pulls or rebases. A push that brings a change of `VERSION` to `origin` from agent-desk's
own main checkout publishes the release, as [Releasing](#releasing) describes.

After `main`, the tick pushes each `agent/*` branch whose head differs from `origin`'s copy, in one push that never
forces. A branch that fails stays in the notice and blocks nothing. While `main`'s push fails or waits to retry, the
branches wait too. `agent-desk merges --json` and `agent-desk pushes --json` print `ahead`, how many commits `main`
holds that `origin`'s `main` lacks, `push`, the push queue's state with when `main` last went to `origin`, and
`agentBranches`, how many agent branches match `origin`'s copy and how many wait, with when a push last sent one and
git's reason for the ones that failed. Merging in the pane and on the page shows `main is N commits ahead of origin`
while N is above zero, with what holds the push back, and the GitHub group shows the same state at all times. The
state lives in `~/.local/state/agent-desk/pushes/<owner>__<repo>.json`.

## Conflict fixes

Every chore timer tick, every 5 minutes, `agent-desk fixes tick` checks each project for ready reviews from
`agent/<slug>` branches, an issue's, a chore's, or an interactive session's, that conflict with their base as the
dispatcher last tried them, or whose head the [merge queue](#merge-queue) notes as one whose checks main broke. It
reads the reviews and the runs from the [local store](#the-local-store) and each branch's head from git in the main
checkout. Each merge moves main and can bring back the conflicts of a review fixed before it, so the tick asks for a
fix, with `agent-desk request fix <n>`, only for the merge queue's oldest review, which holds the queue until its fix
lands: each review gets fixed once, against the main it merges onto. A conflicting review outside the queue waits for
Merge, and one behind others in the queue for its turn; Fix in Needs You resolves a review's conflicts sooner, as when
you want to review the resolution. Like the chores, fixes start only while the desk runs new work and your screen is
unlocked, or, with the config's `presence` at `"always"`, whenever the desk runs new work. `lib/fixes.py` also holds
the oldest review's fix back while:

- a run works the review or its issue, or waits to, as the store's `runs` table lists them;
- a process works in the review's worktree, such as a session you resumed there;
- a fix already tried this head against the current commit of the review's base, main or the branch a
  `base:<branch>` label set, or `fixes.maxTries` fixes tried this head.

`~/.local/state/agent-desk/fixes/<owner>__<repo>.tsv` notes each start by the review's head and its base's commit. A
fix that works moves the head, which starts the count over; once the tries run out, the review stays in Needs You with
its Fix button. `agent-desk fixes status` lists each review due a fix and what its fix waits on, as does the review's
row in Needs You, such as `auto-fix on Merge`; `--json` prints the list for the board.

The dispatcher starts the `fix <n>` run as `agent-desk fix <n>`. The run shares its review's
target with `@agent` requests, and for an issue's review it takes the issue's lock and stops when another run holds
it, so it never works a branch beside that branch's agent. It reads the review's branch, base, and state from the
[local store](#the-local-store) and posts its notes there. A trial merge of the local `<base>` into the local
`<branch>` with `git merge-tree`, which writes no worktree, tells whether the two conflict. For a conflict, the run
checks the branch out in the review's worktree, making it again when it's gone, and merges the local `<base>`. The run
reads and moves only local branches and fetches nothing, so it works with origin out of reach, and [the push
queue](#pushing-main) publishes the branch later. A merge with nothing left to resolve, as when the base moved since
the trial, commits at once; otherwise an agent resolves the conflicts, runs the tests that cover any code it
changed, and commits the merge, which reaches the review without a push. It runs no tests for a resolution in docs
alone, and leaves the full suite to the branch check of its commit. The agent reads the review with `agent-desk review
view` and comments on it with `agent-desk review comment`. It never rebases or force-pushes. When a conflict needs a
decision neither side settles, it aborts the merge and says why on the review. The run counts as a success only when the
branch holds the base.

A review that merges cleanly gets a fix only when local checks fail on its head. `lib/failures.py excerpt <n>` reads the
review from the local store, finds its head as `agent-desk review checks` does, and takes the newest check of each name
on that head from the store. The run gives the agent the name, log path, and error lines of each one that failed, timed
out, or was lost, quoted from its log as `lib/failures.py log` picks them, and says the checks passed until main merged
in. A line says what failed when a word such as `error`, `FAIL`, or `Traceback` appears outside a path or file name,
such as `Audio/error.wav`, and before any warning; the excerpt keeps the four lines after each, where a failed test
prints its expected and actual values, up to 60 lines. A count of no errors or failures doesn't count. When no line says
what failed, the line before ``error: recipe `ci` failed`` does, and a log without one gives its last lines. The agent
reads the commits on main that changed the code the errors name, adapts the review's change to them, runs the project's
tests, and commits, which moves the branch and runs the checks again. When it can't make them pass, it commits nothing
and says why on the review, and its changes stay in the worktree. That run counts as a success only when the agent
committed. The inbox shows a fix like an agent sent back to its review: in Queued while it waits and in Runners while it
works.

## Chores

A chore is a skill or command that Claude Code runs as `/<name>`. `agent-desk run-chore <name>` starts a headless
agent with that one line as its prompt, so any chore skill you install can run, and `chores.every` lists the ones a
project runs. agent-desk ships no chore skills of its own.

The `refactor`, `add-tests`, and `trim-comments` skills that the examples use are optional chores kept in
[erjand/agents](https://github.com/erjand/agents), forks of Jordan Byron's `singleshot-*` skills, published under the
MIT license in the `productivity/` folder of [jordanbyron/agent-skills](https://github.com/jordanbyron/agent-skills):

- `refactor`, from `singleshot-refactor-one`, finds one behavior-preserving refactor, makes it, and opens a PR.
- `add-tests`, from `singleshot-untested-one`, finds the source file that most needs tests, writes them, and opens a PR.
- `trim-comments`, from `singleshot-comment-one`, trims or rewrites the comments in one file and opens a PR.

The refactor and test skills began as Mike Whittemore's. Install the ones you want by cloning either repository and
linking each skill's folder into `~/.claude/skills/`, as its README shows. agent-desk links to them instead of shipping
them in its plugin, because each skill writes the lessons it learns from a rejected PR into its own folder, and Claude
Code keeps a plugin's skills in a cache it replaces on update. agent-desk names a chore by its skill's name as written,
in its branches, `agent/chore-<skill>-<time>`, its rows, its logs, its memory history, and its model settings, and
holds one back while its last PR is open. A chore that files issues instead, `audit` under the label `audit`,
`security-audit` under `security`, and any other chore `chores.issueLabels` names, waits while
`chores.maxAuditIssues` issues with its label are open, and `agent-desk chores status` shows that count as what it
waits on.

`agent-desk chores rename <old> <new>` carries a chore over to a skill's new name, such as `singleshot-refactor-one` to
`refactor`, without losing its schedule. It refuses when `<new>` already has runs or a config entry in the project, or
when `<old>` has neither, and says which. Otherwise it renames `<old>` in `.agent-desk.json`'s `chores.every`,
`chores.paused`, and `models.chores` through the review the web page's schedule controls use, on
`agent/chore-schedule`, and prints the review's number; it changes no file in the main checkout. It then gives the
project's runs of `<old>` in the store the new name in one transaction, with a `chore-renamed` event, and moves the
chore's ledger. `agent-desk chores status`, the pane, and the web page show the old runs under the new name, and the
schedule counts from the newest of them. Until the review merges, the main checkout's config still lists `<old>`, and
`lib/dispatch.py chore-runs` reports `<new>`'s newest run for `<old>` too, by the event, so the timer doesn't start
`<old>` as a chore that never ran. `-R <owner/repo>` names the project, and `--all` renames in every registered
project whose config names `<old>` and skips the rest with a line saying so. The renamed chore's earlier reviews keep
their old branch names, so its brief and its hold on an open PR start again from the new name's branches, and its
memory history from its first runs under the new name.

agent-desk ships no `audit` skill either. A project that wants an audit writes its own skill named `audit`, which files
what it finds as issues labelled `audit`; the timer holds it back while `chores.maxAuditIssues` of them are open. The
shared `security-audit` skill from the same repository files issues labelled `security`, and the timer holds it back by
those the same way.
agent-desk's own audit runs the shared `audit` skill from [erjand/agents](https://github.com/erjand/agents), installed
in `~/.claude/skills/audit`, with the profile and lessons in `.agent-skills/audit/`. The profile names the source the
audit reads, its critical paths, how it verifies a finding without touching the live desk, and the docs and skill
specs it checks for drift. Its `security-audit` chore runs the shared `security-audit` skill every 7 days, with the
profile and lessons in `.agent-skills/security-audit/`. The profile names its trust boundaries, the web server, the
state folder, imported GitHub text, and the text that reaches headless agents, and checks the code against
[`SECURITY.md`](../SECURITY.md). The store creates the `security` label on the first issue that carries it.

`lib/models.py` holds default models for the `trim-comments`, `add-tests`, and `audit` chores, which apply only to a
project that installs and lists those chores. `agent-desk chores status` warns on stderr about each chore in
`chores.every` whose skill Claude Code can't find, through `lib/chore_skills.py`. It looks in the project's
`.claude/skills/` and `.claude/commands/`, the same folders under `~/.claude` or `$CLAUDE_CONFIG_DIR`, and the
plugins in `CLAUDE_CODE_PLUGIN_DIRS` or installed for you or the project.

A chore starts only while you're at the machine, which the config's `presence` decides. At `"desktop"`, the default, a
tick skips while your screen is locked or no Wayland or X11 session of yours is open, and says which. On a machine
without a desktop, such as a headless server, chores and fixes never start at `"desktop"`, and `agent-desk chores
status` warns about it on stderr. Set `"presence": "always"` there to start them whenever they're due, and enable
lingering with `sudo loginctl enable-linger $USER`, since the chore timer is a systemd user timer that stops when
your last session ends.

`agent-desk chore <skill>` asks the [dispatcher](#the-dispatcher) for a `chore <skill>` run, and the dispatcher runs
`agent-desk run-chore <skill>` in a unit of its own. The run tells the agent to open its review in the local store
with `agent-desk review create` where the skill would open a PR, and afterwards finds the chore's open review with
`agent-desk review list --head`. A run that opened none loses its worktree and local branch. A chore queued with a
count or `forever` asks the dispatcher for its next run as the run ends, unless the run failed, and the dispatcher
starts it once the ending run lets go of the chore. The script writes that request as agent-desk's own, since the
dispatcher ignores a request an agent wrote.

The chore timer's `agent-desk chores tick` asks the dispatcher for the most overdue chore the same way. The schedule
reads each chore's newest run from the store's `runs` table through `lib/dispatch.py chore-runs`, so a chore you
start by hand counts as well: `agent-desk chores status` shows it queued and then running, no tick asks for it again
while it waits or works, and once it ends its row shows how it ended and its next run falls due `chores.every` days
after it started.

## Chore history

Each chore run starts from `main`, so `run-chore.sh` tells the agent what the chore's earlier runs did before it
starts, through `lib/chore_ledger.py`:

- leads earlier runs left for later in the last 30 days, to take up first;
- the chore's open reviews, whose files the run leaves alone so each review stands alone;
- files the chore's reviews merged in the last 30 days changed, to leave unless the run finds something new;
- reviews from the chore that you closed without merging in the last 90 days, whose changes it doesn't propose again;
- files earlier runs in the last 60 days examined and passed over, to skip until they change on `main`.

The agent ends its final message with a `chore-report` block listing the files it passed over, the leads it found, and
the brief's leads it settled, and `run-chore.sh` adds it to
`~/.local/state/agent-desk/chores/<owner>__<repo>/<skill>.jsonl`. A lead leaves the brief once a newer run settles it,
copying its note into `settled` after filing it as an issue or finding it no longer holds, or once every file it names
changes on `main` since that run or in a merged review of the chore. A lead that names no files stays until a run
settles it or it turns 30 days old. The ledger keeps a chore's newest 200 runs of the last 90 days, and the brief at
most 40 files and 10 leads a list and 6,000 characters in all, about 1,500 tokens, however long the chore has run. The
brief reads the chore's newest 100 reviews, those of an `agent/chore-<name>-` branch, from the [local
store](#the-local-store), so a review you merge or close counts at once. It reads each review's changed files from git
in the main checkout: a merged review's merge commit against its first parent, and any other review's branch since it
left its base, or the head the review recorded once the branch is gone.

The chores' stats on the web page and the History column's costs read the store's `runs` table, which began with the
store. `python3 lib/run_log.py import [<path>]` copies the runs agent-desk logged before it, in
`~/.local/state/agent-desk/runs.jsonl` by default, into that table as finished runs: a run that exited 0 as succeeded
and any other as failed, each chore under its skill from the project's `.agent-desk.json` or the name
`agent-desk chores rename` gave it since. The log named a chore without a `singleshot-` prefix, which the import still
reads. A line whose run the dispatcher also recorded, the closest of its target to end within two minutes of the line,
fills in what that run's row lacks instead. An import writes one `imported` event for each project it changed, so the
page reads their boards again, and an import run again changes nothing. The chore readers order runs by time, since the
imported ones have newer ids than the runs the dispatcher recorded first.

## Memory

Each agent, chore, and fix run checks the machine itself before its heavy work. `work-issue`, `work-pr`,
`run-chore`, and `fix` call `desk_memory_start`, which runs `lib/memory.py`:

1. It estimates the run's need in GB. For a project's first runs it takes `resources.needGB` for the kind of run,
   `agent`, `chore`, or `fix`, raised by `resources.labelGB` for any of the issue's labels. Once the history holds five
   runs of the same kind in the project (an agent run, or a run of one chore skill), the need becomes the 90th
   percentile of their peaks plus a fifth, raised by any label with three runs of its own.
2. It waits, holding its slot, while either of two checks holds it, and says which in the run's log once a minute,
   such as `memory pressure is 12%, over 5%`. After `resources.maxWaitMinutes` it starts anyway, whatever held it.
   - **Pressure.** The memory pressure your apps feel is above `resources.maxMemoryPressure`, by default 5: in more
     than 5% of the last minute, some of their tasks stalled waiting for memory. Under pressure the kernel swaps your
     apps out, which raises `MemAvailable`, so the memory check alone would let more runs start. The check reads the
     larger `some avg60` of the `memory.pressure` files of `app.slice` and `session.slice` under your systemd user
     manager's cgroup, `/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/`. Runs live in their sibling
     `agentdesk.slice`, so a run the kernel stalls at its own `MemoryHigh` holds no other run. Without either file, as
     without a systemd user manager or cgroup v2, the check reads the machine's `/proc/pressure/memory`, and a kernel
     without that file counts as under no pressure. Swap in use doesn't count, since it stays high long after the
     pressure ends.
   - **Memory.** The memory available, less what running runs claimed and haven't taken up yet, falls short of the
     need plus `resources.reserveGB` kept free for you.
   - **A fix goes first.** The merge queue stops at a review that waits for its conflict fix, so a `fix` run waits only
     for pressure above `resources.fixMemoryPressure`, by default 15, and counts the memory available without the
     part of other runs' claims they haven't taken up yet. `agentdesk.slice`'s `MemoryHigh` holds those runs back as
     they grow. The reserve still stays free for you.
3. It claims the need, so runs that start together don't count the same free memory twice. The claim lasts until the
   run ends, since a run may take half an hour to reach its peak, and counts only the part of the need the run's
   cgroup hasn't allocated yet. A run that never reaches its need keeps the rest claimed, so fewer runs start at once,
   until it passes its settle time.
4. While the run works, it samples the anonymous memory of its unit's cgroup every 5 seconds: what the run's programs
   allocated, without the file cache the kernel takes back under pressure. It keeps the peak so far and the minutes from
   the start of sampling to that peak. When the run ends, both join `~/.local/state/agent-desk/usage.tsv`, which the
   next estimate learns from. Rows written before the history kept the minutes have none, and count toward the need but
   not the settle time.
5. Once the history holds 20 runs of the same kind in the project with their minutes to peak, a run gets a settle
   time: the 95th percentile of those minutes, times 1.5, lengthened by any of the run's labels with 10 such runs of
   its own, and never more than 45 minutes. Past it, nearly every earlier run of its kind had reached its peak, or
   the kind peaks so late that a longer wait would keep the claim in full for most of the run. The run's claim then
   counts as the larger of 1.5 times its peak so far and its peak so far plus 2 GB, and never more than it claimed. A
   run that spiked and fell back keeps its spike in that peak, and `agentdesk.slice`'s `MemoryHigh` and the pressure
   gate hold back a run that grows past its shrunk claim. As before, the memory the run holds counts against the
   memory available, and the claim counts only for the rest. Before the settle time, or without one, the claim counts
   in full. The run's log says when its claim may shrink, and the dispatcher's log says when it does, with the claim
   before and after, the peak so far, and the settle time. `resources.shrinkClaims: false` turns this off for a
   project.

`python3 lib/memory.py claims` prints each live claim as JSON with the unit that holds its process, what it claimed,
what it counts for now, the peak so far when it shrank, its settle time, the memory the run's programs hold, and what
it holds back beyond them. The held figures add up to the claims the memory gate counts. The command only reads, and
leaves the claims of runs that ended for the next memory check to delete. The web page's
[Resources view](#the-web-page) shows its figures.

Every unit the [dispatcher](#the-dispatcher) starts, for an agent run, a discussion, a chore, a fix, a check, or a
cleanup, runs inside `agentdesk.slice`, which `agent-desk dispatch install` writes to `~/.config/systemd/user`. The
slice sets `MemoryHigh=50%`, which systemd reads as half of the machine's physical memory, and `CPUWeight=20` and
`IOWeight=20`, the weights each unit carries, with `Nice=10`. Once the runs together pass half of memory, the kernel
slows them and reclaims memory from them, before it pushes your apps to swap. Each unit keeps its own `MemoryHigh`,
18.75% of the machine's memory for an agent run or a branch check and 12.5% for a chore or fix run, so one run can't
take the whole slice. The slice sets no `MemoryMax`, so the kernel never kills a run's processes for growing. Its name
has no dash, since systemd reads each dash in a slice's name as a level: `agentdesk.slice` sits beside `app.slice`,
where its weights compete with your apps. `agent-desk doctor` warns when the file is missing or differs from what
install writes. See the slice and its runs with `systemctl --user status agentdesk.slice`.

The defaults that guard the machine scale with its total memory, `MemTotal` in `/proc/meminfo`, rounded up to whole
GB, since `MemTotal` falls a few GB short of the memory installed. A project's config overrides the first two.

| Default | Share | 16 GB | 64 GB | 128 GB |
| --- | --- | --- | --- | --- |
| `resources.reserveGB` | 12.5% | 2 | 8 | 16 |
| `chores.minFreeGB` | 18.75% | 3 | 12 | 24 |
| Agent runs' and branch checks' `MemoryHigh` | 18.75% | 3G | 12G | 24G |
| Chore and fix runs' `MemoryHigh` | 12.5% | 2G | 8G | 16G |
| `agentdesk.slice`'s `MemoryHigh` | 50% | 8G | 32G | 64G |

systemd works out the slice's share itself from the memory the kernel reports, without rounding, so a 64 GB machine
with 60 GB in `MemTotal` caps the slice at 30G. `resources.needGB` stays fixed, since a run's size depends on its
project rather than the machine.

## GPU slots

Agent runs in different projects render on one GPU. Two hidden game renders can hold the GPU at 100%, and three at once
have made the NVIDIA driver fail its allocations. `agent-desk gpu` makes GPU work take turns across every project and
checkout on the machine:

```bash
agent-desk gpu -- tools/run_hidden.sh godot-mono --path . res://scenes/tools/place_shots.tscn
agent-desk gpu --exclusive -- tools/run_hidden.sh godot-mono --path . res://scenes/tools/benchmark.tscn
```

Route a project's GPU launches through it, such as the script that starts a hidden render, so its agents wait for the
GPU instead of rendering beside another project's. The dispatcher still starts a run at once, and the run waits only
when it renders.

- The machine has one slot until `agent-desk gpu slots <n>` sets another count, for a GPU that can take more than one
  render. It writes the count to `gpu-slots` in agent-desk's state folder, and every `agent-desk gpu` call reads that
  file as it starts, so the change reaches every project, unit, and shell at their next call. `AGENT_DESK_GPU_SLOTS`
  overrides the file, for tests.
- A shared call takes a slot past the first only while the NVIDIA card has the VRAM free that
  `agent-desk gpu slots --min-free-vram <GB>` sets, 7 GB unless set, so two renders can't fill the card beside your own
  game. The setting lives in `gpu-min-free-vram` beside `gpu-slots`, and `0` turns the check off, such as for a card
  without `nvidia-smi`. Free VRAM is total less used from `nvidia-smi`, of the NVIDIA card with the most memory, in GB
  of 1000 MiB, so a 16303 MiB card has 16.3 GB.
- Below the threshold, the call waits as if every slot were held: it takes slot 1 when it frees, or another slot once
  the VRAM frees, which it reads again every three seconds. Slot 1 never waits for VRAM, so the machine always makes
  progress, and `--exclusive` ignores VRAM. Without `nvidia-smi`, or when it fails or prints something unreadable,
  shared calls use slot 1 only.
- `agent-desk gpu slots` prints the count, where it came from, the VRAM threshold with the VRAM free now, and the
  checkout holding each slot or `free`.
- The command waits for a free slot, runs, and frees the slot when it exits, and `agent-desk gpu` exits with its
  status.
- `--exclusive` holds every slot, for a timed benchmark that must have the GPU alone: slots 1 to the count and every
  other `gpu.slot<n>` lock file in the lock folder, so a caller that started under a higher count can't render beside
  the benchmark.
- Every command passes through a queue lock to reach the slots and keeps it until it holds its slots, so commands that
  start while an exclusive one waits line up behind it, and a stream of short renders can't starve a benchmark.
- While it waits, it prints once to stderr what it waits for and which checkouts hold the slots, by the folder each
  holder runs in, such as `agent-desk gpu: waiting for the GPU slot, held by: game-issue-12`. A call that waits for
  VRAM says so with the VRAM free, such as `agent-desk gpu: waiting for VRAM, 6.3 GB free of the 7 GB a second slot
  needs, or for slot 1, held by: game-issue-12`.
- The locks are the files `gpu.queue` and `gpu.slot<n>` in `$XDG_RUNTIME_DIR/agent-desk/`.
- The command runs without the locks' descriptors, so a daemon it leaves behind keeps no slot. Killing
  `agent-desk gpu` itself frees its slot while the command runs on, so stop the command rather than its wrapper.

Each call leaves a record in the [local store](#the-local-store)'s `gpu_calls` table, which `lib/gpu_log.py` writes:
the run `AGENT_DESK_RUN` names, or none outside a run, the folder it ran in, whether it was exclusive, which checkouts
held the slots when it had to wait, and when it asked for its slots, got them, and released them.

- As a call ends, its run's row adds the minutes it queued and held to `gpu_queued` and `gpu_held`, with a `gpu-used`
  event, so a run that still works shows them. The board's row of a working run notes them, such as `Held GPU for 12
  mins, waited 9 mins for it`. The web page's History adds up the runs behind the PRs it lists in its totals line, and
  each PR's Cost names its own when the pointer rests on it.
- `agent-desk gpu status` prints what `agent-desk gpu slots` prints, then each checkout's waits over the last day: how
  many, the minutes they queued, and the longest with who held the slots then.
- The wrapper records the release as it exits, on SIGTERM, SIGINT, or SIGHUP too. A call killed with SIGKILL leaves its
  record without a release, and no total or wait counts it.
- A record the store refuses, such as from a checkout whose code may not open the live database, is skipped without a
  word, and the command runs as before.

## The pane

The agent-desk pane is a Claude Code mod in `inbox/`, which Claude Code loads in every session, from the marketplace
plugin or from `CLAUDE_CODE_PLUGIN_DIRS` in `~/.claude/settings.json` as `./install.sh` sets it, and which turns on in
a project with a `.agent-desk.json`. Each session decides for itself: run `/agent-desk` in a
session, and the pane docks beside the chat there, reads the board from the [local store](#the-local-store), and
opens again when that session resumes. Each second it reads the store's newest event id from `desk.sqlite-event`, one
small file read and no process, and reads the board again when the id moved, so a change made anywhere, by a button, a
command in a terminal, or a run, shows within a second. A read that fails leaves the id unseen, so the next second
tries again. After a minute without an event it reads the board anyway, for
the rows' times and for the merge queue, the automatic fixes, the desk's mode, and the chores table, which their
commands save in files of their own. Its header shows no read time. It runs
`agent-desk` from PATH and the scripts in `lib/` from the checkout that command links into. With `AGENT_DESK_HOME` set,
it runs both from that checkout instead. When it finds neither, it shows how to install the command in place of the
board.
Its header shows the machine's free RAM and swap and its CPU use, sampled every 10 seconds. Its Report button runs
`/agent-desk:resource-report` in the session as if you typed it. The skill reports what uses the machine's memory and
CPU and what could be freed, and frees only what you name. The web page's Resources view runs the same skill in its Chat with
its Audit with Claude button. Red always means act now. Free RAM turns yellow under 15% and
red under 10%, ahead of where an OOM guard such as earlyoom usually stops the biggest process. Free swap turns yellow
under 10%: nothing needs doing, but free RAM is then all the margin left. Closing the pane by hand stops it in that session
until its next `/agent-desk`. Sessions that never ran it show no pane, refresh nothing, and raise no toasts.

The pane builds every row from `agent-desk board --json`, [one read](#the-boards-read) that asks GitHub
nothing: each issue's column comes from its workflow state, and a PR's checks from the
[branch checks](#branch-checks) of its head. Runners shows each run the [dispatcher](#the-dispatcher) works now, under
its kind, `agent`, `fix`, or `chore`. An issue a session on this machine started with `agent-desk start` shows
there as `local` while that session runs; once the session ends without a ready PR, it shows in Needs You as stopped,
with Take Over and Continue buttons. Needs You lists PRs to review, marking one whose head doesn't merge with its base as
`conflicts` and one whose check failed, timed out, or was lost as `failing`. A check's first failed attempt queues a
second, and the PR shows failing while the second waits and runs, until it passes. Each from an agent branch has a Fix button that asks an agent, after a confirm and by a comment the store marks for
the agent, to merge main and resolve the conflicts or make the checks pass; a failing PR has it in place of Merge, and a conflicting one beside
Merge, since the merge queue has the conflicts fixed when its turn comes. The comment quotes the lines of the failed
check's log that `lib/failures.py log` picks, and a failing PR's row shows the first, such as
`test failed: Sprite.cs(12,5): error CS0103: ...`. A PR whose agent went back to it leaves Needs You for Queued while
the agent waits for a slot, then for Runners while it works. Needs You also lists runs that failed, timed out, or
were lost in the last 24 hours, and agents that stopped before their PR was ready. A stopped issue, in progress with
no agent at work, has Continue as its main button, which has the desk carry on the work after a confirm: it asks the
dispatcher again for the issue's newest failed agent run that no agent went on from, as Retry does, or without one
asks for a fresh start as Agent-ready does, leaving `agent-ready` to you. The run takes up the agent's own session
when the last one was cut off or stopped early, and otherwise starts a new session in the same worktree. Either way
the issue moves to Queued at once, and in Manual waits there for Start. A stopped issue whose agent posted a session
also has Take Over, which copies `cd <worktree> && claude --resume <session>` for you to run in a terminal. A PR whose
agent finished but left it a draft, such as when its check fails for a reason outside the issue, shows there as a
draft to read, with no Take Over button. A draft whose check fails shows its first error and a Fix button, which asks the agent
to merge main and make the checks pass, as Fix does for a failing ready PR. A draft whose head conflicts with main
shows `conflicts with main` and a Fix button alone, which asks the agent to merge main and resolve the conflicts and
moves the row to Queued as `fixing conflicts`; one that also fails a check shows that check's error. A draft whose
check passed and that merges cleanly shows a Ready
button, which asks to confirm, runs `agent-desk review ready` on the PR, and shows it at once as a PR to review and
merge. This
covers a PR whose check waited for a `check` slot until its agent finished. A draft whose check still waits or runs,
or that has none, shows neither button.
Merging lists the merge queue, described below, in the order its PRs merge, each with what it waits on. When the
review at the head of the queue waits on an auto-fix run or a branch check that the dispatcher holds, its note says
why, such as `conflicts with main; auto-fix waits for memory: 5.3 GB free of 9 GB needed` or `check waits for a free
check slot`, and the reviews behind it keep `after #<n>`. Open
lists the issues in the `open` state newest first. Each Open row has a Move to Backlog button beside Agent-ready,
which adds `backlog` in the store and moves the issue to the top of Backlog at once, with no confirm. Blocked lists the parked ones, `on-hold` or blocked by an open
issue, newest first with why each waits. One that only open blockers hold has an Agent-ready button, which queues it
to start once they close; Queued then shows it waiting on them. Once they close, one in no serial group has no note
of its own, so its row reads only why the dispatcher still holds its request, such as `waits for a free agent slot`.
Each parked issue also has an Unblock button, which
asks to confirm, since it deletes links, then takes the issue off hold, removes its "blocked by" links to open issues
in the store, and moves it to the top of Open at once. Links to closed blockers stay as history. A stacked
issue waiting on its base PR has no Unblock button. An issue on hold also has Resume, which queues it at the top of
the start order, as [the dispatcher](#the-dispatcher) describes. Ideas lists issues labelled `idea` that no agent was asked
for, newest first, also without the button. Each idea has a Move to Open button instead, which asks to confirm, then
takes `idea` off in the store and moves the issue to the top of Open. An idea an agent discusses stays in Ideas while
Runners shows the discussion as an agent run noted `discussion`, and a discussion that failed or timed out shows in
Needs You with Retry. Backlog lists open issues labelled `backlog`, concrete work you plan to do later, newest first
and without the Agent-ready button. A backlog issue stays out of Open, Blocked, and Needs Design, and one labelled
`idea` as well shows in Ideas. Each has a Move to Open button, which asks to confirm, then takes `backlog` off and
moves the issue to the top of Open. An issue labelled `backlog` that gets queued another way, such as with
`agent-desk issue edit <n> --add-label agent-ready`, leaves Backlog for Queued, as an idea leaves Ideas, and a comment
sent to the agent on one asks for a work run, as on any open issue. Needs Design lists issues in the `needs-design` state, each with a
Designed button that takes `needs-design` off and moves the issue to Open. Each row lines up in three columns: the number, the description, and its buttons. A description
too long for the pane gives way before its note in parentheses, so why an issue waits always shows. The number is that
of what pressing the row opens: an issue's plain, a PR's in teal. A serial issue whose holder in its serial group has a PR waiting
for review names that PR. Pressing a row opens its issue or PR on the [web page](#the-web-page), at the page's
address for it, such as `http://127.0.0.1:7480/#agent-desk:174`, on the port the board's read names as `web`. The pane
first checks that something answers at that port; when nothing does, it opens nothing and says to run
`agent-desk web on`, or `agent-desk issue view <n>`, or `agent-desk review view <n>` for a PR, to read the item in the
terminal. The buttons merge a ready PR after a confirm,
label a backlog issue `agent-ready` and ask the dispatcher for its run, copy the command that takes over a stopped
agent's session, copy the `journalctl --user -u <unit>` command that prints a failed or timed-out run's log with Log,
retry or dismiss a failed or timed-out run, and start a chore now. A working run says how many minutes it has left in its last 30 before its
unit's time limit. A run that ran past the limit shows as timed out, and one cancelled by hand doesn't
show; a timed-out agent's worktree and commits stay on this machine. Retry asks the dispatcher for the run again,
so an agent takes up its issue again from what the last one left, and a chore does its work again. On an agent's
timed-out run on an issue the button reads Continue, as on a stopped issue, since the run takes up the same session; the run leaves
Needs You while it runs and comes back if it fails again. A failed or timed-out run also leaves once a newer run of
its target is asked for, or its issue or PR closes. Dismiss hides the run after a confirm and starts nothing. A Queued
row for a request that waits, on its own or as an agent going back to its PR, has a Cancel run button, which ends that
one request with `agent-desk request --cancel` and leaves the run working its target alone. A queued or waiting
issue's row has a Move to Open button instead, which sends the issue back to Open, as Move to Open does on an idea,
and Close, which closes the issue, cancels the requests that wait for it, drops its Manual pick, and takes the row out
of Queued at once. A row going back to its PR, a Fix row, and an issue whose run started have no Close.
Queued lists the queued and waiting issues first, in the project's start order, ahead of its other rows: agents sent
back to a PR, Fix runs, chores, and requests with no issue row, which keep their own order. Each of those issue rows
has Move Up and Move to Top unless it is first, and Move Down unless it is last; each runs `agent-desk queue order
<n> --up`, `--down`, or `--top` without a confirm, moves the row at once, and puts it back when the store refuses, as
for an issue that started since the read. On the web page, you can also drag one of those cards by its row to a new
place among its own project's queued and waiting issues. While it drags, a line on the card under the pointer shows
where it lands, and none shows over another project's card or a Queued card outside the order. The drop runs
`agent-desk queue order <n> --to <place>` the same way, and a click on a card still selects it. The dispatcher starts the queued issues' fresh starts in that order, and a
waiting serial issue's note names the first queued issue of its group in it. A row whose request the dispatcher holds
for spare memory, the weekly usage limit, or a free slot adds why after any note it has, such as `waiting on #506;
waits for memory: 5.3 GB free of 9 GB needed` or `waits for a free agent slot`, so a held queue reads apart from a
stuck one.
Merge puts the PR in the merge queue. A toast says when something new needs you.
Each button shows its move at once and holds back the pane's reads while its command runs. Every button writes the
store or the merge queue through `agent-desk` before its command returns, so the read after it shows the move, and a
command that fails takes the move back. Fix's PR shows in Queued from its comment to the agent until the dispatcher
answers it, and then from the request for the agent's run.
Pressing a section's heading collapses or expands it, and the pane remembers collapsed sections across sessions. Open, Blocked, Needs Design, Backlog, and Ideas show five
issues at a time, counted in the heading, with ▲ and ▼ buttons that page through the rest, and ⤒ and ⤓ that jump to the
first and last page. Recently Merged shows the local date and time each PR merged. An issue whose PR merged into another
agent branch, as a stacked PR does, keeps in-progress and waits in Blocked on that branch's PR. A review closes the
issues it fixes only when it merges into `main`, so when that branch's PR merges there, `agent-desk clean` closes the
issue with a comment; until it does, the issue shows in Needs You as merged.

GitHub, the pane's last section, has one row for each place the desk touches GitHub, as the
[web page](#the-web-page)'s GitHub group describes: `main`, the agent branches, and the backup. A healthy row reads dim
and a failing one in the warning color.

The plugin also ships the skills agent-desk runs, in `inbox/skills/`: `work-issue`, `discuss-idea`, `close-parent`,
`create-issue`, and `resource-report`. Claude Code names a plugin's skills after the plugin, so agent-desk and
its prompts call them as `/agent-desk:work-issue` and the like, and loading the plugin brings them along. A skill
finds agent-desk's checkout three folders above its own base directory.

Test it with `claude plugin test inbox` from a checkout of this repository.

## The web page

`agent-desk web on` serves the agent-desk page at <http://127.0.0.1:7480> from a systemd user service that starts at login, and
`agent-desk web off` stops it. Set `AGENT_DESK_WEB_PORT` before `on` for another port, or use `agent-desk web run`, or
`just run` from this folder, to serve it in a terminal. It needs Node 24 or newer, which runs `web/server.ts` with no build step.
The server runs `agent-desk` from its own checkout, or from `$AGENT_DESK_HOME` when that is set, so its service needs no
PATH of its own.

The server loads its code from `web/` and `inbox/hooks/` once, when it starts. After the [merge queue](#merge-queue)
lands a change to those folders in agent-desk's own checkout, it restarts the service while the service runs. A pull,
or a server started with `agent-desk web run`, needs a hand: while the page is open, the server checks its modules
every ten seconds, and once they differ from what it started with, the header says "Restart the web server." in small
text in the warning tone, on a line of its own under the header's gauges and buttons, at the bar's right. Run
`agent-desk web on` to restart the service, or stop `agent-desk web run` and start it again.

`agent-desk web run <owner/repo>...` serves only the registered projects it names, and exits when one isn't registered.
Start it beside the service with its own port and state folder, such as `AGENT_DESK_WEB_PORT=7481
XDG_STATE_HOME=$(mktemp -d) agent-desk web run erjand/agent-desk-demo`. Auto, Manual, and Hold on All still change every
registered project's mode.

Screenshots show made-up cards or agent-desk's own, never another project's. `just demo [port]`, from a checkout of
this repository, builds a demo desk in a new temporary folder with `web/demo.ts` and serves it on port 7481 or the one
given: three made-up projects, with cards in Working, Needs You, Queued, Open, Needs Design, and Ideas, in a store and
projects file of the demo's own, so tab and multi-project pictures read nothing of the live store. Orchard #2's Agent
Log tab shows a made-up session with beat lines, which the server reads from the demo's folder through
`AGENT_DESK_TRANSCRIPTS`. A screenshot server
never takes a copy of the live store, since that holds every project's issues.

The header's bar reads, from the left, `agent-desk`, the Refresh button, the time the shown boards were last read,
the mode buttons, and the [weekly usage limit](#the-weekly-usage-limit). Refresh shows a circular arrow, which turns once a second while a shown project's board reads, or
dims and stays still where the system asks for reduced motion; it reads the open tab's project, or every project on
All.

The page shows every project in `~/.config/agent-desk/projects`, ordered by title, each in a hue of its own: the first
six projects each take a different one, and a seventh repeats the first's. The project tabs form a band under the
header's buttons, in larger text than the rows. The three bars stacked at the top each take a surface of their own: the
header's buttons sit deepest, on a gray a step darker than the panels in light and darker than the page in dark; the
tab band sits on the panels' gray; and the list's search bar sits on the page's ground, as the top of the list under
it. In light, the header's notices and gauges take deeper warning, error, and healthy tones that keep AA contrast on
its gray. All shows every project's work together, and a project's tab shows its
own and counts its Needs you cards in the attention tone. The open tab takes its project's hue in its underline and a
light tint, with All in Working's orange. The header's right end is one row: the machine's gauges, in groups split by
dividers for memory, CPU, and GPU, in a pill beside the New Issue, History, Runners, and Chat buttons, then the `?` button. The pill is a
button with a light fill, as tall as the others, and shows whichever sidebar is open; pressing it shows the Resources
view. Each button shows pressed
while its sidebar shows. On a narrow window the row wraps as one unit under the header's other controls.

A Runners sidebar at the right, which the header's Runners button or `\` shows or hides, lists each shown project's
dispatcher slots, from its [`slots`](#the-config) over the defaults, whether or not anything runs. It takes the History
column's place: showing one hides the other, and the page remembers which shows, or neither, across visits, showing
neither on a first visit. The gauges stay in their pill beside the History and Runners buttons while it shows, and the
sidebar's head shows its title alone. Under it, one table lists the slots, with a small, muted header row naming its
columns: Project on All, Runner, Item, Title, and Time. Each project's rows sit together in the page's order. On All the
project's chip fills the Project cell of its group's first row, the cells under it stay empty, and a rule in the border
color separates one project's group from the next. The Project column stops at 12 characters, so a long project title
leaves Title room: a longer chip ends in an ellipsis and shows the project's full title on hover. A project's tab
leaves out the Project column and the rules, since it shows one group.

Every slot gets a row: its kind under Runner, then, for a busy slot, its number, title, and how long it has worked, or
a muted `free` under Title for an idle one. A busy slot's kind shows in Working's orange, and a kind with every slot
busy shows its Runner cells in the attention tone, which tells why a queued request waits. Each kind's busy slots come
first, in the order the board lists their runs; a check's row names its review. The board counts each kind's busy runs
without naming slots, so a kind that counts more busy runs than its rows match shows the rest as `busy` with no
detail. A kind set to 0 is left out, `benchmark` shows while a benchmark run works, and a benchmark slot's rows are the
agents' on issues labelled `benchmark`. Sessions started with `agent-desk start` take no slot and list after their
project's slots, one row each with `local` as their runner. A long title clips first, so Runner, Item, and Time keep
their room. Pressing a busy row, or its title by keyboard, selects its card, or shows its issue or PR in the panel when
the list has no card for it, as a link to an item does; a free row doesn't react. On a window narrower than 1300
pixels the sidebar hides, as History does.

Under the project tabs, a list on the left holds one card per piece of work: an issue with its PR, runs, and runner, built in
`web/cards.ts` from the board's rows. The cards fall in groups by stage: Needs You, longest waiting first with how long
each has waited; Merging, in the order the queue merges them; Working, with each card's runner; Queued; Open; Blocked;
Needs Design; Backlog; and Ideas. Open cards have Move to Backlog and Backlog cards Move to Open, which work as the
pane's buttons do. Open, Blocked, Needs Design, Backlog, and Ideas list issues by number, highest first, and each heading
has a `# ↓` button at its right end that flips its group to lowest first and back, showing `# ↑` while flipped; on
All, a flipped group keeps each project's cards together in project order and sorts within each. A card shows its project on All and its numbers in columns of their own, so titles line up: its issue's in
gray, and its PR's in green after an arrow when it has one, as `#348 → #349`; a chore's PR shows its own number alone. An issue an agent works or waits to work in Queued or
Working names its open review, so a review sent to the agent keeps its card's review in the panel, and an issue whose
review closed or merged names no review.
Then a state such as `review` or `failed` as a badge, its title, its issue's labels in their GitHub colors, and why it
waits, such as `waiting on #295`, as a note, which a queued or blocked card shows at the row's right end; a note that
names a label, such as `serial` or `on hold`, shows as that label. Chores close the list, each with the model and effort
it runs with, when it last ran and runs next, how its newest ten finished runs went from the store's `runs` table
(their average length and what became of their PRs), a box for how many times to run it, Run, and Loop, which runs it
again each time a run ends; a chore with runs queued shows how many, or that it loops, and a Stop button. A failed run
stops its chore's queue. Edit changes a chore's days between runs, a whole number from 1 to 365, and Pause or Resume
adds it to `chores.paused` or takes it off. Either asks for a confirm, then commits the change to `.agent-desk.json` on
`agent/chore-schedule`, in the `../<repo>-chore-schedule` worktree that `agent-desk worktree chore-schedule` makes, and
opens a review with `agent-desk review create`, titled like `Change the chore schedule: audit every 3 days`. Changes
while that review is open add commits to it and retitle it with `agent-desk review edit`. The commit changes those values alone and keeps the rest of the file's text, and the new schedule
takes effect once the PR merges and the sync updates the main checkout. Until then the row shows the pending value
with the PR's number, as `every 7d → 3d in #52`, which shows the schedule review in the panel. A `git` or `agent-desk` step that fails shows its error in the row,
and removes the worktree and branch the change made, or puts an open PR's branch back where origin has it. Pressing a group's heading, apart from its sort button, collapses it, and Needs Design, Backlog, and Ideas start collapsed. The page remembers which groups it knew, so a
group added since you last opened it, as Backlog was, starts collapsed too. A search
box at the list's top, which `/` focuses, filters it to the cards whose title, number, project, labels, state, note,
runner, or group hold every word typed, opening each group with a match; Enter selects the first, and Esc clears it. A
card added, moved, or dropped leaves the rest of the list where it was on screen: the row under the pointer, or else the
first row in view, holds still. A group's heading, Chores' and GitHub's included, sticks under the search box while its
rows scroll under it, until the next group's heading pushes it out, and it holds no row still, since it stays put
whatever moves above it. Collapsing a group from its stuck heading keeps the heading where it showed, and the keys stop
a card they select below the search box and a stuck heading. On a narrow window, where the page scrolls as one, the
search box and the headings scroll with the list.

A GitHub group closes the list, under Chores, with one row for each place the desk touches GitHub and how it last
went, so it never pushes an issue down. Each project that pushes to a host has a `main` row, which reads `matches
origin` with the time of the last push, or how many commits `main` is ahead and why the push waits: for the next tick,
retrying after a failure with the time left, or refused by `origin`. Its `agent branches` row counts the branches that
match `origin`'s copy and those that wait, with the time a push last sent one, and names any the last push failed to
send with git's reason. A project whose `host` is `none` has neither row. The `backup` row shows once, with no project,
since the backup covers every project: the time of its newest commit and its last push, `no backup remote` on a
machine whose backup repository has none, and the error when the last push or backup failed. A healthy row reads in
the muted tone of the header's notices and a failing one in their warning tone, and the heading counts the failing
rows. Every value comes from a state file or `git` on this machine, and none from GitHub, so the group shows with the
network down and updates with each read of the board, on a store event or the minute's reread. The row under Merging
for `main` ahead of `origin` stays, reading the same state.

A History column at the right lists the reviews the store holds as merged for the shown projects over a span, each
under its issue's number when an agent's branch worked one, with what the runs behind it cost: the total of what Claude
Code's final result reported each agent, fix, or chore run that opened or updated the review would cost at API list
prices, which `lib/run_log.py` records on the run's row in the store's `runs` table. A subscription doesn't bill that, and a run cut off at its time limit never
reports one. A span picker offers the last 24 hours, the default, 3, 7, or 30 days, or a custom range of
whole days up to 90; a line totals the merges, their runs, and the cost; and each heading sorts by its column, newest
or largest first, and again the other way. A search box at the column's top keeps the span's rows whose title, issue or
PR number, or project holds every word, and the totals line then counts and adds up only those; Escape clears it. It shows 20 rows a page. A
Number column between Project and Item draws each row's numbers the way the list's cards do: the issue's in gray, an
arrow, and the PR's in green, as `#12 → #58`, or the PR's alone for a PR with no issue behind it, such as a chore's.
Each number links to its item on the page and shows it in the panel, so either stays a press away after the issue
closes and its card leaves the list, and the Item column holds the title alone. Pressing elsewhere on a row selects its
card when the list shows one, or else shows the PR in the panel as a card of its own. The page remembers the span and the sort, and the header's History button or `h`
shows or hides the column in the Runners sidebar's place, which a window under 1300 pixels wide leaves out. A run the dispatcher didn't start records nothing, so a review
whose runs ended before the store recorded results shows no cost. Each agent run's row also holds its issue's labels
when it started and why it started: `new`, `resumed` after a time limit cut it off, or `feedback` on a returning
request, and its review's state says whether it merged, so you can compare models and efforts by label and outcome.

A Chat at the right, which the header's Chat button or the backtick (`` ` ``) shows or hides in the Runners sidebar's place, talks with a
Claude Code session about the desk. You ask about issues, reviews, runs, the merge queue, or the machine, and it
answers from the desk's live state: it reads `agent-desk issue` and `review`, `agent-desk board`,
`python3 lib/dispatch.py runs`, and `agent-desk merges status`, and runs `/agent-desk:resource-report` and
`/agent-desk:create-issue`. It acts on what you ask: it labels, comments, closes, cancels runs, queues merges, and frees
memory or files. Before anything it can't undo, such as deleting files, stopping processes, merging, or closing a
review, it says what it will do and waits for your yes. It never edits a checkout, and files an issue for a change to
code. Each message carries the card you have selected, its project and its issue or review number, so "this" means
that card. When the card changes between messages, the chat shows a marker such as "Now looking at #12 in Acme".
Enter sends, and Shift+Enter starts a new line. The reply
streams in as it comes, each tool call on one short line as the Agent Log writes it, and Stop ends it. The chat sends
nothing more until the reply ends.

`web/chat.ts` runs each message as one turn of `claude -p` with `--output-format stream-json`,
`--include-partial-messages`, and `--permission-mode auto`, in `agent-desk/chat` under the state folder, never in a
project's checkout, with a system prompt that describes the desk and these rules. The server starts the turn itself,
outside the dispatcher, so the memory gate never holds it, as it never holds your own sessions. The session's id stays
in `agent-desk/chat.json` under the state folder. After a reload or a server restart, the chat reads the conversation
back from the session's transcript under `~/.claude/projects`, and the next message continues it with `--resume`.
Claude Code compacts a long session itself. Clear Chat, after a confirm, forgets the id, and the next message starts a
new session. The server finds `claude` at `AGENT_DESK_CLAUDE`, then on PATH, then at `~/.local/bin/claude`, since the
page's systemd service runs without your shell's PATH, and the turn's tools run `agent-desk` from the server's
checkout. The chat's requests pass the same check of the page's own user as the page's other requests, and a window
under 1300 pixels wide leaves the column out, as it does the others.

The panel on the right shows the selected card the way GitHub shows an issue or a PR. The page keeps GitHub's layout
with a palette of its own, one hue per group at a shared lightness, light or dark with the system. Its title and number
come first with its buttons, then its state as GitHub labels it, such as Open or Merged, and who opened it, with a PR's
branches. Once the header scrolls out of view, a compact bar with the state, title, that line, and the buttons stays at
the panel's top, as on GitHub. Its X, or Esc, closes the card and leaves the panel blank until you pick another.

An issue and its review show as one page with a tab for each conversation, so you read the issue's discussion apart
from the review's. The store keeps them as two items, and the page joins them. The header shows the issue's title and
number, then the review's state, its number, and its `wants to merge … from <branch>` line, with one set of buttons.
The Issue tab shows the issue's description and its comments, oldest first, and counts the issue's comments. The Review
tab shows the review's description and its comments and counts the review's. Each tab ends in its own comment box,
which posts to that tab's item. The Issue tab's box has the issue's Close and Close with Comment, and while the issue
is open, Send to Agent, which sends its text to the agent and asks for a run of the issue, or a discussion of an idea.
An open or draft review's box has Send to Agent, which sends its text with the unsent line comments. Line comments stay on Files Changed,
and a note above the Review tab's box counts the ones waiting unsent. A merged or closed review's tab has no box and
ends in a line that points to the Issue tab. An agent's notes, such as the one naming the review it finished, post to
both items, so they show on both tabs. A card opens on the tab where it waits on you: one in Needs You or Merging with a
review opens on Review, and every other card opens on Issue, whether you click it, step to it with `j` and `k`, or
follow a link to it. The sidebar's Development section lists the issue and its reviews, newest first, each with its
state. Pressing an earlier closed or merged review shows its description and comments on the Review tab, and its files
and checks, and switches to that tab; pressing it again goes back to the open review, and pressing the issue goes back
and switches to the Issue tab. An issue without a review shows the Issue tab and no Review tab, and a review without an
issue, such as a chore's, shows the Review tab and no Issue tab. An issue the store would [delete](#the-local-store),
one with no work behind it, ends its sidebar with Delete Issue, which opens a dialog that asks you to type the issue's
number, `#<n>`, before its Delete Issue button works, since nothing brings the issue back. A delete closes the panel,
and the board drops the issue's card.

Tabs hold Issue, Review, Dependencies, Files Changed, Checks, and Agent Log, each where it applies: Review, Files
Changed, and Checks with a review, and Issue and Agent Log with an issue. The tabs stay in view under the compact bar as you scroll down the card, and a tab you pick there shows
from its top. On a window narrower than 900 pixels the page scrolls as one, and the bar and the tabs stay at the
window's top. Files Changed shows each file's diff in a box that folds, from `git diff <base>...<branch>` in the main
checkout, the branch's changes since it left its base, as GitHub's PR page shows them; a merged review's come from its
merge commit, so they show after its branch is deleted. An imported PR that GitHub rebase-merged shows every commit
the merge wrote, which the import counts. A moved file shows its old path and new, and a binary file
says it has no diff. A review whose branch is gone from the main checkout, such as an imported merged PR whose merge
never reached it, shows its record and comments and says its diff is unavailable. A large diff, past 20,000 rows or
300 files, lists every file with its counts and leaves out the lines of the largest files, with a note that says so;
one past 200,000 changed lines, or one git can't hand over whole, lists its files without lines and says it is too
large to read. On Files Changed the sidebar steps aside for a folder tree of the changed files left of the diffs, as
on GitHub: folders first and then files, by name, with a chain of folders that each hold only one folder joined into
one row, such as `inbox/hooks`. Each folder folds with its caret, and each file carries a mark for its status from
`git diff --name-status`: added, deleted, renamed, or modified, with a renamed file's old path in its row's title. A
click on a file opens its diff, scrolls its heading to just below the files bar, and marks the file in the tree until
you scroll, even when the file sits too near the end to reach the bar. Otherwise the tree marks the file whose heading
has reached just below the files bar, after every scroll and every change that moves the diffs, such as Expand All, a
folded diff, or another review. Over the last screen of scroll that line slides down to the panel's bottom, so the
last files, which can't reach the bar, each take the mark in turn, and the last file holds it at the bottom. A folded
folder takes the mark for a file inside it. When the mark moves to a row outside the tree's view, the tree scrolls
just enough to show it, and never scrolls the page. A filter box above the tree narrows it to the paths holding its
text. The files bar,
with the file count and Expand All and Collapse All, stays in view under the tabs as the diffs scroll, and the tree
stays in view below it, beside the diffs, and scrolls on its own when it is taller than the panel. A button in the files bar hides or shows it,
and the page remembers that across reloads; a panel narrower than 760px hides the tree, and the button shows it
above the diffs. Checks shows the newest branch check
of the review's head from the store, and its Details link opens the check's log from
`~/.local/state/agent-desk/checks/`. An issue's Agent Log tab shows the newest Claude Code session in the
issue's worktree on this machine, run through `lib/agent_log.py` as its run's journal reads, each line led by its
local time in the accent color, and read again every 5 seconds while the agent works. The session's beat lines, from
its file under `~/.local/state/agent-desk/beats/`, go among the agent's own lines by time, muted, so a long wait on a
tool call reads as the beats `lib/agent_run.py` logged during it. Every description and comment renders on this
machine through `web/markdown.ts`, so the panel reads with the network down, and its `#123` references link to the page
itself: a click shows item 123 in the panel, as a card of its own with no buttons when the list has none for it, and a
click with Ctrl or a middle click opens the page at it in a new tab. The number beside the title and a review page's
Development links work the same way. The task-list boxes in an issue's description take clicks, as on GitHub: a
click rewrites that item's `[ ]` or `[x]` in the stored description through `agent-desk issue edit`, counting task
items as marked reads them, so a marker in a code block never counts. The page sends the version of the description it
shows, and the server refuses a click on a description that changed since, puts the box back, and says why. Boxes in
comments and review descriptions stay disabled. A failed or timed-out run's card names the `journalctl --user -u <unit>`
command that prints its unit's log, with a Copy button, and points to the Agent Log tab when the run worked an issue. A box under each tab's conversation writes a comment to
the store as `you`, with a Preview tab the server renders the same way and Ctrl+Enter to post; a comment that mentions
`@agent` goes in marked for the agent. A comment sent to the agent always mentions `@agent`: Send to Agent, on an open
issue or a review, adds `@agent ` to the front of a text without the mention, and the page shows the comment as stored.
The mention and the button write the same comment, so Comment on a text that mentions `@agent` sends it too. The server
refuses Send to Agent on a closed issue and says why.

A `## Decisions Needed` section, in a description or a comment, shows an answer form right under it, so you answer
without scrolling to the box at the bottom. The server cuts the body at the section in `web/decisions.ts` and sends
the section as data beside the body's HTML, every piece through the same sanitizer, and `web/app-decisions.js` draws
it. A section in the numbered layout gets a row per decision with a button per lettered option, labelled with the
option's letter and short label and ✅ on the agent's pick, and an Other field. A second press on a picked option takes
the pick back, and the picked option shows highlighted in the section's text. A section in the older layout gets one
text box. Under the rows, a note box takes free text, and the form has Comment, and Send to Agent wherever the box at
the bottom has it, which on a review also sends the unsent line comments. Neither button works until the form has a
pick, Other text, or a note, and a merged or closed review's conversation, which has no box, has no form. Ctrl+Enter,
or Cmd+Enter, in the note or an Other field presses Comment, as it does in the box at the bottom. The form's
draft stays in place through redraws and a board refresh, apart from the bottom box's, and clears once it posts. It
posts a comment such as:

```markdown
Answers to the Decisions Needed in [the description](#issue-body):

- **1B.** Overlay it
- **2.** Keep the old color for now.

Note text, when given.
```

The first line links the section by its anchor: `#issue-body` or `#review-body` for a description, and
`#comment-<id>` for a comment, with `-2` added for a second section in the same body. A click on that link scrolls the
conversation to the section. The server reads each comment's first line to tie an answer back to its section, and
once the newest answer covers every decision, the section shows a muted line under its heading, such as
`You answered 1B, 2 · 5m ago`, which scrolls to the answer when pressed, and folds the form behind Answer Again. An
answer that leaves a decision open, as when the agent adds one, leaves the form open under that line. The form only
answers: the agent removes answered decisions on its next run, as `work-issue` describes.

A review's page is where you review it. Pressing a line's number in Files Changed, or the + beside it, opens a comment
box under that line of the new file, and Add Comment, or Ctrl+Enter, saves a line comment with its `path`, `line`, and
the `head_sha` of the commit the diff shows. The server refuses a line comment on a merged or closed review, on a file
the review's diff doesn't change, or on a head that isn't one of the review's commits, which its branch holds and its
base doesn't. Each comment shows under its line, marked Not sent until Send to Agent sends it, with a Reply button that
opens the box again. A line's comments and box fit the width the diff shows, up to 820 pixels, and stay in view while
a long line scrolls the diff sideways. A comment made on an earlier head shows under the same line
moved past the lines later commits added or removed above it, from `git diff -U0` between the two heads. One whose
line a later commit changed, or whose head git no longer has, shows as outdated in a box above the diff, with the four
lines of the file it was made on, ending at its own; one on a line the diff leaves out shows there too. Comments are
saved at once and start nothing: Send to Agent, beside the Review tab's Comment button and over the files while any
wait, posts the box's text and every line comment not yet sent as one comment with `to_agent` set, quoting each by its
file and line, and the [dispatcher](#the-dispatcher) starts the agent for it. A line comment made on an earlier head
names that head and the line it sits on now, or says a commit since changed its line. The same write records the
comment's id as each quoted line comment's `sent_in`, so a line comment counts as sent only once a Send to Agent
carried it, and the store refuses a send that quotes one already sent. An `@agent` comment or an agent's own comment
sends no line comment. On a review whose branch is outside `agent/`, Send to Agent sends nothing and says that no agent
works that branch. Each conversation ends at its comment box. An open or draft review's header, and the bar that stays
at the top of the panel, show Close Without Merging after the card's Merge, Fix, and Ready buttons, in the danger style
of an issue's Close. Close Without Merging, or `l`, asks to confirm and closes the review, which starts its cleanup.
Send to Agent and Close Without Merging are each disabled while their request runs, and a toast says how it went or why
the server refused. A merged or closed review has neither. On an issue Close can close, the Issue tab's box also has
GitHub's close button, which reads Close with Comment once the box has text and posts it as the closing comment, in the
same store write as the close, beside a choice of closing as completed, not planned, or duplicate. Duplicate shows a
field for the original's number, with the original's title beside it once the page reads it, or what is wrong, and
Close stays off until the field names another issue of the project; the server checks the number again before the
store closes the issue with a `Duplicate of #<m>` comment ahead of the box's text. The closed issue's header then says
it closed as a duplicate and links the original. A queued or waiting issue in Queued has the button too, so its box
closes a queued duplicate in one step. Every close cancels the agent requests that still wait for the issue and drops
its Manual pick, as `agent-desk issue close` does. An idea's comment box has Send
to Agent beside Comment, which posts the box's text as a comment sent to the agent, and the footer says it asks the
agent to discuss the idea and changes no code. The server refuses that comment on any issue that isn't an idea.
Edit, beside the title in the panel's header, turns the title into a field with Save and Cancel, where Enter saves and
Esc cancels. On an issue's page it edits the issue's title, even beside its review, and on a review with no issue, such
as a chore's, the review's own. Edit in a description's header, open or closed, swaps the description for a box holding
its Markdown, with the comment box's Write and Preview tabs, Ctrl+Enter to save, and Esc to cancel. Save waits until
the text differs from the stored description, and an empty description saves. Edit reads the stored text and the
description's version from `/api/source`, and Save posts to `/api/edit`, which writes through the store as `you`. The
server refuses a description changed since Edit read it, an empty title or one over 256 characters, and a description
over 65,536 characters; the box keeps its text, and a toast gives the reason after the project's name. After a save,
the panel reads the item again and the list shows the new title. While an agent works or waits to work the issue, a
line under Save says the agent read the description when it started and won't see the edit, and that Send to Agent
tells it.

The Labels section of the panel's sidebar has Edit beside its title, which opens a picker under the labels: the
project's labels in their colors, each the item carries pressed, and a box that adds a label the project lacks. It
edits the item the title's Edit does. Pressing a label adds it or takes it off at once: the page posts it to
`/api/edit`, which writes it through `issue edit --add-label` or `--remove-label` as `you`, so a label such as
`backlog` or `idea` moves the card as that command does. The picker never offers the labels the store keeps as an
issue's state, `agent-ready`, `in-progress`, `waiting`, `needs-design`, and `on-hold`, which the card's buttons and
the agent's runs move, and the server refuses them. It refuses a typed name that isn't on one line, of at most 50
characters, without commas, with a 400 and the reason in a toast. Done or Esc closes the picker.

Each comment you wrote that didn't go to the agent has Edit in its header: a comment in a conversation, and a line
comment in Files Changed until Send to Agent carries it. Edit opens the description's edit box on the comment's
Markdown, which it reads from `/api/source?comment=<id>`, and Save posts to `/api/comment-edit`, which writes through
`comment --edit` as `you`. Save waits until the text differs and holds more than spaces. The server refuses a comment
changed since Edit read it, and passes on the store's refusal of a comment by someone else or one sent to the agent,
with the reason in a toast. An edited comment shows a muted `edited` after its time, with the edit's local time on
hover. A Decisions Needed answer you edit ties to the section its new first line names.

The header's New Issue button, or `i`, files an issue without a terminal. Its form fills the panel in place of the
selected card, as GitHub's new-issue page fills its page, with a project picker, a title, a Markdown body with the
comment box's Write and Preview tabs, a label picker, Parent and Blocked By fields, an Agent-Ready box, Create, and
Cancel. The picker starts on the open project's tab,
or on All on the selected card's project, or the first project when no card is selected, and any project can be picked.
The body starts from the project's task template when the form opens with an empty body: `GET
/api/issue-template?desk=<id>` reads `.github/ISSUE_TEMPLATE/task.md` in the project's main checkout without its YAML
front matter and HTML comments, or serves the create-issue skill's `## Goal`, `## Done When`, and `## Notes` headings
when the project has none. Picking another project swaps the body for that project's template only while the body is
empty or still holds the previous template untouched, so text you wrote stays. The label picker shows the project's
labels in their colors, picked with a press, and adds a new one typed by name. It never offers `agent-ready` or
`in-progress`, which the Agent-Ready box and an agent's run set. The Agent-Ready box, unchecked each time the form
opens, files the issue with `agent-ready`, so the dispatcher queues a run at once. Parent takes one issue number and
Blocked By several, as `#12, #15` or `12 15`. Create waits for a title, and Ctrl+Enter anywhere in the form presses
it. It posts to `/api/issue-create`, which files the issue, its body, its labels, and its links in one store write
through `lib/store_cli.py issue create --parent <n> --blocked-by <n>...` as `you` and answers 201 with its number. The
server refuses an empty title or one over 256 characters, a body over 65,536 characters, a label that isn't a name on
one line of at most 50 characters without commas, and a parent or blocker that isn't a positive integer, with a 400 and
the reason, and an unknown project with a 404. A parent or blocker the store doesn't hold comes back as the store's
refusal with a 409, and the store files nothing. The form keeps its text, and a toast gives the reason after the
project's name. After a create the page
reads the project's board, switching to its tab when another project's shows, then selects the new issue's card on its
Issue tab and clears the form. Cancel, Esc, or the form's X closes it, and the draft stays until a create or a reload,
so `i` shows it again. While the form shows, picking a card or stepping with `j` and `k` shows that card in its place.
Close an audit finding you reject as not planned with a comment saying why, and the next audit
learns a rule from it. Authors show as the store names them, `you`, `agent`, or `desk`, or by the login an import kept
for someone else. In place of a picture, each author's circle shows the login's first letter or digit on one of
eight colors that a hash of the login picks, so an author keeps a color everywhere, and the comment box shows yours. A
comment that sent work to the agent, in the conversation or on a line of a
review's diff, shows a 👀 once the dispatcher asks for its run, with a tooltip naming the run. While such a comment
waits for that answer, the panel reads the item again each time the store moves, so the 👀 shows without a reload. A
sidebar
shows the card's project, status, runner, labels, and its issue and reviews, and stays in view as the conversation
scrolls. Its Status shows a failing card's note, the check's first error, in red after a failed icon, and any other
note muted. Below the note, a Needs You card says how long it has waited on you, after a clock, with the time it
entered Needs You on hover. An issue with a parent, sub-issues, or blockers gets a Dependencies tab, whose count is its open sub-issues and
blockers. The tab lists the parent, the sub-issues with how many are done, the issues blocking it, and the issues it
blocks. Each row shows the issue's state, its group in the list, how many of its own sub-issues are done, and its open
blockers. A row's arrow opens it into its own sub-issues and open blockers, so you can follow the tree down, and its
title selects its card when the list shows one, or else shows the issue in the panel. Hide closed issues leaves only the work left. The
sidebar sums it up: the parent, the sub-issues done, and what blocks the issue. In the list, an issue with sub-issues
shows a ring beside its title that fills as they close, with how many are done, such as 6/13; clicking it opens the
Dependencies tab. Resting the pointer on a `#123` reference, or on a GitHub link to an issue or a PR in a project the
page shows, opens a hovercard, as GitHub's: the project and date, the state, title, and number, the start of the
description, and the labels. The panel reads each item from the store whenever it shows it, at most once a minute
while it stays open, and the label colors are the ones the server last read from the store for the board.

`agent-desk shots <review> <picture>...` saves each picture as a 1280-pixel-wide JPEG in
`~/.local/state/agent-desk/assets/<owner>__<repo>/`, named by the SHA-256 of its content, records it in the store's
`assets` table, and prints Markdown that shows it from `/api/asset/<hash>`. With `--comment` it also adds that Markdown
to the review as a comment. The server serves each picture under its hash with a year-long cache lifetime, since a
changed picture gets a new hash, and answers 404 for anything that isn't a 64-character hex hash the store holds. Each
run of the command also deletes the pictures of reviews closed more than 30 days ago, except the ones it just saved.
The server serves a picture only when its real path, with symlinks followed, lies inside the assets folder. It needs no network: it calls no
`gh` and pushes nothing. Descriptions and comments imported from GitHub that point at a repository's `pr-assets`
branch, where the command kept pictures before, still show them while the branch exists: the server reads them from
the main checkout's copy of `origin/pr-assets`.

Keys drive the list: `j` and `k` or the up and down arrows step through the cards, the left and right arrows step
through the open card's tabs, `o` or Enter shows the selected card in the panel, opening the panel when you closed it,
or on a failed or timed-out run's card shows its Agent Log tab when the run worked an issue, `m` merges, `f` fixes a PR, `a` labels an issue
Agent-Ready, `s` starts a queued issue in Manual, `z` unpicks one you started, `r` copies a stopped issue's Take Over command, `e` continues its agent,
`t` retries a failed run or continues a timed-out agent's, `d` dismisses one, `c` closes an issue as completed, `g` moves a Needs Design issue to
Open, `n` moves an idea, a backlog issue, or a queued issue to Open, `b` moves an Open issue to Backlog, `u` unblocks a Blocked issue, `p` marks a draft PR ready for review, `x` cancels a Queued run request that waits, `w` pauses an agent at work on an issue, `q` resumes an issue on hold, `[` moves a queued or waiting issue up the start order, `]` moves it down, `{` moves it to the top, `l` closes the review shown in the
panel, `i` opens the New Issue form, `\` shows or hides the Runners sidebar, `h` shows or hides the History column in
its place, the backtick (`` ` ``) shows or hides the Chat in its place, and `1` through
`9` pick a tab; `?`, or
the button at the header's right, lists them under the sections Move Around, Issues, Start Order, Runs, Reviews,
Panels, and Confirm and Cancel, which flow into as many columns as the window fits. The letter keys work in either case, so `M` with Shift or Caps Lock
merges as `m` does. Close shows on issues with no agent at work and no open PR: in Open,
Blocked, Backlog, Ideas, and Needs Design, queued and waiting ones in Queued, and stopped ones in Needs You. Merge, Fix, Retry, Continue, Dismiss, Close,
Close Without Merging, Unblock, Move to Open, Ready, and Agent-Ready ask
to confirm: press the same key again, `y`, or the button. Each press moves its card at once, the server holds back its
reads of the project's board while the command runs, and a command that fails takes the move back and raises a toast
after its project's name, such as "Acme: Couldn't close #12." A command that works raises none, since its card's
move shows it; a review's buttons and a chore's schedule show the server's answer on their own. Every button writes
the merge queue or the store before its command returns, so the read after it shows the move, Fix's included, as in
the pane. A chore shows as starting, and in Queued until a slot takes it. The page remembers the tab, the collapsed groups, each group's sort order, and the selected card. A new Needs you or
Needs design card raises a toast on the page. In place of the pane's Report button, the Resources view's Audit with
Claude button asks the Chat for `/agent-desk:resource-report`. Its
header's gauges, at the right, add the GPU's use and free memory to the pane's, from `nvidia-smi` on a machine with an
NVIDIA card, colored at the CPU's and RAM's lines.

Pressing the gauges pill, titled "Show what uses the machine's resources", shows the Resources view in the panel in
place of the card, as the New Issue form does, and opens the panel when it's closed. The pill shows pressed while the
view shows, and pressing it again, Esc, the view's close button, or selecting a card closes it. The view reads
`GET /api/resources` when it opens and every 10 seconds while it shows, and lists:

- **Memory**: the total, the memory free as `MemAvailable` reads it, the share the dispatcher keeps for you
  (`lib/memory.py`'s `reserveGB`, such as 8 GB on a 64 GB machine), what is free for runs above that, what running
  runs claimed and haven't taken up (Claimed by runs), the memory free less those claims (Free after claims), and the
  swap free. Free after claims is the free figure a waiting run's reason gives, which must cover the run's need and
  the share kept for you. Each tile's tip says what it counts.
- **Agent-desk runs**: each active `agent-desk-*` unit, largest first, with its kind, its project, and its issue, PR,
  or chore, linked to its card when the board lists one, its memory, swap, and CPU time from `systemctl --user
  show`, which count every process the run started, and its claim. The dispatcher's own unit shows under `dispatch`.
  The Claimed column shows what a run's [memory claim](#memory) counts for now, and its tip what the run claimed at
  start, its settle time or that it has none yet, and what the claim holds back beyond the memory the run's programs
  hold. A unit without a claim, such as the dispatcher, shows a dash. The Memory column counts file cache the kernel
  can take back, while a claim compares against the memory the run's programs allocated themselves (anonymous
  memory), so a run can show several GB in Memory and still hold back most of its claim.
- **Waiting for memory**: each run and branch check the dispatcher holds at its memory gate, in every project, with the
  reason it recorded, such as `memory: 5.3 GB free of 11 GB needed`.
- **Everything else**: the 15 processes outside agent-desk's units that use the most memory and swap, from `ps` and
  each process's `VmSwap`, grouped by the program their command line starts with, so a browser's tabs count with the
  browser.
- **Temp files**: what `/tmp` holds, from `df`, since it is a tmpfs that sits in RAM and swap.

The endpoint reads `systemctl`, `ps`, `df`, `/proc`, the store's runs and checks, and the claims through
`lib/memory.py claims`, the code the dispatcher's memory gate runs, and changes nothing. Freeing memory stays with the
Chat's `/agent-desk:resource-report`, which the view's Audit with Claude button runs: it shows the Chat when it's
hidden and sends the skill with the view's last figures, the memory free, the share the dispatcher keeps for you, what
is free for runs, what running runs claimed, the swap free, and each run waiting for memory with its reason, so the
report starts from what you saw. The Chat lists what uses the machine and what could be freed, and frees only the items you name in your next
messages. The button is disabled while the Chat replies, and its title says so.

The page draws itself with Preact and htm from `web/vendor/preact-htm.js`, htm 3.1.1's standalone build from npm, which
needs no build step. `web/app.js` draws the list and the chores, and handles the keys and the event stream. It imports
the rest from the modules beside it: `web/app-state.js` holds what the page shows and the presses that change it,
`web/app-parts.js` the project chips, icons, and labels several of them draw, `web/app-header.js` the header and its
mode buttons, `web/app-runners.js` the Runners sidebar, `web/app-detail.js` the selected card's panel,
`web/app-review.js` a review's files, line comments, and buttons, `web/app-decisions.js` a body's Decisions Needed
sections and their answer forms, `web/app-history.js` the History column, `web/app-chat.js` the Chat,
`web/app-new-issue.js` the New Issue form, `web/app-delete.js` Delete Issue and its dialog, `web/app-resources.js`
the Resources view,
`web/app-hovercard.js` the hovercards, and `web/app-help.js` the keyboard shortcuts dialog. No module imports `app.js`, so the imports form no cycle. `web/app.css` styles
the page, `web/app-header.css`, linked right after it, styles the header and its project tabs, `web/app-detail.css`
styles the selected card's panel, `web/app-chat.css` the Chat, and
`web/app-resources.css` the Resources view. `server.ts` serves the page's
files by name, and its tests check that it serves each file the page links and each module it imports.

`web/server.ts` keeps the desks, the store follower, the event stream, and the request handler. It imports the rest
from the modules beside it: `web/store.ts` opens the store read-only with `node:sqlite` for the newest event id, the
merged reviews, the runs' results, and the items, labels, links, comments, reviews, and checks the panel shows,
`web/history.ts` builds the History column's rows, `web/review.ts` builds the panel's previews, hovercards, and
relations from those and a review's files and its line comments' places from the main checkout's git, serves
pictures from that git, and posts comments and line comments through `lib/store_cli.py`, `web/issue-create.ts` files
the New Issue form's issue through it and reads the project's task template, `web/item-edit.ts` and
`web/comment-edit.ts` write the page's edits of a title, a description, labels, a task box, and a comment through it,
`web/issue-delete.ts` asks it whether an issue would delete and deletes one, `web/hold.ts` runs the hold controls through `lib/hold.py`, `web/chores.ts` starts and stops chores, counts
their stats, and opens the schedule reviews, `web/card-actions.ts` carries out a card's buttons and a review page's
buttons, `web/chat.ts` runs the Chat's turns and reads its transcript, `web/resources.ts` reads what uses the machine's memory
for the Resources view, `web/peer.ts` checks that a request comes from this
user's own page, and `web/state.ts` holds what they share,
the desks and the host that runs programs. `web/markdown.ts`, which `web/review.ts` calls for every body, renders GitHub-flavored Markdown with marked from
`web/vendor/marked.esm.js` and cuts the HTML to an allow-list of tags, attributes, and `http`, `https`, and same-origin
addresses, since a body that ran script on the page could press its buttons. `web/decisions.ts` cuts a body at its
Decisions Needed sections through the same renderer and reads an answer comment's first line. The server runs every
program without `GH_REPO`, which a run sets to its own project, so each desk's commands read the project of the
desk's main checkout even when an agent starts the server inside a run. It also
runs them without `AGENT_DESK_RUN` and `AGENT_DESK_AUTHOR`, so the store stamps what the page writes as `you`.

The server and the pane read the same board from `inbox/hooks/board.ts` and share the modules beside it: `moves.ts`
redraws the board the moment you press a button, `actions.ts` runs what the buttons do, `load.ts` reads the machine's
load, `notes.ts` reads the notes agents post on an issue, `units.ts` draws the runs the dispatcher holds and says how
long a run has left, and `run.ts` holds the helpers the
others run programs with and the store reader.
None of them imports Claude Code or Node: the server and the pane each pass a host that runs programs, tells the time,
and keeps a few values. Each row carries its text, which the
pane draws, and the parts the page draws. While a page is open, the server samples the machine's load every 10 seconds
and reads the store's newest event id every half second. When the id moves, it reads the projects file again and the
boards of the projects whose events came since its last read, and pushes them to every open page over `/api/events`,
so a press on the page or a command in a terminal shows within a second. On the same tick it checks when
`lib/hold.py` last saved `hold.json`, and when `agent-desk hold`, `agent-desk resume`, or `hold.py` changed the mode
or the picks, it reads every board again, so each queued issue's Start button and note follow the mode the header
shows. Once a minute it also reads the projects file
and every board again, so a project you add shows and a run's age and time left move without an event. Each state it
sends carries the event id its boards were read after as its server-sent event `id`. The page holds one stream;
EventSource retries a dropped one with the last id it saw, and once it gives up the page opens a new one with
`?after=<id>`. Unless the server's boards are as new as the store, as they are not in a server that just started, the
first state waits for them to catch up, so the page never draws a board older than one it showed. Every move you make on
the page reads its project's board at once. With no page open the server reads nothing. The server shows above the
projects why a board couldn't be read, and says so there when it can't read the store, logging the first error of each
streak. It opens the store again when another file replaces it, and reads that file's events from its first. Each
project's board is one read of the local store, which gives the label colors and the chore PRs' states with it. Each
label keeps the color `agent-desk import` copied from GitHub or `agent-desk label edit` gave it, and any other label
takes a color hashed from its name. It keeps the failed runs you dismiss in `~/.local/state/agent-desk/web.json`, apart from the pane's.

The server queues merges, marks reviews ready, closes them, and writes comments to the store as you, and any page in
your browser can send requests to this machine, so it listens on 127.0.0.1 only and refuses a request whose Host header names anything but itself, a POST from
any other origin, and a POST that isn't JSON. No other page may frame it. Any account on the machine can reach
127.0.0.1 and send those headers itself, so the server answers only your own processes: when it accepts a connection,
it finds the connecting socket in `/proc/net/tcp` and refuses every request on it with 403 when another uid owns that
socket or the table doesn't list it. The page loads no script from elsewhere, and
loads pictures only from itself; it drops scripts, styles, and event handlers from the HTML
the server renders before showing it. A description or comment shows only the pictures the server serves, such as the
repository's own screenshots; any other picture, such as an imported GitHub attachment, shows as a link with its alt
text, so opening an item fetches nothing from another host. A click on a picture the server serves opens it over the
page at its natural size, shrunk to fit the window; Escape, the close button, or a click beside it closes it. A
Ctrl-click, Cmd-click, or middle-click opens it in a new tab instead, and a picture inside a link the author wrote
follows that link.

## The local store

`lib/store.py` keeps issues, reviews, comments, runs, and events for every project in one SQLite database,
`~/.local/state/agent-desk/desk.sqlite`. The [dispatcher](#the-dispatcher) reads it and records its runs there,
`agent-desk work-issue`, `agent-desk work-pr`, `agent-desk clean`, and `agent-desk release` read
and write their issues, reviews, and notes there, and the pane and the page build their board from it. The board's
buttons act on the store through `agent-desk`.

`lib/store.py`'s `Store` inherits its import methods from `lib/store_import.py`, its branch and check methods from
`lib/store_checks.py`, its run methods from `lib/store_runs.py`, its GPU call records from `lib/store_gpu.py`, and its
board read from `lib/store_board.py`, and
`lib/store_schema.py` holds the migrations, the values the status columns take, and the time format. `lib/store.py` imports each of their names back, so every
caller imports `store` alone.

`agent-desk issue` and `agent-desk review` read and write it through `lib/store_cli.py`, the store's command line,
with the verbs and flags the scripts pass to `gh` today:

```bash
agent-desk issue create --title "Add a store" --body-file body.md --label serial:web --blocked-by 12
agent-desk issue list --label agent-ready --json number,labels | jq '.[].number'
agent-desk issue list --state all --since 2026-11-12T09:30:00Z
agent-desk issue edit 14 --add-label in-progress --remove-label agent-ready
agent-desk issue close 16 --duplicate-of 14 --comment "Same bug as the store's."
agent-desk review create --branch agent/issue-14 --title "Add a store" --draft --fixes 14
agent-desk review view agent/issue-14 --json number,isDraft,headRefOid
agent-desk review checks 15 --watch
agent-desk review merge 15 --sha "$(git rev-parse main)"
```

`agent-desk issue close <n>` closes an issue with `--reason completed`, the default, or `not-planned`, and posts
`--comment` or `--comment-file` in the same write. `--duplicate-of <m>` closes it with the reason `duplicate`, posts
`Duplicate of #<m>` before the comment, and stores a `duplicate` link from the issue to `#<m>`, which its page reads to
link the original. The command refuses, writing nothing, when `#<m>` isn't an issue of the project or is the issue
itself. Every close ends each agent request that still waits for the issue as `cancelled`, through
`cancel_waiting` in `lib/run_control.py`, the function `unqueue` uses, and then drops the issue's Manual pick, so a
reopened issue waits for Start again; a run that already works the issue keeps working. `agent-desk issue reopen <n>`
drops the `duplicate` link.

`agent-desk label edit <name> --color <hex>` gives a label its color, such as one created on this machine that would
otherwise take a hashed color, and `agent-desk label list` prints the project's colors. The next import replaces a
project's colors with GitHub's, which drops a color for a label GitHub doesn't have.

`--json` takes a comma-separated list of fields and prints them under the names gh uses, such as `labels[].name`,
`comments[].body`, `headRefName`, and `closingIssuesReferences`, so a script pipes the output to `jq` where it passed
`--jq`. The project is `--repo <owner/repo>`, or `GH_REPO`, or the one the origin of the checkout the command runs in
names when the origin is a host address. A project renamed on GitHub needs `agent-desk project rename` to move what
the store and the state folder hold to the new name, as [Renaming a project](#renaming-a-project) describes. A
checkout whose origin is a path, or which has no remote, takes the project `~/.config/agent-desk/projects` records at
the checkout or at its main checkout. A worktree of such a project so finds its project, as do the pane and the web page, which run
`agent-desk board` in the project's checkout.

The store follows these rules:

- **Numbers.** Issues and reviews share one sequence per project. An item created with a number keeps it, and a new
  item takes the number after the highest the project ever held, so a deleted issue's number never names another.
- **Deletes.** `issue delete <n> --yes` deletes an issue filed by mistake in one write: it cancels the agent requests
  that wait for it, removes its labels, its comments, its links in both directions, such as another issue's `blocked
  by` or `parent`, and the issue, writes an `item-deleted` event, which the dispatcher reads as it reads a link's
  removal, and drops the issue's Manual pick. Without `--yes` it prints what it would remove and changes nothing.
  The store refuses an issue with work behind it, and says why and to close it instead: one a review fixes or with a
  review on its `agent/issue-<n>` branch, one whose branch or worktree the main checkout has, one an agent run works,
  and one with sub-issues. `projects.next_number` stays, and the events before the delete stay too.
- **State.** An issue's workflow state is one value: `open`, `queued`, `in-progress`, `waiting`, `needs-design`,
  `on-hold`, or `closed`. `--json status` prints it, and `state` prints `OPEN`, `CLOSED`, or `MERGED` as gh does. The
  labels that name those states on GitHub (`agent-ready` for `queued`, the others by their own names) still work in
  `--add-label`, `--remove-label`, and `--label`, where they move or match the state and never appear in `labels`.
  The labels that group work, such as `serial:web` and `benchmark`, stay labels.
- **Links.** `--parent` makes an issue a sub-issue, `--blocked-by` and `--add-blocked-by` name its blockers, and a
  review's `--fixes` names the issues its merge into main closes. A create files the item with its labels and links
  in one transaction, so one whose link fails files nothing.
- **Checks.** `review checks` reads the `checks` rows of the commit the review's branch points at in the main
  checkout, counting the newest row of each name. It exits 0 when they passed, 1 when one failed, and 8 while one is
  queued or running or none has started. With `--watch` it prints each change and returns once none waits, giving up
  after five minutes without a check, and a project whose `.agent-desk.json` sets no `test` command returns 0 at
  once.
- **Events.** Every write appends rows to `events` in its own transaction, so a reader learns of any change by
  watching the newest event's id, as the web page's server does. An import is the exception: it writes one event for
  the whole run. Once a write with events commits, it saves the newest id in `desk.sqlite-event` beside the database,
  for a reader that doesn't open SQLite, such as [the pane](#the-pane). Each save reads the id and replaces the file
  under a lock on `desk.sqlite-event.lock`, so the file's id never goes back.
- **Run results.** When a run's agent finishes, `lib/run_log.py record` stores what it reported on the run's row,
  which `AGENT_DESK_RUN` names: the model and effort, the minutes, turns, and cost from the stream's `result` event,
  the review it opened or updated, an agent run's issue labels, and why it started, with a `run-result` event. The
  row's `gpu_queued` and `gpu_held` sum the minutes its [GPU calls](#gpu-slots) queued and held, as each call ends.
  `lib/run_log.py import` copies the runs of the log kept before the store, as [Chore history](#chore-history)
  describes.
- **Privacy.** Opening the store creates the state folder, `~/.local/state/agent-desk` or `agent-desk` under
  `XDG_STATE_HOME`, with mode 0700, and narrows it to 0700 when group or others can enter it. The folder's mode guards
  the database, check logs, snapshots, screenshots, and everything else under it, so the files keep the umask's modes.
  It leaves the folders above it alone. `agent-desk doctor` warns about a state folder that group or others can enter.
- **Writes.** The database runs in WAL mode with a 5-second busy timeout, and every write opens with
  `BEGIN IMMEDIATE`, so concurrent writers wait for each other.
- **Versions.** `PRAGMA user_version` holds the schema's version. A process running older code than the database
  reads it and refuses to write, and each write checks again, so a service that outlives an update stops writing.
- **Migrations.** The first process to open the database with newer code migrates it, and creates it when it doesn't
  exist. For the live database, `~/.local/state/agent-desk/desk.sqlite` in the account's home, only the installed
  checkout's code does either: the checkout `agent-desk` on PATH links into, or the one `AGENT_DESK_INSTALLED` names.
  `agent-desk dispatch install`, `web on`, `chores on`, and `backup install` write that variable into their units,
  whose PATH may lack the command, `./install.sh` sets it in the units that exist, and the dispatcher passes it to the
  units it starts. Code from a worktree stops with an error naming the database, the checkout, and the fix, unless it
  runs with its own `XDG_STATE_HOME` or opens a database by its path, as the tests do.
- **Read-only commands.** `agent-desk board`, `issue view`, `list`, and `delete` without `--yes`, `review view`,
  `list`, and `checks`,
  `queue order` without a move, and `lib/dispatch.py runs`, `chore-runs`, and `stop --dry-run` open the database with `mode=ro`. They never create,
  migrate, or write it, and set no journal mode. They read an older database as far as its tables allow, and read a
  missing one as empty.
- **Start order.** Each project's `queued` and `waiting` issues hold a place in one start order, `items.rank`, which
  `agent-desk queue order` prints as one issue a line with its place, number, status, and title, and `issue view
  --json position` prints from 1, or null outside it. `queue order <n> --up`, `--down`, `--top`, or `--to <place>` moves
  issue n, the last for a place past the end, writes a `queue-moved` event, and refuses an issue in any other state. The backup leaves ranks out, so a restored or
  imported queued issue without one joins the end by number.
- **Line comments.** `review comment --path <file> --line <n>` comments on line n of the file in the review's new
  side, and `--head-sha` names the commit it was made on, by default the review's newest head. An issue takes no line
  comment.
- **Comment edits.** `issue comment <n> --edit <comment> --body-file <file>`, and the same on `review`, replaces a
  comment's body, sets its `edited_at`, which `--json comments` prints as `lastEditedAt`, and writes a
  `comment-edited` event, which starts no run, even when the new body mentions `@agent`. `view` prints each comment's
  id beside its author. The store refuses a comment whose author isn't the writer's, as **Authors** names it, so you
  edit only yours and an agent only the agent's, and one sent to the agent, on its own or quoted by a Send to Agent,
  since it records what the agent read. An edit by the agent or the desk needs `--note <what changed>`, and yours
  takes one too: the store adds the note to the body as `**Edited <date> <time> UTC:** <note>`, above the closing
  lines agent-desk signs a comment with, so the comment itself says what changed. `--edit` takes none of
  `--to-agent`, `--quotes`, `--path`, `--line`, and `--head-sha`.
- **Authors.** A comment or event written inside a run, where `AGENT_DESK_RUN` is set, carries the author `agent`.
  One from agent-desk's own scripts carries `desk`, and any other carries `you`. The scripts write through
  `desk_store` in `lib/common.sh`, which records `desk` even inside a run, so a note in the store needs none of the
  hidden `<!-- agent-desk -->` mark that agent-desk's GitHub comments carry.

### Importing from GitHub

`agent-desk import <owner/repo>` copies a GitHub project into the store, keeping every issue's and PR's number, so
the [cutover](#cutover) can import one last time before the desk stops reading GitHub. `lib/import_github.py` holds every GitHub
name, so an importer for another host can sit beside it. It copies:

- every issue and PR, open and closed, with its title, body, timestamps, and labels, and the color of each of the
  repository's labels, which the web page draws. A PR becomes a review with its
  head branch, base, head commit, draft flag, and merge commit, and for a rebase-merge, how many commits it wrote
  onto the base. GitHub names no merge method, so a merge with one parent whose commit repeats the PR's last commit's
  message and author date, or is that commit, counts as a rebase. Rerun the import to count the rebase-merges of PRs
  imported before it did.
- the workflow labels as the state: `agent-ready` as `queued`, and `in-progress`, `waiting`, `needs-design`, and
  `on-hold` as themselves. An open issue with several takes the first of `in-progress`, `needs-design`, `waiting`,
  `on-hold`, and `agent-ready`.
- issue and PR comments, review summaries, and review line comments, each with its GitHub id. A line comment keeps the
  line and commit GitHub shows it at, or the ones it was written on once later commits outdated it, and counts as
  sent, since the agent read it on GitHub.
- sub-issues as `parent` links, blockers as `blocks` links, and the issues a PR closes as `fixes` links. Links to
  another repository's issues stay behind.

A comment that ends with agent-desk's mark takes the author `desk` when it opens the way the desk's own notes do,
such as "An agent started on", and `agent` otherwise. Other text from your account takes `you`, and anyone else's
keeps their login. Only a comment from you that mentions `@agent` imports as sent to the agent, so another account's
`@agent` comment arrives as an ordinary comment. Pictures keep their GitHub URLs.

The import reads issues and PRs a GraphQL page at a time and the repository's label colors and comments 100 to a REST
call, and writes
each page as it arrives without events, then writes one `imported` event for the run. A rerun adds what is new,
updates what changed, and copies nothing twice. When a read fails partway, the import keeps what it wrote, prints
what it added, and exits non-zero with GitHub's message. A full import of a project with about 2,000 items and 3,300
comments takes under a minute and costs about 64 GraphQL points of the 5,000 an hour and 35 REST calls.

`review merge` records a merge that already happened; it runs no git command.

### Cutover

`agent-desk cutover <owner/repo>` makes the local store a project's tracker for good, and `agent-desk cutover --all`
does it for every project in `~/.config/agent-desk/projects`, one after another. It takes each project's main checkout
from that file alone, and stops when the checkout's `origin` names another repository. `lib/cutover.py` refuses to
start unless the project is on Hold, with no run working and its merge queue empty, and says to run
`agent-desk hold <owner/repo>` first. Then it takes these steps in order, prints each, and stops at the first failure
with the step named:

1. **import**: imports the project one last time, as `agent-desk import` does.
2. **reviews**: checks that every open GitHub PR from one of the project's own `agent/` branches has an open review
   in the store and its branch in the main checkout, holding every commit of the PR's head. It fetches a branch the
   checkout lacks, and stops with a list when a PR has no open review or GitHub holds commits the local branch lacks,
   with the command that updates the branch, or the worktree to pull it in when the branch is checked out there.
3. **statuses**: checks that each open issue GitHub labels `in-progress`, `waiting`, `needs-design`, `on-hold`, or
   `agent-ready` has that state in the store, and prints how many open issues hold each state.
4. **record**: records the cutover in the store as a `cutover` event. The store is the project's tracker from here on.
5. **agents**: adds a line naming `agent-desk issue` and `agent-desk review` to the Agent desk section of the
   project's AGENTS.md, or its CLAUDE.md, and commits it in the main checkout. When neither file has the section, the
   file has uncommitted changes, or the checkout is off `main`, has staged changes, or has a merge, rebase,
   cherry-pick, or revert in progress, it changes nothing and prints the line for you to add. The line wraps at 120
   columns with a two-space hanging indent, as agent-desk's own AGENTS.md wraps it. A commit that fails, such as on a
   pre-commit hook that rejects it, puts the file back as it was, prints the line for you to add, and goes on to the
   next step. A rerun skips the step once the section names `agent-desk issue`.
6. **workflows**: disables the agent, agent-cleanup, chores, fix, and test workflows on GitHub, leaving their files.
7. **pulls**: comments on each open GitHub PR from an `agent/` branch with the number of its open local review, and
   closes it. The branch stays. Every other open PR, such as a person's, a bot's, or a fork's, stays open on GitHub,
   and the summary lists it.
8. **issues**: leaves Issues as they are on GitHub, so the issues there stay readable and links to them keep
   working. The desk reads only the store from here on, so an issue filed on GitHub after the cutover reaches no
   board. With `--issues-off`, the cutover turns Issues off on the GitHub repository, which hides them without
   deleting them and returns a 404 for every link to one.

The GitHub changes come last, so a failure before them leaves GitHub as it was. A project whose `host` is `none` skips
every step that reads or changes GitHub. A rerun finishes a cutover that stopped partway: once the cutover is recorded,
it skips the import and the checks, and each later step skips what it finds done, so a disabled workflow stays disabled,
Issues already off stay off, and a PR that already has its pointer gets no second one. Each GitHub change records its
intent in the store before the call and its result after, so a rerun records a change that a killed run made without its
result, and the undo reverses it too. The import writes no `comment-added` events, so the dispatcher asks for no run for
an imported comment that mentions `@agent`. The cutover leaves `host` in `.agent-desk.json` alone: a project that still
pushes `main` to GitHub keeps `github`.

`--dry-run` opens the store read-only and refuses one whose schema is older than the code's, since only the installed
agent-desk migrates it. It reads GitHub and the store, prints each step it would take, including why it would refuse,
and changes nothing. After the last project, the cutover restarts the dispatcher when its service runs, says how to
restart the web page's service and the backup timer on the new code, or how to turn on any that is off, and leaves a web
page you run from a terminal alone. It ends with a summary: what changed on GitHub, what the store holds, and what is
left for you.

On cutover day:

1. Run `agent-desk hold` and let the runs finish and the merge queues empty.
2. Tag `main` as `pre-local-desk`, and merge `local-desk` into `main`.
3. Run `agent-desk cutover --all --dry-run`, then `agent-desk cutover --all`.
4. Turn on the dispatcher and the backup timer with `agent-desk dispatch install` and `agent-desk backup install` if
   the summary says they are off, and run `agent-desk resume`.
5. Create, start, merge, and back up one issue as a check.

Rehearse first against a scratch project with a scratch remote.

`agent-desk cutover --undo <owner/repo>`, or `--rollback`, turns Issues back on when the cutover turned them off,
enables the workflows the cutover disabled, reopens the PRs it closed, and lists the issues and reviews filed in the
store since the cutover, which `agent-desk issue list --state all --since <time>` and `agent-desk review list --state
all --since <time>` also print. Refile them on GitHub by hand. A refiled item takes a new GitHub number, since GitHub's
counter stopped at the cutover, so note the new number on any item a commit cites by its local one. The undo records
itself in the store, so a later cutover starts over with a fresh import. The workflows it enables need self-hosted
runners and an `agent-desk` command that calls them back, which this agent-desk lacks, so the undo says to check out
its `pre-local-desk` tag and install the runners with that version's `agent-desk runners install`.

### Renaming a project

`agent-desk project rename <owner/repo> <new owner/repo>` moves a project to a new name, such as after a rename on
GitHub, keeping its issues, reviews, comments, runs, checks, and state. `lib/rename.py` refuses to start unless the
project is on Hold, with no run working, no branch check running, and its merge queue empty, and refuses when the new
name belongs to another project in the store or in `~/.config/agent-desk/projects`. A queued check, which Hold keeps
from starting once no run works, moves with the project and starts under the new name on Resume; the check step names
each one. Then it takes these steps in order, printing each, and stops at the first failure with the step named:

1. **store**: rewrites the name in every table with a `project` column and in `projects.slug`, moves each
   `checks.log_path` and `assets.path` in the project's `<owner>__<repo>` folder to the new folder, and writes a
   `project-renamed` event naming both names, in one `BEGIN IMMEDIATE` transaction.
2. **files**: moves the project's `<owner>__<repo>` folders in `chores/`, `checks/`, and `assets/`, and its files and
   locks in `fixes/`, `merges/`, and `pushes/`.
3. **hold**: renames the project's mode, its slot overrides, and its Manual picks in `hold.json`, and its queued
   chores in `chore-repeat.json`.
4. **projects**: changes the project's line in `~/.config/agent-desk/projects`.
5. **remote**: points the main checkout's `origin` at the new name with `git remote set-url` when it is a host
   address naming the old one, so the checkout and its worktrees resolve to the new name at once. An `origin` that is
   a path, or none, stays.
6. **backup**: moves the backup repository's `<owner>/<repo>/` folder to the new name with `git mv` and commits the
   move. A file a backup already wrote under the new name replaces the old one, since that backup read the renamed
   store.

`boards/` is keyed by the checkout's path and `claims/` by process, so they stay, and the
history in `usage.tsv` and `runs.jsonl` keeps the name each line was written under. A rerun after a failure finishes
what is left: each step skips what it finds done, and the `project-renamed` event tells it that the new name in the
store is this project's. A file that differs under both names stops the files step until you keep one. `--dry-run`
opens the store read-only, prints each step it would take, including why it would refuse, and changes nothing.

Rename the repository on GitHub first: GitHub redirects the old name to the new one, but a remote pointed at the new
name before GitHub has it fails to fetch and push. Run `agent-desk resume` once the rename finishes.

### Removing a project

`agent-desk project remove <owner/repo>` takes a project off this machine's desk, so it leaves the dispatcher, the
timers, the pane, and the web page. `lib/remove.py` refuses to start unless the project is on Hold, with no run working
or waiting, no branch check running, and its merge queue empty, and names each reason with the commands that clear
it. A queued check, which Hold keeps from starting once no run works, goes with the project's rows; the check step
names each one. Then it takes these steps in order, printing each, and stops at the first failure with the step named:

1. **store**: deletes every row with the project in a table with a `project` column, and its `projects` row, and
   writes a `project-removed` event, which names no project so every reader reads every board again, in one
   `BEGIN IMMEDIATE` transaction.
2. **files**: deletes the project's `<owner>__<repo>` folders and files in the state folder, the ones a rename moves,
   and the files under its main checkout's path in `boards/`, where the pane once kept its board.
3. **hold**: drops the project's mode, its slot overrides, and its Manual picks from `hold.json`, and its queued
   chores from `chore-repeat.json`.
4. **projects**: drops the project's line from `~/.config/agent-desk/projects`.
5. **backup**: deletes the backup repository's `<owner>/<repo>/` folder with `git rm` and commits it, so the backup's
   history still holds the project.

agent-desk never deletes your code: the command leaves the main checkout, its worktrees, its branches, and its remote
as they are, and prints the worktrees `git worktree list` shows besides the main checkout, so you can delete them. A
rerun after a failure finishes what is left, since each step skips what it finds done, and a project with no mode of
its own left in `hold.json` counts as held once the store holds none of it. A name that neither the store, the projects
file, nor anything a failed run left holds exits 1. `--dry-run` opens the store read-only, prints each step it would
take, including why it would refuse, and changes nothing. Run `agent-desk install` in the checkout again to put the
project back on the desk with an empty store.

### The board's read

`agent-desk board --json`, which `lib/board.py` prints, is the one command a refresh of the pane or the page runs, and
it asks GitHub nothing. It reads the store once and the files the timers saved, and prints:

- `issues`: each open issue with its workflow state as `status`, its labels, its blockers with their states, and how
  many of its sub-issues closed. An issue in progress also carries its comments, whose notes say whether its agent
  finished, stopped, or works in a session here, and `landed`: the base its `agent/issue-<n>` branch last merged into,
  with that base's own review, which tells a stacked issue that waits on its base from one that merged with it.
- `reviews`: each open review with its branch, base, head, and draft flag, the checks of its head, and `mergeable`. The
  head is the branch's row in the `branches` table, or the review's recorded head without one. Its checks are the
  newest attempt of the head's [branch check](#branch-checks), leaving out superseded and cancelled ones; a failed first
  attempt stands while its second waits and runs, so the review shows failing until the second passes. `mergeable` is
  whether the head merges with the review's base, as the dispatcher last tried it with `git merge-tree`; before a try,
  it is `CONFLICTING` once a check couldn't merge main, `MERGEABLE` once one got past the merge, and `UNKNOWN` before.
  `agent-desk review view --json checks,mergeable` prints the same two fields, which Fix reads.
- `merged`: the three newest merged reviews.
- `runs`: the runs that wait or work, and those that failed, timed out, or were lost in the last day, as
  `lib/dispatch.py runs` lists them.
- `asked`: the open reviews, by number, with a comment sent to the agent that the dispatcher hasn't answered yet, as
  Fix posts. The board shows each from an agent branch in Queued, as it shows a review whose agent run waits, until
  the dispatcher's answer replaces the comment with the run's request, or with a note for a branch no agent works.
- `checks`: the [branch checks](#branch-checks) running now, each with its branch, its review's number or null, the
  head it checks, and when it started.
- `slots`: the project's slots of each kind, its config's [`slots`](#the-config) over the defaults and the overrides
  `agent-desk slots` sets over both, as the [dispatcher](#the-dispatcher) reads them.
- `queue`: the [merge queue](#merge-queue) and the reviews that left it, as the last `agent-desk merges` tick saved
  it, with `ahead` and `push` from the [push queue](#pushing-main) when the store knows the project's main checkout.
- `fixes`: what each conflicting PR's [automatic fix](#conflict-fixes) waits on, as the last `agent-desk fixes` status
  or tick saved it in `~/.local/state/agent-desk/fixes/<owner>__<repo>.plan.json`.
- `hold`: the desk's mode and the issues and requests picked for Manual, as `lib/hold.py status` prints them.
- `chores`: the table the last `agent-desk chores status` saved in
  `~/.local/state/agent-desk/chores/<owner>__<repo>/status.txt`, which the chore timer runs every 5 minutes.
- `at` and `machine`: the time with its zone, and this machine's name, which a session's note names.
- `web`: the address the [web page](#the-web-page) serves at, which every issue, review, and merged row links to:
  `http://127.0.0.1:<port>` at `AGENT_DESK_WEB_PORT`, or else at the port of the service `agent-desk web on`
  installed, or else at 7480.

Beyond that command, a refresh runs `lib/failures.py log` once for each failed check's log it hasn't read, and `ps` for
each issue a session on this machine claimed with `agent-desk start`. `tests/test_board.py` checks the command's output
against `inbox/hooks/board.golden.ts`, which the pane's tests build the board from, so a field renamed on either side
fails a suite.

Without `--json` it prints a line for each open issue and review.

## The dispatcher

`agent-desk-dispatch.service`, a systemd user service, starts each run the store asks for as a transient systemd
unit with its own cgroup, journal log, and time limit, inside [`agentdesk.slice`](#memory).

```bash
agent-desk dispatch install            # write agentdesk.slice, and write, enable, and start the service
agent-desk request agent 12            # work issue 12
agent-desk request agent 12 --benchmark
agent-desk request agent pr-15         # go back to PR 15 from an agent/<slug> branch that works no issue
agent-desk request fix 15
agent-desk request chore audit
journalctl --user -u agent-desk-dispatch
```

`agent-desk request` adds a row to the store's `runs` table as `requested`, with a `run-requested` event, and prints
the run's id. `--env NAME=VALUE` passes `REPLY_PR`, `REQUEST`, or `EVENT` to the run.
The dispatcher reads the newest event id every 200 ms and starts a request once all eight gates clear, in this order,
so a request held by the mode or by other issues never takes a slot:

1. **The mode.** The desk's [mode](#auto-manual-and-hold) for the project lets it start. On Hold, every request
   waits in the store and starts on Resume. In Manual, every agent, fix,
   and chore request waits until Start picks it: a fresh start, a request with `EVENT=issues` that Agent-ready,
   Retry, Continue, or the dispatcher's wake made, by its issue or its run id, and any other request, such as a
   comment's run, Fix, a chore, or a merge-queue fix, by its run id. Branch checks pass this gate.
2. **The weekly usage limit.** While the [weekly usage limit](#the-weekly-usage-limit) is on, the Claude plan's 7-day
   usage reads under it. At or over it, every agent, chore, and fix request in every project waits, with a note such
   as `weekly plan usage is 81%, at the 80% limit; resets Wed 04:00`. The retry every 15 seconds starts it once a
   reading falls under the limit, as after the weekly reset, with no event. The branch checks spend no Claude tokens
   and never wait on it.
3. **Hold.** For an agent request on an issue, the issue isn't `on-hold`. A request for an issue on hold, such as one
   a comment sent to the agent asks for, waits until the issue leaves the state. Queued shows it as waiting for
   `#<n> to resume`, with Resume beside Cancel run.
4. **Blockers.** For an agent request on an issue that a label, the dispatcher's wake, or `agent-desk serial release`
   made, with `EVENT=issues`, no open issue blocks the issue through a `blocks` link in the store. A request from an `@agent`
   comment on an issue already worked goes on past its blockers.
5. **Serial groups.** For an agent request on an issue labelled `serial` or `serial:<group>`, no other issue in one of
   its groups is `in-progress` or works in a running agent run, by `lib/serial_slots.py`'s `holder` rule.
6. **A slot.** The project runs fewer runs of the kind than its [`slots`](#the-config) and their overrides allow, and for a `--benchmark`
   request fewer benchmark runs than `slots.benchmark`. The `check` slots hold the [branch checks](#branch-checks).
7. **The target.** No other run holds the same target. An agent run on issue or PR n and a fix of PR n share one
   target, `agent-<n>`, and a chore holds `chores-<skill>`. A target's later
   requests wait behind its earliest, in the order they came.
8. **Memory.** `lib/memory.py` finds your apps out of memory pressure and the [memory](#memory) spare for the
   run's need, as `desk_memory_start` does, and the dispatcher claims the need under the unit's process, so the run's
   own memory check starts at once and adds the run's settle time and peak file to the claim. The dispatcher's note
   on a waiting request names the check that holds it. After `resources.maxWaitMinutes` a request starts anyway. Every
   30 seconds the dispatcher logs one line for each run whose claim has shrunk since, as [Memory](#memory) describes.
   A fix the [merge queue](#merge-queue) waits on passes this check more easily, as [Memory](#memory) describes, and
   while one waits for memory, every other request in every project waits behind it, even one past
   `resources.maxWaitMinutes`, so a slot or memory that frees goes to the fix first.

The waiting requests try their gates with the merge queue's fixes first, unless an earlier request holds a fix's
target, and then oldest request first, except the fresh starts of a project's queued and waiting
issues, which trade places among themselves to follow the project's start order: the store keeps one order per project
of its issues in either state, in `items.rank`, which `agent-desk queue order` prints and moves. An issue joins its end
when it enters `queued` or `waiting`, keeps its place as it moves between them, and leaves when it starts, closes, or
goes back to `open`, so the next free slot goes to the first issue in the order whose gates clear. Every other
request, such as an `@agent` comment's or a chore's, keeps its place by request time. A move writes a `queue-moved`
event, which makes the dispatcher try its waiting requests again. The wake and `agent-desk serial release` hand a free
serial slot to the group's first waiting issue in the same order, through `lib/serial_slots.py`'s `release`.

`lib/dispatch.py runs` prints the project's requests that wait and runs that work as JSON, and `agent-desk board --json`
carries the same list, which the board reads on each refresh. The pane and the web page show a run within about a second
of the store event that moves it. A working run shows in Runners under its kind, `agent`, `fix`, or `chore`, with its
issue or PR, and a waiting request's issue shows in Queued. A run that failed, timed out, or was lost shows in Needs You
for a day, until a later run asks for its target or its issue or PR closes; Retry asks for the run again with
`lib/dispatch.py again <run>`. Continue on a stopped issue asks the dispatcher for the run again.
`lib/dispatch.py chore-runs` prints each chore's newest run for the [chore](#chores) schedule, the run that works ahead
of a request queued behind it.

A request that can't start waits and starts when the gate clears. The dispatcher records the gate that holds it in
the store's `runs` row, as `wait_gate`, one of `mode`, `usage`, `hold`, `blocker`, `serial`, `slot`, `lock`, or `memory`, with
`wait_reason`, a short reason that reads after "waits for", such as `memory: 5.3 GB free of 9 GB needed`, `a free fix
slot`, or `run 612 ahead of it`. It writes no event for them, so the board picks them up on its next read. It rewrites
the record and says why in its log when the gate or reason changes, except that a memory reason whose GB free moved by
less than half a GB stays as it was, and it clears the record when the run starts or ends. `lib/dispatch.py runs` lists
each waiting request's `gate` and `reason`, and the board shows them on the cards in Merging and Queued. A queued
branch check that Hold or a full `check` slot holds records its gate and reason in its `checks` row the same
way, which `agent-desk board --json` lists as `waitingChecks`. The first
time a request waits on a blocker or a serial slot, the dispatcher moves its issue to `waiting` and says why in a note
on the issue. A `state-changed`, `label-added`, `label-removed`, `link-added`, or `link-removed` event makes it try
every waiting request again at once, so a blocker closing or a serial holder's review merging starts the next issue
with no timer. On those events, at an agent run's end, and on each 30-second reconcile, the dispatcher also asks for
a run of each `queued` issue that no agent run was asked for since it last moved to `queued`, and that no request
waits for or works, as an `agent-ready` label added from the command line leaves it. It asks with `EVENT=issues`, as
the label's own request does, so Manual holds it for Start. A queued issue whose request was cancelled or failed to
start has had its request, so the dispatcher asks for none again until the issue moves to `queued` once more. It
then asks for a run of each `waiting` issue that has no request, once nothing blocks it and no issue or request holds
its serial slot. In Manual, a request that waits for Start holds no slot, and a waiting serial issue gets a request
only once it is picked; a pick wakes the dispatcher at once. It moves the issue to `queued` first, so a run that fails
to start again leaves the issue there.
Stopping a request that waits drops it and moves its issue from `waiting` back to the state it had before, or to
`open` when that was `queued`, so the dispatcher never asks for it again. The dispatcher
ignores a request whose event an agent wrote, where `AGENT_DESK_RUN` was set, and ends it as `ignored`. It also ends
as `ignored` a request for a target that already has one waiting with the same benchmark limit and environment, and
says so in its log, so two presses of a button queue one run. A request behind a working run of its target still
waits, since it may carry a new comment or review, apart from a fresh start with `EVENT=issues` the same as a run
that works its issue and was asked for since the issue last moved to `queued`. The Agent-ready button labels the
issue and then asks for its run, and the label's event may wake the dispatcher, which asks too, between the two. The
dispatcher ends the second request as `ignored`, whether the first still waits or works, so one press starts one
run. A failed run that `agent-desk release` queues again was asked for before the issue moved back to `queued`, so
the new request waits behind it.

A comment with `to_agent` set, as the page's Send to Agent, the pane's Fix, and a page comment that mentions `@agent`
post, asks for an agent run. Such a comment always mentions `@agent`, since
`store.comment` puts `@agent ` in front of a body sent to the agent without the mention, so a reader of the
conversation sees which comments went to the agent. On an idea, an open issue labelled `idea` in no
agent state as Ideas shows it, it requests `agent idea-<n>`, a discussion run, which `agent-desk discuss-idea` works
as the Discussion runs section below describes. On any other issue, it requests `agent <n>` with
`EVENT=issue_comment`. On a review from `agent/issue-<n>`, it requests `agent <n>` with `REPLY_PR` naming the review,
so `work-issue` reads the review's comments and replies there. On a review from any other `agent/<slug>` branch, it
requests `agent pr-<n>` with the comment in `REQUEST`, which `work-pr` reads. A review from any other branch gets a
note that only agent branches take the request, and starts nothing. An issue or review labelled `benchmark` asks with
`--benchmark`. The dispatcher reads the comment's author from the store and ignores one an agent wrote. It records
its answer to each comment, whether it asked for a run, posted the note, or ignored an agent's comment, as a
`comment-answered` event naming the comment's event, in the transaction that asks for the run. On start, and every
30 seconds, it answers the comments sent to the agent since the newest answer, oldest first, so a comment posted
while the dispatcher is down starts its run once the dispatcher is back, and none asks for two. On a store with no
answer yet, the first start records one at the newest event, so old and imported comments ask for nothing.

Each run starts through `systemd-run --user` as `agent-desk-<kind>-<owner>-<repo>-<target>-<id>.service`, in the
project's main checkout, running `agent-desk work-issue`, `discuss-idea`, `work-pr`, `fix`, or `run-chore`. The unit
carries:

- `RuntimeMaxSec` of 180 minutes for an agent run, 300 for a benchmark run, 30 for a discussion of an idea, 90 for a
  fix, and 120 for a chore.
- `Nice=10`, `CPUWeight=20`, `IOWeight=20`, and the `MemoryHigh` of the run's kind, inside `agentdesk.slice`, whose
  `MemoryHigh` caps the runs together, as [Memory](#memory) describes.
- `AGENT_DESK_RUN` set to the run's id, `AGENT_DESK_UNIT` to the unit's name, `GH_REPO` to the project, the
  dispatcher's `PATH`, and the project's `env`. The run's notes name the unit and the
  `journalctl --user -u <unit>` command that prints its log.
- `TMPDIR` set to the run's own temp folder, `/tmp/ad-run-<id>`, over any the project's `env` sets, so the run's
  scripts, its agent's shell, and the tests it runs put their temp files there. An `ExecStartPre` makes the folder,
  and a second `ExecStopPost`, after the handler, removes it however the run ends: a normal end, a timeout, a stop by
  hand, or a crash. The path stays short because a project's tests may put a Unix socket two levels inside it, and a
  socket's path stops at 107 bytes.
- An `ExecStopPost` handler, `lib/dispatch.py stopped`, that records the run's end in `runs` as `succeeded`,
  `stopped` for status 75, `failed`, `cancelled`, or `timed-out`, with its exit status. For an agent run on an issue
  that failed, was cancelled, or timed out, it first runs `agent-desk release`, quoting the unit's last log lines. The
  release posts on the issue in the store how the run ended, moves the issue from `in-progress` back to `open` unless its review is still open, and
  queues it with a new request once when the agent never started. The run holds its target until the release ends,
  so a run the release requests waits for it. A discussion of an idea that failed, was cancelled, or timed out runs
  `agent-desk release --discussion`, which only posts how the run ended on the issue. A fix, or an agent run sent back
  to a review that works no issue, that was cancelled or timed out says so on the review in the store.

`lib/dispatch.py stop <kind> <target>` stops the units of the runs working a target and drops its requests that
wait. The cleanup after a review closes calls it for the review's issue, after posting that the agent stopped.

`agent-desk request --cancel <run>`, which runs `lib/dispatch.py cancel <run>`, ends one request that still waits as
`cancelled` and leaves every other run alone, such as the run working the same target. It exits 1 with a message for
a run that works or has ended. When no other request of the target waits or works, the request's issue moves from
`waiting` back to the state it had before, as with `stop`. Cancel on a Queued row of the pane or the web page calls
it: a row for a request that waits on its own issue cancels that request, and a row for an agent going back to its
PR cancels the newest request that waits on the issue.

`agent-desk pause <n>`, which runs `lib/dispatch.py pause <n>`, and Pause on an agent's card in Working, which asks to
confirm, stop the agent run on issue n and hold the issue. In one write to the store it moves the issue to `on-hold`,
ends each agent request that waits for it as `cancelled`, and notes the pause on the issue; then it stops the unit of
each agent run that works the issue. The run's exit handler finds the issue on hold and leaves it there, so no run
starts it again, and posts that the run was cancelled. The worktree, its commits, and the Claude session stay. The run
frees its agent slot, its memory claim, and its serial slot, and the dispatcher hands the slot to the next request in
line, a waiting merge-queue fix first. It exits 1 with a message, changing nothing, when no agent run works the issue.

The paused issue shows in Blocked as `on hold`, with Resume. `agent-desk unpause <n>`, which runs `lib/dispatch.py
unpause <n>`, and Resume move an issue on hold to `queued` at the top of its project's start order, and the
dispatcher's wake asks for its run. A request that waited at the hold gate while the issue was on hold, such as one a
comment sent to the agent asks for, starts in place of the wake's, so Resume starts one run. That run takes up the
session the paused run left, since the issue's last run note says it was cancelled, as
[How work flows](#issue-lifecycle) describes. It exits 1, changing nothing, for
an issue that isn't on hold. Resume works on any issue on hold, including one you labelled `on-hold` yourself.

`lib/dispatch.py unqueue <n>` sends a `queued` or `waiting` issue back to `open`: in one write to the store it ends
each agent request that waits for the issue as `cancelled` and sets the issue `open`, so the wake never asks for it
again, and then drops the issue's Manual pick. It exits 1 with a message, changing nothing, while an agent run works
the issue or once the issue has left both states. Move to Open on a queued or waiting issue's row in Queued, on the
pane or the web page, calls it.

When an `agent/*` review merges, from its `review-merged` event, or closes without merging with any close reason,
from its `state-changed` event, the dispatcher starts `agent-desk clean <branch> <merged>` as
`agent-desk-clean-<owner>-<repo>-<slug>-<event>.service`, in the project's main checkout, with a 15-minute limit. The
`state-changed` event a merge writes about itself starts nothing more. The dispatcher
records each cleanup it starts as a `cleanup-started` event naming the review's end. On start, and every 30 seconds,
it starts the cleanups of the reviews that ended since the last one, so a review that ended while the dispatcher was
down, or whose cleanup failed to start, still gets its cleanup.

A dispatcher restart leaves running units alone. It rebuilds its view from `runs` and
`systemctl --user list-units 'agent-desk-*'`, and ends a running run whose unit is gone as `lost`; it checks the same
every 30 seconds. The dispatcher reads `PRAGMA user_version` on each wake and starts nothing while the store's schema
is newer than its code. When a merge into agent-desk's own main changes `lib/dispatch.py` or any `lib/` module it
imports, directly or through other `lib/` modules, as the merged source names them, the merge queue restarts the
service once it fast-forwards the main checkout, and `./install.sh` restarts it when it points it at another checkout.

### Discussion runs

A discussion run, `agent idea-<n>`, talks an idea over with an agent before you plan it. The dispatcher asks for one
when a comment sent to the agent lands on an idea, from the idea's Send to Agent on the web page or a comment that
mentions `@agent`. It takes one of the project's `agent` slots and holds the issue's `agent-<n>` target, so two
discussions of one idea, or a discussion and a `work-issue` run of the issue, never run at once; a comment sent while
one works waits behind it. Its serial and blocking links hold nothing back, and it has its own 30-minute limit and
memory need, `resources.needGB.discuss`, 2 GB unless the project sets one, whatever the issue's labels.

`agent-desk discuss-idea <n>` works it. It posts a note that the discussion started, with the Claude Code session, as
`work-issue`'s start note does, then makes a detached worktree of the local `main` at a path fixed for the issue,
`~/.cache/agent-desk/ideas/<owner>__<repo>/<n>`, and runs `claude -p` there with the `/agent-desk:discuss-idea`
skill. The skill reads the issue, its comments, and the code they name, answers the newest comment with one comment,
and ends; it edits the description only when the comment asks it to, and never commits, opens a review, files an
issue, or changes the issue's labels or state. The worktree goes when the run ends, however it ends, and makes no
branch, so `agent-desk clean` has nothing to remove. Claude Code keeps a session by its folder, so the next discussion
of the idea, in the same path, takes up the session the last start note names when this machine still has it, and
the conversation goes on. The issue stays `open` and in Ideas throughout: the run never moves it, and a failed or
timed-out run's release only says how it ended. Runners shows the run as an agent run noted `discussion`, and one
that failed shows in Needs You with Retry.

### Parent checks

A parent check closes out a parent issue, one that groups sub-issues holding its work. When the `agent-ready` label
reaches an issue with at least one sub-issue and none open, `agent-desk work-issue` runs `lib/close-parent.sh` in
place of the serial claim, worktree, and agent of a full run. The check keeps the `agent <n>` request's target and
agent slot, takes no serial slot, and waits for a discussion's memory, `resources.needGB.discuss`.

`lib/close-parent.sh` moves the issue to `in-progress`, which takes `agent-ready` off, and posts a note with the Claude
Code session. It makes a detached worktree of the local `main` at `~/.cache/agent-desk/parents/<owner>__<repo>/<n>`,
runs `claude -p` there with the `/agent-desk:close-parent` skill, and removes the worktree when the run ends, however
it ends, with no branch, commit, or review. The skill reads the parent, its comments, its sub-issues, and the review
each sub-issue's `fixes` link names, and checks each Done When box against them and the code on `main` with read-only
commands. A box it can't confirm from there, such as work the description names in another repository, counts as
unmet, so a parent closes only once that work is confirmed. A sub-issue closed as a duplicate counts as its original,
read through `agent-desk issue view <sub> --json duplicateOf`: completed once the original closed as completed, and
unmet while the original is open or closed as not planned. A parent with no Done When is met once every sub-issue
counts as completed.

When every box is met, the agent closes the parent as completed with one comment listing each sub-issue with its review
and outcome, then each box with how it was met. Otherwise, or when a sub-issue closed as not planned or as a duplicate
of an issue still open or closed as not planned, it comments under
`## Decisions Needed` what is left, as numbered decisions with the lettered options to file a sub-issue, accept it as
done, or close the parent as not planned, and moves the parent to `needs-design`. A check that ends with the parent in neither state moves it back to
`open` with a note, and a failed or timed-out one's release says how it ended.

### Branch checks

The dispatcher tests each `agent/*` branch on this machine as it moves, so a branch's result is known before anything
reaches GitHub. The board reads the results: a PR's row shows its head's
newest check as passing, failing, or conflicting with main. The [merge queue](#merge-queue) merges a review once the
check of its head merged with main passes, or, in a project without a `test` command, once its head holds main.

Once a second, the dispatcher reads `git for-each-ref refs/heads/agent/` in each project's main checkout, which
sees an agent's commits in its worktree at once, since worktrees share the main checkout's `.git`. Each head that
changed since the last read, including a branch that appeared or was deleted, becomes a `branch-moved` event in the
store and sets the branch's row in the `branches` table, which holds each watched branch's head. A restart compares
the heads with that table, so a move while the dispatcher was down counts too. Once a
second costs a fifth of the CPU that reading on every 200 ms wake would.

A moved head waits until the run working its branch ends, or until it stands still for 60 seconds, so a run that
commits several times gets one check of its last commit. The branch of an agent run on issue n is `agent/issue-<n>`,
and that of a fix, or of an agent run sent back to a PR, is the PR's head branch in the store. The dispatcher then
queues a check of the head unless the project's [`test`](#the-config) is empty, the head's base already holds it, or
the head was checked before. A restart sets each head in `branches` without a check waiting another 60 seconds, so a
head that moved just before the dispatcher restarted still gets its check. A head's base is the base of its branch's
open review in the store, such as `local-desk` for a review a `base:<branch>` label opened, the base of the
merge-queue batch for a `merge-queue/` branch, and `main` for a branch with neither. A queued check starts while the
project has a free `check` slot. Auto and Manual start it; Hold starts it only while a run still works its branch, so
that run can wait for its check and finish. A newer head on the branch, or the
branch's deletion, ends the check of the older head as `superseded` and stops its unit.

The same read takes the heads of the open reviews' bases, from the local branch or else `origin/<base>`. Whenever a
review's head or its base moves, the dispatcher runs `git merge-tree --write-tree` on the pair, which touches no
worktree, and records `MERGEABLE` or `CONFLICTING` from its exit status in the branch's row, with a
`branch-merges` event when the answer changes. It does so whatever the project's `test` setting, so the board marks a
conflicting PR in a project without checks too.

Each check runs `lib/checks.py` in a unit named `agent-desk-check-<owner>-<repo>-<id>.service`, with the priority and
environment of a run's unit, its own temp folder `/tmp/ad-check-<id>` that the unit removes as a run's does, the `MemoryHigh` of an agent run, and the config's [`testMinutes`](#the-config) as its time
limit, 15 minutes by default. It makes a detached scratch worktree of the head under `~/.cache/agent-desk/checks/`,
merges the head's base into it from the local branch or else `origin/<base>`, and names that base in the log's first
line and its `Merge <base>` step. It then copies `worktree.seeds` from the main checkout, runs `worktree.setup`, runs
`test`, and removes the worktree, also from the unit's exit handler after a timeout. A merge conflict ends the check as
`failed` with the name `merge` and runs no test, and a failed setup ends it with the name `setup`. A first attempt that
fails or times out queues a second of the same head, so a flaky test counts as failed only when it fails twice; a
conflict gets no second attempt.

A row of `checks` holds the project, branch, the review the store knows for the branch, the head, the name (`test`,
`merge`, or `setup`), the status (`queued`, `running`, `passed`, `failed`, `timed-out`, `superseded`, `cancelled`, or
`lost`), the attempt, the start and end times, and `log_path`, which is
`~/.local/state/agent-desk/checks/<owner>__<repo>/<id>.log`. `lib/failures.py log <file>` prints the lines of such a
log that say what failed, as [Conflict fixes](#conflict-fixes) describes.

## Backup and restore

`agent-desk backup` keeps the local store in a git repository of its own, so one lost disk doesn't lose the tracker.
The repository is `~/dev/repo/agent-desk-data`, or the folder `AGENT_DESK_DATA` names, and the first backup creates
it when the folder is missing or empty. Each run writes every item as `<owner>/<repo>/<number>.json`, holding the
item with its labels, links, comments, and review fields, beside a `project.json` and a `label_colors.json` for each
project. `label_colors.json` maps each label's name to its color. The files keep their keys sorted, so an unchanged
item or set of colors leaves its file as it was, and a run commits only when one changed. A run
never deletes a file, so a backup taken against an empty database commits nothing.

When the repository has a remote, `origin` or its only one, the backup pushes the branch at most once an hour. A push
that fails prints a warning, leaves the exit status at 0, and the next run tries again. Any git remote works
apart from a public GitHub repository. The store holds every project's issues and comments, private projects beside
public ones, so before a push to a URL on github.com the backup asks `gh repo view <owner/repo> --json visibility`
whether the repository is public, checking each URL `git remote get-url --push --all` lists. It skips the push to a
public repository, and to one whose visibility `gh` can't read because it isn't installed, isn't logged in, or fails,
and records why as the error; the commit stays in the backup repository, and the next run asks again. A remote on
another host pushes without the check. Each run saves how it went in `backup.json` beside the database: its remote, the
time of the newest backup commit and of the last push, whether a commit waits for the push, and the error of a push or
backup that failed. The GitHub group on the [web page](#the-web-page) and in the pane reads it.

`agent-desk backup snapshot` copies the whole database with `VACUUM INTO` to
`~/.local/state/agent-desk/snapshots/<date>.sqlite`, replacing one taken earlier the same day, and deletes the
snapshots older than seven days. The snapshots keep the runs, events, checks, and assets the JSON files leave out.

`agent-desk backup install` writes and enables `agent-desk-backup.timer`, which runs `agent-desk backup tick` every 15
minutes: the backup, then a snapshot when today has none, so the first tick of each day takes it. The service keeps
the `AGENT_DESK_DATA` the install ran with. Its log: `journalctl --user -u agent-desk-backup`.

`agent-desk restore` rebuilds a database from either source, into the state folder `--to` names:

```bash
agent-desk restore --from ~/dev/repo/agent-desk-data --to /tmp/rehearsal/agent-desk
agent-desk restore --from ~/.local/state/agent-desk/snapshots/2026-11-11.sqlite --to ~/.local/state/agent-desk
```

It writes `desk.sqlite` in that folder, refuses to replace one already there, and prints each project's item count,
so a rehearsal into an empty folder can compare the counts with the live store. A restore from the repository reads
the files checked out in it; check out an older commit first to restore an earlier state. Both sources keep the label
colors, and a repository written before the backup kept them restores with none, so each label takes a color hashed
from its name until the next import or `agent-desk label edit`. Each comment's file names its `sent_in`, empty or not.
A repository written before that left it out of some line comments, so a restore counts each line comment without it
as sent, and the next Send to Agent quotes none of them.

## Releasing

A release starts as a local review. `just release` from a clean, up-to-date `main` runs `scripts/release.py`, which
commits the release on `agent/release-X.Y.Z` and opens a `Release X.Y.Z` review of that branch in the
[local store](#the-local-store) with `agent-desk review create`. The commit folds the waiting `changelog.d/` files into
a new `CHANGELOG.md` section, deletes them, and sets `VERSION` and the plugin's `plugin.json` to the new version, and
the review's description holds the new section. The entries pick the version: below 1.0.0, a **Breaking** or
**Workflows** entry bumps the minor number and anything else the patch number; from 1.0.0 on, **Breaking** bumps the
major number, **Workflows** the minor, and anything else the patch. `just release major`, `minor`, `patch`, or `X.Y.Z`
overrides the choice, and `just release --dry-run` prints the version and the section without changing anything. The
script refuses while a release review is open or when `changelog.d/` holds no entries. When a step fails, it drops the
release branch and its changes and returns to `main`, so running it again starts over. Nothing cuts a release on a
schedule.

Merging the release review publishes it. The [merge queue](#merge-queue) lands it on `main`, and once the [push
queue](#pushing-main) pushes a `main` whose `VERSION` changed from agent-desk's main checkout, it runs
`scripts/release.py publish` there, as `just release publish` does. Publishing refuses unless the checkout is on
`main` with no uncommitted changes to tracked files and `origin`'s `main` holds the commit that last changed
`VERSION`, so a release branch or an unpushed `main` never publishes. It then runs four steps:

1. It tags `vX.Y.Z` in local git at the commit on `main` that last changed `VERSION`, unless the tag exists. A tag
   that names another commit stops the publish.
2. It pushes the tag to `origin`, when the checkout has one.
3. It runs the release step of the config's [`host`](#the-config): for `github`, `gh release create` with the
   version's changelog section as its notes, unless the release exists; for `none`, nothing.
4. When the checkout has a `public` git remote, it starts the public publish in a systemd user unit of its own,
   `agent-desk-publish-vX.Y.Z`, and returns without waiting, so the push queue's tick ends at once. Without the remote,
   it says it skipped this step.

A failed step leaves the tag in place and prints why, and running `just release publish` again skips what finished and
does the rest.

agent-desk's development history stays in its private repository. The public repository gets each release as one
snapshot commit, whose only parent is the previous release's snapshot, so its history holds release commits and none
of the private ones. The public publish, `scripts/publish.py vX.Y.Z`, logs to the journal
(`journalctl --user -u agent-desk-publish-vX.Y.Z`) and runs these steps:

1. It builds the public tree in a temporary folder: every file the tag holds, minus the paths
   `scripts/publish-exclude` lists, with the contents of `public/` laid over the root.
2. It refuses when a line or path of the tree matches a pattern in `scripts/publish-forbid`, such as this machine's
   user and host names, and names each file and line. A binary file, one that holds a NUL byte or isn't valid UTF-8,
   has only its path checked, so a picture's compressed bytes never match by chance and a picture still needs a
   person's look.
3. It runs the `test-hosted` workflow at the tag with `gh workflow run`, waits up to 20 minutes for it, and refuses
   unless it passed, naming the run's link. The workflow builds the same public tree and runs the suites on it, so a
   test that needs a file the publish drops fails here.
4. It commits the tree as `Release X.Y.Z` on the public `main` and checks the commit with
   `scripts/publish_history.py`, which refuses, naming the commits, when a local branch, a tag, or one of `origin`'s
   branches also reaches any commit in its history. It pushes the commit to the public `main` and as the tag `vX.Y.Z`
   in one atomic push that never forces, then fetches the public `main` back with `git fetch --no-tags public main`
   and checks `FETCH_HEAD` the same way.
5. It creates the public repository's GitHub Release with the version's changelog section as notes, unless it exists.

A version whose tag the public repository holds skips to the last step, so a rerun after a finished publish does
nothing. After a failure, `just release publish` starts the public publish again, with a new hosted test.
`python3 scripts/publish.py vX.Y.Z` runs it in the foreground, and `--dry-run` builds and checks the tree and prints
the files that would change since the public `main`, without the hosted test or a push. `--tree <dir> <revision>`
writes the public tree of any revision, such as `HEAD`, into a new or empty folder and exits, so you can run the
suites on it.
