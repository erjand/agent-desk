// Run with: node --test web/
// First, so the server's modules read the temporary folders it sets.
import { TEST_HOME } from './test-env.ts'
import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { Board, Row, RowAction } from '../inbox/types'
import { DESK } from '../inbox/hooks/run.ts'
import { act, unblock, undraft } from './card-actions.ts'
import { loop, rebuild } from './server.ts'
import { host } from './state.ts'
import type { Desk } from './state.ts'

test('Unblock moves a Blocked card to Open at once, and puts it back when the store refuses',
  async () => {
  const parked: Row = { key: 'blocked-50', text: 'Switch the GPS clock  (on hold)', number: 50,
    title: 'Switch the GPS clock', note: 'on hold', actions: ['open', 'unblock', 'close'] }
  const board: Board = { at: '', runners: [], needs: [], merging: [], design: [], designTotal: 0, queued: [],
    next: [], blocked: [parked], backlog: [], ideas: [], merged: [], chores: [], error: null }
  const desk = { id: 'game', repo: 'me/game', owner: 'me', title: 'Game', main: '/x', chores: [], board, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {}, modelsFor: '',
    known: null, refreshes: 0, commands: 0 } as Desk
  const asked: string[][] = []
  let edited: string | null = ''
  const real = host.run
  host.run = async argv => {
    asked.push(argv)
    return argv[5] === 'view' ? '{"status": "on-hold", "blockedBy": []}' : edited
  }
  try {
    assert.equal(await unblock(loop, desk, parked), true)
    assert.deepEqual(asked.filter(argv => argv[5] === 'edit').map(argv => argv.slice(4)),
      [['issue', 'edit', '50', '--remove-label', 'on-hold']])
    assert.deepEqual(desk.board?.blocked, [])
    assert.equal(desk.board?.next[0]?.key, 'next-50')

    desk.board = board
    edited = null
    assert.equal(await unblock(loop, desk, parked), false)
    assert.equal(desk.board, board)
  } finally {
    host.run = real
  }
})

test('Ready shows a draft card as a PR to review at once, and puts it back when the store refuses',
  async () => {
  const draft: Row = { key: 'draft-45', text: 'draft  Mark the PR ready', number: 45, issue: 44, pr: true,
    url: 'https://example.test/45', title: 'Mark the PR ready', state: 'draft', since: '2026-11-11T09:00:00Z',
    actions: ['open', 'undraft'] }
  const board: Board = { at: '', runners: [], needs: [draft], merging: [], design: [], designTotal: 0, queued: [],
    next: [], blocked: [], backlog: [], ideas: [], merged: [], chores: [], error: null }
  const desk = { id: 'agents', repo: 'me/agents', owner: 'me', title: 'Agents', main: '/x', chores: [], board, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {}, modelsFor: '',
    known: null, refreshes: 0, commands: 0 } as Desk
  const asked: string[][] = []
  let answer: string | null = ''
  const real = host.run
  host.run = async argv => {
    asked.push(argv)
    return answer
  }
  try {
    assert.equal(await undraft(loop, desk, draft), true)
    assert.deepEqual(asked.map(argv => argv.slice(4)), [['review', 'ready', '45']])
    assert.deepEqual(desk.board?.needs.map(row => [row.key, row.state, row.text, row.actions]),
      [['pr-45', 'review', 'review  Mark the PR ready', ['open', 'merge']]])

    desk.board = board
    answer = null
    assert.equal(await undraft(loop, desk, draft), false)
    assert.equal(desk.board, board)
  } finally {
    host.run = real
  }
})

/** A promise and the function that resolves it, for holding a stubbed command until a test lets it answer. */
function gate<T>(): { done: Promise<T>; open: (value: T) => void } {
  let open: (value: T) => void = () => {}
  const done = new Promise<T>(resolve => {
    open = resolve
  })
  return { done, open }
}

test('Merge moves a PR to Merging at once with no kept move, and no read begun before the queue answered lands '
  + 'over it; the read after shows a PR the queue dropped back in Needs You', async () => {
  const review = { number: 61, title: 'Model fresh food', branch: 'agent/issue-60', base: 'main', head: 'h61',
    draft: false, createdAt: '2026-11-11T09:00:00Z', checks: [], mergeable: 'MERGEABLE' }
  const store = JSON.stringify({ repo: 'me/game', issues: [], reviews: [review], merged: [], runs: [] })
  const pr: Row = { key: 'pr-61', text: 'review  Model fresh food', number: 61, pr: true, issue: 60,
    title: 'Model fresh food', state: 'review', actions: ['open', 'merge'] }
  const board: Board = { at: '', runners: [], needs: [pr], merging: [], design: [], designTotal: 0, queued: [],
    next: [], blocked: [], backlog: [], ideas: [], merged: [], chores: [], error: null }
  const desk = { id: 'game', repo: 'me/game', owner: 'me', title: 'Game', main: TEST_HOME, chores: [], board, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {}, modelsFor: '',
    schedulePr: null, known: null, refreshes: 0, commands: 0 } as Desk
  // Each board read and each merge waits for the gate the test set for it, if any.
  let reading: ReturnType<typeof gate<void>> | null = null
  let merging = gate<string | null>()
  let reads = 0
  const real = host.run
  host.run = async argv => {
    if (argv[2] === `exec "${DESK}" board --json`) {
      reads += 1
      await reading?.done
      return store
    }
    return argv[2]?.includes(' merges add 61') ? merging.done : ''
  }
  // Lets every pending callback run, so a read under way gets as far as its gated command.
  const settle = () => new Promise(resolve => setTimeout(resolve, 20))
  try {
    // A read starts, the person presses Merge, and the read answers with the store as it stood before the queue saved.
    reading = gate()
    const early = rebuild(desk)
    await settle()
    const acting = act(loop, desk, pr, 'merge')
    assert.deepEqual(desk.board?.merging.map(row => [row.key, row.note]), [['merging-61', 'queued']])
    assert.deepEqual(desk.board?.needs, [])
    reading.open()
    reading = null
    await early
    assert.deepEqual(desk.board?.merging.map(row => row.key), ['merging-61'])
    // No read starts while the merge runs.
    const before = reads
    await rebuild(desk)
    assert.equal(reads, before)
    assert.deepEqual(desk.board?.merging.map(row => row.key), ['merging-61'])

    // The queue took the PR, then dropped it; the store shows it open, so the read after the merge shows it in Needs
    // You.
    merging.open('')
    await acting
    assert.deepEqual(desk.board?.needs.map(row => row.key), ['pr-61'])
    assert.deepEqual(desk.board?.merging, [])

    // A merge the queue refuses puts the PR back in Needs You before the read after it lands.
    desk.board = board
    merging = gate()
    reading = gate()
    const refused = act(loop, desk, pr, 'merge')
    merging.open(null)
    await settle()
    assert.equal(desk.board, board)
    reading.open()
    await refused
    assert.deepEqual(desk.board?.needs.map(row => row.key), ['pr-61'])
  } finally {
    host.run = real
  }
})

test('Unpick shows Start on a picked card at once, and puts the card back before the read after when lib/hold.py '
  + 'refuses', async () => {
  const picked: Row = { key: 'queued-46', text: 'Pave the trail  (picked)', number: 46, title: 'Pave the trail',
    note: 'picked', unpicked: 'waits for Start', actions: ['open', 'unpick', 'to-open'] }
  const board: Board = { at: '', runners: [], needs: [], merging: [], design: [], designTotal: 0, queued: [picked],
    next: [], blocked: [], backlog: [], ideas: [], merged: [], chores: [], error: null }
  const desk = { id: 'game', repo: 'me/game', owner: 'me', title: 'Game', main: TEST_HOME, chores: [], board, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {}, modelsFor: '',
    schedulePr: null, known: null, refreshes: 0, commands: 0 } as Desk
  // The board read answers with the card still picked, and the unpick waits for the gate the test set for it.
  const store = JSON.stringify({ repo: 'me/game', issues: [{ number: 46, title: 'Pave the trail', status: 'queued',
    labels: [], blockedBy: [], createdAt: '2026-11-11T09:00:00Z' }], reviews: [], merged: [], runs: [],
  hold: { state: 'manual', picked: ['me/game#46'] } })
  let reading: ReturnType<typeof gate<void>> | null = null
  let unpicking = gate<string | null>()
  const asked: string[][] = []
  const real = host.run
  host.run = async argv => {
    asked.push(argv)
    if (argv[2] === `exec "${DESK}" board --json`) {
      await reading?.done
      return store
    }
    return argv[2]?.includes(' unpick ') ? unpicking.done : ''
  }
  const settle = () => new Promise(resolve => setTimeout(resolve, 20))
  try {
    const acting = act(loop, desk, picked, 'unpick')
    assert.deepEqual(desk.board?.queued.map(row => [row.key, row.note, row.pick, row.actions]),
      [['queued-46', 'waits for Start', 46, ['open', 'start', 'to-open']]])
    unpicking.open('')
    await acting
    assert.ok(asked.some(argv => argv[2]?.endsWith(' unpick "$1"') && argv[4] === '46'))

    // The issue's run claimed it since the board's read, so lib/hold.py refuses, and the card goes back before the read
    // after the command lands.
    desk.board = board
    unpicking = gate()
    reading = gate()
    const refused = act(loop, desk, picked, 'unpick')
    unpicking.open(null)
    await settle()
    assert.equal(desk.board, board)
    reading.open()
    await refused
  } finally {
    host.run = real
  }
})

test('the read after each button that writes the store shows its row where the store puts it, Fix\'s PR in Queued '
  + 'from the comment the dispatcher hasn\'t answered', async () => {
  type Item = Record<string, unknown> & { number: number }
  const issue = (number: number, title: string, status: string, more: Record<string, unknown> = {}): Item =>
    ({ number, title, status, labels: [], blockedBy: [], createdAt: `2026-11-${10 + (number % 10)}T09:00:00Z`,
      updatedAt: '2026-11-11T09:00:00Z', ...(status === 'in-progress' ? { comments: [], landed: null } : {}), ...more })
  const review = (number: number, title: string, branch: string, more: Record<string, unknown> = {}): Item =>
    ({ number, title, branch, base: 'main', head: `h${number}`, draft: false, createdAt: '2026-11-11T09:00:00Z',
      checks: [{ name: 'test', status: 'passed' }], mergeable: 'MERGEABLE', ...more })
  // The store as `agent-desk board --json` prints it, which each command below changes the way the store does before
  // the command returns.
  const issues: Item[] = [issue(64, 'Tidy the sound board', 'open'), issue(19, 'Windows builds', 'needs-design'),
    issue(50, 'Switch the GPS clock', 'on-hold'), issue(66, 'Drop the old maps', 'open'),
    issue(44, 'Mark the PR ready', 'in-progress', { comments: [{ body: 'The agent finished on `agent/issue-44`.',
      createdAt: '2026-11-11T10:00:00Z' }] }),
    issue(61, 'Model trail mix', 'in-progress'), issue(70, 'Study the sprite pipeline', 'open', { labels: ['idea'] })]
  const reviews: Item[] = [review(45, 'Mark the PR ready', 'agent/issue-44', { draft: true }),
    review(62, 'Model trail mix', 'agent/issue-61', { mergeable: 'CONFLICTING' })]
  const runs: Record<string, unknown>[] = []
  const asked: number[] = []
  const store = () => JSON.stringify({ repo: 'me/game', issues, reviews, merged: [], runs, asked })
  const find = (items: Item[], number: number) => items.find(one => one.number === number) as Item
  const desk = { id: 'game', repo: 'me/game', owner: 'me', title: 'Game', main: TEST_HOME, chores: [], board: null,
    at: 0, refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    modelsFor: '', schedulePr: null, known: null, refreshes: 0, commands: 0 } as unknown as Desk
  const real = host.run
  host.run = async argv => {
    if (argv[2] === `exec "${DESK}" board --json`) {
      return store()
    }
    const [noun, verb, number = '', ...flags] = argv.slice(4)
    const line = `${noun} ${verb} ${flags.join(' ')}`
    if (line === 'issue edit --add-label agent-ready') {
      find(issues, Number(number)).status = 'queued'
    } else if (line === 'issue edit --remove-label needs-design' || line === 'issue edit --remove-label on-hold') {
      find(issues, Number(number)).status = 'open'
    } else if (line === 'issue edit --remove-label idea') {
      find(issues, Number(number)).labels = []
    } else if (noun === 'issue' && verb === 'view') {
      return JSON.stringify({ status: find(issues, Number(number)).status, blockedBy: [] })
    } else if (noun === 'issue' && verb === 'close') {
      issues.splice(issues.indexOf(find(issues, Number(number))), 1)
    } else if (noun === 'review' && verb === 'ready') {
      find(reviews, Number(number)).draft = false
    } else if (line === 'review comment --to-agent --body-file -') {
      // The store holds the comment, and the board lists its review as asked until the dispatcher answers it.
      asked.push(Number(number))
    }
    return ''
  }
  const rowOf = (key: string) => desk.board && Object.entries(desk.board)
    .find(([, rows]) => Array.isArray(rows) && rows.some(one => typeof one === 'object' && one.key === key))?.[0]
  const press = async (key: string, action: RowAction) => {
    const row = Object.values(desk.board ?? {}).flatMap(rows => (Array.isArray(rows) ? rows : []))
      .find(one => typeof one === 'object' && one.key === key) as Row
    assert.ok(row, `the board shows ${key}`)
    await act(loop, desk, row, action)
  }
  try {
    await rebuild(desk)
    assert.deepEqual([rowOf('next-64'), rowOf('decide-19'), rowOf('blocked-50'), rowOf('next-66'), rowOf('draft-45'),
      rowOf('pr-62'), rowOf('idea-70')], ['next', 'design', 'blocked', 'next', 'needs', 'needs', 'ideas'])

    await press('next-64', 'ready')
    assert.equal(rowOf('queued-64'), 'queued')
    assert.equal(rowOf('next-64'), undefined)
    await press('decide-19', 'designed')
    assert.equal(rowOf('next-19'), 'next')
    assert.equal(rowOf('decide-19'), undefined)
    await press('idea-70', 'to-open')
    assert.equal(rowOf('next-70'), 'next')
    assert.equal(rowOf('idea-70'), undefined)
    await press('blocked-50', 'unblock')
    assert.equal(rowOf('next-50'), 'next')
    assert.equal(rowOf('blocked-50'), undefined)
    await press('draft-45', 'undraft')
    assert.equal(rowOf('pr-45'), 'needs')
    assert.equal(rowOf('draft-45'), undefined)
    await press('next-66', 'close')
    assert.equal(rowOf('next-66'), undefined)

    await press('pr-62', 'fix')
    assert.equal(rowOf('rerun-pr-62'), 'queued')
    assert.equal(rowOf('pr-62'), undefined)
    // The dispatcher answers the comment with a request for #61's run, and the PR stays in Queued.
    asked.splice(0)
    runs.push({ id: 7, kind: 'agent', target: '61', status: 'requested', unit: null, started_at: null })
    await rebuild(desk)
    assert.equal(rowOf('rerun-pr-62'), 'queued')
  } finally {
    host.run = real
  }
})
