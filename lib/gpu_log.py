"""Records each `agent-desk gpu` call in the store, and lists the waits `agent-desk gpu status` prints.

    python3 gpu_log.py asked <checkout> <exclusive> <asked> [--held-by <names>] [--got <time>]
    python3 gpu_log.py got <call> <time>
    python3 gpu_log.py released <call> <time>
    python3 gpu_log.py status [--hours <n>]

lib/gpu.sh runs `asked` when a call has to wait for its slots, or once it took them without waiting, and `asked`
prints the call's id; `got` when a waiting call takes its slots; and `released` as the wrapper exits. Times are epoch
seconds with their fractions, as bash's EPOCHREALTIME gives them in any locale, and `<exclusive>` is `true` or
`false`. The call belongs to the run AGENT_DESK_RUN names, and to none when it is unset, as outside a run. `status`
lists the waits of the last day, or of the last `<n>` hours, by checkout.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from store import Store, StoreError  # noqa: E402

DAY_HOURS = 24


def option(args, name):
    """Takes `name` and the value after it out of `args`, and returns the value, or None when it is absent."""
    if name in args[:-1]:
        at = args.index(name)
        value = args[at + 1]
        del args[at:at + 2]
        return value
    return None


def seconds_of(text):
    """Epoch seconds as bash's EPOCHREALTIME prints them, with the locale's decimal comma or a point."""
    return float(text.replace(",", "."))


def length(seconds):
    """A length of time as `agent-desk gpu status` prints it: seconds under a minute, then whole minutes."""
    return f"{round(seconds)} s" if seconds < 60 else f"{round(seconds / 60)} min"


def waits_by_checkout(waits):
    """
    The waits grouped by the checkout each ran in, most queued first: each group's checkout, how many waits, the
    seconds they queued in all, and its longest wait with who held the slots then.
    """
    groups = {}
    for wait in waits:
        group = groups.setdefault(wait["checkout"], {"checkout": wait["checkout"], "count": 0, "queued": 0.0,
                                                     "longest": None})
        group["count"] += 1
        group["queued"] += wait["waited"]
        if group["longest"] is None or wait["waited"] > group["longest"]["waited"]:
            group["longest"] = wait
    return sorted(groups.values(), key=lambda group: (-group["queued"], group["checkout"]))


def status_lines(waits, hours):
    """The lines `agent-desk gpu status` prints about the waits of the last `hours`."""
    span = "day" if hours == DAY_HOURS else f"{hours:g} hours"
    groups = waits_by_checkout(waits)
    if not groups:
        return [f"No GPU call waited in the last {span}."]
    lines = [f"GPU waits in the last {span}, by checkout:"]
    for group in groups:
        longest = group["longest"]
        lines.append(f"  {Path(group['checkout']).name}: {group['count']} wait{'' if group['count'] == 1 else 's'},"
                     f" {length(group['queued'])} queued, longest {length(longest['waited'])}"
                     f" behind {longest['held_by'] or 'nobody'}")
    return lines


def main(argv, environ=os.environ, store=None):
    args = list(argv[1:])
    verb = args.pop(0) if args else ""
    try:
        if verb == "status":
            hours = float(option(args, "--hours") or DAY_HOURS)
            if args or hours <= 0:
                raise ValueError
            store = store or Store(read_only=True)
            print("\n".join(status_lines(store.gpu_waits(time.time() - hours * 3600), hours)))
            return 0
        if verb == "asked":
            held_by, got = option(args, "--held-by"), option(args, "--got")
            checkout, exclusive, asked = args
            run = environ.get("AGENT_DESK_RUN", "")
            store = store or Store()
            print(store.gpu_asked(int(run) if run.isdigit() else None, checkout, exclusive == "true",
                                  seconds_of(asked), held_by or None, None if got is None else seconds_of(got)))
            return 0
        if verb in ("got", "released"):
            call, at = args
            store = store or Store()
            (store.gpu_got if verb == "got" else store.gpu_released)(int(call), seconds_of(at))
            return 0
    except ValueError:
        pass
    except StoreError as error:
        print(f"agent-desk: {error}", file=sys.stderr)
        return 1
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
