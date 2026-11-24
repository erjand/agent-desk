#!/usr/bin/env bash
# Copies one path of the config's worktree.seeds from the main checkout into a worktree, for worktree.sh and the branch
# checks in checks.py:
#   seed.sh <main>/target <worktree>/target
# A build in the main checkout can delete or rename files in a seed while cp walks it, such as the Rust target folder
# the sync.run build rewrites just after a merge. cp then copies the rest and exits 1. A pass whose only errors name a
# file that no longer exists copies again over the first, taking only the files newer than their copy, up to three
# passes in all. A seed is a cache the setup completes, so one pass that meets no missing file is enough. Exits 1 with
# cp's errors on stderr when a pass fails for another reason, such as a full disk, or when every pass meets a missing
# file.
set -euo pipefail

seed_passes=3

seed_main() {
    local from=${1:?usage: seed.sh <from> <to>} to=${2:?usage: seed.sh <from> <to>} pass update=()
    mkdir -p "$(dirname "$to")"
    # Global, for the trap that runs after the function returns.
    errors=$(mktemp)
    trap 'rm -f "$errors"' EXIT
    for ((pass = 1; pass <= seed_passes; pass++)); do
        # -T copies into <to> itself when a pass before made it, instead of into a folder of the seed's name inside it.
        # LC_ALL=C keeps cp's errors in the English the check below reads.
        if LC_ALL=C cp -a -T "${update[@]}" "$from" "$to" 2>"$errors"; then
            return 0
        fi
        cat "$errors" >&2
        if [[ ! -s $errors ]] || grep -qv 'No such file or directory' "$errors" || ((pass == seed_passes)); then
            return 1
        fi
        echo "Pass $pass of $seed_passes met files gone from $from, so it copies again." >&2
        update=(--update)
    done
}

seed_main "$@"; exit
