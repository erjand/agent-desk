#!/usr/bin/env bash
# Removes the agent worktrees and local agent/* branches that no run, review, or person still needs:
#   agent-desk sweep
# The dispatcher runs it in a unit of its own for each project at most once an hour, as lib/dispatch_sweep.py
# describes. It catches what the end of a run or a review's cleanup left behind, such as a worktree a process briefly
# worked in, and a branch whose worktree someone removed by hand.
#
# It looks at each worktree beside the main checkout that an agent run made: ../<repo>-issue-<n> on agent/issue-<n>,
# and ../<repo>-chore-<skill>-<time> on agent/chore-<skill>-<time>. It removes one through desk_remove_worktree, with
# that function's guards, once no run of its issue or chore, and no fix or review run of its branch's reviews, is
# running or queued, its branch has no open review, and its issue is in none of in-progress, waiting, and on-hold. A
# worktree made by hand, such as one from `agent-desk worktree <slug>`, stays until the user removes it. It then
# deletes each local agent/* branch with no worktree and no open review whose commits origin or the local main already
# holds, under the same guards for an issue's or a chore's branch. It prints one line for each worktree and branch it
# removes or keeps, with the reason for each one it keeps. It fetches nothing.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
sweep_main() {
    source "$(dirname "$0")/common.sh"
    local runs path branch slug kept base
    # The project's runs that wait or work, one "<kind> <target>" per line.
    if ! runs=$(python3 -B "$desk_lib/dispatch.py" runs --repo "$GH_REPO" |
        jq -r '.[] | select(.status == "requested" or .status == "running") | "\(.kind) \(.target)"'); then
        echo "Couldn't read the project's runs, so the sweep removed nothing." >&2
        exit 1
    fi

    # Why a branch's issue or chore still needs it, or nothing when none does, in kept, and the base its newest review
    # targets, or main, in base: busy <branch>.
    busy() {
        local branch=$1 slug=${1#agent/} found review status
        kept=
        found=$(desk_store review list --head "$branch" --state all --limit 100 --json number,state,baseRefName)
        base=$(jq -r '.[0].baseRefName // "main"' <<<"$found")
        review=$(jq -r '[.[] | select(.state == "OPEN")][0].number // empty' <<<"$found")
        if [[ -n $review ]]; then
            kept="its review #$review is open"
            return 0
        fi
        for review in $(jq -r '.[].number' <<<"$found"); do
            if grep -qxF -e "agent pr-$review" -e "fix $review" <<<"$runs"; then
                kept="a run on its review #$review is running or queued"
                return 0
            fi
        done
        if [[ $slug =~ ^issue-([0-9]+)$ ]]; then
            if grep -qxF "agent ${BASH_REMATCH[1]}" <<<"$runs"; then
                kept="a run of #${BASH_REMATCH[1]} is running or queued"
                return 0
            fi
            status=$(desk_store issue view "${BASH_REMATCH[1]}" --json status 2>/dev/null | jq -r .status) || true
            # A paused issue's next run takes up its session in the same worktree.
            if [[ $status == in-progress || $status == waiting || $status == on-hold ]]; then
                kept="#${BASH_REMATCH[1]} is $status"
            fi
        elif [[ $slug =~ ^chore-(.+)-[0-9]{8}-[0-9]{4}$ ]]; then
            if grep -qxF "chore ${BASH_REMATCH[1]}" <<<"$runs"; then
                kept="a run of the ${BASH_REMATCH[1]} chore is running or queued"
            fi
        fi
        return 0
    }

    # A worktree whose directory someone deleted no longer holds its branch once git forgets it.
    git -C "$main" worktree prune
    local -A swept=()
    while IFS=$'\t' read -r path branch; do
        [[ $path == "$main" ]] && continue
        slug=${branch#agent/}
        if [[ $branch != agent/* || $path != "$(desk_worktree "$slug")" ||
            ! $slug =~ ^(issue-[0-9]+|chore-.+-[0-9]{8}-[0-9]{4})$ ]]; then
            echo "Kept $path: it isn't an issue's or a chore's worktree."
            continue
        fi
        swept[$branch]=1
        busy "$branch"
        if [[ -n $kept ]]; then
            echo "Kept $path: $kept."
            continue
        fi
        desk_remove_worktree "$path" "$branch" "$base" || true
    done < <(git -C "$main" worktree list --porcelain | awk '
        /^worktree / { if (path != "") print path "\t" branch; path = substr($0, 10); branch = "" }
        /^branch refs\/heads\// { branch = substr($0, 19) }
        END { if (path != "") print path "\t" branch }')

    # The branches without a worktree, apart from those of the worktrees above, whose removal said what it did with
    # the branch.
    while read -r branch; do
        [[ -n ${swept[$branch]:-} ]] && continue
        busy "$branch"
        if [[ -n $kept ]]; then
            echo "Kept local $branch: $kept."
            continue
        fi
        desk_remove_worktree "" "$branch" "$base" || true
    done < <(git -C "$main" for-each-ref --format='%(refname:short) %(worktreepath)' 'refs/heads/agent/' |
        awk 'NF == 1 { print $1 }')
}

sweep_main "$@"; exit
