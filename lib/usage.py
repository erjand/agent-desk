"""Holds new runs that spend Claude tokens while the Claude plan's weekly usage is at or over a limit you set:

    python3 usage.py                prints whether the limit is on, its percentage, and the last reading
    python3 usage.py <0-100>        sets the limit's percentage and turns it on
    python3 usage.py on | off       turns the limit on or off

One limit covers every project on the machine. lib/hold.py keeps it in hold.json as `usageLimit`, `{"on", "percent"}`,
beside the desk's mode, so the web page reads it as it reads the mode. While the limit is on, `check` reads the plan's
7-day usage from the endpoint Claude Code's /usage reads, at most every FETCH_SECONDS, with the sign-in in
~/.claude/.credentials.json, and keeps the reading in usage.json in the state folder, so the dispatcher, the chore
timer, and this command share one fetch. The call spends no tokens. A reading at or over the limit holds the
dispatcher's agent, chore, and fix requests and the chore timer's ticks; check runs spend no tokens and go on.

The endpoint is undocumented, and a fetch also fails on an expired sign-in or offline. The limit fails open: it holds
runs on the last good reading for up to STALE_SECONDS, or until the window that reading counts resets, and then lets
them start, and this command, the dispatcher's log, and `agent-desk doctor` say it can't read usage.
"""

import fcntl
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hold  # noqa: E402

CACHE = hold.STATE / "usage.json"
LOCK = hold.STATE / "usage.lock"
URL = "https://api.anthropic.com/api/oauth/usage"
# How often the limit asks for a reading while it is on, whether the last fetch worked or failed.
FETCH_SECONDS = 300
# How long a reading holds runs when no newer one comes.
STALE_SECONDS = 3600
TIMEOUT_SECONDS = 5


class UsageError(Exception):
    """Why a fetch got no reading. Its text never holds the token."""


def credentials():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json"


def token():
    """Claude Code's OAuth access token."""
    path = credentials()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise UsageError(f"{path} can't be read; sign in to Claude Code") from None
    except ValueError:
        raise UsageError(f"{path} isn't JSON") from None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    found = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(found, str) or not found:
        raise UsageError(f"{path} holds no Claude sign-in; sign in to Claude Code")
    return found


def fetch():
    """The 7-day window's usage as a percentage, and when the window resets, from the usage endpoint."""
    request = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token()}",
                                                  "anthropic-beta": "oauth-2025-04-20",
                                                  "Content-Type": "application/json", "User-Agent": "agent-desk"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raise UsageError(f"the usage endpoint answered HTTP {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise UsageError(f"the usage endpoint can't be reached ({reason})") from None
    except ValueError:
        raise UsageError("the usage endpoint answered with something other than JSON") from None
    week = body.get("seven_day") if isinstance(body, dict) else None
    percent = week.get("utilization") if isinstance(week, dict) else None
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        raise UsageError("the usage endpoint's answer holds no seven_day.utilization")
    resets = week.get("resets_at")
    return {"percent": float(percent), "resets_at": resets if isinstance(resets, str) else None}


def cached():
    """The reading usage.json keeps: `percent`, `resets_at`, `read_at`, `tried_at`, and `error`, or {} for none."""
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def due(data, now):
    """Whether to fetch: FETCH_SECONDS after the last try, or at once when the reading's window has reset since."""
    tried = data.get("tried_at")
    if not isinstance(tried, (int, float)) or not 0 <= now - tried < FETCH_SECONDS:
        return True
    reset = resets(data)
    return reset is not None and tried < reset <= now


def reading(now=None, get=None):
    """
    The newest reading, after a fetch when the last one is FETCH_SECONDS old. A failed fetch keeps the last good
    reading and notes its error. The fetch holds a lock, so a dispatcher and a chore tick that both find it due fetch
    once.
    """
    now = time.time() if now is None else now
    data = cached()
    if not due(data, now):
        return data
    hold.STATE.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        data = cached()
        if not due(data, now):
            return data
        try:
            data = {**(get or fetch)(), "read_at": now, "tried_at": now, "error": None}
        except UsageError as error:
            data = {**data, "tried_at": now, "error": str(error)}
        new = CACHE.with_suffix(".json.new")
        new.write_text(json.dumps(data) + "\n", encoding="utf-8")
        new.replace(CACHE)
    return data


def resets(data):
    """When the reading's window resets, as seconds since the epoch, or None."""
    try:
        return datetime.fromisoformat(data["resets_at"].replace("Z", "+00:00")).timestamp()
    except (KeyError, AttributeError, TypeError, ValueError):
        return None


def at(seconds):
    """A time in the coming week, in local time, such as `Wed 04:00`."""
    return time.strftime("%a %H:%M", time.localtime(seconds))


def fresh(data, now):
    """Whether a reading still counts: under STALE_SECONDS old, from a window that hasn't reset since."""
    read = data.get("read_at")
    if not isinstance(read, (int, float)) or not isinstance(data.get("percent"), (int, float)):
        return False
    reset = resets(data)
    return 0 <= now - read < STALE_SECONDS and (reset is None or now < reset)


def limit_of(state):
    """The limit lib/hold.py's state keeps, as {"on", "percent"}, or None when it was never set."""
    limit = state.get("usageLimit")
    if (isinstance(limit, dict) and isinstance(limit.get("on"), bool) and isinstance(limit.get("percent"), int)
            and 0 <= limit["percent"] <= 100):
        return limit
    return None


def shown(percent):
    """A reading as a whole percentage, rounded down, so a reading under the limit never shows as at it."""
    return f"{math.floor(percent)}%"


def held_note(data, limit):
    reset = resets(data)
    return (f"weekly plan usage is {shown(data['percent'])}, at the {limit['percent']}% limit"
            + (f"; resets {at(reset)}" if reset is not None else ""))


def problem_of(data):
    """Why a reading doesn't count, for a limit that is on."""
    if data.get("error"):
        return data["error"]
    if "read_at" not in data:
        return "no reading yet"
    return "the last reading is out of date"


def check(state, now=None, get=None):
    """
    Why the limit holds a run that spends Claude tokens, or None, and why it can't read usage, or None. Fetches only
    while the limit is on.
    """
    limit = limit_of(state)
    if limit is None or not limit["on"]:
        return None, None
    now = time.time() if now is None else now
    data = reading(now, get)
    if not fresh(data, now):
        return None, problem_of(data)
    if data["percent"] >= limit["percent"]:
        return held_note(data, limit), None
    return None, None


def holds(state, now=None, get=None):
    """Why the limit holds a run that spends Claude tokens, or None."""
    return check(state, now, get)[0]


def describe(state, now=None, get=None):
    """What the command prints: the limit, and the last reading with its time and its window's reset."""
    now = time.time() if now is None else now
    limit = limit_of(state)
    if limit is None:
        lines = ["The weekly usage limit is off. Turn it on with: agent-desk usage-limit <0-100>"]
    elif limit["on"]:
        lines = [f"The weekly usage limit is on at {limit['percent']}%."]
    else:
        lines = [f"The weekly usage limit is off; on, it holds new runs at {limit['percent']}%."]
    reason, problem = check(state, now, get)
    data = cached()
    if isinstance(data.get("percent"), (int, float)) and isinstance(data.get("read_at"), (int, float)):
        reset = resets(data)
        lines.append(f"Weekly plan usage read {shown(data['percent'])} at {at(data['read_at'])}"
                     + (f"; the window resets {at(reset)}." if reset is not None else "."))
    else:
        lines.append("No reading yet.")
    if reason:
        lines.append(f"It holds new agent, chore, and fix runs: {reason}.")
    elif problem:
        lines.append(f"It can't read usage, so runs start: {problem}.")
    return "\n".join(lines)


def percent_of(text):
    """The percentage a command line names, such as `80` or `80%`, or None when it names none."""
    text = text.removesuffix("%")
    return int(text) if re.fullmatch(r"-?[0-9]+", text) else None


def main(argv):
    args = argv[1:]
    if not args:
        print(describe(hold.status()))
        return 0
    if len(args) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    arg = args[0]
    if arg in ("on", "off"):
        if arg == "on" and limit_of(hold.status()) is None:
            print("The weekly usage limit has no percentage yet. Set one with: agent-desk usage-limit <0-100>",
                  file=sys.stderr)
            return 1
        hold.update(lambda data: data["usageLimit"].update(on=arg == "on") if limit_of(data) else None)
    else:
        percent = percent_of(arg)
        if percent is None:
            print(__doc__.strip(), file=sys.stderr)
            return 2
        if not 0 <= percent <= 100:
            print(f"The weekly usage limit is a percentage from 0 to 100, not {percent}.", file=sys.stderr)
            return 1
        hold.update(lambda data: data.update(usageLimit={"on": True, "percent": percent}))
    print(describe(hold.status()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
