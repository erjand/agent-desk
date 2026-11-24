"""Holds new runs that spend Claude tokens while the month's spend on the API key is at or over a budget you set:

    python3 budget.py                        prints whether the budget is on, its amount, and the month's spend
    python3 budget.py <dollars>              sets the monthly budget, such as 200 or 49.50, and turns it on
    python3 budget.py on | off               turns the budget on or off, keeping its amount
    python3 budget.py record <session> <account> <cost>
                                             records a Chat turn: its session, the account it billed, and the
                                             total_cost_usd its `result` event reported

`agent-desk account budget` runs it. One budget covers every project on the machine, as the weekly usage limit does:
lib/hold.py keeps it in hold.json as `apiBudget`, `{"on", "dollars"}`.

The month's spend is what Claude Code reported each run and Chat turn billed to the key, summed over the calendar month
in local time, as `spent` reads it from the desk's own records. A run counts by its row in the store's `runs` table: its
`cost`, when its `account` is `api` and it started this month. lib/run_log.py records that cost from the run's last
`result` event as what its session's total grew by since the session's last run, as a Chat turn counts. A Chat turn has
no row there, so the web page's Chat records each turn's report in LEDGER, one JSON object a line. Claude Code reports a
resumed session's total so far, not the turn's own cost, so `record` keeps each report and counts a turn as what its
session's total grew by since the session's last turn, which it records on either account, so a session that moves to
`api` counts only the turns after the move. The total calls nothing at Anthropic, and so leaves out what Claude Code
never reported: a run cut off at its time limit that no later run takes up, a Chat turn stopped before its reply ended,
and anything else that spends the key.

While lib/account.py's account is `api` and the budget is on, a spend at or over it holds the dispatcher's agent,
chore, and fix requests, the chore timer's ticks, and the Chat's new turns, as `holds` says. Runs at work go on, and
branch checks spend no tokens and start. The hold lifts when the month turns, the budget rises or turns off, or the
account moves to `subscription`.
"""

import fcntl
import json
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import account  # noqa: E402
import hold  # noqa: E402
import store  # noqa: E402
from store_schema import store_time  # noqa: E402

LEDGER = hold.STATE / "chat-spend.jsonl"
LOCK = hold.STATE / "chat-spend.lock"
# What a budget's amount may be on the command line: dollars, with or without a `$` and at most two decimals.
AMOUNT = re.compile(r"\$?([0-9]+(?:\.[0-9]{1,2})?)")


def budget_of(state):
    """The budget lib/hold.py's state keeps, as {"on", "dollars"}, or None when it was never set."""
    budget = state.get("apiBudget")
    if (isinstance(budget, dict) and isinstance(budget.get("on"), bool)
            and isinstance(budget.get("dollars"), (int, float)) and not isinstance(budget["dollars"], bool)
            and math.isfinite(budget["dollars"]) and budget["dollars"] > 0):
        return budget
    return None


def dollars(amount):
    """An amount of dollars as the desk prints it, such as `$63.20`."""
    return f"${amount:,.2f}"


def month(now):
    """The calendar month `now` falls in, in local time, as its first second and the next month's, in epoch seconds."""
    year, number = time.localtime(now)[:2]
    following = (year + 1, 1) if number == 12 else (year, number + 1)
    return (time.mktime((year, number, 1, 0, 0, 0, 0, 0, -1)),
            time.mktime((*following, 1, 0, 0, 0, 0, 0, -1)))


def ledger():
    """The Chat turns LEDGER holds, each a dict with `at`, `session`, `account`, `reported`, and `cost`."""
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    turns = []
    for line in lines:
        try:
            turn = json.loads(line)
        except ValueError:
            continue
        if (isinstance(turn, dict) and isinstance(turn.get("at"), (int, float))
                and isinstance(turn.get("cost"), (int, float))):
            turns.append(turn)
    return turns


def record(session, billed, reported, now=None):
    """
    Adds a Chat turn to LEDGER: the session's total so far, `reported`, and the turn's own cost, what that total grew
    by since the session's last turn. A total smaller than the last, as from a Claude Code that started its count
    afresh, counts whole. Returns the turn's cost.
    """
    now = time.time() if now is None else now
    hold.STATE.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        before = next((turn.get("reported") for turn in reversed(ledger()) if turn.get("session") == session), None)
        grew = isinstance(before, (int, float)) and reported >= before
        cost = reported - before if grew else reported
        with open(LEDGER, "a", encoding="utf-8") as out:
            out.write(json.dumps({"at": now, "session": session, "account": billed, "reported": reported,
                                  "cost": cost}) + "\n")
    return cost


def run_spend(desk, start, end):
    """
    What the runs that billed `api` and started from `start` to before `end` reported they cost. An older database,
    which a read-only store opens unmigrated, has no `account` column, so none of its runs billed `api`.
    """
    if not any(column["name"] == "account" for column in desk.db.execute("PRAGMA table_info(runs)")):
        return 0.0
    row = desk.db.execute("SELECT COALESCE(SUM(cost), 0) FROM runs WHERE account = 'api' AND started_at >= ?"
                          " AND started_at < ?", (store_time(start), store_time(end))).fetchone()
    return float(row[0])


def chat_spend(start, end):
    """What the Chat turns billed to `api` from `start` to before `end` cost."""
    return sum(turn["cost"] for turn in ledger() if turn.get("account") == "api" and start <= turn["at"] < end)


def spent(desk=None, now=None):
    """The month's spend on the API key: its runs' costs and its Chat turns'. Opens the store read-only when no `desk`
    is given."""
    now = time.time() if now is None else now
    start, end = month(now)
    if desk is not None:
        return run_spend(desk, start, end) + chat_spend(start, end)
    opened = store.Store(read_only=True)
    try:
        return run_spend(opened, start, end) + chat_spend(start, end)
    finally:
        opened.close()


def of_budget(amount, budget):
    """The month's spend against the budget, such as `$201.40 of $200.00 this month`, or the spend alone without one."""
    return f"{dollars(amount)} of {dollars(budget['dollars'])} this month" if budget else f"{dollars(amount)} this month"


def over(state, desk=None, now=None):
    """
    The month's spend against the budget, such as `$201.40 of $200.00 this month`, while the budget holds runs and
    Chat turns that spend Claude tokens, or None. Reads the spend only while the account is `api` and the budget is on.
    """
    budget = budget_of(state)
    if budget is None or not budget["on"] or account.current(state) != "api":
        return None
    amount = spent(desk, now)
    return of_budget(amount, budget) if amount >= budget["dollars"] else None


def holds(state, desk=None, now=None):
    """Why the budget holds a run, as a card's wait reads after "waits for", such as `the API budget: $201.40 of
    $200.00 this month`, or None."""
    spend = over(state, desk, now)
    return f"the API budget: {spend}" if spend else None


def summary(state, desk=None, now=None):
    """What the account, the month's spend, and the budget are, as `agent-desk board --json` carries them."""
    budget = budget_of(state)
    amount = spent(desk, now)
    on = budget is not None and budget["on"]
    return {"account": account.current(state), "spent": round(amount, 2),
            "budget": {"on": on, "dollars": budget["dollars"]} if budget else None,
            "held": (on and account.current(state) == "api" and amount >= budget["dollars"])}


def describe(state, desk=None, now=None):
    """What the command prints: the budget, the month's spend, and whether it holds runs."""
    budget = budget_of(state)
    amount = spent(desk, now)
    if budget is None:
        lines = ["The API budget is off. Turn it on with: agent-desk account budget <dollars>"]
    elif budget["on"]:
        lines = [f"The API budget is on at {dollars(budget['dollars'])} a month."]
    else:
        lines = [f"The API budget is off; on, it holds new runs at {dollars(budget['dollars'])} a month."]
    lines.append(f"The API key's spend from the desk's runs and Chat: {dollars(amount)} this month.")
    if budget is not None and budget["on"] and account.current(state) != "api":
        lines.append("The account is subscription, so the budget holds no runs.")
    spend = over(state, desk, now)
    if spend:
        lines.append(f"It holds new agent, chore, and fix runs and the Chat's turns: {spend}.")
    return "\n".join(lines)


def amount_of(text):
    """The dollars a command line names, such as `200`, `49.50`, or `$200`, or None when it names none."""
    match = AMOUNT.fullmatch(text)
    return float(match.group(1)) if match else None


def main(argv):
    args = argv[1:]
    if not args:
        print(describe(hold.status()))
        return 0
    if args[0] == "record" and len(args) == 4:
        try:
            reported = float(args[3])
        except ValueError:
            reported = math.nan
        if not math.isfinite(reported) or reported < 0 or args[2] not in account.ACCOUNTS:
            print("agent-desk: record takes a session, an account, and a cost in dollars.", file=sys.stderr)
            return 2
        record(args[1], args[2], reported)
        return 0
    if len(args) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    arg = args[0]
    if arg in ("on", "off"):
        if arg == "on" and budget_of(hold.status()) is None:
            print("The API budget has no amount yet. Set one with: agent-desk account budget <dollars>",
                  file=sys.stderr)
            return 1
        hold.update(lambda data: data["apiBudget"].update(on=arg == "on") if budget_of(data) else None)
    else:
        amount = amount_of(arg)
        if amount is None or amount <= 0:
            print(f"The API budget is a positive number of dollars, such as 200 or 49.50, not {arg}.",
                  file=sys.stderr)
            return 1
        hold.update(lambda data: data.update(apiBudget={"on": True, "dollars": amount}))
    print(describe(hold.status()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
