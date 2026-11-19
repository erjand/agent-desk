"""Hands an issue back when its agent run ends without the run's own report, for the dispatcher's exit handler.

    python3 release.py <issue> <cancelled|failure> [--discussion]

A run that was cancelled or timed out, or one whose script failed, never reaches the
report work-issue posts, so the issue would stay `in-progress` with no agent behind it. This moves it back to `open`,
unless the issue's review is still open, posts that the run ended and how, and hands the serial slot on. A failed run
from the `agent-ready` label whose agent never started goes back to `queued` with a new run request, once, so it runs
again; a second such failure in a row leaves the issue for the user. A run that already posted its report, or stopped
for a decision with `needs-design`, has nothing to release. With --discussion, for a discussion run of an idea, it
only posts how the run ended: the issue keeps its state, holds no serial slot, and no run starts again.

The caller sets GH_REPO and EVENT, REPLY_PR for a request from the issue's review, and RUN_LOG for a file holding the
run's output, whose last line says what failed. The dispatcher's exit handler names the run's unit in AGENT_DESK_UNIT,
which marks the notes the run posted. The issue, its notes, and its review live
in the local store, which this reads and writes as agent-desk itself, with the author `desk`.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import store  # noqa: E402

DESK_LIB = Path(__file__).resolve().parent
# How work-issue's own reports start; a run that posted one reported its outcome already.
REPORTS = ("The agent finished", "The agent stopped", "The agent didn't start")
STARTED = "An agent started"
# work-issue.sh reads the notes that start this way, and takes a run whose last note says "cancelled" before its first
# parenthesis as cut off, so a failure note keeps that word out.
RUN_NOTE = re.compile(r"An agent started|The agent (finished|stopped|run|didn't)")
RETRY = "put `agent-ready` back"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
MAX_ERROR = 300


def mark():
    """desk_mark from lib/common.sh: the store records each note's author, so the note needs no hidden mark."""
    unit = os.environ.get("AGENT_DESK_UNIT", "").removesuffix(".service")
    return f"\n\n<sub>Posted by agent-desk{f' in {unit}' if unit else ''}</sub>"


def run_link(unit):
    """How a note points at the log of the run's unit, as desk_run_link in lib/common.sh words it."""
    return f" (log: `journalctl --user -u {unit}`)" if unit else ""


def last_line(path):
    """The log's last line with text, without terminal colors or backticks, cut to MAX_ERROR characters."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        line = ANSI.sub("", line).replace("`", "'").strip()
        if line:
            return line if len(line) <= MAX_ERROR else line[:MAX_ERROR - 1] + "…"
    return ""


def discussion_plan(issue, outcome, unit, error=""):
    """What releasing a discussion run of an idea takes, as plan() says it: a note on the issue, and nothing else."""
    link = run_link(unit)
    if outcome == "cancelled":
        note = f"The discussion run was cancelled or timed out{link}."
    else:
        note = f"The discussion run failed{link}."
        if error:
            note += f" Its last line: `{error}`"
    return {"free": False, "ready": False, "notes": [(issue, note + mark())]}


def plan(issue, outcome, status, comments, unit, event="", open_pr=False, reply_pr="", error=""):
    """
    What releasing the issue takes: {"free": whether the issue moves from `in-progress` back to `open`, "ready":
    whether it goes back to `queued` to run again, "notes": [(number, body)]}, or None when the run reported its
    outcome itself or the issue waits on a decision. `status` is the issue's workflow state in the store.
    """
    # The run's own notes carry its unit's name. A run without one can't tell its notes from an earlier run's, so it
    # matches none.
    ours = lambda body, start: bool(unit) and unit in body and body.startswith(start)
    if any(ours(body, REPORTS) for body in comments) or status == "needs-design":
        return None
    started = any(ours(body, STARTED) for body in comments)
    notes = [body for body in comments if RUN_NOTE.match(body)]
    retried = bool(notes) and notes[-1].startswith("The agent run failed") and RETRY in notes[-1]
    holding = status == "in-progress" and not open_pr
    retry = (outcome == "failure" and holding and event == "issues" and not reply_pr and not started
             and not retried)
    link = run_link(unit)
    if outcome == "cancelled":
        note = f"The agent run was cancelled or timed out{link}."
        issue_note = f"The agent run on feedback from #{reply_pr} was cancelled or timed out."
    else:
        note = f"The agent run failed{link}."
        if error:
            note += f" Its last line: `{error}`"
        if retry:
            note += f"\n\nThe agent never started, so agent-desk {RETRY} to run it once more."
        elif holding:
            note += "\n\nagent-desk took `in-progress` off. Label the issue `agent-ready` to run it again."
        issue_note = f"The agent run on feedback from #{reply_pr} failed{link}."
    notes_out = [(reply_pr or issue, note + mark())]
    if reply_pr:
        notes_out.append((issue, issue_note + mark()))
    return {"free": holding, "ready": retry, "notes": notes_out}


def serial_release():
    """Hands the serial slot on; a failure leaves it for the next release."""
    if subprocess.run([str(DESK_LIB / "serial.sh"), "release"]).returncode != 0:
        print("agent-desk serial release failed; the dispatcher asks for the waiting issues' runs on its next store "
              "event or reconcile.", flush=True)


def release(desk, issue, outcome, repo, unit, event="", reply_pr="", error="", hand_on=None, discussion=False):
    """Reads the issue from the store `desk`, applies plan(), says what it did, and hands the serial slot on with
    `hand_on`, serial_release by default. A discussion run's release applies discussion_plan() alone."""
    number = int(issue)
    item = desk.item(repo, number, "issue")
    if discussion:
        for target, body in discussion_plan(issue, outcome, unit, error)["notes"]:
            desk.comment(repo, int(target), body)
        print(f"Said on #{issue} how its discussion run ended.")
        return
    comments = [comment["body"] or "" for comment in item["comments"]]
    open_review = bool(desk.items(repo, "review", head=f"agent/issue-{number}", limit=1))
    steps = plan(issue, outcome, item["status"], comments, unit, event, open_review, reply_pr, error)
    if steps is None:
        print(f"The agent reported its outcome on #{issue}, or waits on a decision, so there is nothing to release.")
        return
    # Frees the issue before any note, and queues it again after the notes, so the run it starts finds them.
    if steps["free"] and desk.item(repo, number, "issue")["status"] == "in-progress":
        desk.set_state(repo, number, "open")
        print(f"Moved #{issue} from in-progress back to open.")
    for target, body in steps["notes"]:
        desk.comment(repo, int(target), body)
    if steps["ready"]:
        desk.set_state(repo, number, "queued")
        benchmark = any(label["name"] == "benchmark" for label in item["labels"])
        run = desk.request_run(repo, "agent", str(number), benchmark, {"EVENT": "issues"})
        print(f"Queued #{issue} again as run {run}, so it runs once more.")
    (hand_on or serial_release)()


def main(argv):
    discussion = argv[3:] == ["--discussion"]
    if len(argv) != 3 + discussion or argv[2] not in ("cancelled", "failure") or not os.environ.get("GH_REPO"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    # The notes and state changes are agent-desk's own, even when the exit handler of a run releases its issue.
    os.environ.pop("AGENT_DESK_RUN", None)
    os.environ["AGENT_DESK_AUTHOR"] = "desk"
    try:
        desk = store.Store()
        try:
            release(desk, argv[1], argv[2], os.environ["GH_REPO"],
                    os.environ.get("AGENT_DESK_UNIT", ""), os.environ.get("EVENT", ""),
                    os.environ.get("REPLY_PR", ""), last_line(os.environ.get("RUN_LOG")), discussion=discussion)
        finally:
            desk.close()
    except (store.StoreError, ValueError) as error:
        print(f"Couldn't release #{argv[1]}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
