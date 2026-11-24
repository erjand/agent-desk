#!/usr/bin/env bash
# Asks the dispatcher for the project's most overdue maintenance chore when the user is at the machine and the machine
# has memory to spare. The run starts once the project has a free chore slot, as the dispatcher's slots allow:
#   agent-desk chores tick      start at most one chore
#   agent-desk chores status    show each chore's last run and when it runs next, and start nothing, warning on stderr
#                               about each chore whose skill Claude Code can't find, and when the machine has no
#                               desktop session for the config's "desktop" presence to wait on. It saves the table in
#                               ~/.local/state/agent-desk/chores/<owner>__<repo>/status.txt for agent-desk board.
# A chore listed in the config's chores.paused never starts from a tick, and shows paused as its next run.
# The systemd user timer from agent-desk chores on ticks every project every 5 minutes while the machine is awake. A
# locked screen means the user is away, since the desktop locks it after the user goes idle, unless the config's
# presence is "always", as desk_away describes. A tick asks the dispatcher for the chore's run, as agent-desk chore
# does, and the store's runs table records when each chore last started and how its run ended.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
chores_main() {
    mode=${1:-status}
    source "$(dirname "$0")/common.sh"
    cd "$main"

    # Each chore and the days between its runs, from the config's chores.every, with "paused" after one the timer skips.
    mapfile -t chores < <(desk_config --chores)
    # At most one chore starts in this many hours, so the chores spread out instead of falling due together: about the
    # shortest interval in hours divided by the number of chores.
    gap_hours=$(desk_config chores.gapHours 18)
    # A chore that files issues, such as audit or security-audit, waits while this many of them are open, and any
    # other chore waits while its last PR is open, so chores never get ahead of the user's reviews.
    max_audit_issues=$(desk_config chores.maxAuditIssues 5)
    # A chore starts only while the machine has this much memory available, so it never pushes the user's work or the
    # agents toward earlyoom. The default is a share of the machine's memory, 12 GB of 64.
    min_free_gb=$(desk_config chores.minFreeGB "$(python3 -B "$desk_lib/memory.py" default minFreeGB)")
    ((${#chores[@]})) || { echo "No chores in $main/.agent-desk.json."; exit 0; }

    # Why the machine can't spare the memory for a chore now, or nothing.
    memory_short() {
        local available
        available=$(awk '$1 == "MemAvailable:" { print int($2 / 1048576) }' /proc/meminfo)
        ((available >= min_free_gb)) \
            || echo "only $available GB of memory is free, under the $min_free_gb GB a chore needs"
    }

    # What holds a chore back while the user catches up on its output, or nothing.
    # A chore that files issues, under the label chores.issueLabels or its defaults give it, waits on their count.
    blocked_by() {
        local skill=$1 label count pr
        if label=$(desk_config --issue-label "$skill"); then
            count=$(desk_store issue list --label "$label" --state open --limit 100 --json number | jq length)
            ((count < max_audit_issues)) || echo "$count open $label issues"
        else
            pr=$(desk_store review list --state open --limit 1000 --json number,headRefName |
                jq -r --arg prefix "agent/chore-$skill-" \
                    'first(.[] | select(.headRefName | startswith($prefix)) | .number) // empty')
            [[ -z $pr ]] || echo "PR #$pr"
        fi
    }

    # Each chore's newest run in the store, one per line: the skill, when it started in epoch seconds, and its status,
    # requested while it waits for the dispatcher, running, or how it ended.
    runs=$(python3 -B "$desk_lib/dispatch.py" chore-runs)

    # The newest run of a chore, as "<started> <status>", or nothing.
    last_run() {
        awk -F'\t' -v skill="$1" '$1 == skill { print $2, $3; exit }' <<<"$runs"
    }

    when() {
        date -d "@$1" '+%a %b %-d %H:%M'
    }

    now=$(date +%s)
    # No chore starts within gap_hours of the last one to start.
    gap=$((gap_hours * 3600))
    latest=$(cut -f2 <<<"$runs" | sort -n | tail -n 1)
    next_start=$now
    [[ -n $latest ]] && next_start=$((latest + gap))
    ((next_start > now)) || next_start=$now

    declare -A last_shown waits under_way
    queue=()
    order=0
    for entry in "${chores[@]}"; do
        order=$((order + 1))
        read -r skill days state <<<"$entry"
        read -r at status <<<"$(last_run "$skill")"
        due=$now
        under_way[$skill]=
        if [[ -z ${at:-} ]]; then
            last_shown[$skill]=never
        else
            due=$((at + days * 86400))
            case $status in
                requested) last_shown[$skill]="queued for $(((now - at) / 60)) min" under_way[$skill]=1 ;;
                running) last_shown[$skill]="running for $(((now - at) / 60)) min" under_way[$skill]=1 ;;
                succeeded) last_shown[$skill]="done $(when "$at")" ;;
                *) last_shown[$skill]="$status $(when "$at")" ;;
            esac
        fi
        waits[$skill]=
        [[ $state == paused ]] && continue
        waits[$skill]=$(blocked_by "$skill")
        if [[ -z ${waits[$skill]} && -z ${under_way[$skill]} ]]; then
            queue+=("$due $order $skill")
        fi
    done

    # Chores take turns in the order they fall due, then in the order listed, each at least gap_hours after the one
    # before, so the first in line is the one a tick starts.
    declare -A start_at
    slot=$next_start
    best=
    while read -r due _ skill; do
        [[ -n ${skill:-} ]] || continue
        ((due > slot)) && slot=$due
        start_at[$skill]=$slot
        [[ -n $best ]] || best=$skill
        slot=$((slot + gap))
    done < <(printf '%s\n' "${queue[@]}" | sort -k1,1n -k2,2n)

    if [[ $mode == status ]]; then
        busy=
        if [[ -n $best ]] && ((start_at[$best] <= now)); then
            busy=$(memory_short)
        fi
        # The table also goes to a file, which agent-desk board reads, so the inbox shows the chores without asking
        # GitHub; the chore timer runs this every 5 minutes.
        saved=${XDG_STATE_HOME:-$HOME/.local/state}/agent-desk/chores/${GH_REPO/\//__}/status.txt
        mkdir -p "$(dirname "$saved")"
        # The chore's column fits the longest name, and at least 14 characters; the pane reads where each column
        # starts from the header.
        width=14
        for entry in "${chores[@]}"; do
            read -r skill _ <<<"$entry"
            ((${#skill} <= width)) || width=${#skill}
        done
        {
            printf '%-*s %-6s %-28s %s\n' "$width" Chore Every "Last run" Next
            for entry in "${chores[@]}"; do
                read -r skill days state <<<"$entry"
                if [[ $state == paused ]]; then
                    next=paused
                elif [[ -n ${waits[$skill]} ]]; then
                    next="waiting on ${waits[$skill]}"
                elif [[ -n ${under_way[$skill]} ]]; then
                    next="after this run"
                elif ((start_at[$skill] <= now)); then
                    next="now${busy:+, but $busy}"
                else
                    next=$(when "${start_at[$skill]}")
                fi
                printf '%-*s %-6s %-28s %s\n' "$width" "$skill" "${days}d" "${last_shown[$skill]}" "$next"
            done
        } | tee "$saved.new"
        mv "$saved.new" "$saved"
        # A chore whose skill isn't installed would fail each run. The warnings go to stderr, since the pane and the
        # web page read every stdout line after the header as a chore's row.
        python3 -B "$desk_lib/chore_skills.py" "$main" "${chores[@]%% *}" >&2
        if [[ $(desk_config presence desktop) != always ]] && ! desk_desktop_session >/dev/null; then
            echo 'Warning: this machine has no desktop session, so with the config'\''s presence at "desktop", no' \
                'chore ever starts here. Set "presence": "always" in .agent-desk.json to start chores and fixes' \
                'without one.' >&2
        fi
        exit 0
    fi

    [[ $mode == tick ]] || { echo "usage: agent-desk chores status | tick | on | off" >&2; exit 2; }
    # Manual and Hold stop the timer for both runners. Claude's limits also stop Claude chores.
    gate=()
    if [[ $(desk_project_runner) =~ ^(codex|local)$ ]]; then
        gate=(codex)
    fi
    if python3 -B "$desk_lib/hold.py" held "${gate[@]}"; then
        echo "The desk is in Manual, holds new runs, or is at its weekly usage limit or API budget, so no chore starts."
        exit 0
    fi
    [[ -n $best ]] || { echo "No chore is due."; exit 0; }
    ((start_at[$best] <= now)) || { echo "$best starts no earlier than $(when "${start_at[$best]}")."; exit 0; }
    away=$(desk_away)
    [[ -z $away ]] || { echo "$best is due, but $away."; exit 0; }
    short=$(memory_short)
    [[ -z $short ]] || { echo "$best is due, but $short."; exit 0; }
    desk_store request chore "$best" >/dev/null
    echo "Asked for a run of $best."
}

chores_main "$@"; exit
