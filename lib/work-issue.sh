#!/usr/bin/env bash
# Works one issue with a headless Claude Code agent in ../<repo>-issue-<n>, on agent/issue-<n>:
#   agent-desk work-issue 12
# The dispatcher runs it when the user labels an issue agent-ready, or starts a comment on the issue or its review with
# @agent. It moves the issue to in-progress, posts the session, runs the work-issue skill, and posts the outcome, on the
# review when REPLY_PR names the review that asked. It reads the issue, its labels, its comments, and its review from
# the local store, and writes its notes and state changes back there. Running it again for the same issue continues
# the same branch and review, with its base merged in first, and takes up the last run's session when that run was cut
# off. The base is main, or the branch a base:<branch> label names, which the worktree branches from and the review
# merges into. A run that stops without a review, and not for a decision or early, removes its worktree. A run that
# stops early, its agent ending its turn while a task it started still ran, says so, keeps its worktree, and exits
# with desk_stopped, and the next run takes up its session. The dispatcher starts the run only once the desk's mode,
# the issue's blockers, and its serial groups let it, so agent-desk serial claim moves the issue to in-progress at
# once; an issue claim finds blocked after all waits instead, and agent-desk serial release asks for its run later.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
work_issue_main() {
    issue=${1:?usage: agent-desk work-issue <issue>}
    source "$(dirname "$0")/common.sh"
    slug="issue-$issue"
    dest=$(desk_worktree "$slug")
    session=$(uuidgen)
    run_link=$(desk_run_link)
    reply_to=${REPLY_PR:-$issue}

    # Posts a note where the request came from. A request from the review also leaves a line on the issue, so the
    # issue's history shows every round of work.
    report() {
        if [[ $reply_to == "$issue" ]]; then
            desk_note issue "$issue" "$1"
        else
            desk_note review "$reply_to" "$1"
            desk_note issue "$issue" "$2"
        fi
    }

    # Moves the issue from in-progress back to open when a run ends with no review to show for it. An issue its
    # branch's open review still holds stays in progress.
    free_issue() {
        if [[ -z $(desk_open_review "agent/$slug") ]]; then
            desk_leave_state "$issue" in-progress
        fi
    }

    # A request from the issue and one from its PR run under different workflow concurrency groups, so the second waits
    # here for the first. The worktree setup and the agent run close the lock's descriptor, so build servers and games
    # they leave running never hold it.
    exec 8>"$main/.git/agent-issue-$issue.lock"
    flock 8

    # A parent issue's work lives in its sub-issues, so labelling it again after the split starts no agent while any
    # sub-issue is open, and runs lib/close-parent.sh's short check once they have all closed. Its first run, before
    # any sub-issue exists, still splits it, and an @agent comment still reaches it to change the split.
    if [[ ${EVENT:-} == issues ]]; then
        subs=$(desk_store issue view "$issue" --json subIssues)
        open_subs=$(jq -r '.subIssues[] | select(.state == "OPEN") | "- #\(.number) \(.title)"' <<<"$subs")
        if [[ -n $open_subs ]]; then
            desk_leave_state "$issue" queued
            desk_note issue "$issue" "This parent issue's work is in its open sub-issues, so agent-desk started no \
agent. Label a sub-issue agent-ready to work it, or comment to the agent here to change the split.

$open_subs"
            exit 0
        fi
        # The check holds the issue's lock, with no serial claim, worktree under the project, or branch.
        if jq -e '.subIssues | length > 0' <<<"$subs" >/dev/null; then
            exec "$desk_lib/close-parent.sh" "$issue"
        fi
    fi

    claim=0
    "$desk_lib/serial.sh" claim "$issue" || claim=$?
    if ((claim == 3)); then
        exit 0
    elif ((claim != 0)); then
        exit "$claim"
    fi
    # The issue is the agent's now, so a pick for Manual has done its work; a later run from a label waits for Start.
    python3 -B "$desk_lib/hold.py" spend "$issue" || true
    # The store keeps the workflow state apart from the labels, so these are the grouping labels alone, such as
    # base:<branch>, serial:<group>, and benchmark.
    issue_labels=$(desk_store issue view "$issue" --json labels | jq -r '[.labels[].name] | join(",")')
    # A base:<branch> label names the branch the work starts from and merges into, such as one that gathers a larger
    # change before it lands on main. A base the main checkout has no local branch for, or more than one, stops the run
    # before it waits for memory. The run reads only local branches, so it starts with origin out of reach.
    mapfile -t bases < <(desk_issue_bases "$issue_labels")
    base=${bases[0]:-main}
    stop=
    if ((${#bases[@]} > 1)); then
        # shellcheck disable=SC2016 # The backticks are Markdown for the comment, not command substitution.
        stop="the issue has more than one base label ($(printf '`base:%s` ' "${bases[@]}" | sed 's/ $//')). Keep \
one, then label the issue agent-ready again"
    elif [[ $base != main ]] && ! git -C "$main" show-ref --verify --quiet "refs/heads/$base"; then
        stop="the main checkout has no branch \`$base\`, which the issue's \`base:$base\` label names. Create the \
branch there or change the label, then label the issue agent-ready again"
    fi
    if [[ -n $stop ]]; then
        free_issue
        report "The agent didn't start: $stop$run_link." \
            "The agent didn't start on the feedback from #$reply_to: $stop$run_link."
        "$desk_lib/serial.sh" release
        exit 1
    fi
    # The run waits here, holding its slot, until the machine has the memory its issue's kind of work needs.
    desk_memory_start agent "$issue_labels"
    desk_model_flags agent "$issue_labels"

    # A run cut off, at its time limit or by hand, left its session on this machine, so the next run takes that session
    # up again, with all it knew, in place of a new one. So does a run that stopped early, when the agent ended its turn
    # while a task it started still ran. The notes on the issue and its review say whether the last run was cut off or
    # stopped early, with the task it stopped, and which session it ran.
    continuing=0
    [[ -d $dest ]] && continuing=1
    resumed=
    cut_off=no
    last_session=
    stopped_task=
    read -r cut_off last_session stopped_task < <({ desk_store issue view "$issue" --json comments
        desk_store review view "agent/$slug" --json comments 2>/dev/null || true; } |
        jq -c '.comments[] | {id, createdAt, body}' | python3 -B -c '
import json, re, sys
notes = sorted((json.loads(line) for line in sys.stdin if line.strip()), key=lambda one: (one["createdAt"], one["id"]))
ours = [one["body"] for one in notes if re.match(r"An agent started|The agent (finished|stopped|run)", one["body"])]
last = ours[-1] if ours else ""
early = re.match(r"The agent stopped early: it ended its turn while (.+?) still ran, so the run stopped it\.", last)
cut = "early" if early else "yes" if last.startswith("The agent run") and "cancelled" in last.split("(")[0] else "no"
ids = [m.group(1) for body in ours if (m := re.search(r"claude --resume ([0-9a-f-]+)", body))]
print(cut, ids[-1] if ids else "-", early.group(1) if early else "")') || true
    [[ $last_session == - ]] && last_session=
    if [[ ($cut_off == yes || $cut_off == early) && $continuing == 1 && -n $last_session ]] \
        && find "$HOME/.claude/projects" -maxdepth 2 -name "$last_session.jsonl" | grep -q .; then
        resumed=$last_session
        session=$last_session
    fi

    started="An agent started on \`agent/$slug\`${resumed:+, taking up the session stopped last time,}$run_link."
    report "$started To take over, run \`claude --resume $session\` in \`$dest\`." \
        "An agent started on feedback from #$reply_to$run_link."

    # A failed setup, such as a main that doesn't build, frees the issue as a run that stops without a PR does.
    if [[ ! -d $dest ]] && ! (cd "$main" && "$desk_lib/worktree.sh" "$slug" "$base") 8>&-; then
        free_issue
        setup=$(desk_config worktree.setup "")
        report "The agent didn't start: the worktree setup, \`$setup\`, failed$run_link." \
            "The agent didn't start on the feedback from #$reply_to: the worktree setup failed$run_link."
        "$desk_lib/serial.sh" release
        exit 1
    fi

    # A worktree an earlier run left takes in its base first, so the agent goes on from the fixes that landed since,
    # such as the project's own tools.
    merged=
    if ((continuing)); then
        if [[ -n $(git -C "$dest" status --porcelain --untracked-files=no) ]]; then
            merged="The worktree has uncommitted changes, so agent-desk left $base unmerged: commit or drop them, then \
merge it."
        elif git -C "$dest" merge -q --no-edit "refs/heads/$base" >/dev/null 2>&1; then
            merged="agent-desk merged $base into the branch, so it has what landed since."
        else
            git -C "$dest" merge --abort 2>/dev/null || true
            merged="$base conflicts with the branch, and agent-desk left it unmerged: merge it and resolve the \
conflicts first."
        fi
        echo "$merged"
    fi

    prompt=("/agent-desk:work-issue $issue" --session-id "$session")
    carry_on="Read the comments on the issue and its review since your last commit for anything new, then carry on \
with the /agent-desk:work-issue skill from where you stopped: commit, open or update the draft review, verify, and \
finish."
    if [[ -n $resumed && $cut_off == early ]]; then
        prompt=("agent-desk stopped your last run on #$issue early: you ended your turn while $stopped_task still \
ran, so the run stopped it, and this run takes up the same session. $merged Run it again in the foreground, and don't \
end your turn until it returns: if your shell moves it to the background, wait for it to finish before you go on. \
$carry_on" --resume "$resumed")
    elif [[ -n $resumed ]]; then
        prompt=("agent-desk stopped your last run on #$issue before it finished, at the run's time limit or because \
the user paused it, and this run takes up the same session. $merged $carry_on" --resume "$resumed")
    fi

    status=0
    stream=$(mktemp)
    # The run's stream-json goes to a file too, for its length and turns in the run log.
    desk_claude_run "$dest" "$stream" "${prompt[@]}" 8>&- || status=$?
    # A run that stopped early names the task it stopped, for the note and the next run's prompt.
    left=$(desk_left_task "$stream" "$status")

    # The agent moves the issue to needs-design when it stops for a decision, and has commented already.
    status_now=$(desk_store issue view "$issue" --json status | jq -r .status)
    read -r pr_number pr < <(desk_store review list --head "agent/$slug" --state open --json number,isDraft |
        jq -r '.[0] | select(.)
    | "\(.number) review #\(.number)" + (if .isDraft then " (still a draft)" else " (ready for review)" end)') || true
    start=new
    if [[ -n $resumed ]]; then
        start=resumed
    elif ((continuing)); then
        start=feedback
    fi
    python3 -B "$desk_lib/run_log.py" record "$desk_model" "$desk_effort" "$stream" "${pr_number:-}" \
        --labels "$issue_labels" --start "$start" || true
    rm -f "$stream"
    early="The agent stopped early: it ended its turn while $left still ran, so the run stopped it."
    if [[ $status_now == needs-design ]]; then
        :
    elif [[ -n $left && -n ${pr:-} ]]; then
        # The next run takes up the session, so the issue keeps its worktree and stays in progress.
        desk_note issue "$issue" "$early Its work is on $pr, and the next run on the issue takes up the same \
session$run_link."
        desk_note review "$pr_number" "$early The next run on the issue takes up the same session$run_link."
    elif [[ -n $left ]]; then
        desk_note issue "$issue" "$early It opened no review yet, and the next run on the issue takes up the same \
session$run_link."
    elif [[ -n ${pr:-} ]]; then
        # The issue and the review both get the same note, whichever one asked.
        desk_note issue "$issue" "The agent finished: $pr$run_link."
        desk_note review "$pr_number" "The agent finished: $pr$run_link."
    else
        desk_leave_state "$issue" in-progress
        report "The agent stopped without opening a PR$run_link." \
            "The agent stopped on the feedback from #$reply_to without a PR$run_link."
        # Nothing waits on the worktree, and a later run makes a new one. One that holds work the agent never pushed
        # stays.
        desk_end_leftovers "$dest"
        desk_remove_worktree "$dest" "agent/$slug" || true
    fi
    # An issue that stopped for a decision or without a PR no longer holds the serial slot, so the queue moves on.
    "$desk_lib/serial.sh" release
    return "$status"
}

work_issue_main "$@"; exit
