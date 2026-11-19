#!/usr/bin/env bash
# Cleans up after an agent review merges or closes:
#   agent-desk clean agent/issue-12 true
# The second argument says whether the review merged. A merged branch loses its worktree and its branch, locally and,
# when origin can be reached, on origin. The merge queue (lib/merges.py) moved main and ran the sync when it merged the
# review, and the push queue (lib/pushes.py) publishes a release once origin holds it, so the cleanup leaves main
# alone. An unmerged branch keeps its branch on origin and loses only its worktree and local branch. A branch never
# pushed, whose commits neither origin nor the review's base holds, keeps its local branch, and its worktree when it
# still has one, since they hold the only copy of its work; the user deletes them. Either way, the issue's agent runs
# still running or queued are stopped, since the closed review leaves them nothing to do, and the issue moves from
# in-progress back to open, unless the review merged into another agent branch, as a stacked review does. A merge into
# main closes the issue with a note, and closes the issues of the reviews stacked on it. An issue whose review merged
# into another base, as a base:<branch> label asks, stays open for that base's own merge into main to close.
# It reads and writes the issue and its reviews in the local store. The dispatcher runs it when a review's merged or
# closed event reaches the store. It skips any step that could touch someone's work: a worktree with uncommitted
# changes, commits that neither origin, as the last push or fetch saw it, nor the review's local base holds, or a
# process inside it. It fetches nothing, so only the deletion on origin
# needs origin in reach.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
clean_main() {
    branch=${1:?usage: agent-desk clean <agent/slug> <merged: true|false>}
    merged=${2:?usage: agent-desk clean <agent/slug> <merged: true|false>}
    if [[ $branch != agent/* ]]; then
        echo "Only agent/* branches are cleaned: $branch" >&2
        exit 2
    fi
    source "$(dirname "$0")/common.sh"
    slug=${branch#agent/}
    dest=$(desk_worktree "$slug")

    # Stops the issue's agent runs that haven't finished: the units the dispatcher started. A running one first gets a
    # stopped note naming its unit, so the release its exit handler runs finds the outcome reported and stays quiet.
    # Waits for the agent to leave its worktree, so the worktree can go.
    stop_agent_runs() {
        local issue=$1 reviews unit waited=0 running=false
        reviews=$(desk_store review list --head "$branch" --state all --json number |
            jq -r '[.[].number | "review #\(.)"] | join(", ")')
        # The note goes up before the stop, so the exit handler's release reads it. A desk that never ran the
        # dispatcher has no runs to list.
        while read -r unit; do
            [[ -n $unit ]] || continue
            desk_note issue "$issue" "The agent stopped: ${reviews:-its review} closed while it worked \
(log: \`journalctl --user -u $unit\`)." || true
            echo "Stopping agent run $unit."
            running=true
        done < <(python3 -B "$desk_lib/dispatch.py" stop agent "$issue" --dry-run 2>/dev/null || true)
        python3 -B "$desk_lib/dispatch.py" stop agent "$issue" >/dev/null 2>&1 || true
        while [[ $running == true && -d $dest ]] && desk_in_use "$dest" && ((waited < 120)); do
            sleep 5
            waited=$((waited + 5))
        done
    }

    # Deletes the branch on origin if origin still has it. ls-remote exits 2 when origin lacks the branch, so any other
    # failure means origin couldn't be reached, and the caller tries again.
    # shellcheck disable=SC2329 # desk_retry calls it by name.
    delete_on_origin() {
        local found=0
        git -C "$main" ls-remote --exit-code --heads origin "$branch" >/dev/null || found=$?
        case $found in
            0) git -C "$main" push -q origin --delete "$branch" && echo "Deleted $branch on origin." ;;
            2) ;;
            *) return 1 ;;
        esac
    }

    # Whether an issue in the store is closed, or missing from it: issue_closed <n>.
    issue_closed() {
        local status
        status=$(desk_store issue view "$1" --json status 2>/dev/null | jq -r .status) || true
        [[ ${status:-closed} == closed ]]
    }

    # Closes the open issues whose reviews merged into a branch, as stacked reviews do, and into theirs in turn. The
    # store closes an issue a review fixes only for a merge into main, so their work lands on main with this review
    # and leaves them open.
    close_stacked() {
        local into=$1 landed=$2 review head issue
        while read -r review head; do
            [[ -n $review && $head != "$into" ]] || continue
            if [[ $head =~ ^agent/issue-([0-9]+)$ ]]; then
                issue=${BASH_REMATCH[1]}
                if ! issue_closed "$issue"; then
                    desk_note issue "$issue" "Landed on main with $landed, which holds #$review."
                    desk_store issue close "$issue" >/dev/null
                    echo "Closed #$issue: its review #$review landed on main with $landed."
                fi
            fi
            close_stacked "$head" "$landed"
        done < <(desk_store review list --state merged --base "$into" --limit 50 --json number,headRefName |
            jq -r '.[] | "\(.number) \(.headRefName)"')
    }

    # The review the cleanup is for, the branch's newest merged one after a merge, and the branch a merged review went
    # into: main, the branch an issue's base:<branch> label names, or another agent branch for a stacked review.
    base=
    state=all
    [[ $merged == true ]] && state=merged
    found=$(desk_store review list --head "$branch" --state "$state" --limit 1 --json number,baseRefName)
    review=$(jq -r '.[0].number // empty' <<<"$found")
    # The branch the review targets, merged or not, whose local copy may hold the branch's commits without origin.
    target=$(jq -r '.[0].baseRefName // empty' <<<"$found")
    if [[ $merged == true ]]; then
        base=$(jq -r '.[0].baseRefName // "main"' <<<"$found")
    fi
    if [[ $slug =~ ^issue-([0-9]+)$ ]]; then
        stop_agent_runs "${BASH_REMATCH[1]}"
    fi
    # A merged branch is safe to delete, and an unmerged one stays on origin, so its local copy can go too. One whose
    # commits neither origin nor the review's base holds stays.
    desk_remove_worktree "$dest" "$branch" "$target" || true

    # A merge into main closes the issue, and any other close or merge returns it to open, so none leaves it claimed.
    # A merge into another agent branch keeps it in progress, so the inbox shows it waiting on that branch's review,
    # whose merge into main closes it.
    landed=${review:+#$review}
    landed=${landed:-$branch}
    if [[ $slug =~ ^issue-([0-9]+)$ ]]; then
        issue=${BASH_REMATCH[1]}
        if [[ $base == agent/* ]]; then
            echo "Kept #$issue in progress: its review merged into $base."
        elif [[ $base == main ]]; then
            # The store closed the issue already when the review fixes it, and the note still says where it landed.
            desk_note issue "$issue" "Landed on main with $landed." || true
            if ! issue_closed "$issue"; then
                desk_store issue close "$issue" >/dev/null
            fi
            echo "Closed #$issue: it landed on main with $landed."
        else
            desk_leave_state "$issue" in-progress
            echo "Moved #$issue from in-progress back to open."
        fi
        # The issue gave up the serial slot, if it held it, so the next waiting serial issue starts from this main.
        "$desk_lib/serial.sh" release
    fi

    if [[ $merged == true && $base == main ]]; then
        close_stacked "$branch" "$landed"
    fi

    if [[ $merged == true ]]; then
        # Origin out of reach keeps its copy of the branch, and the cleanup goes on.
        desk_retry delete_on_origin || echo "Couldn't delete $branch on origin."
    fi
}

clean_main "$@"; exit
