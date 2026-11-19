# Security

agent-desk runs headless Claude Code agents on your machine, under your account, when you ask for them through its
local store. Read this before you install it: it covers who can start a run, what an agent can reach, and what
agent-desk does on GitHub in your name.

## Reporting a vulnerability

Report a vulnerability through a private security advisory on the public repository:
<https://github.com/erjand/agent-desk/security/advisories/new>. Say what an attacker can do, what they need to do it,
and how to reproduce it. Keep the details out of public issues, PRs, and discussions until a fix is released.

## Who can start a run

Every run starts from a request in the local store, the SQLite database at `~/.local/state/agent-desk/desk.sqlite`.
`agent-desk request` writes one, and so do the pane's and the web page's buttons and the dispatcher's own wake of a
waiting issue. `agent-desk-dispatch.service` reads each request and starts its run as a
systemd user unit.

Any process running as your account can write to the database or run `agent-desk request`, so any such process can
start a run. Nothing on GitHub starts one: no label, comment, review, or pull request there reaches the dispatcher, so
another GitHub account can't start a run on your machine.

The store stamps the author of each request, comment, and event from the environment of the process that writes it:
`agent` when `AGENT_DESK_RUN` is set, as it is in every run the dispatcher starts, `desk` when agent-desk's own scripts
write with `AGENT_DESK_AUTHOR=desk`, and `you` otherwise. The dispatcher ignores a request whose author is `agent`, so
an agent can't start runs of its own by asking for them. `agent-desk merges add` reads the same variable and refuses to
queue a merge inside a run, so merging a review stays your choice, made from the page, the pane, or your own terminal.
The stamp keeps a run from starting more runs or queuing a merge by mistake. It isn't a boundary against a hostile
agent: an agent runs as your account, so it can clear the variable, write the queue file, or write the database
directly.

## Public repositories

The agents read issues and reviews from the local store, and nothing a stranger posts on GitHub reaches the store
unless you import it with `agent-desk import`. Read what an import brings in before you label an issue `agent-ready`:
the agent treats what it reads as its instructions, and a comment written to steer it can make it misuse what it
reaches.

agent-desk's own repositories follow this model. Development and agent-desk's own agents stay in a private repository.
Each release publishes a snapshot to the public `erjand/agent-desk`, whose CI runs only on GitHub-hosted runners, and
outside PRs to it are ported to the private repository by hand. The snapshot is one commit holding the release's files,
whose only parent is the previous release's snapshot, so the public history carries none of the private commits,
issues, or reviews. Before a snapshot goes public, a check in the private repository refuses one that shares a commit
with the private history.

## What headless agents can reach

Each run starts `claude -p` with `--permission-mode auto` from `lib/work-issue.sh`, `lib/work-pr.sh`, `lib/fix.sh`, or
`lib/run-chore.sh`, as your account, in a worktree beside the project's main checkout. Auto mode lets the agent run
commands and edit files without asking, and Claude Code's own checks are its only guard. The agent can reach whatever
your account can:

- the local store, where it can read and change every project's issues and reviews;
- your `gh` login and its token, when you have one, with every scope it holds, across every repository it can reach;
- your SSH keys, Git credentials, and anything else in your home folder, such as `~/.claude` and other projects;
- the network, the machine's other services, and agent-desk's state under `~/.local/state/agent-desk`.

## Who can read agent-desk's state

agent-desk keeps its store, check logs, snapshots, and screenshots in its state folder, `~/.local/state/agent-desk`,
or `agent-desk` under `XDG_STATE_HOME`. The folder is private to your account: opening the store creates it with mode
0700 and narrows it to 0700 when group or others can enter it, and `agent-desk doctor` warns when they can. Other
accounts on the machine can't read what agents and comments wrote there. `~/.config/agent-desk/projects`, which names
each project and its checkout, keeps the umask's mode.

Keep secrets out of the repository and out of `env` in `.agent-desk.json`, which the dispatcher passes to
each run.

## What agent-desk does as you on GitHub

agent-desk's daily work stays on your machine, and nothing it writes to the store reaches GitHub. Git pushes to
origin, such as an agent's branch, go out with your Git credentials. A project whose `host` is `github` publishes its
releases through your `gh` login. GitHub shows all of it as yours. `agent-desk shots` keeps screenshots in the state
folder on your machine.

## The web page

`agent-desk web` serves a page that changes your issues and reviews and starts runs, and any page in your browser can
send requests to your machine. The server listens on 127.0.0.1 only and answers only your own processes: it looks up
each connection in the kernel's socket table, `/proc/net/tcp`, and refuses one that another account opened or that the
table doesn't list, since any account on the machine can reach 127.0.0.1 and send the page's own headers. It refuses a
request whose Host header names anything but itself, a POST from any other origin, and a POST that isn't JSON, and no
other page may frame it. The page loads no script from elsewhere, loads pictures only from itself, shows any other
picture as a link, and drops scripts, styles, and event handlers from rendered Markdown before showing it.
[The web page](docs/reference.md#the-web-page) describes it.
