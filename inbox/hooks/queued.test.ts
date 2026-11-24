// The tests of Queued's rows: the start order and its moves, Manual's Start, Unpick and picked notes, Hold, waiting
// serial issues, Move to Open, requests that wait for Start, and the wait notes the dispatcher's gates add.
import { expect, test } from 'claude-code/testing'

import type { Row } from '../types'
import type { StoreBoard } from './board.ts'
import { byStartOrder, fetchBoard, serialGroups, startPicks } from './board.ts'
import { issue, review, storeHost, storeOf } from './board.fixtures.ts'
import { moveToQueued } from './moves.ts'

test('a waiting serial issue waits on the holder of its own serial group', async () => {
  const issues = [issue(435, 'in-progress', { labels: ['serial:audio'] }),
    issue(380, 'in-progress', { labels: ['serial'] }), issue(590, 'waiting', { labels: ['serial:audio'] }),
    issue(564, 'waiting', { labels: ['serial:terrain'] }), issue(591, 'waiting', { labels: ['serial'] })]
  const board = await fetchBoard(storeHost(storeOf({ issues })), { root: '/x', main: '/x' })
  expect(board.queued.map(row => [row.key, row.note])).toEqual([['waiting-564', 'waiting on the serial slot'],
    ['waiting-590', 'waiting on #435'], ['waiting-591', 'waiting on #380']])
  expect([...serialGroups(new Set(['serial:audio', 'serial:', 'bug', 'serial']))]).toEqual(['serial:audio', 'serial'])
})

test('an issue blocked by open issues can be queued, and waits in Queued on its blockers', async () => {
  const blockers = (...numbers: number[]) => [...numbers.map(number => ({ number, state: 'OPEN' })),
    { number: 9, state: 'CLOSED' }]
  const issues = [issue(70), issue(80, 'open', { blockedBy: blockers(70), listPosition: 1 }),
    issue(81, 'on-hold', { blockedBy: blockers(70), listPosition: 2 }),
    issue(82, 'waiting', { blockedBy: blockers(70) }),
    issue(83, 'waiting', { labels: ['serial'], blockedBy: blockers() })]
  const board = await fetchBoard(storeHost(storeOf({ issues })), { root: '/x', main: '/x' })
  expect(board.blocked.map(row => [row.key, row.note, row.actions])).toEqual([
    ['blocked-80', 'blocked by #70', ['open', 'ready', 'unblock', 'close', 'down', 'bottom']],
    ['blocked-81', 'blocked by #70', ['open', 'unpause', 'unblock', 'close', 'up', 'top']],
  ])
  expect(board.queued.map(row => [row.key, row.note])).toEqual([['waiting-82', 'waiting on #70'],
    ['waiting-83', 'waiting on the serial slot']])

  // Agent-ready moves a blocked issue to Queued under its own title.
  const moved = moveToQueued(board, board.blocked[0] as Row)
  expect(moved.blocked.map(row => row.key)).toEqual(['blocked-81'])
  expect(moved.queued.find(row => row.key === 'queued-80')?.text).toBe('Issue 80')
})

test('a waiting issue in no serial group whose blockers closed reads only why the dispatcher holds its request',
  async () => {
  const issues = [issue(85, 'waiting', { blockedBy: [{ number: 70, state: 'CLOSED' }] })]
  const agent = (extra: object) => [{ id: 700, kind: 'agent', target: '85', status: 'requested', unit: null,
    started_at: null, labelled: true, ...extra }]
  const board = async (parts: Partial<StoreBoard>) => fetchBoard(storeHost(storeOf({ issues, ...parts })),
    { root: '/x', main: '/x' })
  const held = await board({ runs: agent({ gate: 'slot', reason: 'a free agent slot' }) })
  expect(held.queued.map(row => [row.key, row.text, row.note])).toEqual([
    ['waiting-85', 'Issue 85  (waits for a free agent slot)', 'waits for a free agent slot']])
  const unheld = await board({ runs: agent({}) })
  expect(unheld.queued.map(row => [row.key, row.text, row.note])).toEqual([['waiting-85', 'Issue 85', undefined]])
  // In Manual, it has no Start button, so it never waits for Start, and a picked one says only that.
  const manual = await board({ hold: { state: 'manual', picked: [] } })
  expect(manual.queued.map(row => [row.text, row.note, row.actions])).toEqual([
    ['Issue 85', undefined, ['open', 'to-open', 'close']]])
  const picked = await board({ hold: { state: 'manual', picked: ['org/game#85'] } })
  expect(picked.queued.map(row => [row.text, row.note, row.unpicked])).toEqual([['Issue 85  (picked)', 'picked', '']])
})

test('in Manual, a queued issue nobody picked waits in Queued for Start', async () => {
  const board = async (hold: unknown) => fetchBoard(storeHost(storeOf({ issues: [issue(90, 'queued'),
    issue(91, 'queued')], hold })), { root: '/x', main: '/x' })
  const manual = await board({ state: 'manual', picked: ['org/game#91', 'org/other#90'] })
  expect(manual.queued.map(row => [row.key, row.note, row.actions])).toEqual([
    ['queued-90', 'waits for Start', ['open', 'start', 'to-open', 'close', 'down', 'bottom']],
    ['queued-91', 'picked', ['open', 'unpick', 'to-open', 'close', 'up', 'top']],
  ])
  // A picked row keeps the note it reads once Unpick drops its pick; an unpicked one has none to keep.
  expect(manual.queued.map(row => [row.text, row.unpicked])).toEqual([['Issue 90  (waits for Start)', undefined],
    ['Issue 91  (picked)', 'waits for Start']])
  const auto = await board({ state: 'running', picked: [] })
  expect(auto.queued.map(row => row.actions)).toEqual([['open', 'to-open', 'close', 'down', 'bottom'],
    ['open', 'to-open', 'close', 'up', 'top']])
  const autoHere = await board({ state: 'manual', picked: [], projects: { 'org/game': 'running' } })
  expect(autoHere.queued.map(row => row.actions)).toEqual([['open', 'to-open', 'close', 'down', 'bottom'],
    ['open', 'to-open', 'close', 'up', 'top']])
  expect(startPicks({ state: 'running', picked: [], projects: { 'org/game': 'manual' } }, 'org/game'))
    .toEqual({ picked: new Set(), runs: new Set(), held: false })
  expect(startPicks(null)).toBeNull()
  expect(startPicks('manual')).toBeNull()
})

test('on Hold, every queued issue nobody picked shows Start, and a picked one starts on Resume', async () => {
  const board = async (hold: unknown) => fetchBoard(storeHost(storeOf({ issues: [issue(90, 'queued'),
    issue(91, 'queued'), issue(92, 'waiting', { labels: ['serial:atlas'] })], hold })), { root: '/x', main: '/x' })
  const held = await board({ state: 'held', before: 'manual', picked: ['org/game#91', 'org/game#92'] })
  expect(held.queued.map(row => [row.key, row.note, row.actions])).toEqual([
    ['queued-90', undefined, ['open', 'start', 'to-open', 'close', 'down', 'bottom']],
    ['queued-91', 'picked; starts on Resume', ['open', 'unpick', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['waiting-92', 'picked; starts on Resume', ['open', 'unpick', 'to-open', 'close', 'up', 'top']],
  ])
  expect(held.queued[1]?.text).toBe('Issue 91  (picked; starts on Resume)')
  // On Hold, an issue nobody picked waits for nothing but Resume, so a queued one unpicked reads no note.
  expect(held.queued.map(row => row.unpicked)).toEqual([undefined, '', 'waiting on the serial slot'])
  const projectHeld = await board({ state: 'running', picked: [], projects: { 'org/game': 'held' } })
  expect(projectHeld.queued.map(row => row.actions)).toEqual([['open', 'start', 'to-open', 'close', 'down', 'bottom'],
    ['open', 'start', 'to-open', 'close', 'up', 'down', 'top', 'bottom'], ['open', 'start', 'to-open', 'close', 'up', 'top']])
  expect(startPicks({ state: 'held', picked: ['org/game#91'] }, 'org/game'))
    .toEqual({ picked: new Set(['org/game#91']), runs: new Set(), held: true })
  expect(startPicks({ state: 'manual', picked: [], pickedRuns: [4, 'x', 9] })?.runs).toEqual(new Set([4, 9]))
})

test('in Manual and on Hold, a picked queued or waiting issue says so first and shows Unpick, and in Auto neither',
  async () => {
  // #91 waits on its blocker and #92 on its serial group's slot.
  const issues = [issue(90, 'queued'), issue(91, 'waiting', { blockedBy: [{ number: 70, state: 'OPEN' }] }),
    issue(92, 'waiting', { labels: ['serial:atlas'] }), issue(70)]
  const picked = ['org/game#90', 'org/game#91', 'org/game#92']
  const board = async (hold: unknown) => fetchBoard(storeHost(storeOf({ issues, hold })), { root: '/x', main: '/x' })
  const manual = await board({ state: 'manual', picked })
  expect(manual.queued.map(row => [row.key, row.note, row.unpicked, row.actions])).toEqual([
    ['queued-90', 'picked', 'waits for Start', ['open', 'unpick', 'to-open', 'close', 'down', 'bottom']],
    ['waiting-91', 'picked; waiting on #70', 'waiting on #70', ['open', 'unpick', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['waiting-92', 'picked; waiting on the serial slot', 'waits for Start',
      ['open', 'unpick', 'to-open', 'close', 'up', 'top']],
  ])
  const held = await board({ state: 'running', picked, projects: { 'org/game': 'held' } })
  expect(held.queued.map(row => row.note)).toEqual(['picked; starts on Resume', 'picked; waiting on #70',
    'picked; starts on Resume'])
  // A pick left over in Auto means nothing, so nothing shows it.
  const auto = await board({ state: 'running', picked })
  expect(auto.queued.map(row => [row.note, row.unpicked, row.actions.includes('unpick')])).toEqual([
    [undefined, undefined, false], ['waiting on #70', undefined, false], ['waiting on the serial slot', undefined, false]])
})

test('every queued or waiting issue in Queued shows Move to Open and Close, and a row back to its PR or fixing it '
  + 'keeps Cancel instead', async () => {
  // #8 is in progress with a draft PR its agent goes back to, and #9 with a PR its agent fixes; #20 is queued and #21
  // waits on its blocker.
  const issues = [issue(8, 'in-progress'), issue(9, 'in-progress'), issue(20, 'queued'),
    issue(21, 'waiting', { blockedBy: [{ number: 70, state: 'OPEN' }] }), issue(70)]
  const runs = [{ id: 4, kind: 'agent', target: '8', status: 'requested', unit: null, started_at: null,
    labelled: false }, { id: 5, kind: 'agent', target: '20', status: 'requested', unit: null, started_at: null,
    labelled: true }, { id: 6, kind: 'agent', target: '9', status: 'requested', unit: null, started_at: null,
    labelled: false }]
  const reviews = [review(30, { branch: 'agent/issue-8', draft: true }), review(31, { branch: 'agent/issue-9' })]
  for (const hold of [{ state: 'running', picked: [] }, { state: 'manual', picked: [] }]) {
    const board = await fetchBoard(storeHost(storeOf({ issues, runs, reviews, hold })), { root: '/x', main: '/x' })
    const actions = Object.fromEntries(board.queued.map(row => [row.key, row.actions]))
    expect(actions['rerun-8']).toEqual(hold.state === 'manual' ? ['open', 'start', 'cancel'] : ['open', 'cancel'])
    expect(actions['rerun-pr-31']).toEqual(hold.state === 'manual' ? ['open', 'start', 'cancel'] : ['open', 'cancel'])
    expect(actions['queued-20']?.slice(-4)).toEqual(['to-open', 'close', 'down', 'bottom'])
    expect(actions['waiting-21']).toEqual(['open', 'to-open', 'close', 'up', 'top'])
  }
})

test('in Manual, a fresh start Retry or Restart asked for shows Start on its issue in Queued until Start picks it',
  async () => {
  // #8 is a stopped issue with a draft PR, Restarted or Retried; #10 is an open issue asked for from the command line.
  const issues = [issue(8, 'in-progress'), issue(10)]
  const runs = [
    { id: 4, kind: 'agent', target: '8', status: 'requested', unit: null, started_at: null, labelled: true },
    { id: 9, kind: 'agent', target: '10', status: 'requested', unit: null, started_at: null, labelled: true },
  ]
  const reviews = [review(30, { branch: 'agent/issue-8', draft: true })]
  const board = async (hold: unknown) => fetchBoard(storeHost(storeOf({ issues, runs, reviews, hold })),
    { root: '/x', main: '/x' })
  const manual = await board({ state: 'manual', picked: [] })
  expect(manual.queued.map(row => [row.key, row.number, row.pr, row.note, row.request, row.actions])).toEqual([
    ['rerun-8', 8, undefined, 'waits for Start', 4, ['open', 'start', 'cancel']],
    ['request-9', 10, undefined, 'waits for Start', 9, ['open', 'start', 'cancel']]])
  expect(manual.queued[0]?.text).toBe('Issue 8  (waits for Start)')
  const picked = await board({ state: 'manual', picked: ['org/game#8', 'org/game#10'] })
  expect(picked.queued.map(row => [row.key, row.note, row.actions])).toEqual([
    ['rerun-8', undefined, ['open', 'cancel']], ['request-9', undefined, ['open', 'cancel']]])
  const held = await board({ state: 'held', picked: ['org/game#8'] })
  expect(held.queued.map(row => [row.key, row.note, row.actions])).toEqual([
    ['rerun-8', 'starts on Resume', ['open', 'cancel']], ['request-9', undefined, ['open', 'start', 'cancel']]])
  // Start on a fresh start's row picks its issue.
  expect(manual.queued.map(row => [row.pick, row.starts])).toEqual([[8, undefined], [10, undefined]])
  // In Auto, the rows stay as they were.
  const auto = await board({ state: 'running', picked: [] })
  expect(auto.queued.map(row => [row.key, row.pr, row.note, row.actions])).toEqual([
    ['rerun-8', true, 'back to its PR', ['open', 'cancel']], ['request-9', undefined, 'waits to start', ['open', 'cancel']]])

  // Retry on an issue whose review is ready shows Start on the review's row in Queued, by its issue.
  const readyReview = await fetchBoard(storeHost(storeOf({ issues, runs: runs.slice(0, 1),
    reviews: [review(30, { branch: 'agent/issue-8' })], hold: { state: 'manual', picked: [] } })),
  { root: '/x', main: '/x' })
  expect(readyReview.queued.map(row => [row.number, row.pr, row.note, row.request, row.actions])).toEqual([
    [8, undefined, 'waits for Start', 4, ['open', 'start', 'cancel']]])
})

test('in Manual, every request back to a PR, Fix, and chore waits in Queued for Start, which picks its requests alone',
  async () => {
  // #682 is in progress with a draft review its comments go back to, twice; #700's review is ready and conflicts, with
  // a fix asked for; a chore waits; #90 is a fresh start of a queued issue.
  const issues = [issue(682, 'in-progress'), issue(700, 'in-progress'), issue(90, 'queued')]
  const reviews = [review(780, { branch: 'agent/issue-682', draft: true }),
    review(701, { mergeable: 'CONFLICTING' })]
  const request = (id: number, kind: string, target: string, labelled = false) => ({ id, kind, target,
    status: 'requested', unit: null, started_at: null, labelled })
  const runs = [request(603, 'agent', '682'), request(604, 'agent', '682'), request(610, 'fix', '701'),
    request(620, 'chore', 'audit'), request(630, 'agent', '90', true)]
  const board = async (hold: unknown) => fetchBoard(storeHost(storeOf({ issues, reviews, runs, hold })),
    { root: '/x', main: '/x' })
  const rows = (shown: Awaited<ReturnType<typeof board>>) => shown.queued.map(row => [row.key, row.note, row.pick,
    row.starts, row.actions])

  const manual = await board({ state: 'manual', picked: [], pickedRuns: [] })
  expect(rows(manual)).toEqual([
    ['rerun-682', 'back to its PR; waits for Start', undefined, [603, 604], ['open', 'start', 'cancel']],
    ['rerun-pr-701', 'fixing conflicts; waits for Start', undefined, [610], ['open', 'start', 'cancel']],
    ['queued-90', 'waits for Start', 90, undefined, ['open', 'start', 'to-open', 'close']],
    ['request-620', 'waits for Start', undefined, [620], ['start', 'cancel']],
  ])
  expect(manual.queued[0]?.text).toBe('Issue 682  (back to its PR; waits for Start)')
  expect(manual.queued.map(row => [row.number, row.pr])).toEqual([[780, true], [701, true], [90, undefined],
    [undefined, undefined]])

  // Start picked #682's requests and the chore's: their rows lose Start, and the rest keep it.
  const picked = await board({ state: 'manual', picked: [], pickedRuns: [603, 604, 620] })
  expect(rows(picked)).toEqual([
    ['rerun-682', 'back to its PR', undefined, undefined, ['open', 'cancel']],
    ['rerun-pr-701', 'fixing conflicts; waits for Start', undefined, [610], ['open', 'start', 'cancel']],
    ['queued-90', 'waits for Start', 90, undefined, ['open', 'start', 'to-open', 'close']],
    ['request-620', 'waits to start', undefined, undefined, ['cancel']],
  ])
  // A request that came after Start waits for it again, on its own.
  const later = await fetchBoard(storeHost(storeOf({ issues, reviews, runs: [...runs, request(605, 'agent', '682')],
    hold: { state: 'manual', picked: [], pickedRuns: [603, 604] } })), { root: '/x', main: '/x' })
  expect(later.queued.find(row => row.key === 'rerun-682')?.starts).toEqual([605])

  // On Hold, Start picks for Resume, and a picked row says it starts then.
  const held = await board({ state: 'held', before: 'manual', picked: [], pickedRuns: [610] })
  expect(rows(held).filter(row => row[0] !== 'queued-90')).toEqual([
    ['rerun-682', 'back to its PR', undefined, [603, 604], ['open', 'start', 'cancel']],
    ['rerun-pr-701', 'fixing conflicts; starts on Resume', undefined, undefined, ['open', 'cancel']],
    ['request-620', 'waits to start', undefined, [620], ['start', 'cancel']],
  ])

  // In Auto, nothing waits for Start.
  const auto = await board({ state: 'running', picked: [], pickedRuns: [] })
  expect(rows(auto)).toEqual([
    ['rerun-682', 'back to its PR', undefined, undefined, ['open', 'cancel']],
    ['rerun-pr-701', 'fixing conflicts', undefined, undefined, ['open', 'cancel']],
    ['queued-90', undefined, undefined, undefined, ['open', 'to-open', 'close']],
    ['request-620', 'waits to start', undefined, undefined, ['cancel']],
  ])
})

test('a waiting serial issue names the queued issue ahead of it, and in Manual one nobody picked waits for Start',
  async () => {
  // #506 waits for Start and #518 behind it; #619 is picked, so #506 holds no slot for it. #700's group is free, and
  // #701 is in no serial group, so it waits on no slot and never for Start.
  const issues = [issue(506, 'queued', { labels: ['serial:atlas'] }), issue(518, 'waiting', { labels: ['serial:atlas'] }),
    issue(619, 'waiting', { labels: ['serial:atlas'] }), issue(700, 'waiting', { labels: ['serial:town'] }),
    issue(701, 'waiting')]
  const board = async (hold: unknown) => fetchBoard(storeHost(storeOf({ issues, hold })), { root: '/x', main: '/x' })
  const manual = await board({ state: 'manual', picked: ['org/game#619'] })
  expect(manual.queued.filter(row => row.key.startsWith('waiting')).map(row => [row.key, row.note, row.actions]))
    .toEqual([
      ['waiting-518', 'waiting on #506, which waits for Start', ['open', 'start', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
      ['waiting-619', 'picked; waiting on the serial slot', ['open', 'unpick', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
      ['waiting-700', 'waits for Start', ['open', 'start', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
      ['waiting-701', undefined, ['open', 'to-open', 'close', 'up', 'top']],
    ])
  // Once picked, #506 holds the slot for every issue behind it.
  const picked = await board({ state: 'manual', picked: ['org/game#506', 'org/game#619'] })
  expect(picked.queued.filter(row => row.key.startsWith('waiting')).map(row => row.note).slice(0, 2))
    .toEqual(['waiting on #506', 'picked; waiting on #506'])
  const auto = await board({ state: 'running', picked: [] })
  expect(auto.queued.filter(row => row.key.startsWith('waiting')).map(row => [row.note, row.actions])).toEqual([
    ['waiting on #506', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['waiting on #506', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['waiting on the serial slot', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    [undefined, ['open', 'to-open', 'close', 'up', 'top']]])
})

test('Queued lists the agents sent back to their reviews, oldest request first, then the queued and waiting issues in '
  + 'the start order, with the moves each allows among them', async () => {
  // The store put #12 first, then waiting #5, then #30; #40 holds no place yet, so it follows by number. #9's ready
  // review went back to its agent before #8's draft did, so its row leads.
  const issues = [issue(5, 'waiting', { position: 2, blockedBy: [{ number: 70, state: 'OPEN' }] }),
    issue(8, 'in-progress'), issue(9, 'in-progress'), issue(12, 'queued', { position: 1 }),
    issue(30, 'queued', { position: 3 }), issue(40, 'queued'), issue(70)]
  const request = (id: number, target: string) => ({ id, kind: 'agent', target, status: 'requested', unit: null,
    started_at: null, labelled: false })
  const runs = [request(4, '8'), request(3, '9')]
  const reviews = [review(31, { branch: 'agent/issue-8', draft: true }), review(32, { branch: 'agent/issue-9' })]
  const board = await fetchBoard(storeHost(storeOf({ issues, runs, reviews })), { root: '/x', main: '/x' })
  expect(board.queued.map(row => [row.key, row.actions])).toEqual([
    ['rerun-pr-32', ['open', 'cancel']],
    ['rerun-8', ['open', 'cancel']],
    ['queued-12', ['open', 'to-open', 'close', 'down', 'bottom']],
    ['waiting-5', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['queued-30', ['open', 'to-open', 'close', 'up', 'down', 'top', 'bottom']],
    ['queued-40', ['open', 'to-open', 'close', 'up', 'top']],
  ])
  // A lone issue in the order has nowhere to move.
  const alone = await fetchBoard(storeHost(storeOf({ issues: [issue(12, 'queued', { position: 1 })] })),
    { root: '/x', main: '/x' })
  expect(alone.queued.map(row => row.actions)).toEqual([['open', 'to-open', 'close']])
  expect([issue(9), issue(3, 'queued', { position: 2 }), issue(7, 'queued', { position: 1 }), issue(2)]
    .sort(byStartOrder).map(one => one.number)).toEqual([7, 3, 2, 9])
})

test('a waiting serial issue names the first queued issue of its group in the start order, not the lowest',
  async () => {
  const issues = [issue(506, 'queued', { labels: ['serial:atlas'], position: 2 }),
    issue(507, 'queued', { labels: ['serial:atlas'], position: 1 }),
    issue(518, 'waiting', { labels: ['serial:atlas'], position: 3 })]
  const board = await fetchBoard(storeHost(storeOf({ issues })), { root: '/x', main: '/x' })
  expect(board.queued.find(row => row.key === 'waiting-518')?.note).toBe('waiting on #507')
  // An in-progress holder still comes first, by number.
  const held = await fetchBoard(storeHost(storeOf({ issues: [...issues, issue(600, 'in-progress',
    { labels: ['serial:atlas'] })] })), { root: '/x', main: '/x' })
  expect(held.queued.find(row => row.key === 'waiting-518')?.note).toBe('waiting on #600')
})

test('a Queued card whose request waits for memory, the usage limit, the API budget, or a free slot says so after '
  + 'its own note', async () => {
  const issues = [issue(90, 'queued', { position: 1 }), issue(91, 'waiting', { position: 2,
    blockedBy: [{ number: 506, state: 'OPEN' }] }), issue(92, 'queued', { position: 3 }), issue(10), issue(11)]
  const request = (id: number, target: string, extra: object) => ({ id, kind: 'agent', target, status: 'requested',
    unit: null, started_at: null, labelled: true, ...extra })
  const runs = [request(1, '90', { gate: 'slot', reason: 'a free agent slot' }),
    request(2, '91', { gate: 'memory', reason: 'memory: 5.3 GB free of 1 GB + 8 GB reserve' }),
    request(3, '92', { gate: 'mode', reason: 'Start' }),
    request(4, '10', { labelled: false, gate: 'usage', reason: 'weekly usage to fall under its limit' }),
    request(5, '11', { labelled: false, gate: 'usage', reason: 'the API budget: $201.40 of $200.00 this month' })]
  const board = await fetchBoard(storeHost(storeOf({ issues, runs, hold: { state: 'manual', picked: ['org/game#90',
    'org/game#91'], pickedRuns: [4, 5] } })), { root: '/x', main: '/x' })
  expect(board.queued.map(row => [row.key, row.note])).toEqual([
    ['queued-90', 'picked; waits for a free agent slot'],
    ['waiting-91', 'picked; waiting on #506; waits for memory: 5.3 GB free of 1 GB + 8 GB reserve'],
    // The desk's mode shows in the card's own note already.
    ['queued-92', 'waits for Start'],
    ['request-4', 'waits to start; waits for weekly usage to fall under its limit'],
    ['request-5', 'waits to start; waits for the API budget: $201.40 of $200.00 this month']])
  expect(board.queued[1]?.text)
    .toBe('Issue 91  (picked; waiting on #506; waits for memory: 5.3 GB free of 1 GB + 8 GB reserve)')
})

test('a request sent to an on-hold issue\'s agent waits in Queued for the issue to resume, with Resume', async () => {
  const issues = [issue(11, 'on-hold')]
  const runs = [{ id: 5, kind: 'agent', target: '11', status: 'requested', unit: null, started_at: null,
    gate: 'hold', reason: '#11 to resume' }]
  const board = await fetchBoard(storeHost(storeOf({ issues, runs })), { root: '/x', main: '/x' })
  expect(board.blocked.map(row => [row.key, row.note, row.actions])).toEqual([
    ['blocked-11', 'on hold', ['open', 'unpause', 'unblock', 'close']]])
  expect(board.queued.map(row => [row.key, row.note, row.actions])).toEqual([
    ['request-5', 'waits to start; waits for #11 to resume', ['open', 'unpause', 'cancel']]])
})
