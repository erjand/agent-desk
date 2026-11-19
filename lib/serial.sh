#!/usr/bin/env bash
# Starts a waiting issue only once the work it depends on is done, and gives one serial issue of each serial group at
# a time the right to work, through the issues' states in the local store:
#   agent-desk serial claim 12    move the issue to in-progress, or to waiting behind its open blockers or, for a serial
#                                 issue, behind the issue working in one of its serial groups
#   agent-desk serial release     ask for a run of each waiting issue whose blockers have all closed and whose run no
#                                 longer waits at the dispatcher, a serial one only when no issue holds the slot of any
#                                 of its serial groups and, in Manual, only once it is picked
# The dispatcher keeps the same gates before it starts a run, as lib/dispatch.py describes, so a run it starts claims
# at once. claim covers a start outside it, such as agent-desk start in an interactive session, and release a waiting
# issue whose run ended, such as one claim moved to waiting. The dispatcher also asks for the run of each waiting issue
# with no request once its gates clear, so an issue claim moved to waiting starts even when no release runs. A queued
# issue while the store lists an open issue blocking it waits, so it starts from a main that has its blockers' work. A
# label named `serial` or `serial:<group>` puts an issue in that serial group, and lib/serial_slots.py keeps a slot for
# each group, so issues of different groups work side by side. A serial issue holds its groups' slots while it is
# in-progress, which lasts until its review merges or closes. claim exits 3 when it moves the issue to waiting.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
serial_main() {
    source "$(dirname "$0")/common.sh"
    # Every run on this machine shares the main checkout, so a lock there keeps two runs from claiming at once.
    exec 9>"$main/.git/serial-slot.lock"
    flock 9

    # The open issues that hold or are about to take a slot, with their labels and states.
    holding() {
        desk_store issue list --status in-progress --limit 1000 --json number,labels,status
        desk_store issue list --status queued --limit 1000 --json number,labels,status
    }

    # Moves an issue to waiting, saying why the first time, so asking for it again waits without a second note.
    wait_with() {
        desk_store issue edit "$1" --status waiting >/dev/null
        if [[ $2 != waiting ]]; then
            desk_note issue "$1" "$3"
        fi
    }

    case ${1:-} in
        claim)
            issue=${2:?usage: agent-desk serial claim <issue>}
            IFS='|' read -r status blockers labels < <(desk_store issue view "$issue" --json status,labels,blockedBy |
                jq -r '[.status, ([.blockedBy[] | select(.state == "OPEN") | "#\(.number)"] | join(",")),
                    ([.labels[].name] | join(" "))] | join("|")')
            [[ -n ${status:-} ]] || { echo "agent-desk: the store has no issue #$issue." >&2; exit 1; }
            # Only a fresh start waits on blockers: an @agent request on an issue already worked goes on with its review.
            if [[ -n $blockers && $status == queued ]]; then
                wait_with "$issue" "$status" "Waiting for ${blockers//,/, } to close. This issue starts on its own once \
nothing blocks it, from a main that has that work."
                echo "#$issue waits for ${blockers//,/, }."
                exit 3
            fi
            if [[ " $labels " == *" serial "* || " $labels " == *" serial:"* ]]; then
                busy=$(holding | python3 -B "$desk_lib/serial_slots.py" holder "$issue" "$labels")
                if [[ -n $busy ]]; then
                    # The note names no holder, since the slot may pass to other issues before this one's turn.
                    wait_with "$issue" "$status" "Waiting for the serial slot. This issue starts on its own when the \
serial issues ahead of it in its serial group finish."
                    echo "#$issue waits for #$busy."
                    exit 3
                fi
            fi
            # A run that claims an issue answers any decision it waited on, so it leaves needs-design too. Otherwise
            # work-issue.sh and release.py would take this run for one that stopped for a decision and post nothing. A
            # closed issue, such as one an @agent comment reached, stays closed.
            if [[ $status != closed ]]; then
                desk_store issue edit "$issue" --status in-progress >/dev/null
            fi
            ;;
        release)
            # The issues whose agent run waits at the dispatcher or works already, which need no second request.
            runs=$(python3 -B "$desk_lib/dispatch.py" runs --repo "$GH_REPO")
            asked=$(jq -c '[.[] | select(.kind == "agent" and (.status == "requested" or .status == "running"))
                | .target]' <<<"$runs")
            # In Manual, the issues that may start: those picked, those with a request Start picked, and those whose
            # run works already. A queued issue outside them waits for Start and holds no slot, and a waiting serial
            # issue outside them stays waiting.
            # lib/hold.py's mode_of gives the project's mode the same way.
            mode=$(python3 -B "$desk_lib/hold.py" status)
            picked=()
            if jq -e --arg repo "$GH_REPO" '(.projects[$repo] // .state) == "manual"' <<<"$mode" >/dev/null; then
                picked=("$( {
                    jq -r --arg repo "$GH_REPO" '.picked[] | select(startswith($repo + "#")) | ltrimstr($repo + "#")' \
                        <<<"$mode"
                    jq -r --argjson ids "$(jq -c '.pickedRuns // []' <<<"$mode")" \
                        '.[] | select(.kind == "agent" and (.status == "running" or (.id | IN($ids[])))) | .target' \
                        <<<"$runs"
                } | tr '\n' ' ')")
            fi
            # The waiting issues no open issue blocks and no run waits for, with their labels.
            waiting=$(mktemp)
            desk_store issue list --status waiting --limit 1000 --json number,labels,blockedBy,position |
                jq --argjson asked "$asked" 'map(select(all(.blockedBy[]; .state != "OPEN")
                    and ((.number | tostring | IN($asked[])) | not)) | {number, labels, position})' >"$waiting"
            # Every waiting issue outside the serial groups starts, and the first in the project's start order of each
            # group whose slot is free. A queued serial issue is on its way to claiming its groups' slots, so it counts
            # as holding them here. Otherwise two releases in a row, such as two cleanups, start two waiting issues of
            # a group before either claims. The request carries EVENT=issues, as a run a label asked for does, so
            # Manual holds it for Start.
            started=$(holding | python3 -B "$desk_lib/serial_slots.py" release "$waiting" "${picked[@]}")
            rm -f "$waiting"
            while read -r number; do
                [[ -n $number ]] || continue
                desk_store issue edit "$number" --status queued >/dev/null
                desk_store request agent "$number" --env EVENT=issues --repo "$GH_REPO" >/dev/null
                echo "Started #$number."
            done <<<"$started"
            ;;
        *)
            echo "usage: agent-desk serial claim <issue> | release" >&2
            exit 2
            ;;
    esac
}

serial_main "$@"; exit
