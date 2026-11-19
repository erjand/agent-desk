#!/usr/bin/env bash
# Runs a command while it holds one of the machine's GPU slots, so GPU work from every project and checkout takes
# turns instead of all rendering at once:
#   agent-desk gpu -- tools/run_hidden.sh godot-mono --path . res://scenes/tools/place_shots.tscn
#   agent-desk gpu --exclusive -- tools/run_hidden.sh godot-mono --path . res://scenes/tools/benchmark.tscn
#   agent-desk gpu slots [<n>] [--min-free-vram <GB>]
# The machine has the number of slots `agent-desk gpu slots <n>` writes to gpu-slots in agent-desk's state folder, or
# one when it has written none, and every call reads that file as it starts. AGENT_DESK_GPU_SLOTS overrides it, for
# tests. A shared call takes a slot past the first only while the largest NVIDIA card has at least the VRAM free that
# `--min-free-vram` writes to gpu-min-free-vram beside it, 7 GB by default, and 0 turns the check off. Without a
# reading from nvidia-smi, it takes slot 1 only. --exclusive holds every slot whatever VRAM is free, for timed
# benchmarks: slots 1 to the count and every other slot's lock file in the lock folder, so a caller that still counts
# more slots can't render beside it. The command's exit status becomes this script's. `agent-desk gpu slots` prints
# the count, the VRAM threshold with the VRAM free now, and who holds each slot.
#
# Each slot is a lock file in $XDG_RUNTIME_DIR/agent-desk/, shared by every project and checkout. Every command passes
# through the queue lock to reach the slots, and keeps the queue until it holds its slots, so commands that start while
# an exclusive one waits line up behind it instead of keeping a slot busy forever.
#
# The command runs as a child with the lock descriptors closed, and this script holds the locks until it exits, so a
# daemon the command leaves behind keeps no slot. Killing this script outright frees the slot while the command runs on.
#
# Each call is recorded in the store through lib/gpu_log.py: the run AGENT_DESK_RUN names, the folder it runs in,
# whether it is exclusive, when it asked for its slots, got them, and released them, and who held them when it had to
# wait. The release is recorded as this script exits, on a signal too; a call killed outright leaves its record open,
# and no total counts it. A record that fails is skipped, so the command runs whatever the store says.
#   agent-desk gpu status
# prints what `agent-desk gpu slots` prints, then the last day's waits by checkout.
set -euo pipefail

# Bash reads a running script as it goes, so the work sits in a function bash reads whole before it runs: a
# file replaced under a running job can't change what the job runs next.
gpu_main() {
    usage() {
        echo "usage: agent-desk gpu [--exclusive] -- <command> [args...]" >&2
        echo "       agent-desk gpu slots [<n>] [--min-free-vram <GB>]" >&2
        echo "       agent-desk gpu status" >&2
        exit 2
    }
    here=${BASH_SOURCE[0]%/*}
    setting=${XDG_STATE_HOME:-$HOME/.local/state}/agent-desk/gpu-slots
    vram_setting=${setting%/*}/gpu-min-free-vram
    locks=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/agent-desk
    if [[ ${1:-} == slots ]]; then
        shift
        slots_main "$@"
        return
    fi
    if [[ ${1:-} == status ]]; then
        (($# == 1)) || usage
        slots_main
        python3 -B "$here/gpu_log.py" status
        return
    fi
    asked=$EPOCHREALTIME
    call=
    exclusive=false
    while (($#)); do
        case $1 in
            --exclusive) exclusive=true; shift ;;
            --) shift; break ;;
            -*) usage ;;
            *) break ;;
        esac
    done
    (($#)) || usage
    read_count
    [[ $exclusive == true ]] || ((count == 1)) || read_min

    mkdir -p "$locks"
    numbers=()
    for ((i = 1; i <= count; i++)); do
        numbers+=("$i")
    done
    if [[ $exclusive == true ]]; then
        while read -r i; do
            ((i <= count)) || numbers+=("$i")
        done < <(present)
    fi
    slots=()
    paths=()
    for i in "${numbers[@]}"; do
        paths+=("$locks/gpu.slot$i")
    done
    exec {queue}>>"$locks/gpu.queue"
    for path in "${paths[@]}"; do
        exec {fd}>>"$path"
        slots+=("$fd")
    done

    said=false
    told=
    if [[ $exclusive == true ]]; then
        what="all ${#slots[@]} GPU slots"
        ((${#slots[@]} > 1)) || what="the GPU slot"
    else
        what="a GPU slot"
        ((count > 1)) || what="the GPU slot"
    fi

    take -x "$queue"
    held=()
    if [[ $exclusive == true ]]; then
        for fd in "${slots[@]}"; do
            take -x "$fd"
            held+=("$fd")
        done
    else
        take_any
        held+=("$got")
    fi
    exec {queue}>&-
    for fd in "${slots[@]}"; do
        [[ " ${held[*]} " == *" $fd "* ]] || exec {fd}>&-
    done
    if [[ -n $call ]]; then
        note got "$call" "$EPOCHREALTIME"
    else
        asked=$EPOCHREALTIME
        call=$(note asked "$PWD" "$exclusive" "$asked" --got "$asked")
        [[ -z $call ]] || trap released EXIT
    fi

    status=0
    (
        for fd in "${held[@]}"; do exec {fd}>&-; done
        exec "$@"
    ) || status=$?
    return "$status"
}

# Prints the slot count, the VRAM a slot past the first needs free, and who holds each slot. With a number, writes it
# to the setting file for every later call, and with --min-free-vram, writes the threshold beside it.
slots_main() {
    local i holder ahead n="" has_n=false threshold="" has_gb=false
    while (($#)); do
        case $1 in
            --min-free-vram)
                (($# >= 2)) || usage
                threshold=$2 has_gb=true
                shift 2
                ;;
            -*) usage ;;
            *)
                [[ $has_n == false ]] || usage
                n=$1 has_n=true
                shift
                ;;
        esac
    done
    if [[ $has_n == true ]] && ! valid "$n"; then
        echo "agent-desk gpu slots: the count must be a whole number of at least 1, not '$n'" >&2
        exit 2
    fi
    if [[ $has_gb == true ]] && ! gigabytes "$threshold"; then
        echo "agent-desk gpu slots: --min-free-vram must be a number of GB, such as 7 or 6.5, not '$threshold'" >&2
        exit 2
    fi
    [[ $has_n == false ]] || write "$setting" "$n"
    [[ $has_gb == false ]] || write "$vram_setting" "$threshold"
    if [[ $has_n == true && -n ${AGENT_DESK_GPU_SLOTS:-} ]]; then
        echo "agent-desk gpu slots: AGENT_DESK_GPU_SLOTS=$AGENT_DESK_GPU_SLOTS overrides the setting in this" \
            "environment" >&2
    fi
    read_count
    case $from in
        default) echo "GPU slots: $count, the default" ;;
        AGENT_DESK_GPU_SLOTS) echo "GPU slots: $count, from AGENT_DESK_GPU_SLOTS" ;;
        *) echo "GPU slots: $count, set in $from" ;;
    esac
    read_min
    local where=", set in $min_from"
    [[ $min_from == default ]] && where=", the default"
    if ((min_mib == 0)); then
        echo "Slots past the first open whatever VRAM is free$where"
    elif read_vram; then
        echo "Slots past the first open with $min GB of VRAM free$where; $(gb "$free") GB free now"
    else
        echo "Slots past the first open with $min GB of VRAM free$where; nvidia-smi gives no VRAM reading, so" \
            "only slot 1 opens"
    fi
    for ((i = 1; i <= count; i++)); do
        holder=$(holders "$locks/gpu.slot$i")
        [[ $holder == nobody ]] && holder=free
        echo "slot $i: $holder"
    done
    # A caller that still counts more slots may hold one past the count.
    while read -r i; do
        ((i > count)) || continue
        holder=$(holders "$locks/gpu.slot$i")
        [[ $holder == nobody ]] || echo "slot $i: $holder, past the count"
    done < <(present)
    ahead=$(holders "$locks/gpu.queue")
    [[ $ahead == nobody ]] || echo "next in line: $ahead"
}

# Sets count to the machine's slot count and from to where it came from: AGENT_DESK_GPU_SLOTS when it is set, then
# the setting file, then the default of one. Exits with status 2 when the count is not a whole number of at least 1.
read_count() {
    if [[ -n ${AGENT_DESK_GPU_SLOTS:-} ]]; then
        count=$AGENT_DESK_GPU_SLOTS
        from=AGENT_DESK_GPU_SLOTS
    elif [[ -f $setting ]]; then
        count=
        read -r count <"$setting" || true
        from=$setting
    else
        count=1
        from=default
    fi
    valid "$count" || {
        echo "agent-desk gpu: $from must hold a whole number of at least 1, not '$count'" >&2
        exit 2
    }
}

valid() {
    [[ $1 =~ ^[1-9][0-9]*$ ]]
}

# Writes a value to a setting file whole, so a call that reads it as it is written sees the old value or the new one.
write() {
    mkdir -p "$(dirname "$1")"
    local new
    new=$(mktemp "$1.XXXXXX")
    printf '%s\n' "$2" >"$new"
    mv "$new" "$1"
}

# Sets min to the GB of VRAM a shared call needs free to take a slot past the first, from the gpu-min-free-vram file
# or the default of 7, min_from to where it came from, and min_mib to it in MiB. A GB here is 1000 of the MiB
# nvidia-smi reports, so a card of 16303 MiB has 16.3 GB. Exits with status 2 when the file holds no number.
read_min() {
    if [[ -f $vram_setting ]]; then
        min=
        read -r min <"$vram_setting" || true
        min_from=$vram_setting
    else
        min=7
        min_from=default
    fi
    gigabytes "$min" || {
        echo "agent-desk gpu: $min_from must hold a number of GB, such as 7 or 6.5, not '$min'" >&2
        exit 2
    }
    min_mib=$(awk -v gb="$min" 'BEGIN { printf "%d", gb * 1000 }')
}

gigabytes() {
    [[ $1 =~ ^[0-9]+(\.[0-9]+)?$ ]]
}

# Prints MiB as GB with one decimal.
gb() {
    awk -v mib="$1" 'BEGIN { printf "%.1f", mib / 1000 }'
}

# Sets free to the MiB of VRAM free on the NVIDIA card with the most memory, total less used, from nvidia-smi run with
# every lock descriptor closed. Returns 1 with free empty when nvidia-smi is missing, fails, takes over ten seconds,
# or prints a line that is not two whole numbers.
read_vram() {
    free=$(
        for fd in ${queue:-} "${slots[@]}"; do
            exec {fd}>&-
        done
        timeout 10 nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null |
            awk -F' *, *' '
                NF != 2 || $1 !~ /^ *[0-9]+$/ || $2 !~ /^[0-9]+ *$/ { bad = 1; exit }
                $2 + 0 > total { total = $2 + 0; left = $2 - $1 }
                END {
                    if (bad || total == 0) exit 1
                    print left < 0 ? 0 : left
                }'
    ) 2>/dev/null || {
        free=
        return 1
    }
}

# Returns 0 when the card has the VRAM free that a slot past the first needs.
roomy() {
    ((min_mib == 0)) && return
    read_vram && ((free >= min_mib))
}

# Prints the number of every slot whose lock file is in the lock folder, one per line, in order.
present() {
    local path number
    for path in "$locks"/gpu.slot*; do
        number=${path##*/gpu.slot}
        if valid "$number"; then echo "$number"; fi
    done | sort -n
}

# Runs flock with the given options on one descriptor, with every other lock descriptor closed in the flock process,
# so a flock left waiting by a killed command keeps nothing else locked.
lock() {
    local fd=${*: -1}
    (
        for other in "$queue" "${slots[@]}"; do
            ((other == fd)) || exec {other}>&-
        done
        exec flock "$@"
    )
}

# Takes a lock at once if it is free, and otherwise says what it waits for and blocks until it gets it.
take() {
    lock -n "$@" && return
    waiting
    lock "$@"
}

# Takes the first free slot and sets got to its descriptor. A slot past the first opens only while the card has the
# VRAM free that read_min sets, which it reads only when slot 1 is held. While every open slot is held, it blocks on
# each open slot for a second in turn and tries them all again, so it takes whichever slot frees first. While only
# slot 1 is open, it blocks on slot 1 for three seconds at a time and reads the VRAM again between them.
take_any() {
    local i=0 j open
    while :; do
        lock -n -x "${slots[0]}" && { got=${slots[0]}; return; }
        open=1
        if ((${#slots[@]} > 1)) && roomy; then
            open=${#slots[@]}
        fi
        for ((j = 1; j < open; j++)); do
            lock -n -x "${slots[j]}" && { got=${slots[j]}; return; }
        done
        if ((open == 1 && ${#slots[@]} > 1)); then
            waiting vram
            lock -w 3 -x "${slots[0]}" && { got=${slots[0]}; return; }
        else
            waiting
            i=$((i % open))
            lock -w 1 -x "${slots[i]}" && { got=${slots[i]}; return; }
            i=$((i + 1))
        fi
    done
}

# Records the call once as one that waits behind the checkouts that hold the slots and the queue, then says on stderr
# what it waits for and behind whom. With vram, it waits for slot 1 or for the VRAM to open another slot. It says each
# of the two once.
waiting() {
    local kind=${1:-slots} behind ahead
    [[ " $told " == *" $kind "* ]] && return
    told+=" $kind"
    if [[ $kind == vram ]]; then
        behind=$(holders "${paths[0]}")
    else
        behind=$(holders "${paths[@]}")
    fi
    ahead=$(holders "$locks/gpu.queue")
    [[ $ahead == nobody ]] || behind+="; next in line: $ahead"
    if [[ $said == false ]]; then
        said=true
        call=$(note asked "$PWD" "$exclusive" "$asked" --held-by "$behind")
        [[ -z $call ]] || trap released EXIT
    fi
    if [[ $kind == slots ]]; then
        echo "agent-desk gpu: waiting for $what, held by: $behind" >&2
    elif [[ -n $free ]]; then
        echo "agent-desk gpu: waiting for VRAM, $(gb "$free") GB free of the $min GB a second slot needs," \
            "or for slot 1, held by: $behind" >&2
    else
        echo "agent-desk gpu: waiting for slot 1, held by: $behind; nvidia-smi gives no VRAM reading, so no" \
            "other slot opens" >&2
    fi
}

# Records that the call released its slots, or stopped waiting for them, as the script exits.
# shellcheck disable=SC2329 # The EXIT trap runs it.
released() {
    note released "$call" "$EPOCHREALTIME"
}

# Runs lib/gpu_log.py with every lock descriptor closed, so it holds no slot, and prints what it prints. A record that
# fails says nothing and changes nothing, so the command runs whatever the store says.
note() {
    (
        for fd in "$queue" "${slots[@]}"; do
            exec {fd}>&-
        done
        exec python3 -B "$here/gpu_log.py" "$@"
    ) 2>/dev/null || true
}

# Names the checkouts whose commands hold the given lock files, by the folder each holder runs in, or "nobody".
# It reads each process's /proc/<pid>/fdinfo, which lists the locks the process holds and leaves out one it waits for,
# and matches each locked descriptor to a lock file with -ef, which compares device and inode. lslocks reads
# /proc/locks, which the kernel prints in pieces, and skips a held lock when another process takes or releases a lock
# between two pieces. It needs neither grep nor stat, so it works under a PATH that lacks them. Every caller runs this
# in a command substitution, a copy of the script that shares its locks, so it closes its copies of the lock descriptors
# and skips the script's own process.
holders() {
    local fd pid path cwd
    local -A pids=() seen=()
    for fd in "${queue:-}" "${slots[@]}"; do
        [[ -z $fd ]] || exec {fd}>&-
    done
    while read -r pid fd; do
        [[ -z ${pids[$pid]:-} ]] || continue
        for path in "$@"; do
            [[ /proc/$pid/fd/$fd -ef $path ]] || continue
            pids[$pid]=1
            cwd=$(readlink "/proc/$pid/cwd") && seen[$(basename "$cwd")]=1
            break
        done
    done < <(
        printf '%s\n' /proc/[0-9]*/fdinfo/* | awk -v me="$$" '
            {
                split($0, part, "/")
                if (part[3] == me) next
                while ((getline line < $0) > 0)
                    if (line ~ /^lock:/ && line !~ /->/) { print part[3], part[5]; break }
                close($0)
            }
        '
    )
    if ((${#seen[@]} == 0)); then
        echo nobody
    else
        printf '%s\n' "${!seen[@]}" | sort | paste -sd ' '
    fi
}

gpu_main "$@"; exit
