#!/usr/bin/env bash
# Sets up agent-desk in a project, run from its main checkout or a worktree of it:
#   agent-desk install [<owner>/<repo>]
# The project takes its name from its origin. A project whose config sets "host": "none" may have an origin that is a
# path on disk, or none, and takes its name from the argument, or from the projects file when it was installed before.
# A checkout with no config, whose origin names no host, gets a starter config whose host is none when it is named.
# Writes a starter .agent-desk.json into the current checkout when it has none, records the main checkout in
# ~/.config/agent-desk/projects so the dispatcher and the chore timer find it, and registers the project in the local
# store's projects table, where its issues and reviews live. It reads nothing from GitHub, so it works on a machine
# with no GitHub login. The runs and the pane run agent-desk from PATH, so it warns when that command runs another
# checkout, and when the dispatcher that starts every run isn't running.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
install_main() {
    lib=$(cd "$(dirname "$0")" && pwd)
    root=$(dirname "$lib")
    projects=${XDG_CONFIG_HOME:-$HOME/.config}/agent-desk/projects
    main=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
    here=$(git rev-parse --show-toplevel)
    DESK_HELPERS_ONLY=1 source "$lib/common.sh"
    config=$here/.agent-desk.json
    # A new config's host is none when the project is named here and its origin names no host, and github otherwise.
    host=github
    if [[ -f $config ]]; then
        host=$(python3 -B "$lib/config.py" "$config" host github)
    elif [[ -n ${1:-} ]] && ! desk_origin_repo >/dev/null; then
        host=none
    fi
    if [[ -n ${1:-} ]]; then
        if [[ $host != none ]]; then
            echo "agent-desk install takes a project name only when .agent-desk.json sets \"host\": \"none\"; a" \
                "project on a host takes its name from its origin." >&2
            exit 2
        fi
        if [[ ! $1 =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
            echo "Name the project as <owner>/<repo>, not $1." >&2
            exit 2
        fi
        repo=$1
    elif ! repo=$(desk_repo_here); then
        if [[ $host == none ]]; then
            echo "The checkout's origin is a path or missing, so name the project:" \
                "agent-desk install <owner>/<repo>." >&2
        elif [[ ! -f $config ]]; then
            echo "The checkout's origin is a path or missing, so name the project: agent-desk install" \
                "<owner>/<repo> writes a config whose host is none." >&2
        else
            echo "The checkout has no GitHub origin. For a project kept only on this machine, set \"host\": \"none\"" \
                "in .agent-desk.json and run agent-desk install <owner>/<repo>." >&2
        fi
        exit 2
    fi

    if [[ ! -f $config ]]; then
        cat >"$config" <<JSON
{
  "host": "$host",
  "test": "",
  "worktree": { "seeds": [], "setup": "" },
  "sync": { "run": "", "skipWhile": [] },
  "chores": { "every": {}, "gapHours": 18, "maxAuditIssues": 5 }
}
JSON
        echo "Wrote $config; fill in its commands and chores."
    fi

    mkdir -p "$(dirname "$projects")"
    touch "$projects"
    { awk -v repo="$repo" '$1 != repo' "$projects"; echo "$repo $main"; } >"$projects.new"
    mv "$projects.new" "$projects"
    echo "Recorded $repo at $main in $projects."

    python3 -B "$lib/store_cli.py" project add "$repo" "$main"
    echo "Registered $repo in the local store."
    python3 -B "$lib/desk_path.py" "$root"
    if ! systemctl --user is-active --quiet agent-desk-dispatch.service 2>/dev/null; then
        echo "The dispatcher isn't running, and it starts every agent, chore, and fix run. Turn it on with" \
            "agent-desk dispatch install." >&2
    fi
}

install_main "$@"; exit
