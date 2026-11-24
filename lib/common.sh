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

# Makes a run's worktree with worktree.sh from the main checkout: desk_make_worktree <slug> <base>. When that fails, it
# sets desk_worktree_failed to the clause naming the step that failed, such as "the worktree setup, `just build`,
# failed", for the run's comment, and returns worktree.sh's status.
desk_make_worktree() {
    local failure status=0
    failure=$(mktemp)
    (cd "$main" && AGENT_DESK_SETUP_FAILURE=$failure "$desk_lib/worktree.sh" "$@") 8>&- || status=$?
    desk_worktree_failed=$(cat "$failure")
    rm -f "$failure"
    desk_worktree_failed=${desk_worktree_failed:-agent-desk failed to make the worktree}
    return "$status"
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

# Each process's PID and working directory, one `<pid> <dir>` line each, from a single find over /proc: desk_cwds. A
# process that ended meanwhile, or whose directory this user can't read, prints nothing. A readlink for each of the
# machine's processes would start as many processes as it has, near half a second on a desktop. Callers read it with
# `< <(cd / && desk_cwds)`, so the subshell and the scan's own processes never list the caller's folder.
desk_cwds() {
    find /proc/[0-9]*/cwd -maxdepth 0 -printf '%h %l\n' 2>/dev/null | sed -n 's|^/proc/\([0-9]*\) \(/.*\)|\1 \2|p'
}

# Whether any process works in the directory or below it, such as an agent still running or a resumed session. Sets
# desk_in_use_by to the first one it finds, as desk_process_report prints it, or to nothing.
desk_in_use() {
    local pid dir
    desk_in_use_by=
    while read -r pid dir; do
        if [[ $dir == "$1" || $dir == "$1"/* ]]; then
            desk_in_use_by=$(desk_process_report "$pid")
            return 0
        fi
    done < <(cd / && desk_cwds)
    return 1
}

# A process on one line, so a message can say what held a worktree: its PID, its command line cut to 120 characters,
# and the last part of its cgroup path, such as `123 git status (app-dev.zed.Zed@1.service)`: desk_process_report
# <pid>. Leaves out what it can't read, as of a process that just ended.
desk_process_report() {
    local pid=$1 command cgroup
    local -a args=()
    mapfile -d '' -t args 2>/dev/null <"/proc/$pid/cmdline" || true
    command="${args[*]}"
    [[ -n $command ]] || command=$(cat "/proc/$pid/comm" 2>/dev/null) || command=
    command=${command//[$'\n\t\r']/ }
    if ((${#command} > 120)); then
        command="${command:0:119}…"
    fi
    cgroup=$(sed -n 's/^0:://p' "/proc/$pid/cgroup" 2>/dev/null) || cgroup=
    cgroup=${cgroup%/}
    cgroup=${cgroup##*/}
    echo "$pid${command:+ $command}${cgroup:+ ($cgroup)}"
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

# Removes a scratch worktree with anything an agent left in it, unless someone works in it, and then says which process
# kept it: desk_scratch_remove <dest>.
desk_scratch_remove() {
    if [[ -d $1 ]]; then
        if desk_in_use "$1"; then
            echo "Kept $1: a process is working in it: $desk_in_use_by."
        else
            git -C "$main" worktree remove --force "$1" 2>/dev/null || true
        fi
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
# desk_bash_default_timeout_ms on a command it gives no timeout. The run bills the account desk_account_flags reads,
# and drops ANTHROPIC_API_KEY, which Claude Code would bill ahead of either account. Returns 1 without starting claude
# on `api` without a key. The run's row in the store records the session its --resume or --session-id names before
# claude starts, so a run cut off at its time limit names it too.
desk_claude_run() {
    local dest=$1 stream=$2 arg previous='' session=''
    shift 2
    desk_account_flags || return 1
    for arg in "$@"; do
        if [[ $previous == --resume || ($previous == --session-id && -z $session) ]]; then
            session=$arg
        fi
        previous=$arg
    done
    if [[ -n $session ]]; then
        python3 -B "$desk_lib/run_log.py" session "$session" || true
    fi
    (cd "$dest" && unset ANTHROPIC_API_KEY && BASH_MAX_TIMEOUT_MS=${BASH_MAX_TIMEOUT_MS:-$desk_bash_max_timeout_ms} \
        BASH_DEFAULT_TIMEOUT_MS=${BASH_DEFAULT_TIMEOUT_MS:-$desk_bash_default_timeout_ms} \
        python3 -B "$desk_lib/agent_run.py" "$stream" claude -p "$@" --permission-mode auto \
        "${desk_model_args[@]}" "${desk_account_args[@]}" --disallowedTools ScheduleWakeup,CronCreate,Monitor \
        --output-format stream-json --verbose)
}

desk_project_runner() {
    local runner
    runner=$(python3 -B "$desk_lib/hold.py" issue-runner "$GH_REPO" 2>/dev/null) || runner=
    if [[ -n $runner ]]; then
        printf '%s\n' "$runner"
    else
        desk_config issueRunner claude
    fi
}

# Selects the project runner for this run and prepares its model settings.
desk_runner_flags() {
    local -a local_settings
    desk_runner=${AGENT_DESK_ISSUE_RUNNER:-$(desk_project_runner)}
    desk_runner_error=
    desk_model=
    desk_effort=
    desk_local_provider=
    case "$desk_runner" in
        claude) desk_model_flags "$@" ;;
        codex|local)
            if ! command -v codex >/dev/null; then
                desk_runner_error="install the Codex CLI on the dispatcher's PATH"
            elif [[ $desk_runner == local ]]; then
                mapfile -t local_settings < <(jq -r '
                    def setting: if type == "string" and test("\\S") and (test("[[:cntrl:]]") | not)
                                 then . else "" end;
                    (.localRunner.provider | setting), (.localRunner.model | setting)
                ' "$main/.agent-desk.json" 2>/dev/null)
                desk_local_provider=${local_settings[0]:-}
                desk_model=${local_settings[1]:-}
                if [[ $desk_local_provider != ollama && $desk_local_provider != lmstudio ]]; then
                    desk_runner_error="set localRunner.provider to ollama or lmstudio in .agent-desk.json"
                elif [[ -z $desk_model ]]; then
                    desk_runner_error="set localRunner.model to an installed model name in .agent-desk.json"
                fi
            fi
            ;;
        *) desk_runner_error="issueRunner must be claude, codex, or local" ;;
    esac
    if [[ -n $desk_runner_error ]]; then
        echo "agent-desk: $desk_runner_error." >&2
        return 1
    fi
}

desk_takeover_note() {
    if [[ $desk_runner != claude ]]; then
        echo "The ${desk_local_provider:+Local }Codex session ID is in the run log."
    else
        echo "To take over, run \`claude --resume $2\` in \`$1\`${3:-}."
    fi
}

desk_codex_thread() {
    local thread
    [[ -f $1 ]] && read -r thread <"$1" &&
        [[ $thread =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || return 1
    printf '%s\n' "$thread"
}

desk_has_claude_session() {
    [[ $1 =~ ^[0-9a-f-]+$ ]] &&
        [[ -n $(find "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects" -maxdepth 2 -name "$1.jsonl" -print -quit 2>/dev/null) ]]
}

# Runs the selected CLI. Codex reads the skill file named in its prompt and supplies its own thread ID.
desk_run() {
    local dest=$1 stream=$2 prompt=$3 session=$4 resumed=${5:-} marker=${6:-}
    if [[ $desk_runner == claude ]]; then
        if [[ -n $resumed ]]; then
            desk_claude_run "$dest" "$stream" "$prompt" --resume "$resumed"
        else
            desk_claude_run "$dest" "$stream" "$prompt" --session-id "$session"
        fi
        return $?
    fi
    local -a args
    if [[ $desk_runner == local ]]; then
        args=(exec --oss --local-provider "$desk_local_provider" "--model=$desk_model"
            --sandbox workspace-write -c 'approval_policy="never"' --json)
    else
        args=(exec --approve-for-me --json)
    fi
    if [[ -n $resumed ]]; then
        args+=(resume -- "$resumed")
    else
        args+=(--)
    fi
    (cd "$dest" && AGENT_DESK_CODEX=1 AGENT_DESK_CODEX_SESSION_FILE="$marker" \
        AGENT_DESK_LOCAL_PROVIDER="$desk_local_provider" AGENT_DESK_LOCAL_MODEL="$desk_model" \
        python3 -B "$desk_lib/agent_run.py" "$stream" codex "${args[@]}" "$prompt" </dev/null)
}

desk_skill_prompt() {
    local skill=$1 issue=$2
    if [[ $desk_runner != claude ]]; then
        printf 'Read and follow %s/inbox/skills/%s/SKILL.md for issue #%s.\n' "$desk_root" "$skill" "$issue"
        printf "Use the installed agent-desk command for the project's store and keep the main checkout untouched.\n"
    else
        printf '/agent-desk:%s %s\n' "$skill" "$issue"
    fi
}

# The Claude account a run bills, read once a run: AGENT_DESK_ACCOUNT, as the dispatcher read the setting when it
# started the run's unit, or else lib/account.py's setting as it stands, for a run started by hand. Leaves the account
# in desk_account and the flags that bill it in desk_account_args, says which in the run's log, and returns 1, saying
# why, on `api` without a key.
desk_account_flags() {
    local out settings
    [[ -n ${desk_account:-} ]] && return 0
    out=$(python3 -B "$desk_lib/account.py" claude ${AGENT_DESK_ACCOUNT:+"$AGENT_DESK_ACCOUNT"}) || return 1
    { read -r desk_account; read -r settings; } <<<"$out"
    desk_account_args=()
    [[ -n $settings ]] && desk_account_args=(--settings "$settings")
    echo "Account: $desk_account."
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

# Runs the selected CLI and records the run: desk_agent_run <dest> <labels> <start> <desk_run arguments after stream>.
# Returns the agent's status, desk_stopped for a run that stopped
# early, and leaves the task that run stopped in desk_left, empty for any other run.
desk_agent_run() {
    local dest=$1 labels=$2 start=$3 stream status=0
    shift 3
    stream=$(mktemp)
    desk_run "$dest" "$stream" "$@" || status=$?
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
# nothing. It sends TERM, then KILL to any left 3 seconds later, or AGENT_DESK_LEFTOVER_GRACE seconds when that is
# set, for tests.
desk_end_leftovers() {
    local dest=$1 dir pid pids=()
    [[ -n ${AGENT_DESK_UNIT:-} ]] || return 0
    while read -r pid dir; do
        [[ $dir == "$dest" || $dir == "$dest"/* ]] || continue
        ((pid == $$ || pid == BASHPID)) && continue
        desk_in_unit "$pid" "$AGENT_DESK_UNIT" && pids+=("$pid")
    done < <(cd / && desk_cwds)
    ((${#pids[@]})) || return 0
    echo "Ending ${#pids[@]} leftover processes in $dest."
    kill "${pids[@]}" 2>/dev/null || true
    sleep "${AGENT_DESK_LEFTOVER_GRACE:-3}" || true
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

# How long the config's worktree.teardown may run before it is stopped. lib/checks.py keeps the same limit.
desk_teardown_seconds=60

# Runs the config's worktree.teardown in a worktree about to be removed, so resources its setup or tools made outside it,
# such as a database named after the checkout, go with it: desk_teardown <dest>. A teardown that fails or runs past
# desk_teardown_seconds prints why and still returns 0, so it never blocks a removal.
desk_teardown() {
    local dest=$1 teardown status=0
    teardown=$(desk_config worktree.teardown "") || teardown=
    [[ -n ${teardown//[[:space:]]/} && -d $dest ]] || return 0
    echo "Tearing down $dest: $teardown"
    (cd "$dest" && timeout -k 10 "$desk_teardown_seconds" bash -c "$teardown" </dev/null) || status=$?
    if ((status == 124 || status == 137)); then
        echo "The worktree teardown ran past ${desk_teardown_seconds}s and was stopped: $teardown"
    elif ((status)); then
        echo "The worktree teardown failed with status $status: $teardown"
    fi
    return 0
}

# Removes an agent worktree and its local branch: desk_remove_worktree <dest> <branch> [<base>]. Keeps both when a
# process works in the worktree or it holds uncommitted changes. Keeps the local branch when it holds commits that
# neither origin nor the local <base> branch holds, so a branch never pushed keeps its only copy while its worktree
# goes, and worktree.sh checks the branch out again for a later run. Returns 0 when the worktree is gone.
desk_remove_worktree() {
    local dest=$1 branch=$2 base=${3:-}
    local where="origin${3:+ or $3}"
    if [[ -d $dest ]]; then
        if desk_in_use "$dest"; then
            echo "Kept $dest: a process is working in it: $desk_in_use_by."
            return 1
        elif [[ -n $(git -C "$dest" status --porcelain) ]]; then
            echo "Kept $dest: it has uncommitted changes."
            return 1
        fi
        desk_teardown "$dest"
        # The checks above found the worktree clean, so --force removes only files the teardown left.
        git -C "$main" worktree remove --force "$dest" || return 1
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

# Deletes a branch on origin if origin still has it: desk_delete_on_origin <branch> [<holder>]. With <holder>, a ref
# such as refs/remotes/origin/main, it deletes the branch only when <holder> holds origin's head of it. ls-remote exits
# 2 when origin lacks the branch, so any other failure means origin couldn't be reached, and it returns 1.
desk_delete_on_origin() {
    local branch=$1 holder=${2:-} found=0 head
    head=$(git -C "$main" ls-remote --exit-code origin "refs/heads/$branch") || found=$?
    case $found in
        0) ;;
        2) return 0 ;;
        *) return 1 ;;
    esac
    if [[ -n $holder ]] && ! git -C "$main" merge-base --is-ancestor "${head%%[[:space:]]*}" "$holder" 2>/dev/null; then
        echo "Kept $branch on origin: it has commits that aren't on $holder."
        return 0
    fi
    git -C "$main" push -q origin --delete "$branch" && echo "Deleted $branch on origin."
}

# Deletes origin's copy of an agent branch whose run ended without a review, once the run removed the local branch:
# desk_drop_origin_branch <branch>. The copy stays while the local branch does, while the branch has a review in any
# state, whose comments may point at the copy, and while it holds commits origin's main lacks. A project without
# origin, or whose host is none, has no copy, and origin out of reach keeps it.
desk_drop_origin_branch() {
    local branch=$1 reviews
    git -C "$main" show-ref --verify --quiet "refs/heads/$branch" && return 0
    [[ $(desk_config host "") != none ]] && git -C "$main" remote get-url origin >/dev/null 2>&1 || return 0
    reviews=$(desk_store review list --head "$branch" --state all --limit 1 --json number | jq length) || reviews=
    [[ $reviews == 0 ]] || return 0
    desk_delete_on_origin "$branch" refs/remotes/origin/main || echo "Couldn't reach origin to delete $branch there."
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
