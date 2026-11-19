# Changelog

This file records the notable changes in each agent-desk release, in the form
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) describes. Releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and `VERSION` holds the current one.

An entry that starts with **Workflows** changed the workflow templates in `workflows/`. After upgrading past one, rerun
`agent-desk install` in each project and commit the workflows it writes. README.md's
[Upgrade](README.md#upgrade) section walks through it. An entry that starts with **Breaking** changes something you rely
on, such as a command, a config key, or a label.

Pending entries wait in [`changelog.d/`](changelog.d/README.md), one file per pull request, until a release folds them
into this file.

## [Unreleased]

The changes since the last release sit in [`changelog.d/`](changelog.d/README.md).

## [0.2.1] - 2026-11-19

### Fixed

- The Python tests pass on a machine with no git identity and no agent-desk setup, such as a GitHub-hosted runner.
  (#515)

## [0.2.0] - 2026-11-19

### Added

- agent-desk is under the MIT license, with `THIRD_PARTY_NOTICES.md` listing the licenses of the web page's vendored
  Preact and htm, and the plugin's manifest names its license, author, and repository. (#10)
- `SECURITY.md` describes what agent-desk lets other people and agents do on your machine, how to keep self-hosted
  runners safe on a public repository, and how to report a vulnerability. The README's setup section links to it.
  (#12)
- **Workflows**: `agent-desk install` writes the runners' architecture label into each job, `X64` or `ARM64` from
  `uname -m`, in place of the hard-coded `x64`, so the workflows run on ARM64 runners. `runners.arch` in
  `.agent-desk.json` overrides it for runners on another machine. (#15)
- `.agent-desk.json` takes `presence`: `"desktop"`, the default, starts chores and conflict fixes only while your
  desktop session is unlocked, and `"always"` starts them on a machine without one, such as a headless server.
  `agent-desk chores status` warns when `"desktop"` presence keeps chores from ever starting there. (#16)
- `agent-desk runners install` without counts warns when the pool's `MemoryHigh` adds up to more than the machine's
  memory, and suggests counts that fit. (#17)
- The self-hosted-runner installer takes `RUNNER_PIN_CPUS=0` to skip CPU pinning, and a rerun with it removes the pin
  an earlier run wrote. (#18)
- `.claude-plugin/marketplace.json` publishes the plugin through a Claude Code marketplace, so
  `claude plugin marketplace add erjand/agent-desk` and `claude plugin install agent-desk` install the pane and skills,
  and a clone supplies the `agent-desk` command. README's Install section gives this path and the development one. (#22)
- The pane says how to install the `agent-desk` command when it can't find it, in place of the board. (#22)
- Each pull request records its changelog entries in a file of its own in `changelog.d/`, which a release folds into
  `CHANGELOG.md`. A Breaking marker flags an entry that breaks a command, a config key, or a label. (#50)
- `just release` opens a `Release X.Y.Z` PR that folds `changelog.d/` into `CHANGELOG.md` and bumps `VERSION` and the
  plugin's version, picking the version from the entries' markers unless you name one. The `release` workflow runs it
  from a button and every Monday, and merging the PR tags the version and publishes a GitHub Release with its
  changelog section as notes. (#51)
- `just release publish` ends by publishing the release to the public repository, when the checkout has a `public`
  remote, as one snapshot commit of the public files with its tag and GitHub Release. It runs in a systemd unit of its
  own, refuses a tree that holds a pattern from `scripts/publish-forbid`, and waits for the `test-hosted` workflow to
  pass first. `python3 scripts/publish.py --dry-run vX.Y.Z` lists the files a release would change there. (#53)
- `agent-desk web run <owner/repo>...` serves the web page for only the registered projects it names, such as a demo
  project for screenshots. (#54)
- `agent-desk runners install` and the self-hosted-runner skill's installer download the ARM64 runner on an `aarch64`
  machine, checked against its own pinned SHA256, so the ARM64 jobs the workflows ask for find runners. On a machine
  other than `x86_64` or `aarch64`, the installer stops before downloading and names the machine. (#60)
- The web page's Open, Blocked, Needs Design, and Ideas headings each have a `# ↓` button that flips the group between
  highest issue number first and lowest first, and the page remembers each group's choice. (#63)
- `agent-desk dispatch install` turns on `agent-desk-dispatch.service`, a systemd user service that starts each
  requested agent, chore, and fix run as its own systemd unit, with the workflows' time limits and the runners'
  priority, once the project has a free slot, no other run holds the target, and the memory is spare. Nothing asks it
  for runs yet. (#72)
- `agent-desk request <agent|chore|fix> <target> [--benchmark]` asks the dispatcher for a run. (#72)
- The `slots` config key caps how many runs of each kind the dispatcher runs at once in a project, by default
  `{"agent": 3, "chore": 1, "fix": 1, "check": 2, "benchmark": 2}`, and `agent-desk doctor` checks it. (#72)
- An issue labelled `base:<branch>` works from that branch in place of `main`: its worktree branches from it, its PR
  targets it, and the merge queue, conflict fixes, and later runs merge with it. `agent-desk worktree <slug> [base]`
  takes the base as a second argument or from `AGENT_DESK_BASE`. A base origin lacks stops the run with a comment
  naming it. (#73)
- The pane and the page show the runs the dispatcher started: a working run in Runners under its kind, and a waiting
  request's issue in Queued. An issue whose agent works in a unit no longer shows as stopped. A run of the
  dispatcher's that failed, timed out, or was lost shows in Needs You for a day, with Retry, which asks the dispatcher
  for the run again, and Dismiss. (#75)
- The dispatcher follows the desk's mode. On Hold, or while the machine gets ready to sleep, requests wait in the
  store and start on Resume, and in Manual a request from an `agent-ready` label waits for Start. Stop and Get Ready
  to Sleep list and stop the dispatcher's units, and ask for each stopped run again. (#75)
- `agent-desk import <owner/repo>` copies a GitHub project's issues, PRs, comments, labels, sub-issues, and blockers
  into the local store with their numbers, and a rerun copies only what changed. (#76)
- `agent-desk backup` writes every item in the local store as a JSON file into a git repository,
  `~/dev/repo/agent-desk-data` or `AGENT_DESK_DATA`, commits any change, and pushes to its remote at most once an
  hour. `agent-desk backup snapshot` copies the database to `~/.local/state/agent-desk/snapshots/<date>.sqlite` and
  keeps seven days of them. (#77)
- `agent-desk backup install` turns on `agent-desk-backup.timer`, which backs the store up every 15 minutes and
  snapshots it once a day, and `agent-desk doctor` checks that it runs. (#77)
- `agent-desk restore --from <repository | snapshot> --to <state folder>` rebuilds the store's database from either
  source, refuses to replace an existing one, and prints the item count per project. (#77)
- The `test` config key names the command that tests a branch, such as `just test`. When it is set, the dispatcher
  watches each `agent/*` branch in the main checkout and, once the branch's run ends or its head stands still for a
  minute, runs the command on the head merged with `main` in a scratch worktree, in a `check` slot with a 15-minute
  limit. A check that fails runs once more before it counts as failed, a merge conflict fails as `merge` without
  testing, and a newer head stops the check of the older one. The results go to the store's `checks` table, with logs
  under `~/.local/state/agent-desk/checks/`; nothing reads them yet. `agent-desk install` writes `"test": ""`, which
  turns the checks off. (#78)
- `agent-desk review checks <n> --watch` prints the local checks of a review's newest commit and waits for them to
  finish, exiting 0 when they pass and 1 when one fails, as `gh pr checks` does. (#84)
- `agent-desk board [--json]` prints everything the inbox's board shows of a project without asking GitHub: its open
  issues with their states, labels, blockers, and comments, its open reviews with their heads' branch checks and
  whether they merge with their bases, its newest merges, its runs, the merge queue and the automatic fixes' plan as
  their last ticks saved them, the desk's mode, the chores table, and the clock. `agent-desk review view --json`
  takes `checks` and `mergeable`. (#86)
- The dispatcher tries each open review's head against its base with `git merge-tree` whenever either moves, so the
  board marks a conflicting PR in a project without a `test` command, and before its check runs. (#86)
- `agent-desk fixes` saves the plan it shows, and `agent-desk chores status` the table it prints, for the board to
  read. The chore timer runs `agent-desk chores status` in each project after its tick. (#86)
- A review's Files Changed tab on the web page takes line comments: press a line's number, or the + beside it, to
  comment on that line. A comment shows under its line, moved past the lines later commits added above it, and one
  whose line a later commit changed shows as outdated above the diff with the code it was made on. (#92)
- Send to Agent on a review's page posts the comment box's text and every unsent line comment as one comment to the
  agent, and the dispatcher starts `work-issue` for an `agent/issue-<n>` branch or `work-pr` for any other agent
  branch. (#92)
- A merge box on a review's conversation holds Ready for Review, Merge, and Close Review, each acting on the review in
  the local store. (#92)
- `agent-desk review comment` takes `--head-sha` with `--path` and `--line`, and a line comment defaults to the
  review's newest head. (#92)
- `agent-desk pushes` pushes `main` to `origin` after each merge, and the `agent/*` branches that moved, best effort.
  It fetches `origin`'s `main` before it counts, pushes nothing for a project whose `host` is `none`, and never waits
  on a password or ssh passphrase prompt. A fetch or push that fails for a network or server reason retries with a
  growing wait, and an `origin` whose `main` moved stops the push until you merge its commits into `main` by hand. Merging in the pane and on the page shows
  "main is N commits ahead of origin" while `main` holds unpushed commits, with what holds the push back. (#94)
- `.agent-desk.json` takes `host`, `github` or `none`, which picks how a release publishes, and `agent-desk install`
  writes `"host": "github"` into a new config. `agent-desk doctor` names any other value. (#95)
- `agent-desk doctor` checks that the local store's database opens and holds a schema the checkout can write, and that
  the store registers the project at its main checkout. For a project whose `host` is `github`, it warns when `gh` is
  missing or logged out, which releases and imports need, and names each open GitHub PR from an `agent/*` branch,
  which the desk never sees. (#96)
- `agent-desk cutover <owner/repo>` and `agent-desk cutover --all` make the local store a project's tracker for good:
  they import it one last time, check its open PRs' branches and reviews and its issues' states, record the cutover,
  add the line naming `agent-desk issue` and `agent-desk review` to its AGENTS.md, and then disable its workflows,
  close its agents' PRs with a pointer to their reviews, and turn its Issues off on GitHub. A person's, a bot's, or a
  fork's PR stays open on GitHub. `--dry-run` prints the steps from a read-only store, and a rerun finishes a cutover
  that stopped partway. `agent-desk cutover --undo` turns back on what the cutover turned off and lists what was filed
  locally since. (#97)
- `agent-desk issue list` and `agent-desk review list` take `--since <time>`. (#97)
- The web page's header says when its checkout holds newer code than the server runs, as after a pull or under
  `agent-desk web run`, which nothing restarts. (#117)
- `agent-desk request --cancel <run>` ends one run request that still waits and leaves every other run alone, and
  Cancel on a Queued row of the pane or the web page calls it. (#118)
- `python3 lib/run_log.py import [<path>]` copies the runs in the old `~/.local/state/agent-desk/runs.jsonl` into the
  store's `runs` table, so the History column shows what reviews merged before the store cost and the chores' stats
  count the runs from before it. A line whose run the store already holds fills in that run's results, and a second
  import adds nothing. (#144)
- `agent-desk issue close` takes `--comment` or `--comment-file` and writes the comment and the close together, and
  `agent-desk review comment --to-agent --quotes <id>` marks the line comments it carries as sent. (#164)
- `agent-desk label edit <name> --color <hex>` gives a label in the local store the color the pane and the page draw,
  and `agent-desk label list` prints a project's colors. (#169)
- A failed or timed-out run in the pane's Needs You has a Log button, which copies the `journalctl --user -u <unit>`
  command that prints its unit's log. The web page names the same command on the run's card, with a Copy button. (#174)
- `agent-desk board --json` prints `web`, the address the web page serves at, from `AGENT_DESK_WEB_PORT` or the port
  of the service `agent-desk web on` installed. (#174)
- `agent-desk install <owner>/<repo>` names a project whose origin is a path or missing, and writes `"host": "none"`
  into the starter config when the checkout has none. (#183)
- The web page and the pane show a GitHub group with each place the desk touches GitHub and how it last went: `main`
  matching `origin` with its last push, or how far ahead it is and why the push waits; the agent branches pushed and
  waiting, with any that failed; and the backup's last commit and push, once for every project. A failing row reads
  in the warning tone. `agent-desk pushes --json` adds `pushedAt` and `agentBranches`, and each backup saves
  `backup.json` beside the database. (#198)
- The web page lets you tick and untick the task-list boxes in an issue's description, as GitHub does. A tick
  rewrites that item's `[ ]` or `[x]` in the stored description, and a tick on a description that changed since the
  page read it changes nothing and says why. Boxes in comments and review descriptions stay disabled. (#205)
- `agent-desk project rename <owner/repo> <new owner/repo>` moves a project held on Hold to a new name in the local
  store, the state folder, `hold.json`, the projects file, its main checkout's `origin`, and the backup repository,
  and a rerun finishes a rename that stopped partway. (#210)
- A review's Files Changed tab on the web page lists the changed files in a folder tree left of the diffs, with a mark
  for each file's status, a filter box, a click that scrolls to a file's diff, and a button that hides the tree. The
  sidebar steps aside on that tab. (#219)
- An idea's comment box on the web page has Send to Agent, which starts a discussion run: an agent reads the idea,
  your comment, and the code, and answers on the issue with research, options, or a sharper scope, from a throwaway
  worktree of `main`. The run makes no branch, commit, or review, the issue stays in Ideas, and a later comment
  continues the same session. An `@agent` comment on an idea starts the same run. `agent-desk discuss-idea <n>` and
  the `/agent-desk:discuss-idea` skill run it. (#226)
- An issue in Ideas has a Move to Open button in the pane and on the web page, with the key `n` on the page. After a
  confirm it takes the `idea` label off and moves the issue to Open, where Agent-ready queues it like any other. (#227)
- The `testMinutes` key in `.agent-desk.json` sets a branch check's time limit in whole minutes, by default 15.
  `agent-desk doctor` fails a config whose `testMinutes` isn't a whole number above 0. (#234)
- `agent-desk doctor` warns when the project's `test` command is missing or empty, since its reviews then merge
  untested. (#234)
- The web page shows a 👀 under a comment that sent work to the agent, in a conversation or on a review's diff, once
  the dispatcher asks for its run, with a tooltip naming the run. The open panel picks up the answer without a
  reload. (#236)
- A click on a picture in a description or comment on the web page opens it over the page at its natural size,
  shrunk to fit the window. Escape, the close button, or a click beside it closes it, and a Ctrl-click, Cmd-click, or
  middle-click opens it in a new tab. (#264)
- `agent-desk chores rename <old> <new> [-R <owner/repo> | --all]` carries a chore over to a skill's new name: it
  renames the chore in `.agent-desk.json`'s `chores.every`, `chores.paused`, and `models.chores` through the schedule
  review and prints its number, moves the project's runs of the chore in the store and its ledger to the new name, and
  refuses when the new name already has runs or a config entry, or the old one has neither. (#280)
- The chore timer holds back the shared `security-audit` chore while `chores.maxAuditIssues` issues labelled
  `security` are open, as it holds back `audit` by its `audit` issues, and `agent-desk chores status` shows the count.
  The new `chores.issueLabels` config key maps any other chore that files issues to its label. (#291)
- Labelling a parent issue `agent-ready` once all its sub-issues have closed starts a short parent check in place of a
  full run: an agent reads the closed sub-issues and their reviews, checks the parent's Done When against them and
  `main`, and closes the parent with a summary, or asks what is left under `## Decisions Needed` and moves it to
  `needs-design`. The check runs from a throwaway worktree of `main`, takes no serial slot, and makes no branch,
  commit, or review. The `/agent-desk:close-parent` skill runs it. (#294)
- A `## Decisions Needed` section on the web page, in a description or a comment, shows an answer form right under
  it: a button per lettered option and an Other field for each numbered decision, or one text box for a section in the
  older layout, then a note, Comment, and Send to Agent where the conversation's box has it. The form posts an answer
  comment that links the section, and an answered section shows who answered what and when, with its form folded
  behind Answer Again. (#300)
- The web page files a new issue from a New Issue form, which the header's button or `i` opens in the panel: a project
  picker, a title, a Markdown body with Write and Preview, and a label picker. Create selects the new issue's card in
  the list. (#328)
- The web page's New Issue form starts its body from the project's task template, or the Goal, Done When, and Notes
  headings without one, and has an Agent-Ready box that queues an agent at once, a Parent field, and a Blocked By field
  that takes several issue numbers. (#329)
- The web page edits an issue's or a review's title and description: Edit beside the panel's title turns it into a
  field, and Edit in a description's header opens its Markdown in a box with Write and Preview tabs. Save refuses a
  description changed since Edit read it, and warns while an agent works the issue that it won't see the edit. (#330)
- You can edit a comment you wrote, on an issue or a review, from the web page's Edit in its header, including an
  unsent line comment in Files Changed, and with `agent-desk issue comment <n> --edit <comment> --body-file <file>`
  or the same on `review`. An edited comment shows `edited` after its time. A comment by someone else, or one sent to
  the agent, stays as written. `issue view` and `review view` print each comment's id. An agent edits only the
  agent's comments, and its edit needs `--note <what changed>`, which the store adds to the comment as an
  `**Edited <time>:**` line. The work-issue skill uses it to remove answered decisions from an agent's comment.
  (#331)
- Ctrl+Enter, or Cmd+Enter, in a Decisions Needed answer form's note or Other field on the web page posts the answer
  as a comment, as it does in the comment box at the bottom of a conversation. (#337)
- Every unit the dispatcher starts runs inside `agentdesk.slice`, whose `MemoryHigh=50%` slows the runs together and
  reclaims memory from them before the kernel pushes your apps to swap. `agent-desk dispatch install` writes the
  slice, and `agent-desk doctor` warns when it is missing or differs. Run `agent-desk dispatch install` once after
  updating. (#343)
- An agent, chore, or fix run waits while the machine's memory pressure, `some avg60` in `/proc/pressure/memory`, is
  above `resources.maxMemoryPressure`, by default 5%, so runs stop starting on memory the kernel freed by swapping
  your apps out. The run's log and the dispatcher's note name the check that holds the run. (#344)
- `agent-desk gpu [--exclusive] -- <command>` runs a command while it holds one of the machine's GPU slots, so GPU
  renders from every project and checkout take turns. The machine has one slot, or `AGENT_DESK_GPU_SLOTS` of them,
  and `--exclusive` holds them all for a timed benchmark. (#345)
- `agent-desk usage-limit [<0-100> | on | off]` sets a weekly plan-usage limit for every project on the machine.
  While the Claude plan's 7-day usage reads at or over it, the dispatcher holds new agent, chore, and fix requests and
  the chore timer starts nothing; branch checks go on, and held requests start on their own once usage falls under
  the limit. When it can't read usage for an hour, the limit lets runs start, and `agent-desk doctor` warns. (#364)
- The web page's header shows your weekly plan usage beside the mode buttons while the weekly usage limit is on, in
  the Hold amber at or over the limit, and says when it can't read usage. Its popover turns the limit on or off and
  sets its percentage, as `agent-desk usage-limit` does. (#365)
- Queued shows a Move to Open button, key `n` on the page, on every queued or waiting issue's row, on the web page and
  in the pane. After a confirm it cancels the agent requests that wait for the issue, sets it `open`, and drops its
  Manual pick, so nothing starts it until Agent-Ready again; an issue whose run started stays put, and the button says
  so. `lib/dispatch.py unqueue <n>` does the same from the command line. (#369)
- Close an issue as a duplicate of another with `agent-desk issue close <n> --duplicate-of <m>`, or with Duplicate in
  the close choice of the web page's comment box, which takes the original's number and waits until it names another
  issue of the project. The close comments `Duplicate of #<m>`, and the closed issue's page links the original. (#370)
- `agent-desk issue delete <n> --yes` deletes an issue filed by mistake, with its labels, comments, and links, and
  cancels the agent requests that wait for it; without `--yes` it prints what it would remove. The store refuses an
  issue with a review, an `agent/issue-<n>` branch or worktree, a working agent run, or sub-issues, and says to close
  it instead. A deleted issue's number is never used again. (#371)
- An issue's page on the web page ends its sidebar with Delete Issue when the store would delete the issue, and its
  dialog asks you to type `#<n>` before it deletes. (#371)
- Each project keeps one start order of its queued and waiting issues, and the dispatcher gives the next free agent
  slot, and each serial group's slot, to the first issue in it whose gates are clear. Queued on the web page and in the
  pane lists those issues in that order, ahead of its other rows, with Move Up, Move Down, and Move to Top buttons, and
  the page's `[`, `]`, and `{` keys press them. `agent-desk queue order` prints the order, and
  `agent-desk queue order <n> --up | --down | --top` moves an issue in it. (#374)
- On the web page, you drag a queued or waiting issue's card within Queued to change its place in its project's start
  order. A line shows where it lands, and the drop moves it at once. `agent-desk queue order <n> --to <place>` moves
  issue n to a place from 1. (#375)
- The board says why the dispatcher holds a run back. A Merging card whose queue waits on an auto-fix run or a branch
  check names the gate, such as `conflicts with main; auto-fix waits for memory: 5.3 GB free of 9 GB needed`, and a
  Queued card whose request waits for spare memory, the weekly usage limit, or a free slot adds the reason after its
  note. `lib/dispatch.py runs` lists each waiting request's `gate` and `reason`, and the dispatcher's log says a
  memory reason again only once the free memory moves by half a GB. (#378)
- The web page's gauges pill opens a Resources view in the panel: memory free and the share the dispatcher keeps
  for you, each agent-desk run with its memory, swap, and CPU time and a link to its card, the runs the dispatcher
  holds for memory and what each needs, the apps using the most memory outside agent-desk, and the size of `/tmp`.
  It reads again every 10 seconds while it shows. (#379)
- The web page has a Chat, which the header's Chat button or the backtick (`` ` ``) shows in the side column: a Claude Code session that
  answers questions about the desk's issues, reviews, runs, merge queue, and machine from its live state, and acts on
  what you ask, confirming first before anything it can't undo. Each message carries the selected card, the reply
  streams in with Stop to end it, and the conversation survives reloads and server restarts until Clear Chat. (#380)
- The web page's Resources view has an Audit with Claude button. It shows the Chat and runs
  `/agent-desk:resource-report` there with the view's figures: the memory free, the share the dispatcher keeps for
  you, what running runs claimed, and the runs waiting for memory. The Chat reports what uses the machine and what could be freed, and frees
  what you name in your next messages. The button is disabled while the Chat replies. (#381)
- The pane and the web page show a Backlog section between Open and Ideas for open issues labelled `backlog`,
  concrete work you plan to do later. Backlog rows have Move to Open in place of Agent-ready, and Open rows gain Move
  to Backlog, `b` on the page, which labels the issue `backlog` at once with no confirm. The web page starts Backlog
  collapsed. `/agent-desk:create-issue` adds `--label backlog` when you say the work is for later. (#395)
- The web page's list pins each group's heading under the search box while you scroll its cards, until the next
  group's heading pushes it out, so you always see which group you are reading. (#409)
- The web page's Resources view shows the memory running runs claimed and haven't taken up, with Claimed by runs and
  Free after claims tiles and a Claimed column in its runs table, whose tip gives each claim's start size, settle
  time, and what it holds back. The figures come from the new `lib/memory.py claims` command, so they match the
  dispatcher's memory gate. (#411)
- A queued or waiting issue's row in Queued has Close on the web page, so its comment box closes it as completed, not
  planned, or a duplicate in one step, and the row leaves Queued at once. (#418)
- In Manual and on Hold, a picked queued or waiting issue's note in Queued starts with `picked`, such as
  `picked; waits for a free agent slot` or `picked; starts on Resume`, on the pane and the web page. (#420)
- An Unpick button on a picked row, `z` on the page, drops the issue's pick with no confirm and shows Start again at
  once. `agent-desk unpick <n> [<owner/repo>]` does the same from a shell, and `agent-desk unpick --all
  [<owner/repo>]` drops every pick, or one project's. (#420)
- A chore run can retire leads from the next runs' brief: its `chore-report` lists in `settled` each lead it filed as
  an issue or found no longer holds, and the brief leaves out a lead a newer run settled. (#461)
- `.agent-desk.json` takes `limitMinutes`, the time limit in minutes of a project's `agent`, `benchmark`, `chore`,
  and `fix` runs, and `agent-desk doctor` fails a value that isn't a whole number above 0. A key left out keeps its
  built-in limit. (#490)
- The web page's detail view has Edit in its Labels section, which opens a picker of the project's labels that adds
  or takes off each label as you press it, and adds a new label by name. It leaves `agent-ready`, `in-progress`,
  `waiting`, `needs-design`, and `on-hold` to the card's buttons. (#491)
- `agent-desk gpu slots <n>` sets the GPU slot count for every later `agent-desk gpu` call on the machine, in any
  project, unit, or shell, and `agent-desk gpu slots` prints the count and who holds each slot.
  `AGENT_DESK_GPU_SLOTS` still overrides it. (#492)
- `agent-desk gpu` records each call in the store: its run, its checkout, whether it was exclusive, and how long it
  queued for its slots and held them. A working run's row on the board shows how long its GPU calls held and queued
  for the GPU, the web page's History adds them up for the PRs it lists, and `agent-desk gpu status` prints who holds
  the slots now and the last day's waits by checkout, with the longest wait and who held the slot. (#493)
- `agent-desk project remove [--dry-run] <owner/repo>` takes a project held on Hold off this machine's desk: it
  deletes the project's rows in the store, its files in the state folder, its mode, picks, and queued chores, its line
  in the projects file, and its folder in the backup repository, and leaves its checkout and worktrees, which it lists.
  (#501)
- `agent-desk gpu` takes a GPU slot past the first only while the NVIDIA card has the VRAM free that
  `agent-desk gpu slots --min-free-vram <GB>` sets, 7 GB unless set, and otherwise waits for slot 1 or for the VRAM.
  Without a reading from `nvidia-smi`, shared calls use slot 1 only. `agent-desk gpu slots` prints the threshold with
  the VRAM free now. (#503)
- `agent-desk issue` and `agent-desk review` file, list, and change issues and reviews in a local SQLite store,
  `~/.local/state/agent-desk/desk.sqlite`, with the verbs and flags the scripts pass to `gh`. Nothing reads the store
  yet. (#71)
- Pause on an agent's card in Working, or `agent-desk pause <n>`, stops the agent run on an issue and puts the issue
  on hold. Its worktree, commits, and session stay, and its slot and memory go to the next run in line. Resume on the
  held issue's card in Blocked, or `agent-desk unpause <n>`, queues it at the top of the start order, and its next run
  takes up the session. (#471)

### Changed

- The dispatcher starts a merge-queue conflict fix ahead of every other waiting run. While the fix waits for memory, no
  other run starts in any project, so a freed slot or freed memory goes to the fix first. (#469)
- A conflict fix waits only for memory pressure over the new `resources.fixMemoryPressure`, by default 15%, and ignores
  the memory other runs claimed but haven't used yet. (#469)
- A run's agent waits up to 15 minutes on a command it gives no timeout, in place of Claude Code's 2, so a test suite
  runs to its end in the foreground. An exported `BASH_DEFAULT_TIMEOUT_MS` keeps its value.
- A conflict fix runs only the tests that cover code its resolution changed, and none for a resolution in docs alone,
  leaving the full suite to the branch check.
- A working run's row in the pane and on the web page, and History's Cost and totals, say how long a run used the GPU
  as `Held GPU for 12 mins, waited 9 mins for it` instead of `GPU 12 min held, 9 min queued`.
- **Workflows**: The agent workflow's comment says an `@agent` mention reaches no one only on a private repository.
  (#12)
- A chore or fix tick that skips says whether the screen is locked or no desktop session is open. (#16)
- Memory defaults scale with the machine's total memory: `resources.reserveGB` defaults to 12.5% of it and
  `chores.minFreeGB` to 18.75%, and `agent-desk runners install` gives agent and test runners a `MemoryHigh` of 18.75%
  and chore and fix runners 12.5%, rounded up to whole GB. A 64 GB machine keeps 8, 12, 12G, and 8G. (#17)
- `./install.sh` leaves `CLAUDE_CODE_PLUGIN_DIRS` alone when the settings enable agent-desk from a marketplace, and
  `agent-desk doctor` accepts that plugin, warning when its version differs from the checkout's. (#22)
- The `self-hosted-runner` and `resource-report` skills find the agent-desk checkout through the `agent-desk` command,
  since a plugin installed from a marketplace runs them from Claude Code's plugin cache. (#22)
- **Workflows**: The `test-hosted` workflow runs only when started from its Run workflow button or by the public
  publish, and no longer on release PRs. (#53)
- The web page lists Needs Design by issue number, highest first, as it does Open, Blocked, and Ideas. (#63)
- The web page's History column shows each row's issue and PR numbers in a Number column of their own, as the list
  draws them: `#12 → #58`, the issue's in gray and the PR's in green, or the PR's alone for a chore. Both stay links
  to GitHub, and the Item column shows the title alone. (#65)
- Cleanup after a merge fast-forwards the main checkout and runs the sync only for a PR that merged into `main`. (#73)
- **Breaking**: **Workflows**: The `agent`, `chores`, and `fix` workflows no longer run agents on agent, chore, and
  fix runners. Each job runs on the test runner, asks the dispatcher for the run with `agent-desk request`, and ends,
  and the run works as a systemd unit on this machine. Turn the dispatcher on with `agent-desk dispatch install`
  before running `agent-desk install` in a project. A project keeps working on its runners until its workflows are
  rewritten, and `agent-desk doctor` names the workflows that still use them. (#74)
- A run the dispatcher started links its log in its notes as `journalctl --user -u <unit>` and names its unit in the
  line under each comment. A fix or a PR-only agent run that was cancelled or timed out says so on the PR, and the
  cleanup after a PR closes stops the unit working its issue. (#74)
- `agent-desk doctor` checks that the dispatcher's service is active, and checks for agent, chore, and fix runners
  only in a project whose workflows still use them. (#74)
- Restart on a stopped issue asks the dispatcher for the run, which starts within a second, where the dispatcher's
  service runs. On a desk without it, Restart dispatches the agent workflow as before. (#75)
- `agent-desk work-issue` and `agent-desk work-pr` read the issue, its labels, its comments, and its review from the
  local store, and post their notes and move the issue's state there in place of on GitHub. A parent issue's
  sub-issues and the notes that say a run was cut off come from the store too. (#80)
- The agent that `agent-desk work-pr` starts reads the review with `agent-desk review view` and comments with
  `agent-desk review comment`. (#80)
- The notes agent-desk posts in the store end with the visible "Posted by agent-desk" line alone, without the hidden
  `<!-- agent-desk -->` mark, since the store records each comment's author. (#80)
- The dispatcher holds an agent request for an issue that an open issue blocks, or whose serial group's slot another
  issue holds, before the request takes a slot. It moves the issue to `waiting` with a note, and starts the request
  as soon as a store event, such as the blocker closing or the holder leaving `in-progress`, clears the gate. (#81)
- `agent-desk serial claim` and `agent-desk serial release` read and set the issue's state, labels, and blockers in
  the local store in place of GitHub's labels. `serial release` asks the dispatcher for a run of each waiting issue it
  starts, and leaves an issue whose run already waits there. (#81)
- The dispatcher asks for a run of each `waiting` issue that has no request, once its blockers have closed and its
  serial slot is free, on a store event that may free it, an agent run's end, or its 30-second reconcile. An issue
  `agent-desk start` moved to `waiting`, or one whose waiting request couldn't start, no longer waits forever. (#81)
- The dispatcher leaves an issue `on-hold` or in `needs-design` in that state while its request waits on a blocker or
  a serial slot. (#81)
- Stopping a request that waits moves its issue from `waiting` back to the state it had before. (#81)
- `agent-desk fix` reads the review's branch, base, and state from the local store and posts its notes there, and
  learns whether the branch conflicts with its base from a trial merge with `git merge-tree` in place of GitHub's
  `mergeable` field. Its agent reads the review with `agent-desk review view` and comments with
  `agent-desk review comment`. (#82)
- `agent-desk chore` asks the dispatcher for a chore run with `agent-desk request chore` in place of starting the
  chores workflow, and a chore queued to run again asks for its next run the same way. (#82)
- A chore run tells its agent to open its review in the local store with `agent-desk review create`, and finds it
  there afterwards. (#82)
- `agent-desk chores tick` asks the dispatcher for a due chore with `agent-desk request chore`, and the chore schedule
  reads each chore's last start, whether it is under way, and how it ended from the local store's runs. A chore
  started with `agent-desk chore` now shows in `agent-desk chores status` as queued, then running, then done, and no
  tick asks for it again while it waits or works. A run that ended badly shows as `failed`, `cancelled`,
  `timed-out`, or `lost`. (#82)
- `agent-desk clean` reads and writes the issue and its reviews in the local store and stops the issue's runs by their
  units. A merge into `main` closes the issue with a "Landed on main" note, and a merge into another base moves the
  issue back to `open`. The dispatcher runs the cleanup in a unit of its own when a review merges or closes. (#83)
- `agent-desk release` reads the issue and posts its notes in the local store, and the dispatcher's note that a run on
  a review was cancelled or timed out goes on the review there. A run after a cancelled one takes up its session
  again. A failed run that never started goes back to `queued` with a new run request. (#83)
- The `work-issue`, `create-issue`, `audit`, and `resource-report` skills read and write issues and reviews with
  `agent-desk issue` and `agent-desk review` instead of `gh`. `work-issue` opens a local draft review, waits on its
  local checks, and marks it ready there, and `resource-report` reads each run's memory from its systemd unit. (#84)
- A run that takes up a cut-off session tells the agent to commit and open or update its draft review, as the
  `work-issue` skill says, instead of pushing and opening a PR. (#84)
- `work-issue` and `create-issue` say that `agent-desk shots` still uploads through GitHub, and that an agent whose
  upload fails keeps the pictures in its worktree and names their paths in the review. (#84)
- `agent-desk issue create` and `agent-desk review create` file the item with its labels and links in one
  transaction, so a link that fails files nothing. (#84)
- **Breaking**: The pane and the web page build the board from the local store in one `agent-desk board --json` read,
  where they read GitHub through `gh` and `lib/ghread.py`. A project shows an empty board until
  `agent-desk import <owner/repo>` fills the store. A PR's checks come from the branch checks on this machine, and
  Fix quotes the failed check's local log. A PR whose check's first attempt failed shows failing while the second
  attempt waits and runs. (#86)
- **Breaking**: A refresh of the pane or the web page no longer runs `agent-desk merges tick`, `failures tick`, or
  `fixes tick`; the merge and chore timers that `agent-desk chores on` starts run them, and the board shows what they
  last saved. Without the timers, the merge queue no longer moves. (#86)
- The web page shows a change to the store within a second: while a page is open, the server reads the store's newest
  event id every half second and pushes the boards of the projects that changed, where it refreshed every project
  each minute, or every 5 minutes while every page was hidden, and followed only run events between. It still reads
  every board once a minute, so a new project and a run's age show without an event. A page that reconnects, even to a
  server that just restarted, never draws a board older than one it showed. (#87)
- The web page says when its server can't read the store, and the server logs the first error of each streak and
  opens the store again when another file replaces it. (#87)
- The History column lists the reviews the store holds as merged, where it read merged PRs from GitHub, and the cost
  of the runs behind each, which `lib/run_log.py` now records on each run's row in the store. The chores' stats count
  each chore's finished runs from the store. (#87)
- Edit and Pause on a chore open or retitle the schedule review with `agent-desk review`, where they ran `gh pr`. (#87)
- The pane reads the board again within a second of any change to the local store. It reads the store's newest event
  id once a second from `desk.sqlite-event`, a file the store saves beside its database, in place of reading the
  whole board each minute, and still reads the board after a quiet minute for the rows' times and the merge queue's
  notes. Its header no longer shows when the board was read or a budget notice. Merge, Retry, and Restart show what
  the store reads right after their commands, and only the buttons that still write GitHub hold their moves through
  reads. (#88)
- The web page's detail panel reads issues, reviews, comments, labels, links, and checks from the local store, and a
  review's files from `git diff <base>...<branch>` in the main checkout, so it opens with the network down. A review
  whose branch is gone shows its record and comments and says its diff is unavailable, and a moved file shows its old
  path beside its new one. (#91)
- Descriptions and comments render on this machine, and a `#123` reference opens item 123 in the panel, even one the
  list has no card for, with its hovercard on hover. (#91)
- A comment posted from the page goes into the store as `you`, marked for the agent when it mentions `@agent`, and a
  check's Details link opens the check's log on the page. (#91)
- The pane's and the page's Close, Designed, Unblock, Agent-Ready, Ready, and Fix act on the local store through
  `agent-desk` instead of `gh`. Agent-Ready also asks the dispatcher for the issue's run, and Fix's comment reaches the
  agent through the store without an `@agent` prefix. (#92)
- **Breaking**: `agent-desk shots` saves each picture in `~/.local/state/agent-desk/assets/` and records it in the
  store instead of pushing it to the `pr-assets` branch, and prints Markdown that shows it from the web page's
  `/api/asset/<hash>`, so it needs no `gh` and no network. `--comment` adds the Markdown to the review in the local
  store, and each run deletes the pictures of reviews closed more than 30 days ago. Pictures that imported GitHub
  bodies show from `pr-assets` still show while the branch exists. (#93)
- The `work-issue` and `create-issue` skills no longer say that `agent-desk shots` uploads through GitHub. (#93)
- **Breaking**: The merge queue merges reviews from the local store in local git instead of GitHub PRs. It merges
  `main` into a branch in its worktree, tests heads with the local branch checks, fast-forwards `main` in the main
  checkout under its lock, records each review merged, runs `sync.run`, and stops with a note on the board when the
  main checkout has uncommitted changes to files the merge changes. A review whose worktree can't take `main` waits
  with a note while the queue goes on, and a review its base already holds, as after an interrupted tick, is recorded
  merged with the commit already there. (#94)
- `agent-desk fixes` reads reviews and runs from the local store and asks for each fix with `agent-desk request fix`.
  (#94)
- The cleanup after a merge no longer fast-forwards the main checkout or publishes a release; the push queue
  publishes a release once `origin` holds it. (#94)
- `just release` opens the release as a local review with `agent-desk review create` instead of a GitHub PR, and
  `just release publish` tags the version in local git, pushes the tag, and runs the host's release step. The cleanup
  after a merge into agent-desk's main runs it when `VERSION` changes. (#95)
- **Breaking**: `agent-desk install` no longer reads your `gh` login, creates GitHub labels, or writes the agent,
  agent-cleanup, chores, and fix workflows. It registers the project in the local store, and it works on a machine
  with no `gh` login. (#96)
- **Breaking**: `agent-desk doctor` no longer requires `gh`, and no longer checks a project's workflows, its GitHub
  labels, or its runners. (#96)
- `agent-desk start` reads the issue's open review from the local store and posts its note there. The chore timer
  counts open audit issues and finds a chore's open review in the store, and no longer waits on runners: the
  dispatcher holds a chore's run until the project has a free chore slot. (#96)
- The pane's and the web page's Retry, Restart, and chore Run buttons ask the dispatcher for the run, and never start
  a GitHub workflow. (#96)
- **Breaking**: **Workflows**: `agent-desk install` writes no workflows from `workflows/` into a project and installs no
  runners; `agent-desk cutover` disables the workflows an earlier install wrote. (#97)
- **Breaking**: `.agent-desk.json` rejects the `runners` key, and `agent-desk doctor` says to set run counts under
  `slots` and to move `runners.env` to a top-level `env`, which the dispatcher passes to each run. (#98)
- `agent-desk doctor` warns when the GitHub API budget queue, the GitHub response cache, or the failure write-ups'
  ledger is left on disk, and its fix deletes them. (#98)
- The web page shows a run the dispatcher queues, starts, or ends within about two seconds plus the board's read, and
  the pane within about three, instead of at the next refresh up to a minute later. The page's server reads the local
  store's run events every second while a page shows, and each pane every two seconds through a read-only
  `lib/dispatch.py events --repo <owner/repo>`. The web page and every pane share one board read for each event. (#115)
- The merge queue restarts `agent-desk-web.service` when a merge changes `web/` or `inbox/hooks/` in agent-desk's own
  checkout, so the web page runs the new code without a hand. (#117)
- The dispatcher ignores a request for a target that already has one waiting with the same environment, so two
  presses of a button queue one run. (#118)
- A fix of a review whose checks fail reads the failed local checks of the review's head from the local store and
  quotes their logs, and a chore's brief reads the chore's reviews from the store and their changed files from git, so
  neither asks GitHub. (#129)
- The web page reads its label colors and the chore PRs' states from the local store, so a refresh runs no `gh` and
  the page draws them with the network down. `agent-desk import` copies each label's color from GitHub, and a label
  it never saw takes a color hashed from its name. (#134)
- The web page shows Merge, Retry, and Restart the way the store reads them right after their commands, as the pane
  does, and drops a read of the board that began before a button's command returned. A PR the merge queue drops
  shows back in Needs You on the next read. (#139)
- The web page shows issues and reviews in its own panel from every link and key that opened them on GitHub: the
  number beside a card's title, the sidebar's Development links, `o` and Enter, and the History column's numbers and
  rows. `o` and Enter also open the panel again after you close it. A failed run's log and a runner's job still open
  on GitHub. (#149)
- The web page shows a picture in a description or comment only when it serves the picture itself, such as an
  `agent-desk shots` screenshot; any other picture, including an imported GitHub attachment, shows as a link with its
  alt text, so opening an item fetches nothing from another host. (#150)
- A review's Files Changed tab past 20,000 rows or 300 files lists every file with its counts and leaves out the lines
  of the largest files with a note, and a diff too large to read says so and still lists its files. (#150)
- **Breaking**: An issue labelled `base:<branch>` starts from the main checkout's local `<branch>`, and a run stops
  with a comment when that local branch is missing, even if origin has it. Create the branch in the main checkout
  before you label an issue with it. (#151)
- Agent runs, `@agent` returns to a review, and conflict fixes branch from, merge, and compare against the local base
  branch and fetch nothing, so they start with origin out of reach. A conflict fix commits its merge to the local
  branch for the push queue to publish, and `agent-desk worktree` cuts a branch from the local `<base>` or `main`.
  (#151)
- Fix conflicts in the pane asks the agent to merge `main` and commit, in place of `origin/main` and a push, and a
  chore schedule change cuts its branch from the local `main` and compares against it. (#151)
- Send to Agent on the web page names the head an older line comment was made on and the line it sits on now, and on
  a review whose branch is outside `agent/` it sends nothing and says no agent works that branch. (#164)
- Pressing a row in the pane opens its issue or review on the web page instead of GitHub, so a project with
  `"host": "none"` and an item that exists only on this machine open too. When the web page isn't running, the pane
  says to run `agent-desk web on`, or `agent-desk issue view <n>` to read the item in the terminal. (#174)
- The web page's failed and timed-out run cards say the dispatcher stopped the run and no longer link to GitHub, and a
  working card's Runner Job links are gone. (#174)
- The store creates or migrates the desk's database in `~/.local/state/agent-desk` only from the installed checkout,
  the one `agent-desk` on PATH runs or `AGENT_DESK_INSTALLED` names. Code run from any other checkout without its own
  `XDG_STATE_HOME` stops with an error that names the database, the checkout, and the fix. `agent-desk dispatch
  install`, `web on`, `chores on`, and `backup install` write `AGENT_DESK_INSTALLED` into their units, and
  `./install.sh` adds it to the units that exist. (#180)
- `agent-desk board`, `issue view` and `list`, `review view`, `list`, and `checks`, and the dispatcher's `runs`,
  `chore-runs`, and `stop --dry-run` open the store read-only, so they never create, migrate, or write it. (#180)
- Labelling an issue `agent-ready` from any place starts its run: the dispatcher asks for a run of each queued issue
  that no run was asked for since it moved to queued, as `agent-desk issue edit <n> --add-label agent-ready` leaves it,
  and one Agent-ready press still starts one run. (#182)
- Stopping or cancelling a request that waits on a blocker or serial slot moves a queued issue back to open, so the
  dispatcher never asks for its run again. (#182)
- The pane and the web page show Agent-ready, Designed, Unblock, Ready, and the page's Close the way the store reads
  them right after their commands, and stop holding those moves for two minutes. Fix still keeps its PR in Queued
  until the dispatcher's request for the agent's run shows, since the dispatcher writes that request after the
  comment. (#206)
- The web page drops the merge box at the foot of a review's conversation. Close Review moves to the review's header
  beside Merge, Fix, and Ready, asks to confirm, and takes the key `l`. A draft whose checks haven't passed loses the
  box's Ready for Review button and gets Fix in the header when its checks fail. (#213)
- The pane and the web page show a PR Fix sent back to its agent in Queued from the store's read right after the
  comment, and stop holding that move for two minutes. `agent-desk board --json` lists in `asked` the open reviews
  whose comments to the agent the dispatcher hasn't answered yet. (#217)
- The web page keeps a card's tabs in view under the compact bar as you scroll down its panel, and a tab picked there
  shows from its top. On a narrow window the compact bar and the tabs now stay at the window's top too. (#220)
- The web page's strip shows each project's dispatcher slots at all times: a line per project with each kind's busy and
  total count, such as `agent 1/3 · check 1/2`, a full kind in the attention tone, then a line for each busy slot,
  running branch checks included. `agent-desk board --json` carries the project's `slots` and its running `checks`
  for it. (#221)
- An `@agent` comment on an issue labelled `idea` starts a discussion run in place of `work-issue`, so it no longer
  implements the idea. (#226)
- The web page's strip shows All as one line of project tiles, each with the project's chip and a pip per agent slot,
  filled while busy, plus the chore, fix, check, or benchmark pips while one of that kind works. A tile with every
  agent slot busy reads "full" in the attention tone, its tooltip names its counts, and pressing it opens the
  project's tab. The strip no longer lists busy agents, which Working shows; local sessions and busy chores, fixes,
  and checks keep their rows. (#237)
- The web page's project tabs sit in a band of their own in larger text, each with a dot in its project's color, and
  the open tab takes that color in its underline and a light tint. Six project colors replace four, so five projects
  no longer share one, and the light-mode colors darken to pass WCAG AA. A tab's Needs You count shows in the
  attention tone. (#238)
- The web page shows an issue and its review as one page: the issue's title with the review's state and branches, one
  conversation that merges the issue's and the review's comments in the order they were posted, each marked with where
  it was posted, and one comment box that posts to the review while it is open and to the issue after. The sidebar's
  Development section lists the issue's earlier reviews, and pressing one shows its conversation and files. (#240)
- The web page shows "Restart the web server." in small text right under the header's gauges, in place of a
  full-width line between the header's buttons and the project tabs. (#248)
- The web page's Status sidebar shows a failing card's note in red after a failed icon, and the card's wait on its own
  line as "Waiting on you for 16m" after a clock, with the time it entered Needs You on hover, so the wait no longer
  reads as a retry countdown for the error. (#249)
- The web page's project tabs show each project's mode, Auto, Manual, or Hold, at all times, where they showed it only
  while the projects' modes differed, and read Sleep while the machine gets ready to sleep. (#253)
- The web page's header bar no longer repeats the last button's result after Get Ready to Sleep. A button whose
  command fails raises a toast after its project's name, such as "Acme: Couldn't close #12.", and one that works
  raises none, since its card's move shows it. (#255)
- Every open issue's comment box on the web page has Send to Agent beside Comment, as a review's and an idea's do, so a
  reply reaches the agent with one button instead of a typed `@agent`. On a plain issue it asks for a run of the issue,
  as an `@agent` comment does. A closed issue's box has none. (#263)
- The web page keeps a review's files bar, with Hide File Tree, the file count, Expand All, and Collapse All, in view
  under the tabs as its diffs scroll, and the file tree stays in view below it. A file picked in the tree now lands
  with its heading just below the files bar, where before the tabs covered it, and the tree marks that file. (#265)
- A comment sent to the agent always mentions `@agent`: Send to Agent on a review or an idea, the pane's Fix, and
  `--to-agent` on `agent-desk issue comment` or `agent-desk review comment` put `@agent ` in front of a body without
  the mention, so the conversation shows which comments went to the agent. (#267)
- **Breaking**: agent-desk names a chore by its skill's name as written, without dropping a `singleshot-` prefix, in its
  branches, run logs, memory history, model settings, `agent-desk chores status`, the pane, and the web page. A project
  that runs the upstream `singleshot-*` skills gets branches such as `agent/chore-singleshot-refactor-one-<time>`, and
  a model setting under `models.chores` takes the full skill name. (#280)
- **Breaking**: The default models for the comment and test chores key by `trim-comments` and `add-tests`, the names
  the chore skills take in erjand/agents, so `singleshot-comment-one` and `singleshot-untested-one` lose them; run
  `agent-desk chores rename` after renaming the skills. (#280)
- `agent-desk chores status` widens its chore column to fit the longest chore's name, and the pane reads each column
  from the header. (#280)
- The web page shows its runners in a Runners sidebar at the right, which `\` shows or hides in History's place. Each
  shown project lists under its chip and counts, such as `agent 2/3 · check 1/2`, with a row per slot naming what
  works in it, busy agents included, or `free`, and local sessions follow in a Local section. The strip over the list
  is gone, so the tabs sit right over the list. (#283)
- The web page's header stacks the gauges over a History button and a new Runners button, which shows or hides the
  Runners sidebar as `\` does. While the Runners sidebar shows, the gauges move into its head, under its title, and
  return to the header when it hides or the window is too narrow for it. (#286)
- The `work-issue`, `create-issue`, `close-parent`, and `discuss-idea` skills write each decision under
  `## Decisions Needed` as a numbered question heading with lettered options and the agent's pick marked ✅, and
  `work-issue` reads a short reply such as `1B` or `1 B, 2 A` as the chosen options. An answered decision's whole block
  goes, and the rest keep their numbers. (#296)
- The web page's button that closes an open or draft review reads Close Without Merging in place of Close Review, and
  its confirm and the `?` help read the same. (#299)
- The web page splits an issue's page into Issue and Review tabs in place of the one Conversation tab. Each tab shows
  its item's description and comments, counts its own comments, and ends in a comment box that posts to that item: the
  Issue tab's with Close and Close with Comment, and an open or draft review's with Send to Agent. A merged or closed
  review's tab points to the Issue tab in place of a box, and comments no longer carry an Issue or Review marker. A
  card in Needs You or Merging with a review opens on Review, and every other card opens on Issue. Picking a review or
  the issue in the Development section switches to its tab. (#307)
- The web page's project tabs drop the colored dot before each title; the open tab keeps its project's hue in its
  underline and tint. (#308)
- The web page's Runners sidebar shows one table with Runner, Item, Title, and Time columns under a muted header row,
  in place of a heading and a line of slot counts per project. On All a Project column names each project's group and
  a rule separates the groups, a kind with every slot busy shows its runner cells in the attention tone, and local
  sessions list in their project's group with `local` as their runner. (#312)
- The web page's header draws the machine's gauges in a pill beside the History and Runners buttons, in one row with
  them and the `?` button, whichever sidebar shows. The Runners sidebar no longer draws the gauges, and the restart
  notice shows on a line of its own under the row. (#317)
- The web page's letter hotkeys work with Shift or Caps Lock on, so `M` merges as `m` does and `Y` confirms as `y`
  does. (#319)
- The web page's Refresh button is a circular-arrow icon left of the "Updated" time, and the arrow spins while the
  boards read. The pressed Auto, Manual, or Hold button takes its mode's color from the project tabs, and the "Each
  project has its own mode" text is gone. (#332)
- The web page's header, project tab band, and list search bar each sit on a surface of their own, in both themes:
  the header's buttons on the deepest gray, the tabs one step lighter, and the search bar on the list's ground. The
  open tab's tint mixes into the band's new gray, and in light the header's warning, error, and healthy-gauge text
  deepens to keep AA contrast. (#335)
- A run's memory claim lasts until the run ends, instead of 10 minutes, and counts the part of its need the run hasn't
  allocated yet, so fewer runs start at once. (#344)
- The board warns when the merge queue stops on uncommitted changes in a project's main checkout or a worktree: Needs
  You leads with a warning row, which the web page shows among its header's notices, naming the files, the reviews
  they hold, and what clears them. The stopped review's card in Merging takes a `stopped` badge and icon and a note in
  the warning tone that starts with the files, and `agent-desk merges` names the main checkout or the worktree's
  folder instead of its full path. (#362)
- In Manual, an issue's agent starts only when you press Start on its row in Queued. Agent-ready, Retry on a failed
  agent run, and Restart on a stopped issue now queue the issue with a Start button instead of starting it. On Hold,
  each queued issue nobody picked shows Start too, which picks it without starting anything; its row reads
  `starts on Resume`, and Resume into Manual starts it. (#363)
- Closing an issue for any reason cancels each agent request that still waits for it. A run that already works the
  issue keeps working. (#370)
- The web page's keyboard shortcuts dialog groups its keys under Move Around, Issues, Runs, Reviews, Panels, and
  Confirm and Cancel, in as many columns as the window fits, so it fits on one screen on a wide window. (#376)
- The web page's Agent Log tab shows the time that leads each line in the accent color, so one entry stands apart
  from the next. (#377)
- A run's memory claim shrinks once the run has gone past its settle time, 1.5 times the 95th percentile of the minutes
  its project's past runs of the same kind took to reach their peaks, learned from at least 20 such runs. Past it, the
  claim counts as the larger of 1.5 times the run's peak so far and that peak plus 2 GB, so more runs can start while
  a heavy kind's runs sit far under their claims. `usage.tsv` records each run's minutes to its peak in a new last
  column, the dispatcher's log names each claim that shrinks, and `resources.shrinkClaims: false` turns shrinking off
  for a project. (#382)
- The web page's avatar circles show each author's initial on a color picked from their login, in place of an empty
  circle, so you, the agent, the desk, and imported authors read apart at a glance. (#401)
- `agent-desk issue close` drops the issue's Manual pick, so a reopened issue waits for Start again. (#418)
- `/agent-desk:close-parent` counts a sub-issue closed as a duplicate as its original does: completed once the
  original closed as completed, and unmet while the original is open or closed as not planned, so a parent no longer
  closes on a duplicate whose work is still open. (#419)
- A project that goes to Auto, by Auto or by a Resume into Auto, drops its Manual picks, so a Start pressed days ago
  no longer starts the issue the next time the project is in Manual. Hold, sleep, and Manual keep them. (#420)
- **Breaking**: Manual holds every agent, fix, and chore run until you press Start on its row in Queued, including
  runs from a comment sent to the agent, Fix, a chore's Start Now, and the merge queue's conflict fixes, which used to
  start on their own. A run going back to its review, a fix, and a chore each show in Queued with Start and the note
  `waits for Start`, and Start starts the requests that row shows and nothing else. Branch checks and cleanups still
  run. `lib/hold.py start-runs <run>...` picks requests by id, and `hold.py status` lists them as `pickedRuns`. (#422)
- The `work-issue` skill starts every review description with a `## Summary` of one or two plain sentences saying
  what the review changes for you and why, without file paths or function names, directly under
  `## Decisions Needed` when the review has one. (#456)
- Agents sign their comments with the unit of their run alone, and the `work-issue` skill no longer reads
  `$RUNNER_NAME`. (#477)
- Headless runs start Claude Code with `BASH_MAX_TIMEOUT_MS` set to 2 hours, so an agent can wait on a long command
  such as a benchmark in the foreground, instead of Claude Code moving it to the background at 10 minutes and the run
  stopping it after the agent's turn ends. A value you export or set in Claude Code's settings keeps its own. (#478)
- An agent run on an issue that ends its turn while a task it started still runs stops early: agent-desk ends the
  task, says on the issue and its review which command it stopped, and records the run as `stopped` rather than
  succeeded. Needs You shows the issue as stopped with Resume and Restart, and the next run takes up the same session
  and runs the command again in the foreground. (#479)
- Fix, chore, review, parent-check, and discussion runs that end because their agent left a task running say they
  stopped early on their review or issue, naming the command, and the store records them as `stopped` instead of
  `succeeded`. A chore that stopped early asks for no queued next run. (#481)
- A stopped issue's buttons in the pane and on the web page read Continue, which has the desk carry on the agent's
  run and is the row's main button, and Take Over, which copies the `claude --resume` command for a terminal. Retry
  on an agent's timed-out run on an issue reads Continue too, and the detail view's Copy Resume Command reads Copy
  Take Over Command. Resume names only Hold's and an on-hold issue's button. (#489)
- `agent-desk cutover` leaves a project's Issues on at GitHub, so the issues there stay readable and links to them keep
  working. `--issues-off` turns them off as before, and the undo turns back on only what a cutover turned off. (#97)
- The web page's Runners sidebar sets its column headers in small, spaced capitals over a rule, apart from the rows
  under them.
- The web page's header says "Restart the web server." when its checkout holds newer server code, in place of a
  sentence naming the checkout and both ways to restart.

### Removed

- **Breaking**: The run scripts no longer wait at their start for Hold, Manual, or sleep, since the dispatcher keeps
  those gates. Runs that workflows from an earlier agent-desk start on agent, chore, and fix runners start whatever
  the desk's mode; run `agent-desk install` to rewrite those workflows. (#81)
- The chores timer no longer runs `agent-desk serial release` every 5 minutes. (#81)
- **Breaking**: The board's GitHub API budget notice and the board that agent-desk processes shared under
  `lib/ghread.py claim`, since a refresh no longer spends the budget. The board no longer shows GitHub Actions runners
  or their runs, or failed Actions agent runs; Runners and Needs You show the dispatcher's runs. (#86)
- **Breaking**: agent-desk no longer writes `~/.local/state/agent-desk/runs.jsonl`. A run records its model, effort,
  length, turns, cost, review, labels, and start in the store's `runs` table, and only a run the dispatcher started
  records them. The page shows no cost or chore stats for runs logged in the old file. (#87)
- `lib/dispatch.py events` and `inbox/hooks/watch.ts`, with which each pane read its project's run events every two
  seconds. The pane's watch of `desk.sqlite-event` covers every event, run events among them, without starting a
  process. (#88)
- The panel no longer shows GitHub avatars or reactions, and the server no longer reads your login with
  `gh api user`. (#91)
- **Workflows**: The `release` and `publish` workflows, with the weekly scheduled release. Run `just release` to cut
  one. (#95)
- **Breaking**: **Workflows**: The agent, agent-cleanup, chores, and fix workflow templates in `workflows/`, and the
  `self-hosted-runner` skill that installed the runners they ran on. (#98)
- **Breaking**: `agent-desk runners`, which installed and started the self-hosted runners, and `just runners`. (#98)
- **Breaking**: `agent-desk failures`, which wrote up failed GitHub Actions runs on their PRs and reran them. Fix
  still quotes what failed from a local check's log. (#98)
- **Breaking**: Get Ready to Sleep and Stop and Requeue on the web page, and `agent-desk sleep`. Hold before a suspend
  keeps the runs working, and they carry on after the machine wakes. (#98)
- **Breaking**: `agent-desk chore-pr`, the old name of `agent-desk work-pr`. (#98)
- Runs no longer wait on the GitHub API budget, and the `github.reserveCalls` config key does nothing. (#98)

### Fixed

- `agent-desk shots --comment` posts its comment instead of stopping on an unset variable. (#7)
- The self-hosted-runner installer pins runners to one L3 domain only when the domains differ in size, so a CPU with
  equal domains, such as a Ryzen 9 7950X, keeps every core for jobs. (#18)
- An auto-fix of a PR into a branch other than main, as a `base:<branch>` label makes, retries when that branch moves
  instead of waiting on main, and its note names the branch, such as `auto-fix tried this local-desk; retries when
  local-desk moves`. (#100)
- `agent-desk clean` keeps a local branch whose commits neither `origin` nor the review's base holds, such as a
  branch never pushed, even after its worktree was removed by hand. (#135)
- The dispatcher starts the cleanup of a review that merged or closed while it was down, and of a review closed with
  any close reason. (#135)
- The pane no longer misses a store change until its quiet-minute read. Two writes that save the store's newest
  event id at once can no longer leave `desk.sqlite-event` holding the older id, and a board read that fails is tried
  again on the next second. (#146)
- A merged review that GitHub rebase-merged shows every commit of the review instead of the last one. `agent-desk
  import` records how many commits the merge wrote; rerun it for PRs imported earlier. (#150)
- A local branch check tests the head merged with the base of the branch's open review, such as `local-desk` for a
  review a `base:<branch>` label opened, instead of always with main, and a head that base already holds gets no
  check. The check's log names the base it merged. (#159)
- A comment sent to the agent while the dispatcher is stopped or restarting, such as from Send to Agent on the page
  or Fix in the pane, starts its agent run once the dispatcher is back. The dispatcher records its answer to each such
  comment in the store and answers the ones since its last answer on start and every 30 seconds. (#163)
- Send to Agent sends every line comment the agent has not seen and none twice. A line comment followed by an `@agent`
  comment or an agent's own comment no longer shows as sent. (#164)
- The web page refuses a line comment on a merged or closed review, on a file the diff doesn't change, or on a head
  that isn't one of the review's commits. (#164)
- Close with Comment on the web page and the pane leaves no comment behind when the close fails. (#164)
- `agent-desk backup` writes each project's label colors to `label_colors.json` beside its `project.json`, and
  `agent-desk restore` from the backup repository brings them back, where every label took a hashed color before.
  (#169)
- On the web page, `o` or Enter on a failed or timed-out run's card shows its Agent Log tab when the run worked an
  issue, and selects the card otherwise, instead of opening a second copy of the page in a new tab. (#175)
- Pressing a busy runner on the web page whose card the list doesn't show opens its issue or PR in the panel instead
  of a new tab, and a chore's pending schedule links to its schedule review on the page instead of GitHub. (#175)
- Every command, the pane, and the web page find a project whose origin is a path on disk, or which has no remote, by
  the name `~/.config/agent-desk/projects` records for its main checkout, so it no longer needs `GH_REPO`. (#183)
- `agent-desk cutover` commits the tracker line to AGENTS.md only when the main checkout is on `main`, with nothing
  staged and no merge, rebase, cherry-pick, or revert in progress, and otherwise leaves the line for you. A commit
  that fails puts the file back, so a rerun adds the line again. (#184)
- `agent-desk backup` writes every comment's `sent_in`, and a restore from an older backup counts each line comment
  without one as sent, so the next Send to Agent doesn't repeat old line comments. (#184)
- `agent-desk cutover` wraps the Tracker line it adds to a project's AGENTS.md at 120 columns, so a pre-commit hook
  that lints Markdown line length takes the commit. When the commit fails anyway, the cutover prints the line for you
  to add and goes on instead of stopping at the agents step. (#193)
- A merge into agent-desk's own main restarts the dispatcher when it changes any `lib/` module `lib/dispatch.py`
  imports, directly or through other modules, so the dispatcher no longer keeps running old code from `lib/checks.py`,
  `lib/hold.py`, `lib/memory.py`, and the other modules it loads. (#201)
- `agent-desk cutover` prints its usage with one `usage:` prefix when it rejects an argument, such as
  `agent-desk cutover --bogus`, where it printed `usage: usage:` before. (#215)
- Resume, Auto, Manual, and Start drop each GitHub run noted for Resume whose project has cut over to the local store,
  printing a line for each, instead of trying to rerun it on GitHub and leaving the header on resuming. (#229)
- `agent-desk cutover` refuses to start while `hold.json` notes GitHub runs of the project for Resume, and names each
  run with its issue and how to clear it. (#229)
- In Manual, Retry on a failed agent run and Restart on a stopped issue start a run an `agent-ready` label asked for
  again, instead of leaving it waiting for a Start button the board doesn't show. `lib/dispatch.py again` picks the
  run's issue before it asks for the run. (#233)
- agent-desk's own `.agent-desk.json` sets `"test": "just test"`, so its reviews get branch checks again. (#234)
- After Send to Agent, the web page keeps showing the review while the agent works it: an issue's rows in Queued and
  Working name its open review, as `#588 → #640`, and the pane lists a review waiting for its agent once, with its
  Cancel run button. (#239)
- A failed check's note in Needs You, the Fix comment, and failure write-ups name the failing test instead of a
  `ResourceWarning` or other warning printed before it, and no longer take a file name such as `error.wav` or a
  `0 error(s)` summary for the failure. When no line names the failure, they quote the line before the runner's
  `error: recipe … failed` line. (#246)
- In Manual, a serial group's slot goes to an issue you picked. A queued serial issue that waits for Start no longer
  holds its group's slot, and a waiting serial issue starts only once picked, so the issues behind an unpicked one can
  start. A waiting serial issue nobody picked has a Start button, and its note names the issue ahead of it, such as
  `waiting on #506, which waits for Start`. (#250)
- The desk sees a review that conflicts with main as conflicting, so an auto-fix resolves it, where git's `merge-tree
  --quiet` had called some conflicting merges clean and left the review in the merge queue with git's first output
  line as its note. A worktree merge that conflicts anyway names the conflicting files in its note. (#254)
- On a window narrower than 900 pixels, the web page stacks the list above the selected card while the History column
  or the Runners sidebar is shown, as it does with both hidden, instead of squeezing the card into a strip beside the
  list and scrolling sideways. (#269)
- The web page's hidden tooltips on Auto, Manual, Hold, and Get Ready to Sleep no longer widen the page, so a
  phone-width window no longer scrolls sideways because of them, and a tooltip near the window's right edge shifts
  left to stay in the window. (#279)
- The web page's keyboard help, opened with `?`, shows over the list's search box and the review panel's tabs and
  Files Changed bar, which hid some of its rows. (#305)
- The web page's `?` keyboard help scrolls in a window too short for its table, starting at its first row, and still
  sits centred when the whole table fits. (#316)
- The web page's Runners sidebar caps its Project column at 12 characters on All, so a long project title no longer
  squeezes the Title column until its header runs into Time. A longer chip ends in an ellipsis and shows its full
  title on hover. (#324)
- The web page's file tree on a review's Files Changed tab shows the tails of g, j, p, q, and y and the underscore in
  file and folder names whole, where it cut them off before. (#336)
- A picture opened from a description or comment that has a Decisions Needed section shows centred in the window
  instead of against its top. (#342)
- A line comment's thread in a review's Files Changed fits the width the diff shows, so its comment box and buttons
  stay in view in a narrow panel, and it stays in view while a long line scrolls the diff sideways. (#355)
- The web page reads every board again within a second when `agent-desk hold`, `agent-desk resume`, or `lib/hold.py`
  changes the mode or the picks outside the page, so each queued issue's Start button and note match the mode the
  header shows. (#373)
- A project on Hold starts the branch checks of the runs still working, so an agent that waits for its check with
  `agent-desk review checks --watch` finishes and frees its runner and memory. Checks of other branches still wait for
  Resume, and Get Ready to Sleep starts none. (#383)
- An agent, fix, chore, or discussion run ends 30 seconds after its agent's final message, even when the agent left a
  task such as `agent-desk review checks --watch` running in the background, so the run frees its runner and its
  memory claim at once. Runs also forbid the Monitor tool, which kept a run alive the same way. (#384)
- The web page's file tree on Files Changed marks the file you're reading every time: a click marks the file even
  near the end of a review, the last files take the mark in turn as the diffs reach the bottom, the mark follows
  Expand All, Collapse All, a folded diff, and another review, a folded folder marks the file inside it, and the tree
  scrolls to show the marked row. (#388)
- A dispatcher restart keeps the check of a branch head that moved within the minute before it, instead of dropping
  it until the branch moves again. (#399)
- The memory pressure check reads the pressure your apps feel in `app.slice` and `session.slice`, falling back to
  `/proc/pressure/memory` only without them, so a run the kernel stalls at its own `MemoryHigh` no longer holds every
  queued run. (#431)
- A finished draft review whose branch conflicts with main shows `conflicts with main` and a Fix button in Needs You
  on the pane and the web page, and Fix moves it to Queued as `fixing conflicts`. It showed only Open before. (#444)
- A waiting issue in no serial group whose blockers closed no longer reads `waiting on the serial slot` in Queued on
  the pane and the web page. Its row reads only why the dispatcher still holds its request, such as `waits for a free
  agent slot`, or its title alone. (#450)
- A headless run reaps the processes it adopts when their parent exits, so a zombie no longer lingers until the run
  ends and a project's test that checks a killed process is gone passes inside a run. (#455)
- A comment sent to the agent on an issue on hold, such as one Pause stopped, no longer starts a run at once. The
  dispatcher holds every agent request for an on-hold issue, and Queued shows it waiting for the issue to resume,
  with Resume on its card. After Resume, a request that waited starts as the issue's one run. (#471)
- A run that removes its worktree, after a failed setup or when it opened no review, first ends the processes it left
  there, such as a compiler server. (#477)
- `agent-desk gpu --exclusive` also holds every other slot's lock file it finds, so a caller that counts more slots
  can't render beside a benchmark. (#492)
- A working run's "N min left" note on the pane and the web page counts down from the time limit its unit runs
  under, so a project that sets `limitMinutes` sees it near its own limit. (#497)
- `agent-desk gpu` names every checkout that holds a GPU slot, in its waiting line, its record, and
  `agent-desk gpu slots`, while other processes take and release locks. Under load it sometimes dropped a holder.
  (#508)

### Security

- The web page's Content-Security-Policy loads pictures only from the page itself, so a picture from GitHub's image
  hosts or any other host can't load. (#154)
- The web page serves a saved picture only when its real path, with symlinks followed, lies inside the assets folder,
  and `agent-desk shots` reads a picture named like an option as a file and strips `[`, `]`, `\`, and line breaks
  from its alt text. `shots` no longer deletes the pictures it just saved for a review closed over 30 days ago. (#164)
- `agent-desk merges add` refuses to queue a merge from inside an agent run, so an agent can't merge its own review
  into main. Merge in the pane, on the web page, or from your own terminal queues as before. (#258)
- The web page's server answers only processes of the user it runs as. It looks up each connection's peer in
  `/proc/net/tcp` and refuses with 403 a connection that another account opened, so another account on the machine
  can no longer read the boards or press Start or Merge by sending the page's headers itself. (#259)
- Opening the store creates the state folder, `~/.local/state/agent-desk`, with mode 0700 and narrows an existing one
  that group or others can enter, so other accounts on the machine can't read the store, check logs, snapshots, or
  screenshots. `agent-desk doctor` warns about a state folder they can enter and prints the `chmod` that fixes it.
  (#260)
- `agent-desk backup` refuses to push to a public GitHub repository, asking `gh repo view` for its visibility before
  each push to github.com, and skips the push with an error on the board when the repository is public or `gh` can't
  read it. Commits still land in the backup repository, and the next run asks again. (#261)
- `agent-desk import` marks an `@agent` comment as sent to the agent only when you wrote it, so another GitHub
  account's `@agent` comment arrives as an ordinary comment instead of as your request to the agent. (#262)

## [0.1.0] - 2026-11-11

The first numbered release. It covers the work since agent-desk moved out of the agents repository into a repository
of its own.

### Added

- **Workflows**: `VERSION` holds agent-desk's version, which `agent-desk --version` prints and the plugin's
  `plugin.json` matches. `agent-desk install` writes it into each workflow's header, and `agent-desk doctor` warns when
  a project's workflows came from an older version. (#24)
- `agent-desk doctor` checks the machine and the current project, prints a fix under each warning and failure, and
  exits 1 when a check fails. (#41)
- `agent-desk install` creates the labels agent-desk uses that a repository lacks, with their colors and descriptions.
  (#40)
- `agent-desk install` warns when the `agent-desk` on PATH runs another checkout, or when a runner's service lacks its
  folder on PATH. (#31)
- A root `install.sh` links `agent-desk` onto PATH, loads the plugin in every Claude Code session through
  `CLAUDE_CODE_PLUGIN_DIRS`, and points the systemd user units at the checkout. (#33)
- `agent-desk runners install` and `agent-desk runners add` set up a personal repository's runners as well as an
  organization's pool. (#44)
- agent-desk works its own issues as an agent-desk project, with chore and fix runners of its own. (#28, #42)

### Changed

- **Workflows**: The workflows, the pane, and the web page run `agent-desk` from PATH instead of
  `~/.agents/agent-desk`, and `AGENT_DESK_HOME` picks a checkout ahead of PATH. (#31)
- Headless runs and the pane call the bundled skills by their plugin names, such as `/agent-desk:work-issue`. (#32)
- The main checkout of this repository runs the live desk, and the `~/.agents/agent-desk` link is gone. (#35, #45)
- README.md runs the tests with `just test` (#30) and lists the singleshot chore skills as optional installs (#39).

### Fixed

- A run that claims an issue takes `needs-design` off it. (#36)

[Unreleased]: https://github.com/erjand/agent-desk/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/erjand/agent-desk/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/erjand/agent-desk/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/erjand/agent-desk/releases/tag/v0.1.0
