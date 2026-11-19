#!/usr/bin/env bash
# Sends a headless Claude Code agent back to a review of an agent/<slug> branch other than agent/issue-<n>, such as a
# chore's or an interactive session's, with the user's request:
#   REQUEST="@agent Merge main ..." agent-desk work-pr 537
# The dispatcher runs it when the user mentions @agent on such a review, as the inbox's Fix conflicts button does, and
# passes the comment in REQUEST. The agent works in the review's worktree, ../<repo>-<slug>, which it makes again from
# the local branch when it's gone, and the script posts the outcome on the review. It reads the review and writes its
# notes in the local store, and reads only local branches, so it starts with origin out of reach. A worktree someone
# works in stops the run, as does a review whose branch the main checkout lacks. A chore's review runs with that chore's
# model and memory; any other runs as an agent. A run whose agent ended its turn while a task it started still ran
# stopped early: it says so on the review, naming the task, and exits with desk_stopped.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
work_pr_main() {
    pr=${1:?usage: agent-desk work-pr <pr>}
    source "$(dirname "$0")/common.sh"
    review=$(desk_store review view "$pr" --json headRefName,baseRefName)
    branch=$(jq -r .headRefName <<<"$review")
    base=$(jq -r .baseRefName <<<"$review")
    if [[ $branch =~ ^agent/issue-[0-9]+$ || ! $branch =~ ^agent/([a-z0-9][a-z0-9-]*)$ ]]; then
        echo "Review #$pr is from $branch, not an agent/<slug> branch other than agent/issue-<n>." >&2
        exit 2
    fi
    slug=${BASH_REMATCH[1]}
    dest=$(desk_worktree "$slug")
    session=$(uuidgen)
    run_link=$(desk_run_link)
    if [[ $slug =~ ^chore-(.+)-[0-9]{8}-[0-9]{4}$ ]]; then
        name=${BASH_REMATCH[1]}
        opened="A chore run of the $name skill opened review #$pr"
    else
        name=
        opened="Review #$pr was opened"
    fi

    if [[ -d $dest ]] && desk_in_use "$dest"; then
        desk_note review "$pr" "The agent didn't start: someone works in \`$dest\`$run_link."
        exit 0
    elif [[ ! -d $dest ]] && ! git -C "$main" show-ref --verify --quiet "refs/heads/$branch"; then
        desk_note review "$pr" "The agent didn't start: the main checkout has no branch \`$branch\`$run_link."
        exit 1
    fi
    repo=${GH_REPO:-$(cd "$main" && desk_repo_here || true)}
    # The run waits here, holding its slot, until the machine has the memory its kind of run needs.
    if [[ -n $name ]]; then
        desk_memory_start "chore/$name"
        desk_model_flags chore "$name"
    else
        desk_memory_start agent
        desk_model_flags agent
    fi
    desk_note review "$pr" "An agent started on \`$branch\`$run_link. To take over, run \`claude --resume \
$session\` in \`$dest\`."

    if [[ ! -d $dest ]] && ! (cd "$main" && "$desk_lib/worktree.sh" "$slug" "$base"); then
        setup=$(desk_config worktree.setup "")
        desk_note review "$pr" "The agent didn't start: the worktree setup, \`$setup\`, failed$run_link."
        exit 1
    fi

    # The store records the agent as the author of the comments it posts, so they need no footer.
    prompt="You run unattended, so nobody can answer a question: where you would ask, stop and say why in a comment on
the review instead, with \`agent-desk review comment $pr --body <text>\`. $opened from this directory, $dest, on its
branch $branch. The user asked for more on it, in this comment on the review:

<request>
${REQUEST:-}
</request>

Read the review and its comments with \`agent-desk review view $pr\`, and its diff with
\`git diff $base...HEAD\`, then do what the request asks on this branch, keeping the review to its own change,
and commit. The branch lives in the repository this worktree shares with the main checkout, so each commit reaches the
review without a push."

    status=0
    stream=$(mktemp)
    # The run's stream-json goes to a file too, for its length and turns in the run log.
    desk_claude_run "$dest" "$stream" "$prompt" --session-id "$session" || status=$?
    left=$(desk_left_task "$stream" "$status")

    python3 -B "$desk_lib/run_log.py" record "$desk_model" "$desk_effort" "$stream" "$pr" --start feedback || true
    rm -f "$stream"
    if [[ -n $left ]]; then
        outcome="The agent stopped early on \`$branch\`: it ended its turn while $left still ran, so the run stopped \
it$run_link. Any commits it made are on the branch."
    elif ((status == 0)); then
        outcome="The agent finished on \`$branch\`$run_link."
    else
        outcome="The agent stopped with an error on \`$branch\`$run_link."
    fi
    desk_note review "$pr" "$outcome"
    return "$status"
}

work_pr_main "$@"; exit
