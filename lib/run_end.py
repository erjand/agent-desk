"""The exit handler: `python3 dispatch.py stopped`, each run unit's ExecStopPost, records how the unit's run ended."""

import os
import subprocess
import tempfile

import store
from agent_run import STOPPED
from run_units import AGENT_DESK


def outcome(result, code, status):
    """How a unit's run ended, from the SERVICE_RESULT, EXIT_CODE, and EXIT_STATUS systemd gives ExecStopPost, and
    its exit status when it exited. A run that exits with agent_run.py's STOPPED stopped early: its agent ended its
    turn while a task it started still ran, and the run's script has said so."""
    exit_code = int(status) if code == "exited" and (status or "").isdigit() else None
    if result == "timeout":
        return "timed-out", exit_code
    if exit_code == STOPPED:
        return "stopped", exit_code
    if result == "success":
        # A stop, as Cancel does, ends the run with SIGTERM, which systemd counts as a clean exit.
        return ("succeeded" if code == "exited" else "cancelled"), exit_code
    return "failed", exit_code


def journal_tail(unit, lines=50):
    """A file with the last lines the unit logged, for the release to quote."""
    done = subprocess.run(["journalctl", "--user", "-u", unit, "-n", str(lines), "-o", "cat", "--no-pager"],
                          capture_output=True, text=True)
    handle = tempfile.NamedTemporaryFile("w", prefix="agent-desk-run-", suffix=".log", delete=False)
    with handle:
        handle.write(done.stdout if done.returncode == 0 else "")
    return handle.name


def release_issue(issue, ending, unit, environ, discussion=False):
    """Hands an issue back as the agent workflow's release step does. A discussion's release only says how the run
    ended."""
    log = journal_tail(unit)
    try:
        subprocess.run([str(AGENT_DESK), "release", issue, "failure" if ending == "failed" else "cancelled",
                        *(["--discussion"] if discussion else [])], env={**environ, "RUN_LOG": log})
    finally:
        os.unlink(log)


def report_pr(desk, project, pr, kind, unit):
    """Tells a review in the store that the run sent to it was cancelled or timed out, which the run's script never
    reaches. The note carries desk_mark from lib/common.sh, since the store records its author."""
    what = "fix" if kind == "fix" else "agent"
    body = (f"The {what} run was cancelled or timed out (log: `journalctl --user -u {unit}`)."
            f"\n\n<sub>Posted by agent-desk in {unit.removesuffix('.service')}</sub>")
    try:
        desk.comment(project, int(pr), body)
    except store.StoreError as problem:
        print(f"Couldn't say so on #{pr}: {problem}", flush=True)


def stopped(desk, environ, release=release_issue, report=report_pr):
    """
    Records how the unit's run ended. A run that stopped early has posted its own note and keeps its issue. An agent
    run on an issue that failed, was cancelled, or timed out first hands the issue back, while it still holds its
    target, so a run the release starts again waits for it. A discussion of an idea that ended so says how on the
    issue, and leaves its state alone. A cancelled or timed-out run on a PR, as a fix or an agent sent back to a PR
    that works no issue, says so on the PR.
    """
    run = int(environ["AGENT_DESK_RUN"])
    ending, exit_code = outcome(environ.get("SERVICE_RESULT"), environ.get("EXIT_CODE"), environ.get("EXIT_STATUS"))
    row = desk.run(run)
    environ = {key: value for key, value in environ.items() if key != "AGENT_DESK_RUN"}
    try:
        failed = ending in ("failed", "cancelled", "timed-out")
        if row["kind"] == "agent" and row["target"].isdigit() and failed:
            release(row["target"], ending, row["unit"], environ)
        elif row["kind"] == "agent" and row["target"].startswith("idea-") and failed:
            release(row["target"].removeprefix("idea-"), ending, row["unit"], environ, discussion=True)
        elif row["kind"] in ("agent", "fix") and ending in ("cancelled", "timed-out"):
            report(desk, row["project"], row["target"].removeprefix("pr-"), row["kind"], row["unit"])
    finally:
        desk.run_ended(run, ending, exit_code)
    return ending
