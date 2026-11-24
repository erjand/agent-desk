"""Says what every Claude process the desk starts bills, and keeps the Anthropic API key it bills on `api`:

    python3 account.py                       prints both accounts, the subscription's week and the API key's
                                             month, marks the one in use, and says whether a key is set
    python3 account.py api | subscription    switches the account; `api` refuses while no key is set
    python3 account.py key                   reads a key from a prompt that doesn't echo, or from standard input
                                             when it isn't a terminal, and saves it
    python3 account.py key --clear           removes the key, and moves an `api` account back to `subscription`
    python3 account.py claude [<account>]    prints the account a Claude process bills, the setting's or the one
                                             named, and on a second line the JSON its --settings takes, empty on
                                             `subscription`; exits 1, saying why, on `api` without a key
    python3 account.py chat                  prints what `claude` prints for a turn of the web page's Chat, and
                                             exits 1, saying why, while lib/budget.py's API budget holds new runs
    python3 account.py budget [<dollars> | on | off]
                                             shows or sets the monthly API budget, as lib/budget.py describes
    python3 account.py json                  prints what the web page's header reads, as `reading` gives it

`subscription` bills the Claude plan Claude Code is signed in to, and `api` bills the key. One setting covers every
project on the machine, as the weekly usage limit does: lib/hold.py keeps it in hold.json as `account`, and a state
without one reads as `subscription`. The dispatcher reads it when it starts a run's unit, records it on the run's row,
and hands it to the run as AGENT_DESK_ACCOUNT, so a switch changes only the runs that start after it. The web page's
Chat reads it at each turn.

The key lives in KEY, a file of its own with owner-only permissions, in the config folder beside the projects file,
outside the repository, the store, the backup, and hold.json. A Claude process on `api` gets `--settings` with an
`apiKeyHelper` that prints the file, so Claude Code reads the key itself and the key reaches no argument, environment,
unit property, or log. Claude Code bills ANTHROPIC_API_KEY ahead of a helper and of the plan, so lib/common.sh's
desk_claude_run and the Chat drop it from every Claude process they start, on either account. The helper reads the file
whenever Claude Code asks for the key, so clearing the key ends what a run on `api` can bill, though its account
stays `api` until it ends.

Nothing here prints more of the key than its last four characters.
"""

import getpass
import json
import os
import shlex
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hold  # noqa: E402

ACCOUNTS = ("subscription", "api")
DEFAULT = "subscription"
KEY = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-desk" / "anthropic-api-key"
# What each account bills, as the status says it.
BILLS = {"subscription": "the Claude plan Claude Code is signed in to", "api": "the Anthropic API key"}


class AccountError(Exception):
    """Why the key can't be used or saved, in one line that never holds the key."""


def current(state=None):
    """The account hold.json sets, or `subscription` when it sets none."""
    state = hold.status() if state is None else state
    account = state.get("account")
    return account if account in ACCOUNTS else DEFAULT


def read_key(path=None):
    """The key the file holds, stripped, or "" when it is missing, empty, or unreadable."""
    try:
        return (path or KEY).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def ending(key):
    """The key's last four characters, all that any message shows of it."""
    return key[-4:]


def key_problem(path=None):
    """
    Why the key's file can't be billed on `api`, and how to fix it, or None: the file is missing, empty, or readable
    by anyone but its owner.
    """
    path = path or KEY
    again = "agent-desk account key, or bill the plan with: agent-desk account subscription"
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return f"no API key is set in {path}", again
    except OSError as error:
        return f"{path} can't be read: {error.strerror}", again
    if not read_key(path):
        return f"{path} holds no API key", again
    if mode & 0o077:
        return f"{path} has mode {mode:04o}, so other accounts can read the API key", f"chmod 600 {path}"
    return None


def save_key(key, path=None):
    """Writes the key to its file, created with mode 0600, in place of any key there."""
    path = path or KEY
    path.parent.mkdir(parents=True, exist_ok=True)
    new = path.with_name(path.name + ".new")
    fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # A .new file left by an earlier save keeps the mode it was made with, so this sets it again.
        os.fchmod(fd, 0o600)
        os.write(fd, key.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    new.replace(path)


def settings(path=None):
    """The JSON a Claude process's --settings takes on `api`: a helper that prints the key's file."""
    return json.dumps({"apiKeyHelper": f"cat -- {shlex.quote(str(path or KEY))}"})


def claude(account=None, state=None, path=None):
    """
    The account a Claude process bills and the JSON for its --settings, "" on `subscription`. Raises AccountError on
    `api` without a key.
    """
    account = account or current(state)
    if account not in ACCOUNTS:
        raise AccountError(f"{account} isn't an account; name one of {', '.join(ACCOUNTS)}")
    if account == "subscription":
        return account, ""
    if not read_key(path):
        raise AccountError(f"the account is api, but no API key is set in {path or KEY}; set one with: agent-desk "
                        "account key, or switch with: agent-desk account subscription")
    return account, settings(path)


def week(state):
    """The subscription's line: its weekly usage from the last reading lib/usage.py saved, without a fetch."""
    import usage
    data = usage.cached()
    if not isinstance(data.get("percent"), (int, float)) or not isinstance(data.get("read_at"), (int, float)):
        return "Week: no reading yet"
    reset = usage.resets(data)
    return (f"Week {usage.shown(data['percent'])}" + (f", resets {usage.at(reset)}" if reset is not None else "")
            + f" (read {usage.at(data['read_at'])})")


def month(state, desk=None, now=None):
    """The API key's line: the month's spend, against the budget while it is on."""
    import budget
    limit = budget.budget_of(state)
    return "API " + budget.of_budget(budget.spent(desk, now), limit if limit and limit["on"] else None)


def describe(state=None, path=None, desk=None, now=None):
    """
    What `agent-desk account` prints: each account, the subscription's week and the API key's month, with `*` before
    the one in use, which account the desk bills, whether a key is set, and why the budget holds runs, when it does.
    """
    import budget
    state = hold.status() if state is None else state
    chosen = current(state)
    key = read_key(path)
    lines = [f"{'*' if chosen == 'subscription' else ' '} subscription  {week(state)}",
             f"{'*' if chosen == 'api' else ' '} api           {month(state, desk, now)}",
             f"The desk's runs and Chat bill the {chosen} account: {BILLS[chosen]}."]
    if key:
        lines.append(f"An API key ending in {ending(key)} is set in {path or KEY}.")
    else:
        lines.append("No API key is set. Set one with: agent-desk account key")
    spend = budget.over(state, desk, now)
    if spend:
        lines.append(f"The API budget holds new agent, chore, and fix runs and the Chat's turns: {spend}. Raise it "
                     "with: agent-desk account budget <dollars>")
    return "\n".join(lines)


def reading(state=None, path=None, desk=None, now=None):
    """
    What the web page's header reads, as lib/budget.py's summary gives it, with `key`: whether one is set and, when it
    is, its last four characters. Nothing here holds more of the key.
    """
    import budget
    state = hold.status() if state is None else state
    key = read_key(path)
    return {**budget.summary(state, desk, now), "key": {"set": bool(key), "ending": ending(key) if key else None}}


def chat(state=None, path=None):
    """
    The account a Chat turn bills and its --settings JSON, as `claude` gives them. Raises AccountError while the API
    budget holds new runs, as it holds the turn too.
    """
    import budget
    state = hold.status() if state is None else state
    spend = budget.over(state)
    if spend:
        raise AccountError(f"the API budget holds new turns: {spend}; raise it with: agent-desk account budget "
                           "<dollars>, or bill the plan with: agent-desk account subscription")
    return claude(state=state, path=path)


def switch(account, path=None):
    """Sets the account, refusing `api` while no key is set. Returns the exit status."""
    refused = []

    def change(data):
        if account == "api" and not read_key(path):
            refused.append(True)
            return
        data["account"] = account
    hold.update(change)
    if refused:
        print("No API key is set, so the account stays as it was. Set one with: agent-desk account key",
              file=sys.stderr)
        return 1
    print(describe(path=path))
    return 0


def ask_key(stdin=None):
    """The key from a prompt that doesn't echo, or from standard input when it isn't a terminal."""
    stdin = stdin or sys.stdin
    if stdin.isatty():
        return getpass.getpass("Anthropic API key (it won't show as you type): ")
    return stdin.read()


def set_key(stdin=None, path=None):
    """Saves the key `ask_key` reads, refusing an empty one or one with spaces inside. Returns the exit status."""
    key = ask_key(stdin).strip()
    if not key:
        print("The API key is empty, so nothing changed.", file=sys.stderr)
        return 1
    if any(char.isspace() for char in key):
        print("The API key has spaces or line breaks inside it, so nothing changed. Paste the key alone.",
              file=sys.stderr)
        return 1
    save_key(key, path)
    print(f"Saved the API key ending in {ending(key)} in {path or KEY}.")
    if current() == "subscription":
        print("The account is subscription; switch to the key with: agent-desk account api")
    return 0


def clear_key(path=None):
    """Removes the key, and moves an `api` account back to `subscription`, saying so. Returns the exit status."""
    path = path or KEY
    moved = []

    def change(data):
        if current(data) == "api":
            data["account"] = "subscription"
            moved.append(True)
        path.unlink(missing_ok=True)
    had = path.exists()
    hold.update(change)
    print(f"Removed the API key from {path}." if had else "No API key was set.")
    if moved:
        print("The account was api, so it is subscription now: the desk's runs bill the Claude plan.")
    return 0


def main(argv):
    args = argv[1:]
    try:
        if not args:
            print(describe())
            return 0
        if len(args) == 1 and args[0] in ACCOUNTS:
            return switch(args[0])
        if args == ["key"]:
            return set_key()
        if args == ["key", "--clear"]:
            return clear_key()
        if args[0] == "claude" and len(args) <= 2:
            account, flags = claude(args[1] if len(args) == 2 else None)
            print(account)
            print(flags)
            return 0
        if args == ["chat"]:
            account, flags = chat()
            print(account)
            print(flags)
            return 0
        if args == ["json"]:
            print(json.dumps(reading()))
            return 0
        if args[0] == "budget":
            import budget
            return budget.main(["budget.py", *args[1:]])
    except AccountError as problem:
        print(f"agent-desk: {problem}.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nNo key read, so nothing changed.", file=sys.stderr)
        return 1
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
