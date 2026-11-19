#!/usr/bin/env bash
# Merges the reviews queued with Merge in local git, each tested against the main it lands on:
#   agent-desk merges [status] [--json]   show the queue, the reviews that left it, and how far main is ahead of origin
#   agent-desk merges add <n>...          queue reviews to merge, for the next tick to move on, outside an agent run
#   agent-desk merges remove <n>...       take reviews out of the queue
#   agent-desk merges tick [--json]       move the queue on and push main, as the merge and chore timers do
# lib/merges.py keeps the queue and says how it moves on, and lib/pushes.py pushes main to origin.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
merges_main() {
    source "$(dirname "$0")/common.sh"
    cd "$main"
    mode=status
    if [[ -n ${1:-} && $1 != --json ]]; then
        mode=$1
        shift
    fi
    exec python3 -B "$desk_lib/merges.py" "$mode" "$main" "$@"
}

merges_main "$@"; exit
