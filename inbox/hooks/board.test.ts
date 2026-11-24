import { expect, test } from 'claude-code/testing'

import type { Row } from '../types'
import { agentWork, ago, backupOf, byListOrder, choreColumns, choreLines, cite, fetchBoard, githubRows, heldBy,
  isBacklog, isIdea, isInFlight, linkOf, mergeQueueOf, originRow, stackedOn, stopOf, stoppedRow, when } from './board.ts'
import type { BackupState, Host, StoreBoard } from './board.ts'
import { BOARD_READ, NOW, issue, review, storeHost, storeOf } from './board.fixtures.ts'
import { GOLDEN } from './board.golden.ts'
import { prName } from './moves.ts'
import { FAILURES } from './run.ts'

/** Whether a command reads GitHub through gh. */
const readsGitHub = (argv: string[]) => argv[0] === 'gh'

test('a time reads compact in minutes, hours, then days', () => {
  const now = Date.parse('2026-10-24T12:00:00Z')
  expect(ago(now, '2026-10-24T11:48:00Z')).toBe('12m')
  expect(ago(now, '2026-10-24T04:00:00Z')).toBe('8h')
  expect(ago(now, '2026-10-24T00:00:00Z')).toBe('12h')
  expect(ago(now, '2026-10-21T12:00:00Z')).toBe('3d')
})

test('a merge reads as its date and time in the local zone', () => {
  expect(when('2026-10-23T21:05:00Z', '-0700')).toBe('Oct 23 14:05')
  expect(when('2026-10-23T23:30:00Z', '+0530')).toBe('Oct 24 05:00')
  expect(when('2026-10-03T09:07:00Z', '+0000')).toBe('Oct  3 09:07')
})

test('a row links to its item on the web page, by the page\'s desk name, at the address the read names', () => {
  expect(linkOf('http://127.0.0.1:7480', 'org/game', 72)).toBe('http://127.0.0.1:7480/#game:72')
  expect(linkOf('http://127.0.0.1:7490/', 'org/game', 81)).toBe('http://127.0.0.1:7490/#game:81')
  // A project with no owner, as one with "host": "none" may be, keeps its whole name.
  expect(linkOf('http://127.0.0.1:7480', 'game', 5)).toBe('http://127.0.0.1:7480/#game:5')
})

test('issue, review, and merged rows open on the web page at its port, and none on GitHub', async () => {
  const store = storeOf({
    issues: [issue(70), issue(76, 'needs-design')],
    reviews: [review(81, { branch: 'agent/issue-80' })],
    merged: [{ number: 69, title: 'PR 69', branch: 'agent/issue-68', base: 'main', mergedAt: '2026-10-29T08:00:00Z' }],
    web: 'http://127.0.0.1:7490',
  })
  const board = await fetchBoard(storeHost(store), { root: '/x', main: '/x' })
  const urls = [...board.next, ...board.design, ...board.needs, ...board.merged].map(row => [row.key, row.url])
  expect(urls).toEqual([['next-70', 'http://127.0.0.1:7490/#game:70'], ['decide-76', 'http://127.0.0.1:7490/#game:76'],
    ['pr-81', 'http://127.0.0.1:7490/#game:81'], ['merged-69', 'http://127.0.0.1:7490/#game:69']])
  // A read from before the store named the page's address links to the port the page serves at by default.
  const older = await fetchBoard(storeHost({ ...store, web: undefined }), { root: '/x', main: '/x' })
  expect(older.next[0]?.url).toBe('http://127.0.0.1:7480/#game:70')
})

test('each column comes from the store: an issue\'s state, its blockers and labels, and a review\'s checks',
  async () => {
  const log = '/state/agent-desk/checks/org__game/12.log'
  const store = storeOf({
    issues: [
      issue(70), issue(71, 'queued'), issue(72, 'in-progress'), issue(73, 'waiting',
        { blockedBy: [{ number: 70, state: 'OPEN' }, { number: 9, state: 'CLOSED' }] }),
      issue(74, 'waiting', { labels: ['serial:web'] }), issue(75, 'in-progress', { labels: ['serial:web'] }),
      issue(76, 'needs-design'), issue(77, 'on-hold'), issue(78, 'open', { labels: ['idea'] }),
      issue(79, 'open', { blockedBy: [{ number: 70, state: 'OPEN' }] }), issue(80, 'in-progress'),
    ],
    reviews: [
      review(81, { branch: 'agent/issue-80', checks: [{ name: 'test', status: 'passed', attempt: 1 }] }),
      review(82, { branch: 'agent/tidy-docs', checks: [{ name: 'test', status: 'failed', attempt: 2, log }] }),
      review(83, { branch: 'agent/tidy-css' }),
      review(84, { branch: 'agent/issue-75', draft: true }),
    ],
    merged: [{ number: 69, title: 'PR 69', branch: 'agent/issue-68', base: 'main', mergedAt: '2026-10-29T08:00:00Z' }],
    runs: [{ id: 5, kind: 'agent', target: '72', status: 'running', unit: 'u5.service',
      started_at: '2026-10-29T11:00:00Z' }],
    queue: { queue: [{ pr: 83, note: 'checks running' }], dropped: [], ahead: 2, push: { state: 'ok' } },
    at: '12:00 +0000',
  })
  const host = storeHost(store, argv => (argv[2] === `exec python3 -B ${FAILURES} log "$1"` && argv[4] === log
    ? 'docs/x.md:3: error: bad link\n' : ''))
  const board = await fetchBoard(host, { root: '/x', main: '/x' })
  expect(board.error).toBeNull()
  expect(board.next.map(row => row.key)).toEqual(['next-70'])
  expect(board.queued.map(row => [row.key, row.note])).toEqual([['queued-71', undefined],
    ['waiting-73', 'waiting on #70'], ['waiting-74', 'waiting on #75']])
  expect(board.runners.map(row => [row.key, row.number, row.url])).toEqual([
    ['unit-5', 72, 'http://127.0.0.1:7480/#game:72']])
  expect(board.design.map(row => row.key)).toEqual(['decide-76'])
  expect(board.blocked.map(row => [row.key, row.note])).toEqual([['blocked-79', 'blocked by #70'],
    ['blocked-77', 'on hold']])
  // An idea's row moves it to Open with Move to Open, and has no Agent-ready button.
  expect(board.ideas.map(row => [row.key, row.actions])).toEqual([['idea-78', ['open', 'to-open', 'close']]])
  expect(board.merging.map(row => [row.key, row.text])).toEqual([['merging-83', 'PR 83  (checks running)'],
    ['origin', 'main is 2 commits ahead of origin']])
  // #81 is #80's ready PR, so #80 waits on review and not as stopped; #75's PR is a draft its agent hasn't finished.
  expect(board.needs.map(row => [row.key, row.state, row.note, row.actions])).toEqual([
    ['pr-81', 'review', undefined, ['open', 'merge']],
    ['pr-82', 'failing', 'test failed: x.md:3: error: bad link', ['open', 'fix']],
    ['stopped-75', 'stopped', undefined, ['open', 'restart', 'close']],
  ])
  expect(board.needs[0]?.url).toBe('http://127.0.0.1:7480/#game:81')
  expect(board.merged.map(row => [row.key, row.time])).toEqual([['merged-69', 'Oct 29 08:00']])
  expect(host.asked.some(readsGitHub)).toBe(false)
})

test('the golden read tests/test_board.py checks builds every column, from one command and a failed check\'s log',
  async () => {
  const host = storeHost(GOLDEN, argv => (argv[2] === `exec python3 -B ${FAILURES} log "$1"`
    && argv[4] === '/state/checks/org__game/9.log' ? 'src/map.ts:4: error: no tiles\n' : ''),
  Date.parse('2026-11-10T12:00:00Z'))
  const board = await fetchBoard(host, { root: '/x', main: '/x' })
  expect(board.error).toBeNull()
  expect(board.at).toBe('12:00')
  expect(board.next.map(row => row.key)).toEqual(['next-1'])
  // Fix sent #15 back to its agent, whose comment the dispatcher hasn't answered, and its row leads the issues in the
  // start order. #2 is picked for Manual, so it says so, waits for a slot and not for Start, and offers Unpick.
  // Nobody picked serial #4, so it waits for Start too.
  expect(board.queued.map(row => [row.key, row.note, row.actions])).toEqual([
    ['rerun-pr-15', 'fixing conflicts', ['open']],
    ['queued-2', 'picked', ['open', 'unpick', 'to-open', 'close', 'down', 'bottom']],
    ['waiting-3', 'waiting on #1', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['waiting-4', 'waiting on #5', ['open', 'start', 'to-open', 'close', 'up', 'top']]])
  // #9's run stalled at 11:48 in a test that left the CPU idle.
  expect(board.runners.map(row => [row.key, row.number, row.labels, row.note, row.stalled])).toEqual([['unit-1', 9,
    ['benchmark'], 'stalled 12 min in Bash: cargo test --workspace', true]])
  // The project sets no slots, so it has the defaults; #13's retry is the running check.
  expect(board.slots).toEqual([{ kind: 'agent', busy: 1, total: 3 }, { kind: 'chore', busy: 0, total: 1 },
    { kind: 'fix', busy: 0, total: 1 }, { kind: 'check', busy: 1, total: 2 }])
  expect(board.checks?.map(row => [row.key, row.number, row.issue, row.note, row.time])).toEqual([
    ['check-agent/issue-12@failing-head', 13, 12, 'agent/issue-12', '1h']])
  expect(board.design.map(row => row.key)).toEqual(['decide-6'])
  expect(board.blocked.map(row => [row.key, row.note])).toEqual([['blocked-7', 'on hold']])
  expect(board.ideas.map(row => row.key)).toEqual(['idea-8'])
  expect(board.merging.map(row => [row.key, row.text])).toEqual([['merging-17',
    'The merging review  (next to merge)']])
  // #13's retry runs, and its first attempt's failure shows until the retry ends.
  expect(board.needs.map(row => [row.key, row.state, row.note, row.actions, row.rerun])).toEqual([
    ['pr-11', 'review', undefined, ['open', 'merge'], undefined],
    ['pr-13', 'failing', 'test failed: map.ts:4: error: no tiles', ['open', 'fix'], undefined],
    ['stopped-5', 'stopped', 'failed', ['open', 'log', 'restart', 'close'], -2],
    ['draft-19', 'draft', undefined, ['open', 'undraft'], undefined],
  ])
  expect(board.merged.map(row => [row.key, row.time])).toEqual([['merged-21', 'Nov 10 10:30']])
  expect(board.choreList).toEqual([{ name: 'audit', every: '7d', last: 'done Mon Nov 9 08:41',
    next: 'Mon Nov 16 08:41' }])
  // The golden project has no checkout, so only the backup shows in the GitHub group.
  expect(board.github?.map(row => [row.key, row.note, row.notice])).toEqual([['github-backup',
    'last commit Oct 30 11:50, last push Oct 30 11:10, a commit waits for the hourly push', 'muted']])
  expect(host.asked.map(argv => argv[2])).toEqual([BOARD_READ, `exec python3 -B ${FAILURES} log "$1"`])
})

test('Unblock frees an issue on hold or behind open blockers, and not one waiting on a stacked PR\'s base',
  async () => {
  const at = (number: number) => ({ createdAt: `2026-10-29T09:${number}:00Z` })
  const open = [{ number: 70, state: 'OPEN' }]
  // #53's PR merged into agent/issue-52, whose PR #186 is still open.
  const issues = [issue(70, 'open', at(70)), issue(80, 'on-hold', at(80)), issue(81, 'open', { ...at(81),
    blockedBy: open }), issue(82, 'on-hold', { ...at(82), blockedBy: open }),
  issue(53, 'in-progress', { landed: { base: 'agent/issue-52', basePr: { number: 186, state: 'OPEN' } } })]
  const board = await fetchBoard(storeHost(storeOf({ issues })), { root: '/x', main: '/x' })
  expect(board.blocked.map(row => [row.key, row.note, row.actions])).toEqual([
    ['blocked-82', 'blocked by #70', ['open', 'unpause', 'unblock', 'close', 'down', 'bottom']],
    ['blocked-81', 'blocked by #70', ['open', 'ready', 'unblock', 'close', 'up', 'down', 'top', 'bottom']],
    ['blocked-80', 'on hold', ['open', 'unpause', 'unblock', 'close', 'up', 'top']],
    ['stack-wait-53', 'waiting on PR #186', ['open']],
  ])

  // Once the base PR merges too, the issue needs closing by hand.
  const merged = await fetchBoard(storeHost(storeOf({ issues: [issue(53, 'in-progress',
    { landed: { base: 'agent/issue-52', basePr: { number: 186, state: 'MERGED' } } })] })), { root: '/x', main: '/x' })
  expect(merged.needs.map(row => [row.key, row.note])).toEqual([['stacked-53', 'with PR #186']])
})

test('a row reads by the number of what it opens, and a toast names an agent PR\'s issue too', () => {
  expect(cite({ number: 61 })).toBe('#61')
  expect(cite({})).toBe('')
  expect(prName({ number: 61, issue: 60 })).toBe('PR #61 (#60)')
  expect(prName({ number: 58 })).toBe('PR #58')
})

test('an in-progress issue with no agent counts as in flight, unless a PR or a run has it', () => {
  const none = new Set<string>()
  const idle = new Set<number>()
  expect(isInFlight('in-progress', 120, none, idle)).toBe(true)
  expect(isInFlight('queued', 101, none, idle)).toBe(false)
  expect(isInFlight('waiting', 102, none, idle)).toBe(false)
  expect(isInFlight('in-progress', 103, new Set(['agent/issue-103']), idle)).toBe(false)
  expect(isInFlight('in-progress', 104, none, new Set([104]))).toBe(false)
  expect(isInFlight('open', 105, none, idle)).toBe(false)
})

test('an idea an agent discusses stays in Ideas while Runners shows the discussion, and its failure in Needs You',
  async () => {
  const ideas = [issue(8, 'open', { labels: ['idea'], listPosition: 1 }),
    issue(9, 'open', { labels: ['idea'], listPosition: 2 })]
  const runs = [{ id: 4, kind: 'agent', target: 'idea-8', status: 'running', unit: 'u4.service',
    started_at: '2026-10-29T11:50:00Z' },
  { id: 5, kind: 'agent', target: 'idea-9', status: 'failed', unit: 'u5.service', started_at: '2026-10-29T11:00:00Z',
    ended_at: '2026-10-29T11:30:00Z' }]
  const board = await fetchBoard(storeHost(storeOf({ issues: ideas, runs })), { root: '/x', main: '/x' })
  expect(board.ideas.map(row => row.key)).toEqual(['idea-8', 'idea-9'])
  expect(board.runners.map(row => [row.key, row.number, row.note])).toEqual([['unit-4', 8, 'discussion · 20 min left']])
  expect(board.needs.map(row => [row.key, row.number, row.actions])).toEqual([
    ['failed-unit-5', 9, ['open', 'log', 'retry', 'dismiss']]])
  expect([...board.queued, ...board.next, ...board.design].map(row => row.key)).toEqual([])
})

test('an issue labelled idea sits in Ideas until the person queues it for an agent', () => {
  expect(isIdea(new Set(['idea', 'enhancement']), 'open')).toBe(true)
  expect(isIdea(new Set(['idea']), 'needs-design')).toBe(true)
  expect(isIdea(new Set(['idea']), 'on-hold')).toBe(true)
  expect(isIdea(new Set(['idea']), 'queued')).toBe(false)
  expect(isIdea(new Set(['idea']), 'in-progress')).toBe(false)
  expect(isIdea(new Set(['idea']), 'waiting')).toBe(false)
  expect(isIdea(new Set(['enhancement']), 'open')).toBe(false)
})

test('an issue labelled backlog sits in Backlog until the person queues it, and one labelled idea too sits in Ideas',
  () => {
  expect(isBacklog(new Set(['backlog', 'enhancement']), 'open')).toBe(true)
  expect(isBacklog(new Set(['backlog']), 'needs-design')).toBe(true)
  expect(isBacklog(new Set(['backlog']), 'on-hold')).toBe(true)
  expect(isBacklog(new Set(['backlog']), 'queued')).toBe(false)
  expect(isBacklog(new Set(['backlog']), 'in-progress')).toBe(false)
  expect(isBacklog(new Set(['backlog']), 'waiting')).toBe(false)
  expect(isBacklog(new Set(['backlog', 'idea']), 'open')).toBe(false)
  expect(isBacklog(new Set(['enhancement']), 'open')).toBe(false)
})

test('Backlog lists the issues labelled backlog in the list order, apart from Open, Blocked, and Needs Design, with '
  + 'Move to Open and no Agent-ready button', async () => {
  const at = (place: number) => ({ listPosition: place })
  const issues = [
    issue(90, 'open', { labels: ['backlog'], ...at(5) }),
    issue(91, 'needs-design', { labels: ['backlog'], ...at(3) }),
    issue(92, 'on-hold', { labels: ['backlog'], ...at(4) }),
    issue(93, 'open', { labels: ['backlog'], blockedBy: [{ number: 96, state: 'OPEN' }], ...at(2) }),
    issue(94, 'open', { labels: ['backlog', 'idea'], ...at(1) }),
    issue(95, 'queued', { labels: ['backlog'] }),
    issue(96, 'open', at(6)),
  ]
  const board = await fetchBoard(storeHost(storeOf({ issues })), { root: '/x', main: '/x' })
  expect(board.backlog.map(row => [row.key, row.actions])).toEqual([
    ['backlog-93', ['open', 'to-open', 'close', 'down', 'bottom']],
    ['backlog-91', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['backlog-92', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['backlog-90', ['open', 'to-open', 'close', 'up', 'top']]])
  expect(board.backlog[0]?.labels).toEqual(['backlog'])
  // Open rows offer Move to Backlog beside Agent-ready; an issue queued another way leaves Backlog for Queued.
  expect(board.next.map(row => [row.key, row.actions])).toEqual([['next-96', ['open', 'ready', 'to-backlog', 'close']]])
  expect(board.ideas.map(row => row.key)).toEqual(['idea-94'])
  expect(board.queued.map(row => row.key)).toEqual(['queued-95'])
  expect([...board.blocked, ...board.design].map(row => row.key)).toEqual([])
  expect(board.designTotal).toBe(0)
})

test('Open, Blocked, Needs Design, Backlog, and Ideas list their issues in the list order, those without a place '
  + 'after, the highest number first, and Blocked lists an issue waiting on a stacked PR\'s base last', async () => {
  const at = (place: number) => ({ listPosition: place })
  const issues = [
    issue(10, 'open', at(6)), issue(11, 'open', at(2)), issue(12, 'open'), issue(13, 'open', at(9)), issue(14, 'open'),
    issue(20, 'on-hold', at(4)), issue(21, 'open', { blockedBy: [{ number: 10, state: 'OPEN' }], ...at(1) }),
    issue(22, 'in-progress', { landed: { base: 'agent/issue-9', basePr: { number: 186, state: 'OPEN' } }, ...at(3) }),
    issue(30, 'needs-design', at(8)), issue(31, 'needs-design', at(5)),
    issue(40, 'open', { labels: ['backlog'], ...at(11) }), issue(41, 'open', { labels: ['backlog'], ...at(7) }),
    issue(50, 'open', { labels: ['idea'], ...at(12) }), issue(51, 'open', { labels: ['idea'], ...at(10) }),
  ]
  const board = await fetchBoard(storeHost(storeOf({ issues })), { root: '/x', main: '/x' })
  expect(board.next.map(row => row.number)).toEqual([11, 10, 13, 14, 12])
  expect(board.blocked.map(row => row.number)).toEqual([21, 20, 22])
  expect(board.design.map(row => row.number)).toEqual([31, 30])
  expect(board.backlog.map(row => row.number)).toEqual([41, 40])
  expect(board.ideas.map(row => row.number)).toEqual([51, 50])
  expect([issue(3), issue(5), issue(4, 'open', at(1))].sort(byListOrder).map(one => one.number)).toEqual([4, 5, 3])

  // Each issue gets the moves its place in its section allows, after its other buttons, and Blocked's issue that
  // waits on a stacked PR's base gets none.
  const moves = (rows: Row[]) => rows.map(row => row.actions.filter(one => ['up', 'down', 'top', 'bottom'].includes(one)))
  expect(moves(board.next)).toEqual([['down', 'bottom'], ['up', 'down', 'top', 'bottom'],
    ['up', 'down', 'top', 'bottom'], ['up', 'down', 'top', 'bottom'], ['up', 'top']])
  expect(board.blocked.map(row => [row.key, row.actions])).toEqual([
    ['blocked-21', ['open', 'ready', 'unblock', 'close', 'down', 'bottom']],
    ['blocked-20', ['open', 'unpause', 'unblock', 'close', 'up', 'top']], ['stack-wait-22', ['open']]])
  expect(board.design.map(row => row.actions)).toEqual([['open', 'designed', 'close', 'down', 'bottom'],
    ['open', 'designed', 'close', 'up', 'top']])
  expect(board.backlog.map(row => row.actions)).toEqual([['open', 'to-open', 'close', 'down', 'bottom'],
    ['open', 'to-open', 'close', 'up', 'top']])
  expect(board.ideas.map(row => row.actions)).toEqual([['open', 'to-open', 'close', 'down', 'bottom'],
    ['open', 'to-open', 'close', 'up', 'top']])
  // A section with one issue has no moves.
  const lone = await fetchBoard(storeHost(storeOf({ issues: [issue(60, 'open'), issue(61, 'open', { labels: ['idea'] })] })),
    { root: '/x', main: '/x' })
  expect([lone.next[0]?.actions, lone.ideas[0]?.actions]).toEqual([['open', 'ready', 'to-backlog', 'close'],
    ['open', 'to-open', 'close']])
})

test('a PR stacked on another agent branch waits on the base PR, then merges with it', () => {
  expect(stackedOn('agent/issue-53', { number: 186, state: 'OPEN' })).toEqual({ pr: 186, merged: false })
  expect(stackedOn('agent/issue-53', { number: 186, state: 'MERGED' })).toEqual({ pr: 186, merged: true })
  // Work that merged into the default branch, or whose base PR closed unmerged or never existed, leaves the agent
  // stopped.
  expect(stackedOn('main', { number: 186, state: 'OPEN' })).toBeNull()
  expect(stackedOn('agent/issue-53', { number: 186, state: 'CLOSED' })).toBeNull()
  expect(stackedOn(undefined, undefined)).toBeNull()
  expect(stackedOn('agent/issue-53', null)).toBeNull()
})

test('an issue waits behind its open blockers, or for its hold to come off', () => {
  expect(heldBy('open', [])).toBeNull()
  expect(heldBy('on-hold', [])).toBe('on hold')
  expect(heldBy('open', [{ number: 141, state: 'OPEN' }, { number: 90, state: 'CLOSED' }])).toBe('blocked by #141')
  expect(heldBy('open', [{ number: 90, state: 'CLOSED' }])).toBeNull()
  expect(heldBy('on-hold', [{ number: 141, state: 'OPEN' }, { number: 142, state: 'OPEN' }]))
    .toBe('blocked by #141, #142')
})

test('the merge queue shows in Merging, and a PR that left it says why in Needs You until its branch moves on',
  async () => {
  const reviews = [review(61), review(62), review(63), review(64)]
  // #64's agent pushed a fix after it left the queue.
  const tick = { queue: [{ pr: 63, note: 'checks running' }, { pr: 61, note: 'after #63' }],
    dropped: [{ pr: 62, reason: 'checks failed', head: 'h62' }, { pr: 64, reason: 'conflicts with main',
      head: 'h0' }] }
  const board = await fetchBoard(storeHost(storeOf({ reviews, queue: tick })), { root: '/x', main: '/x' })
  expect(board.needs.map(row => [row.key, row.note])).toEqual([['pr-62', 'left the merge queue: checks failed'],
    ['pr-64', undefined]])
  expect(board.needs[0]?.text).toBe('review  PR 62  (left the merge queue: checks failed)')
  expect(board.merging.map(row => [row.key, row.text])).toEqual([
    ['merging-63', 'PR 63  (checks running)'],
    ['merging-61', 'PR 61  (after #63)'],
  ])
  expect(board.merged).toEqual([])
})

test('a merging review whose auto-fix or branch check the dispatcher holds says why, and the reviews behind it wait',
  async () => {
  const reviews = [review(298, { mergeable: 'CONFLICTING' }), review(300), review(303)]
  const queue = { queue: [{ pr: 298, note: 'conflicts with main; auto-fix resolving' }, { pr: 300, note: 'after #298' },
    { pr: 303, note: 'after #300' }], dropped: [] }
  const fix = (extra: object) => [{ id: 600, kind: 'fix', target: '298', status: 'requested', unit: null,
    started_at: null, ...extra }]
  const board = async (parts: Partial<StoreBoard>) => fetchBoard(storeHost(storeOf({ reviews, queue, ...parts })),
    { root: '/x', main: '/x' })
  const memory = await board({ runs: fix({ gate: 'memory', reason: 'memory: 5.3 GB free of 1 GB + 8 GB reserve' }) })
  expect(memory.merging.map(row => [row.key, row.note])).toEqual([
    ['merging-298', 'conflicts with main; auto-fix waits for memory: 5.3 GB free of 1 GB + 8 GB reserve'],
    ['merging-300', 'after #298'], ['merging-303', 'after #300']])
  expect(memory.merging[0]?.text).toBe('PR 298  (conflicts with main; auto-fix waits for memory: 5.3 GB free of 1 GB '
    + '+ 8 GB reserve)')
  const slot = await board({ runs: fix({ gate: 'slot', reason: 'a free fix slot' }) })
  expect(slot.merging[0]?.note).toBe('conflicts with main; auto-fix waits for a free fix slot')
  // A fix the dispatcher records no reason for, or one that works, leaves the note as the queue wrote it.
  for (const runs of [fix({}), fix({ status: 'running', unit: 'u.service', gate: 'slot', reason: 'a free fix slot' })]) {
    expect((await board({ runs })).merging[0]?.note).toBe('conflicts with main; auto-fix resolving')
  }
  // A review's own check, and a batch's on its merge-queue/ branch, say why they wait the same way.
  const own = await board({ queue: { queue: [{ pr: 300, note: 'checks starting' }], dropped: [] },
    waitingChecks: [{ branch: 'agent/issue-299', review: 300, head: 'h300', gate: 'slot', reason: 'a free check slot' }] })
  expect(own.merging.map(row => row.note)).toEqual(['check waits for a free check slot'])
  const batch = await board({ queue: { queue: [{ pr: 300, note: 'batch with #303: checks starting' },
    { pr: 303, note: 'batch with #300: checks starting' }], dropped: [] },
  waitingChecks: [{ branch: 'merge-queue/1700000000', review: null, head: 'b1', gate: 'mode', reason: 'Resume' }] })
  expect(batch.merging.map(row => row.note)).toEqual(['batch with #303: check waits for Resume',
    'batch with #300: check waits for Resume'])
})

test('a merge queue stopped on uncommitted changes leads Needs You with a warning, and its review says so in Merging',
  async () => {
  const reviews = [review(275), review(289)]
  const stopped = { where: 'the main checkout', files: 'README.md' }
  const tick = { queue: [{ pr: 275, note: 'stopped: the main checkout has uncommitted changes to README.md, which the '
    + 'merge changes', stopped }, { pr: 289, note: 'after #275' }], dropped: [] }
  const board = await fetchBoard(storeHost(storeOf({ reviews, queue: tick })), { root: '/x', main: '/x' })
  expect(board.needs.map(row => [row.key, row.text, row.notice, row.number])).toEqual([['merge-stopped',
    'merge queue stopped: the main checkout has uncommitted changes to README.md, which #275 changes. Commit, stash, '
    + 'or undo them.', 'warning', undefined]])
  // The files lead the stopped review's note, so the card's note column cuts the rest; the review behind it waits.
  expect(board.merging.map(row => [row.key, row.state, row.note])).toEqual([
    ['merging-275', 'stopped', 'README.md uncommitted in the main checkout'],
    ['merging-289', undefined, 'after #275'],
  ])
  // Once the queue moves, the warning goes.
  const moved = { queue: [{ pr: 275, note: 'checks running' }, { pr: 289, note: 'after #275' }], dropped: [] }
  const after = await fetchBoard(storeHost(storeOf({ reviews, queue: moved })), { root: '/x', main: '/x' })
  expect(after.needs).toEqual([])
  expect(after.merging.map(row => row.state)).toEqual([undefined, undefined])
})

test('a stopped batch names each review it holds, and a stop the board cannot read holds nothing', () => {
  const stopped = { where: 'demo-local', files: 'a.md, b.md, c.md and 2 more' }
  const batch = mergeQueueOf({ queue: [{ pr: 3, note: 'batch with #4, #5: stopped', stopped },
    { pr: 4, note: 'batch with #3, #5: stopped', stopped }, { pr: 5, note: 'batch with #3, #4: stopped', stopped },
    { pr: 6, note: 'after #5' }], dropped: [] })
  expect(stoppedRow(batch)?.text).toBe('merge queue stopped: demo-local has uncommitted changes to a.md, b.md, c.md '
    + 'and 2 more, which #3, #4 and #5 change. Commit, stash, or undo them.')
  expect(stoppedRow(mergeQueueOf({ queue: [{ pr: 3, note: 'checks running' }], dropped: [] }))).toBeNull()
  expect(stopOf({ where: 'the main checkout' })).toBeNull()
  expect(stopOf('stopped')).toBeNull()
  expect(stopOf(undefined)).toBeNull()
})

test('a PR just merged into another base shows in Recently Merged, and its issue, not yet out of progress, not as '
  + 'stopped', async () => {
  // The cleanup takes the issue out of progress a few seconds after a merge into a base other than main.
  const store = storeOf({ issues: [issue(60, 'in-progress')], merged: [{ number: 61, title: 'PR 61',
    branch: 'agent/issue-60', base: 'local-desk', mergedAt: '2026-10-29T12:00:00Z' }] })
  const board = await fetchBoard(storeHost(store, () => '', Date.parse('2026-10-29T12:00:30Z')), { root: '/x' })
  expect(board.needs).toEqual([])
  expect(board.merged.map(row => row.key)).toEqual(['merged-61'])
})

test('an issue reopened after its PR merged, whose new agent stopped, shows as stopped', async () => {
  const comments = [
    { body: 'An agent started on `agent/issue-60`.', createdAt: '2026-10-29T09:05:00Z' },
    { body: 'The agent finished: https://example.test/61 (ready for review).', createdAt: '2026-10-29T09:40:00Z' },
    { body: 'An agent started on `agent/issue-60`.', createdAt: '2026-10-29T11:00:00Z' },
  ]
  const store = storeOf({ issues: [issue(60, 'in-progress', { comments })],
    merged: [{ number: 61, title: 'PR 61', branch: 'agent/issue-60', base: 'main', mergedAt: '2026-10-29T10:00:00Z' }] })
  const board = await fetchBoard(storeHost(store), { root: '/x', main: '/x' })
  expect(board.needs.map(row => row.key)).toEqual(['stopped-60'])
})

test('a run the dispatcher holds shows in Runners while it works and in Queued while it waits, not as stopped',
  async () => {
  const issues = [issue(7, 'in-progress', { labels: ['serial:web'] }), issue(8, 'in-progress'), issue(9, 'in-progress'),
    issue(10), issue(43, 'in-progress', { labels: ['benchmark'] })]
  const runs = [
    { id: 3, kind: 'agent', target: '7', status: 'running', unit: 'agent-desk-agent-org-game-7-3.service',
      started_at: '2026-10-29T09:20:00Z', benchmark: false },
    { id: 4, kind: 'agent', target: '8', status: 'requested', unit: null, started_at: null },
    { id: 9, kind: 'agent', target: '10', status: 'requested', unit: null, started_at: null },
    { id: 5, kind: 'chore', target: 'audit', status: 'running', unit: 'agent-desk-chore-org-game-audit-5.service',
      started_at: '2026-10-29T11:48:00Z' },
    // A benchmark issue's agent gets 5 hours, so 2 hours 40 minutes in it has no warning.
    { id: 2, kind: 'agent', target: '43', status: 'running', unit: 'u2.service', started_at: '2026-10-29T09:20:00Z' },
  ]
  const board = await fetchBoard(storeHost(storeOf({ issues, runs })), { root: '/x', main: '/x' })
  expect(board.runners.map(row => [row.key, row.note, row.labels])).toEqual([
    ['unit-3', 'serial · 20 min left', ['serial:web']], ['unit-5', undefined, undefined],
    ['unit-2', undefined, ['benchmark']]])
  expect(board.queued.map(row => [row.key, row.note, row.request, row.actions])).toEqual([
    ['rerun-8', 'back to its PR', 4, ['open', 'cancel']], ['request-9', 'waits to start', 9, ['open', 'cancel']]])
  expect(board.needs.map(row => row.key)).toEqual(['stopped-9'])
  const ended = [
    { id: 6, kind: 'agent', target: '9', status: 'failed', unit: 'u6.service', started_at: '2026-10-29T10:00:00Z',
      ended_at: '2026-10-29T10:30:00Z' },
    { id: 7, kind: 'chore', target: 'audit', status: 'timed-out', unit: 'u7.service', started_at: null,
      ended_at: '2026-10-29T11:00:00Z' },
    // Its issue closed since, so the failure has nothing left to retry.
    { id: 8, kind: 'agent', target: '44', status: 'failed', unit: 'u8.service', started_at: null },
  ]
  // #9's stopped row carries its failed run, so the run shows there alone.
  const after = await fetchBoard(storeHost(storeOf({ issues, runs: ended })), { root: '/x', main: '/x' })
  expect(after.needs.filter(row => row.number === 9 || row.key.startsWith('failed-')).map(row => [row.key, row.rerun]))
    .toEqual([['failed-unit-7', undefined], ['stopped-9', -6]])
})

test('a PR whose comment to the agent the dispatcher hasn\'t answered waits in Queued for its agent, and one no agent '
  + 'works stays in Needs You', async () => {
  const issues = [issue(80, 'in-progress'), issue(85, 'in-progress'), issue(87, 'in-progress')]
  const reviews = [review(81, { mergeable: 'CONFLICTING' }), review(86, { branch: 'agent/issue-85', draft: true }),
    review(88, { branch: 'agent/issue-87', draft: true, mergeable: 'CONFLICTING' }),
    review(90, { branch: 'feature/fence' }), review(92, { branch: 'agent/chore-comment-one-3' })]
  const board = await fetchBoard(storeHost(storeOf({ issues, reviews, asked: [81, 86, 88, 90, 92] })),
    { root: '/x', main: '/x' })
  // The drafts' issues have no request to cancel until the dispatcher answers the comment. A conflicting draft's agent
  // goes back to fix its conflicts.
  expect(board.queued.map(row => [row.key, row.note, row.request, row.actions])).toEqual([
    ['rerun-85', 'back to its PR', undefined, ['open']],
    ['rerun-87', 'fixing conflicts', undefined, ['open']],
    ['rerun-pr-81', 'fixing conflicts', undefined, ['open']],
    ['rerun-pr-92', 'back to its PR', undefined, ['open']]])
  expect(board.needs.map(row => row.key)).toEqual(['pr-90'])
})

test('an issue sent back to its open review names the review in Queued and Working, and no review once it closed',
  async () => {
  const issues = [issue(588, 'in-progress'), issue(590, 'in-progress')]
  const reviews = [review(640, { branch: 'agent/issue-588' }), review(641, { branch: 'agent/issue-590', draft: true })]
  const requested = [{ id: 7, kind: 'agent', target: '588', status: 'requested', unit: null, started_at: null },
    { id: 8, kind: 'agent', target: '590', status: 'requested', unit: null, started_at: null }]
  const project = { root: '/x', main: '/x' }
  const waiting = await fetchBoard(storeHost(storeOf({ issues, reviews, runs: requested })), project)
  // Each issue shows once, by its review and its issue, with Cancel for the request that waits on it.
  expect(waiting.queued.map(row => [row.key, row.number, row.issue, row.pr, row.url, row.request, row.actions]))
    .toEqual([
      ['rerun-pr-640', 640, 588, true, 'http://127.0.0.1:7480/#game:640', 7, ['open', 'cancel']],
      ['rerun-590', 641, 590, true, 'http://127.0.0.1:7480/#game:641', 8, ['open', 'cancel']]])
  expect(waiting.needs).toEqual([])
  const running = requested.map(one => ({ ...one, status: 'running', unit: `u${one.id}.service`,
    started_at: '2026-10-29T11:00:00Z' }))
  const working = await fetchBoard(storeHost(storeOf({ issues, reviews, runs: running })), project)
  expect(working.runners.map(row => [row.key, row.title, row.number, row.issue, row.pr, row.url])).toEqual([
    ['unit-7', 'Issue 588', 640, 588, true, 'http://127.0.0.1:7480/#game:640'],
    ['unit-8', 'Issue 590', 641, 590, true, 'http://127.0.0.1:7480/#game:641']])
  expect([working.queued, working.needs]).toEqual([[], []])
  // A closed review leaves the issue's row naming the issue alone, and a review the run opens since names that one.
  const reopened = [review(650, { branch: 'agent/issue-590', draft: true })]
  const after = await fetchBoard(storeHost(storeOf({ issues, reviews: reopened, runs: running })), project)
  expect(after.runners.map(row => [row.key, row.number, row.issue, row.pr, row.url])).toEqual([
    ['unit-7', 588, undefined, undefined, 'http://127.0.0.1:7480/#game:588'],
    ['unit-8', 650, 590, true, 'http://127.0.0.1:7480/#game:650']])
})

test('a PR whose checks fail shows failing with its first error and Fix, and a conflicting one when its fix starts',
  async () => {
  const log = '/state/agent-desk/checks/org__game/31.log'
  const reviews = [review(81, { checks: [{ name: 'test', status: 'failed', attempt: 2, log }] }),
    review(82, { mergeable: 'CONFLICTING', checks: [{ name: 'merge', status: 'failed', attempt: 1, log }] }),
    review(83, { checks: [{ name: 'test', status: 'passed', attempt: 1 }] }),
    review(84, { checks: [{ name: 'test', status: 'queued', attempt: 2 }] })]
  const excerpt = "Game/Sprite.cs(12,5): error CS0103: The name 'Size' does not exist\n"
    + 'Game/Sprite.cs(13,5): error CS0103: The name \'Width\' does not exist\n'
  const host = storeHost(storeOf({ reviews, fixes: [{ pr: 82, verdict: 'auto-fix on Merge' }, { pr: 'x' }] }),
    argv => (argv[2] === `exec python3 -B ${FAILURES} log "$1"` ? excerpt : ''))
  const board = await fetchBoard(host, { root: '/fails', main: '/fails' })
  expect(board.needs.map(row => [row.key, row.state, row.note, row.actions])).toEqual([
    ['pr-81', 'failing', "test failed: Sprite.cs(12,5): error CS0103: The name 'Size' does not exist", ['open', 'fix']],
    ['pr-82', 'conflicts', 'auto-fix on Merge', ['open', 'merge', 'fix']],
    ['pr-83', 'review', undefined, ['open', 'merge']],
    ['pr-84', 'review', undefined, ['open', 'merge']],
  ])
})

test('a ready PR names the base its check merged: a failure names the commit, and a pass against an older one how far behind',
  async () => {
  const log = '/state/agent-desk/checks/org__game/32.log'
  const reviews = [review(85, { checks: [{ name: 'test', status: 'failed', attempt: 2, log, base: 'main',
    baseSha: '01840f3c9e2a', recheck: true }] }),
  review(86, { checks: [{ name: 'test', status: 'passed', attempt: 1, base: 'main', baseSha: 'aa', behind: 2 }] }),
  review(87, { checks: [{ name: 'test', status: 'passed', attempt: 1, base: 'main', baseSha: 'bb', behind: 1 }] }),
  review(88, { checks: [{ name: 'test', status: 'passed', attempt: 1, base: 'main', baseSha: 'cc' }] })]
  const host = storeHost(storeOf({ reviews }),
    argv => (argv[2] === `exec python3 -B ${FAILURES} log "$1"` ? 'tests/frame.rs:9: error: bad header\n' : ''))
  const board = await fetchBoard(host, { root: '/behind', main: '/behind' })
  expect(board.needs.map(row => [row.key, row.state, row.note, row.actions])).toEqual([
    ['pr-85', 'failing', 'test failed merged with main at 01840f3: frame.rs:9: error: bad header', ['open', 'fix']],
    ['pr-86', 'review', 'test passed, 2 merges behind', ['open', 'merge']],
    ['pr-87', 'review', 'test passed, 1 merge behind', ['open', 'merge']],
    ['pr-88', 'review', undefined, ['open', 'merge']],
  ])
})

test('a saved queue the board can\'t read shows an empty queue', () => {
  const empty = { queue: [], dropped: [], ahead: 0, push: null, agentBranches: null }
  expect(mergeQueueOf(undefined)).toEqual(empty)
  expect(mergeQueueOf('gh failed')).toEqual(empty)
  expect(mergeQueueOf({ queue: [{ pr: 1, note: 'checks running' }], ahead: 'x', push: { error: 'no state' } }))
    .toEqual({ ...empty, queue: [{ pr: 1, note: 'checks running' }] })
})

test('Merging says how far main is ahead of origin and what holds its push back', () => {
  const queue = (ahead: number, push: unknown) => mergeQueueOf({ queue: [], dropped: [], ahead, push })
  expect(originRow(queue(0, { state: 'ok' }))).toBeNull()
  expect(originRow(queue(1, { state: 'ok' }))).toEqual({ key: 'origin', text: 'main is 1 commit ahead of origin',
    title: 'main is 1 commit ahead of origin', notice: 'muted', actions: [] })
  expect(originRow(queue(3, { state: 'retrying', error: 'Could not resolve host: github.com', retryIn: 240 }))?.text)
    .toBe('main is 3 commits ahead of origin  (push failed: Could not resolve host: github.com; retrying in 4m)')
  const refused = originRow(queue(2, { state: 'rejected', error: '[rejected] (fetch first)' }))
  expect([refused?.note, refused?.notice]).toEqual(["origin refused the push: merge origin's main by hand", 'warning'])
  expect(originRow(queue(2, { state: 'ok', branches: ['agent/issue-8'] }))?.note).toBe("couldn't push agent/issue-8")
  expect(originRow(queue(0, { state: 'ok', branches: ['agent/issue-8'] }))).toBeNull()
})

// 2026-11-10T12:00:00Z, in seconds since the epoch.
const NOON = 1794312000
const pushQueue = (ahead: number, push: unknown, agentBranches?: unknown) =>
  mergeQueueOf({ queue: [], dropped: [], ahead, push, agentBranches })
const backupDone: BackupState = { at: NOON, remote: 'origin', committedAt: NOON - 60, pushedAt: NOON - 3000,
  waiting: false }
const rowsOf = (rows: Row[]) => rows.map(row => [row.title, row.note, row.notice])

test('the GitHub group says main matches origin with its last push, or how far ahead it is and why the push waits',
  () => {
  const main = (ahead: number, push: unknown) => githubRows(pushQueue(ahead, push), backupDone, '+0000')[0]
  expect(main(0, { state: 'ok', pushedAt: NOON - 120 })).toEqual({ key: 'github-main',
    text: `${'main'.padEnd(15)}matches origin, last push Nov 10 11:58`, title: 'main',
    note: 'matches origin, last push Nov 10 11:58', notice: 'muted', actions: [] })
  expect(main(0, { state: 'ok' })?.note).toBe('matches origin')
  expect([main(2, { state: 'ok' })?.note, main(2, { state: 'ok' })?.notice])
    .toEqual(['2 commits ahead of origin, waiting for the next push', 'muted'])
  const retrying = main(1, { state: 'retrying', error: 'Could not resolve host: github.com', retryIn: 45 })
  expect([retrying?.note, retrying?.notice]).toEqual(
    ['1 commit ahead of origin; push failed: Could not resolve host: github.com; retrying in 45s', 'warning'])
  const refused = main(2, { state: 'rejected', error: "origin's main has 1 commit main lacks" })
  expect([refused?.note, refused?.notice]).toEqual(
    ["2 commits ahead of origin; origin refused the push: origin's main has 1 commit main lacks", 'warning'])
  // Merging's row and the group's read the same state.
  const queue = pushQueue(2, { state: 'rejected', error: 'x' })
  expect([originRow(queue)?.notice, githubRows(queue, null, '+0000')[0]?.notice]).toEqual(['warning', 'warning'])
})

test('the GitHub group counts the agent branches pushed and waiting, and names the ones that failed', () => {
  const branches = (counts: unknown, push: unknown = { state: 'ok' }) =>
    githubRows(pushQueue(0, push, counts), backupDone, '+0000')[1]
  expect(rowsOf([branches({ pushed: 12, waiting: 0, pushedAt: NOON - 600 })!])).toEqual(
    [['agent branches', '12 pushed, last push Nov 10 11:50', 'muted']])
  expect(branches({ pushed: 0, waiting: 0 })?.note).toBe('none to push')
  expect(rowsOf([branches({ pushed: 10, waiting: 2, pushedAt: NOON - 600 })!])).toEqual(
    [['agent branches', '10 pushed, 2 wait for the next push', 'muted']])
  expect(branches({ pushed: 10, waiting: 2 }, { state: 'retrying', error: 'down', retryIn: 30 })?.note)
    .toBe("10 pushed, 2 wait for main's push")
  expect(rowsOf([branches({ pushed: 10, waiting: 1, failed: ['agent/issue-8'], error: '[rejected] (fetch first)' })!]))
    .toEqual([['agent branches', "10 pushed, 1 wait for the next push; couldn't push agent/issue-8: "
      + '[rejected] (fetch first)', 'warning']])
})

test('a project whose host is none shows no main or branch row, and the backup shows once per board', () => {
  const none = githubRows(pushQueue(0, { state: 'none' }), backupDone, '+0000')
  expect(none.map(row => row.key)).toEqual(['github-backup'])
  expect(githubRows(mergeQueueOf(undefined), backupDone, '+0000').map(row => row.key)).toEqual(['github-backup'])
  const pushing = githubRows(pushQueue(0, { state: 'ok' }, { pushed: 1, waiting: 0 }), backupDone, '+0000')
  expect(pushing.map(row => row.key)).toEqual(['github-main', 'github-agent-branches', 'github-backup'])
})

test('the GitHub group gives the backup\'s last commit and push, the push that waits, and the error that failed',
  () => {
  const backup = (state: unknown) => githubRows(mergeQueueOf(undefined), backupOf(state), '+0200')[0]
  expect(rowsOf([backup(backupDone)!])).toEqual(
    [['backup', 'last commit Nov 10 13:59, last push Nov 10 13:10', 'muted']])
  expect(backup({ ...backupDone, waiting: true })?.note)
    .toBe('last commit Nov 10 13:59, last push Nov 10 13:10, a commit waits for the hourly push')
  expect(rowsOf([backup({ at: NOON, remote: null, committedAt: NOON - 60, waiting: false })!])).toEqual(
    [['backup', 'last commit Nov 10 13:59, no backup remote', 'muted']])
  expect(rowsOf([backup({ ...backupDone, waiting: true, error: 'the push to origin failed: Connection refused' })!]))
    .toEqual([['backup', 'last commit Nov 10 13:59, last push Nov 10 13:10; the push to origin failed: '
      + 'Connection refused', 'warning']])
  expect(backup({ at: NOON, remote: 'origin', error: 'the backup failed: disk full' })?.note)
    .toBe('no commit yet, never pushed to origin; the backup failed: disk full')
  expect(rowsOf([backup(null)!])).toEqual([['backup', 'no backup has run', 'muted']])
  expect(backup('not a state')?.note).toBe('no backup has run')
})

test('an agent goes back to an issue\'s PR by its issue and to any other agent branch\'s PR by the PR', () => {
  expect(agentWork({ number: 61, headRefName: 'agent/issue-12' })).toBe(12)
  expect(agentWork({ number: 537, headRefName: 'agent/chore-comment-one-20261028-1411' })).toBe(537)
  expect(agentWork({ number: 528, headRefName: 'agent/knowledge-fixes' })).toBe(528)
  expect(agentWork({ number: 90, headRefName: 'feature/mine' })).toBeUndefined()
})

test('the chores lines split into their columns, past the header', () => {
  const lines = ['Chore          Every  Last run                     Next',
    'audit          1d     done Mon Oct 26 08:41        Thu Oct 29 14:01',
    'untested-one   3d     running for 12 min           after this run',
    'refactor-one   5d     done Sun Nov 1 03:00         paused', '']
  expect(choreLines(lines)).toEqual({ chores: lines.slice(0, 4), choreList: [
    { name: 'audit', every: '1d', last: 'done Mon Oct 26 08:41', next: 'Thu Oct 29 14:01' },
    { name: 'untested-one', every: '3d', last: 'running for 12 min', next: 'after this run' },
    { name: 'refactor-one', every: '5d', last: 'done Sun Nov 1 03:00', next: 'paused' },
  ] })
})

test('a chore named past 14 characters widens the first column, and the columns follow the header', () => {
  const lines = ['Chore                   Every  Last run                     Next',
    'audit                   1d     done Mon Oct 26 08:41        Thu Oct 29 14:01',
    'singleshot-untested-one 3d     never                        now']
  expect(choreColumns(lines[0])).toEqual([0, 24, 31, 60])
  expect(choreLines(lines).choreList).toEqual([
    { name: 'audit', every: '1d', last: 'done Mon Oct 26 08:41', next: 'Thu Oct 29 14:01' },
    { name: 'singleshot-untested-one', every: '3d', last: 'never', next: 'now' },
  ])
  expect(choreColumns('no header')).toEqual([0, 15, 22, 51])
})

test('a stopped issue with no agent note shows Restart, which starts it afresh without a failed run', async () => {
  const board = await fetchBoard(storeHost(storeOf({ issues: [issue(1297, 'in-progress')] })), { root: '/x' })
  expect(board.needs.map(row => [row.key, row.actions, row.rerun])).toEqual([
    ['stopped-1297', ['open', 'restart', 'close'], undefined],
  ])
})

test('Restart asks again for the newest failed agent run of a stopped issue, unless an agent went on after it',
  async () => {
  const failed = (id: number, target: string, at: string) => ({ id, kind: 'agent', target, status: 'failed',
    unit: `u${id}.service`, started_at: null, ended_at: at })
  const store = storeOf({
    issues: [issue(1297, 'in-progress'), issue(1391, 'in-progress', {
      // #1391's agent started again after its failed run ended, and stopped on its own; Restart starts it afresh.
      comments: [{ body: 'An agent started on `agent/issue-1391`. To take over, run `claude --resume aaaa-1111` in '
        + '`/w/issue-1391`.', createdAt: '2026-10-29T08:00:00Z' }] })],
    runs: [failed(30, '1297', '2026-10-29T08:00:00Z'), failed(32, '1297', '2026-10-29T10:00:00Z'),
      failed(40, '1391', '2026-10-29T07:05:00Z')],
  })
  const board = await fetchBoard(storeHost(store), { root: '/x', main: '/x' })
  const stopped = board.needs.filter(row => row.state === 'stopped')
  expect(stopped.map(row => [row.key, row.rerun, row.actions])).toEqual([
    ['stopped-1297', -32, ['open', 'log', 'restart', 'close']],
    ['stopped-1391', undefined, ['open', 'resume', 'restart', 'close']],
  ])
})

test('a stopped issue and its timed-out run show as one row, while a run on an issue with a review or no stopped row '
  + 'keeps its own', async () => {
  const ended = (id: number, target: string, status: string, at: string) => ({ id, kind: 'agent', target, status,
    unit: `u${id}.service`, started_at: null, ended_at: at })
  const store = storeOf({
    // #1158 has no review, and another agent's comment edit at 10:01 changed it after its run timed out at 09:31.
    // #1160's agent opened draft review 1161 before it failed, and #1170 left progress after its run failed.
    issues: [issue(1158, 'in-progress', { updatedAt: '2026-10-29T10:01:00Z' }), issue(1160, 'in-progress'),
      issue(1170, 'open')],
    reviews: [review(1161, { draft: true })],
    runs: [ended(1465, '1158', 'timed-out', '2026-10-29T09:31:00Z'), ended(1470, '1160', 'failed',
      '2026-10-29T09:40:00Z'), ended(1480, '1170', 'failed', '2026-10-29T09:50:00Z')],
  })
  const board = await fetchBoard(storeHost(store), { root: '/x', main: '/x' })
  expect(board.needs.map(row => [row.key, row.text, row.number, row.issue, row.pr, row.run, row.rerun, row.since,
    row.log, row.actions])).toEqual([
    ['failed-unit-1470', 'failed  agent: Issue 1160', 1161, 1160, true, -1470, undefined, '2026-10-29T09:40:00Z',
      'journalctl --user -u u1470.service', ['open', 'log', 'retry', 'dismiss']],
    ['failed-unit-1480', 'failed  agent: Issue 1170', 1170, undefined, undefined, -1480, undefined,
      '2026-10-29T09:50:00Z', 'journalctl --user -u u1480.service', ['open', 'log', 'retry', 'dismiss']],
    ['stopped-1158', 'stopped  Issue 1158  (timed out)', 1158, undefined, undefined, undefined, -1465,
      '2026-10-29T09:31:00Z', 'journalctl --user -u u1465.service', ['open', 'log', 'restart', 'close']],
    ['stopped-1160', 'stopped  Issue 1160  (failed)', 1160, undefined, undefined, undefined, -1470,
      '2026-10-29T09:40:00Z', 'journalctl --user -u u1470.service', ['open', 'log', 'restart', 'close']],
  ])
  // The failed run on #1160 opens its issue, and Continue on #1158 takes up the timed-out run's session.
  expect(board.needs[0]?.url?.endsWith('#game:1160')).toBe(true)
  expect(board.needs[2]?.continues).toBe(true)
})

test('a draft whose checks fail shows its first error and Fix, one whose checks all passed shows Ready, and one '
  + 'whose check waits, runs, or is missing shows Open', async () => {
  const finished = (number: number) => ({ comments: [
    { body: `An agent started on \`agent/issue-${number}\`.`, createdAt: '2026-11-08T09:00:00Z' },
    { body: `The agent finished: https://example.test/${number + 1} (draft).`, createdAt: '2026-11-08T10:00:00Z' },
  ] })
  const log = '/state/agent-desk/checks/org__game/41.log'
  const draft = (number: number, status?: string) => review(number + 1, { draft: true,
    checks: status ? [{ name: 'test', status, attempt: 1, ...(status === 'failed' ? { log } : {}) }] : [] })
  const numbers = [1960, 1962, 1964, 1966, 1968]
  const host = storeHost(storeOf({
    issues: numbers.map(number => issue(number, 'in-progress', finished(number))),
    reviews: [draft(1960, 'failed'), draft(1962, 'passed'), draft(1964, 'running'), draft(1966, 'queued'),
      draft(1968)],
  }), argv => (argv[2] === `exec python3 -B ${FAILURES} log "$1"`
    ? 'Game/ActionMenuState.cs:54: expected Members, got Everyone\n' : ''))
  const board = await fetchBoard(host, { root: '/x', main: '/x' })
  expect(board.needs.map(row => [row.key, row.state, row.text, row.note, row.actions])).toEqual([
    ['draft-1961', 'draft', 'draft  PR 1961  (test failed: ActionMenuState.cs:54: expected Members, got Everyone)',
      'test failed: ActionMenuState.cs:54: expected Members, got Everyone', ['open', 'fix']],
    ['draft-1963', 'draft', 'draft  PR 1963', undefined, ['open', 'undraft']],
    ['draft-1965', 'draft', 'draft  PR 1965', undefined, ['open']],
    ['draft-1967', 'draft', 'draft  PR 1967', undefined, ['open']],
    ['draft-1969', 'draft', 'draft  PR 1969', undefined, ['open']],
  ])
})

test('a draft whose agent stopped early shows as a stopped issue with Resume and Restart, not as a finished draft',
  async () => {
  const started = { body: 'An agent started on `agent/issue-1000`. To take over, run `claude --resume bbbb-2222` in '
    + '`/w/issue-1000`.', createdAt: '2026-11-08T09:00:00Z' }
  const early = { body: 'The agent stopped early: it ended its turn while `just bench` still ran, so the run stopped '
    + 'it. Its work is on review #1001 (still a draft), and the next run on the issue takes up the same session.',
  createdAt: '2026-11-08T10:00:00Z' }
  const host = storeHost(storeOf({
    issues: [issue(1000, 'in-progress', { comments: [started, early] })],
    reviews: [review(1001, { draft: true, checks: [{ name: 'test', status: 'passed', attempt: 1 }] })],
  }))
  const board = await fetchBoard(host, { root: '/x', main: '/x' })
  expect(board.needs.map(row => [row.key, row.state, row.resume, row.actions])).toEqual([
    ['stopped-1000', 'stopped', 'cd /w/issue-1000 && claude --resume bbbb-2222',
      ['open', 'resume', 'restart', 'close']],
  ])
})

test('a finished draft that conflicts with main shows so and Fix, and one that also fails a check shows its error',
  async () => {
  const finished = (number: number) => ({ comments: [
    { body: `An agent started on \`agent/issue-${number}\`.`, createdAt: '2026-11-08T09:00:00Z' },
    { body: `The agent finished: https://example.test/${number + 1} (draft).`, createdAt: '2026-11-08T10:00:00Z' },
  ] })
  const log = '/state/agent-desk/checks/org__game/42.log'
  // A failed merge check, or the branch watch alone, marks the draft conflicting.
  const reviews = [
    review(1971, { draft: true, mergeable: 'CONFLICTING',
      checks: [{ name: 'merge', status: 'failed', attempt: 1, log: '/x.log' }] }),
    review(1973, { draft: true, mergeable: 'CONFLICTING', checks: [{ name: 'test', status: 'passed', attempt: 1 }] }),
    review(1975, { draft: true, mergeable: 'CONFLICTING',
      checks: [{ name: 'test', status: 'failed', attempt: 1, log }] }),
  ]
  const host = storeHost(storeOf({
    issues: [1970, 1972, 1974].map(number => issue(number, 'in-progress', finished(number))), reviews,
  }), argv => (argv[2] === `exec python3 -B ${FAILURES} log "$1"` ? 'Game/Board.cs:7: expected 3, got 4\n' : ''))
  const board = await fetchBoard(host, { root: '/x', main: '/x' })
  expect(board.needs.map(row => [row.key, row.state, row.text, row.note, row.conflicts, row.actions])).toEqual([
    ['draft-1971', 'draft', 'draft  PR 1971  (conflicts with main)', 'conflicts with main', true, ['open', 'fix']],
    ['draft-1973', 'draft', 'draft  PR 1973  (conflicts with main)', 'conflicts with main', true, ['open', 'fix']],
    ['draft-1975', 'draft', 'draft  PR 1975  (test failed: Board.cs:7: expected 3, got 4)',
      'test failed: Board.cs:7: expected 3, got 4', true, ['open', 'fix']],
  ])
})

test('the pane and the page read the store anew on each refresh, with no shared claim, less '
  + 'this host\'s dismissed runs', async () => {
  const runs = [{ id: 6, kind: 'chore', target: 'audit', status: 'failed', unit: 'u6.service', started_at: null,
    ended_at: '2026-10-29T10:30:00Z' }, { id: 7, kind: 'chore', target: 'tidy', status: 'failed', unit: 'u7.service',
    started_at: null, ended_at: '2026-10-29T10:40:00Z' }]
  const host = { ...storeHost(storeOf({ runs })), get: async () => [-6] }
  const board = await fetchBoard(host, { root: '/x', main: '/x' })
  expect(board.needs.map(row => row.key)).toEqual(['failed-unit-7'])
  expect(host.asked.some(readsGitHub)).toBe(false)
  expect(host.asked.filter(argv => argv[2] === BOARD_READ).length).toBe(1)
})

test('a store the command can\'t read shows an empty board that says how to see why', async () => {
  const host: Host = { run: async () => null, now: async () => NOW, get: async () => undefined,
    set: async () => undefined }
  const board = await fetchBoard(host, { root: '/x', main: '/x' })
  expect(board.at).toMatch(/^\d\d:\d\d$/)
  expect(board.needs).toEqual([])
  expect(board.error).toBe("agent-desk couldn't read the local store; run `agent-desk board` in the project's "
    + 'checkout to see why.')
})
