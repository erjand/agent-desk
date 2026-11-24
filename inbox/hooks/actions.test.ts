import { expect, test } from 'claude-code/testing'

import { START_AGENT, askToFix, cancelRequest, checksPassed, closeIssue, closeReview, fixBody, labelBacklog,
  labelReady, listOrderFailure, listSortFailure, markDesigned, markReady, moveBacklogToOpen, moveIdeaToOpen,
  moveInListOrder, pauseIssue, restartIssue, retryRun, sortListOrder, startChore, unblockIssue, unpauseIssue,
  unpickFailure, unpickIssue, unqueueFailure, unqueueIssue } from './actions.ts'
import type { Host } from './board.ts'
import { DESK, DISPATCH, FAILURES, HOLD } from './run.ts'

// How every action runs the agent-desk command, with its arguments after these.
const AGENT_DESK = ['bash', '-c', `exec "${DESK}" "$@"`, '_']

/** A host that records each command with its input, and answers each with `answer`. */
function recording(answer: (argv: string[]) => string | null = () => '') {
  const asked: { argv: string[]; stdin?: string }[] = []
  const host = { run: async (argv: string[], _cwd?: string, stdin?: string) => {
    asked.push({ argv, ...(stdin === undefined ? {} : { stdin }) })
    return answer(argv)
  } } as unknown as Host
  return { host, asked, desk: () => asked.filter(one => one.argv[2] === AGENT_DESK[2]).map(one => one.argv.slice(4)) }
}

test('Close closes as completed in the store, and the comment box can close as not planned with a comment in the '
  + 'same write, or as a duplicate of another issue', async () => {
  const { host, asked, desk } = recording()
  expect(await closeIssue(host, {}, 64)).toBe(true)
  expect(await closeIssue(host, {}, 70, 'not planned', 'Audits should skip generated files.')).toBe(true)
  expect(desk()).toEqual([['issue', 'close', '64', '--reason', 'completed'],
    ['issue', 'close', '70', '--reason', 'not-planned', '--comment-file', '-']])
  expect(asked[1]?.stdin).toBe('Audits should skip generated files.')
  const duplicate = recording()
  expect(await closeIssue(duplicate.host, {}, 71, { duplicateOf: 64 })).toBe(true)
  expect(duplicate.desk()).toEqual([['issue', 'close', '71', '--duplicate-of', '64']])
  expect(await closeIssue(recording(() => null).host, {}, 64)).toBe(false)
  // A close the store refused posts no comment of its own.
  const refused = recording(() => null)
  expect(await closeIssue(refused.host, {}, 70, 'completed', 'Done.')).toBe(false)
  expect(refused.desk()).toEqual([['issue', 'close', '70', '--reason', 'completed', '--comment-file', '-']])
})

test('Designed takes needs-design off an issue in the store, and says when agent-desk refuses', async () => {
  const { host, desk } = recording()
  expect(await markDesigned(host, {}, 19)).toBe(true)
  expect(desk()).toEqual([['issue', 'edit', '19', '--remove-label', 'needs-design']])
  expect(await markDesigned(recording(() => null).host, {}, 19)).toBe(false)
})

test('Move to Open takes idea off an issue in the store, and says when agent-desk refuses', async () => {
  const { host, desk } = recording()
  expect(await moveIdeaToOpen(host, {}, 70)).toBe(true)
  expect(desk()).toEqual([['issue', 'edit', '70', '--remove-label', 'idea']])
  expect(await moveIdeaToOpen(recording(() => null).host, {}, 70)).toBe(false)
})

test('Move to Open takes backlog off an issue, Move to Backlog labels one backlog, and each says when agent-desk '
  + 'refuses', async () => {
  const { host, desk } = recording()
  expect(await moveBacklogToOpen(host, {}, 72)).toBe(true)
  expect(await labelBacklog(host, {}, 64)).toBe(true)
  expect(desk()).toEqual([['issue', 'edit', '72', '--remove-label', 'backlog'],
    ['issue', 'edit', '64', '--add-label', 'backlog']])
  expect(await moveBacklogToOpen(recording(() => null).host, {}, 72)).toBe(false)
  expect(await labelBacklog(recording(() => null).host, {}, 64)).toBe(false)
})

test('Ready marks a draft review ready and Close closes a review, in the store', async () => {
  const { host, desk } = recording()
  expect(await markReady(host, {}, 45)).toBe(true)
  expect(await closeReview(host, {}, 46)).toBe(true)
  expect(desk()).toEqual([['review', 'ready', '45'], ['review', 'close', '46']])
  expect(await markReady(recording(() => null).host, {}, 45)).toBe(false)
  expect(await closeReview(recording(() => null).host, {}, 46)).toBe(false)
})

test('Agent-Ready labels the issue in the store and asks the dispatcher for its run without picking it', async () => {
  const { host, asked, desk } = recording()
  expect(await labelReady(host, {}, 12)).toBe(true)
  expect(await labelReady(host, {}, 13, ['benchmark', 'bug'])).toBe(true)
  // Start alone picks, so in Manual the run waits for it.
  expect(asked.some(one => one.argv.join(' ').includes(HOLD))).toBe(false)
  expect(desk()).toEqual([['issue', 'edit', '12', '--add-label', 'agent-ready'],
    ['request', 'agent', '12', '--env', 'EVENT=issues'],
    ['issue', 'edit', '13', '--add-label', 'agent-ready'],
    ['request', 'agent', '13', '--env', 'EVENT=issues', '--benchmark']])
  // A label the store refused asks for no run.
  const refused = recording(argv => (argv.includes('edit') ? null : ''))
  expect(await labelReady(refused.host, {}, 12)).toBe(false)
  expect(refused.desk().map(args => args[0])).toEqual(['issue'])
  expect(await labelReady(recording(argv => (argv.includes('request') ? null : '')).host, {}, 12)).toBe(false)
})

test('a PR\'s checks pass when its head\'s check passed, and not while one waits, runs, or failed', () => {
  expect(checksPassed([{ name: 'test', status: 'passed', attempt: 2 }])).toBe(true)
  expect(checksPassed([{ name: 'test', status: 'queued', attempt: 2 }])).toBe(false)
  expect(checksPassed([{ name: 'test', status: 'running' }])).toBe(false)
  expect(checksPassed([{ name: 'merge', status: 'failed' }])).toBe(false)
  expect(checksPassed([])).toBe(false)
  expect(checksPassed(undefined)).toBe(false)
})

test('Fix reads the PR\'s checks from the store and quotes what lib/failures.py picks from the failed one\'s log',
  async () => {
  const log = '/home/me/.local/state/agent-desk/checks/o__r/7.log'
  const { host, asked, desk } = recording(argv => {
    if (argv[2] === `exec "${DESK}" review view "$1" --json mergeable,checks`) {
      return JSON.stringify({ mergeable: 'CONFLICTING', checks: [{ name: 'test', status: 'failed', log }] })
    }
    return argv[2] === `exec python3 -B ${FAILURES} log "$1"` && argv[4] === log
      ? 'Game/Sprite.cs(12,5): error CS0103\n(3 more lines in the log.)\n' : ''
  })
  expect(await askToFix(host, {}, 81)).toBe(true)
  // The comment goes to the agent through the store's flag, so its text needs no @agent.
  expect(desk()).toEqual([['review', 'comment', '81', '--to-agent', '--body-file', '-']])
  const body = asked.at(-1)?.stdin
  expect(body).toMatch(/^This PR's test checks fail:\n\n```\nGame\/Sprite.cs\(12,5\): error CS0103\n/)
  expect(body).toContain('It also has merge conflicts with main. Merge main into the branch, resolve '
    + 'the conflicts, fix the failures')
  expect(await askToFix(recording(() => null).host, {}, 81)).toBe(false)

  // A check that couldn't merge main is a conflict, with no failed test to quote.
  expect(await fixBody(host, {}, { mergeable: 'CONFLICTING', checks: [{ name: 'merge', status: 'failed', log }] }))
    .toMatch(/^This PR has merge conflicts/)
  expect(await fixBody(host, {}, null)).toMatch(/^This PR has merge conflicts/)
})

test('Unblock takes an issue off hold and removes its open blockers, read when pressed, in one edit', async () => {
  // A host whose issue #50 stands in `status` and is blocked by `blockers`, and whose edit answers `edited`.
  const hostOf = (status: string, blockers: { number: number; state: string }[], edited: string | null = '') =>
    recording(argv => (argv.includes('view') ? JSON.stringify({ status, blockedBy: blockers })
      : argv.includes('edit') ? edited : null))
  const both = hostOf('on-hold', [{ number: 43, state: 'OPEN' }, { number: 9, state: 'CLOSED' },
    { number: 44, state: 'OPEN' }])
  expect(await unblockIssue(both.host, {}, 50)).toBe(true)
  expect(both.desk()).toEqual([['issue', 'view', '50', '--json', 'status,blockedBy'],
    ['issue', 'edit', '50', '--remove-label', 'on-hold', '--remove-blocked-by', '43', '--remove-blocked-by', '44']])

  // Only the flags that apply: no hold to take off, or no open blocker to remove.
  const edits = (one: ReturnType<typeof hostOf>) => one.desk().filter(args => args[1] === 'edit')
  const blockersOnly = hostOf('waiting', [{ number: 43, state: 'OPEN' }])
  expect(await unblockIssue(blockersOnly.host, {}, 50)).toBe(true)
  expect(edits(blockersOnly)).toEqual([['issue', 'edit', '50', '--remove-blocked-by', '43']])
  const heldOnly = hostOf('on-hold', [{ number: 9, state: 'CLOSED' }])
  expect(await unblockIssue(heldOnly.host, {}, 50)).toBe(true)
  expect(edits(heldOnly)).toEqual([['issue', 'edit', '50', '--remove-label', 'on-hold']])
  const free = hostOf('open', [])
  expect(await unblockIssue(free.host, {}, 50)).toBe(true)
  expect(edits(free)).toEqual([])

  // A read or an edit agent-desk refuses fails the press.
  expect(await unblockIssue(hostOf('on-hold', [], null).host, {}, 50)).toBe(false)
  expect(await unblockIssue(recording(() => null).host, {}, 50)).toBe(false)
})

test('Run now asks the dispatcher for the chore, and says when agent-desk refuses', async () => {
  const asked: string[][] = []
  const host = { run: async (argv: string[]) => { asked.push(argv); return '1' } } as unknown as Host
  expect(await startChore(host, {}, 'refactor')).toBe(true)
  expect(asked).toEqual([['bash', '-c', `exec "${DESK}" request chore "$1"`, '_', 'refactor']])
  const refusing = { run: async () => null } as unknown as Host
  expect(await startChore(refusing, {}, 'audit')).toBe(false)
})

test('Cancel asks the dispatcher to cancel the one run request, and says when it refuses one that works', async () => {
  const asked: string[][] = []
  const host = { run: async (argv: string[]) => { asked.push(argv); return 'Cancelled run 12.' } } as unknown as Host
  expect(await cancelRequest(host, {}, 12)).toBe(true)
  expect(asked).toEqual([['bash', '-c', `exec python3 -B ${DISPATCH} cancel "$1"`, '_', '12']])
  const refusing = { run: async () => null } as unknown as Host
  expect(await cancelRequest(refusing, {}, 12)).toBe(false)
})

test('Pause and Resume ask the dispatcher to hold an issue and to queue it again, and say when it refuses', async () => {
  const asked: string[][] = []
  const host = { run: async (argv: string[]) => { asked.push(argv); return 'Paused #12.' } } as unknown as Host
  expect(await pauseIssue(host, {}, 12)).toBe(true)
  expect(await unpauseIssue(host, {}, 12)).toBe(true)
  expect(asked).toEqual([['bash', '-c', `exec python3 -B ${DISPATCH} pause "$1"`, '_', '12'],
    ['bash', '-c', `exec python3 -B ${DISPATCH} unpause "$1"`, '_', '12']])
  const refusing = { run: async () => null } as unknown as Host
  expect(await pauseIssue(refusing, {}, 12)).toBe(false)
  expect(await unpauseIssue(refusing, {}, 12)).toBe(false)
})

test('Move to Open on a queued issue asks the dispatcher to unqueue it, and says when its run started', async () => {
  const asked: string[][] = []
  const host = { run: async (argv: string[]) => { asked.push(argv); return 'Moved #12 to Open.' } } as unknown as Host
  expect(await unqueueIssue(host, {}, 12)).toBe(true)
  expect(asked).toEqual([['bash', '-c', `exec python3 -B ${DISPATCH} unqueue "$1"`, '_', '12']])
  const refusing = { run: async () => null } as unknown as Host
  expect(await unqueueIssue(refusing, {}, 12)).toBe(false)
  expect(unqueueFailure(12)).toBe("Couldn't move #12 to Open: its run started.")
})

test('a move in the list order runs list order beside the issue it passes, and a sort resets the section\'s issues, '
  + 'each saying when the store refuses', async () => {
  const { host, desk } = recording()
  expect(await moveInListOrder(host, {}, 12, { before: 9 })).toBe(true)
  expect(await moveInListOrder(host, {}, 12, { after: 30 })).toBe(true)
  expect(await sortListOrder(host, {}, [12, 40, 9])).toBe(true)
  expect(desk()).toEqual([['list', 'order', '12', '--before', '9'], ['list', 'order', '12', '--after', '30'],
    ['list', 'order', '--reset', '12', '40', '9']])
  const refusing = recording(() => null)
  expect(await moveInListOrder(refusing.host, {}, 12, { before: 9 })).toBe(false)
  expect(await sortListOrder(refusing.host, {}, [12, 9])).toBe(false)
  expect(listOrderFailure(12)).toBe("Couldn't move #12 in the list order: it may have closed or been queued.")
  expect(listSortFailure('Backlog')).toBe("Couldn't sort Backlog; the list shows the store's order.")
})

test('Unpick drops a queued issue\'s Manual pick through lib/hold.py, which refuses an issue its run claimed',
  async () => {
  const asked: string[][] = []
  const host = { run: async (argv: string[]) => { asked.push(argv); return '' } } as unknown as Host
  expect(await unpickIssue(host, {}, 12)).toBe(true)
  expect(asked).toEqual([['bash', '-c', `exec python3 -B ${HOLD} unpick "$1"`, '_', '12']])
  const refusing = { run: async () => null } as unknown as Host
  expect(await unpickIssue(refusing, {}, 12)).toBe(false)
  expect(unpickFailure(12)).toBe("Couldn't unpick #12: its run claimed it, or its pick dropped already.")
})

test('Restart asks the dispatcher for the failed run again, or else for a fresh start, and picks nothing',
  async () => {
  const asked: string[][] = []
  const host: Host = {
    run: async argv => {
      asked.push([...argv])
      return ''
    },
    now: async () => 0,
    get: async () => undefined,
    set: async () => undefined,
  }
  expect(await restartIssue(host, { root: '/x' }, { number: 1297, rerun: -32, title: 'Bake the lightmaps' }))
    .toBe(true)
  expect(asked).toEqual([['bash', '-c', `exec python3 -B ${DISPATCH} again "$1"`, '_', '32']])

  // Only a run the dispatcher started, whose id comes below zero, can run again; nothing runs for any other id.
  asked.length = 0
  expect(await retryRun(host, { root: '/x' }, 6)).toBe(false)
  expect(asked).toEqual([])

  asked.length = 0
  expect(await restartIssue(host, { root: '/x' }, { number: 1297, title: 'Bake the lightmaps',
    labels: ['in-progress', 'benchmark'] })).toBe(true)
  // A fresh start never labels the issue, since agent-ready is the person's, and never picks it, since Start does.
  expect(asked).toEqual([['bash', '-c', START_AGENT, '_', '1297', '--benchmark']])
  // The dispatcher takes the request as Agent-Ready's, which Manual holds for Start, and nothing asks GitHub.
  expect(START_AGENT).toBe(`exec "${DESK}" request agent "$1" --env EVENT=issues $2`)
  expect(asked.flat().some(arg => arg.includes(HOLD))).toBe(false)

  const refusing = { run: async () => null } as unknown as Host
  expect(await restartIssue(refusing, { root: '/x' }, { number: 1297, title: 'Bake the lightmaps' })).toBe(false)
})
