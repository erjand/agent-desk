#!/usr/bin/env bash
# Checks a parent issue whose sub-issues have all closed with a headless agent, which closes the issue or
# says what is left:
#   lib/close-parent.sh 12
# work-issue.sh runs it in place of a full run when the agent-ready label reaches a parent issue with at least one
# sub-issue and none open, so the run keeps the issue's agent-<n> target and agent slot. It moves the issue to
# in-progress, which takes agent-ready off, makes a detached worktree of main at a path fixed for the issue,
# $XDG_CACHE_HOME/agent-desk/parents/<owner>__<repo>/<n>, runs the close-parent skill there, and removes the worktree
# when the run ends. The skill checks the issue's Done When against its closed sub-issues, their reviews, and main,
# then closes the issue or moves it to needs-design with a question. The run takes no serial slot and makes no branch,
# commit, or review. A run that ends with the issue in neither state moves it back to open with a note; when the run
# fails or times out, the dispatcher's exit handler says so on the issue. A run whose agent ended its turn while a
# task it started still ran stopped early: unless the agent asked for a decision, it says so on the issue, naming the
# task, and it exits with desk_stopped.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
close_parent_main() {
    issue=${1:?usage: close-parent.sh <issue>}
    if [[ ! $issue =~ ^[0-9]+$ ]]; then
        echo "usage: close-parent.sh <issue>" >&2
        exit 2
    fi
    source "$(dirname "$0")/common.sh"
    dest=$(desk_scratch_path parents "$issue")
    session=$(uuidgen)
    run_link=$(desk_run_link)

    # Someone who took over the last check may still work in its worktree.
    if [[ -d $dest ]] && desk_in_use "$dest"; then
        desk_leave_state "$issue" queued
        desk_note issue "$issue" "The parent check didn't start: someone works in \`$dest\`$run_link. Label the \
issue agent-ready to check it again."
        exit 0
    fi

    # The check is the agent's from here, so a pick for Manual has done its work.
    issue_labels=$(desk_store issue view "$issue" --json labels | jq -r '[.labels[].name] | join(",")')
    if ! desk_runner_flags agent "$issue_labels"; then
        desk_leave_state "$issue" queued
        desk_note issue "$issue" "The parent check didn't start: $desk_runner_error$run_link."
        return 1
    fi
    desk_store issue edit "$issue" --status in-progress >/dev/null
    python3 -B "$desk_lib/hold.py" spend "$issue" || true
    # A check builds nothing, so it waits for a discussion's memory, whatever the issue's labels.
    desk_memory_start discuss
    # Removes the worktree however the run ends; a stop, as at the time limit, ends the script through its EXIT trap.
    trap close_parent_end EXIT
    trap 'exit 143' TERM

    desk_note issue "$issue" "A parent check started on this issue, to close it once its closed sub-issues meet its \
Done When$run_link. $(desk_takeover_note "$dest" "$session" " while it runs")"
    desk_scratch_add "$dest"

    status=0
    desk_agent_run "$dest" "$issue_labels" new "$(desk_skill_prompt close-parent "$issue")" "$session" || status=$?

    # The agent closes the issue or moves it to needs-design, and has commented already.
    status_now=$(desk_store issue view "$issue" --json status | jq -r .status)
    early=
    if [[ -n $desk_left ]]; then
        early="The parent check stopped early: the agent ended its turn while $desk_left still ran, so the run \
stopped it$run_link."
    fi
    if [[ $status_now != closed && $status_now != needs-design ]]; then
        desk_leave_state "$issue" in-progress
        if [[ -n $early ]]; then
            desk_note issue "$issue" "$early It neither closed this issue nor asked for a decision. Label the issue \
agent-ready to check it again."
        else
            desk_note issue "$issue" "The parent check stopped without closing this issue or asking for a \
decision$run_link. Label the issue agent-ready to check it again."
        fi
    elif [[ -n $early && $status_now == closed ]]; then
        desk_note issue "$issue" "$early It closed this issue first."
    fi
    return "$status"
}

# shellcheck disable=SC2329 # The EXIT trap calls it by name.
close_parent_end() {
    desk_scratch_remove "$dest" || true
    desk_memory_end
}

close_parent_main "$@"; exit
