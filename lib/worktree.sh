#!/usr/bin/env bash
# Creates a worktree for an agent task beside the main checkout, on a new agent/<slug> branch from the main checkout's
# local <base> branch:
#   agent-desk worktree river-flow              creates ../<repo>-river-flow on agent/river-flow, from main
#   agent-desk worktree issue-12 local-desk     cuts agent/issue-12 from local-desk
# The base comes from the second argument, else AGENT_DESK_BASE, else main. A base the main checkout has no local branch
# for exits with status 4. It never fetches, so it works with origin out of reach. A local agent/<slug> branch that
# already exists is checked out instead, so an agent can come back to its review. Copies the paths the config lists
# under worktree.seeds from the main checkout with seed.sh, such as caches and the generated or downloaded folders git
# ignores, then runs worktree.setup in the new worktree. A failed setup runs worktree.teardown before it removes the
# worktree. When AGENT_DESK_SETUP_FAILURE names a file, a failed seed copy or setup writes there, in Markdown, the
# clause that names it, such as "the worktree setup, `just build`, failed", for the caller's comment.
#   agent-desk worktree --teardown chore-schedule
# runs worktree.teardown in ../<repo>-chore-schedule, for a caller about to remove that worktree itself, and exits 0
# even when the teardown fails or runs past its limit.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
worktree_main() {
    local teardown=0
    if [[ ${1:-} == --teardown ]]; then
        teardown=1
        shift
    fi
    slug=${1:?usage: agent-desk worktree [--teardown] <slug> [base]}
    base=${2:-${AGENT_DESK_BASE:-main}}
    if [[ ! $slug =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        echo "The slug takes lowercase letters, digits, and hyphens: $slug" >&2
        exit 2
    fi
    source "$(dirname "$0")/common.sh"
    dest=$(desk_worktree "$slug")
    if ((teardown)); then
        desk_teardown "$dest"
        return 0
    fi

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
    # The callers skip setup for a worktree that exists, so a failed seed copy or setup removes the worktree, and the
    # branch this run made, for the next run to seed and set up again.
    remove_worktree() {
        git -C "$main" worktree remove --force "$dest"
        if ((made_branch)); then
            git -C "$main" branch -q -D "agent/$slug"
        fi
        echo "$1, so $dest is removed." >&2
        if [[ -n ${AGENT_DESK_SETUP_FAILURE:-} ]]; then
            printf '%s\n' "$2" >"$AGENT_DESK_SETUP_FAILURE"
        fi
        exit 1
    }

    local seed copied copy_status
    while read -r seed; do
        [[ -n $seed && -e $main/$seed ]] || continue
        copy_status=0
        copied=$("$(dirname "$0")/seed.sh" "$main/$seed" "$dest/$seed" 2>&1) || copy_status=$?
        if [[ -n $copied ]]; then
            echo "$copied" >&2
        fi
        if ((copy_status)); then
            remove_worktree "The copy of the seed $seed failed" \
                "the copy of the seed \`$seed\` from \`$main/$seed\` failed: \`$(tail -n 1 <<<"$copied")\`"
        fi
    done < <(desk_config worktree.seeds "")

    setup=$(desk_config worktree.setup "")
    if [[ -n $setup ]] && ! (cd "$dest" && bash -c "$setup"); then
        desk_end_leftovers "$dest"
        desk_teardown "$dest" >&2
        remove_worktree "The worktree setup failed: $setup" "the worktree setup, \`$setup\`, failed"
    fi
    echo "Worktree ready: $dest on agent/$slug"
}

worktree_main "$@"; exit
