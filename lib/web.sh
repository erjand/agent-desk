#!/usr/bin/env bash
# Turns on or off the systemd user service that serves the agent-desk web page, every registered project's inbox in a
# browser:
#   agent-desk web on | off | status
#   agent-desk web run [<owner/repo>...]   serves it in this terminal instead, only for the projects named if any
# It listens on 127.0.0.1 only, at port 7480 or AGENT_DESK_WEB_PORT when `on` runs.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
web_main() {
    lib=$(cd "$(dirname "$0")" && pwd)
    server=$(dirname "$lib")/web/server.ts
    units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
    port=${AGENT_DESK_WEB_PORT:-7480}

    case ${1:-status} in
        on)
            node=$(command -v node) || { echo "The agent-desk web page needs Node 24 or newer on PATH." >&2; exit 1; }
            mkdir -p "$units"
            printf '%s\n' "[Unit]" "Description=agent-desk web page at http://127.0.0.1:$port" "" "[Service]" \
                "Environment=\"AGENT_DESK_INSTALLED=$(dirname "$lib")\"" "Environment=AGENT_DESK_WEB_PORT=$port" \
                "ExecStart=$node $server" "Restart=on-failure" "RestartSec=10" \
                "" "[Install]" "WantedBy=default.target" >"$units/agent-desk-web.service"
            systemctl --user daemon-reload
            systemctl --user enable agent-desk-web.service
            # A restart picks up a changed server after an update; it starts one that isn't running.
            systemctl --user restart agent-desk-web.service
            echo "agent-desk on at http://127.0.0.1:$port. Its log: journalctl --user -u agent-desk-web"
            ;;
        off)
            systemctl --user disable --now agent-desk-web.service
            ;;
        status)
            systemctl --user --no-pager status agent-desk-web.service
            ;;
        run)
            exec node "$server" "${@:2}"
            ;;
        *)
            echo "usage: agent-desk web on | off | status | run [<owner/repo>...]" >&2
            exit 2
            ;;
    esac
}

web_main "$@"; exit
