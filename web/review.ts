// What the page shows of an issue or a review in its detail view: the previews, hovercards, relations, and pictures,
// read from agent-desk's store and the project's main checkout, and the comments it posts through the store. A
// review's files come from git, and every body renders on this machine, so nothing here asks GitHub.
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { realpathSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'

import { answerOf, bodyParts } from './decisions.ts'
import type { Answer, BodyPart, DecisionSection } from './decisions.ts'
import { markdownText, renderMarkdown } from './markdown.ts'
import { childEnv, HERE, host } from './state.ts'
import type { Desk } from './state.ts'
import { storedAnswers, storedAsset, storedChecks, storedComments, storedItem, storedLabels, storedLineComments,
  storedLinked, storedPosting, storedReview, storePath } from './store.ts'
import type { PostingIdentity, StoredCheck, StoredComment, StoredItem, StoredLineComment, StoredLinked, StoredReview }
  from './store.ts'

const STORE_CLI = join(HERE, '..', 'lib', 'store_cli.py')

/** A check on a review: its name, how it stands, such as success, failure, or in_progress, and its log on the page. */
type Check = { name: string; state: string; url?: string }

/**
 * A changed file, with its old path when it moved, and its diff in rows; no rows for a binary file, or for one whose
 * rows a large diff left out, which `omitted` marks.
 */
/** How a file changed, which the Files Changed tab marks it with. */
type FileStatus = 'added' | 'deleted' | 'renamed' | 'modified'

type ChangedFile = { path: string; from?: string; status: FileStatus; additions: number; deletions: number
  lines?: DiffLine[]; omitted?: true }

/** What the page shows when a review opens: its description and comments as HTML, its files, its checks, and more. */
export type Preview = {
  number: number
  title: string
  /** The address that opens it on the page. */
  url: string
  /** open, draft, merged, or closed. */
  state: string
  author: Person
  /** When it opened, in ISO 8601. */
  at: string
  head: string
  base: string
  commits: number
  labels: Label[]
  additions: number
  deletions: number
  files: ChangedFile[]
  /** Why the review shows no files, such as a branch that is gone; absent when git read its diff. */
  noDiff?: string
  /** Why some files show without their rows, such as a diff too large to send whole; absent when every file has them. */
  diffNote?: string
  checks: Check[]
  issues: { number: number; title: string; url: string }[]
  body: string
  /** The description cut at its Decisions Needed sections, as bodyParts cuts it; absent when it has none. */
  bodyParts?: BodyPart[]
  comments: Comment[]
  /** Whether a comment sent to the agent waits for the dispatcher's answer, so the page reads it again soon. */
  waiting: boolean
  /** The commit whose files the diff shows, which a new line comment is made on; null without a diff. */
  tip: string | null
  lineComments: LineComment[]
}

/**
 * A comment on a line of a review's diff. `line` is its line in the new file at the head it was made on, and `shown`
 * the same line in the diff the page shows, which a commit since may have moved; null when a commit since changed
 * the line, which makes it outdated, or when the page shows no diff. An outdated comment carries the lines of the file
 * it was made on, ending at its own. `sent` says whether a Send to agent carried it, and `run` names the agent run the
 * comment that carried it asked for.
 */
export type LineComment = Comment & { id: number; path: string; line: number; shown: number | null
  outdated: boolean; code?: { line: number; text: string }[]; sent: boolean }

/**
 * The store's author and the recorded model of the run that posted as that author.
 */
export type Person = { login: string; posting?: PostingIdentity }

/** A label, with its color as six hex digits. */
type Label = { name: string; color: string }

/**
 * A comment as HTML, with its id, who wrote it and when, and the agent run the dispatcher asked for when it sent work
 * to the agent, which the page shows as a 👀 reaction. A comment with a Decisions Needed section carries `parts`, as
 * bodyParts cuts it, and an answer to one carries `answers`, as answerOf reads it. One sent to the agent carries
 * `toAgent`, and one its writer edited carries `edited`, the edit's time.
 */
type Comment = { id: number; author: Person; at: string; html: string; run?: number; parts?: BodyPart[]
  answers?: Answer; toAgent?: true; edited?: string }

/** What the page shows for an issue: its labels, and its description and comments as HTML. */
type IssuePreview = {
  number: number
  title: string
  url: string
  /** open, completed, not_planned, or duplicate. */
  state: string
  /** The issue it duplicates when it closed as a duplicate, from its `duplicate` link; absent otherwise. */
  duplicateOf?: { number: number; title: string; url: string }
  author: Person
  at: string
  labels: Label[]
  /** The description, with its task boxes numbered for the page to tick. */
  body: string
  /** The description cut at its Decisions Needed sections, as bodyParts cuts it; absent when it has none. */
  bodyParts?: BodyPart[]
  /** The stored description's version, which a tick sends back so it never lands on a description changed since. */
  version: string
  /** How many comments it has; `comments` holds every one. */
  commentCount: number
  comments: Comment[]
  /** Whether a comment sent to the agent waits for the dispatcher's answer, so the page reads it again soon. */
  waiting: boolean
  /** Whether it is an idea, whose comment box sends its text to the agent for a discussion. */
  idea: boolean
  /** The reviews that fix it, oldest first, which its page shows with it. */
  reviews: IssueReview[]
  /** Whether the store would delete it, which shows Delete Issue; the server adds it, and absent reads as no. */
  deletable?: boolean
}

/** A review that fixes an issue: its number, title, state as a review's preview names it, and when it opened. */
type IssueReview = { number: number; title: string; state: string; at: string }

/** What a link's hovercard shows for an issue or a review: its state, title, text, and labels. */
export type Hovercard = {
  kind: 'issue' | 'pr'
  number: number
  title: string
  /** An issue's open, completed, not_planned, or duplicate; a review's open, draft, merged, or closed. */
  state: string
  at: string
  labels: Label[]
  /** The start of its description as plain text, at most HOVERCARD_CHARS long. */
  excerpt: string
}

/** An issue related to another, as the Dependencies tab lists it. */
type Related = {
  number: number
  /** `#12`. */
  ref: string
  title: string
  url: string
  /** Whether it is in the project's own repository, so the page can show its card and expand it; always, here. */
  local: boolean
  /** open, completed, not_planned, or duplicate. */
  state: string
  subs: { total: number; done: number }
  /** The refs of the open issues blocking it. */
  blockers: string[]
}

/** An issue's parent, sub-issues, the issues blocking it, and the issues it blocks. */
type Relations = { parent: Related | null; subs: Related[]; blockedBy: Related[]; blocking: Related[] }

/**
 * One row of a file's diff: a hunk's header, or a line added, removed, or left as it was, with its numbers in the old
 * and new file; or git's note that a file has no newline at its end.
 */
type DiffLine = { kind: 'hunk' | 'add' | 'del' | 'same' | 'note'; old?: number; new?: number; text: string }

// The most characters of a description a hovercard shows.
const HOVERCARD_CHARS = 240
// The longest comment or description the page may write, and the longest title, as GitHub caps one.
export const MAX_COMMENT = 65_536
export const MAX_TITLE = 256
// How many pictures /api/asset keeps in memory.
const MAX_PICTURES = 64
// The most rows of diff a preview carries, and the most files it carries rows for; past either, the largest files go
// without their rows, so the page stays quick on a very large review.
const MAX_DIFF_ROWS = 20_000
const MAX_DIFF_FILES = 300
// The most changed lines, as git counts them, of a diff the server reads at all; a larger one lists its files alone.
const MAX_READ_LINES = 200_000
// A commit's name as the store keeps it, which git can't read as an option.
const SHA = /^[\da-f]{4,64}$/i
// A mention that sends an agent to work, as lib/store.py's MENTION reads one.
const MENTION = /(?<![\w/])@agent\b/
// How many lines of the file an outdated comment shows, ending at its own; and how many diffs between two heads of a
// file the server keeps, which never change.
const OUTDATED_LINES = 4
const MAX_MOVES = 256
// The page's word for each status of a branch check, in the words the Checks tab tones.
const CHECK_STATES: Record<string, string> = { passed: 'success', failed: 'failure', 'timed-out': 'timed_out',
  lost: 'error', running: 'in_progress', queued: 'queued' }
const pictures = new Map<string, Buffer>()
const moves = new Map<string, string | null>()

/** The address that opens item `number` in the page's detail view, which `#123` in a body links to. */
export function itemLink(desk: Pick<Desk, 'id'>, number: number): string {
  return `#${desk.id}:${number}`
}

export function personOf(author: string | null): Person {
  return { login: author ?? 'ghost' }
}

const LEGACY_SIGNATURE = /(?:^|\n)<sub>Posted by agent-desk\b[^\n]*<\/sub>\s*(?:<!-- agent-desk -->\s*)?$/

function postOf(desk: Desk, post: StoredItem | StoredComment): { author: Person; body: string } {
  const author = personOf(post.author)
  const posting = author.login === 'agent' && post.author_run ? storedPosting(desk.repo, post.author_run) : undefined
  return posting ? { author: { ...author, posting }, body: post.body.replace(LEGACY_SIGNATURE, '') }
    : { author, body: post.body }
}

function labelsOf(labels: Label[]): Label[] {
  return labels.map(({ name, color }) => ({ name, color: /^[0-9a-f]{6}$/i.test(color) ? color : 'ededed' }))
}

function coloredLabels(desk: Desk, number: number): Label[] {
  return labelsOf(storedLabels(desk.repo, number).map(name => ({ name, color: desk.labels[name] ?? '' })))
}

/**
 * Markdown as the page shows it, with `#123` opening item 123 on the page, and the repository's own pictures served
 * through /api/asset; a picture from another host shows as a link. With `tasks`, as in an issue's description, its
 * task boxes take clicks.
 */
export function markdownOf(desk: Desk, text: string, tasks = false): string {
  return withAssets(renderMarkdown(text, number => itemLink(desk, number),
    src => assetUrl(src, desk.id, desk.repo), { tasks }), desk.id, desk.repo)
}

/**
 * A body cut at its Decisions Needed sections, as bodyParts cuts it under `anchor`, with markdownOf's links and
 * pictures; undefined when it has none, which leaves the field out of the page's JSON.
 */
function partsOf(desk: Desk, text: string, anchor: string, place: string, tasks = false): BodyPart[] | undefined {
  return bodyParts(text, anchor, place, number => itemLink(desk, number), src => assetUrl(src, desk.id, desk.repo),
    { tasks })?.map(part => 'html' in part ? { html: withAssets(part.html, desk.id, desk.repo) }
    : { section: sectionWithAssets(desk, part.section) })
}

/** A Decisions Needed section with every piece of its HTML through withAssets, as markdownOf passes a whole body. */
function sectionWithAssets(desk: Desk, section: DecisionSection): DecisionSection {
  const own = (text: string) => withAssets(text, desk.id, desk.repo)
  return { ...section, heading: own(section.heading), intro: own(section.intro),
    decisions: section.decisions.map(decision => ({ ...decision, head: own(decision.head), after: own(decision.after),
      options: decision.options.map(option => ({ ...option, html: own(option.html) })) })) }
}

/** A stored comment as the page shows it, with its Decisions Needed sections and the section it answers. */
function commentOf(desk: Desk, one: StoredComment): Comment {
  const { author, body } = postOf(desk, one)
  return { id: one.id, author, at: one.createdAt, html: markdownOf(desk, body),
    parts: partsOf(desk, body, `comment-${one.id}`, `the comment of ${one.createdAt}`),
    answers: answerOf(one.body) ?? undefined, ...marksOf(one) }
}

/** Whether a stored comment went to the agent and when its writer edited it, as a comment's `toAgent` and `edited`. */
function marksOf(one: StoredComment): Pick<Comment, 'toAgent' | 'edited'> {
  return { ...one.toAgent ? { toAgent: true } : {}, ...one.editedAt ? { edited: one.editedAt } : {} }
}

/** A body's version: a hash of its text, which changes whenever the text does. */
export function bodyVersion(text: string): string {
  return createHash('sha256').update(text).digest('hex').slice(0, 16)
}

/** An issue's state as the page names it: open, completed, not_planned, or duplicate. */
function issueState(item: Pick<StoredItem, 'state' | 'stateReason'>): string {
  const reason = (item.stateReason ?? '').toLowerCase().replace(/[-\s]/g, '_')
  return item.state !== 'closed' ? 'open' : reason === 'not_planned' || reason === 'duplicate' ? reason : 'completed'
}

/** The issue a closed duplicate names as its original, as an issue's preview shows it, or nothing for any other. */
function duplicateOf(desk: Desk, number: number, state: string): Pick<IssuePreview, 'duplicateOf'> {
  const original = state === 'duplicate' ? storedLinked(desk.repo, number, 'duplicate', true)[0] : undefined
  return original ? { duplicateOf: { number: original.number, title: original.title,
    url: itemLink(desk, original.number) } } : {}
}

/** A review's state as the page names it: merged, closed, draft, or open. */
function reviewState(item: StoredItem, review: StoredReview | null): string {
  return review?.mergedSha ? 'merged' : item.state === 'closed' ? 'closed' : review?.draft ? 'draft' : 'open'
}

/** An item's comments, oldest first, each with the run it asked for. */
function commentsOf(desk: Desk, number: number, runs: Map<number, number>): Comment[] {
  return storedComments(desk.repo, number).map(one => ({ ...commentOf(desk, one), ...ranOf(runs, one.id) }))
}

/** The run comment `id` asked for, as a comment's `run`, or nothing when it asked for none. */
function ranOf(runs: Map<number, number>, id: number): { run?: number } {
  const run = runs.get(id)
  return run === undefined ? {} : { run }
}

// The workflow states an issue labelled idea leaves Ideas for, as AGENT_STATES in inbox/hooks/board.ts names them.
const AGENT_STATES = new Set(['queued', 'in-progress', 'waiting', 'closed'])

/**
 * Whether a stored item is an idea, as the board's Ideas column shows one and lib/dispatch_comments.py's is_idea reads
 * it: an open issue labelled idea in no agent state. A comment sent to the agent on one asks for a discussion of it.
 */
function isIdeaItem(desk: Desk, item: StoredItem): boolean {
  return item.kind === 'issue' && !AGENT_STATES.has(item.state) && storedLabels(desk.repo, item.number).includes('idea')
}

/** An issue's preview, or null when the project has no issue by that number. */
export function issuePreviewOf(desk: Desk, number: number): IssuePreview | null {
  const issue = storedItem(desk.repo, number)
  if (issue?.kind !== 'issue') {
    return null
  }
  const answers = storedAnswers(desk.repo, number)
  const comments = commentsOf(desk, number, answers.runs)
  const state = issueState(issue)
  const { author, body } = postOf(desk, issue)
  return { number, title: issue.title, url: itemLink(desk, number), state, ...duplicateOf(desk, number, state),
    author, at: issue.createdAt, labels: coloredLabels(desk, number),
    body: markdownOf(desk, body, true),
    bodyParts: partsOf(desk, body, 'issue-body', 'the description', true), version: bodyVersion(issue.body),
    commentCount: comments.length,
    comments, waiting: answers.waiting, idea: isIdeaItem(desk, issue), reviews: reviewsFixing(desk, number) }
}

/** The reviews whose merge closes issue `number`, oldest first. */
function reviewsFixing(desk: Desk, number: number): IssueReview[] {
  return storedLinked(desk.repo, number, 'fixes', false).flatMap(one => {
    const item = storedItem(desk.repo, one.number)
    return item?.kind === 'review' ? [{ number: one.number, title: item.title,
      state: reviewState(item, storedReview(desk.repo, one.number)), at: item.createdAt }] : []
  })
}

/** An issue's or a review's hovercard, or null when the project has no item by that number. */
export function hovercardOf(desk: Desk, number: number): Hovercard | null {
  const item = storedItem(desk.repo, number)
  if (item === null) {
    return null
  }
  const text = markdownText(item.body).replace(/\s+/g, ' ').trim()
  const kind = item.kind === 'review' ? 'pr' : 'issue'
  return { kind, number, title: item.title,
    state: kind === 'pr' ? reviewState(item, storedReview(desk.repo, number)) : issueState(item), at: item.createdAt,
    labels: coloredLabels(desk, number),
    excerpt: text.length > HOVERCARD_CHARS ? `${text.slice(0, HOVERCARD_CHARS - 1).trimEnd()}…` : text }
}

function relatedOf(desk: Desk, one: StoredLinked): Related {
  return { number: one.number, ref: `#${one.number}`, title: one.title, url: itemLink(desk, one.number), local: true,
    state: issueState(one), subs: { total: one.subs, done: one.subsDone },
    blockers: one.blockers.map(number => `#${number}`) }
}

/** An issue's relations, or null when the project has no issue by that number. */
export function relationsOf(desk: Desk, number: number): Relations | null {
  if (storedItem(desk.repo, number)?.kind !== 'issue') {
    return null
  }
  const linked = (kind: string, outward: boolean) => storedLinked(desk.repo, number, kind, outward)
    .map(one => relatedOf(desk, one))
  return { parent: linked('parent', true)[0] ?? null, subs: linked('parent', false),
    blockedBy: linked('blocks', false), blocking: linked('blocks', true) }
}

/** The log of check `id` of `repo`, where lib/checks.py writes it. */
export function checkLogPath(repo: string, id: number): string {
  return join(dirname(storePath()), 'checks', repo.replace('/', '__'), `${id}.log`)
}

/** A branch check as the Checks tab shows it, linked to its log once it has started. */
function checkOf(desk: Desk, check: StoredCheck): Check {
  const url = `/api/check-log?desk=${encodeURIComponent(desk.id)}&check=${check.id}`
  return { name: check.name, state: CHECK_STATES[check.status] ?? check.status,
    ...(check.status === 'queued' ? {} : { url }) }
}

/** A file's unified diff, from its first hunk, in rows. */
export function diffLines(patch: string): DiffLine[] {
  const lines: DiffLine[] = []
  let old = 0
  let now = 0
  for (const line of patch.split('\n')) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if (hunk) {
      old = Number(hunk[1])
      now = Number(hunk[2])
      lines.push({ kind: 'hunk', text: line })
    } else if (line.startsWith('+')) {
      lines.push({ kind: 'add', new: now++, text: line.slice(1) })
    } else if (line.startsWith('-')) {
      lines.push({ kind: 'del', old: old++, text: line.slice(1) })
    } else if (line.startsWith('\\')) {
      lines.push({ kind: 'note', text: line.slice(2) })
    } else {
      lines.push({ kind: 'same', old: old++, new: now++, text: line.slice(1) })
    }
  }
  return lines
}

/**
 * Each file's status in one diff, by its new path, from git's `--name-status -z`: a letter, with a score after a move's
 * or copy's, then the path, or a move's old and new paths. A copy counts as added, and a change of type as modified.
 */
export function statusesOf(nameStatus: string): Map<string, FileStatus> {
  const fields = nameStatus.split('\0')
  const statuses = new Map<string, FileStatus>()
  for (let at = 0; at < fields.length - 1;) {
    const letter = (fields[at++] ?? '').charAt(0)
    const paired = letter === 'R' || letter === 'C'
    if (paired) {
      at++
    }
    statuses.set(fields[at++] ?? '', letter === 'R' ? 'renamed' : letter === 'A' || letter === 'C' ? 'added'
      : letter === 'D' ? 'deleted' : 'modified')
  }
  return statuses
}

/**
 * The files of one diff, from git's `--numstat -z` and its patch, which list the files in the same order, with each
 * one's status from `statusesOf`. A binary file, which numstat counts as `-`, gets no rows, and a file that only moved
 * or changed mode gets none to show.
 */
function filesOf(numstat: string, patch: string, statuses: Map<string, FileStatus>): ChangedFile[] {
  const sections = patch.split(/^(?=diff --git )/m).filter(one => one.startsWith('diff --git '))
  const fields = numstat.split('\0')
  const files: ChangedFile[] = []
  for (let at = 0; at < fields.length - 1;) {
    const [added = '', deleted = '', path = ''] = (fields[at++] ?? '').split('\t')
    // A move names no path on its counts' line, and its old and new paths in the next two fields.
    const from = path === '' ? fields[at++] : undefined
    const to = path === '' ? fields[at++] ?? '' : path
    const section = sections[files.length] ?? ''
    const hunks = section.indexOf('\n@@')
    const binary = added === '-'
    const status = from === undefined ? statuses.get(to) ?? 'modified' : 'renamed'
    files.push({ path: to, ...(from === undefined ? {} : { from }), status, additions: binary ? 0 : Number(added),
      deletions: binary ? 0 : Number(deleted),
      ...(binary ? {} : { lines: hunks < 0 ? [] : diffLines(section.slice(hunks + 1).replace(/\n$/, '')) }) })
  }
  return files
}

/** Git's output in the project's main checkout, or null when git fails. */
function git(desk: Desk, args: string[]): Promise<string | null> {
  return host.run(['git', '-c', 'core.quotePath=false', ...args], desk.main)
}

/** The commit a branch names in the main checkout, as a local branch or origin's, or null when neither has it. */
async function commitOf(desk: Desk, branch: string): Promise<string | null> {
  for (const ref of [`refs/heads/${branch}`, `refs/remotes/origin/${branch}`]) {
    const sha = (await git(desk, ['rev-parse', '--verify', '--quiet', `${ref}^{commit}`]))?.trim()
    if (sha) {
      return sha
    }
  }
  return null
}

type Diff = { files: ChangedFile[]; commits: number; tip: string; note?: string } | { noDiff: string }

/** A file without its rows, as a large diff sends it; a binary file, or one that only moved, has none to leave out. */
function withoutLines(file: ChangedFile): ChangedFile {
  const { lines, ...rest } = file
  return lines === undefined || file.additions + file.deletions === 0 ? file : { ...rest, omitted: true }
}

/**
 * `files` cut to MAX_DIFF_ROWS rows over at most MAX_DIFF_FILES files, keeping the rows of the smallest files and
 * leaving out those of the largest, with how many files lost their rows.
 */
export function capDiff(files: ChangedFile[]): { files: ChangedFile[]; omitted: number } {
  const bySize = files.flatMap((file, at) => file.lines?.length ? [at] : [])
    .sort((one, other) => (files[one]?.lines?.length ?? 0) - (files[other]?.lines?.length ?? 0))
  const left = new Set<number>()
  let rows = 0
  let kept = 0
  for (const at of bySize) {
    const size = files[at]?.lines?.length ?? 0
    if (kept < MAX_DIFF_FILES && rows + size <= MAX_DIFF_ROWS) {
      rows += size
      kept++
    } else {
      left.add(at)
    }
  }
  return { files: files.map((file, at) => left.has(at) ? withoutLines(file) : file), omitted: left.size }
}

/**
 * A review's changes: a merged review's from where its merge left main, which is its merge commit's first parent, or
 * the commit before a rebase-merge's first, and an open or closed one's from where its branch left its base, as
 * `git diff base...branch` shows them. A diff past the caps lists every file with its counts, and leaves out the rows
 * of the largest files.
 */
async function diffOf(desk: Desk, review: StoredReview): Promise<Diff> {
  let spec: string[]
  let commits: string | null
  let tip: string
  const merged = review.mergedSha && SHA.test(review.mergedSha)
    && (await git(desk, ['rev-parse', '--verify', '--quiet', `${review.mergedSha}^{commit}`]))?.trim()
  if (merged) {
    // A merge commit's second parent is the branch, whose commits since main the review held; a squash is one.
    const branch = (await git(desk, ['rev-parse', '--verify', '--quiet', `${merged}^2`]))?.trim()
    const rebased = review.mergedCommits ?? 1
    const start = !branch && rebased > 1
      ? (await git(desk, ['rev-parse', '--verify', '--quiet', `${merged}~${rebased}^{commit}`]))?.trim() : undefined
    spec = [start || `${merged}^1`, merged]
    commits = branch ? await git(desk, ['rev-list', '--count', `${merged}^1..${branch}`]) : start ? `${rebased}` : '1'
    // A merge commit's files hold main's changes too, so line comments read against the branch it merged.
    tip = branch || merged
  } else {
    const [head, base] = await Promise.all([commitOf(desk, review.branch), commitOf(desk, review.base)])
    if (head === null) {
      return { noDiff: `The branch ${review.branch} is gone, so its diff is unavailable.` }
    }
    if (base === null) {
      return { noDiff: `The base ${review.base} is gone, so the diff is unavailable.` }
    }
    spec = [`${base}...${head}`]
    commits = await git(desk, ['rev-list', '--count', `${base}..${head}`])
    tip = head
  }
  const flags = ['diff', '--no-color', '--no-ext-diff', '--no-textconv', '-M']
  const [numstat, nameStatus] = await Promise.all([git(desk, [...flags, '--numstat', '-z', ...spec, '--']),
    git(desk, [...flags, '--name-status', '-z', ...spec, '--'])])
  if (numstat === null || nameStatus === null) {
    return { noDiff: `git couldn't read the diff of ${review.branch} in ${desk.main}.` }
  }
  const count = Number(commits ?? 0) || 0
  const statuses = statusesOf(nameStatus)
  const listed = filesOf(numstat, '', statuses)
  const changed = listed.reduce((sum, file) => sum + file.additions + file.deletions, 0)
  // A patch that git can't hand over within the server's limits, when its counts read, is too large.
  const patch = changed > MAX_READ_LINES ? null : await git(desk, [...flags, ...spec, '--'])
  if (patch === null) {
    return { files: listed.map(withoutLines), commits: count, tip,
      note: `This diff is too large to read, so its ${plural(listed.length, 'file')} show without their lines.` }
  }
  const capped = capDiff(filesOf(numstat, patch, statuses))
  return { files: capped.files, commits: count, tip, ...(capped.omitted === 0 ? {} : {
    note: `This diff is large, so the ${plural(capped.omitted, 'largest file')} show without their lines.` }) }
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString('en-US')} ${noun}${count === 1 ? '' : 's'}`
}

/** A review's preview, or null when the project has no review by that number. */
export async function previewOf(desk: Desk, number: number): Promise<Preview | null> {
  const item = storedItem(desk.repo, number)
  const review = storedReview(desk.repo, number)
  if (item?.kind !== 'review' || review === null) {
    return null
  }
  const diff = await diffOf(desk, review)
  const files = 'files' in diff ? diff.files : []
  const tip = 'tip' in diff ? diff.tip : null
  const answers = storedAnswers(desk.repo, number)
  const { author, body } = postOf(desk, item)
  return {
    number, title: item.title, url: itemLink(desk, number), state: reviewState(item, review),
    author, at: item.createdAt, head: review.branch, base: review.base,
    commits: 'commits' in diff ? diff.commits : 0, labels: coloredLabels(desk, number),
    additions: files.reduce((sum, file) => sum + file.additions, 0),
    deletions: files.reduce((sum, file) => sum + file.deletions, 0), files,
    ...('noDiff' in diff ? { noDiff: diff.noDiff } : {}), ...('note' in diff && diff.note ? { diffNote: diff.note } : {}),
    checks: storedChecks(desk.repo, review.branch, review.head).map(check => checkOf(desk, check)),
    issues: storedLinked(desk.repo, number, 'fixes', true).map(one => ({ number: one.number, title: one.title,
      url: itemLink(desk, one.number) })),
    body: markdownOf(desk, body), bodyParts: partsOf(desk, body, 'review-body', 'the description'),
    comments: commentsOf(desk, number, answers.runs), waiting: answers.waiting,
    tip, lineComments: await Promise.all(storedLineComments(desk.repo, number)
      .map(one => lineCommentOf(desk, one, tip, answers.runs))),
  }
}

/**
 * Where line `line` of a file sits after the changes in `patch`, a `git diff -U0` of that file from the commit the
 * line was read at: its new number, or null when a hunk changed it.
 */
export function movedLine(patch: string, line: number): number | null {
  let shift = 0
  for (const hunk of patch.matchAll(/^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@/gm)) {
    const start = Number(hunk[1])
    const removed = hunk[2] === undefined ? 1 : Number(hunk[2])
    const added = hunk[3] === undefined ? 1 : Number(hunk[3])
    // A hunk that removes nothing adds its lines after line `start`.
    if (removed === 0 ? start >= line : start > line) {
      break
    }
    if (removed > 0 && line < start + removed) {
      return null
    }
    shift += added - removed
  }
  return line + shift
}

/** `git diff -U0` of one file between two commits, kept, or null when git can't read either commit. */
async function fileMoves(desk: Desk, from: string, to: string, path: string): Promise<string | null> {
  const key = `${desk.main}:${from}:${to}:${path}`
  if (!moves.has(key)) {
    moves.set(key, await git(desk, ['diff', '--no-color', '--no-ext-diff', '--no-textconv', '-U0', from, to, '--',
      path]))
    if (moves.size > MAX_MOVES) {
      moves.delete(moves.keys().next().value ?? '')
    }
  }
  return moves.get(key) ?? null
}

/** The lines of a file at a commit that end at line `line`, OUTDATED_LINES at most; none when git has no such file. */
async function codeAt(desk: Desk, sha: string, path: string, line: number): Promise<{ line: number; text: string }[]> {
  const text = await git(desk, ['cat-file', 'blob', `${sha}:${path}`])
  const first = Math.max(1, line - OUTDATED_LINES + 1)
  return text === null ? []
    : text.split('\n').slice(first - 1, line).map((one, at) => ({ line: first + at, text: one }))
}

/**
 * Where a line comment sits in the diff of `tip`: its line there, moved by the commits since its head, or null when
 * one of them changed the line or git no longer has its head, which `lost` says; the head it was made on when that is
 * a commit.
 */
async function placeOf(desk: Desk, one: StoredLineComment, tip: string):
  Promise<{ shown: number | null; head: string | null; lost: boolean }> {
  const head = one.head !== null && SHA.test(one.head) ? one.head : null
  if (one.head === tip) {
    return { shown: one.line, head, lost: false }
  }
  const patch = head === null ? null : await fileMoves(desk, head, tip, one.path)
  return { shown: patch === null ? null : movedLine(patch, one.line), head, lost: patch === null }
}

/**
 * A line comment as the page shows it against the diff of `tip`: on its line, moved by the commits since its head, or
 * outdated, with the code it was made on, when one of them changed its line or git no longer has its head.
 */
async function lineCommentOf(desk: Desk, one: StoredLineComment, tip: string | null, runs: Map<number, number>):
  Promise<LineComment> {
  // A line comment reaches the agent in the comment that quoted it, or on its own when it was sent to the agent.
  const { author, body } = postOf(desk, one)
  const comment = { id: one.id, author, at: one.createdAt, html: markdownOf(desk, body),
    path: one.path, line: one.line, sent: one.sentIn !== null, ...ranOf(runs, one.sentIn ?? one.id), ...marksOf(one) }
  if (tip === null) {
    return { ...comment, shown: null, outdated: false }
  }
  const { shown, head } = await placeOf(desk, one, tip)
  return shown === null
    ? { ...comment, shown: null, outdated: true,
      code: head === null ? [] : await codeAt(desk, head, one.path, one.line) }
    : { ...comment, shown, outdated: false }
}

/**
 * Runs the store's command line as `you` with `args`, writing `text` to its input: what it printed, or why it refused,
 * from the last line it said on its standard error, without its `agent-desk:` and its closing period.
 */
export function runStore(desk: Desk, args: string[], text: string): Promise<{ out: string } | { error: string }> {
  return new Promise(resolve => {
    const child = execFile('python3', ['-B', STORE_CLI, ...args], { cwd: desk.main, env: childEnv(), timeout: 30_000 },
      (error, stdout, stderr) => resolve(error === null ? { out: stdout } : { error: stderr.trim().split('\n').at(-1)
        ?.replace(/^agent-desk: /, '').replace(/\.$/, '') || 'the store refused' }))
    child.stdin?.on('error', () => {})
    child.stdin?.end(text)
  })
}

/** Runs the store's command line as runStore does; what it printed, or null when it refused. */
export async function writeStore(desk: Desk, args: string[], text: string): Promise<string | null> {
  const done = await runStore(desk, args, text)
  return 'out' in done ? done.out : null
}

/** Writes a comment on an item through the store as `you`, with `flags` after its number; false when it refused. */
async function writeComment(desk: Desk, item: StoredItem, flags: string[], text: string): Promise<boolean> {
  return await writeStore(desk, [item.kind, 'comment', String(item.number), '--repo', desk.repo, '--body-file', '-',
    ...flags], text) !== null
}

/** A comment `you` just wrote, as the page shows it. */
function yours(desk: Desk, text: string): Omit<Comment, 'id'> {
  return { author: personOf('you'), at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'), html: markdownOf(desk, text) }
}

/**
 * Posts a comment on an issue or a review through the store as `you`, and returns the comment the store kept as the
 * page shows it; null when the store refuses it. A comment that mentions @agent goes to the agent, as one on GitHub
 * did, and so does one with `toAgent`, as an issue's Send to Agent posts it, which the store gives the @agent mention
 * it lacks. Only an open issue takes `toAgent`: a review's Send to Agent goes through reviewAction with its line
 * comments.
 */
export async function postComment(desk: Desk, number: number, text: string, toAgent = false):
  Promise<Comment | Refusal | null> {
  const item = storedItem(desk.repo, number)
  if (toAgent && item?.kind !== 'issue') {
    return { status: 409, error: `#${number} isn't an issue, so Send to Agent from its comment box starts nothing` }
  }
  if (toAgent && item?.state === 'closed') {
    return { status: 409, error: `#${number} is closed, so Send to Agent from its comment box starts nothing` }
  }
  if (item === null || !await writeComment(desk, item, toAgent || MENTION.test(text) ? ['--to-agent'] : [], text)) {
    return null
  }
  const kept = storedComments(desk.repo, number).findLast(one => one.author === 'you')
  return kept ? commentOf(desk, kept) : { ...yours(desk, text), id: 0 }
}

async function within(desk: Desk, ancestor: string, commit: string): Promise<boolean> {
  return await git(desk, ['merge-base', '--is-ancestor', ancestor, commit]) !== null
}

/** Why the server refused a request, with the HTTP status it answers. */
export type Refusal = { status: number; error: string }

/**
 * Why a review takes no line comment on file `path` at commit `head`, or null when it takes one: it must be open, its
 * diff must change the file, and the head must be one of its commits, which its branch holds and its base doesn't.
 */
async function lineCommentProblem(desk: Desk, number: number, path: string, head: string | null):
  Promise<Refusal | null> {
  const item = storedItem(desk.repo, number)
  const review = storedReview(desk.repo, number)
  if (item?.kind !== 'review' || review === null) {
    return { status: 404, error: 'no such review' }
  }
  const refused = (error: string) => ({ status: 409, error })
  if (item.state === 'closed') {
    return refused(`#${number} is ${review.mergedSha ? 'merged' : 'closed'}`)
  }
  if (head !== null && !SHA.test(head)) {
    return { status: 400, error: 'a head names a commit' }
  }
  const diff = await diffOf(desk, review)
  if ('noDiff' in diff) {
    return refused(diff.noDiff.replace(/\.$/, ''))
  }
  if (!diff.files.some(file => file.path === path)) {
    return refused(`#${number} doesn't change ${path}`)
  }
  const base = await commitOf(desk, review.base)
  if (head !== null && (!await within(desk, head, diff.tip) || (base !== null && await within(desk, head, base)))) {
    return refused(`${head.slice(0, 7)} isn't a commit of #${number}; reload it`)
  }
  return null
}

/**
 * Posts a comment on line `line` of file `path` in a review's diff of commit `head`, through the store as `you`, and
 * returns it; why not when the review takes no such comment, as lineCommentProblem says, or the store refuses it. It
 * waits for Send to agent.
 */
export async function postLineComment(desk: Desk, number: number, path: string, line: number, head: string | null,
  text: string): Promise<LineComment | Refusal> {
  const problem = await lineCommentProblem(desk, number, path, head)
  const item = storedItem(desk.repo, number)
  if (problem !== null || item === null) {
    return problem ?? { status: 404, error: 'no such review' }
  }
  const flags = [`--path=${path}`, `--line=${line}`, ...(head === null ? [] : [`--head-sha=${head}`])]
  return await writeComment(desk, item, flags, text)
    ? { ...yours(desk, text), id: 0, path, line, shown: line, outdated: false, sent: false }
    : { status: 502, error: 'the store refused the comment' }
}

/** Why Send to agent sends nothing: the box is empty and every line comment went already. */
export const NOTHING_TO_SEND = 'write a comment or a line comment to send'

export function unsentOf(desk: Desk, number: number): StoredLineComment[] {
  return storedLineComments(desk.repo, number).filter(one => one.sentIn === null)
}

/**
 * A line comment as Send to agent quotes it: its file, its line, and its text, and when it was made on a head before
 * the one the review shows, that head and its line there now: null when a commit since changed the line, and absent
 * when git no longer has the head.
 */
type Quoted = Pick<StoredLineComment, 'path' | 'line' | 'body'> & { madeOn?: string; now?: number | null }

/** Where a quoted line comment sits: its line, and the head it was made on and its line now when that head is older. */
function placeText(one: Quoted): string {
  const at = `\`${one.path}\` line ${one.line}`
  if (one.madeOn === undefined) {
    return at
  }
  const now = one.now === undefined ? 'a commit git here no longer has'
    : one.now === null ? 'a line a commit since changed' : `now line ${one.now}`
  return `${at} at ${one.madeOn.slice(0, 7)}, ${now}`
}

/**
 * The comment Send to agent posts: the person's text, or a line asking for the line comments, then each line comment
 * not yet sent, by its file and line, quoted.
 */
export function agentRequest(text: string, unsent: Quoted[]): string {
  const lead = text.trim() || 'Please address my line comments on the diff.'
  const quoted = unsent.map(one => `- ${placeText(one)}:\n\n${one.body.trim().split('\n')
    .map(line => `  > ${line}`.trimEnd()).join('\n')}`)
  return quoted.length === 0 ? lead : `${lead}\n\n**Line comments**\n\n${quoted.join('\n\n')}`
}

// The branches an agent works, as lib/dispatch_comments.py's AGENT_BRANCH names them.
const AGENT_BRANCH = /^agent\/[a-z0-9][a-z0-9-]*$/

async function quotedOf(desk: Desk, unsent: StoredLineComment[], tip: string | null): Promise<Quoted[]> {
  return await Promise.all(unsent.map(async one => {
    const quoted = { path: one.path, line: one.line, body: one.body }
    if (tip === null || one.head === null || one.head === tip) {
      return quoted
    }
    const { shown, lost } = await placeOf(desk, one, tip)
    return { ...quoted, madeOn: one.head, ...(lost ? {} : { now: shown }) }
  }))
}

/**
 * Sends a review's unsent line comments and `text` to the agent in one comment, asks the dispatcher for the agent's
 * run, and marks those line comments sent. Returns how many it sent, or a string saying why it sent nothing.
 */
export async function sendToAgent(desk: Desk, number: number, text: string): Promise<{ sent: number } | string> {
  const item = storedItem(desk.repo, number)
  const review = storedReview(desk.repo, number)
  if (item?.kind !== 'review' || review === null) {
    return 'no such review'
  }
  if (!AGENT_BRANCH.test(review.branch)) {
    return `no agent works ${review.branch}, so nothing was sent; only agent/<slug> branches have one`
  }
  const unsent = unsentOf(desk, number)
  if (unsent.length === 0 && text.trim() === '') {
    return NOTHING_TO_SEND
  }
  const request = agentRequest(text, await quotedOf(desk, unsent, await commitOf(desk, review.branch)))
  const quotes = unsent.map(one => `--quotes=${one.id}`)
  return await writeComment(desk, item, ['--to-agent', ...quotes], request) ? { sent: unsent.length }
    : 'the store refused the comment'
}

/**
 * A pattern for a link to a file of `repo` on GitHub, such as `agent-desk shots` wrote for each picture before it kept
 * pictures in the state folder, that captures the file's ref and path.
 */
function assetPattern(repo: string): string {
  return `https://github\\.com/${repo.replace(/[.]/g, '\\.')}/(?:blob|raw)/([\\w.-]+)/([\\w./-]+?)(?:\\?raw=true)?`
}

function assetAddress(desk: string, ref: string, path: string): string {
  const query = (name: string, value: string) => `${name}=${encodeURIComponent(value)}`
  return `/api/asset?${[query('desk', desk), query('ref', ref), query('path', path)].join('&')}`
}

/** The page's address for `url` when it links to a file of `repo` on GitHub, or null. */
function assetUrl(url: string, desk: string, repo: string): string | null {
  const found = new RegExp(`^${assetPattern(repo)}$`).exec(url)
  return found ? assetAddress(desk, found[1] ?? '', found[2] ?? '') : null
}

/**
 * HTML with each link to the repository's own files, such as the screenshots `agent-desk shots` once kept on its
 * pr-assets branch, served through /api/asset from the main checkout's git.
 */
export function withAssets(html: string, desk: string, repo: string): string {
  const own = new RegExp(`"${assetPattern(repo)}"`, 'g')
  return html.replace(own, (_, ref: string, path: string) =>
    `"${assetAddress(desk, ref, path).replace(/&/g, '&amp;')}"`)
}

// A picture's media type, by its extension; /api/asset serves no other file.
const PICTURES: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  webp: 'image/webp' }

/** A picture's media type when `ref` and `path` name a picture in a repository plainly, or null. */
export function pictureType(ref: string, path: string): string | null {
  const plain = /^[\w.-]+(\/[\w.-]+)*$/
  if (!plain.test(ref) || !plain.test(path) || path.split('/').some(part => part === '..' || part === '.')) {
    return null
  }
  return PICTURES[path.split('.').at(-1)?.toLowerCase() ?? ''] ?? null
}

// A picture's name at /api/asset/<hash>: the SHA-256 of its content, as lib/assets.py names it.
const ASSET_HASH = /^[\da-f]{64}$/

/** A path with every symlink in it followed, or null when nothing is there. */
function realPath(path: string): string | null {
  try {
    return realpathSync(path)
  } catch {
    return null
  }
}

/**
 * The file and media type of the picture `agent-desk shots` saved with `hash`, or null when `hash` isn't one, the store
 * keeps no such picture, or its row points outside the state folder's assets folder, through a symlink too, or at a
 * file that isn't a picture.
 */
export function assetFile(hash: string): { path: string; type: string } | null {
  const stored = ASSET_HASH.test(hash) ? storedAsset(hash) : null
  const path = stored === null ? null : realPath(stored)
  const assets = realPath(join(dirname(storePath()), 'assets'))
  if (path === null || assets === null) {
    return null
  }
  const within = relative(assets, path)
  const type = PICTURES[path.split('.').at(-1)?.toLowerCase() ?? '']
  return within !== '' && !within.startsWith('..') && type ? { path, type } : null
}

/** The bytes of a file at a commit in the main checkout, or null when git has no such file there. */
function blobOf(desk: Desk, spec: string): Promise<Buffer | null> {
  return new Promise(resolve => {
    execFile('git', ['cat-file', 'blob', spec], { cwd: desk.main, env: childEnv(), timeout: 30_000,
      maxBuffer: 64 * 1_048_576, encoding: 'buffer' }, (error, stdout) => resolve(error ? null : stdout))
  })
}

/**
 * A picture from the repository at `ref`, kept in memory: from origin's copy of the branch as the main checkout last
 * fetched it, or a local branch or commit by that name; null when git has neither.
 */
export async function pictureOf(desk: Desk, ref: string, path: string): Promise<Buffer | null> {
  const key = `${desk.repo}:${ref}:${path}`
  const kept = pictures.get(key)
  if (kept) {
    return kept
  }
  const bytes = await blobOf(desk, `refs/remotes/origin/${ref}:${path}`) ?? await blobOf(desk, `${ref}:${path}`)
  if (bytes !== null) {
    pictures.set(key, bytes)
    if (pictures.size > MAX_PICTURES) {
      pictures.delete(pictures.keys().next().value ?? '')
    }
  }
  return bytes
}
