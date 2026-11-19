# shellcheck shell=bash
# Shared by every agent-desk command: where the project's main checkout is, its repository, and its config.
# Sourced, never run.

desk_lib=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC2034 # The scripts that source this file read it.
desk_root=$(dirname "$desk_lib")
# Each project's repository and main checkout, one "<owner>/<repo> <path>" per line, written by agent-desk install.
desk_projects=${XDG_CONFIG_HOME:-$HOME/.config}/agent-desk/projects

# The repository the current directory's checkout belongs to, as owner/repo: the one its origin names when that is a
# host address, so a project renamed on its host follows the new name, or else the one the projects file records at
# the checkout or at its main checkout, as for a project whose origin is a path or that has none. lib/store.py's
# repo_here finds it the same way.
desk_repo_here() {
    desk_origin_repo || desk_listed_repo
}

# The owner/repo the checkout's origin names, or status 1 when the origin is a path on disk or missing.
desk_origin_repo() {
    local url host='^([^/:@]+@[^:]+:|[a-z+]+://[^/]+/)(.+)$'
    url=$(git remote get-url origin 2>/dev/null) || return 1
    [[ $url =~ $host ]] || return 1
    url=${BASH_REMATCH[2]%/}
    echo "${url%.git}"
}

# The owner/repo the projects file records at the checkout or at its main checkout, or status 1 when it records none.
desk_listed_repo() {
    local top main repo
    [[ -f $desk_projects ]] || return 1
    top=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
    main=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
    repo=$(awk -v top="$top" -v main="$main" \
        '{ path = $0; sub(/^[^ \t]+[ \t]+/, "", path) } path == top || path == main { print $1; exit }' \
        "$desk_projects")
    [[ -n $repo ]] || return 1
    echo "$repo"
}

# The main checkout of the current repository. A runner's own checkout lives under its work folder, so the projects
# file names the main one; elsewhere the checkout's shared git folder does.
desk_main() {
    local repo path
    if [[ -n ${AGENT_DESK_CHECKOUT:-} ]]; then
        echo "$AGENT_DESK_CHECKOUT"
        return
    fi
    repo=${GH_REPO:-$(desk_repo_here || true)}
    if [[ -n $repo && -f $desk_projects ]]; then
        path=$(awk -v repo="$repo" '$1 == repo { print $2; exit }' "$desk_projects")
        if [[ -n $path && -d $path ]]; then
            echo "$path"
            return
        fi
    fi
    if ! git rev-parse --git-common-dir >/dev/null 2>&1; then
        echo "agent-desk: no checkout here, and $desk_projects names none for ${repo:-this repository}." >&2
        return 1
    fi
    dirname "$(git rev-parse --path-format=absolute --git-common-dir)"
}

# A value from the project's .agent-desk.json in the main checkout: desk_config <key.path> [default].
desk_config() {
    python3 -B "$desk_lib/config.py" "$main/.agent-desk.json" "$@"
}

# The worktree for a slug, beside the main checkout: ../<repo>-<slug>.
desk_worktree() {
    echo "$(dirname "$main")/$(basename "$main")-$1"
}

# How a note points at the log of the run posting it: the command that prints the journal of the unit the dispatcher
# started. It names one run only, which lib/release.py relies on to tell this run's notes from an earlier run's.
desk_run_link() {
    if [[ -n ${AGENT_DESK_UNIT:-} ]]; then
        echo " (log: \`journalctl --user -u $AGENT_DESK_UNIT\`)"
    fi
}

# Ends every note agent-desk posts in the local store: a small visible line naming the unit of the run that posts it.
# The store records each comment's author, so the note needs no hidden mark.
desk_poster=${AGENT_DESK_UNIT:+ in ${AGENT_DESK_UNIT%.service}}
desk_mark=$'\n\n<sub>Posted by agent-desk'"$desk_poster"$'</sub>'

# Reads or writes the local store through agent-desk issue, review, and request, as agent-desk itself: desk_store
# issue|review <verb> [args] | request <kind> <target>. Its writes carry the author desk, even inside a run, where
# AGENT_DESK_RUN would make them the agent's and the dispatcher would ignore a request.
desk_store() {
    AGENT_DESK_RUN='' AGENT_DESK_AUTHOR=desk "$desk_root/bin/agent-desk" "$@"
}

# Posts a note, ending with desk_mark, on an issue or a review in the local store: desk_note issue|review <n> <body>.
desk_note() {
    desk_store "$1" comment "$2" --body="$3$desk_mark" >/dev/null
}

# Moves an issue in the local store from a workflow state back to open, and leaves one in any other state, such as an
# issue the agent closed, as it is, the way removing the state's label did on GitHub: desk_leave_state <n> <state>.
desk_leave_state() {
    if [[ $(desk_store issue view "$1" --json status | jq -r .status) == "$2" ]]; then
        desk_store issue edit "$1" --status open >/dev/null
    fi
}

# The number of a branch's open review in the local store, or nothing: desk_open_review <branch>.
desk_open_review() {
    desk_store review list --head "$1" --state open --json number | jq -r '.[0].number // empty'
}

# Runs a command up to four times, waiting 10, 30, then 60 seconds between tries, so it outlasts another git process,
# such as one of the user's, holding a ref the command updates, and a minute or two of failures reaching GitHub.
desk_retry() {
    local wait
    for wait in 10 30 60 0; do
        "$@" && return 0
        ((wait)) && sleep "$wait"
    done
    return 1
}

# Whether any process works in the directory or below it, such as an agent still running or a resumed session.
desk_in_use() {
    local cwd dir
    for cwd in /proc/[0-9]*/cwd; do
        dir=$(readlink "$cwd" 2>/dev/null) || continue
        [[ $dir == "$1" || $dir == "$1"/* ]] && return 0
    done
    return 1
}

# Where a run that reads the code and changes none works on an issue: a detached worktree of main at a path fixed for
# the issue, $XDG_CACHE_HOME/agent-desk/<kind>/<owner>__<repo>/<n>, outside the project's worktrees: desk_scratch_path
# <kind> <n>.
desk_scratch_path() {
    echo "${XDG_CACHE_HOME:-$HOME/.cache}/agent-desk/$1/${GH_REPO//\//__}/$2"
}

# Makes a detached worktree of the local main at a scratch path, after removing one a run cut off before its end left
# there, so the run starts from today's main: desk_scratch_add <dest>.
desk_scratch_add() {
    desk_scratch_remove "$1"
    mkdir -p "$(dirname "$1")"
    git -C "$main" worktree add -q --detach "$1" refs/heads/main
}

# Removes a scratch worktree with anything an agent left in it, unless someone works in it: desk_scratch_remove
# <dest>.
desk_scratch_remove() {
    if [[ -d $1 ]] && ! desk_in_use "$1"; then
        git -C "$main" worktree remove --force "$1" 2>/dev/null || true
    fi
    git -C "$main" worktree prune
}

# The status lib/agent_run.py returns when it ended claude -p after a successful result because a task the agent left
# running still ran, its STOPPED.
desk_stopped=75

# The longest a headless agent may wait on one foreground command, in milliseconds: 2 hours, under the 3-hour limit of
# an agent run in lib/dispatch.py. Claude Code's own ceiling of 10 minutes moves a longer command to the background,
# where lib/agent_run.py stops it once the agent's turn ends. A BASH_MAX_TIMEOUT_MS the user exports keeps its value,
# and one in the env block of Claude Code's settings replaces both.
desk_bash_max_timeout_ms=7200000

# The timeout of a command the agent gives none, in milliseconds: 15 minutes, a branch check's default time limit, so a
# test suite runs in the foreground to its end. Claude Code's own default of 2 minutes moves a longer suite to the
# background, where a headless agent waits on it with a loop of its own, and a loop whose condition never comes true
# holds the run until Claude Code's 10-minute cap ends it. A BASH_DEFAULT_TIMEOUT_MS the user exports keeps its value.
desk_bash_default_timeout_ms=900000

# Runs a headless agent in a folder with the model desk_model_flags chose, prints its readable log, and writes its
# stream-json to a file for the run log: desk_claude_run <dest> <stream> <claude -p arguments>... Returns the agent's
# status. lib/agent_run.py ends claude -p and everything it started 30 seconds after the agent's result, so a task the
# agent left running in the background can't hold the runner and the memory claim, and then returns desk_stopped and
# writes the task it stopped to <stream>.left. A scheduled wakeup, cron job, or Monitor would keep claude -p alive the
# same way, so the run forbids them. The agent may wait on one command for up to desk_bash_max_timeout_ms, and waits
# desk_bash_default_timeout_ms on a command it gives no timeout.
desk_claude_run() {
    local dest=$1 stream=$2
    shift 2
    (cd "$dest" && BASH_MAX_TIMEOUT_MS=${BASH_MAX_TIMEOUT_MS:-$desk_bash_max_timeout_ms} \
        BASH_DEFAULT_TIMEOUT_MS=${BASH_DEFAULT_TIMEOUT_MS:-$desk_bash_default_timeout_ms} \
        python3 -B "$desk_lib/agent_run.py" "$stream" claude -p "$@" --permission-mode auto \
        "${desk_model_args[@]}" --disallowedTools ScheduleWakeup,CronCreate,Monitor \
        --output-format stream-json --verbose)
}

# The task a run that stopped early left running, from the <stream>.left desk_claude_run wrote, or "a task it started"
# when it named none: desk_left_task <stream> <status>. Prints nothing for a run that didn't stop early, and removes
# the file either way.
desk_left_task() {
    local left=
    if (($2 == desk_stopped)); then
        left=$(cat "$1.left" 2>/dev/null) || true
        left=${left:-a task it started}
    fi
    rm -f "$1.left"
    printf '%s' "$left"
}

# Runs a headless agent with desk_claude_run, and adds the run's length and turns to the run log: desk_agent_run <dest>
# <labels> <new|feedback> <claude -p arguments>... Returns the agent's status, desk_stopped for a run that stopped
# early, and leaves the task that run stopped in desk_left, empty for any other run.
desk_agent_run() {
    local dest=$1 labels=$2 start=$3 stream status=0
    shift 3
    stream=$(mktemp)
    desk_claude_run "$dest" "$stream" "$@" || status=$?
    # shellcheck disable=SC2034 # The scripts that source this file read it.
    desk_left=$(desk_left_task "$stream" "$status")
    python3 -B "$desk_lib/run_log.py" record "$desk_model" "$desk_effort" "$stream" \
        --labels "$labels" --start "$start" || true
    rm -f "$stream"
    return "$status"
}

# The Claude model and effort for a run, as lib/models.py picks them from agent-desk's defaults and the project's
# models config: desk_model_flags agent <labels> | chore <skill> | fix. Leaves the flags in desk_model_args and the
# choice in desk_model and desk_effort, and says what it chose in the run's log.
desk_model_flags() {
    local model effort unset="the user's own setting"
    { read -r model; read -r effort; } < <(python3 -B "$desk_lib/models.py" "$main/.agent-desk.json" "$@")
    # shellcheck disable=SC2034 # The scripts that source this file read them.
    desk_model=$model
    # shellcheck disable=SC2034
    desk_effort=$effort
    desk_model_args=()
    [[ -n $model ]] && desk_model_args+=(--model "$model")
    [[ -n $effort ]] && desk_model_args+=(--effort "$effort")
    echo "Model: ${model:-$unset}; effort: ${effort:-$unset}."
}

# Holds a run until the machine has the memory for it, then watches how much memory it uses: desk_memory_start <kind>
# [labels]. lib/memory.py estimates the need from the project's resources config, or from the runs of the same kind
# before it, and claims it so runs starting together don't count the same free memory twice. The claim names the file
# the run's peak goes to, so it can shrink once the run is past when its kind peaks. When the script exits, the run's peak and when it came join the history the next
# estimate learns from.
desk_memory_start() {
    desk_memory_kind=$1
    desk_memory_labels=${2:-}
    desk_memory_repo=${GH_REPO:-$(cd "$main" && desk_repo_here || true)}
    desk_memory_peak=$(mktemp)
    DESK_CONFIG="$main/.agent-desk.json" python3 -B "$desk_lib/memory.py" wait "$desk_memory_repo" \
        "$desk_memory_kind" "$desk_memory_labels" "$$" "$desk_memory_peak" || true
    python3 -B "$desk_lib/memory.py" sample "$desk_memory_peak" &
    desk_memory_sampler=$!
    trap desk_memory_end EXIT
}

desk_memory_end() {
    kill "$desk_memory_sampler" 2>/dev/null || true
    wait "$desk_memory_sampler" 2>/dev/null || true
    python3 -B "$desk_lib/memory.py" record "$desk_memory_repo" "$desk_memory_kind" "$desk_memory_labels" \
        "$desk_memory_peak" || true
    rm -f "$desk_memory_peak"
}

# Whether process <pid> runs in the cgroup of systemd unit <unit>, or in one below it: desk_in_unit <pid> <unit>.
desk_in_unit() {
    local path
    path=$(sed -n 's/^0:://p' "/proc/$1/cgroup" 2>/dev/null) || return 1
    [[ /$path/ == */"$2"/* ]]
}

# Ends the processes this run left in a worktree, such as a compiler server a build started, which would otherwise
# keep the worktree in use when a later step of the run reuses it. It finds them by the cgroup of the run's unit, which
# holds every process the run started, so a session someone resumed there keeps running. Outside a unit it ends
# nothing.
desk_end_leftovers() {
    local dest=$1 cwd dir pid pids=()
    [[ -n ${AGENT_DESK_UNIT:-} ]] || return 0
    for cwd in /proc/[0-9]*/cwd; do
        dir=$(readlink "$cwd" 2>/dev/null) || continue
        [[ $dir == "$dest" || $dir == "$dest"/* ]] || continue
        pid=${cwd#/proc/}
        pid=${pid%/cwd}
        ((pid == $$ || pid == BASHPID)) && continue
        desk_in_unit "$pid" "$AGENT_DESK_UNIT" && pids+=("$pid")
    done
    ((${#pids[@]})) || return 0
    echo "Ending ${#pids[@]} leftover processes in $dest."
    kill "${pids[@]}" 2>/dev/null || true
    sleep 3
    kill -9 "${pids[@]}" 2>/dev/null || true
}

# The branches an issue's base:<branch> labels name, one per line, from its labels joined by commas: desk_issue_bases
# <labels>. An issue without the label prints nothing, and its work branches from main and merges into it.
desk_issue_bases() {
    local label
    local -a names
    IFS=, read -ra names <<<"$1"
    for label in "${names[@]}"; do
        [[ $label == base:?* ]] && echo "${label#base:}"
    done
    return 0
}

# Whether a commit holds history that neither origin nor the local branch <base> holds, as the commits of a branch
# never pushed do: desk_unsaved <commit> [<base>]. A failed read counts as unsaved, so nothing is deleted on a guess.
desk_unsaved() {
    local commit=$1 base=${2:-} found
    local -a holders=(--remotes=origin)
    if [[ -n $base ]] && git -C "$main" show-ref --verify --quiet "refs/heads/$base"; then
        holders+=("refs/heads/$base")
    fi
    found=$(git -C "$main" rev-list -1 "$commit" --not "${holders[@]}" 2>/dev/null) || return 0
    [[ -n $found ]]
}

# Removes an agent worktree and its local branch: desk_remove_worktree <dest> <branch> [<base>]. Keeps both when a
# process works in the worktree or it holds uncommitted changes. Keeps whichever of them holds commits that neither
# origin nor the local <base> branch holds, so a branch never pushed keeps its only copy, even after its worktree was
# removed by hand. Returns 0 when the worktree is gone.
desk_remove_worktree() {
    local dest=$1 branch=$2 base=${3:-} head
    local where="origin${3:+ or $3}"
    if [[ -d $dest ]]; then
        head=$(git -C "$dest" rev-parse --verify -q HEAD) || head=
        if desk_in_use "$dest"; then
            echo "Kept $dest: a process is working in it."
            return 1
        elif [[ -n $(git -C "$dest" status --porcelain) ]]; then
            echo "Kept $dest: it has uncommitted changes."
            return 1
        elif desk_unsaved "$head" "$base"; then
            echo "Kept $dest: it has commits that aren't on $where."
            return 1
        fi
        git -C "$main" worktree remove "$dest" || return 1
        echo "Removed $dest."
    fi
    if git -C "$main" show-ref --verify --quiet "refs/heads/$branch"; then
        if desk_unsaved "refs/heads/$branch" "$base"; then
            echo "Kept local $branch: it has commits that aren't on $where."
        else
            git -C "$main" branch -q -D "$branch"
            echo "Deleted local $branch."
        fi
    fi
}

# The id of the user's desktop session, a wayland or x11 one, or nothing and status 1 on a machine without one.
desk_desktop_session() {
    local id
    for id in $(loginctl list-sessions --no-legend | awk -v uid="$(id -u)" '$2 == uid { print $1 }'); do
        if [[ $(loginctl show-session "$id" -p Class --value) == user &&
            $(loginctl show-session "$id" -p Type --value) =~ ^(wayland|x11)$ ]]; then
            echo "$id"
            return 0
        fi
    done
    return 1
}

# Why the user counts as away from the machine, or nothing while they're there. Chores and fixes start only while the
# user is there. With the config's presence at "desktop", the default, the user is away while their desktop session is
# locked, or when there is none; a desktop that locks the screen after some idle minutes marks the user away then.
# With presence at "always", as on a headless server, the user always counts as there.
desk_away() {
    local id
    [[ $(desk_config presence desktop) == always ]] && return 0
    if ! id=$(desk_desktop_session); then
        echo 'no desktop session is open, and the config'\''s presence is "desktop"'
    elif [[ $(loginctl show-session "$id" -p LockedHint --value) == yes ||
        $(busctl --user call org.freedesktop.ScreenSaver /ScreenSaver org.freedesktop.ScreenSaver GetActive \
            2>/dev/null) == "b true" ]]; then
        echo "the screen is locked"
    fi
}

# agent-desk install sources the helpers alone, before the project has a config.
[[ -n ${DESK_HELPERS_ONLY:-} ]] && return 0
main=$(desk_main) || exit 2
if [[ ! -f $main/.agent-desk.json ]]; then
    echo "agent-desk: $main has no .agent-desk.json; run agent-desk install there first." >&2
    exit 2
fi
# The store's commands read the project from GH_REPO ahead of the current checkout's origin, so a command run from
# another checkout still names this project.
export GH_REPO=${GH_REPO:-$(cd "$main" && desk_repo_here)}
