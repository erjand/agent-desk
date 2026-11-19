#!/usr/bin/env bash
# Turns on or off the systemd user service that starts requested runs as systemd units, and writes agentdesk.slice,
# the slice those units run in:
#   agent-desk dispatch install | off | status
#   agent-desk dispatch serve      runs the dispatcher in this terminal instead, as the service does
# lib/dispatch.py describes the dispatcher, and `agent-desk request` asks it for a run.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
dispatch_main() {
    lib=$(cd "$(dirname "$0")" && pwd)
    root=$(dirname "$lib")
    units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

    case ${1:-status} in
        install)
            mkdir -p "$units"
            # The service keeps this shell's PATH, so its units find claude, gh, and node where a terminal does.
            printf '%s\n' "[Unit]" "Description=agent-desk dispatcher, which starts requested runs as systemd units" \
                "" "[Service]" "Environment=\"AGENT_DESK_INSTALLED=$root\"" "Environment=\"PATH=$PATH\"" \
                "Environment=AGENT_DESK_AUTHOR=desk" \
                "ExecStart=$root/bin/agent-desk dispatch serve" "Restart=on-failure" "RestartSec=10" \
                "" "[Install]" "WantedBy=default.target" >"$units/agent-desk-dispatch.service"
            # Every unit the dispatcher starts runs in this slice, whose MemoryHigh caps the runs together.
            python3 -B "$lib/run_units.py" slice >"$units/agentdesk.slice"
            systemctl --user daemon-reload
            systemctl --user enable agent-desk-dispatch.service
            # A restart picks up changed code after an update, and leaves the run units it started running.
            systemctl --user restart agent-desk-dispatch.service
            echo "agent-desk dispatcher on. Its log: journalctl --user -u agent-desk-dispatch"
            ;;
        off)
            systemctl --user disable --now agent-desk-dispatch.service
            ;;
        status)
            systemctl --user --no-pager status agent-desk-dispatch.service
            ;;
        serve)
            exec python3 -B "$lib/dispatch.py" serve
            ;;
        *)
            echo "usage: agent-desk dispatch install | off | status | serve" >&2
            exit 2
            ;;
    esac
}

dispatch_main "$@"; exit
