#!/usr/bin/env bash
# Starts a chore now, once or queued to run again, or stops its queue:
#   agent-desk chore <skill>             run it once
#   agent-desk chore <skill> 3           run it 3 times, one after another
#   agent-desk chore <skill> forever     run it again each time a run ends, until stopped
#   agent-desk chore <skill> stop        run it no more after the run going now
# Each asks the dispatcher for a run with agent-desk request chore <skill>. Each run starts when the one before it
# ends, as run-chore.sh asks lib/chore_repeat.py, and a failed run stops the queue. Each waits for the memory it needs,
# as every chore run does.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
chore_main() {
    skill=${1:?usage: agent-desk chore <skill> [times | forever | stop]}
    times=${2:-1}
    source "$(dirname "$0")/common.sh"
    repo=${GH_REPO:-$(cd "$main" && desk_repo_here)}
    if [[ $times == stop ]]; then
        python3 -B "$desk_lib/chore_repeat.py" stop "$repo" "$skill"
        echo "$skill runs no more after the run going now."
        return
    fi
    if [[ $times != forever && ! $times =~ ^[1-9][0-9]*$ ]]; then
        echo "usage: agent-desk chore <skill> [times | forever | stop]" >&2
        exit 2
    fi
    python3 -B "$desk_lib/chore_repeat.py" set "$repo" "$skill" "$times"
    desk_store request chore "$skill" >/dev/null
    echo "Asked for a run of $skill."
}

chore_main "$@"; exit
