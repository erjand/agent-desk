// What `agent-desk board --json` prints for the store tests/test_board.py seeds, which board.test.ts builds the
// board from. tests/test_board.py fails when the command's output differs from it; to change it, run
// AGENT_DESK_GOLDEN=write python3 -B -m unittest tests.test_board, then make board.test.ts pass again.
import type { StoreBoard } from './board.ts'

export const GOLDEN: StoreBoard = {
  "repo": "org/game",
  "issues": [
    {
      "number": 1,
      "title": "Add a map",
      "status": "open",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": []
    },
    {
      "number": 2,
      "title": "Picked for Manual",
      "status": "queued",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "position": 1
    },
    {
      "number": 3,
      "title": "Waits on its blocker",
      "status": "waiting",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [
        {
          "number": 1,
          "state": "OPEN"
        }
      ],
      "position": 2
    },
    {
      "number": 4,
      "title": "Waits on its serial group",
      "status": "waiting",
      "labels": [
        "serial:web"
      ],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "position": 3
    },
    {
      "number": 5,
      "title": "Holds the serial slot",
      "status": "in-progress",
      "labels": [
        "serial:web"
      ],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [
        {
          "body": "An agent started on `agent/issue-5`.",
          "createdAt": "2026-11-10T09:00:00Z"
        }
      ],
      "landed": null
    },
    {
      "number": 6,
      "title": "Pick the colors",
      "status": "needs-design",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": []
    },
    {
      "number": 7,
      "title": "Parked",
      "status": "on-hold",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": []
    },
    {
      "number": 8,
      "title": "Maybe later",
      "status": "open",
      "labels": [
        "idea"
      ],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": []
    },
    {
      "number": 9,
      "title": "Being worked",
      "status": "in-progress",
      "labels": [
        "benchmark"
      ],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [],
      "landed": null
    },
    {
      "number": 10,
      "title": "Has a passing review",
      "status": "in-progress",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [],
      "landed": null
    },
    {
      "number": 12,
      "title": "Has a failing review",
      "status": "in-progress",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [],
      "landed": null
    },
    {
      "number": 14,
      "title": "Has a conflicting review",
      "status": "in-progress",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [],
      "landed": null
    },
    {
      "number": 16,
      "title": "Has a merging review",
      "status": "in-progress",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [],
      "landed": null
    },
    {
      "number": 18,
      "title": "Has a draft review",
      "status": "in-progress",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [
        {
          "body": "An agent started on `agent/issue-18`.",
          "createdAt": "2026-11-10T09:00:00Z"
        },
        {
          "body": "The agent finished: https://example.test/19 (draft).",
          "createdAt": "2026-11-10T09:00:00Z"
        }
      ],
      "landed": null
    },
    {
      "number": 20,
      "title": "Has a merged review",
      "status": "in-progress",
      "labels": [],
      "createdAt": "2026-11-10T09:00:00Z",
      "updatedAt": "2026-11-10T09:00:00Z",
      "blockedBy": [],
      "comments": [],
      "landed": {
        "base": "main",
        "basePr": null
      }
    }
  ],
  "reviews": [
    {
      "number": 11,
      "title": "The passing review",
      "branch": "agent/issue-10",
      "base": "main",
      "head": "passing-head",
      "draft": false,
      "createdAt": "2026-11-10T09:00:00Z",
      "checks": [
        {
          "name": "test",
          "status": "passed",
          "attempt": 1,
          "log": null,
          "startedAt": null,
          "endedAt": "2026-11-10T09:00:00Z"
        }
      ],
      "mergeable": "MERGEABLE"
    },
    {
      "number": 13,
      "title": "The failing review",
      "branch": "agent/issue-12",
      "base": "main",
      "head": "failing-head",
      "draft": false,
      "createdAt": "2026-11-10T09:00:00Z",
      "checks": [
        {
          "name": "test",
          "status": "failed",
          "attempt": 1,
          "log": "/state/checks/org__game/9.log",
          "startedAt": "2026-11-10T09:00:00Z",
          "endedAt": "2026-11-10T09:00:00Z"
        }
      ],
      "mergeable": "MERGEABLE"
    },
    {
      "number": 15,
      "title": "The conflicting review",
      "branch": "agent/issue-14",
      "base": "main",
      "head": "conflicting-head",
      "draft": false,
      "createdAt": "2026-11-10T09:00:00Z",
      "checks": [],
      "mergeable": "CONFLICTING"
    },
    {
      "number": 17,
      "title": "The merging review",
      "branch": "agent/issue-16",
      "base": "main",
      "head": "merging-head",
      "draft": false,
      "createdAt": "2026-11-10T09:00:00Z",
      "checks": [
        {
          "name": "test",
          "status": "passed",
          "attempt": 1,
          "log": null,
          "startedAt": null,
          "endedAt": "2026-11-10T09:00:00Z"
        }
      ],
      "mergeable": "MERGEABLE"
    },
    {
      "number": 19,
      "title": "The draft review",
      "branch": "agent/issue-18",
      "base": "main",
      "head": "draft-head",
      "draft": true,
      "createdAt": "2026-11-10T09:00:00Z",
      "checks": [
        {
          "name": "test",
          "status": "passed",
          "attempt": 1,
          "log": null,
          "startedAt": null,
          "endedAt": "2026-11-10T09:00:00Z"
        }
      ],
      "mergeable": "MERGEABLE"
    }
  ],
  "merged": [
    {
      "number": 21,
      "title": "The merged review",
      "branch": "agent/issue-20",
      "base": "main",
      "mergedAt": "2026-11-10T10:30:00Z"
    }
  ],
  "runs": [
    {
      "id": 1,
      "kind": "agent",
      "target": "9",
      "status": "running",
      "unit": "agent-desk-agent-org-game-9-1.service",
      "started_at": "2026-11-10T11:00:00Z",
      "ended_at": null,
      "benchmark": false,
      "labelled": false
    },
    {
      "id": 2,
      "kind": "agent",
      "target": "5",
      "status": "failed",
      "unit": "u.service",
      "started_at": "2026-11-10T11:00:00Z",
      "ended_at": "2026-11-10T11:30:00Z",
      "benchmark": false,
      "labelled": false
    }
  ],
  "asked": [
    15
  ],
  "slots": {
    "agent": 3,
    "chore": 1,
    "fix": 1,
    "check": 2,
    "benchmark": 2
  },
  "checks": [
    {
      "branch": "agent/issue-12",
      "review": 13,
      "head": "failing-head",
      "startedAt": "2026-11-10T10:45:00Z"
    }
  ],
  "waitingChecks": [],
  "queue": {
    "queue": [
      {
        "pr": 17,
        "note": "next to merge"
      }
    ],
    "dropped": []
  },
  "fixes": [
    {
      "pr": 15,
      "verdict": "auto-fix on Merge"
    }
  ],
  "hold": {
    "state": "manual",
    "since": null,
    "before": null,
    "picked": [
      "org/game#2"
    ],
    "pickedRuns": [],
    "projects": {}
  },
  "chores": [
    "Chore          Every  Last run                     Next",
    "audit          7d     done Mon Nov 9 08:41         Mon Nov 16 08:41"
  ],
  "backup": {
    "at": 1793361000,
    "committedAt": 1793361000,
    "pushedAt": 1793358600,
    "remote": "origin",
    "waiting": true
  },
  "at": "12:00 +0000",
  "machine": "desk-1",
  "web": "http://127.0.0.1:7480"
}
