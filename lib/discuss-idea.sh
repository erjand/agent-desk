#!/usr/bin/env bash
# Discusses an idea with a headless Claude Code agent, which answers on the issue and changes no code:
#   agent-desk discuss-idea 12
# The dispatcher runs it when a comment sent to the agent, from the web page's Send to Agent or an @agent mention,
# lands on an idea: an open issue labelled idea in no agent state. It makes a detached worktree of main at a path fixed
# for the issue, $XDG_CACHE_HOME/agent-desk/ideas/<owner>__<repo>/<n>, so the agent reads the code without touching the
# main checkout, runs the discuss-idea skill there, and removes the worktree when the run ends. Claude Code keeps a
# session by its folder, so a later discussion of the issue takes up the session the last one ran, whose id the last
# start note names. The run makes no branch, commit, or review, and leaves the issue's state and labels as they are.
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
    desk_model_flags agent "$issue_labels"
    # Removes the worktree however the run ends; a stop, as at the time limit, ends the script through its EXIT trap.
    trap discuss_end EXIT
    trap 'exit 143' TERM

    # The last discussion's session, when this machine still has it.
    resumed=
    last_session=$(desk_store issue view "$issue" --json comments | jq -r '[.comments[].body
        | select(startswith("A discussion run started")) | capture("claude --resume (?<id>[0-9a-f-]+)").id] | last
        | values')
    if [[ -n $last_session ]] \
        && find "$HOME/.claude/projects" -maxdepth 2 -name "$last_session.jsonl" 2>/dev/null | grep -q .; then
        resumed=$last_session
        session=$last_session
    fi
    taking=
    [[ -n $resumed ]] && taking=", taking up the session of the last discussion"
    desk_note issue "$issue" "A discussion run started on this idea$taking$run_link. To take over, run \
\`claude --resume $session\` in \`$dest\` while it runs."

    desk_scratch_add "$dest"

    prompt=("/agent-desk:discuss-idea $issue" --session-id "$session")
    if [[ -n $resumed ]]; then
        prompt=("/agent-desk:discuss-idea $issue" --resume "$resumed")
    fi
    status=0
    desk_agent_run "$dest" "$issue_labels" "$([[ -n $resumed ]] && echo feedback || echo new)" "${prompt[@]}" ||
        status=$?
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
