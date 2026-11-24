#!/usr/bin/env bash
# Runs one maintenance skill with a headless agent in a worktree of its own:
#   agent-desk run-chore refactor
# The dispatcher runs it for a `chore <skill>` request, which agent-desk chore makes, as does the project's chores
# workflow when agent-desk chores tick finds the chore due. The agent works in ../<repo>-chore-<skill>-<time> on
# agent/chore-<skill>-<time> and opens its review in the local store. When the agent opens no review, the worktree
# and its local branch go, and so does origin's copy when origin's main holds it. A run whose agent ended its turn while a task it started still ran stopped early: it says
# so, on its review when it opened one, asks for no queued next run, and exits with desk_stopped.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
run_chore_main() {
    skill=${1:?usage: agent-desk run-chore <skill>}
    source "$(dirname "$0")/common.sh"
    slug="chore-$skill-$(date +%Y%m%d-%H%M)"
    dest=$(desk_worktree "$slug")
    session=$(uuidgen)

    repo=${GH_REPO:-$(cd "$main" && desk_repo_here || true)}
    # The run waits here, holding its slot, until the machine has the memory this chore needs.
    desk_memory_start "chore/$skill"
    desk_runner_flags chore "$skill" || return 1
    (cd "$main" && "$desk_lib/worktree.sh" "$slug")
    commit=$(git -C "$main" rev-parse HEAD)
    echo "Running /$skill in $dest. $(desk_takeover_note "$dest" "$session")"

    # Chore skills such as refactor create their own worktree and labels and open a PR on the host by default; this run
    # already has a worktree, AGENTS.md asks before new labels, and agent-desk keeps its reviews in the local store.
    instruction="/$skill"
    if [[ $desk_runner != claude ]]; then
        skill_file=$(python3 -B "$desk_lib/chore_skills.py" --file "$main" "$skill" | jq -r '.path // empty')
        if [[ -n $skill_file ]]; then
            skill_file=${skill_file/#"$main/"/"$dest/"}
            instruction="Read and follow the $skill skill in $skill_file."
        else
            instruction="Find the $skill skill in the available Codex skills and follow its instructions. If it is missing, stop and report that."
        fi
        instruction="$instruction Use the installed agent-desk command for the project's store and keep the main checkout untouched."
    fi
    prompt="$instruction

You run unattended as a chore, so nobody can answer a question: where the skill would ask, stop and say why instead.
Work in this directory, $dest, on its branch agent/$slug, instead of creating another worktree. Where the skill would
open a PR, commit to agent/$slug and open a review in agent-desk instead, with
\`agent-desk review create --branch agent/$slug --title <title> --body-file <file>\`. The branch lives in the
repository the main checkout shares, so each commit reaches the review without a push. Add only labels that already
exist."

    # What earlier runs of this chore did: their open, merged, and rejected reviews, the files they passed over, and the
    # leads they left. A run starts from main and would otherwise often pick the same change again. lib/chore_ledger.py
    # keeps the brief to about 1,500 tokens however long the chore has run.
    prompt="$prompt

$(python3 -B "$desk_lib/chore_ledger.py" brief "$repo" "$skill" "$main")"

    status=0
    stream=$(mktemp)
    # The run's stream-json goes to a file too, for the report the agent ends with.
    desk_run "$dest" "$stream" "$prompt" "$session" || status=$?
    left=$(desk_left_task "$stream" "$status")

    pr_number=$(desk_open_review "agent/$slug") || true
    python3 -B "$desk_lib/chore_ledger.py" record "$repo" "$skill" "$commit" "$stream" "${pr_number:-}" || true
    python3 -B "$desk_lib/run_log.py" record "$desk_model" "$desk_effort" "$stream" "${pr_number:-}" || true
    rm -f "$stream"
    if [[ -n $left ]]; then
        early="The agent stopped early: it ended its turn while $left still ran, so the run stopped it"
        echo "$early."
        if [[ -n ${pr_number:-} ]]; then
            desk_note review "$pr_number" "$early$(desk_run_link)."
        fi
    fi
    if [[ -n ${pr_number:-} ]]; then
        echo "The chore opened review #$pr_number."
    else
        echo "The chore opened no review."
        desk_end_leftovers "$dest"
        "$desk_lib/clean.sh" "agent/$slug" false
        desk_drop_origin_branch "agent/$slug"
    fi
    # A chore queued to run again asks for its next run when this one ends, unless this one failed or stopped early.
    # The dispatcher starts it once this run's unit lets go of the chore. desk_store writes the request as
    # agent-desk's, since the dispatcher ignores one an agent wrote.
    if [[ $(python3 -B "$desk_lib/chore_repeat.py" next "$repo" "$skill" "$status") == again ]]; then
        desk_store request chore "$skill" >/dev/null && echo "Asked for the next queued run of $skill."
    fi
    return "$status"
}

run_chore_main "$@"; exit
