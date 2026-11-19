#!/usr/bin/env bash
# Turns on or off the systemd user timers that tick every registered project's chores and merge queue, or runs one tick:
#   agent-desk chores on | off
#   agent-desk chores tick-all    what the 5-minute timer runs: agent-desk merges tick, fixes tick, chores tick,
#                                 and chores status, which saves the table the board reads, in each project
#   agent-desk chores merges-all  what the 1-minute timer runs: agent-desk merges tick in each project with reviews
#                                 queued, and agent-desk pushes tick in each project
# Monotonic timers pause while the machine sleeps, so a chore never fires the moment it wakes. The merge queue gets a
# timer of its own because each step waits only on local checks, and the push queue retries a failed push on it.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
chores_timer_main() {
    lib=$(cd "$(dirname "$0")" && pwd)
    projects=${XDG_CONFIG_HOME:-$HOME/.config}/agent-desk/projects
    units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

    case ${1:-} in
        on)
            mkdir -p "$units"
            printf '%s\n' "[Unit]" "Description=Start overdue agent-desk chores and conflict fixes" "" "[Service]" \
                "Environment=\"AGENT_DESK_INSTALLED=$(dirname "$lib")\"" "Type=oneshot" \
                "ExecStart=$(dirname "$lib")/bin/agent-desk chores tick-all" \
                >"$units/agent-desk-chores.service"
            printf '%s\n' "[Unit]" "Description=Check for overdue agent-desk chores and conflicts every 5 minutes" "" \
                "[Timer]" "OnStartupSec=10min" "OnUnitActiveSec=5min" "AccuracySec=1min" "" "[Install]" \
                "WantedBy=timers.target" >"$units/agent-desk-chores.timer"
            printf '%s\n' "[Unit]" "Description=Move agent-desk merge queues on" "" "[Service]" \
                "Environment=\"AGENT_DESK_INSTALLED=$(dirname "$lib")\"" "Type=oneshot" \
                "ExecStart=$(dirname "$lib")/bin/agent-desk chores merges-all" >"$units/agent-desk-merges.service"
            printf '%s\n' "[Unit]" "Description=Move agent-desk merge queues on every minute" "" "[Timer]" \
                "OnStartupSec=1min" "OnUnitActiveSec=1min" "AccuracySec=10s" "" "[Install]" \
                "WantedBy=timers.target" >"$units/agent-desk-merges.timer"
            systemctl --user daemon-reload
            systemctl --user enable --now agent-desk-chores.timer agent-desk-merges.timer
            echo "Timers on. Their decisions: journalctl --user -u agent-desk-chores -u agent-desk-merges"
            ;;
        off)
            systemctl --user disable --now agent-desk-chores.timer agent-desk-merges.timer
            ;;
        merges-all)
            [[ -f $projects ]] || exit 0
            state=${XDG_STATE_HOME:-$HOME/.local/state}/agent-desk/merges
            while read -r repo path; do
                [[ -n ${path:-} && -f $path/.agent-desk.json ]] || continue
                # Only a queue holding a review needs a tick, which pushes too; any other project only pushes.
                if grep -q '"pr"' "$state/${repo/\//__}.json" 2>/dev/null; then
                    (cd "$path" && "$lib/merges.sh" tick </dev/null) || echo "$repo: the merges tick failed."
                else
                    (cd "$path" && "$lib/pushes.sh" tick </dev/null) || echo "$repo: the pushes tick failed."
                fi
            done <"$projects"
            ;;
        tick-all)
            [[ -f $projects ]] || { echo "No projects in $projects."; exit 0; }
            while read -r repo path; do
                [[ -n ${path:-} && -f $path/.agent-desk.json ]] || continue
                echo "$repo:"
                (cd "$path" && "$lib/merges.sh" tick </dev/null) || echo "$repo: the merges tick failed."
                (cd "$path" && "$lib/fixes.sh" tick </dev/null) || echo "$repo: the fixes tick failed."
                (cd "$path" && "$lib/chores.sh" tick </dev/null) || echo "$repo: the tick failed."
                # Saves the chores table the inbox's board reads.
                (cd "$path" && "$lib/chores.sh" status </dev/null >/dev/null 2>&1) \
                    || echo "$repo: the chores status failed."
            done <"$projects"
            ;;
        *)
            echo "usage: agent-desk chores on | off | tick-all | merges-all" >&2
            exit 2
            ;;
    esac
}

chores_timer_main "$@"; exit
