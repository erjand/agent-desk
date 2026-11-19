#!/usr/bin/env bash
# Pushes main to origin once the merge queue moves it, and the agent branches with it, retrying while origin is down:
#   agent-desk pushes [status] [--json]   show how far main is ahead of origin and how its last push went
#   agent-desk pushes tick [--json]       push main and the agent branches that moved, as each merges tick does
# lib/pushes.py says when it retries and when it stops.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
pushes_main() {
    source "$(dirname "$0")/common.sh"
    cd "$main"
    mode=status
    if [[ -n ${1:-} && $1 != --json ]]; then
        mode=$1
        shift
    fi
    exec python3 -B "$desk_lib/pushes.py" "$mode" "$main" "$@"
}

pushes_main "$@"; exit
