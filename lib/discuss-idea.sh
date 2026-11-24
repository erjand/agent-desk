#!/usr/bin/env bash
# Discusses an idea with a headless agent, which answers on the issue and changes no code:
#   agent-desk discuss-idea 12
# The dispatcher runs it when a comment sent to the agent, from the web page's Send to Agent or an @agent mention,
# lands on an idea: an open issue labelled idea in no agent state. It makes a detached worktree of main at a path fixed
# for the issue, $XDG_CACHE_HOME/agent-desk/ideas/<owner>__<repo>/<n>, so the agent reads the code without touching the
# main checkout, runs the discuss-idea skill there, and removes the worktree when the run ends. A later discussion
# takes up the last session when it uses the same runner. The run makes no branch, commit, or review, and leaves the
# issue's state and labels as they are.
# When the run fails or times out, the dispatcher's exit handler says so on the issue. A run whose agent ended its turn
# while a task it started still ran stopped early: it says so on the issue, naming the task, and exits with
# desk_stopped.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
discuss_idea_main() {
    issue=${1:?usage: agent-desk discuss-idea <issue>}
    if [[ ! $issue =~ ^[0-9]+$ ]]; then
        echo "usage: agent-desk discuss-idea <issue>" >&2
        exit 2
    fi
    source "$(dirname "$0")/common.sh"
    dest=$(desk_scratch_path ideas "$issue")
    session=$(uuidgen)
    run_link=$(desk_run_link)

    # Someone who took over the last discussion may still work in its worktree.
    if [[ -d $dest ]] && desk_in_use "$dest"; then
        desk_note issue "$issue" "The discussion run didn't start: someone works in \`$dest\`$run_link."
        exit 0
    fi

    issue_labels=$(desk_store issue view "$issue" --json labels | jq -r '[.labels[].name] | join(",")')
    # The run waits here until the machine has the memory a discussion needs, which it learns apart from agent runs'.
    # A discussion builds nothing, so the labels that raise a build's need leave it alone.
    desk_memory_start discuss
    desk_runner_flags agent "$issue_labels" || return 1
    # The last start note identifies its runner, even if the CLI never started a session.
    resumed=
    marker=
    last_note=$(desk_store issue view "$issue" --json comments | jq -r '[.comments[].body
        | select(startswith("A discussion run started"))] | last // empty')
    if [[ $desk_runner != claude ]]; then
        marker="${XDG_STATE_HOME:-$HOME/.local/state}/agent-desk/discussions/${GH_REPO//\//__}/$issue"
        if [[ $last_note == *"$(desk_takeover_note "$dest" "$session")"* ]]; then
            resumed=$(desk_codex_thread "$marker") || resumed=
        fi
        if [[ -z $resumed ]]; then
            rm -f -- "$marker"
        fi
    elif [[ $last_note =~ claude\ --resume\ ([0-9a-f-]+) ]]; then
        last_session=${BASH_REMATCH[1]}
        if desk_has_claude_session "$last_session"; then
            resumed=$last_session
        fi
    fi
    taking=
    start=new
    if [[ -n $resumed ]]; then
        taking=", taking up the session of the last discussion"
        start=resumed
    fi
    desk_note issue "$issue" "A discussion run started on this idea$taking$run_link. \
$(desk_takeover_note "$dest" "${resumed:-$session}" " while it runs")"

    # Removes the scratch worktree on exit, including at the time limit.
    trap discuss_end EXIT
    trap 'exit 143' TERM
    desk_scratch_add "$dest"

    status=0
    desk_agent_run "$dest" "$issue_labels" "$start" "$(desk_skill_prompt discuss-idea "$issue")" \
        "$session" "$resumed" "$marker" || status=$?
    if [[ -n $desk_left ]]; then
        desk_note issue "$issue" "The discussion run stopped early: the agent ended its turn while $desk_left still \
ran, so the run stopped it$run_link. The next discussion takes up the same session."
    fi
    return "$status"
}

# shellcheck disable=SC2329 # The EXIT trap calls it by name.
discuss_end() {
    desk_scratch_remove "$dest" || true
    desk_memory_end
}

discuss_idea_main "$@"; exit
