"""Sorts a project's start order into its default: the issues that can start now, then the ones that wait.

lib/store.py's `Store.sort_order` calls `sorted_order` with the store's items, and `agent-desk queue order --sort` runs
it. The order has two tiers, one after the other:

1. Startable: an issue with no blocker still open and, for each of its serial groups, no other issue in progress
   holding the group and no issue earlier in this tier in the same group. These sort by number.
2. Gated: every other issue, sorted so each comes after every queued or waiting issue that blocks it, and by number
   where blockers don't decide. The members of a cycle of blockers sort by number among themselves.
"""

import heapq

from serial_slots import groups, holder, names


def sorted_order(queue, blockers, open_issues):
    """
    The numbers of the `queue`, a project's queued and waiting issues as the store lists them with their labels, in
    sorted order. `blockers` maps an issue's number to the numbers of its blockers that aren't closed, and
    `open_issues` holds the project's open issues with their `status` and labels, as lib/serial_slots.py reads them.
    """
    startable, gated, taken = [], [], set()
    for issue in sorted(queue, key=lambda one: one["number"]):
        number, wanted = issue["number"], groups(names(issue))
        if blockers.get(number) or wanted & taken or (wanted and holder(number, names(issue), open_issues)):
            gated.append(number)
            continue
        taken |= wanted
        startable.append(number)
    return startable + after_blockers(gated, blockers)


def after_blockers(numbers, blockers):
    """
    The `numbers` ordered so each comes after those of them that block it, by number where blockers don't decide. The
    members of a cycle of blockers stay together, by number.
    """
    inside = set(numbers)
    blocked_by = {number: set(blockers.get(number, ())) & inside for number in numbers}
    reach = {number: reachable(number, blocked_by) for number in numbers}
    # A cycle is the set of issues that each block one another, through any chain; an issue outside one is its own.
    cycle = {number: frozenset(one for one in reach[number] if number in reach[one]) | {number} for number in numbers}
    waits = {members: {cycle[blocker] for member in members for blocker in blocked_by[member]} - {members}
             for members in set(cycle.values())}
    ready = [(min(members), members) for members, ahead in waits.items() if not ahead]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, members = heapq.heappop(ready)
        ordered += sorted(members)
        for later, ahead in waits.items():
            if members in ahead:
                ahead.discard(members)
                if not ahead:
                    heapq.heappush(ready, (min(later), later))
    return ordered


def reachable(number, blocked_by):
    """Every issue that blocks `number` through a chain of one or more blockers."""
    seen, stack = set(), list(blocked_by[number])
    while stack:
        one = stack.pop()
        if one not in seen:
            seen.add(one)
            stack.extend(blocked_by[one])
    return seen
