#!/usr/bin/env bash
# Asks for an agent on each review from an agent/<slug> branch that conflicts with main, to merge main and resolve
# them, or whose checks the merge queue found main broke, to adapt it to main:
#   agent-desk fixes tick [--json]     ask for the fixes due, as the chore timer does every 5 minutes; each status and
#                                      tick saves the plan the inbox's board shows
#   agent-desk fixes status [--json]   show each review due a fix and what its fix waits on, and ask for nothing
# Fixes start only while the desk runs, in Auto or Manual, and the user is at the machine. lib/fixes.py decides which
# reviews are due and asks for each fix with `agent-desk request fix <n>`, which the dispatcher starts as a unit.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
fixes_main() {
    mode=${1:-status}
    source "$(dirname "$0")/common.sh"
    cd "$main"
    json=
    [[ ${2:-} == --json ]] && json=--json
    case $mode in
        status) ;;
        tick)
            # A tick held back shows the plan as status does, so the inbox keeps its notes. Manual still asks for the
            # fixes, which wait in Queued for Start.
            if python3 -B "$desk_lib/hold.py" held fix; then
                [[ -n $json ]] || { echo "The desk holds new runs, so no fix starts."; exit 0; }
                mode=status
            else
                away=$(desk_away)
                if [[ -n $away ]]; then
                    [[ -n $json ]] || { echo "${away^}, so no fix starts."; exit 0; }
                    mode=status
                fi
            fi
            ;;
        *) echo "usage: agent-desk fixes [status | tick] [--json]" >&2; exit 2 ;;
    esac
    exec python3 -B "$desk_lib/fixes.py" "$mode" "$main" ${json:+"$json"}
}

fixes_main "$@"; exit
