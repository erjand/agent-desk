#!/usr/bin/env bash
# Merges a review's base, main or the branch it targets, into a review of an agent/<slug> branch that conflicts with
# it, such as an issue's, a chore's, or an interactive session's, and has a headless agent resolve the
# conflicts:
#   agent-desk fix 537
# The dispatcher runs it for a `fix <n>` request, which the project's fix workflow makes when agent-desk fixes tick
# finds the review due. It reads the review from the local store and posts its notes there, and a trial merge of the two
# local branches in git, which touches no worktree, tells whether they conflict. It reads and moves only local
# branches, so it runs with origin out of reach, and the push queue (lib/pushes.py) publishes the branch later. A merge
# with no conflicts left, such as when the base moved since the trial, commits without an agent. The agent works in the
# review's own worktree, which it makes again from the branch when it's gone, and leaves it as it found it when it can't
# resolve them: the merge aborted, and a comment on the review saying why. A review that merges cleanly but fails checks
# on its head, as when the merge queue found that the base's changes broke them, gets an agent that learns the failed
# checks' errors from lib/failures.py and adapts the branch to the base instead, told whether the checks fail since the
# queue merged the base in or since an earlier fix resolved conflicts with it; the run counts only when the agent
# commits. A run whose agent ended its turn while a task it started still ran stopped early, whatever the branch holds:
# it says so on the review, naming the task, and exits with desk_stopped.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
fix_main() {
    pr=${1:?usage: agent-desk fix <pr>}
    source "$(dirname "$0")/common.sh"
    review=$(desk_store review view "$pr" --json headRefName,state,baseRefName)
    branch=$(jq -r .headRefName <<<"$review")
    state=$(jq -r .state <<<"$review")
    base=$(jq -r .baseRefName <<<"$review")
    if [[ ! $branch =~ ^agent/([a-z0-9][a-z0-9-]*)$ ]]; then
        echo "Review #$pr is from $branch, not an agent/<slug> branch." >&2
        exit 2
    fi
    slug=${BASH_REMATCH[1]}
    issue=
    [[ $slug =~ ^issue-([0-9]+)$ ]] && issue=${BASH_REMATCH[1]}
    [[ $state == OPEN ]] || { echo "Review #$pr is ${state,,}."; exit 0; }
    repo=${GH_REPO:-$(cd "$main" && desk_repo_here || true)}
    # A trial merge in the main checkout writes only objects, so it tells whether the branch conflicts with its base
    # without touching a worktree. git merge-tree exits 1 for conflicts and above 1 when it can't merge at all. It runs
    # the full merge, since git 2.56 exits 0 with --quiet on some merges that conflict.
    conflicts=0
    git -C "$main" merge-tree --write-tree --no-messages "refs/heads/$base" "refs/heads/$branch" >/dev/null ||
        conflicts=$?
    if ((conflicts > 1)); then
        echo "git couldn't try merging $base into $branch." >&2
        exit 1
    fi
    # A review without conflicts needs a fix only for local checks that fail on its head; their errors go to the
    # agent. lib/failures.py reads them from the store and finds the head in the main checkout.
    checks=
    if ((!conflicts)); then
        checks=$(cd "$main" && python3 -B "$desk_lib/failures.py" excerpt "$pr")
        if [[ -z $checks ]]; then
            echo "Review #$pr no longer conflicts with $base, and no check fails on its head."
            exit 0
        fi
    fi
    dest=$(desk_worktree "$slug")
    session=$(uuidgen)
    run_link=$(desk_run_link)

    # An issue's runs take its lock, and a fix never waits on a run that may last hours: the next tick tries again.
    if [[ -n $issue ]]; then
        exec 8>"$main/.git/agent-issue-$issue.lock"
        flock -n 8 || { echo "Another run works issue #$issue, so the fix stops."; exit 0; }
    fi
    if [[ -d $dest ]]; then
        if desk_in_use "$dest"; then
            echo "Someone works in $dest, so the fix stops."
            exit 0
        elif [[ -n $(git -C "$dest" status --porcelain) ]]; then
            echo "$dest has uncommitted changes, so the fix stops."
            exit 0
        fi
    fi

    # The run waits here, holding its slot, until the machine has the memory a fix needs.
    desk_memory_start fix
    desk_runner_flags fix || return 1
    if [[ -d $dest ]]; then
        git -C "$dest" checkout -q "$branch"
    else
        (cd "$main" && "$desk_lib/worktree.sh" "$slug" "$base") 8>&-
    fi

    # The fix counts only when the branch holds the base it merged, which may have moved on since, and a fix of failed
    # checks only when the agent committed. The queue merged the base into that branch already, and merges it again
    # when the base moves.
    merged=$(git -C "$dest" rev-parse "refs/heads/$base")
    before=$(git -C "$dest" rev-parse HEAD)
    if [[ -n $checks ]]; then
        merged=$before
        work="adapting \`$branch\` to $base, whose changes broke its checks"
        # The merge queue notes whether the checks broke when it merged the base in, or when a fix resolved conflicts.
        cause=$(GH_REPO=$repo python3 -B "$desk_lib/merges.py" cause "$main" "$pr" 2>/dev/null || true)
        if [[ $cause == conflict ]]; then
            broke="conflicted with its base, $base, and an earlier fix resolved the conflicts by merging $base into it.
The conflict is resolved, and the branch's checks, which passed before, fail since, so a change on $base most likely
broke it"
        else
            broke="passed its checks until its base, $base, merged into it, and fails them since, so a change on $base
most likely broke it"
        fi
        prompt="You run unattended, so nobody can answer a question. Review #$pr, of the branch $branch checked out in
this directory, $broke. These checks failed on its head:

$checks

Adapt the review's change to $base so the checks pass. Read the review with \`agent-desk review view $pr\`, and the
commits on $base that changed the code the errors name, to learn what $base changed and why, and keep $base's change.
Change only what the failures need. Run the project's tests as its AGENTS.md or CLAUDE.md says, then commit. The
branch lives in the repository this worktree shares with the main checkout, so the commit reaches the review without a
push. Never rebase or force-push.

When the failures need a decision neither the review nor $base settles, or you can't make the tests pass, commit
nothing, and comment on the review which checks still fail and why, with
\`agent-desk review comment $pr --body <text>\`."
    elif git -C "$dest" merge -q --no-edit "refs/heads/$base"; then
        if [[ $(git -C "$dest" rev-parse HEAD) == "$before" ]]; then
            echo "$branch already holds $base."
            exit 0
        fi
        desk_note review "$pr" "Merged $base into \`$branch\` with no conflicts left to resolve$run_link."
        exit 0
    fi
    if [[ -z $checks ]]; then
        # A merge git refused to start, such as over an untracked file it would overwrite, leaves nothing to resolve.
        if ! git -C "$dest" rev-parse -q --verify MERGE_HEAD >/dev/null; then
            desk_note review "$pr" "The fix stopped: git couldn't start merging $base into \`$branch\` in \
\`$dest\`.$run_link"
            exit 1
        fi
        conflicted=$(git -C "$dest" diff --name-only --diff-filter=U | sed 's/^/- /')
        work="resolving the conflicts with $base on \`$branch\`"
        prompt="You run unattended, so nobody can answer a question. Review #$pr, of the branch $branch checked out in
this directory, conflicts with its base, $base. A merge of $base into it is in progress here, with conflicts in:

$conflicted

Resolve them so the branch keeps both its review's change and what $base changed. Read the review with
\`agent-desk review view $pr\`, and the commits on $base that touch each conflicted file, to learn what each side
meant. Change only what the conflicts need. When your resolution changed code, run the tests that cover it, as the
project's AGENTS.md or CLAUDE.md says; when it changed only docs, run none. The desk's branch check runs the full suite
on your commit, so leave the rest to it. Then finish the merge with git commit --no-edit. The branch lives in the
repository this worktree shares with the main checkout, so the commit reaches the review without a push. Never rebase
or force-push.

When a conflict needs a decision neither side settles, or the tests fail in a way the merge caused and you can't fix,
run git merge --abort and comment on the review which files and why, with
\`agent-desk review comment $pr --body <text>\`."
    fi
    desk_note review "$pr" "An agent started $work$run_link. $(desk_takeover_note "$dest" "$session")"
    # The store records the agent as the author of the comments it posts, so they need no footer.

    status=0
    stream=$(mktemp)
    # The run's stream-json goes to a file too, for its length and turns in the run log.
    desk_run "$dest" "$stream" "$prompt" "$session" 8>&- || status=$?
    # A run that stopped early says so whether or not the branch moved, so it never reads as a failed fix.
    left=$(desk_left_task "$stream" "$status")

    if ((status == 0)) && ! git -C "$main" merge-base --is-ancestor "$merged" "refs/heads/$branch"; then
        status=1
    elif ((status == 0)) && [[ -n $checks && $(git -C "$main" rev-parse "refs/heads/$branch") == "$before" ]]; then
        status=1
    fi
    if git -C "$dest" rev-parse -q --verify MERGE_HEAD >/dev/null; then
        git -C "$dest" merge --abort
    fi
    python3 -B "$desk_lib/run_log.py" record "$desk_model" "$desk_effort" "$stream" "$pr" || true
    rm -f "$stream"
    if [[ -n $left ]]; then
        outcome="The agent $work stopped early: it ended its turn while $left still ran, so the run stopped \
it$run_link. Any commit it made is on \`$branch\`."
    elif [[ -n $checks ]] && ((status == 0)); then
        outcome="The agent adapted \`$branch\` to $base and committed, which runs its checks again$run_link."
    elif [[ -n $checks ]]; then
        outcome="The agent couldn't make \`$branch\`'s checks pass on $base$run_link; any change it made stays in \
\`$dest\`."
    elif ((status == 0)); then
        outcome="The agent resolved the conflicts with $base on \`$branch\`$run_link."
    else
        outcome="The agent couldn't resolve the conflicts with $base on \`$branch\`$run_link."
    fi
    desk_note review "$pr" "$outcome"
    return "$status"
}

fix_main "$@"; exit
