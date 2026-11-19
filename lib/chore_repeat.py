"""Keeps how many more times each chore runs after the one that runs now, or that it runs until stopped.

    python3 chore_repeat.py set <repo> <skill> <times|forever>   queue the chore to run <times> times in all, or loop
    python3 chore_repeat.py next <repo> <skill> <status>         after a run: prints `again` when another run is due
    python3 chore_repeat.py stop <repo> <skill>                  runs no more after the one running now
    python3 chore_repeat.py show <repo>                          prints {skill: runs left, or "forever"} as JSON

`agent-desk chore` sets the count as it starts the first run, and run-chore.sh asks `next` when a run ends. A failed
run stops the queue, so a broken chore doesn't loop.
"""

import fcntl
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk"
MAX_TIMES = 99


@contextmanager
def queues(path=None):
    """Every project's queued chores, as {repo: {skill: runs left, or "forever"}}, written back on leaving."""
    path = path or STATE / "chore-repeat.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        yield data
        data = {repo: chores for repo, chores in data.items() if chores}
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def set_times(data, repo, skill, times):
    """Queues the chore to run `times` times in all, counting the run starting now, or to loop on `forever`."""
    if times == "forever":
        data.setdefault(repo, {})[skill] = "forever"
        return
    left = max(0, min(int(times), MAX_TIMES) - 1)
    if left > 0:
        data.setdefault(repo, {})[skill] = left
    else:
        data.get(repo, {}).pop(skill, None)


def take_next(data, repo, skill, status):
    """After a run of the chore: whether another is due, counting it off. A failed run ends the queue."""
    left = data.get(repo, {}).get(skill)
    if left is None:
        return False
    if status != 0:
        data[repo].pop(skill)
        return False
    if left == "forever":
        return True
    if left <= 1:
        data[repo].pop(skill)
    else:
        data[repo][skill] = left - 1
    return True


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command, repo = argv[1], argv[2]
    with queues() as data:
        if command == "set" and len(argv) == 5:
            set_times(data, repo, argv[3], argv[4])
        elif command == "next" and len(argv) == 5:
            again = take_next(data, repo, argv[3], int(argv[4]))
            print("again" if again else "done")
        elif command == "stop" and len(argv) == 4:
            data.get(repo, {}).pop(argv[3], None)
        elif command == "show":
            print(json.dumps(data.get(repo, {})))
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
