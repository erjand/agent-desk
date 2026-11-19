// The inbox's board: what it reads from the local store and this machine. The Claude Code pane (register.tsx) and the
// web inbox (../../web/server.ts) both draw it, each through a Host of its own, so this file and the modules beside it
// import nothing from either: no Claude Code module and no Node module.
import type { Board, Chore, Row, RowAction } from '../types'
import { checksPassed, dismissedRuns, failingNote } from './actions.ts'
import { fixingRow, mergingRow, withOrderMoves } from './moves.ts'
import { finishedLast, localSessionOf, resumeOf, startedSince, workedHere } from './notes.ts'
import { readStore, run } from './run.ts'
import { branchIssues, checkRows, type DeskRun, deskRuns, lastFailure, mergeWait, queuedWait, runningChecks, slotCounts,
  timeLeft, unitFailures, unitRequests, unitRows, waitingChecks, waitingRequest, waitingRequests } from './units.ts'

// The workflow states of an issue the person queued for an agent, or that an agent works or waits to work.
const AGENT_STATES = new Set(['queued', 'in-progress', 'waiting'])

/**
 * A check of a review's head, as the store's `checks` table holds it: `test`, or `merge` or `setup` when that step
 * failed, with its status (`queued`, `running`, `passed`, `failed`, `timed-out`, or `lost`), its log, and when it
 * started and ended.
 */
export type Check = { name: string; status: string; attempt?: number; log?: string | null
  startedAt?: string | null; endedAt?: string | null }

/**
 * An open issue as `agent-desk board --json` gives it: its workflow state, the labels that group it, its blockers, how
 * many of its sub-issues closed, its place in the project's start order from 1 while it is queued or waiting, and for
 * one in progress its comments and where its agent/issue-<n> branch last merged.
 */
export type StoreIssue = { number: number; title: string; status: string; labels: string[]; createdAt: string
  updatedAt?: string; blockedBy: { number: number; state: string }[]; subIssues?: { total: number; completed: number }
  position?: number
  comments?: { body: string; createdAt: string }[]
  landed?: { base: string; basePr: { number: number; state: string } | null } | null }
/** An open review as `agent-desk board --json` gives it, with its head's checks and whether it merges with its base. */
export type StoreReview = { number: number; title: string; branch: string; base: string; head: string | null
  draft: boolean; createdAt: string; checks: Check[]; mergeable: string }
/** A merged review as `agent-desk board --json` gives it. */
type StoreMerged = { number: number; title: string; branch: string; base: string; mergedAt: string }
/**
 * What `agent-desk board --json` prints. `asked` numbers the open reviews with a comment the dispatcher hasn't answered.
 * `queue` and `fixes` are the merge queue and the automatic fixes' plan as their last ticks saved them, and `chores` is
 * the table the last `agent-desk chores status` saved. `at` is the time with its zone, such as `14:05 +0200`, and `web`
 * the page's address, such as `http://127.0.0.1:7480`.
 */
export type StoreBoard = { repo: string; issues: StoreIssue[]; reviews: StoreReview[]; merged: StoreMerged[]
  runs: unknown; asked?: unknown; checks?: unknown; waitingChecks?: unknown; slots?: unknown; queue?: unknown; fixes?: unknown; hold?: unknown; chores?: string[]; backup?: unknown; at?: string
  machine?: string; web?: string }

/** An open issue, with the web page's address for it. */
type Issue = StoreIssue & { url: string }
/** An open PR, as the board reads a review, under the names gh gives a PR's fields. */
export type Pr = { number: number; title: string; url: string; isDraft: boolean; headRefName: string
  baseRefName?: string; headRefOid?: string | null; mergeable?: string; createdAt?: string; checks?: Check[] }
type Merged = { number: number; title: string; url: string; mergedAt: string; headRefName: string; baseRefName: string }

/** What the board needs from whoever draws it: a way to run a program, the clock, and a store that outlasts it. */
export type Host = {
  /**
   * Runs a program in `cwd`, or the host's own folder, with `stdin` written to its input, and returns its output, or
   * null when it fails or runs past 30 seconds.
   */
  run(argv: string[], cwd?: string, stdin?: string): Promise<string | null>
  now(): Promise<number>
  get(key: string): Promise<unknown>
  set(key: string, value: unknown): Promise<void>
}

/**
 * A project's checkout, where agent-desk finds its repository by the origin remote, and its main checkout, where the
 * buttons' merge queue changes run. Without a checkout, the commands run in the host's own folder.
 */
export type Project = { root?: string; main?: string }

/**
 * Whether an issue is in progress with no ready PR and no agent at work: its agent either waits for a slot, which
 * Queued shows, or stopped short, which waits on the person. Runners shows one an agent works now.
 */
export function isInFlight(status: string, number: number, readyHeads: Set<string>, started: Set<number>): boolean {
  return status === 'in-progress' && !readyHeads.has(`agent/issue-${number}`) && !started.has(number)
}

/**
 * Whether an open issue belongs in Ideas: the person labelled it idea and hasn't queued it for an agent since. Ideas
 * take no Agent-ready button, and they stay out of Open and Needs Design until Move to Open takes the label off.
 */
export function isIdea(labels: Set<string>, status: string): boolean {
  return labels.has('idea') && !AGENT_STATES.has(status)
}

/**
 * Whether an open issue belongs in Backlog: the person labelled it backlog to do later and hasn't queued it for an
 * agent since. Backlog issues take no Agent-ready button, and they stay out of Open, Blocked, and Needs Design until
 * Move to Open takes the label off. An issue labelled idea as well belongs in Ideas.
 */
export function isBacklog(labels: Set<string>, status: string): boolean {
  return labels.has('backlog') && !isIdea(labels, status) && !AGENT_STATES.has(status)
}

/**
 * Why an open issue is parked, or null: the person put it on hold, or the store lists an open issue blocking it.
 * Blocked lists a parked issue, with the Agent-ready button only when blockers alone hold it, since its run waits for
 * them to close.
 */
export function heldBy(status: string, blockers: { number: number; state: string }[]): string | null {
  const open = blockers.filter(one => one.state === 'OPEN').map(one => `#${one.number}`)
  if (open.length > 0) {
    return `blocked by ${open.join(', ')}`
  }
  return status === 'on-hold' ? 'on hold' : null
}

/**
 * Where an issue's work went when its agent/issue-<n> PR merged into `base`, another agent branch, as a stacked PR
 * does: it waits while `basePr`, that branch's PR, is open, and has merged with it once it merges. Null when the PR
 * merged nowhere, merged into the default branch, or its base PR closed unmerged, which leaves the agent stopped.
 */
export function stackedOn(base: string | undefined, basePr: { number: number; state: string } | null | undefined):
  { pr: number; merged: boolean } | null {
  if (!base?.startsWith('agent/') || !basePr || basePr.state === 'CLOSED') {
    return null
  }
  return { pr: basePr.number, merged: basePr.state === 'MERGED' }
}

/**
 * How a row's number reads: the number of what pressing the row opens, an issue's or a PR's, as GitHub writes both.
 * The pane draws a PR's number in PR_HUE to tell the two apart.
 */
export function cite(row: Pick<Row, 'number'>): string {
  return row.number === undefined ? '' : `#${row.number}`
}

/** The issue an agent/issue-<n> branch works, or undefined for any other branch. */
function agentIssue(head: string): number | undefined {
  const issue = head.match(/^agent\/issue-(\d+)$/)?.[1]
  return issue === undefined ? undefined : Number(issue)
}

/**
 * What an agent run sent back to a PR works, as the board tracks it: the issue of an agent/issue-<n> PR, or the PR
 * itself from any other agent/<slug> branch, such as a chore's or an interactive session's, whose agent run names the
 * PR. Undefined for a PR from outside agent/, which no agent goes back to.
 */
export function agentWork(pr: { number: number; headRefName: string }): number | undefined {
  return agentIssue(pr.headRefName) ?? (/^agent\/[a-z0-9][a-z0-9-]*$/.test(pr.headRefName) ? pr.number : undefined)
}

/** A PR's number fields for its row: its own number, which the row shows and Merge acts on, and its agent issue. */
function prRef(pr: { number: number; headRefName: string }): Pick<Row, 'number' | 'issue' | 'pr'> {
  const issue = agentIssue(pr.headRefName)
  return { number: pr.number, pr: true, ...(issue === undefined ? {} : { issue }) }
}

/**
 * How two issues sort in their project's start order: by their places in it, and those without a place after, by
 * number, as in-progress issues sort among themselves.
 */
export function byStartOrder(a: Pick<StoreIssue, 'number' | 'position'>,
  b: Pick<StoreIssue, 'number' | 'position'>): number {
  return (a.position ?? Number.POSITIVE_INFINITY) - (b.position ?? Number.POSITIVE_INFINITY) || a.number - b.number
}

function names(issue: StoreIssue): Set<string> {
  return new Set(issue.labels)
}

/**
 * The serial groups an issue's labels put it in, `serial` and each `serial:<group>`, as lib/serial_slots.py reads
 * them.
 */
export function serialGroups(labels: Set<string>): Set<string> {
  return new Set([...labels].filter(name => name === 'serial' || (name.startsWith('serial:') && name.length > 7)))
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * When a stamp happened, in the zone `date +%z` printed (`+0200`, `-0700`), such as `Oct 23 14:05`. The day takes two
 * columns so right-justified times line up.
 */
export function when(stamp: string, zone: string): string {
  const [, sign = '+', hours = '0', minutes = '0'] = /^([+-])(\d\d)(\d\d)$/.exec(zone) ?? []
  const offset = (sign === '-' ? -1 : 1) * (Number(hours) * 60 + Number(minutes))
  const local = new Date(Date.parse(stamp) + offset * 60_000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${MONTHS[local.getUTCMonth()]} ${String(local.getUTCDate()).padStart(2)} `
    + `${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`
}

/** The time since a stamp, compact: minutes under an hour, hours under two days, then days, such as `12h`. */
export function ago(now: number, stamp: string): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(stamp)) / 60_000))
  if (minutes < 60) {
    return `${minutes}m`
  }
  const hours = Math.round(minutes / 60)
  return hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`
}

/** The board as the local store and this machine show it now, without the failed runs this host's person dismissed. */
export async function fetchBoard(host: Host, project: Project): Promise<Board> {
  return withoutDismissed(await readBoard(host, project), await dismissedRuns(host))
}

/** The board with the failed runs `dismissed` names left out of Needs You. */
function withoutDismissed(board: Board, dismissed: number[]): Board {
  const hidden = new Set(dismissed)
  return { ...board, needs: board.needs.filter(row => row.run === undefined || !hidden.has(row.run)) }
}

/** A board with no rows, which says why. */
function emptyBoard(at: string, error: string): Board {
  return { at, runners: [], needs: [], merging: [], design: [], designTotal: 0, queued: [], next: [], blocked: [],
    backlog: [], ideas: [], merged: [], chores: [], github: [], error }
}

// Where the web page serves when the board's read doesn't say, as `agent-desk web on` serves it by default.
const WEB_ADDRESS = 'http://127.0.0.1:7480'

/**
 * The web page's address for issue or review `number` of `repo`, which shows the item in the page's panel:
 * `<web>/#<desk>:<n>`, with the desk named as the page names it, by the repository's name without its owner.
 */
export function linkOf(web: string, repo: string, number: number): string {
  const [, desk = repo] = repo.split('/')
  return `${web.replace(/\/+$/, '')}/#${desk}:${number}`
}

/** The time `stamp` shows on this host's clock, such as `14:05`. */
function clockOf(stamp: number): string {
  const local = new Date(stamp)
  return `${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}`
}

/**
 * The board as the local store and this machine show it now, every failed run included. One `agent-desk board --json`
 * reads it all, and asks GitHub nothing: the merge queue, the automatic fixes, and the failed checks' write-ups move on
 * from the timers `agent-desk chores on` starts, and the board shows what they last saved. Beyond that one command, a
 * refresh runs `lib/failures.py log` once for each failed check's log it hasn't read before, and `ps` for each issue a
 * session on this machine claimed with `agent-desk start`.
 */
async function readBoard(host: Host, project: Project): Promise<Board> {
  const now = await host.now()
  const read = await readStore(host, project)
  if (read === null) {
    return emptyBoard(clockOf(now), "agent-desk couldn't read the local store; run `agent-desk board` in the "
      + "project's checkout to see why.")
  }
  const mergeQueue = mergeQueueOf(read.queue)
  const merging = new Map(mergeQueue.queue.map(one => [one.pr, one.note]))
  const leftQueue = new Map(mergeQueue.dropped.map(one => [one.pr, one]))
  const fixPlan = fixVerdicts(read.fixes)
  const [clock = clockOf(now), zone = '+0000'] = (read.at ?? '').trim().split(' ').filter(Boolean)

  const web = read.web || WEB_ADDRESS
  const issues: Issue[] = read.issues.map(one => ({ ...one, url: linkOf(web, read.repo, one.number) }))
  const prs: Pr[] = read.reviews.map(one => ({ number: one.number, title: one.title,
    url: linkOf(web, read.repo, one.number), isDraft: one.draft, headRefName: one.branch, baseRefName: one.base,
    headRefOid: one.head, mergeable: one.mergeable, createdAt: one.createdAt, checks: one.checks }))
  const merged: Merged[] = read.merged.map(one => ({ number: one.number, title: one.title,
    url: linkOf(web, read.repo, one.number), mergedAt: one.mergedAt, headRefName: one.branch,
    baseRefName: one.base }))
  const units = deskRuns(read.runs)
  // An agent run started from an issue's PR names the PR, whose agent/issue-<n> branch names the issue.
  const issueOfPr = new Map(prs.flatMap(pr => {
    const issue = agentIssue(pr.headRefName)
    return issue === undefined ? [] : [[pr.number, issue] as const]
  }))
  // An issue's open review, from its agent/issue-<n> branch. An issue's row in Queued or Working names the review, as
  // Needs You's row does, so the web page keeps showing the review while an agent works it. An issue whose review
  // closed or merged names itself alone.
  const reviewOf = new Map(prs.flatMap(pr => {
    const issue = agentIssue(pr.headRefName)
    return issue === undefined ? [] : [[issue, pr] as const]
  }))
  const workRef = (number: number | undefined): Pick<Row, 'number' | 'issue' | 'pr' | 'url'> => {
    const pr = number === undefined ? undefined : reviewOf.get(number)
    return pr ? { ...prRef(pr), url: pr.url } : { number }
  }
  // A number a run names may be a PR's, which its row shows by the PR's issue or as a PR.
  const prsByNumber = new Map([...prs, ...merged].map(pr => [pr.number, pr]))
  const refOf = (number: number | undefined): Pick<Row, 'number' | 'issue' | 'pr'> => {
    const pr = number === undefined ? undefined : prsByNumber.get(number)
    return pr ? prRef(pr) : { number }
  }
  // The issues an agent or fix run works now, and those with one that waits for a slot from the dispatcher. A PR with
  // a comment sent to the agent, as Fix posts, waits too until the dispatcher answers it with the run's request: its
  // agent/issue-<n> branch's issue, or the PR itself from another agent branch.
  const agentStarted = new Set(branchIssues(units, 'running', issueOfPr))
  const asked = new Set(Array.isArray(read.asked) ? read.asked : [])
  const askedWork = prs.filter(pr => asked.has(pr.number)).flatMap(pr => agentWork(pr) ?? [])
  const agentQueued = new Set([...branchIssues(units, 'requested', issueOfPr), ...askedWork]
    .filter(issue => !agentStarted.has(issue)))
  const serial = new Set(issues.filter(issue => serialGroups(names(issue)).size > 0).map(issue => issue.number))
  // How long a run has left before its time limit: the one its unit runs under, or else its workflow's, which is
  // longer for an issue labelled benchmark.
  const benchmarks = new Set(issues.filter(issue => names(issue).has('benchmark')).map(issue => issue.number))
  const left = (workflow: string, started: string | undefined, issue: number | undefined, benchmark = false,
    limit?: number) => timeLeft(now, workflow, started, benchmark || (issue !== undefined && benchmarks.has(issue)),
    limit)
  const byNumber = new Map(issues.map(issue => [issue.number, issue]))
  const unitContext = { issues: byNumber, prs: prsByNumber, openPrs: new Set(prs.map(pr => pr.number)), issueOfPr,
    serial, refOf, workRef, timeLeft: left, ago: (stamp: string) => ago(now, stamp) }

  const ready = prs.filter(pr => !pr.isDraft)
  const readyHeads = new Set(ready.map(pr => pr.headRefName))
  // Ideas and Backlog hold their issues apart from the work to decide or queue soon.
  const parked = (issue: Issue) => isIdea(names(issue), issue.status) || isBacklog(names(issue), issue.status)
  const undecided = issues.filter(issue => issue.status === 'needs-design' && !parked(issue))
  const design: Row[] = undecided.map(issue => ({
    key: `decide-${issue.number}`, text: issue.title, number: issue.number, url: issue.url, title: issue.title,
    actions: ['open', 'designed', 'close'] as RowAction[] }))
  // A ready PR whose agent went back to it leaves Needs You: Runners shows the agent at work, and Queued one waiting
  // for a slot.
  const sentBack = (pr: Pr) => {
    const work = agentWork(pr)
    return work !== undefined && (agentStarted.has(work) || agentQueued.has(work))
  }
  const reviewing = ready.filter(pr => !sentBack(pr) && !merging.has(pr.number))
  const failing = new Map(await Promise.all(reviewing.map(async pr =>
    [pr.number, await failingNote(host, project, pr)] as const)))
  const needs: Row[] = [
    // A failing PR can't merge, so an agent branch's PR gets Fix alone. One that conflicts with main can still join the
    // merge queue, which has an agent fix its conflicts when its turn comes; Fix does so sooner. The note says when
    // the automatic fix starts, or the failing check's first error. A PR that left the queue says why until a push to
    // its branch, such as an agent's fix, answers it.
    ...reviewing.map(pr => {
      const conflicts = pr.mergeable === 'CONFLICTING'
      const failed = failing.get(pr.number)
      const agent = agentWork(pr) !== undefined
      const actions: RowAction[] = !conflicts && !failed ? ['open', 'merge'] : !agent ? ['open']
        : failed ? ['open', 'fix'] : ['open', 'merge', 'fix']
      const state = conflicts ? 'conflicts' : failed ? 'failing' : 'review'
      const dropped = leftQueue.get(pr.number)
      const answered = dropped?.head != null && dropped.head !== pr.headRefOid
      const why = dropped === undefined || answered ? undefined : `left the merge queue: ${dropped.reason}`
      const note = (conflicts ? fixPlan.get(pr.number) : failed) ?? why
      return { key: `pr-${pr.number}`, text: `${state}  ${pr.title}${note ? `  (${note})` : ''}`, ...prRef(pr),
        url: pr.url, actions, title: pr.title, state, ...(note ? { note } : {}), since: pr.createdAt }
    }),
    ...unitFailures(units, unitContext),
  ]

  const inFlight = issues.filter(issue => isInFlight(issue.status, issue.number, readyHeads, agentStarted))
  // An issue with no agent at work stopped short, unless its PR merged into another agent branch: then it waits in
  // Open on that branch's PR, or once that PR merged too, needs closing by hand, since only a merge into the default
  // branch closes the issues it fixes.
  // One whose PR just merged into another base is done: the cleanup drops its in-progress state a few seconds after
  // the merge, so a refresh can read it in progress between the two. An agent that started on it after the merge, as
  // on an issue reopened for more work, makes it in flight again.
  const landedAt = new Map(merged.filter(pr => !pr.baseRefName.startsWith('agent/')).flatMap(pr => {
    const issue = agentIssue(pr.headRefName)
    return issue === undefined ? [] : [[issue, pr.mergedAt] as const]
  }))
  const commentsOf = (issue: Issue) => issue.comments ?? []
  const idle = inFlight.filter(issue => !agentQueued.has(issue.number)).filter(issue => {
    const landed = landedAt.get(issue.number)
    return landed === undefined || startedSince(commentsOf(issue), landed)
  })
  const stacks = idle.map(issue => stackedOn(issue.landed?.base, issue.landed?.basePr))
  const stalled = idle.filter((_, index) => stacks[index] === null)
  const notes = stalled.map(issue => commentsOf(issue).map(one => one.body))
  // One a session on this machine works, by `agent-desk start`, shows in Runners while that session runs.
  const sessions = notes.map(one => localSessionOf(one))
  const machine = read.machine
  const live = await Promise.all(sessions.map(async session => workedHere(session, machine,
    session ? await run(host, project, ['ps', '-p', String(session.pid), '-o', 'args=']) : null)))
  const local: Row[] = stalled.flatMap((issue, index) => (live[index]
    ? [{ key: `local-${issue.number}`, text: `${'local'.padEnd(9)} ${issue.title}`, url: issue.url,
      ...workRef(issue.number), runner: 'local', state: 'working', title: issue.title,
      actions: ['open'] as RowAction[] }] : []))
  // A finished agent's draft PR shows for the person to read, without Resume. Failing checks, or a head that conflicts
  // with main, add Fix; passing ones add Ready, since a check may have waited for a slot until the agent finished. A
  // draft that fails a check and conflicts too shows the failed check's error, and its Fix asks for both.
  const drafts = new Map(prs.filter(pr => pr.isDraft).map(pr => [pr.headRefName, pr]))
  const finished = stalled.map((issue, index) => (!live[index] && finishedLast(notes[index] ?? [])
    ? drafts.get(`agent/issue-${issue.number}`) : undefined))
  const draftFailing = await Promise.all(finished.map(pr => (pr ? failingNote(host, project, pr) : undefined)))
  needs.push(...stalled.flatMap((issue, index) => {
    if (live[index]) {
      return []
    }
    const draft = finished[index]
    if (draft) {
      const conflicts = draft.mergeable === 'CONFLICTING'
      const note = draftFailing[index] ?? (conflicts ? 'conflicts with main' : undefined)
      const actions: RowAction[] = note ? ['open', 'fix'] : checksPassed(draft.checks) ? ['open', 'undraft'] : ['open']
      return [{ key: `draft-${draft.number}`, text: `draft  ${draft.title}${note ? `  (${note})` : ''}`,
        ...prRef(draft), url: draft.url, title: draft.title, state: 'draft', ...(note ? { note } : {}),
        ...(conflicts ? { conflicts } : {}), since: draft.createdAt, actions }]
    }
    // Continue asks again for the issue's newest failed agent run that no agent went on from, or else starts a fresh
    // run.
    const resume = resumeOf(notes[index] ?? [])
    const rerun = lastFailure(units, issue.number, ended => startedSince(commentsOf(issue), ended))
    return [{ key: `stopped-${issue.number}`, text: `stopped  ${issue.title}`, number: issue.number, url: issue.url,
      resume, ...(rerun === undefined ? {} : { rerun }), title: issue.title, state: 'stopped', since: issue.updatedAt,
      actions: (resume ? ['open', 'resume', 'restart', 'close'] : ['open', 'restart', 'close']) as RowAction[] }]
  }))
  needs.push(...idle.flatMap((issue, index) => (stacks[index]?.merged
    ? [{ key: `stacked-${issue.number}`, text: `merged  ${issue.title}  (with PR #${stacks[index]?.pr})`,
      number: issue.number, url: issue.url, title: issue.title, state: 'merged', note: `with PR #${stacks[index]?.pr}`,
      since: issue.updatedAt, actions: ['open'] as RowAction[] }] : [])))
  const stackWaits: Row[] = idle.flatMap((issue, index) => (stacks[index] && !stacks[index].merged
    ? [{ key: `blocked-${issue.number}`, text: `${issue.title}  (waiting on PR #${stacks[index].pr})`,
      number: issue.number, url: issue.url, title: issue.title, note: `waiting on PR #${stacks[index].pr}`,
      actions: ['open'] as RowAction[] }] : []))

  // In Manual, a queued issue nobody picked here waits for Start, whose run the gate ended or will end. On Hold, Start
  // picks a queued issue for Resume, and the issue says it starts then. A picked one says so first, and Unpick drops
  // its pick.
  const picks = startPicks(read.hold, read.repo)
  const unstarted = (number: number) => picks !== null && !picks.picked.has(`${read.repo}#${number}`)
  const picked = (number: number) => picks !== null && !unstarted(number)
  const waitsForStart = (issue: Issue) => picks?.held === false && unstarted(issue.number)
  const startNote = (number: number) => (picks === null ? undefined : unstarted(number)
    ? (picks.held ? undefined : 'waits for Start') : picks.held ? 'starts on Resume' : undefined)
  // What a waiting serial issue waits on: the lowest in-progress issue that shares one of its serial groups, or else
  // the first queued one in the project's start order, or undefined for none. A holder whose agent finished keeps the
  // slot until its PR merges, so the wait is on the person's review. In Manual, a queued issue that waits for Start
  // holds no slot for a picked issue.
  const slotOf = (waiting: Issue) => {
    const mine = serialGroups(names(waiting))
    const ahead = (status: string) => issues.filter(issue => issue.number !== waiting.number
      && issue.status === status && [...serialGroups(names(issue))].some(group => mine.has(group))
      && !(status === 'queued' && waitsForStart(issue) && !waitsForStart(waiting)))
      .sort(byStartOrder)[0]
    const holder = ahead('in-progress')
    if (holder) {
      const holderPr = ready.find(pr => pr.headRefName === `agent/issue-${holder.number}`)
      return holderPr ? `#${holder.number}'s PR #${holderPr.number}` : `#${holder.number}`
    }
    const queued = ahead('queued')
    return queued && (waitsForStart(queued) ? `#${queued.number}, which waits for Start` : `#${queued.number}`)
  }
  const serialIssue = (issue: Issue) => serialGroups(names(issue)).size > 0
  const startActions = (number: number, rest: RowAction[] = []) =>
    ['open', ...(unstarted(number) ? ['start'] : picked(number) ? ['unpick'] : []), ...rest] as RowAction[]
  // Move to Open and Close both cancel the requests that wait on the issue and drop its pick. A row that waits to go
  // back to its PR keeps Cancel instead.
  const freshActions = (number: number, serial = true) =>
    [...(serial ? startActions(number) : ['open', ...(picked(number) ? ['unpick'] : [])]), 'to-open',
      'close'] as RowAction[]
  // In Manual, the dispatcher holds every request until Start picks it: a fresh start by its issue or its run, any
  // other by its run. Start picks all of a row's requests, so two comments sent before it run one after the other.
  const requestPicked = (one: DeskRun) => picks !== null && (picks.runs.has(one.id)
    || (one.labelled === true && picks.picked.has(`${read.repo}#${one.target}`)))
  // A fresh start waits in Manual until Start picks its issue, however the issue reached Queued, so its row shows the
  // issue rather than its PR. Any other request's row keeps its PR and note and adds the wait for Start. `asked` holds
  // the row's requests, by default those that wait on its issue.
  const freshStart = (request: number | undefined) => units.find(one => one.id === request)?.labelled === true
  const withStart = (row: Row, issue: Issue | undefined, asked?: DeskRun[]): Row => {
    if (picks === null) {
      return row
    }
    const requests = asked ?? (issue === undefined ? [] : waitingRequests(units, issue.number, issueOfPr))
    const held = requests.filter(one => !requestPicked(one))
    const pick = issue !== undefined && held.some(one => one.labelled) ? issue.number : undefined
    const starts = held.filter(one => !one.labelled || pick === undefined).map(one => one.id)
    const waits = held.length > 0
    const said = waits ? (picks.held ? undefined : 'waits for Start')
      : picks.held && requests.length > 0 ? 'starts on Resume' : undefined
    const start = { ...(pick === undefined ? {} : { pick }), ...(starts.length > 0 ? { starts } : {}) }
    const cancel = row.request === undefined ? [] : ['cancel'] as RowAction[]
    if (issue !== undefined && freshStart(row.request)) {
      const { pr: _pr, issue: _issue, note: _note, ...rest } = row
      return { ...rest, number: issue.number, url: issue.url, text: `${issue.title}${said ? `  (${said})` : ''}`,
        ...(said ? { note: said } : {}), ...start,
        actions: ['open', ...(waits ? ['start'] : []), ...cancel] as RowAction[] }
    }
    const note = [row.note === 'waits to start' && said ? undefined : row.note, said].filter(Boolean).join('; ')
    const { note: _note, ...rest } = row
    return { ...rest, text: `${row.title ?? row.text}${note ? `  (${note})` : ''}`, ...(note ? { note } : {}),
      ...start, actions: waits ? [...row.actions.filter(one => one === 'open'), 'start',
        ...row.actions.filter(one => one !== 'open')] : row.actions }
  }
  // A queued issue waits for a free slot. A waiting issue waits on its open blockers, or, with none, a serial one on
  // its serial group's slot. The dispatcher starts it once neither holds it, from the store event that frees it or its
  // 30-second reconcile. A waiting issue in no serial group whose blockers closed has no note of its own: its row reads
  // only why the dispatcher still holds its request. In Manual, a serial one nobody picked waits for Start as well. A
  // picked issue's note starts with `picked`, and its row keeps in `unpicked` the note it reads once Unpick drops the
  // pick.
  const issueNote = (issue: Issue, starts: (number: number) => string | undefined) => {
    if (issue.status === 'queued') {
      return starts(issue.number)
    }
    const blockers = issue.blockedBy.filter(one => one.state === 'OPEN').map(one => `#${one.number}`)
    const ahead = blockers.join(', ') || slotOf(issue)
    if (ahead) {
      return `waiting on ${ahead}`
    }
    return serialIssue(issue) ? starts(issue.number) || 'waiting on the serial slot' : undefined
  }
  const queuedRow = (issue: Issue): Row => {
    const own = issueNote(issue, startNote)
    const note = [picked(issue.number) ? 'picked' : undefined, own].filter(Boolean).join('; ') || undefined
    const unpicked = picked(issue.number)
      ? { unpicked: issueNote(issue, () => (picks?.held ? undefined : 'waits for Start')) ?? '' } : {}
    const serial = issue.status === 'queued' || serialIssue(issue)
    return { key: `${issue.status === 'queued' ? 'queued' : 'waiting'}-${issue.number}`,
      text: note ? `${issue.title}  (${note})` : issue.title, number: issue.number, url: issue.url, title: issue.title,
      ...(note ? { note } : {}), ...unpicked, actions: freshActions(issue.number, serial) }
  }
  // The queued and waiting issues lead Queued in the project's start order, each with Move Up, Move Down, and Move to
  // Top as its place allows. Start on one picks the issue, and any other request of it that waits for Start.
  const ordered = issues.filter(issue => issue.status === 'queued' || issue.status === 'waiting').sort(byStartOrder)
  const queued: Row[] = [
    ...ordered.map((issue, index) => {
      const row = queuedRow(issue)
      const starts = waitingRequests(units, issue.number, issueOfPr)
        .filter(one => !one.labelled && !requestPicked(one)).map(one => one.id)
      const start = !row.actions.includes('start') ? {}
        : { pick: issue.number, ...(starts.length > 0 ? { starts } : {}) }
      return { ...row, ...start, actions: withOrderMoves(row.actions, index, ordered.length) }
    }),
    // Cancel ends the newest request that waits on the issue, and leaves the run that works it alone. An issue whose
    // PR's comment the dispatcher hasn't answered has no request yet, so no Cancel. One whose draft conflicts with main
    // goes back to fix the conflicts.
    ...inFlight.filter(issue => agentQueued.has(issue.number)).map(issue => {
      const request = waitingRequest(units, issue.number, issueOfPr)
      const note = drafts.get(`agent/issue-${issue.number}`)?.mergeable === 'CONFLICTING' ? 'fixing conflicts'
        : 'back to its PR'
      return withStart({ key: `rerun-${issue.number}`, text: `${issue.title}  (${note})`, url: issue.url,
        ...workRef(issue.number), title: issue.title, note, request,
        actions: (request === undefined ? ['open'] : ['open', 'cancel']) as RowAction[] }, issue)
    }),
    ...ready.filter(pr => agentQueued.has(agentWork(pr) ?? Number.NaN)).map(pr => {
      const asked = waitingRequests(units, agentWork(pr) ?? Number.NaN, issueOfPr)
      const request = asked.at(-1)?.id
      return withStart({ ...fixingRow(pr.title, pr.mergeable === 'CONFLICTING', { ...prRef(pr), url: pr.url }),
        ...(request === undefined ? {} : { request, actions: ['open', 'cancel'] as RowAction[] }) },
      byNumber.get(agentIssue(pr.headRefName) ?? Number.NaN), asked)
    }),
  ]
  // A request the dispatcher holds that no row above shows gets a row of its own: one on an issue, a fix or an agent's
  // run on a PR, or a chore. A row that names a PR stands for the PR's issue, or for the PR when it works none.
  const queuedIssues = new Set(queued.map(row => (row.pr ? row.issue ?? row.number : row.number)))
  queued.push(...unitRequests(units, unitContext, queuedIssues).map(row => {
    const asked = units.filter(one => one.id === row.request)
    return withStart(row, asked[0]?.labelled ? byNumber.get(row.number ?? Number.NaN) : undefined, asked)
  }))
  // A card whose request waits for memory, the usage limit, or a free slot says so after its own note.
  const queuedRows = queued.map(row => queuedWait(row, units, issueOfPr))

  // Open lists the issues free to start, newest first. Blocked lists the parked ones, newest first, with why each
  // waits, then the ones waiting on a stacked PR's base.
  const free = issues.filter(issue => (issue.status === 'open' || issue.status === 'on-hold') && !parked(issue))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .map(issue => ({ issue, held: heldBy(issue.status, issue.blockedBy) }))
  const next: Row[] = free.filter(({ held }) => held === null).map(({ issue }) => ({ key: `next-${issue.number}`,
    text: issue.title, number: issue.number, url: issue.url, title: issue.title,
    actions: ['open', 'ready', 'to-backlog', 'close'] as RowAction[] }))
  // An issue held only by open blockers can be queued already: its run waits until they close. Unblock frees a parked
  // issue of its hold and its open blockers; an issue waiting on a stacked PR's base has neither to drop. Resume queues
  // an issue on hold, such as one Pause stopped, at the top of the start order.
  const blocked: Row[] = [...free.flatMap(({ issue, held }) => (held === null ? []
    : [{ key: `blocked-${issue.number}`, text: `${issue.title}  (${held})`, number: issue.number, url: issue.url,
      title: issue.title, note: held,
      actions: (issue.status === 'on-hold' ? ['open', 'unpause', 'unblock', 'close']
        : ['open', 'ready', 'unblock', 'close']) as RowAction[] }])),
  ...stackWaits]

  // Move to Open takes the idea label off.
  const thoughts = issues.filter(issue => isIdea(names(issue), issue.status))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  const ideas: Row[] = thoughts.map(issue => ({ key: `idea-${issue.number}`,
    text: issue.title, number: issue.number, url: issue.url, title: issue.title,
    actions: ['open', 'to-open', 'close'] as RowAction[] }))
  const later: Row[] = issues.filter(issue => isBacklog(names(issue), issue.status))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .map(issue => ({ key: `backlog-${issue.number}`, text: issue.title, number: issue.number, url: issue.url,
      title: issue.title, actions: ['open', 'to-open', 'close'] as RowAction[] }))

  // Merging lists the merge queue in the order its PRs merge, then how far main is ahead of origin.
  const queuedPrs = new Map(prs.map(pr => [pr.number, pr]))
  const origin = originRow(mergeQueue)
  const heldChecks = waitingChecks(read.waitingChecks)
  // A review the queue stopped on uncommitted changes says which files and where, and the stop leads Needs You. One
  // whose queue waits on an auto-fix or a branch check the dispatcher holds says why it is held.
  const mergingRows: Row[] = [...mergeQueue.queue.flatMap(({ pr: number, note, stopped }) => {
    const pr = queuedPrs.get(number)
    const stop = stopOf(stopped)
    return pr ? [mergingRow(pr.title, stop ? `${stop.files} uncommitted in ${stop.where}`
      : mergeWait(note, number, pr.headRefName, units, heldChecks), { ...prRef(pr), url: pr.url },
    stop ? 'stopped' : undefined)] : []
  }), ...(origin ? [origin] : [])]
  const stopped = stoppedRow(mergeQueue)
  const recent: Row[] = merged.sort((a, b) => b.mergedAt.localeCompare(a.mergedAt))
    .map(pr => ({ key: `merged-${pr.number}`, text: pr.title, time: when(pr.mergedAt, zone),
      ...prRef(pr), url: pr.url, title: pr.title, since: pr.mergedAt, actions: ['open'] as RowAction[] }))

  // Each open issue's row carries the issue's labels, which the web page draws in their colors, and how many of its
  // sub-issues are done, when it has any.
  const tagged = (rows: Row[]) => rows.map(row => {
    const issue = byNumber.get(row.issue ?? (row.pr ? Number.NaN : row.number ?? Number.NaN))
    const summary = issue?.subIssues
    return { ...row, ...(issue?.labels.length ? { labels: issue.labels } : {}),
      ...(summary?.total ? { subs: { total: summary.total, done: summary.completed } } : {}) }
  })
  const checks = runningChecks(read.checks)
  return { at: clock, runners: tagged([...unitRows(units, unitContext), ...local]),
    slots: slotCounts(read.slots, units, checks), checks: tagged(checkRows(checks, unitContext)),
    needs: tagged(stopped ? [stopped, ...needs] : needs),
    merging: tagged(mergingRows), design: tagged(design), designTotal: undecided.length, queued: tagged(queuedRows),
    next: tagged(next), blocked: tagged(blocked), backlog: tagged(later), ideas: tagged(ideas),
    merged: recent,
    ...choreLines(read.chores ?? []), github: githubRows(mergeQueue, backupOf(read.backup), zone), error: null }
}

/**
 * The issues picked with Start, as `owner/repo#n`, and the requests, by run id, from the desk's mode as lib/hold.py's
 * status gives it, and whether `repo`'s project is on Hold, where Start picks for Resume; null unless the project is in
 * Manual or on Hold, by a mode of its own or the desk's.
 */
export function startPicks(hold: unknown, repo = ''):
  { picked: Set<string>; runs: Set<number>; held: boolean } | null {
  if (typeof hold !== 'object' || hold === null) {
    return null
  }
  const mode = hold as { state?: string; picked?: unknown; pickedRuns?: unknown; projects?: Record<string, string> }
  const state = mode.projects?.[repo] ?? mode.state
  return state === 'manual' || state === 'held'
    ? { picked: new Set(Array.isArray(mode.picked) ? mode.picked.filter(one => typeof one === 'string') : []),
      runs: new Set(Array.isArray(mode.pickedRuns) ? mode.pickedRuns.filter(one => typeof one === 'number') : []),
      held: state === 'held' } : null
}

// Where each column of agent-desk chores status starts when every chore's name fits the first column's 14 characters.
const CHORE_COLUMNS = [0, 15, 22, 51]
// The header's title of each column after the first.
const CHORE_HEADS = ['Every', 'Last run', 'Next']

/**
 * Where each column of agent-desk chores status starts, read from its header row: chores.sh widens the first column to
 * fit the longest chore's name.
 */
export function choreColumns(header = ''): number[] {
  const starts = [0, ...CHORE_HEADS.map(head => header.search(new RegExp(` ${head}( |$)`)) + 1)]
  return starts.slice(1).every((start, index) => start > (starts[index] ?? 0)) ? starts : CHORE_COLUMNS
}

/** The chores status lines, and the chores they list in columns. */
export function choreLines(lines: string[]): { chores: string[]; choreList: Chore[] } {
  const kept = lines.map(line => line.trimEnd()).filter(Boolean)
  const columns = choreColumns(kept[0])
  const cell = (line: string, index: number) => line.slice(columns[index], columns[index + 1]).trim()
  return { chores: kept, choreList: kept.slice(1).map(line => ({ name: cell(line, 0), every: cell(line, 1),
    last: cell(line, 2), next: cell(line, 3) })) }
}

/**
 * How lib/pushes.py's last push of main went: `ok`; `retrying` after a failure, with git's `error` and the seconds
 * until the next try; `rejected` while origin refuses main, as when origin's main moved; or `none` without an origin.
 * `pushedAt` is when main last went to origin, in seconds since the epoch, and `branches` lists the agent branches the
 * last push failed to send.
 */
type PushState = { state: string; error?: string; retryIn?: number; pushedAt?: number; branches?: string[] }

/**
 * The agent branches as lib/pushes.py counts them: how many match origin's copy and how many wait for a push, when a
 * push last sent one, and the ones the last push failed to send with git's `error`.
 */
type BranchPushes = { pushed: number; waiting: number; pushedAt?: number; failed?: string[]; error?: string }

/**
 * The merge queue as lib/merges.py last saved it: each queued PR, with the stop that holds it when the queue stopped
 * on uncommitted changes to land it, why each PR that left it left and the head it had then, how many commits main
 * holds that origin's main lacks, the push queue's state, and its agent branches.
 */
type MergeQueue = { queue: { pr: number; note: string; stopped?: QueueStop }[]
  dropped: { pr: number; reason: string; head?: string | null }[]; ahead: number; push: PushState | null
  agentBranches?: BranchPushes | null }

/**
 * Where the merge queue stopped on uncommitted changes, as lib/merges.py's stop_of words it: "the main checkout" or a
 * worktree's folder name, and the files, up to three and how many more.
 */
type QueueStop = { where: string; files: string }

/** A queued PR's stop, or null for one the queue doesn't hold on uncommitted changes. */
export function stopOf(stopped: unknown): QueueStop | null {
  const stop = (typeof stopped === 'object' && stopped !== null ? stopped : {}) as Partial<QueueStop>
  return typeof stop.where === 'string' && typeof stop.files === 'string' ? { where: stop.where, files: stop.files }
    : null
}

/**
 * Needs You's row for a merge queue stopped on uncommitted changes: where they are, their files, the PRs whose landing
 * they hold, and what clears them. The web page shows it among its header's notices; null while the queue moves.
 */
export function stoppedRow(queue: MergeQueue): Row | null {
  const held = queue.queue.flatMap(one => {
    const stop = stopOf(one.stopped)
    return stop ? [{ pr: one.pr, stop }] : []
  })
  const first = held[0]
  if (first === undefined) {
    return null
  }
  const prs = held.map(one => `#${one.pr}`)
  const which = prs.length === 1 ? `${prs[0]} changes` : `${prs.slice(0, -1).join(', ')} and ${prs.at(-1)} change`
  return { key: 'merge-stopped', text: `merge queue stopped: ${first.stop.where} has uncommitted changes to ${
    first.stop.files}, which ${which}. Commit, stash, or undo them.`, notice: 'warning', actions: [] }
}

/** The merge queue in the board's read, or an empty one when the read holds none the board can use. */
export function mergeQueueOf(saved: unknown): MergeQueue {
  const parsed = (typeof saved === 'object' && saved !== null ? saved : {}) as Partial<MergeQueue>
  const push = parsed.push
  const branches = parsed.agentBranches
  return { queue: Array.isArray(parsed.queue) ? parsed.queue : [],
    dropped: Array.isArray(parsed.dropped) ? parsed.dropped : [],
    ahead: typeof parsed.ahead === 'number' && parsed.ahead > 0 ? parsed.ahead : 0,
    push: typeof push === 'object' && push !== null && typeof push.state === 'string' ? push : null,
    agentBranches: typeof branches === 'object' && branches !== null && typeof branches.pushed === 'number'
      && typeof branches.waiting === 'number' ? branches : null }
}

/**
 * How the last backup of the store went, as lib/backup.py saved it: when it ran, its remote or null, when its newest
 * commit was made and its last push went, in seconds since the epoch, whether a commit waits for the hourly push, and
 * the error of a push or a backup that failed.
 */
export type BackupState = { at: number; remote: string | null; committedAt?: number; pushedAt?: number
  waiting?: boolean; error?: string }

/** The backup's state in the board's read, or null before the first backup or when the read holds none it can use. */
export function backupOf(saved: unknown): BackupState | null {
  const parsed = (typeof saved === 'object' && saved !== null ? saved : null) as Partial<BackupState> | null
  return parsed && typeof parsed.at === 'number'
    ? { ...parsed, at: parsed.at, remote: typeof parsed.remote === 'string' ? parsed.remote : null } : null
}

/** A time in seconds since the epoch as the GitHub rows word it, such as `Nov 3 14:05`, in the read's zone. */
function whenOf(seconds: number, zone: string): string {
  return when(new Date(seconds * 1000).toISOString(), zone).replace(/ +/g, ' ')
}

/** One row of the GitHub group: what the desk touches, how it last went, and the tone it reads in. */
function touchpoint(name: string, note: string, failing: boolean): Row {
  return { key: `github-${name.replace(/ /g, '-')}`, text: `${name.padEnd(15)}${note}`, title: name, note,
    notice: failing ? 'warning' : 'muted', actions: [] }
}

/**
 * The GitHub group's rows, each from state saved on this machine and none from GitHub: main's push, as Merging's row
 * reads it; the agent branches' pushes; and the backup's commit and push, which covers every project, so the page
 * shows it once. A project whose `host` is `none`, or without an origin, has no main or branch row.
 */
export function githubRows(queue: MergeQueue, backup: BackupState | null, zone: string): Row[] {
  const rows: Row[] = []
  const push = queue.push
  if (push && push.state !== 'none') {
    const ahead = `${queue.ahead} commit${queue.ahead === 1 ? '' : 's'} ahead of origin`
    const last = push.pushedAt ? `last push ${whenOf(push.pushedAt, zone)}` : ''
    const note = push.state === 'rejected' ? `${ahead}; origin refused the push: ${push.error ?? 'git failed'}`
      : push.state === 'retrying'
        ? `${ahead}; push failed: ${push.error ?? 'git failed'}; retrying in ${waitOf(push.retryIn ?? 0)}`
        : queue.ahead > 0 ? `${ahead}, waiting for the next push` : ['matches origin', last].filter(Boolean).join(', ')
    rows.push(touchpoint('main', note, push.state === 'rejected' || push.state === 'retrying'))
    const branches = queue.agentBranches
    if (branches) {
      const failed = branches.failed ?? []
      const waits = branches.waiting === 0 ? '' : push.state === 'retrying'
        ? `${branches.waiting} wait for main's push` : `${branches.waiting} wait for the next push`
      const sent = branches.pushedAt ? `last push ${whenOf(branches.pushedAt, zone)}` : ''
      const counts = branches.pushed + branches.waiting === 0 ? 'none to push'
        : [`${branches.pushed} pushed`, waits || sent].filter(Boolean).join(', ')
      const note = failed.length > 0 ? `${counts}; couldn't push ${failed.join(', ')}: ${branches.error || 'git failed'}`
        : counts
      rows.push(touchpoint('agent branches', note, failed.length > 0))
    }
  }
  if (backup === null) {
    rows.push(touchpoint('backup', 'no backup has run', false))
    return rows
  }
  const committed = backup.committedAt ? `last commit ${whenOf(backup.committedAt, zone)}` : 'no commit yet'
  const pushed = backup.remote === null ? 'no backup remote'
    : backup.pushedAt ? `last push ${whenOf(backup.pushedAt, zone)}` : `never pushed to ${backup.remote}`
  const waits = backup.remote !== null && backup.waiting && !backup.error ? 'a commit waits for the hourly push' : ''
  const note = [committed, pushed, waits].filter(Boolean).join(', ')
  rows.push(touchpoint('backup', backup.error ? `${note}; ${backup.error}` : note, Boolean(backup.error)))
  return rows
}

/** Seconds as the board words a wait: `45s`, `3m`, or `1h`. */
function waitOf(seconds: number): string {
  return seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.round(seconds / 60)}m` : `${Math.round(seconds / 3600)}h`
}

/**
 * Merging's row for main ahead of origin, with what holds the push back: null while main matches origin and its push
 * neither failed nor was refused.
 */
export function originRow(queue: MergeQueue): Row | null {
  const push = queue.push
  const notes = push?.state === 'rejected' ? [`origin refused the push: merge origin's main by hand`]
    : push?.state === 'retrying' ? [`push failed: ${push.error ?? 'git failed'}; retrying in ${waitOf(push.retryIn ?? 0)}`]
      : []
  if (queue.ahead === 0 && notes.length === 0) {
    return null
  }
  if (push?.branches?.length) {
    notes.push(`couldn't push ${push.branches.join(', ')}`)
  }
  const title = queue.ahead > 0 ? `main is ${queue.ahead} commit${queue.ahead === 1 ? '' : 's'} ahead of origin`
    : 'main matches origin'
  const note = notes.join('; ')
  const failing = push?.state === 'rejected' || push?.state === 'retrying'
  return { key: 'origin', text: note ? `${title}  (${note})` : title, title, ...(note ? { note } : {}),
    notice: failing ? 'warning' : 'muted', actions: [] }
}

/**
 * What each conflicting agent PR's automatic fix waits on, by PR, from the plan the last `agent-desk fixes` status or
 * tick saved.
 */
function fixVerdicts(plan: unknown): Map<number, string> {
  return new Map((Array.isArray(plan) ? plan : [])
    .filter(one => typeof one?.pr === 'number' && typeof one?.verdict === 'string')
    .map(one => [one.pr as number, one.verdict as string]))
}
