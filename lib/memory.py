"""Holds an agent-desk run until the machine has the memory for it, and learns how much each kind of run uses.

    python3 memory.py need <repo> <kind> [labels]          prints the GB a run needs
    python3 memory.py wait <repo> <kind> [labels] [pid] [peak-file]
                                                           waits until the machine has that much free, then claims it
    python3 memory.py sample <file>                        keeps <file> at its cgroup's peak memory and when it
                                                           reached it, until stopped
    python3 memory.py record <repo> <kind> [labels] <file> adds a finished run's peak from <file> to the history
    python3 memory.py default <name>                       prints a default from SHARES in whole GB for this machine
    python3 memory.py claims                               prints the live claims and what each counts for, as JSON

A kind is `agent`, `fix`, `chore/<skill>`, or `discuss` for a discussion of an idea or a parent issue's check, which
read code and build nothing; labels are the issue's, comma-separated. The project's .agent-desk.json, which DESK_CONFIG names, sets a need
for each kind and label under `resources`, and the history of finished runs takes over once it holds enough of them. A
run waits while the user's apps are under memory pressure, or while the memory available, less what running runs claimed
and haven't taken up yet, falls short of its need and the reserve kept for the person at the machine. A run's claim
shrinks once it has run past its settle time, when nearly every past run of its kind had reached its peak. A fix, which
the merge queue waits on, goes first: it waits only while the pressure is over its own, higher limit, or while the
memory available falls short of its need and the reserve, whatever other runs claimed.
"""

import fcntl
import json
import math
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk"
HISTORY = STATE / "usage.tsv"
CLAIMS = STATE / "claims"
# What a project's config leaves unset: each kind's need, the memory pressure above which no run starts, as the share
# of the last minute some of the user's apps stalled waiting for memory, the higher pressure a fix the merge queue waits
# on starts under, and how long a run waits before it starts anyway, so a
# need the machine can never meet doesn't hold its slot forever. The GB kept free for the person, reserveGB, scales
# with the machine as SHARES sets.
DEFAULTS = {"maxWaitMinutes": 120, "maxMemoryPressure": 5, "fixMemoryPressure": 15,
            "needGB": {"agent": 6, "chore": 3, "fix": 3, "discuss": 2}, "labelGB": {}, "shrinkClaims": True}
# The kinds of run the merge queue waits on, which go first.
PRIORITY_KINDS = ("fix",)
# The defaults that scale with the machine, as shares of its total memory: resources.reserveGB, chores.minFreeGB, the
# MemoryHigh of agent runs and branch checks and of chore and fix runs, and the MemoryHigh of agentdesk.slice, which
# holds every unit the dispatcher starts. A 64 GB machine gets 8, 12, 12, 8, and 32 GB. lib/run_units.py writes the
# slice's share as a percentage, which systemd reads as a share of physical memory.
SHARES = {"reserveGB": 0.125, "minFreeGB": 0.1875, "agentMemoryHigh": 0.1875, "choreMemoryHigh": 0.125,
          "sliceMemoryHigh": 0.5}
# Tests point these at stand-ins to try machines of other sizes and under other loads.
MEMINFO = os.environ.get("AGENT_DESK_MEMINFO", "/proc/meminfo")
PRESSURE = os.environ.get("AGENT_DESK_PRESSURE", "/proc/pressure/memory")
# The user manager's cgroup, whose app.slice and session.slice hold the user's apps. Runs live in agentdesk.slice, their
# sibling, so a run stalled at its own MemoryHigh never counts as pressure on the user's apps.
USER_CGROUP = os.environ.get("AGENT_DESK_USER_CGROUP",
                             f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service")
APP_SLICES = ("app.slice", "session.slice")
# How many finished runs of a kind, and of a kind with a label, the history needs before it sets the need.
MIN_RUNS = 5
MIN_LABEL_RUNS = 3
# The learned need is this share above the 90th percentile of past peaks, for runs larger than any seen yet.
HEADROOM = 1.2
# A run's settle time is SETTLE_FACTOR times the SETTLE_PERCENTILE of the minutes its kind's past runs took to reach their
# peaks, learned from at least MIN_SETTLE_RUNS timed runs, and lengthened by any of its labels with MIN_LABEL_SETTLE_RUNS
# of its own. Past it, the run's claim counts as the larger of SHRINK_FACTOR times its peak so far and its peak so far
# plus SHRINK_SPARE_GB, never more than the claim.
SETTLE_PERCENTILE = 0.95
SETTLE_FACTOR = 1.5
MIN_SETTLE_RUNS = 20
MIN_LABEL_SETTLE_RUNS = 10
SHRINK_FACTOR = 1.5
SHRINK_SPARE_GB = 2.0
POLL_SECONDS = 15
SAMPLE_SECONDS = 5
MAX_HISTORY = 2000
GIB = 1024**3


def resources(config_path=None):
    """The `resources` settings of the project's config, `config_path` or DESK_CONFIG, over the defaults."""
    try:
        with open(config_path or os.environ["DESK_CONFIG"], encoding="utf-8") as handle:
            config = json.load(handle).get("resources", {})
    except (KeyError, OSError, ValueError):
        config = {}
    merged = {**DEFAULTS, "reserveGB": default_gb("reserveGB")}
    for key, value in config.items():
        merged[key] = {**DEFAULTS[key], **value} if isinstance(DEFAULTS.get(key), dict) else value
    return merged


def history(path=None):
    """
    Every finished run the history holds, as (repo, kind, labels, peak GB, minutes to peak). Rows written before the
    history kept the minutes to peak have None there.
    """
    runs = []
    try:
        lines = (path or HISTORY).read_text(encoding="utf-8").splitlines()
    except OSError:
        return runs
    for line in lines:
        fields = line.split("\t")
        if len(fields) in (5, 6):
            repo, kind, labels, peak = fields[1:5]
            minutes = float(fields[5]) if len(fields) == 6 else None
            runs.append((repo, kind, set(filter(None, labels.split(","))), float(peak), minutes))
    return runs


def percentile(values, share=0.9):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def need(repo, kind, labels, settings, runs):
    """
    The GB a run needs. With enough past runs of its kind in the project, the 90th percentile of their peaks with
    HEADROOM, raised by any of its labels with enough runs of their own. Without them, the config's need for the kind,
    raised by any of its labels' needs.
    """
    labels = set(labels)
    configured = max([settings["needGB"].get(kind.split("/")[0], DEFAULTS["needGB"]["agent"])]
                     + [settings["labelGB"][label] for label in labels if label in settings["labelGB"]])
    peaks = [peak for run_repo, run_kind, _, peak, _ in runs if run_repo == repo and run_kind == kind]
    if len(peaks) < MIN_RUNS:
        return configured
    learned = percentile(peaks)
    for label in labels:
        tagged = [peak for run_repo, run_kind, run_labels, peak, _ in runs
                  if run_repo == repo and run_kind == kind and label in run_labels]
        if len(tagged) >= MIN_LABEL_RUNS:
            learned = max(learned, percentile(tagged))
        elif label in settings["labelGB"]:
            learned = max(learned, settings["labelGB"][label] / HEADROOM)
    return round(max(1.0, learned * HEADROOM), 1)


def settle(repo, kind, labels, runs):
    """
    The minutes after which a run's claim may shrink: SETTLE_FACTOR times the SETTLE_PERCENTILE of the minutes past
    runs of its kind in the project took to reach their peaks, lengthened by any of its labels with
    MIN_LABEL_SETTLE_RUNS timed runs of its own. None until the history holds MIN_SETTLE_RUNS timed runs of the kind.
    """
    timed = [(run_labels, minutes) for run_repo, run_kind, run_labels, _, minutes in runs
             if run_repo == repo and run_kind == kind and minutes is not None]
    if len(timed) < MIN_SETTLE_RUNS:
        return None
    learned = percentile([minutes for _, minutes in timed], SETTLE_PERCENTILE)
    for label in set(labels):
        tagged = [minutes for run_labels, minutes in timed if label in run_labels]
        if len(tagged) >= MIN_LABEL_SETTLE_RUNS:
            learned = max(learned, percentile(tagged, SETTLE_PERCENTILE))
    return round(learned * SETTLE_FACTOR, 1)


def meminfo_gb(field, meminfo=None):
    """A field of /proc/meminfo in GB, or 0 when it's missing."""
    with open(meminfo or MEMINFO, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(f"{field}:"):
                return int(line.split()[1]) / 1024**2
    return 0.0


def available_gb(meminfo=None):
    return meminfo_gb("MemAvailable", meminfo)


def total_gb(meminfo=None):
    return meminfo_gb("MemTotal", meminfo)


def pressure():
    """
    The memory pressure the user's apps feel: the larger `some avg60` of app.slice and session.slice under the user
    manager's cgroup, the percentage of the last minute in which some of their tasks stalled waiting for memory, as
    when the kernel swaps them out to make room. Without either slice's file, as without a systemd user manager or
    cgroup v2, it reads the machine's, and a kernel without pressure stall information counts as no pressure, so the
    check never holds a run on it.
    """
    slices = [avg60(Path(USER_CGROUP) / name / "memory.pressure") for name in APP_SLICES]
    found = [value for value in slices if value is not None]
    if found:
        return max(found)
    machine = avg60(PRESSURE)
    return 0.0 if machine is None else machine


def avg60(path):
    """The `some avg60` of a pressure stall file, or None when the file is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("some "):
                    return float(dict(field.split("=") for field in line.split()[1:])["avg60"])
    except (OSError, ValueError, KeyError):
        pass
    return None


def default_gb(name, total=None):
    """
    A default from SHARES in whole GB, at least 1. It rounds up, since MemTotal falls a few GB short of the memory
    installed, so a 64 GB machine with 60 GB in MemTotal still gets 12 GB for 18.75%.
    """
    total = total_gb() if total is None else total
    return max(1, math.ceil(round(SHARES[name] * total, 3)))


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_claim(path):
    """
    A claim file's GB, process, settle time in minutes or None, and peak file or None. The dispatcher writes
    `<gb> <time> <pid>`, and the run's `wait` adds `<settle or -> <peak file>`. Raises OSError or ValueError for a
    file that holds no claim.
    """
    fields = path.read_text(encoding="utf-8").split(maxsplit=4)
    if len(fields) not in (3, 5):
        raise ValueError(f"{path} holds no claim")
    gb, _, pid = fields[:3]
    minutes = None if len(fields) == 3 or fields[3] == "-" else float(fields[3])
    return float(gb), int(pid), minutes, Path(fields[4].strip()) if len(fields) == 5 else None


def read_peak(path):
    """
    A peak file's peak in bytes, seconds from the run's start to that peak, and the start's time, as `sample` keeps
    them, or None while it holds none.
    """
    try:
        peak, seconds, started = path.read_text(encoding="utf-8").split()
        return int(peak), float(seconds), float(started)
    except (OSError, ValueError):
        return None


def counted_gb(gb, minutes, peak_file, now=None):
    """
    The GB a claim counts for, and the run's peak so far in GB when it counts for less than `gb`. Past its settle
    time, `minutes`, a claim counts as the larger of SHRINK_FACTOR times the run's peak so far and that peak plus
    SHRINK_SPARE_GB, never more than `gb`. Before it, or without one, the claim counts in full.
    """
    peak = read_peak(peak_file) if minutes is not None and peak_file is not None else None
    if peak is None or (now or time.time()) - peak[2] < minutes * 60:
        return gb, None
    so_far = peak[0] / GIB
    shrunk = round(max(SHRINK_FACTOR * so_far, so_far + SHRINK_SPARE_GB), 1)
    return (shrunk, so_far) if shrunk < gb else (gb, None)


def live_claims(claims=None, prune=True):
    """
    Each live claim as (pid, gb, settle minutes, peak file). With `prune`, it deletes the claims of runs that ended;
    without, it skips them.
    """
    live = []
    for path in (claims or CLAIMS).glob("*"):
        try:
            gb, pid, minutes, peak_file = read_claim(path)
        except (OSError, ValueError):
            continue
        if alive(pid):
            live.append((pid, gb, minutes, peak_file))
        elif prune:
            path.unlink(missing_ok=True)
    return live


def claim_figures(claims=None, used=None, prune=True):
    """
    Each live claim as a dict of its process, `pid`; the GB it claimed, `claimed`; the GB it counts for now, `counted`;
    the run's peak so far in GB when it counts for less, else None, `peak`; its settle time in minutes or None,
    `settle`; the GB the run's programs hold, `used`; and the GB it holds back beyond them, `held`, which
    `claimed_gb` sums.
    """
    used = used or used_gb
    figures = []
    for pid, gb, minutes, peak_file in live_claims(claims, prune):
        counted, so_far = counted_gb(gb, minutes, peak_file)
        using = used(pid)
        figures.append({"pid": pid, "claimed": gb, "counted": counted, "peak": so_far, "settle": minutes,
                        "used": using, "held": max(0.0, counted - using)})
    return figures


def claimed_gb(claims=None, used=None):
    """
    The GB that running runs have claimed and not yet taken up, dropping the claims of runs that ended. A claim lasts
    as long as its run, which may take half an hour to reach its peak, and shrinks once the run is past its settle
    time, as `counted_gb` decides. Memory a run has already allocated counts against the memory available, so its
    claim counts only for the rest.
    """
    return sum((one["held"] for one in claim_figures(claims, used)), 0.0)


def claims_json(claims=None, used=None, unit=None):
    """
    The live claims as `claim_figures` gives them, each with the systemd unit that holds its process, `unit`, as JSON
    for the web page's Resources view. It reads only, so it leaves the claims of runs that ended for `wait` to delete.
    """
    unit = unit or unit_of
    rounded = lambda one: {key: round(value, 3) if isinstance(value, float) else value for key, value in one.items()}
    return json.dumps([{**rounded(one), "unit": unit(one["pid"])} for one in claim_figures(claims, used, prune=False)])


def shrunk(claims=None):
    """
    The live claims that count for less than they claimed, as (pid, claimed GB, counted GB, peak so far in GB, settle
    minutes), for the dispatcher's log.
    """
    found = []
    for pid, gb, minutes, peak_file in live_claims(claims):
        counted, so_far = counted_gb(gb, minutes, peak_file)
        if so_far is not None:
            found.append((pid, gb, counted, round(so_far, 2), minutes))
    return found


def unit_of(pid):
    """The systemd unit whose cgroup holds `pid`, or None when that can't be read."""
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as handle:
            path = next(line.strip()[3:] for line in handle if line.startswith("0::"))
    except (OSError, StopIteration):
        return None
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name if name.endswith(".service") else None


def used_gb(pid):
    """The GB a run's programs hold now, from the cgroup of its process, or none when that can't be read."""
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as handle:
            path = next(line.strip()[3:] for line in handle if line.startswith("0::"))
        return anon_bytes(Path("/sys/fs/cgroup") / path.lstrip("/")) / GIB
    except (OSError, StopIteration):
        return 0.0


class Shortfall(str):
    """
    Why memory holds a run, as a run's log and the dispatcher's journal say it, with `short`, the reason a card shows
    after "waits for", and for a shortfall of memory, `free` and `needed`, the GB free and the GB the run and the
    reserve take. The dispatcher compares the figures to tell a reason that moved from one that only drifted.
    """

    def __new__(cls, said, short, free=None, needed=None):
        made = super().__new__(cls, said)
        made.short, made.free, made.needed = short, free, needed
        return made


def held_by(gb, settings, priority=False):
    """
    Why a run that needs `gb` can't start now, as a Shortfall, or None when it can: memory pressure over
    resources.maxMemoryPressure, or the memory available, less what running runs claimed, short of its need and the
    reserve. A `priority` run, which the merge queue waits on, waits only for pressure over
    resources.fixMemoryPressure, and counts the memory available without what running runs claimed and haven't taken
    up, since agentdesk.slice's MemoryHigh holds those runs back as they grow.
    """
    stalled = pressure()
    most = settings["fixMemoryPressure" if priority else "maxMemoryPressure"]
    if stalled > most:
        return Shortfall(f"memory pressure is {stalled:g}%, over {most:g}%",
                         f"memory pressure under {most:g}%, now {stalled:g}%")
    free = max(0.0, available_gb() - (0.0 if priority else claimed_gb()))
    reserve = settings["reserveGB"]
    if free < gb + reserve:
        return Shortfall(f"the run needs {gb:g} GB and {reserve:g} GB stays free for the user, and {free:.1f} GB is free",
                         f"memory: {free:.1f} GB free of {gb + reserve:g} GB needed", round(free, 1), gb + reserve)
    return None


def wait(repo, kind, labels, pid, peak_file=None):
    """
    Waits until the user's apps are out of memory pressure and the memory available, less what running runs claimed,
    covers the run's need and the reserve, then claims the need. Says why it waits in the run's log once a minute.
    The claim names the run's settle time and `peak_file`, where `sample` keeps its peak, so the claim can shrink once
    the run is past that time. A run the dispatcher claimed for adds them to its claim.
    """
    settings = resources()
    runs = history()
    gb = need(repo, kind, labels, settings, runs)
    minutes = settle(repo, kind, labels, runs) if settings["shrinkClaims"] is True else None
    shrinks = f" {'-' if minutes is None else f'{minutes:g}'} {peak_file}" if peak_file else ""
    with claims_locked():
        held = claim_of(pid)
        if held is not None:
            claimed = (CLAIMS / str(pid)).read_text(encoding="utf-8").split()[:3]
            (CLAIMS / str(pid)).write_text(f"{' '.join(claimed)}{shrinks}\n", encoding="utf-8")
    if held is not None:
        print(f"Starting with the {held:g} GB the dispatcher claimed.", flush=True)
        say_settle(minutes, peak_file)
        return
    CLAIMS.mkdir(parents=True, exist_ok=True)
    started = time.time()
    said = 0.0
    with open(STATE / "claims.lock", "w", encoding="utf-8") as lock:
        while True:
            fcntl.flock(lock, fcntl.LOCK_EX)
            now = time.time()
            reason = held_by(gb, settings, kind in PRIORITY_KINDS)
            late =now - started > settings["maxWaitMinutes"] * 60
            if reason is None or late:
                (CLAIMS / str(pid)).write_text(f"{gb} {now} {pid}{shrinks}\n", encoding="utf-8")
                fcntl.flock(lock, fcntl.LOCK_UN)
                break
            fcntl.flock(lock, fcntl.LOCK_UN)
            if now - said >= 60:
                print(f"Waiting for memory: {reason}.", flush=True)
                said = now
            time.sleep(POLL_SECONDS)
    waited = round((time.time() - started) / 60)
    if reason is not None:
        print(f"Starting after {waited} min, though {reason}.", flush=True)
    else:
        print(f"Starting with {gb:g} GB claimed{f' after {waited} min' if waited else ''}.", flush=True)
    say_settle(minutes, peak_file)


def say_settle(minutes, peak_file):
    if minutes is not None and peak_file:
        print(f"The claim may shrink after {minutes:g} min, when nearly every past run of its kind had reached its "
              f"peak.", flush=True)


def claim_of(pid, claims=None):
    """The GB a live claim under `pid` holds, as the dispatcher writes for a unit it started, or None."""
    try:
        gb = read_claim((claims or CLAIMS) / str(pid))[0]
    except (OSError, ValueError):
        return None
    return gb if alive(pid) else None


@contextmanager
def claims_locked():
    """Holds the lock that `wait` takes, so a check of the memory free and the claim that follows it stay together."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "claims.lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def spare(repo, kind, labels, waited_seconds, config_path=None):
    """
    Whether a run can start now, without waiting, as `wait` decides, the GB it needs, and what holds it, or None:
    (start, gb, reason). It starts when `held_by` finds nothing holding it, or once it has waited
    resources.maxWaitMinutes. The caller holds `claims_locked` and claims the need with `claim` when the run starts.
    """
    settings = resources(config_path)
    gb = need(repo, kind, labels, settings, history())
    reason = held_by(gb, settings, kind in PRIORITY_KINDS)
    return reason is None or waited_seconds > settings["maxWaitMinutes"] * 60, gb, reason


def claim(gb, pid):
    CLAIMS.mkdir(parents=True, exist_ok=True)
    (CLAIMS / str(pid)).write_text(f"{gb} {time.time()} {pid}\n", encoding="utf-8")


def cgroup_folder():
    with open("/proc/self/cgroup", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("0::"):
                return Path("/sys/fs/cgroup") / line.strip()[3:].lstrip("/")
    return None


def anon_bytes(folder):
    """The memory programs in the cgroup allocated themselves, without the file cache the kernel takes back."""
    with open(folder / "memory.stat", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("anon "):
                return int(line.split()[1])
    return 0


def sample(path):
    """
    Keeps `path` at the peak anonymous memory of this process's cgroup, the seconds from the start of sampling to that
    peak, and the start's time, as `<bytes> <seconds> <start>`, until stopped. The dispatcher starts each run in a
    unit of its own, so the unit's cgroup holds the run.
    """
    folder = cgroup_folder()
    started = time.time()
    peak, at = 0, 0.0
    while folder is not None:
        try:
            now = anon_bytes(folder)
        except OSError:
            return
        if now > peak:
            peak, at = now, time.time() - started
        Path(path).write_text(f"{peak} {at:.0f} {started:.0f}", encoding="utf-8")
        time.sleep(SAMPLE_SECONDS)


def record(repo, kind, labels, path, history_path=None):
    """
    Adds a finished run's peak and the minutes it took to reach it to the history, which keeps its newest MAX_HISTORY
    runs.
    """
    history_path = history_path or HISTORY
    peak = read_peak(Path(path))
    if peak is None:
        # A sampler started before the peak file kept the peak's time wrote the peak alone.
        try:
            peak = (int(Path(path).read_text(encoding="utf-8").strip() or 0), None)
        except (OSError, ValueError):
            return
    if peak[0] <= 0:
        return
    history_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    row = [time.strftime("%Y-%m-%dT%H:%M:%S"), repo, kind, labels, f"{peak[0] / GIB:.2f}"]
    if peak[1] is not None:
        row.append(f"{peak[1] / 60:.1f}")
    lines.append("\t".join(row))
    history_path.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n", encoding="utf-8")


def main(argv):
    if len(argv) < 3 and argv[1:] != ["claims"]:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command = argv[1]
    labels = lambda index: [label for label in (argv[index] if len(argv) > index else "").split(",") if label]
    if command == "need" and len(argv) >= 4:
        print(f"{need(argv[2], argv[3], labels(4), resources(), history()):g}")
    elif command == "wait" and len(argv) >= 4:
        wait(argv[2], argv[3], labels(4), int(argv[5]) if len(argv) > 5 else os.getppid(),
             argv[6] if len(argv) > 6 else None)
    elif command == "sample":
        sample(argv[2])
    elif command == "record" and len(argv) >= 6:
        record(argv[2], argv[3], argv[4], argv[5])
    elif command == "claims":
        print(claims_json())
    elif command == "default" and argv[2] in SHARES:
        print(default_gb(argv[2]))
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
