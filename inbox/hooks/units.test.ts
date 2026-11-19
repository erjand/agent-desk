import { expect, test } from 'claude-code/testing'

import { ago } from './board.ts'
import { branchIssues, checkRows, deskRuns, gpuUse, lastFailure, runningChecks, slotCounts, timeLeft, unitFailures,
  unitRequests, unitRows, waitingRequest } from './units.ts'
import type { DeskRun, RunningCheck, UnitContext } from './units.ts'

const NOW = Date.parse('2026-10-29T12:00:00Z')

const item = (number: number, title: string) => ({ number, title, url: `https://example.test/${number}` })
const ISSUES = new Map([7, 8, 9, 10].map(number => [number, item(number, `Issue ${number}`)]))

/**
 * What the board read: open issues 7 to 10, 7 labelled serial; open PR 30 of issue 9's agent branch; PR 31, which
 * merged; and the open PR 32 of no agent's branch.
 */
function context(): UnitContext {
  const prs = new Map([[30, item(30, 'PR for issue 9')], [31, item(31, 'Merged PR')], [32, item(32, 'Hand PR')]])
  const issueOfPr = new Map([[30, 9], [31, 8]])
  return {
    issues: ISSUES,
    prs,
    openPrs: new Set([30, 32]),
    issueOfPr,
    serial: new Set([7]),
    refOf: number => {
      const issue = number === undefined ? undefined : issueOfPr.get(number)
      return number !== undefined && prs.has(number) ? { number, pr: true, ...(issue === undefined ? {} : { issue }) }
        : { number }
    },
    workRef: number => (number === 9 ? { number: 30, issue: 9, pr: true, url: 'https://example.test/30' } : { number }),
    timeLeft: (workflow, started, _issue, benchmark, limit) => timeLeft(NOW, workflow, started, benchmark, limit),
    ago: stamp => ago(NOW, stamp),
  }
}

function desk(id: number, kind: string, target: string, status: string, more: Partial<DeskRun> = {}): DeskRun {
  return { id, kind, target, status, unit: null, started_at: null, ...more }
}

test('a unit at work shows under its kind, by its issue\'s or PR\'s title, with how long it has left', () => {
  const rows = unitRows([
    desk(3, 'agent', '7', 'running', { started_at: '2026-10-29T09:20:00Z' }),
    desk(4, 'agent', 'pr-30', 'running', { started_at: '2026-10-29T11:00:00Z' }),
    desk(5, 'chore', 'audit', 'running', { started_at: '2026-10-29T11:48:00Z' }),
    desk(6, 'fix', '32', 'running', { started_at: '2026-10-29T10:45:00Z' }),
    desk(8, 'agent', '10', 'running', { started_at: '2026-10-29T07:20:00Z', benchmark: true }),
    desk(9, 'agent', '8', 'requested'),
    desk(10, 'agent', '7', 'failed'),
  ], context())
  expect(rows.map(row => [row.key, row.text, row.runner, row.state, row.number, row.issue, row.pr, row.note,
    row.time, row.url])).toEqual([
    ['unit-3', 'agent     Issue 7  (serial · 20 min left)', 'agent', 'working', 7, undefined, undefined,
      'serial · 20 min left', '3h', 'https://example.test/7'],
    ['unit-4', 'agent     PR for issue 9', 'agent', 'working', 30, 9, true, undefined, '1h',
      'https://example.test/30'],
    ['unit-5', 'chore     chore: audit', 'chore', 'working', undefined, undefined, undefined, undefined, '12m',
      undefined],
    ['unit-6', 'fix       Hand PR  (fix · 15 min left)', 'fix', 'working', 32, undefined, true, 'fix · 15 min left',
      '1h', 'https://example.test/32'],
    ['unit-8', 'agent     Issue 10  (20 min left)', 'agent', 'working', 10, undefined, undefined, '20 min left', '5h',
      'https://example.test/10'],
  ])
  // Pause stops an agent on an issue, and leaves an agent sent to a PR, a chore, and a fix alone.
  expect(rows.map(row => row.actions)).toEqual([['open', 'pause'], ['open'], [], ['open'], ['open', 'pause']])
  expect(rows[0]?.since).toBe('2026-10-29T09:20:00Z')
})

test('a unit at work says how long its GPU calls held and queued for the GPU, and nothing while it used none', () => {
  const rows = unitRows([
    desk(3, 'agent', '7', 'running', { started_at: '2026-10-29T09:20:00Z', gpu_held: 12.4, gpu_queued: 9.2 }),
    desk(5, 'chore', 'audit', 'running', { started_at: '2026-10-29T11:48:00Z', gpu_held: 3, gpu_queued: 0 }),
    desk(6, 'chore', 'refactor', 'running', { started_at: '2026-10-29T11:48:00Z' }),
  ], context())
  expect(rows.map(row => row.note)).toEqual(['serial · 20 min left · Held GPU for 12 mins, waited 9 mins for it',
    'Held GPU for 3 mins', undefined])
  expect(rows[0]?.text).toBe('agent     Issue 7  (serial · 20 min left · Held GPU for 12 mins, waited 9 mins for it)')
})

test('GPU minutes read whole, a part of one reads as under one, and no hold reads as no GPU use', () => {
  expect(gpuUse(0.2, 0.4)).toBe('Held GPU for <1 min, waited <1 min for it')
  expect(gpuUse(0, 5)).toBe('Held GPU for 0 mins, waited 5 mins for it')
  expect(gpuUse(61.5, 0)).toBe('Held GPU for 62 mins')
  expect(gpuUse(1.2, 0.6)).toBe('Held GPU for 1 min, waited 1 min for it')
  expect(gpuUse(undefined, 3)).toBe(undefined)
  expect(gpuUse(null, null)).toBe(undefined)
})

test('an agent at work on an issue with an open review names the review and the issue, and links to the review', () => {
  const [row] = unitRows([desk(3, 'agent', '9', 'running')], context())
  expect([row?.title, row?.number, row?.issue, row?.pr, row?.url]).toEqual(['Issue 9', 30, 9, true,
    'https://example.test/30'])
})

test('a unit on a number the board doesn\'t know shows the number, without a link', () => {
  const [row] = unitRows([desk(3, 'agent', '44', 'running')], context())
  expect([row?.title, row?.number, row?.url, row?.time, row?.actions]).toEqual(['#44', 44, undefined, undefined,
    ['pause']])
})

test('a unit that failed, timed out, or vanished shows in Needs You while its issue or PR is open', () => {
  const rows = unitFailures([
    desk(6, 'agent', '9', 'failed', { ended_at: '2026-10-29T10:30:00Z' }),
    desk(7, 'chore', 'audit', 'timed-out', { ended_at: '2026-10-29T11:00:00Z' }),
    desk(8, 'fix', '30', 'lost'),
    // Its issue closed since, so the failure has nothing left to retry.
    desk(11, 'agent', '44', 'failed'),
    // A merged PR has nothing left to fix.
    desk(12, 'fix', '31', 'failed'),
    desk(13, 'agent', '7', 'running'),
  ], context())
  expect(rows.map(row => [row.key, row.text, row.run, row.number, row.issue, row.state, row.note, row.since,
    row.actions])).toEqual([
    ['failed-unit-6', 'failed  agent: Issue 9', -6, 9, undefined, 'failed', 'agent', '2026-10-29T10:30:00Z',
      ['open', 'retry', 'dismiss']],
    ['failed-unit-7', 'timed out  chores: audit', -7, undefined, undefined, 'timed out', 'chores',
      '2026-10-29T11:00:00Z', ['retry', 'dismiss']],
    ['failed-unit-8', 'failed  fix: PR for issue 9', -8, 30, 9, 'failed', 'fix', undefined,
      ['open', 'retry', 'dismiss']],
  ])
})

test('Retry continues only an agent\'s timed-out run on an issue, which takes up its session again', () => {
  const rows = unitFailures([
    desk(6, 'agent', '9', 'timed-out'),
    desk(7, 'agent', '7', 'failed'),
    desk(8, 'chore', 'audit', 'timed-out'),
    desk(9, 'fix', '30', 'timed-out'),
    desk(10, 'agent', 'idea-8', 'timed-out'),
  ], context())
  expect(rows.map(row => [row.key, row.continues])).toEqual([['failed-unit-6', true], ['failed-unit-7', undefined],
    ['failed-unit-8', undefined], ['failed-unit-9', undefined], ['failed-unit-10', undefined]])
})

test('a failed run whose unit the store names copies its journal command, and opens its issue on the page', () => {
  const rows = unitFailures([
    desk(6, 'agent', '9', 'failed', { unit: 'agent-desk-agent-org-game-9-6.service' }),
    desk(7, 'chore', 'audit', 'timed-out', { unit: 'agent-desk-chore-org-game-audit-7.service' }),
  ], context())
  expect(rows.map(row => [row.key, row.url, row.log, row.actions])).toEqual([
    ['failed-unit-6', 'https://example.test/9', 'journalctl --user -u agent-desk-agent-org-game-9-6.service',
      ['open', 'log', 'retry', 'dismiss']],
    ['failed-unit-7', undefined, 'journalctl --user -u agent-desk-chore-org-game-audit-7.service',
      ['log', 'retry', 'dismiss']],
  ])
})

test('a request waits in Queued by its issue, PR, or chore when no other Queued row shows it', () => {
  const rows = unitRequests([
    desk(4, 'agent', '8', 'requested'),
    desk(9, 'agent', '10', 'requested'),
    desk(10, 'agent', 'pr-30', 'requested'),
    desk(11, 'agent', '44', 'requested'),
    desk(12, 'agent', '7', 'running'),
    desk(13, 'fix', '32', 'requested'),
    desk(14, 'chore', 'audit', 'requested'),
  ], context(), new Set([8, 9]))
  expect(rows.map(row => [row.key, row.text, row.number, row.pr, row.note, row.request, row.actions])).toEqual([
    ['request-9', 'Issue 10  (waits to start)', 10, undefined, 'waits to start', 9, ['open', 'cancel']],
    ['request-13', 'Hand PR  (waits to start)', 32, true, 'waits to start', 13, ['open', 'cancel']],
    ['request-14', 'chore: audit  (waits to start)', undefined, undefined, 'waits to start', 14, ['cancel']]])
})

test('Cancel takes the newest agent or fix request that waits on an issue, and none that works', () => {
  const units = [desk(4, 'agent', '8', 'requested'), desk(6, 'fix', '30', 'requested'),
    desk(7, 'agent', '8', 'running'), desk(9, 'chore', 'audit', 'requested'), desk(2, 'agent', '7', 'requested')]
  const issueOfPr = new Map([[30, 8]])
  expect(waitingRequest(units, 8, issueOfPr)).toBe(6)
  expect(waitingRequest(units, 7, issueOfPr)).toBe(2)
  expect(waitingRequest(units.filter(one => one.status !== 'requested'), 8, issueOfPr)).toBeUndefined()
})

test('an agent or fix unit works the issue its target or its PR\'s branch names', () => {
  const units = [desk(1, 'agent', '7', 'running'), desk(2, 'agent', 'pr-30', 'running'),
    desk(3, 'fix', '32', 'running'), desk(4, 'chore', 'audit', 'running'), desk(5, 'agent', '8', 'requested')]
  const issueOfPr = new Map([[30, 9]])
  expect(branchIssues(units, 'running', issueOfPr)).toEqual([7, 9, 32])
  expect(branchIssues(units, 'requested', issueOfPr)).toEqual([8])
})

test('a run says how long it has left only in its last 30 minutes, and a benchmark agent gets 5 hours', () => {
  expect(timeLeft(NOW, 'agent', '2026-10-29T09:20:00Z')).toBe('20 min left')
  expect(timeLeft(NOW, 'agent', '2026-10-29T11:00:00Z')).toBeUndefined()
  expect(timeLeft(NOW, 'agent', '2026-10-29T07:20:00Z')).toBe('0 min left')
  expect(timeLeft(NOW, 'agent', '2026-10-29T08:00:00Z', true)).toBeUndefined()
  expect(timeLeft(NOW, 'agent', '2026-10-29T07:20:00Z', true)).toBe('20 min left')
  expect(timeLeft(NOW, 'fix', '2026-10-29T10:45:00Z', true)).toBe('15 min left')
  expect(timeLeft(NOW, 'test', '2026-10-29T08:00:00Z')).toBeUndefined()
  expect(timeLeft(NOW, 'agent', undefined)).toBeUndefined()
})

test('a run under its project\'s own limit counts down from that limit, whatever its kind', () => {
  // An agent run under a 6-hour limit, three and a half hours in, has hours left; the built-in 3 hours would end it.
  expect(timeLeft(NOW, 'agent', '2026-10-29T08:30:00Z', false, 360)).toBeUndefined()
  expect(timeLeft(NOW, 'agent', '2026-10-29T08:30:00Z')).toBe('0 min left')
  expect(timeLeft(NOW, 'agent', '2026-10-29T06:20:00Z', false, 360)).toBe('20 min left')
  // The limit the unit runs under counts over the benchmark label's.
  expect(timeLeft(NOW, 'agent', '2026-10-29T04:20:00Z', true, 480)).toBe('20 min left')
  expect(timeLeft(NOW, 'chores', '2026-10-29T11:00:00Z', false, 75)).toBe('15 min left')
  expect(timeLeft(NOW, 'fix', '2026-10-29T11:00:00Z', false, 60)).toBe('0 min left')
  // A shorter limit warns sooner: a fix under 45 minutes, started 20 minutes ago, has 25 left.
  expect(timeLeft(NOW, 'fix', '2026-10-29T11:40:00Z', false, 45)).toBe('25 min left')
  // A limit that isn't a number of minutes above 0 counts as none.
  expect(timeLeft(NOW, 'agent', '2026-10-29T09:20:00Z', false, 0)).toBe('20 min left')
})

test('a working run\'s row counts down from the limit its unit runs under', () => {
  const units = [desk(16, 'agent', '8', 'running', { started_at: '2026-10-29T08:30:00Z', limit: 360 }),
    desk(17, 'agent', '10', 'running', { started_at: '2026-10-29T06:20:00Z', limit: 360 }),
    desk(18, 'agent', '8', 'running', { started_at: '2026-10-29T09:20:00Z' })]
  expect(unitRows(units, context()).map(row => [row.key, row.note])).toEqual([['unit-16', undefined],
    ['unit-17', '20 min left'], ['unit-18', '20 min left']])
})

test('the store\'s list of runs reads as its runs, and anything else as none', () => {
  expect(deskRuns('agent-desk: no project here')).toEqual([])
  expect(deskRuns(null)).toEqual([])
  expect(deskRuns({ id: 1 })).toEqual([])
  expect(deskRuns([{ id: 1, kind: 'fix', target: '4', status: 'running' }, { id: 'x' }]).map(one => one.id))
    .toEqual([1])
})

test('a discussion of an idea shows as a busy agent run by its issue, with its own limit, and Retry once it fails',
  () => {
  // Issue 7 is serial, and a discussion holds no serial slot.
  const units = [desk(14, 'agent', 'idea-7', 'running', { started_at: '2026-10-29T11:45:00Z' }),
    desk(15, 'agent', 'idea-8', 'timed-out', { ended_at: '2026-10-29T11:50:00Z' })]
  expect(unitRows(units, context()).map(row => [row.key, row.text, row.runner, row.state, row.number, row.note,
    row.url])).toEqual([['unit-14', 'agent     Issue 7  (discussion · 15 min left)', 'agent', 'working', 7,
    'discussion · 15 min left', 'https://example.test/7']])
  expect(unitFailures(units, context()).map(row => [row.key, row.text, row.run, row.number, row.note, row.actions]))
    .toEqual([['failed-unit-15', 'timed out  discussion: Issue 8', -15, 8, 'discussion', ['open', 'retry', 'dismiss']]])
  // A discussion works no branch, so its issue counts as neither started nor queued, and Restart never takes it up.
  expect(branchIssues(units, 'running', new Map())).toEqual([])
  expect(lastFailure(units, 8, () => false)).toBeUndefined()
  expect(timeLeft(NOW, 'discussion', '2026-10-29T11:45:00Z')).toBe('15 min left')
})

test('Restart asks again for an issue\'s newest failed agent run, unless an agent went on after it ended', () => {
  const ended = (id: number, target: string, status: string, at: string) => ({ id, kind: 'agent', target, status,
    unit: `u${id}.service`, started_at: null, ended_at: at })
  const units = [ended(4, '7', 'failed', '2026-10-29T09:00:00Z'), ended(6, '7', 'timed-out', '2026-10-29T10:00:00Z'),
    ended(8, '9', 'failed', '2026-10-29T11:00:00Z'), ended(9, 'pr-7', 'failed', '2026-10-29T11:30:00Z'),
    { ...ended(10, '7', 'running', '2026-10-29T11:40:00Z'), ended_at: null }]
  expect(lastFailure(units, 7, () => false)).toBe(-6)
  expect(lastFailure(units, 9, ended => ended < '2026-10-29T11:10:00Z')).toBeUndefined()
  expect(lastFailure(units, 12, () => false)).toBeUndefined()
})

// The slots lib/run_units.py gives a project that sets none.
const DEFAULT_SLOTS = { agent: 3, chore: 1, fix: 1, check: 2, benchmark: 2 }

function check(branch: string, review: number | null, startedAt: string | null = '2026-10-29T11:40:00Z'):
  RunningCheck {
  return { branch, review, head: `${branch}-head`, startedAt }
}

const summary = (counts: { kind: string; busy: number; total: number }[]) =>
  counts.map(one => `${one.kind} ${one.busy}/${one.total}`).join(' · ')

test('a project with nothing running has every kind of slot free', () => {
  expect(summary(slotCounts(DEFAULT_SLOTS, [], []))).toBe('agent 0/3 · chore 0/1 · fix 0/1 · check 0/2')
})

test('the slots count the running units of each kind and the running checks, and leave waiting and failed runs out',
  () => {
    const units = [desk(1, 'agent', '7', 'running'), desk(2, 'agent', '8', 'requested'),
      desk(3, 'fix', '32', 'running'), desk(4, 'chore', 'audit', 'failed')]
    expect(summary(slotCounts(DEFAULT_SLOTS, units, [check('agent/issue-9', 30)])))
      .toBe('agent 1/3 · chore 0/1 · fix 1/1 · check 1/2')
  })

test('a kind whose slots are all busy counts them all, and a benchmark run adds its own count', () => {
  const units = [desk(1, 'agent', '7', 'running'), desk(2, 'agent', '8', 'running', { benchmark: true }),
    desk(3, 'agent', '9', 'running')]
  const checks = [check('agent/issue-9', 30), check('agent/x', null)]
  expect(slotCounts(DEFAULT_SLOTS, units, checks)).toEqual([{ kind: 'agent', busy: 3, total: 3 },
    { kind: 'chore', busy: 0, total: 1 }, { kind: 'fix', busy: 0, total: 1 }, { kind: 'check', busy: 2, total: 2 },
    { kind: 'benchmark', busy: 1, total: 2 }])
})

test('a project\'s own slots set the totals, a kind set to 0 is left out, and slots that aren\'t an object give none',
  () => {
    const slots = { agent: 1, chore: 0, fix: 2, check: 4, benchmark: 2, other: 5 }
    expect(summary(slotCounts(slots, [desk(1, 'agent', '7', 'running')], []))).toBe('agent 1/1 · fix 0/2 · check 0/4')
    expect(slotCounts(null, [], [])).toEqual([])
    expect(slotCounts('3', [], [])).toEqual([])
  })

test('a running check shows under check by its review\'s issue and title with its branch, or by its branch alone', () => {
  const rows = checkRows([check('agent/issue-9', 30), check('agent/chore-audit', null, null),
    check('agent/issue-8', 31)], context())
  expect(rows.map(row => [row.key, row.text, row.runner, row.state, row.number, row.issue, row.pr, row.title,
    row.note, row.time, row.url, row.actions])).toEqual([
    ['check-agent/issue-9@agent/issue-9-head', 'check     PR for issue 9  (agent/issue-9)', 'check', 'working', 30, 9,
      true, 'PR for issue 9', 'agent/issue-9', '20m', 'https://example.test/30', ['open']],
    ['check-agent/chore-audit@agent/chore-audit-head', 'check     agent/chore-audit', 'check', 'working', undefined,
      undefined, undefined, 'agent/chore-audit', undefined, undefined, undefined, []],
    ['check-agent/issue-8@agent/issue-8-head', 'check     Merged PR  (agent/issue-8)', 'check', 'working', 31, 8,
      true, 'Merged PR', 'agent/issue-8', '20m', 'https://example.test/31', ['open']],
  ])
})

test('the store\'s list of running checks reads as its checks, and anything else as none', () => {
  expect(runningChecks([check('agent/x', null), { branch: 1 }, null])).toEqual([check('agent/x', null)])
  expect(runningChecks({ branch: 'agent/x' })).toEqual([])
})
