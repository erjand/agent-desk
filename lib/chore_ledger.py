"""Keeps what each chore's runs passed over and left for later, so the next run starts where the last ones stopped.

    python3 chore_ledger.py brief <repo> <skill> <main>                    prints what the next run should know
    python3 chore_ledger.py record <repo> <skill> <commit> <stream> [pr]   adds a finished run's report

`brief` reads the chore's reviews from the local store (open, merged lately, and closed without merging), the files
each changed from git in the main checkout, and the ledger's files passed over and leads left for later, and prints
them for run-chore.sh to add to the agent's prompt, with how to report. `record` takes the report the agent ends its
final message with from the run's stream-json and adds it to the ledger,
~/.local/state/agent-desk/chores/<owner>__<repo>/<skill>.jsonl.

Both ends stay small however long a chore runs: the ledger keeps the newest KEEP_RUNS runs of the last KEEP_DAYS, and
the brief keeps to MAX_PATHS files and MAX_LEADS leads a list and MAX_BRIEF characters in all, about 1,500 tokens.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import review_diff
import store

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "agent-desk" / "chores"
KEEP_DAYS = 90
KEEP_RUNS = 200
# How far back each part of the brief looks.
MERGED_DAYS = 30
CLOSED_DAYS = 90
PASSED_DAYS = 60
LEAD_DAYS = 30
MAX_PATHS = 40
MAX_LEADS = 10
MAX_BRIEF = 6000
DAY = 86400
# How many of the chore's newest reviews the brief reads.
MAX_REVIEWS = 100

REPORT = """When you finish, end your final message with a fenced block tagged `chore-report` holding JSON, which
the next runs of this chore read:

```chore-report
{"passed": [{"path": "src/a.py", "why": "its comments already explain the cache rules"}],
 "leads": [{"note": "Constants.cs has unused texture keys worth removing", "paths": ["src/Core/Constants.cs"]}],
 "settled": ["Loader.cs retries a failed read forever"]}
```

`passed` lists the files you examined closely and chose not to change, and `leads` what you found worth doing in a
later run. `settled` copies, word for word and without its dash, each lead from the list above that you filed as an
issue or found no longer holds, so later runs stop seeing it. Keep each `why` and `note` to one line, and leave a list
empty when you have nothing for it."""


def ledger(repo, skill):
    return STATE / store.project_key(repo) / f"{skill}.jsonl"


def entries(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    runs = []
    for line in lines:
        try:
            runs.append(json.loads(line))
        except ValueError:
            continue
    return runs


def report_of(stream_text):
    """The `chore-report` JSON the agent ended its final message with, from the run's stream-json, or None."""
    final = ""
    for line in stream_text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            final = event["result"]
        elif event.get("type") == "assistant":
            for part in event.get("message", {}).get("content", []):
                if part.get("type") == "text":
                    final = part.get("text", final)
    blocks = re.findall(r"```chore-report\s*\n(.*?)```", final, re.S)
    if not blocks:
        return None
    try:
        report = json.loads(blocks[-1])
    except ValueError:
        return None
    return report if isinstance(report, dict) else None


def one_line(text):
    return " ".join(str(text).split())[:200]


def clean(report):
    """The report's lists, kept to well-formed one-line entries and to sizes the ledger can hold."""
    passed = [{"path": str(item["path"]), "why": one_line(item.get("why", ""))}
              for item in (report or {}).get("passed", []) if isinstance(item, dict) and item.get("path")]
    leads = [{"note": one_line(item["note"]), "paths": [str(path) for path in item.get("paths", [])][:10]}
             for item in (report or {}).get("leads", []) if isinstance(item, dict) and item.get("note")]
    settled = [one_line(note) for note in (report or {}).get("settled", []) if isinstance(note, str) and note.strip()]
    return passed[:100], leads[:20], settled[:20]


def record(path, commit, report, pr, now):
    """Adds a run to the ledger, which keeps the newest KEEP_RUNS runs of the last KEEP_DAYS."""
    passed, leads, settled = clean(report)
    run = {"at": now, "commit": commit, "pr": pr, "passed": passed, "leads": leads, "settled": settled}
    kept = [old for old in entries(path) if now - old.get("at", 0) < KEEP_DAYS * DAY] + [run]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(one) + "\n" for one in kept[-KEEP_RUNS:]), encoding="utf-8")


def stamp(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() if text else 0


def capped(items, limit):
    shown = items[:limit]
    return shown + ([f"and {len(items) - limit} more"] if len(items) > limit else [])


def brief(prs, runs, changed, prefix, now):
    """
    What the next run should know, as Markdown: the chore's open reviews, files its reviews changed lately, its reviews
    the user closed without merging, files earlier runs passed over that haven't changed since, and their leads, less
    those a newer run settled. `prs` are the reviews as `reviews` gives them, and `changed` tells which files changed on main since a commit.
    """
    mine = [pr for pr in prs if pr.get("headRefName", "").startswith(prefix)]
    files = lambda pr: [item["path"] for item in pr.get("files", [])]
    open_prs = [f"#{pr['number']} {pr['title']}: {', '.join(files(pr))}" for pr in mine if pr["state"] == "OPEN"]
    merged = sorted({path for pr in mine if pr["state"] == "MERGED"
                     and now - stamp(pr.get("mergedAt")) < MERGED_DAYS * DAY for path in files(pr)})
    closed = [f"#{pr['number']} {pr['title']}: {', '.join(files(pr))}" for pr in mine if pr["state"] == "CLOSED"
              and now - stamp(pr.get("closedAt")) < CLOSED_DAYS * DAY]
    passed, leads, seen, settled = [], [], set(), set()
    for run in reversed(runs):
        age = now - run.get("at", 0)
        moved = changed(run.get("commit")) if age < PASSED_DAYS * DAY else set()
        for item in run.get("passed", []) if age < PASSED_DAYS * DAY else []:
            if item["path"] not in seen and item["path"] not in moved and item["path"] not in merged:
                seen.add(item["path"])
                passed.append(f"{item['path']}: {item['why']}" if item["why"] else item["path"])
        for lead in run.get("leads", []) if age < LEAD_DAYS * DAY else []:
            done = lead["paths"] and all(path in moved or path in merged for path in lead["paths"])
            if not done and lead["note"] not in leads and one_line(lead["note"]) not in settled:
                leads.append(lead["note"])
        settled.update(one_line(note) for note in run.get("settled", []))
    parts = []
    if leads:
        parts.append("Earlier runs left these leads. Take one up first if it still holds:\n"
                     + "\n".join(f"- {line}" for line in capped(leads, MAX_LEADS)))
    if open_prs:
        parts.append("Earlier runs have these reviews open. Pick a change that touches none of their files, so each "
                     "review stands alone:\n" + "\n".join(f"- {line}" for line in capped(open_prs, MAX_PATHS)))
    if merged:
        parts.append(f"Reviews from this chore merged in the last {MERGED_DAYS} days changed these files. Leave them "
                     "unless you find something new:\n" + "\n".join(f"- {line}" for line in capped(merged, MAX_PATHS)))
    if closed:
        parts.append("The user closed these reviews from this chore without merging them. Don't propose the same "
                     "changes again:\n" + "\n".join(f"- {line}" for line in capped(closed, MAX_PATHS)))
    if passed:
        parts.append("Earlier runs examined these files and found nothing to change, and they haven't changed since. "
                     "Skip them:\n" + "\n".join(f"- {line}" for line in capped(passed, MAX_PATHS)))
    text = "\n\n".join(parts)
    if len(text) > MAX_BRIEF:
        text = text[:MAX_BRIEF].rsplit("\n", 1)[0] + "\n- and more, left out for length"
    return (text + "\n\n" if text else "") + REPORT


def git_changed(main):
    """Which files changed on the main checkout since a commit; nothing for a commit it doesn't know."""
    def changed(commit):
        if not commit:
            return set()
        out = subprocess.run(["git", "-C", main, "diff", "--name-only", commit, "HEAD"], capture_output=True,
                             text=True)
        return set(out.stdout.split()) if out.returncode == 0 else set()
    return changed


def git_files(main):
    """
    Which files a review changed, from the main checkout's git, over the span `agent-desk review diff` reads from
    review_diff. A review whose commits or branch git doesn't know changed nothing.
    """
    def files(review):
        try:
            spans = review_diff.span(main, review)
        except store.StoreError:
            return []
        out = subprocess.run(["git", "-C", main, "diff", "--name-only", *spans, "--"], capture_output=True, text=True)
        return out.stdout.split() if out.returncode == 0 else []
    return files


def reviews(desk, repo, prefix, files_of):
    """
    The project's newest MAX_REVIEWS reviews of a branch starting with `prefix`, open, merged, and closed, from the
    local store, each with its state, when it merged or closed, and `files_of(row)` as its changed files, where `row`
    is the review's row as `Store.review` returns it.
    """
    found = []
    for review in desk.items(repo, "review", state="all", head_prefix=prefix, limit=MAX_REVIEWS):
        merged = review["state"] == "MERGED"
        files = files_of(desk.review(repo, review["number"]))
        found.append({"number": review["number"], "title": review["title"], "state": review["state"],
                      "headRefName": review["headRefName"], "mergedAt": review["closedAt"] if merged else None,
                      "closedAt": review["closedAt"], "files": [{"path": path} for path in files]})
    return found


def main(argv):
    now = time.time()
    if len(argv) == 5 and argv[1] == "brief":
        _, _, repo, skill, main_path = argv
        prefix = f"agent/chore-{skill}-"
        desk = store.Store()
        try:
            prs = reviews(desk, repo, prefix, git_files(main_path))
        finally:
            desk.close()
        print(brief(prs, entries(ledger(repo, skill)), git_changed(main_path), prefix, now))
    elif len(argv) in (6, 7) and argv[1] == "record":
        _, _, repo, skill, commit, stream = argv[:6]
        try:
            text = Path(stream).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        pr = int(argv[6]) if len(argv) == 7 and argv[6].isdigit() else None
        record(ledger(repo, skill), commit, report_of(text), pr, now)
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
