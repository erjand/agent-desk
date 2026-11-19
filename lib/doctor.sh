#!/usr/bin/env bash
# Checks the machine, and the project when run from its checkout, for what agent-desk needs, and says how to fix what's
# missing:
#   agent-desk doctor
# lib/doctor.py runs the checks and exits 1 when any fails.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
doctor_main() {
    DESK_HELPERS_ONLY=1 source "$(dirname "$0")/common.sh"
    local here repo main project=()
    if here=$(git rev-parse --show-toplevel 2>/dev/null) && repo=$(desk_repo_here); then
        main=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
        project=("$repo" "$main" "$here")
    fi
    exec python3 -B "$desk_lib/doctor.py" "$desk_root" "${project[@]}"
}

doctor_main "$@"; exit
