---
name: resource-report
description: Report what is using this workstation's memory and CPU, by project, agent, and app, and list what could be freed, such as stale temp folders, finished worktrees, and leftover game or build processes, without changing anything. Use when the user asks why memory or swap is full, what agents are consuming, or what can be cleaned up, or from the agent-desk pane's Report button or the web page's Audit with Claude button.
---

# Resource Report

Find what is using the machine's memory and CPU, and what could be freed, then report it and change nothing. The user
decides what goes; act only on items they name, in the same conversation.

The skill runs in two places. The pane's Report button runs it in the user's Claude Code session. The web page's
Resources view runs it in the desk chat through its Audit with Claude button, with the figures the view showed as
its arguments: the memory free, the share the dispatcher keeps for the user, and the runs the dispatcher holds for
memory. Start the report from those figures and say how they changed by the time you read the machine. In the desk
chat, each message the user sends continues the same session, so they name the items to free in later messages, and
the rules below hold there as here.

## Step 1: Read the machine's state

1. Memory, swap, and the OOM guard: `free -h`, `zramctl`, `df -h /tmp`, and earlyoom's thresholds from
   `systemctl cat earlyoom` and `/etc/default/earlyoom`. When swap is zram, it keeps compressed pages in RAM, so its
   compressed size already counts in RAM used. earlyoom stops the largest process only when free RAM
   and free swap are both under its lines, so with swap spent, free RAM is the margin that matters.
2. CPU: `uptime`, and two samples of the busiest processes a few seconds apart, such as `top -b -n 2 -d 3 -o %CPU`.
3. Processes by memory, with how much of each is swapped out:
   `ps -eo pid,ppid,rss,etime,args --sort=-rss | head -30`, and `VmSwap` from `/proc/<pid>/status`, summed by process
   name so a browser's content processes show as one row:

   ```bash
   cat /proc/[0-9]*/status 2>/dev/null | awk '/^Name:/ { n = substr($0, 7) } /^VmSwap:/ { s[n] += $2; t += $2 }
     END { for (n in s) if (s[n]) printf "%8.1f MB  %s\n", s[n] / 1024, n; printf "%8.1f MB  total\n", t / 1024 }' |
     sort -rn | head -20
   ```

   The `cat` skips a process that exits while it reads; awk given the files itself stops at the first one.

## Step 2: Attribute it

Group the processes and say who each group belongs to. A process's working directory, from
`readlink /proc/<pid>/cwd`, names its checkout.

- **Agents.** Agent-desk runs each agent in `../<repo>-issue-<n>` or `../<repo>-chore-*` beside the project's main
  checkout. Its projects file, `${XDG_CONFIG_HOME:-~/.config}/agent-desk/projects`, lists each project's repository
  and main checkout, and its state, such as the run log `runs.jsonl`, sits in
  `${XDG_STATE_HOME:-~/.local/state}/agent-desk/`. Its own checkout is the one the `agent-desk` command on PATH runs,
  two folders above `readlink -f "$(command -v agent-desk)"`; a plugin installed from a marketplace has a copy of this
  skill in Claude Code's plugin cache, apart from the checkout. Name each agent's issue, and whether it is still
  running: its issue's status is `in-progress`, and its run is an active systemd user unit named
  `agent-desk-<kind>-<project>-<target>-<id>.service`, which `systemctl --user list-units 'agent-desk-*.service'
  --state=active` lists beside the dispatcher's own unit. Read each run's memory, swap, and CPU time with
  `systemctl --user show <unit> -p MemoryCurrent,MemorySwapCurrent,CPUUsageNSec`, which count every process the run
  started.
- **The user's sessions.** Interactive Claude Code sessions, the games, editors, and servers they started, and their
  scratchpads under `/tmp/claude-<uid>/<project>/<session>/`. A session is live while its transcript,
  `~/.claude/projects/<project>/<session>.jsonl`, changed in the last hour or a process has files open in its folder.
- **The user's apps.** IDEs, browsers, the desktop shell, and indexers such as `baloo_file`. Note the ones idle for
  days with much of their memory swapped out, but never suggest closing one by inference alone; name it and let the
  user judge.

## Step 3: Find what could be freed

Check each candidate before listing it, and give its size, its age (newest file inside), and its owner.

- **Temp files.** `/tmp` is a tmpfs, so its files sit in RAM and swap. zram's `DATA` from `zramctl` holds the swapped
  pages of every process, which the `VmSwap` sum from Step 1 gives, plus the swapped part of the tmpfs, which is at
  most its used size from `df -h /tmp`. Name both figures in the report, so the user sees how much of the swap
  temp files hold. `/tmp` can hold over 100,000 entries, more than a shell glob such as `/tmp/*` passes to one
  command, and a list of each one buries the leaks. Size every entry without a glob and group the entries by name with
  the random suffix taken off, so a thousand `agent-desk-web-*` folders show as one row with a count and a total:

  ```bash
  find /tmp -mindepth 1 -maxdepth 1 -print0 | du -xs --files0-from=- 2>/dev/null |
    awk -F'\t' '{ n = $2; sub(/^.*\//, "", n)
      if (match(n, /[-._][A-Za-z0-9_]{6,}$/)) n = substr(n, 1, RSTART) "*"
      else if (match(n, /[A-Za-z0-9_]{6}$/)) { t = substr(n, RSTART)
        if ((t ~ /[A-Z]/) + (t ~ /[a-z]/) + (t ~ /[0-9_]/) >= 2) n = substr(n, 1, RSTART - 1) "*" }
      gsub(/[0-9]+/, "N", n); kb[n] += $1; c[n]++ }
      END { for (n in kb) printf "%9.1f MB %7d  %s\n", kb[n] / 1024, c[n], n }' | sort -rn | head -40
  ```

  Sort by the count column, `sort -k3,3rn`, to find a group of many small entries. The row named `*` holds names
  that are random throughout. List a group's members with `find /tmp -maxdepth 1 -name '<prefix>*'`, and size the
  session scratchpads inside `claude-<uid>` the same way:
  `find /tmp/claude-$(id -u) -mindepth 2 -maxdepth 2 -print0 | du -xs --files0-from=- | sort -rn | head -20`.

  A folder is a candidate when nothing inside changed in the last day and no process has it as its working directory
  or holds a file open in it (`/proc/*/cwd` and `/proc/*/fd`). `find <dir> -mmin -1440 -print -quit` prints the
  first path in the folder changed in the last day, and nothing for an untouched folder. Claude Code's shell runs
  `find` as bfs, which rejects relative dates such as `-newermt '-1 day'` with an error on stderr and prints nothing,
  so a broken check makes every folder look untouched: trust its silence only when it exits 0. Leave apps' own
  folders, such as browser, IDE, `.dotnet`, and daemon sockets, alone.
- **Scratch folders.** `.scratch/` in each checkout, which the projects' Memory rule uses for large intermediate files.
- **Worktrees.** For each worktree of each registered project, `git worktree list`, its branch, its review's state
  (`agent-desk review list --state all --head <branch> --json number,state`), commits not in `origin/main`,
  uncommitted changes, and processes inside. One whose review merged or closed, with nothing uncommitted beyond
  generated files main already tracks, is a candidate.
  Never list the worktrees a project's AGENTS.md or the user's memory says to keep.
- **Leftover processes.** Games, benchmarks, build servers such as `VBCSCompiler`, Blender, Godot editors, and local
  servers whose checkout has no live session or running agent.

## Step 4: Report

Lead with one line on the state: free RAM, free swap, CPU, and how far free RAM is from earlyoom's line. Then two
tables, largest first:

1. **Using it now**: group, owner, memory (resident and swapped), CPU, and whether it's live.
2. **Could be freed**: item, size, age, owner, why it's safe, and the command that frees it.

Number the rows of the second table so the user can answer with numbers. End with the two or three moves that free
the most for the least risk. If nothing is worth freeing, say so.

## Rules

- **Change nothing until the user names what goes.** Then free only those items, and check each again first: a folder
  that changed or gained an open file since the report stays, and you say why.
- **Never discard work you didn't create without that answer.** Uncommitted changes, unpushed commits, and files a live
  session or running agent uses are not candidates, however stale they look.
- **Stop processes only on the user's word,** and only ones a finished session or agent left behind. Never stop
  another session's running work or the user's apps.
