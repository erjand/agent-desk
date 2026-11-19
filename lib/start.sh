#!/usr/bin/env bash
# Starts an interactive session's work on an issue, as agent-desk work-issue does for a headless run:
#   agent-desk start 12
# Claims the issue, creates ../<repo>-issue-<n> on agent/issue-<n> or continues the branch, and posts a note naming
# the session. The inbox reads the note's process to show the issue as worked here while the session runs, and as
# stopped, with the command that resumes it, once the session ends. Exits 3 when a serial issue waits behind
# another, as serial claim does.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
start_main() {
    issue=${1:?usage: agent-desk start <issue>}
    source "$(dirname "$0")/common.sh"
    slug="issue-$issue"
    dest=$(desk_worktree "$slug")

    # A headless run holds this lock while it works the issue.
    exec 8>"$main/.git/agent-issue-$issue.lock"
    if ! flock -n 8; then
        echo "An agent run is working #$issue now; wait for it to finish or stop it first." >&2
        exit 1
    fi

    claim=0
    "$desk_lib/serial.sh" claim "$issue" || claim=$?
    if ((claim != 0)); then
        exit "$claim"
    fi
    # A failed setup, or a base:<branch> label naming a branch origin lacks, gives the claim back, so the issue doesn't
    # sit in-progress with no one on it. The worktree branches from that base, or from main. serial claim moved the
    # issue to in-progress in the local store, so the issue goes back to open there.
    if [[ ! -d $dest ]]; then
        labels=$(desk_store issue view "$issue" --json labels | jq -r '[.labels[].name] | join(",")')
        base=$(desk_issue_bases "$labels" | sed -n 1p)
        if ! (cd "$main" && "$desk_lib/worktree.sh" "$slug" "${base:-main}") 8>&-; then
            if [[ -z $(desk_open_review "agent/$slug") ]]; then
                desk_leave_state "$issue" in-progress
            fi
            "$desk_lib/serial.sh" release
            exit 1
        fi
    fi

    # Claude Code sets both for the commands a session runs. The process is the session's own, which lives as long as
    # it.
    note="An agent started on \`agent/$slug\` in a local session"
    if [[ -n ${CLAUDE_PID:-} ]]; then
        note+=" (pid $CLAUDE_PID on \`$(uname -n)\`)"
    fi
    note+="."
    if [[ -n ${CLAUDE_CODE_SESSION_ID:-} ]]; then
        note+=" To take over, run \`claude --resume $CLAUDE_CODE_SESSION_ID\` in \`$dest\`."
    fi
    desk_note issue "$issue" "$note"
    echo "Working #$issue in $dest"
}

start_main "$@"; exit
