// What the web server reads straight from agent-desk's store, lib/store.py's SQLite database: the newest event's id,
// which moves with every change, the projects whose events moved, the reviews that merged in a span, and what each
// run's agent reported. The server opens it read-only; every write goes through the agent-desk commands.
import { statSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'

/** The store's database, in agent-desk's state folder, as lib/store.py names it. */
export function storePath(env: NodeJS.ProcessEnv = process.env): string {
  return join(env.XDG_STATE_HOME ?? join(homedir(), '.local', 'state'), 'agent-desk', 'desk.sqlite')
}

let path = storePath()
let db: DatabaseSync | null = null
// The file the open handle reads, by device and inode, and how many times a handle has opened.
let file = ''
let opens = 0
// Each query that failed on its last read, with why; a query that reads again clears it. A streak lasts while any
// query fails, and only its first failure is logged.
const failing = new Map<string, string>()

/** Points the reads at another database, as the tests do, closing the one open. */
export function useStore(other: string): void {
  close()
  path = other
  failing.clear()
}

function close(): void {
  db?.close()
  db = null
  file = ''
}

/**
 * The database, opened once it exists and opened again when another file has replaced it; null before any agent-desk
 * command has made it.
 */
function open(): DatabaseSync | null {
  let now: string
  try {
    const info = statSync(path)
    now = `${info.dev}:${info.ino}`
  } catch {
    close()
    return null
  }
  if (now !== file) {
    close()
    db = new DatabaseSync(path, { readOnly: true, timeout: 5_000 })
    file = now
    opens += 1
    // Failures from the file it replaced say nothing about this one.
    failing.clear()
  }
  return db
}

/** How many times the server has opened the store; a new count means the event ids before it name another file's. */
export function storeOpens(): number {
  return opens
}

/** Why the store can't be read, from the first query that failed on its last read, or null when every one read. */
export function storeProblem(): string | null {
  return failing.values().next().value ?? null
}

/**
 * The rows a query returns, or none when the store is missing or can't be read. The first failure while every query
 * read is logged, and storeProblem() names a failure until its query reads again.
 */
function rows<T>(sql: string, ...params: (string | number)[]): T[] {
  try {
    const found = (open()?.prepare(sql).all(...params) ?? []) as T[]
    failing.delete(sql)
    return found
  } catch (error) {
    const why = error instanceof Error ? error.message : String(error)
    if (failing.size === 0) {
      console.error(`agent-desk: can't read the store at ${path}: ${why}`)
    }
    failing.set(sql, why)
    return []
  }
}

/** The newest event's id, or 0 before the store holds any. */
export function lastEvent(): number {
  return rows<{ id: number }>('SELECT COALESCE(MAX(id), 0) AS id FROM events')[0]?.id ?? 0
}

/**
 * The newest event's id and the projects, by owner/repo, whose events came after `after`; `all` when one of those
 * events names no project.
 */
export function movedSince(after: number): { last: number; projects: Set<string>; all: boolean } {
  const moved = rows<{ project: string | null; last: number }>(
    'SELECT project, MAX(id) AS last FROM events WHERE id > ? GROUP BY project', after)
  return { last: Math.max(after, ...moved.map(one => one.last)),
    projects: new Set(moved.flatMap(one => one.project ?? [])), all: moved.some(one => one.project === null) }
}

/** A review that merged, as the store holds it, with when in ISO 8601. */
export type MergedReview = { number: number; title: string; mergedAt: string; branch: string }

/** A time in milliseconds as the store writes one: UTC to the second. */
function stamp(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d+Z$/, 'Z')
}

/** A project's reviews that merged in [since, until), in milliseconds, newest first, up to `limit`. */
export function mergedReviews(repo: string, since: number, until: number, limit: number): MergedReview[] {
  return rows<MergedReview>('SELECT items.number, items.title, items.closed_at AS mergedAt, reviews.branch'
    + ' FROM items JOIN reviews ON reviews.project = items.project AND reviews.number = items.number'
    + ' WHERE items.project = ? AND reviews.merged_sha IS NOT NULL AND items.closed_at >= ? AND items.closed_at < ?'
    + ' ORDER BY items.closed_at DESC, items.number DESC LIMIT ?', repo, stamp(since), stamp(until), limit)
}

/**
 * What the recorded runs behind a review cost, how many there were, and the minutes their GPU calls held a slot and
 * queued for one, null when none of them used the GPU.
 */
export type ReviewCost = { cost: number; runs: number; gpuHeld: number | null; gpuQueued: number | null }

/** What the recorded runs behind each of a project's reviews cost, by review, as ReviewCost reads. */
export function reviewCosts(repo: string): Map<number, ReviewCost> {
  return new Map(rows<ReviewCost & { review: number }>('SELECT review, COALESCE(SUM(cost), 0) AS cost,'
    + ' COUNT(*) AS runs, SUM(gpu_held) AS gpuHeld, SUM(gpu_queued) AS gpuQueued FROM runs'
    + ' WHERE project = ? AND review IS NOT NULL GROUP BY review', repo)
    .map(({ review, ...spent }) => [review, spent]))
}

/** A finished chore run, by its chore's skill, with what its agent reported; nulls where it reported nothing. */
export type ChoreRun = { skill: string; model: string | null; effort: string | null; minutes: number | null
  turns: number | null; review: number | null }

/**
 * A project's chore runs that finished, oldest first by when each ended: those that succeeded, failed, timed out, or
 * were lost, leaving out the ones someone cancelled and the requests the dispatcher ignored. Runs imported from the old
 * log have higher ids than newer runs the dispatcher recorded first, so the order goes by time.
 */
export function choreRuns(repo: string): ChoreRun[] {
  return rows<ChoreRun>('SELECT target AS skill, model, effort, minutes, turns, review FROM runs'
    + " WHERE project = ? AND kind = 'chore' AND status IN ('succeeded', 'failed', 'timed-out', 'lost')"
    + ' ORDER BY ended_at, id', repo)
}

/** An issue or a review as the store holds it: `state` is closed or a workflow state such as open or queued. */
export type StoredItem = { number: number; kind: 'issue' | 'review'; title: string; body: string; state: string
  stateReason: string | null; author: string | null; createdAt: string }

/** One of a project's issues or reviews, or null when it has none by that number. */
export function storedItem(repo: string, number: number): StoredItem | null {
  return rows<StoredItem>('SELECT number, kind, title, body, state, state_reason AS stateReason, author,'
    + ' created_at AS createdAt FROM items WHERE project = ? AND number = ?', repo, number)[0] ?? null
}

/**
 * A review's branch and base, its head as the dispatcher last saw the branch, its merge commit once it merged, and how
 * many commits a rebase-merge wrote onto the base, ending at that commit; null for a merge that wrote one.
 */
export type StoredReview = { branch: string; base: string; head: string | null; draft: boolean
  mergedSha: string | null; mergedCommits: number | null }

export function storedReview(repo: string, number: number): StoredReview | null {
  // Every column of `reviews`, so a store that lib/store.py hasn't yet given merged_commits still reads.
  const found = rows<{ branch: string; base: string; head: string | null; draft: number; merged_sha: string | null
    merged_commits?: number | null }>('SELECT reviews.*, COALESCE(branches.head_sha, reviews.head_sha) AS head'
    + ' FROM reviews LEFT JOIN branches ON branches.project = reviews.project AND branches.branch = reviews.branch'
    + ' WHERE reviews.project = ? AND reviews.number = ?', repo, number)[0]
  return found ? { branch: found.branch, base: found.base, head: found.head, draft: found.draft !== 0,
    mergedSha: found.merged_sha, mergedCommits: found.merged_commits ?? null } : null
}

/** An item's labels, by name. */
export function storedLabels(repo: string, number: number): string[] {
  return rows<{ name: string }>('SELECT name FROM labels WHERE project = ? AND number = ? ORDER BY name', repo, number)
    .map(one => one.name)
}

/**
 * A project's label colors by name, as six hex digits: each color `agent-desk import` copied from the host, and for any
 * other label an item carries, a color hashed from its name, so a label keeps one color without an import.
 */
export function labelColors(repo: string): Record<string, string> {
  // A store that lib/store.py hasn't yet given label_colors reads as one without colors.
  const copied = rows<{ name: string }>("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'label_colors'")
    .length === 0 ? [] : rows<{ name: string; color: string }>('SELECT name, color FROM label_colors WHERE project = ?',
    repo)
  const colors: Record<string, string> = Object.fromEntries(rows<{ name: string }>(
    'SELECT DISTINCT name FROM labels WHERE project = ?', repo).map(one => [one.name, hashedColor(one.name)]))
  for (const { name, color } of copied) {
    colors[name] = /^[0-9a-f]{6}$/i.test(color) ? color.toLowerCase() : hashedColor(name)
  }
  return colors
}

/** A label's color picked from its name: a hue from the name's FNV-1a hash, at a middling saturation and lightness. */
export function hashedColor(name: string): string {
  let hash = 0x811c9dc5
  for (const byte of new TextEncoder().encode(name)) {
    hash = Math.imul(hash ^ byte, 0x01000193) >>> 0
  }
  const hue = hash % 360
  const [saturation, lightness] = [0.6, 0.55]
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation
  const channel = (n: number): string => {
    const k = (n + hue / 30) % 12
    const value = lightness - chroma / 2 * Math.max(-1, Math.min(k - 3, 9 - k, 1))
    return Math.round(value * 255).toString(16).padStart(2, '0')
  }
  return channel(0) + channel(8) + channel(4)
}

/** A review a chore opened, by its branch, in the state gh names: OPEN, CLOSED, or MERGED. */
type ChoreReview = { number: number; branch: string; state: 'OPEN' | 'CLOSED' | 'MERGED' }

/** A project's reviews on `agent/chore-*` branches, which include the chores' schedule branch, by number. */
export function choreReviews(repo: string): ChoreReview[] {
  return rows<ChoreReview>('SELECT items.number, reviews.branch, CASE WHEN reviews.merged_sha IS NOT NULL'
    + " THEN 'MERGED' WHEN items.state = 'closed' THEN 'CLOSED' ELSE 'OPEN' END AS state"
    + ' FROM items JOIN reviews ON reviews.project = items.project AND reviews.number = items.number'
    + " WHERE items.project = ? AND reviews.branch GLOB 'agent/chore-*' ORDER BY items.number", repo)
}

/**
 * A comment as the store holds it, with its id, whether it went to the agent, and when its writer last edited it, or
 * null when never.
 */
export type StoredComment = { id: number; author: string | null; body: string; createdAt: string; toAgent: boolean
  editedAt: string | null }

/** A comment's row as `SELECT *` reads it, where a store lib/store.py hasn't yet migrated lacks the newer columns. */
type CommentRow = { id: number; author: string | null; body: string; created_at: string; to_agent: number
  edited_at?: string | null; path: string | null; line: number | null; head_sha: string | null
  sent_in?: number | null }

function commentOf(row: CommentRow): StoredComment {
  return { id: row.id, author: row.author, body: row.body, createdAt: row.created_at, toAgent: row.to_agent === 1,
    editedAt: row.edited_at ?? null }
}

/** An item's conversation, oldest first: its comments, leaving out a review's comments on lines of its diff. */
export function storedComments(repo: string, number: number): StoredComment[] {
  return rows<CommentRow>('SELECT * FROM comments WHERE project = ? AND number = ? AND path IS NULL ORDER BY id', repo,
    number).map(commentOf)
}

/** Comment `id` of a project, on any of its items, or null when it holds none by that id. */
export function storedComment(repo: string, id: number): (StoredComment & { number: number }) | null {
  const [row] = rows<CommentRow & { number: number }>('SELECT * FROM comments WHERE project = ? AND id = ?', repo, id)
  return row ? { ...commentOf(row), number: row.number } : null
}

/**
 * A review's comment on a line of its diff: its file, its line in the new file, the head it was made on, and the
 * comment sent to the agent that quoted it, if one did.
 */
export type StoredLineComment = StoredComment & { path: string; line: number; head: string | null
  sentIn: number | null }

/** A review's line comments, oldest first. */
export function storedLineComments(repo: string, number: number): StoredLineComment[] {
  // Every column, so a store that lib/store.py hasn't yet given sent_in still reads, with each comment unsent.
  return rows<CommentRow & { path: string; line: number }>('SELECT * FROM comments WHERE project = ? AND number = ?'
    + ' AND path IS NOT NULL AND line IS NOT NULL ORDER BY id', repo, number)
    .map(one => ({ ...commentOf(one), path: one.path, line: one.line, head: one.head_sha,
      sentIn: one.sent_in ?? null }))
}

/**
 * How the dispatcher answered an item's comments sent to the agent: the runs it asked for, by the comment that asked,
 * and whether a comment still waits for its answer. A comment waits while it has no answer and its `comment-added`
 * event comes after the newest one answered, as lib/store.py's asked_reviews reads it, since the dispatcher answers
 * them oldest first; before the dispatcher's first answer none waits.
 */
export function storedAnswers(repo: string, number: number): { runs: Map<number, number>; waiting: boolean } {
  const asked = rows<{ comment: number; added: number; answer: string | null; run: number | null }>(
    "SELECT json_extract(added.payload, '$.comment') AS comment, added.id AS added,"
    + " json_extract(answered.payload, '$.answer') AS answer, json_extract(answered.payload, '$.run') AS run"
    + ' FROM events AS added LEFT JOIN events AS answered ON answered.project = added.project'
    + " AND answered.number = added.number AND answered.type = 'comment-answered'"
    + " AND json_extract(answered.payload, '$.added') = added.id"
    + " WHERE added.project = ? AND added.number = ? AND added.type = 'comment-added'"
    + " AND json_extract(added.payload, '$.to_agent') ORDER BY added.id", repo, number)
  const newest = asked.some(one => one.answer === null) ? rows<{ added: number | null }>(
    "SELECT MAX(json_extract(payload, '$.added')) AS added FROM events WHERE type = 'comment-answered'")[0]?.added
    : null
  return {
    runs: new Map(asked.flatMap(one => one.answer === 'run' && one.run !== null ? [[one.comment, one.run]] : [])),
    waiting: newest !== null && newest !== undefined && asked.some(one => one.answer === null && one.added > newest),
  }
}

/**
 * An item a link joins to another, with how many sub-issues it has and how many of those are closed, and the numbers
 * of the open issues blocking it.
 */
export type StoredLinked = { number: number; title: string; state: string; stateReason: string | null; subs: number
  subsDone: number; blockers: number[] }

/**
 * The items a link of `kind` (parent, blocks, or fixes) joins to `number`: those it points at when `outward`, or those
 * that point at it. A sub-issue's `parent` link points at its parent, and a blocker's `blocks` link at what it blocks.
 */
export function storedLinked(repo: string, number: number, kind: string, outward: boolean): StoredLinked[] {
  const [here, there] = outward ? ['from_number', 'to_number'] : ['to_number', 'from_number']
  return rows<Omit<StoredLinked, 'blockers'> & { blockers: string | null }>('SELECT items.number, items.title,'
    + ' items.state, items.state_reason AS stateReason,'
    + " (SELECT COUNT(*) FROM links AS sub WHERE sub.project = items.project AND sub.to_number = items.number"
    + " AND sub.kind = 'parent') AS subs,"
    + ' (SELECT COUNT(*) FROM links AS sub JOIN items AS one ON one.project = sub.project'
    + " AND one.number = sub.from_number WHERE sub.project = items.project AND sub.to_number = items.number"
    + " AND sub.kind = 'parent' AND one.state = 'closed') AS subsDone,"
    + ' (SELECT group_concat(block.from_number) FROM links AS block JOIN items AS one ON one.project = block.project'
    + " AND one.number = block.from_number WHERE block.project = items.project AND block.to_number = items.number"
    + " AND block.kind = 'blocks' AND one.state != 'closed') AS blockers"
    + ` FROM links JOIN items ON items.project = links.project AND items.number = links.${there}`
    + ` WHERE links.project = ? AND links.${here} = ? AND links.kind = ? ORDER BY items.number`, repo, number, kind)
    .map(one => ({ ...one, blockers: (one.blockers ?? '').split(',').filter(Boolean).map(Number)
      .sort((a, b) => a - b) }))
}

/** A branch check as the store holds it: its status is queued, running, passed, failed, timed-out, or lost. */
export type StoredCheck = { id: number; name: string; status: string; attempt: number }

/**
 * The checks of `head` on `branch` that the review shows, as lib/store.py's newest_checks picks them: the newest
 * attempt, or the failed first attempt while the second it queued waits or runs. None while no check of the head was
 * queued, or each one was superseded or cancelled.
 */
export function storedChecks(repo: string, branch: string, head: string | null): StoredCheck[] {
  if (!head) {
    return []
  }
  const [newest, before] = rows<StoredCheck>('SELECT id, name, status, attempt FROM checks WHERE project = ?'
    + " AND branch = ? AND head_sha = ? AND status NOT IN ('superseded', 'cancelled') ORDER BY id DESC LIMIT 2",
    repo, branch, head)
  if (newest === undefined) {
    return []
  }
  const retrying = ['queued', 'running'].includes(newest.status) && before !== undefined
    && ['failed', 'timed-out', 'lost'].includes(before.status)
  return [retrying && before ? before : newest]
}

/** Whether check `id` is one of the project's. */
export function storedCheck(repo: string, id: number): boolean {
  return rows<{ id: number }>('SELECT id FROM checks WHERE project = ? AND id = ?', repo, id).length > 0
}

/** Where the store keeps the picture `agent-desk shots` saved with SHA-256 `hash`, or null when it keeps none. */
export function storedAsset(hash: string): string | null {
  return rows<{ path: string }>('SELECT path FROM assets WHERE hash = ?', hash)[0]?.path ?? null
}

/**
 * A run the dispatcher holds, in any project, as the Resources view reads it: its unit while it works, and while it
 * waits, the gate that holds it and why. A branch check names its review's number as its target, or none.
 */
export type HeldRun = { id: number; project: string; kind: string; target: string | null; status: string
  unit: string | null; gate: string | null; reason: string | null }

/** Every run and branch check that waits or works, in every project, oldest first. */
export function heldRuns(): HeldRun[] {
  return [
    ...rows<HeldRun>('SELECT id, project, kind, target, status, unit, wait_gate AS gate, wait_reason AS reason'
      + " FROM runs WHERE status IN ('requested', 'running') ORDER BY id"),
    ...rows<HeldRun>("SELECT id, project, 'check' AS kind, CAST(number AS TEXT) AS target, status, NULL AS unit,"
      + " wait_gate AS gate, wait_reason AS reason FROM checks WHERE status IN ('queued', 'running') ORDER BY id"),
  ]
}
