"""Checks that the runs and the pane will find this checkout's agent-desk command.

    python3 desk_path.py <checkout>

The dispatcher's units and the pane run `agent-desk` from PATH. This prints a warning, and exits 0 either way, when
`agent-desk` on PATH is missing or runs a different checkout.
"""

import os
import shutil
import sys
from pathlib import Path


def found(path):
    """The agent-desk command on a PATH, as the shell finds it, or None."""
    return shutil.which("agent-desk", path=path)


def warnings(checkout, path):
    """What stops the runs from running this checkout's agent-desk, as lines to print."""
    own = Path(checkout, "bin", "agent-desk").resolve()
    command = found(path)
    if command is None:
        return [f"agent-desk isn't on PATH, and the runs and the pane run it from there. Link it into a folder on "
                f"PATH, such as with ln -s {own} ~/.local/bin/agent-desk."]
    if Path(command).resolve() != own:
        return [f"agent-desk on PATH ({command}) runs {Path(command).resolve().parents[1]}, not this checkout, "
                f"{own.parents[1]}."]
    return []


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    for line in warnings(argv[0], os.environ.get("PATH", "")):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
