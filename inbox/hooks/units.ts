// The runs the dispatcher holds on this machine, as the board draws them: in Runners while a unit works one, in Needs
// You once it failed, and in Queued while it waits for a slot, with how long a run has left before its time limit.
// board.ts reads the runs from the store with everything else and passes in what it read. Like board.ts, it imports
// no Claude Code module and no Node module, so the pane and the web server share it.
import type { Row, RowAction } from '../types'

// Each kind of run's built-in time limit in minutes, as LIMIT_MINUTES in lib/run_units.py sets it; an issue labelled
// benchmark gets the longer limit, and a discussion of an idea DISCUSS_MINUTES. They count for a run whose listing
// carries no `limit`, as one started before the store recorded it. A working run's row says how long it has left once
// it is within WARN_MINUTES of its end.
const RUN_MINUTES: Record<string, number> = { agent: 180, fix: 90, chores: 120, discussion: 30 }
const BENCHMARK_MINUTES = 300
const WARN_MINUTES = 30

/**
 * A run the dispatcher holds on this machine, as lib/dispatch.py lists it: an agent run's target is an issue's number,
 * `pr-<n>`, or `idea-<n>` for a discussion of an idea, a fix's a PR's number, and a chore's its skill.
 */
export type DeskRun = { id: number; kind: string; target: string; status: string; unit: string | null
  started_at: string | null; ended_at?: string | null; benchmark?: boolean
  /** While it works, the time limit in minutes the dispatcher gave its unit, with its project's `limitMinutes`. */
  limit?: number
  /** Whether it is a fresh start of an issue, which Manual holds until Start picks the issue. */
  labelled?: boolean
  /** While it waits, the gate that holds it, such as `memory` or `slot`, and why, which reads after "waits for". */
  gate?: string; reason?: string
  /** The minutes its `agent-desk gpu` calls held a GPU slot and queued for one, once one ended. */
  gpu_held?: number; gpu_queued?: number }

/** A branch check running now, as `agent-desk board --json` lists it, with its review's number or null. */
export type RunningCheck = { branch: string; review: number | null; head: string; startedAt: string | null }

/** A queued branch check the dispatcher holds, as `agent-desk board --json` lists it, with the gate and why. */
type WaitingCheck = { branch: string; review: number | null; head: string; gate: string; reason: string }

// The gates whose reason a Queued card shows: the others, the desk's mode, blockers, serial slots, and target locks,
// show in the card's own note or the issue's state already. A request held for its on-hold issue shows as a request
// of its own, since the issue's card is in Blocked.
const QUEUED_GATES = new Set(['memory', 'usage', 'slot', 'hold'])
// The end of the note lib/merges.py gives a queued review whose queue waits on an auto-fix run, and on a branch check.
const FIX_WAIT = /auto-fix (resolving|adapting it to main)$/
const CHECK_WAIT = /checks starting$/
// The branches lib/merges.py tests a batch of reviews on, as its BATCH_PREFIX names them.
const BATCH_PREFIX = 'merge-queue/'

/** How many of a project's slots of one kind work now, out of how many it has. */
type SlotCount = { kind: string; busy: number; total: number }

// The kinds of slot the dispatcher caps each project's runs by, in the order the web page's Runners sidebar names
// them. A `benchmark` slot caps the agent runs on benchmark issues, so the sidebar names it only while one works.
const SLOT_KINDS = ['agent', 'chore', 'fix', 'check']

/** An open issue or a PR, as a unit's row shows it. */
type Item = { title: string; url: string }

/** What the board read that the units' rows show. */
export type UnitContext = {
  /** The open issues, by number. */
  issues: Map<number, Item>
  /** The open PRs and the recently merged ones, by number. */
  prs: Map<number, Item>
  /** The open PRs' numbers. */
  openPrs: Set<number>
  /** The issue each agent PR's agent/issue-<n> branch names, by PR. */
  issueOfPr: Map<number, number>
  /** The open issues labelled with a serial group. */
  serial: Set<number>
  /** How a row names a number: by its PR's issue, as a PR, or as an issue. */
  refOf(number: number | undefined): Pick<Row, 'number' | 'issue' | 'pr'>
  /** How a row names an issue an agent works: by its open review with the review's link, or as the issue. */
  workRef(issue: number | undefined): Pick<Row, 'number' | 'issue' | 'pr' | 'url'>
  /** How long a run has left before its time limit, `limit` minutes or else its workflow's, once that is near. */
  timeLeft(workflow: string, started: string | undefined, issue: number | undefined, benchmark?: boolean,
    limit?: number): string | undefined
  /** How long ago a time was, compactly. */
  ago(stamp: string): string
}

/**
 * How long a run of `workflow` that started at `started` has left at `now` before its time limit ends it, once that
 * is WARN_MINUTES or less. The limit is `minutes`, the one its unit runs under, or else its workflow's built-in one.
 */
export function timeLeft(now: number, workflow: string, started: string | undefined, benchmark = false,
  minutes?: number): string | undefined {
  const limit = typeof minutes === 'number' && minutes > 0 ? minutes
    : workflow === 'agent' && benchmark ? BENCHMARK_MINUTES : RUN_MINUTES[workflow]
  if (limit === undefined || !started) {
    return undefined
  }
  const left = Math.max(0, Math.ceil(limit - (now - Date.parse(started)) / 60_000))
  return left <= WARN_MINUTES ? `${left} min left` : undefined
}

/** Minutes as a GPU total reads them: whole minutes, or `<1 min` for a part of one. */
function gpuMinutes(minutes: number): string {
  if (minutes > 0 && minutes < 0.5) {
    return '<1 min'
  }
  const whole = Math.round(minutes)
  return `${whole} ${whole === 1 ? 'min' : 'mins'}`
}

/**
 * How long a run's GPU calls, or the runs behind a review, held a GPU slot and queued for one, such as `Held GPU for
 * 12 mins, waited 9 mins for it`, leaving out a queue they never waited in; undefined when they never used the GPU.
 */
export function gpuUse(held: unknown, queued: unknown): string | undefined {
  if (typeof held !== 'number') {
    return undefined
  }
  const waited = typeof queued === 'number' && queued > 0 ? `, waited ${gpuMinutes(queued)} for it` : ''
  return `Held GPU for ${gpuMinutes(held)}${waited}`
}

/** The runs in the store's board that read as runs, each with its id, kind, and target; none from anything else. */
export function deskRuns(listed: unknown): DeskRun[] {
  return Array.isArray(listed) ? listed.filter(one => typeof one?.id === 'number' && typeof one?.kind === 'string'
    && typeof one?.target === 'string') as DeskRun[] : []
}

/** The waiting checks in the store's board that read as checks with a reason; none from anything else. */
export function waitingChecks(listed: unknown): WaitingCheck[] {
  return Array.isArray(listed) ? listed.filter(one => typeof one?.branch === 'string' && typeof one?.gate === 'string'
    && typeof one?.reason === 'string') as WaitingCheck[] : []
}

/**
 * A merging review's note with why the dispatcher holds the run its queue waits on: the review's auto-fix run, a `fix`
 * of its PR that waits, in place of `auto-fix resolving` or `auto-fix adapting it to main`, or the branch check of its
 * head, or of its batch on a merge-queue/ branch, in place of `checks starting`. Any other note stays as it is, as
 * does `after #<n>` on the reviews behind it.
 */
export function mergeWait(note: string, pr: number, branch: string | undefined, units: DeskRun[],
  checks: WaitingCheck[]): string {
  if (FIX_WAIT.test(note)) {
    const fix = units.find(one => one.kind === 'fix' && one.status === 'requested' && one.target === String(pr)
      && one.reason)
    return fix ? note.replace(FIX_WAIT, `auto-fix waits for ${fix.reason}`) : note
  }
  if (CHECK_WAIT.test(note)) {
    const batch = note.startsWith('batch with ')
    const check = checks.find(one => (batch ? one.branch.startsWith(BATCH_PREFIX)
      : one.review === pr || one.branch === branch))
    return check ? note.replace(CHECK_WAIT, `check waits for ${check.reason}`) : note
  }
  return note
}

/**
 * A Queued row with why the dispatcher holds its request, after any note it has, when the gate is spare memory, the
 * weekly usage limit, a free slot, or its issue on hold: the request the row names, or else the newest that waits on
 * its issue.
 */
export function queuedWait(row: Row, units: DeskRun[], issueOfPr: Map<number, number>): Row {
  const issue = row.pr ? row.issue : row.number
  const id = row.request ?? (issue === undefined ? undefined : waitingRequest(units, issue, issueOfPr))
  const request = units.find(one => one.id === id && one.status === 'requested')
  if (!request?.reason || !QUEUED_GATES.has(request.gate ?? '')) {
    return row
  }
  const note = [row.note, `waits for ${request.reason}`].filter(Boolean).join('; ')
  return { ...row, text: `${row.title ?? row.text}  (${note})`, note }
}

/** The running checks in the store's board that read as checks, each with its branch and head; none from anything else. */
export function runningChecks(listed: unknown): RunningCheck[] {
  return Array.isArray(listed) ? listed.filter(one => typeof one?.branch === 'string'
    && typeof one?.head === 'string') as RunningCheck[] : []
}

/**
 * Each kind of a project's slots with how many work now, as lib/dispatch.py counts them: its running units of the kind,
 * or for `check` its running branch checks, out of `slots`, the project's slots as lib/run_units.py's slots_of reads
 * them. A kind with no slots is left out, and `benchmark` shows only while a benchmark run works. Slots that aren't an
 * object give none.
 */
export function slotCounts(slots: unknown, units: DeskRun[], checks: RunningCheck[]): SlotCount[] {
  if (typeof slots !== 'object' || slots === null) {
    return []
  }
  const totals = slots as Record<string, unknown>
  const working = units.filter(one => one.status === 'running')
  const busyOf = (kind: string) => (kind === 'check' ? checks.length
    : kind === 'benchmark' ? working.filter(one => one.kind === 'agent' && one.benchmark).length
      : working.filter(one => one.kind === kind).length)
  const counts = SLOT_KINDS.map(kind => ({ kind, busy: busyOf(kind), total: totals[kind] }))
  const benchmark = { kind: 'benchmark', busy: busyOf('benchmark'), total: totals.benchmark }
  return [...counts, ...(benchmark.busy > 0 ? [benchmark] : [])]
    .filter((one): one is SlotCount => typeof one.total === 'number' && one.total > 0)
}

/** The issue's or PR's number a run's target names; NaN for a chore's skill. */
function numberOfUnit(one: DeskRun): number {
  return Number(one.target.replace(/^(pr|idea)-/, ''))
}

/** Whether a run is an agent's discussion of an idea, which changes no code and leaves its issue's state alone. */
export function discusses(one: DeskRun): boolean {
  return one.kind === 'agent' && one.target.startsWith('idea-')
}

/** The workflow a run's time limit and its failure's note name: a discussion's own, or its kind's. */
function workflowOf(one: DeskRun): string {
  return one.kind === 'chore' ? 'chores' : discusses(one) ? 'discussion' : one.kind
}

/** Whether a run is an agent's on an issue, and not on a PR. */
function worksIssue(one: DeskRun): boolean {
  return one.kind === 'agent' && !one.target.startsWith('pr-')
}

/**
 * A run's issue: an agent run names its issue, or a PR whose agent/issue-<n> branch names the issue, and a fix names
 * its PR the same way.
 */
function issueOfUnit(one: DeskRun, issueOfPr: Map<number, number>): number {
  return worksIssue(one) ? numberOfUnit(one) : issueOfPr.get(numberOfUnit(one)) ?? numberOfUnit(one)
}

/** The issues the dispatcher's agent and fix runs in `status` work on, by issueOfUnit; a discussion works none. */
export function branchIssues(units: DeskRun[], status: string, issueOfPr: Map<number, number>): number[] {
  return units.filter(one => (one.kind === 'agent' || one.kind === 'fix') && !discusses(one) && one.status === status)
    .map(one => issueOfUnit(one, issueOfPr))
}

function known(number: number): number | undefined {
  return Number.isNaN(number) ? undefined : number
}

/**
 * A unit at work shows in Runners under its kind, as a runner's job does under the runner's name. An agent's row takes
 * its issue's title and number, with the issue's open review when it has one, a fix's or a PR-only agent's its PR's,
 * and a chore's its skill, and its note says how long its GPU calls held and queued for the GPU once one ended. An
 * agent's row on an issue has Pause, which stops the run and holds the issue.
 */
export function unitRows(units: DeskRun[], seen: UnitContext): Row[] {
  return units.filter(one => one.status === 'running').map(one => {
    const chore = one.kind === 'chore'
    const number = chore ? undefined : known(numberOfUnit(one))
    const issue = chore ? undefined : known(issueOfUnit(one, seen.issueOfPr))
    const shown = worksIssue(one) ? seen.issues.get(number ?? Number.NaN) : seen.prs.get(number ?? Number.NaN)
    const title = chore ? `chore: ${one.target}` : shown?.title ?? `#${number}`
    const started = one.started_at ?? undefined
    const gpu = gpuUse(one.gpu_held, one.gpu_queued)
    // A discussion holds no serial slot.
    const note = [one.kind === 'fix' ? 'fix' : discusses(one) ? 'discussion' : '',
      issue !== undefined && !discusses(one) && seen.serial.has(issue) ? 'serial' : '',
      seen.timeLeft(workflowOf(one), started, issue, one.benchmark, one.limit), gpu].filter(Boolean).join(' · ')
    return { key: `unit-${one.id}`, text: `${one.kind.padEnd(9)} ${title}${note ? `  (${note})` : ''}`,
      ...(shown ? { url: shown.url } : {}), ...(worksIssue(one) ? seen.workRef(number) : seen.refOf(number)),
      runner: one.kind,
      state: 'working', title, ...(note ? { note } : {}), ...(started ? { since: started } : {}),
      time: started ? seen.ago(started) : undefined,
      actions: [...(shown ? ['open'] : []), ...(pausable(one) ? ['pause'] : [])] as RowAction[] }
  })
}

/** Whether Pause can stop a run: an agent's on an issue, which `dispatch.py pause` stops by the issue's number. */
function pausable(one: DeskRun): boolean {
  return one.kind === 'agent' && /^\d+$/.test(one.target)
}

/**
 * A branch check at work shows in the web page's Runners sidebar under `check`, as a unit does under its kind: by its
 * review's issue and title with its branch as the note, or by its branch alone while it has no review.
 */
export function checkRows(checks: RunningCheck[], seen: UnitContext): Row[] {
  return checks.map(one => {
    const review = one.review ?? undefined
    const shown = review === undefined ? undefined : seen.prs.get(review)
    const title = shown?.title ?? one.branch
    const note = shown ? one.branch : undefined
    const started = one.startedAt ?? undefined
    return { key: `check-${one.branch}@${one.head}`, text: `${'check'.padEnd(9)} ${title}${note ? `  (${note})` : ''}`,
      ...(review === undefined ? {} : seen.refOf(review)), ...(shown ? { url: shown.url } : {}), runner: 'check',
      state: 'working', title, ...(note ? { note } : {}), ...(started ? { since: started } : {}),
      time: started ? seen.ago(started) : undefined, actions: (shown ? ['open'] : []) as RowAction[] }
  })
}

/**
 * Whether Retry on a run carries on its session: lib/work-issue.sh takes up the session of an agent's run on an issue
 * that hit its time limit, while every other run starts fresh.
 */
function continues(one: DeskRun): boolean {
  return worksIssue(one) && !discusses(one) && one.status === 'timed-out'
}

/**
 * A unit's run that failed, timed out, or vanished shows in Needs You until a later run asks for its target, or its
 * issue or PR closes. Its row carries the run's id below zero, which tells Retry and Dismiss it from an Actions run's:
 * Retry asks the dispatcher for the run again, and reads Continue on a row that continues. A run whose unit the store
 * names has a Log, the command that prints the unit's log from the journal.
 */
export function unitFailures(units: DeskRun[], seen: UnitContext): Row[] {
  return units.filter(one => ['failed', 'timed-out', 'lost'].includes(one.status)).flatMap(one => {
    const chore = one.kind === 'chore'
    const number = chore ? undefined : known(numberOfUnit(one))
    const shown = worksIssue(one) ? seen.issues.get(number ?? Number.NaN)
      : seen.openPrs.has(number ?? Number.NaN) ? seen.prs.get(number ?? Number.NaN) : undefined
    if (!chore && shown === undefined) {
      return []
    }
    const state = one.status === 'timed-out' ? 'timed out' : 'failed'
    const workflow = workflowOf(one)
    const title = chore ? one.target : shown?.title ?? ''
    const log = one.unit ? `journalctl --user -u ${one.unit}` : undefined
    return [{ key: `failed-unit-${one.id}`, text: `${state}  ${workflow}: ${title}`,
      ...(worksIssue(one) ? { number } : seen.refOf(number)), ...(shown ? { url: shown.url } : {}),
      ...(log ? { log } : {}), run: -one.id, ...(continues(one) ? { continues: true } : {}), title, state,
      note: workflow, ...(one.ended_at ? { since: one.ended_at } : {}),
      actions: [...(shown ? ['open'] : []), ...(log ? ['log'] : []), 'retry', 'dismiss'] as RowAction[] }]
  })
}

/**
 * A request that no row in Queued shows gets a row of its own, so nothing the dispatcher holds is out of sight, with
 * Cancel to end it: one on an issue, as Continue makes on an issue without labels while the desk is on Hold, by the
 * issue, a fix or an agent's run on a PR by the PR, and a chore by its skill. `queued` holds the issues Queued's other
 * rows show, by issueOfUnit, as a row that names a PR shows its issue. A request held for its issue on hold has Resume
 * too, since the web page shows the issue's card here in place of its card in Blocked.
 */
export function unitRequests(units: DeskRun[], seen: Pick<UnitContext, 'issues' | 'prs' | 'issueOfPr' | 'refOf'>,
  queued: Set<number | undefined>): Row[] {
  return units.filter(one => one.status === 'requested').flatMap(one => {
    if (one.kind === 'chore') {
      const title = `chore: ${one.target}`
      return [{ key: `request-${one.id}`, text: `${title}  (waits to start)`, title, note: 'waits to start',
        request: one.id, actions: ['cancel'] as RowAction[] }]
    }
    const number = numberOfUnit(one)
    const shown = worksIssue(one) ? seen.issues.get(number) : seen.prs.get(number)
    if (shown === undefined || queued.has(issueOfUnit(one, seen.issueOfPr))) {
      return []
    }
    return [{ key: `request-${one.id}`, text: `${shown.title}  (waits to start)`,
      ...(worksIssue(one) ? { number } : seen.refOf(number)), url: shown.url, title: shown.title,
      note: 'waits to start', request: one.id,
      actions: (one.gate === 'hold' && worksIssue(one) ? ['open', 'unpause', 'cancel']
        : ['open', 'cancel']) as RowAction[] }]
  })
}

/** The agent and fix requests that wait on `issue`, by issueOfUnit, oldest first. */
export function waitingRequests(units: DeskRun[], issue: number, issueOfPr: Map<number, number>): DeskRun[] {
  return units.filter(one => (one.kind === 'agent' || one.kind === 'fix') && one.status === 'requested'
    && issueOfUnit(one, issueOfPr) === issue).sort((a, b) => a.id - b.id)
}

/**
 * The newest agent or fix request that waits on `issue`, by issueOfUnit, as Cancel ends it; undefined when none
 * waits.
 */
export function waitingRequest(units: DeskRun[], issue: number, issueOfPr: Map<number, number>): number | undefined {
  return waitingRequests(units, issue, issueOfPr).at(-1)?.id
}

/**
 * The newest agent run on `issue` that failed, timed out, or was lost, as Continue asks for again, with its id below
 * zero as Retry takes it; undefined when there is none, or when `answered` says an agent went on after it ended. A
 * discussion of the issue while it was an idea is no work to restart.
 */
export function lastFailure(units: DeskRun[], issue: number, answered: (ended: string) => boolean): number | undefined {
  const failed = units.filter(one => worksIssue(one) && !discusses(one) && numberOfUnit(one) === issue
    && ['failed', 'timed-out', 'lost'].includes(one.status) && !(one.ended_at && answered(one.ended_at)))
    .sort((a, b) => (b.ended_at ?? '').localeCompare(a.ended_at ?? '') || b.id - a.id)[0]
  return failed === undefined ? undefined : -failed.id
}
