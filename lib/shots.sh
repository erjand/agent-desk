#!/usr/bin/env bash
# Saves screenshots for a review and prints the Markdown that shows them:
#   agent-desk shots 48 .shots/filter_near.png .shots/filter_wide.png
#   agent-desk shots --comment 48 .shots/filter_near.png
# Each picture becomes a 1280-pixel-wide JPEG in ~/.local/state/agent-desk/assets/<owner>__<repo>/, named by a hash of
# its content, and lib/assets.py records it in the store's `assets` table. The web page serves it at
# /api/asset/<hash>, so a later save of a changed picture leaves earlier comments showing what they showed. --comment
# also adds the Markdown to the review as a comment.
# Each save also deletes the pictures of reviews closed more than 30 days ago, except the ones it just saved.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
shots_main() {
    # For desk_mark, which ends the --comment note, and desk_root.
    DESK_HELPERS_ONLY=1 source "$(dirname "$0")/common.sh"
    comment=false
    if [[ ${1:-} == --comment ]]; then
        comment=true
        shift
    fi
    review=${1:?usage: agent-desk shots [--comment] <review> <picture>...}
    shift
    (($#)) || { echo "usage: agent-desk shots [--comment] <review> <picture>..." >&2; exit 2; }
    [[ $review =~ ^[0-9]+$ ]] || { echo "Not a review number: $review" >&2; exit 2; }

    days=30
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    jpegs=()
    names=()
    for picture; do
        # The alt text is the file's name without the characters that would end it or the line: ], [, \, and line
        # breaks.
        name=$(basename -- "${picture%.*}" | tr -d '][\\\r\n')
        jpeg=$tmp/${#jpegs[@]}.jpg
        # A relative path starts with ./, so magick reads a name such as -help.png as a file, not an option.
        [[ $picture == /* ]] || picture=./$picture
        magick "$picture" -flatten -resize '1280x>' -quality 85 "$jpeg"
        jpegs+=("$jpeg")
        names+=("$name")
    done
    mapfile -t hashes < <(python3 -B "$desk_lib/assets.py" save "$review" "${jpegs[@]}")
    ((${#hashes[@]} == ${#jpegs[@]})) || exit 1

    dropped=$(python3 -B "$desk_lib/assets.py" prune --days "$days" "${hashes[@]/#/--keep=}")
    ((dropped == 0)) || echo "Deleted $dropped pictures of reviews closed over $days days ago." >&2

    markdown=$tmp/markdown
    for i in "${!hashes[@]}"; do
        printf '![%s](/api/asset/%s)\n' "${names[i]}" "${hashes[i]}"
    done >"$markdown"
    cat "$markdown"
    if [[ $comment == true ]]; then
        printf '%s\n' "$desk_mark" >>"$markdown"
        "$desk_root/bin/agent-desk" review comment "$review" --body-file "$markdown" >/dev/null
        echo "Commented on #$review."
    fi
}

shots_main "$@"; exit
