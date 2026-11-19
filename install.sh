#!/usr/bin/env bash
# Sets agent-desk up on this machine from the checkout this script sits in:
#   ./install.sh
# It links ~/.local/bin/agent-desk to bin/agent-desk, moving whatever else sat there into a backup folder, adds inbox/
# to CLAUDE_CODE_PLUGIN_DIRS in ~/.claude/settings.json so every Claude Code session loads the pane and skills, unless
# the settings enable agent-desk installed from a marketplace, and points the systemd user units that `agent-desk chores
# on`, `agent-desk backup install`, `agent-desk web on`, and `agent-desk dispatch install` wrote at this checkout. A
# second run changes nothing.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
install_main() {
    root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
    bin=$HOME/.local/bin
    settings=$HOME/.claude/settings.json
    units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
    backups=${XDG_STATE_HOME:-$HOME/.local/state}/agent-desk/backups/$(date +%Y%m%d-%H%M%S)

    install_link "$root/bin/agent-desk" "$bin/agent-desk"
    python3 -B "$root/lib/plugin_dirs.py" "$settings" "$root/inbox"
    install_units "$root"

    case ":$PATH:" in
        *":$bin:"* | *":$bin/:"*)
            found=$(command -v agent-desk || true)
            if [[ -n $found && $(readlink -f "$found") != "$root/bin/agent-desk" ]]; then
                echo "note    $found comes before $bin on PATH and runs $(readlink -f "$found")."
            fi
            ;;
        *) echo "note    $bin isn't on PATH. Add it in your shell's profile so agent-desk runs from anywhere." ;;
    esac
}

# Links `live` to `source`. Anything else at `live`, a file, folder, or link elsewhere, moves into the backup folder.
install_link() {
    local source=$1 live=$2
    if [[ -L $live && $(readlink -f "$live") == "$(readlink -f "$source")" ]]; then
        echo "ok      $live -> $source"
        return
    fi
    mkdir -p "$(dirname "$live")"
    if [[ -e $live || -L $live ]]; then
        mkdir -p "$backups"
        mv "$live" "$backups/"
        echo "backup  $live -> $backups/$(basename "$live")"
    fi
    ln -s "$source" "$live"
    echo "linked  $live -> $source"
}

# Rewrites the command or server path in the ExecStart line of each agent-desk unit that exists, keeping the rest of
# the unit, such as the web page's port and node, and whether it is enabled. It sets the unit's AGENT_DESK_INSTALLED
# to this checkout, which lets the unit's code create and migrate the store. It reloads systemd after a change, and
# restarts the web page and the dispatcher when they run, so they run this checkout. A dispatcher restart leaves the
# run units it started running.
install_units() {
    local root=$1 unit changed=() new escaped
    # The checkout's path goes into sed's replacement, where | ends it and & and \ mean more than themselves.
    escaped=$(printf '%s' "$root" | sed 's/[|&\\]/\\&/g')
    for unit in agent-desk-chores.service agent-desk-merges.service agent-desk-backup.service \
        agent-desk-web.service agent-desk-dispatch.service; do
        [[ -f $units/$unit ]] || continue
        new=$(sed -E -e "s|^(ExecStart=)[^ ]*/bin/agent-desk( .*)?$|\1$escaped/bin/agent-desk\2|" \
            -e "s|^(ExecStart=[^ ]+ )[^ ]*/web/server\.ts$|\1$escaped/web/server.ts|" \
            -e '/^Environment="?AGENT_DESK_INSTALLED=/d' \
            -e "s|^\[Service\]$|[Service]\nEnvironment=\"AGENT_DESK_INSTALLED=$escaped\"|" "$units/$unit")
        if [[ $new == "$(cat "$units/$unit")" ]]; then
            echo "ok      $units/$unit"
            continue
        fi
        printf '%s\n' "$new" >"$units/$unit"
        changed+=("$unit")
        echo "updated $units/$unit -> $root"
    done
    ((${#changed[@]})) || return 0
    systemctl --user daemon-reload
    for unit in agent-desk-web.service agent-desk-dispatch.service; do
        if [[ " ${changed[*]} " == *" $unit "* ]] && systemctl --user --quiet is-active "$unit"; then
            systemctl --user restart "$unit"
            echo "restarted $unit"
        fi
    done
}

install_main "$@"; exit
