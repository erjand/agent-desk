#!/usr/bin/env bash
# Creates a worktree for an agent task beside the main checkout, on a new agent/<slug> branch from the main checkout's
# local <base> branch:
#   agent-desk worktree river-flow              creates ../<repo>-river-flow on agent/river-flow, from main
#   agent-desk worktree issue-12 local-desk     cuts agent/issue-12 from local-desk
# The base comes from the second argument, else AGENT_DESK_BASE, else main. A base the main checkout has no local branch
# for exits with status 4. It never fetches, so it works with origin out of reach. A local agent/<slug> branch that
# already exists is checked out instead, so an agent can come back to its review. Copies the paths the config lists
# under worktree.seeds from the main checkout, such as caches and the generated or downloaded folders git ignores, then
# runs worktree.setup in the new worktree.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
worktree_main() {
    slug=${1:?usage: agent-desk worktree <slug> [base]}
    base=${2:-${AGENT_DESK_BASE:-main}}
    if [[ ! $slug =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        echo "The slug takes lowercase letters, digits, and hyphens: $slug" >&2
        exit 2
    fi
    source "$(dirname "$0")/common.sh"
    dest=$(desk_worktree "$slug")

    made_branch=1
    if git -C "$main" show-ref --verify --quiet "refs/heads/agent/$slug"; then
        git -C "$main" worktree add -q "$dest" "agent/$slug"
        made_branch=0
    elif ! git -C "$main" show-ref --verify --quiet "refs/heads/$base"; then
        echo "The main checkout has no local branch $base to cut agent/$slug from." >&2
        exit 4
    else
        git -C "$main" worktree add -q -b "agent/$slug" "$dest" "refs/heads/$base"
    fi
    while read -r seed; do
        if [[ -n $seed && -e $main/$seed ]]; then
            mkdir -p "$(dirname "$dest/$seed")"
            cp -a "$main/$seed" "$dest/$seed"
        fi
    done < <(desk_config worktree.seeds "")

    setup=$(desk_config worktree.setup "")
    # The callers skip setup for a worktree that exists, so a failed setup removes the worktree, and the branch this run
    # made, for the next run to set up again.
    if [[ -n $setup ]] && ! (cd "$dest" && bash -c "$setup"); then
        desk_end_leftovers "$dest"
        git -C "$main" worktree remove --force "$dest"
        if ((made_branch)); then
            git -C "$main" branch -q -D "agent/$slug"
        fi
        echo "The worktree setup failed, so $dest is removed: $setup" >&2
        exit 1
    fi
    echo "Worktree ready: $dest on agent/$slug"
}

worktree_main "$@"; exit
