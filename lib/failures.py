"""Prints what failed in a local branch check's log, the lines that say why, for the agent that fixes the branch and
for the board:

    python3 failures.py excerpt <n>              prints what failed in each failed local check of review n's head, for
                                                 lib/fix.sh
    python3 failures.py log <file>               prints what failed in a local branch check's log, as lib/checks.py
                                                 writes it

An excerpt quotes the log lines that say what failed, each but a compiler's with the lines after it, which hold a failed
test's expected and actual values.
"""

import re
import sys
from pathlib import Path

import review_checks
import store

# How many lines follow each failing line, and how many an excerpt quotes in all.
CONTEXT = 4
MAX_LINES = 60
# Log lines that say what failed; a count of no failures or errors, as a passing test summary or build prints; and the
# lines a recipe runner prints about a failed step, which don't say why.
FAILURE_LINE = re.compile(r"\berror\b|exception|panic|traceback|\bfail(s|ed|ure)?\b|\bassert", re.I)
PASSING_LINE = re.compile(r"\bfail(ed|ures?)?:?\s+0\b|\b0\s+(errors?\b|error\(s\)|failures?\b|failed\b)", re.I)
RUNNER_LINE = re.compile(r"^error: recipe `[^`]*` failed", re.I)
# A compiler's error at a file's line and column, which the lines after it, from a parallel build, don't explain.
COMPILER_LINE = re.compile(r"[(:]\d+[,:]\d+\)?:\s*(error|warning)\b", re.I)
# A warning, such as Python's `ResourceWarning: …`, a tool's `warning: …`, or a compiler's at a file's line and column,
# and the tracemalloc hint Python prints after an unclosed resource's warning. A failure word after it doesn't count.
WARNING = re.compile(r"\b\w*warning:|[(:]\d+[,:]\d+\)?:\s*warning\b|\benable tracemalloc\b", re.I)
# A path or a file name, such as `Content/Audio/UI/error.wav` or `exception_handler.py`, which a failure word inside
# doesn't make a failure line.
PATH = re.compile(r"\S*[/\\]\S*|[\w.-]*\.[a-z0-9]{1,4}")
TOKEN_END = ":,;)]}'\"`"
# A local check's scratch worktree, and the lines lib/checks.py writes around each step's output.
SCRATCH = re.compile(r"[^\s\"'(]*/agent-desk/checks/[^/\s]+/")
CHECK_LINE = re.compile(r"^== |^Check \d+ of |^The check \w+\.$")


def clean_local(line):
    """A local check's log line without the check's own step lines and its scratch worktree's path."""
    return "" if CHECK_LINE.match(line) else SCRATCH.sub("", line).rstrip()


def in_path(line, match):
    """Whether a failure word FAILURE_LINE matched lies in a path or a file name, the line's word around it."""
    start = max(line.rfind(" ", 0, match.start()), line.rfind("\t", 0, match.start())) + 1
    word = line[start:].split(None, 1)[0].lstrip("\"'`([{").rstrip(TOKEN_END)
    return bool(PATH.fullmatch(word))


def says_what_failed(line):
    """
    Whether a log line says what failed: it holds a failure word outside a path or a file name, and before any
    warning.
    """
    warning = WARNING.search(line)
    head = line[:warning.start()] if warning else line
    return any(not in_path(line, match) for match in FAILURE_LINE.finditer(head))


def failure_excerpt(log, context=CONTEXT, limit=MAX_LINES):
    """
    The log lines that say what failed, in the log's order, each but a compiler's error with the `context` lines after
    it, and how many more such lines the excerpt leaves out past `limit`. When no line says what failed, the last line
    before each of the recipe runner's lines about a failed step does, since that step's output ends with why it
    failed; a log without those lines gives its last lines.
    """
    lines, before_runner = [], set()
    for one in map(clean_local, log.splitlines()):
        if not one.strip() or PASSING_LINE.search(one):
            continue
        if RUNNER_LINE.match(one.strip()):
            if lines:
                before_runner.add(len(lines) - 1)
            continue
        lines.append(one)
    failing = [index for index, line in enumerate(lines) if says_what_failed(line)] or sorted(before_runner)
    keep = set()
    for index in failing:
        after = 0 if COMPILER_LINE.search(lines[index]) else context
        keep.update(range(index, min(index + after + 1, len(lines))))
    chosen = [lines[index] for index in sorted(keep)] if keep else lines[-limit:]
    seen, excerpt = set(), []
    for line in chosen:
        if line not in seen:
            seen.add(line)
            excerpt.append(line)
    return excerpt[:limit], max(0, len(excerpt) - limit)


def log_excerpt(path, context=CONTEXT, limit=MAX_LINES):
    """failure_excerpt of a local check's log file, as lib/checks.py writes it."""
    return failure_excerpt(Path(path).read_text(encoding="utf-8", errors="replace"), context, limit)


# How a brief says each way a check can fail ended.
ENDED = {"failed": "failed", "timed-out": "timed out", "lost": "was lost"}


def brief(check, excerpt, more):
    """
    A failed local check's name, how it ended, its log's path, and the log lines that say what failed, as lib/fix.sh
    tells its agent. `check` is a row of the store's `checks`.
    """
    log = check.get("log_path")
    ended = ENDED.get(check["status"], check["status"])
    if not log:
        return f"{check['name']} {ended} before it wrote a log."
    quoted = "\n".join(excerpt) or "The check's log shows no lines."
    tail = f"\n({more} more lines in {log}.)" if more else ""
    return f"{check['name']} {ended}: {log}\n\n{quoted}{tail}"


def failed_checks(desk, repo, number, checkout):
    """
    The newest check of each name on review `number`'s head that failed, timed out, or was lost, as rows of the store's
    `checks`. The head is the commit the review's branch points at in the main checkout, as lib/review_checks.py finds
    it.
    """
    _, head = review_checks.head_of(desk, repo, number, checkout)
    return [row for row in desk.head_checks(repo, head) if row["status"] in review_checks.FAILED] if head else []


def check_brief(check):
    """brief of a failed check, quoting no lines when its log is gone."""
    try:
        excerpt, more = log_excerpt(check["log_path"]) if check.get("log_path") else ([], 0)
    except OSError:
        excerpt, more = [], 0
    return brief(check, excerpt, more)


def excerpt_review(desk, repo, number, checkout):
    """The briefs of each failed check on review `number`'s head, read from the local store and git."""
    return "\n\n".join(check_brief(check) for check in failed_checks(desk, repo, number, checkout))


def main(argv):
    if len(argv) != 3 or argv[1] not in ("excerpt", "log") or (argv[1] == "excerpt") != argv[2].isdigit():
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if argv[1] == "log":
        excerpt, more = log_excerpt(argv[2])
        print("\n".join(excerpt + ([f"({more} more lines in {argv[2]}.)"] if more else [])))
        return 0
    try:
        repo = store.repo_here()
        desk = store.Store()
        try:
            text = excerpt_review(desk, repo, int(argv[2]), store.checkout_of(repo) or ".")
        finally:
            desk.close()
    except store.StoreError as error:
        print(f"agent-desk: {error}", file=sys.stderr)
        return 1
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
