// What the board's buttons run, and the failed-check readers behind Fix. Like board.ts, it imports no Claude Code
// module and no Node module, so the pane and the web server share it.
import type { Row } from '../types'
import type { Check, Host, Pr, Project } from './board.ts'
import { type ListMove, type OrderMove, prName } from './moves.ts'
import { DESK, DISPATCH, FAILURES, HOLD, parsed, run } from './run.ts'

// The ids of failed runs the person dismissed, kept across sessions in the host's store; the newest MAX_DISMISSED of
// them.
const DISMISSED_KEY = 'dismissed-runs'
const MAX_DISMISSED = 200

// A visible footer says where it came from. The comment reaches the agent through the store's to_agent flag.
const FOOTER = '\n\n<sub>Posted from the agent-desk inbox</sub>'
const FIX_BODY = 'This PR has merge conflicts with main. Merge main into the branch, resolve the '
  + `conflicts, rerun the tests, and commit.${FOOTER}`
// How long a row's note may run.
const NOTE_CHARS = 120
// The ends of a check that fail a PR's head. A `merge` check that failed found conflicts with main, which the PR's
// `mergeable` says, and no failed test.
const FAILED_CHECKS = new Set(['failed', 'timed-out', 'lost'])

/** A PR's failed checks, each with its name, its log when it has one, and the base it merged when it names one. */
function failedChecks(checks: Check[] = []): { name: string; log?: string; against: string }[] {
  return checks.filter(check => FAILED_CHECKS.has(check.status) && check.name !== 'merge')
    .map(check => ({ name: check.name, ...(check.log ? { log: check.log } : {}), against: mergedWith(check) }))
}

/** The base a check merged, such as ` merged with main at 01840f3`, or nothing when the check names none. */
function mergedWith(check: Check): string {
  return check.baseSha ? ` merged with ${check.base ?? 'main'} at ${check.baseSha.slice(0, 7)}` : ''
}

/** Whether a PR has checks and every one passed; a PR with none, or with one still queued or running, hasn't. */
export function checksPassed(checks: Check[] = []): boolean {
  return checks.length > 0 && checks.every(check => check.status === 'passed')
}

// Each failed check's lines, read once: a check's log doesn't change once it ends.
const failures = new Map<string, string[]>()

/**
 * The lines of a failed check's log that say what failed, as lib/failures.py log picks them, without the scratch
 * worktree's paths; none when it can't read the log, which a later refresh tries again.
 */
async function failureOf(host: Host, project: Project, log: string): Promise<string[]> {
  const known = failures.get(log)
  if (known !== undefined) {
    return known
  }
  const out = await run(host, project, ['bash', '-c', `exec python3 -B ${FAILURES} log "$1"`, '_', log])
  if (out === null) {
    return []
  }
  const lines = out.split('\n').filter(line => line.trim() !== '')
  failures.set(log, lines)
  return lines
}

/** A failing PR's note: its first failed check and that check's first error, by file name; undefined when none fail. */
export async function failingNote(host: Host, project: Project, pr: Pick<Pr, 'checks'>): Promise<string | undefined> {
  const [first] = failedChecks(pr.checks)
  if (first === undefined) {
    return undefined
  }
  const [error] = first.log === undefined ? [] : await failureOf(host, project, first.log)
  const note = `${first.name} failed${first.against}${error ? `: ${error.replace(/^\S*\//, '')}` : ''}`
  return note.length > NOTE_CHARS ? `${note.slice(0, NOTE_CHARS - 1)}…` : note
}

/**
 * A ready PR's note when its check passed merged with an older commit of its base, such as
 * `test passed, 2 merges behind`; undefined while it passed against the newest or hasn't passed.
 */
export function behindNote(checks: Check[] = []): string | undefined {
  const [check] = checks
  const behind = checks.length === 1 && check?.status === 'passed' ? check.behind ?? 0 : 0
  return behind > 0 ? `${check?.name} passed, ${behind} ${behind === 1 ? 'merge' : 'merges'} behind` : undefined
}

export async function dismissedRuns(host: Host): Promise<number[]> {
  const stored = await host.get(DISMISSED_KEY)
  return Array.isArray(stored) ? stored.filter((id): id is number => typeof id === 'number') : []
}

/**
 * Runs a failed or timed-out run again: the dispatcher takes a new request for its kind and target, so an agent run
 * takes up its issue or review again from the worktree and commits it left, and a chore or cleanup does its work again.
 * The run leaves Needs You while it runs, and comes back if it fails again. In Manual, a run Agent-Ready or Continue
 * asked for waits again in Queued for Start. A dispatcher run carries its id below zero on the board. False when the dispatcher couldn't take the request, or for any other id.
 */
export async function retryRun(host: Host, project: Project, id: number): Promise<boolean> {
  if (id >= 0) {
    return false
  }
  return (await run(host, project, ['bash', '-c', `exec python3 -B ${DISPATCH} again "$1"`, '_', String(-id)]))
    !== null
}

/**
 * Ends one run request that still waits, as `agent-desk request --cancel` does, and leaves every other run alone. False
 * when the run works or has ended, which the dispatcher refuses.
 */
export async function cancelRequest(host: Host, project: Project, id: number): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec python3 -B ${DISPATCH} cancel "$1"`, '_', String(id)]))
    !== null
}

/**
 * Sends a queued or waiting issue back to Open: the dispatcher cancels each agent request that waits for it, sets it
 * open, and drops its Manual pick, so nothing starts it until Agent-Ready again. False when an agent run works the
 * issue, which the dispatcher refuses, changing nothing.
 */
export async function unqueueIssue(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec python3 -B ${DISPATCH} unqueue "$1"`, '_', String(issue)]))
    !== null
}

/**
 * Pauses an issue's agent run: the dispatcher puts the issue on hold, cancels the requests that wait for it, and stops
 * the run, whose worktree, commits, and session stay. False when no agent run works the issue, which it refuses.
 */
export async function pauseIssue(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec python3 -B ${DISPATCH} pause "$1"`, '_', String(issue)]))
    !== null
}

/**
 * Stops the command an issue's agent run waits on, as `agent-desk stop-command` does: the run ends the command's
 * processes and goes on with its failure. False when no agent run works the issue, the run waits on no command, or it
 * doesn't answer.
 */
export async function stopCommand(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec python3 -B ${DISPATCH} stop-command "$1"`, '_',
    String(issue)])) !== null
}

/**
 * Resumes an issue on hold: the dispatcher queues it at the top of the start order, and its next run takes up the
 * session a paused run left. False when the issue isn't on hold, which it refuses.
 */
export async function unpauseIssue(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec python3 -B ${DISPATCH} unpause "$1"`, '_', String(issue)]))
    !== null
}

/** What the desk says when Move to Open couldn't send a queued issue back, which its started run refuses. */
export function unqueueFailure(issue: number): string {
  return `Couldn't move #${issue} to Open: its run started.`
}

/**
 * Starts a Queued row in Manual: picks its issue, the row's `pick`, so its fresh start runs, and starts again the run
 * Manual ended for it, and picks each request in the row's `starts`, so the dispatcher starts it. On Hold it only
 * picks, so Resume into Manual starts them. Start is the only action that picks. False when lib/hold.py couldn't.
 */
export async function startRow(host: Host, project: Project, row: Pick<Row, 'pick' | 'starts'>): Promise<boolean> {
  const issue = row.pick === undefined
    || (await run(host, project, ['bash', '-c', `exec python3 -B ${HOLD} start "$1"`, '_', String(row.pick)])) !== null
  const requests = !row.starts?.length || (await run(host, project, ['bash', '-c',
    `exec python3 -B ${HOLD} start-runs "$@"`, '_', ...row.starts.map(String)])) !== null
  return issue && requests
}

/**
 * Drops a queued or waiting issue's Manual pick, and those of its agent requests that wait, so its run waits for Start
 * again and the issue stays in Queued. False when lib/hold.py refuses, as when the issue's run claimed it meanwhile.
 */
export async function unpickIssue(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec python3 -B ${HOLD} unpick "$1"`, '_', String(issue)])) !== null
}

/** What the desk says when Unpick couldn't drop an issue's pick, which its run claimed or Auto dropped already. */
export function unpickFailure(issue: number): string {
  return `Couldn't unpick #${issue}: its run claimed it, or its pick dropped already.`
}

/** What a Queued row's messages call it: its PR or issue by number, or its title, as a chore's row has no number. */
export function queuedName(row: Pick<Row, 'number' | 'issue' | 'pr' | 'title' | 'text'>): string {
  if (row.number === undefined) {
    return row.title ?? row.text
  }
  return row.pr ? prName(row) : `#${row.number}`
}

/**
 * Starts a stopped issue's agent again: reruns its newest failed or cancelled agent run as Retry does, or without one,
 * asks for a fresh start of the issue, as Agent-Ready does. Neither picks the issue, so in Manual the run waits in
 * Queued for Start. The issue keeps its labels: agent-ready is the person's to add. A benchmark issue's run gets the
 * benchmark limit. False when agent-desk couldn't.
 */
export async function restartIssue(host: Host, project: Project,
  row: Pick<Row, 'number' | 'rerun' | 'labels'>): Promise<boolean> {
  if (row.rerun !== undefined) {
    return retryRun(host, project, row.rerun)
  }
  return (await run(host, project, ['bash', '-c', START_AGENT, '_', String(row.number ?? ''),
    row.labels?.includes('benchmark') ? '--benchmark' : ''])) !== null
}

// Asks the dispatcher for a fresh start of an issue's agent, as `bash -c` runs it with the issue and `--benchmark` or
// nothing. EVENT=issues marks it as Agent-Ready's request, so Manual holds it until Start, and the blockers gate
// applies. The dispatcher starts the run once its gates clear, or holds the request until its service runs.
export const START_AGENT = `exec "${DESK}" request agent "$1" --env EVENT=issues $2`

/**
 * Why an issue closed: completed or not planned, as the board and the page name them and `agent-desk issue close
 * --reason` takes them, or as a duplicate of the issue `duplicateOf`, which `--duplicate-of` takes.
 */
export type CloseReason = 'completed' | 'not planned' | { duplicateOf: number }

/**
 * Runs `agent-desk <args>` in the project's checkout, with `stdin` as its input when given, and returns its output, or
 * null when it failed.
 */
function desk(host: Host, project: Project, args: string[], stdin?: string): Promise<string | null> {
  return run(host, project, ['bash', '-c', `exec "${DESK}" "$@"`, '_', ...args], project.root, stdin)
}

/**
 * Closes an issue in the store, with a comment when one is given, in one write, so a close that fails leaves no
 * comment; false when agent-desk couldn't. The board's Close button closes as completed: the audit skill learns a rule
 * from each audit issue closed as not planned, from the comment saying why. A close as a duplicate comments
 * `Duplicate of #<n>` before the comment, and the store refuses an original that isn't another of its issues.
 */
export async function closeIssue(host: Host, project: Project, issue: number, reason: CloseReason = 'completed',
  comment?: string): Promise<boolean> {
  const close = ['issue', 'close', String(issue), ...typeof reason === 'string'
    ? ['--reason', reason.replace(' ', '-')] : ['--duplicate-of', String(reason.duplicateOf)]]
  return (comment ? await desk(host, project, [...close, '--comment-file', '-'], comment)
    : await desk(host, project, close)) !== null
}

/**
 * Takes needs-design off an issue whose design is settled, which moves it to Open; false when agent-desk couldn't.
 */
export async function markDesigned(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await desk(host, project, ['issue', 'edit', String(issue), '--remove-label', 'needs-design'])) !== null
}

/**
 * Moves a queued or waiting issue in its project's start order, one place up, one place down, to the top or the
 * bottom, or to a place from 1; false when the store refused, as for an issue that started or left Queued since the
 * board's read.
 */
export async function moveInStartOrder(host: Host, project: Project, issue: number, where: OrderMove | number):
  Promise<boolean> {
  const move = typeof where === 'number' ? ['--to', String(where)] : [`--${where}`]
  return (await desk(host, project, ['queue', 'order', String(issue), ...move])) !== null
}

/** What the desk says when a move in the start order failed. */
export function orderFailure(issue: number): string {
  return `Couldn't move #${issue} in the start order: it may have started or left Queued.`
}

/**
 * Sorts a project's start order into its default, as `agent-desk queue order --sort` does: the issues that can start
 * now first, blockers ahead of what they block, then by number. Given `repo`, the command names the project with
 * `-R`; without it, the store finds the project from the checkout. False when the store refused.
 */
export async function sortStartOrder(host: Host, project: Project, repo?: string): Promise<boolean> {
  return (await desk(host, project, ['queue', 'order', '--sort', ...(repo ? ['-R', repo] : [])])) !== null
}

/** What the desk says when the store refused to sort a project's start order. */
export const SORT_FAILURE = 'Couldn\'t sort the start order; the list shows the store\'s order.'

/**
 * Moves an issue in its project's list order, the order of Open, Blocked, Needs Design, Backlog, and Ideas, right above
 * or right below another issue; false when the store refused, as for an issue that closed or was queued since the
 * board's read.
 */
export async function moveInListOrder(host: Host, project: Project, issue: number, to: ListMove): Promise<boolean> {
  const move = 'before' in to ? ['--before', String(to.before)] : ['--after', String(to.after)]
  return (await desk(host, project, ['list', 'order', String(issue), ...move])) !== null
}

/** What the desk says when a move in the list order failed. */
export function listOrderFailure(issue: number): string {
  return `Couldn't move #${issue} in the list order: it may have closed or been queued.`
}

/**
 * Puts a section's issues back in number order, highest first, as `agent-desk list order --reset` does with their
 * numbers: they keep the places they hold in the project's list order among them. False when the store refused.
 */
export async function sortListOrder(host: Host, project: Project, issues: number[]): Promise<boolean> {
  return (await desk(host, project, ['list', 'order', '--reset', ...issues.map(String)])) !== null
}

/** What the desk says when the store refused to sort a section's issues. */
export function listSortFailure(section: string): string {
  return `Couldn't sort ${section}; the list shows the store's order.`
}

/** Takes idea off an issue in Ideas, which moves it to Open; false when agent-desk couldn't. */
export async function moveIdeaToOpen(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await desk(host, project, ['issue', 'edit', String(issue), '--remove-label', 'idea'])) !== null
}

/** Takes backlog off an issue in Backlog, which moves it to Open; false when agent-desk couldn't. */
export async function moveBacklogToOpen(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await desk(host, project, ['issue', 'edit', String(issue), '--remove-label', 'backlog'])) !== null
}

/** Labels an issue in Open backlog, which moves it to Backlog; false when agent-desk couldn't. */
export async function labelBacklog(host: Host, project: Project, issue: number): Promise<boolean> {
  return (await desk(host, project, ['issue', 'edit', String(issue), '--add-label', 'backlog'])) !== null
}

/**
 * Frees a parked issue, which moves it to Open: takes it off hold when it is on-hold, and removes its "blocked by"
 * links to open issues, read when pressed. Links to closed blockers stay, as the history of what held it. False when
 * agent-desk couldn't read the issue or edit it.
 */
export async function unblockIssue(host: Host, project: Project, issue: number): Promise<boolean> {
  const read = parsed<{ status: string; blockedBy: { number: number; state: string }[] }>(
    await desk(host, project, ['issue', 'view', String(issue), '--json', 'status,blockedBy']))
  if (read === null) {
    return false
  }
  const held = read.status === 'on-hold'
  const open = read.blockedBy.filter(one => one.state.toLowerCase() === 'open').map(one => one.number)
  if (!held && open.length === 0) {
    return true
  }
  return (await desk(host, project, ['issue', 'edit', String(issue), ...(held ? ['--remove-label', 'on-hold'] : []),
    ...open.flatMap(one => ['--remove-blocked-by', String(one)])])) !== null
}

/** Hides a failed run from Needs You, in this host's store. */
export async function dismissRun(host: Host, id: number): Promise<void> {
  await host.set(DISMISSED_KEY, [...(await dismissedRuns(host)).filter(one => one !== id), id].slice(-MAX_DISMISSED))
}

/**
 * Labels an issue agent-ready, which moves it to Queued, and asks the dispatcher for its agent run, as a label did on
 * GitHub, with the benchmark limit for a benchmark issue. It picks nothing, so in Manual the run waits for Start. False
 * when agent-desk couldn't.
 */
export async function labelReady(host: Host, project: Project, issue: number, labels: string[] = []):
  Promise<boolean> {
  if (await desk(host, project, ['issue', 'edit', String(issue), '--add-label', 'agent-ready']) === null) {
    return false
  }
  return (await desk(host, project, ['request', 'agent', String(issue), '--env', 'EVENT=issues',
    ...(labels.includes('benchmark') ? ['--benchmark'] : [])])) !== null
}

/**
 * Queues a PR with agent-desk merges, which merges main into it when main moved since its checks started, and merges
 * it once they pass against the main it lands on. False when the queue couldn't take it.
 */
export async function queueMerge(host: Host, project: Project, pr: number): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec "${DESK}" merges add ${pr}`],
    project.main ?? project.root)) !== null
}

/**
 * Marks a draft review ready, which shows it in Needs You to review and merge; false when agent-desk couldn't.
 */
export async function markReady(host: Host, project: Project, pr: number): Promise<boolean> {
  return (await desk(host, project, ['review', 'ready', String(pr)])) !== null
}

/**
 * Closes a review without merging it, which asks the dispatcher for its branch's cleanup; false when agent-desk
 * couldn't.
 */
export async function closeReview(host: Host, project: Project, pr: number): Promise<boolean> {
  return (await desk(host, project, ['review', 'close', String(pr)])) !== null
}

/** What the desk says after Merge on a PR, which the queue took or couldn't. */
export function queuedNote(row: Pick<Row, 'number' | 'issue'>, done: boolean): string {
  return done ? `Queued ${prName(row)} to merge; it merges once its checks pass against the main it lands on.`
    : `Couldn't queue ${prName(row)} to merge.`
}

/**
 * Sends an agent back to an agent branch's PR to merge main and fix it: to resolve its conflicts, and to make its
 * failed checks pass, quoting their errors. The comment goes to the agent, so the dispatcher asks for its run. False
 * when agent-desk can't comment.
 */
export async function askToFix(host: Host, project: Project, pr: number): Promise<boolean> {
  const view = parsed<Pick<Pr, 'mergeable' | 'checks'>>(await run(host, project,
    ['bash', '-c', `exec "${DESK}" review view "$1" --json mergeable,checks`, '_', String(pr)]))
  const body = await fixBody(host, project, view)
  return (await desk(host, project, ['review', 'comment', String(pr), '--to-agent', '--body-file', '-'], body))
    !== null
}

/** The comment that sends an agent back to fix a PR: for its conflicts alone, or for its failed checks' errors. */
export async function fixBody(host: Host, project: Project, pr: Pick<Pr, 'mergeable' | 'checks'> | null):
  Promise<string> {
  const failed = failedChecks(pr?.checks)
  if (failed.length === 0) {
    return FIX_BODY
  }
  const logs = [...new Set(failed.flatMap(one => one.log === undefined ? [] : [one.log]))]
  const errors = [...new Set((await Promise.all(logs.map(log => failureOf(host, project, log)))).flat())]
  const quoted = errors.length > 0 ? `:\n\n\`\`\`\n${errors.join('\n')}\n\`\`\`` : '.'
  const conflicts = pr?.mergeable === 'CONFLICTING'
  return `This PR's ${[...new Set(failed.map(one => one.name))].join(', ')} checks fail${quoted}\n\n`
    + `${conflicts ? 'It also has merge conflicts with main. ' : ''}Merge main into the branch`
    + `${conflicts ? ', resolve the conflicts' : ''}, fix the failures, rerun the tests, and commit.${FOOTER}`
}

/** Asks the dispatcher for a chore's run now; it waits for a free chore slot. False when agent-desk couldn't ask. */
export async function startChore(host: Host, project: Project, skill: string): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `exec "${DESK}" request chore "$1"`, '_', skill])) !== null
}
