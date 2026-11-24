// The agent-desk web page: the inbox for every project in agent-desk's projects file, served to a browser on this
// machine. `agent-desk web on` runs it as a systemd user service; by hand:
//   node web/server.ts      from the agent-desk checkout, serves http://127.0.0.1:7480, or AGENT_DESK_WEB_PORT
//   node web/server.ts <owner/repo>...   serves only the projects named, such as a demo project for screenshots
// It reads the same board as the Claude Code pane, from ../inbox/hooks/board.ts, and while a page is open it follows the
// store's events: each change reads the boards of the projects it touched again and pushes them to every page.
import { readFile, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

import type { Board, Row, RowAction, SystemLoad } from '../inbox/types'
import { fetchBoard } from '../inbox/hooks/board.ts'
import { gaugesOf, loadOf } from '../inbox/hooks/load.ts'
import type { CpuTimes } from '../inbox/hooks/load.ts'
import { announce, inOrder, listSection, listSortable, sortable } from '../inbox/hooks/moves.ts'
import { budgetAmount, changeBudget, clearKey, MAX_KEY, readAccount, refreshAccount, setKey, switchAccount }
  from './account.ts'
import { agentLogOf } from './agent-log.ts'
import { act, close, closeReasonOf, LIST_GROUPS, listPlace, place, REVIEW_ACTIONS, reviewAction, sortList, sortOrder }
  from './card-actions.ts'
import type { ReviewAction } from './card-actions.ts'
import { cardsOf } from './cards.ts'
import { chatSnapshot, clearChat, contextOf, loadChat, MAX_MESSAGE, onChat, sendChat, stopChat }
  from './chat.ts'
import { choreTextOf } from './chore-text.ts'
import { codeDigest, codeNotice } from './code.ts'
import { readCodexUsage, refreshCodexUsage } from './codex-usage.ts'
import { commentSourceOf, editComment } from './comment-edit.ts'
import { changeSchedule, choreInfoOf, pendingOf, readRepeats, runChore, SCHEDULE_BRANCH, scheduleRequest, stopChore,
  withStarting } from './chores.ts'
import { mergedRows } from './history.ts'
import type { HistoryRow } from './history.ts'
import { agentSlotsOf, changeHold, changeIssueRunner, changeSlots, changeUsageLimit, configAgentSlots, configGpuSlots,
  DEFAULT_AGENT_SLOTS, gpuSlotsOf, holdStamp, isIssueRunner, issueRunnerOf, PROJECT_MODES, readHold, readIssueRunners,
  readUsage, refreshClaudeUsage } from './hold.ts'
import { createIssue, issueTemplate } from './issue-create.ts'
import { deletable, deleteIssue } from './issue-delete.ts'
import { editItem, sourceOf, tickTask } from './item-edit.ts'
import { desks, HERE, host, loadStored } from './state.ts'
import type { Desk, Loop } from './state.ts'
import { loopback, ownPeer, refusal } from './peer.ts'
import { readResources } from './resources.ts'
import { assetFile, checkLogPath, hovercardOf, issuePreviewOf, markdownOf, MAX_COMMENT, personOf, pictureOf,
  pictureType, postComment, postLineComment, previewOf, relationsOf } from './review.ts'
import type { Person } from './review.ts'
import { choreReviews, heldRuns, labelColors, lastEvent, mergedReviews, movedSince, reviewCosts, storedCheck,
  storeOpens, storeProblem } from './store.ts'

const PORT = Number(process.env.AGENT_DESK_WEB_PORT ?? 7480)
// How often the server reads the store's newest event id while a page is open, reads every project's board again
// whether or not an event came, so a new project shows and a run's age moves, and samples the machine's load and
// checks whether the checkout's code still matches what the server runs.
const EVENTS_MS = 500
const REREAD_MS = 60_000
const LOAD_MS = 10_000
// The largest request body a POST may send: a few short fields, or a comment of up to MAX_COMMENT characters.
const MAX_BODY = 131_072
// The longest file path a line comment may name.
const MAX_PATH = 4_096
// The most agent slots the Runners sidebar's stepper may set for a project.
const MAX_SLOTS = 20
const CONFIG = process.env.XDG_CONFIG_HOME ?? join(homedir(), '.config')
const PROJECTS = join(CONFIG, 'agent-desk', 'projects')
// The page's own files, the only paths served besides the API: app.js and the modules it imports.
export const STATIC: Record<string, [string, string]> = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/app.js': ['app.js', 'text/javascript; charset=utf-8'],
  '/app-chat.js': ['app-chat.js', 'text/javascript; charset=utf-8'],
  '/app-chore.js': ['app-chore.js', 'text/javascript; charset=utf-8'],
  '/app-decisions.js': ['app-decisions.js', 'text/javascript; charset=utf-8'],
  '/app-delete.js': ['app-delete.js', 'text/javascript; charset=utf-8'],
  '/app-drag.js': ['app-drag.js', 'text/javascript; charset=utf-8'],
  '/app-detail.js': ['app-detail.js', 'text/javascript; charset=utf-8'],
  '/app-edit.js': ['app-edit.js', 'text/javascript; charset=utf-8'],
  '/app-github.js': ['app-github.js', 'text/javascript; charset=utf-8'],
  '/app-header.js': ['app-header.js', 'text/javascript; charset=utf-8'],
  '/app-help.js': ['app-help.js', 'text/javascript; charset=utf-8'],
  '/app-history.js': ['app-history.js', 'text/javascript; charset=utf-8'],
  '/app-hovercard.js': ['app-hovercard.js', 'text/javascript; charset=utf-8'],
  '/app-item.js': ['app-item.js', 'text/javascript; charset=utf-8'],
  '/app-new-issue.js': ['app-new-issue.js', 'text/javascript; charset=utf-8'],
  '/app-order.js': ['app-order.js', 'text/javascript; charset=utf-8'],
  '/app-parts.js': ['app-parts.js', 'text/javascript; charset=utf-8'],
  '/app-resources.js': ['app-resources.js', 'text/javascript; charset=utf-8'],
  '/app-review.js': ['app-review.js', 'text/javascript; charset=utf-8'],
  '/app-runners.js': ['app-runners.js', 'text/javascript; charset=utf-8'],
  '/app-usage.js': ['app-usage.js', 'text/javascript; charset=utf-8'],
  '/app-state.js': ['app-state.js', 'text/javascript; charset=utf-8'],
  '/app.css': ['app.css', 'text/css; charset=utf-8'],
  '/app-chat.css': ['app-chat.css', 'text/css; charset=utf-8'],
  '/app-header.css': ['app-header.css', 'text/css; charset=utf-8'],
  '/app-detail.css': ['app-detail.css', 'text/css; charset=utf-8'],
  '/app-resources.css': ['app-resources.css', 'text/css; charset=utf-8'],
  '/anthropic.svg': ['anthropic.svg', 'image/svg+xml'],
  '/openai.svg': ['openai.svg', 'image/svg+xml'],
  '/vendor/preact-htm.js': ['vendor/preact-htm.js', 'text/javascript; charset=utf-8'],
}

/** The projects agent-desk's projects file lists, one per line as `<owner>/<repo> <main checkout>`. */
export function parseProjects(text: string): { repo: string; path: string }[] {
  return text.split('\n').flatMap(line => {
    const found = line.trim().match(/^(\S+\/\S+)\s+(.+)$/)
    return found ? [{ repo: found[1] ?? '', path: found[2] ?? '' }] : []
  })
}

/** The projects listed that the server serves: the ones named, matched without regard to case, or all when none are. */
export function servedProjects<T extends { repo: string }>(listed: T[], named: string[]): T[] {
  const wanted = new Set(named.map(repo => repo.toLowerCase()))
  return wanted.size ? listed.filter(({ repo }) => wanted.has(repo.toLowerCase())) : listed
}

/**
 * The header's GPU gauges from nvidia-smi's utilization.gpu, memory.used, and memory.total, one CSV line per card in
 * MiB: its use and its free memory, colored at the CPU's and RAM's lines. A machine with several cards numbers them.
 */
export function gpuGauges(csv: string): { text: string; color: string }[] {
  const red = '#ff3b30'
  const healthy = '#5f875f'
  const cards = csv.trim().split('\n').map(line => line.split(',').map(Number)).filter(fields => fields.length === 3
    && fields.every(Number.isFinite))
  return cards.flatMap(([use = 0, used = 0, total = 0], index) => {
    const name = cards.length > 1 ? `GPU${index}` : 'GPU'
    const free = (total - used) / 1024
    return [
      { text: `${name} ${Math.round(use)}%`, color: use >= 90 ? red : use >= 70 ? 'warning' : healthy },
      { text: `${cards.length > 1 ? `${name} ` : ''}VRAM ${free < 10 ? free.toFixed(1) : Math.round(free)}G free`,
        color: free < total / 1024 * 0.1 ? red : free < total / 1024 * 0.15 ? 'warning' : healthy },
    ]
  })
}

/** The rows of a board, across its sections. */
function rowsOf(board: Board): Row[] {
  return [board.runners, board.needs, board.queued, board.next, board.blocked, board.design, board.backlog,
    board.ideas, board.merged].flat()
}

const clients = new Set<ServerResponse>()
// The person the server acts as, as the store names them, beside the page's comment box.
const me: Person = personOf('you')
let load: SystemLoad | null = null
// nvidia-smi's last reading, empty on a machine without it.
let gpu = ''
let lastCpu: CpuTimes | null = null
// The checkout the server runs from, and digests of its modules as the server started with them and as they last stood.
const CHECKOUT = dirname(HERE)
let codeStarted: string | null = null
let codeNow: string | null = null
let sampling: NodeJS.Timeout | null = null
let following: NodeJS.Timeout | null = null
let rereading: NodeJS.Timeout | null = null
// The newest store event the server has read, and the newest every board it publishes was read after, which each
// state carries as its event id.
let readTo = 0
let builtTo = 0
// The catch-up with the store under way, which a tick that comes meanwhile joins; whether the next one reads every
// board again; the store's open count the server last read events under; and the store problem the page last got.
let catching: Promise<void> | null = null
let rereadDue = false
// When lib/hold.py had last saved its state as the boards were read, since a board's Start buttons and notes follow
// the mode.
let heldAt = holdStamp()
let readOpens = 0
let toldProblem: string | null = null
// The projects the server was started for, by owner/repo, or none to serve every project in the projects file.
let only: string[] = []

/** Reads the projects file again, keeping the desks it still lists and adding new ones. */
async function readDesks(): Promise<void> {
  const listed = servedProjects(parseProjects(await readFile(PROJECTS, 'utf8').catch(() => '')), only)
  const seen = new Set<string>()
  for (const { repo, path } of listed) {
    let config: { title?: string; chores?: { every?: Record<string, number> }; slots?: unknown; issueRunner?: unknown }
    try {
      config = JSON.parse(await readFile(join(path, '.agent-desk.json'), 'utf8'))
    } catch {
      continue
    }
    const [owner = '', id = repo] = repo.split('/')
    seen.add(id)
    const desk = desks.get(id)
    const issueRunner = config.issueRunner === undefined ? 'claude' : isIssueRunner(config.issueRunner)
      ? config.issueRunner : null
    const fields = { repo, owner, title: config.title ?? id, main: path, issueRunner,
      chores: Object.keys(config.chores?.every ?? {}), agentSlots: configAgentSlots(config),
      gpuSlots: configGpuSlots(config) }
    if (desk) {
      Object.assign(desk, fields)
    } else {
      desks.set(id, { id, ...fields, board: null, at: 0, refreshing: false, starting: new Map(),
        labels: {}, models: {}, chorePrs: {}, schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 })
    }
  }
  for (const id of desks.keys()) {
    if (!seen.has(id)) {
      desks.delete(id)
    }
  }
}

function effectiveIssueRunner(desk: Desk, saved: Record<string, unknown>) {
  return issueRunnerOf(desk.repo, desk.issueRunner === undefined ? 'claude' : desk.issueRunner, saved)
}

/**
 * What the page draws: each project's board, by title, the machine's load, and a notice when the checkout holds newer
 * code than the server runs.
 */
function snapshot(): unknown {
  const all = [...desks.values()].sort((a, b) => a.title.localeCompare(b.title))
  const savedRunners = readIssueRunners()
  return {
    desks: all.map(desk => ({ id: desk.id, repo: desk.repo, title: desk.title, owner: desk.owner, board: desk.board,
      cards: desk.board && desk.board.error === null ? cardsOf(desk.id, desk.board) : [], labels: desk.labels,
      repeats: readRepeats(desk.repo), choreInfo: choreInfoOf(desk), schedule: pendingOf(desk),
      agentSlots: agentSlotsOf(desk.repo, desk.agentSlots ?? DEFAULT_AGENT_SLOTS),
      gpuSlots: gpuSlotsOf(desk.repo, desk.gpuSlots ?? null),
      issueRunner: effectiveIssueRunner(desk, savedRunners),
      refreshing: desk.refreshing })),
    gauges: [...load === null ? [] : gaugesOf(load), ...gpuGauges(gpu)],
    hold: readHold(),
    usage: readUsage(),
    codexUsage: readCodexUsage(),
    account: readAccount(),
    me,
    store: storeProblem(),
    code: codeNotice(codeStarted, codeNow),
  }
}

/** Writes one server-sent event; a state carries the store event id its boards were read after, as its `id`. */
function send(client: ServerResponse, event: string, data: unknown, id?: number): void {
  client.write(`${id === undefined ? '' : `id: ${id}\n`}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
}

function publish(): void {
  toldProblem = storeProblem()
  const state = snapshot()
  for (const client of clients) {
    send(client, 'state', state, builtTo)
  }
}

// Each change to the chat goes to every open page.
onChat(patch => {
  for (const client of clients) {
    send(client, 'chat', patch)
  }
})

function toast(text: string): void {
  for (const client of clients) {
    send(client, 'toast', { text })
  }
}

/**
 * Toasts an action that failed, after its project's name. One that worked sends nothing, since its card's move or the
 * board read after it shows it.
 */
function fail(desk: Desk, text: string): void {
  toast(`${desk.title}: ${text}`)
}

/** Reads a project's board again and publishes it, showing the project as refreshing meanwhile. */
async function refresh(desk: Desk): Promise<void> {
  desk.refreshing = true
  publish()
  await rebuild(desk)
  publish()
}

/**
 * Reads a project's board, label colors, and chore PRs from the store, and its model table when it's due, without
 * publishing it. A read that a later one or a button's command overtook leaves the board as that one made it, and no
 * read starts while a button's command runs; the read after the command shows what it wrote.
 */
export async function rebuild(desk: Desk): Promise<void> {
  if (desk.commands > 0) {
    return
  }
  const mine = ++desk.refreshes
  let fetched: Board
  try {
    // The model table changes only with the project's config or models.py, so it's read when those files change.
    const config = join(desk.main, '.agent-desk.json')
    const modelsFor = (await Promise.all([config, MODELS].map(path => stat(path).then(info => info.mtimeMs, () => 0))))
      .join(':')
    const [board, models] = await Promise.all([
      fetchBoard(host, { root: desk.main, main: desk.main }),
      modelsFor === desk.modelsFor ? Promise.resolve(null)
        : host.run(['python3', '-B', MODELS, config, 'table', ...desk.chores], desk.main)])
    fetched = board
    desk.labels = labelColors(desk.repo)
    const prs = choreReviews(desk.repo)
    desk.chorePrs = Object.fromEntries(prs.map(pr => [pr.number, pr.state]))
    desk.schedulePr = prs.find(pr => pr.branch === SCHEDULE_BRANCH && pr.state === 'OPEN')?.number ?? null
    try {
      if (models) {
        desk.models = JSON.parse(models)
        desk.modelsFor = modelsFor
      }
    } catch {
      // A table Python couldn't print leaves the last one, and the next read tries it again.
    }
  } catch (error) {
    fetched = { at: '', runners: [], needs: [], design: [], designTotal: 0, queued: [], next: [], blocked: [],
      backlog: [], ideas: [], merged: [], chores: [], error: `The board couldn't be read: ${String(error)}` }
  } finally {
    if (mine === desk.refreshes) {
      desk.refreshing = false
    }
  }
  if (mine !== desk.refreshes) {
    return
  }
  desk.board = fetched.error === null
    ? withStarting(fetched, desk.starting, Date.now()) : fetched
  desk.at = Date.now()
  if (fetched.error === null) {
    const watched = [...desk.board.needs, ...desk.board.design]
    const before = desk.known
    for (const row of before === null ? [] : watched.filter(one => !before.has(one.key))) {
      toast(`${desk.title}: ${announce(row)}`)
    }
    desk.known = new Set(watched.map(row => row.key))
  }
}

/**
 * Reads the store's newest event id, and when it moved, reads the projects file again and the boards of the projects
 * whose events came since the last read, along with any project not read yet, and publishes them under that id. A
 * catch-up due to read everything again, or one that finds the store's file replaced or the mode changed, reads every
 * board. A catch-up asked for while one runs joins it, and the next tick reads whatever came meanwhile.
 */
export function catchUp(): Promise<void> {
  // The promise clears itself only once it settles, after it is assigned, even when the read had nothing to wait for.
  catching ??= follow().finally(() => {
    catching = null
  })
  return catching
}

async function follow(): Promise<void> {
  const reread = rereadDue
  rereadDue = false
  let moved = movedSince(readTo)
  // Another file's event ids say nothing about this one's, so the server reads it from its first event.
  const replaced = storeOpens() !== readOpens
  if (replaced) {
    readOpens = storeOpens()
    moved = movedSince(0)
  }
  // A mode set outside the page, by agent-desk hold or resume or lib/hold.py, writes no store event.
  const held = holdStamp()
  const remoded = held !== heldAt
  heldAt = held
  // hold.json keeps the account and the budget, which a command or a run may have changed.
  if (remoded) {
    await refreshAccount()
  }
  const all = reread || replaced || remoded || moved.all
  const fresh = all || moved.last > readTo
  readTo = moved.last
  if (fresh) {
    await readDesks()
  }
  const stale = [...desks.values()].filter(desk => desk.board === null || all || moved.projects.has(desk.repo))
  if (fresh || stale.length > 0) {
    await Promise.all(stale.map(rebuild))
    builtTo = replaced ? moved.last : Math.max(builtTo, moved.last)
    publish()
  } else if (storeProblem() !== toldProblem) {
    publish()
  }
}

/** Whether every project's board is built and as new as the store, so a page that connects can have it at once. */
function current(): boolean {
  return builtTo >= lastEvent() && storeOpens() === readOpens && holdStamp() === heldAt
    && [...desks.values()].every(desk => desk.board !== null)
}

/** Reads the projects file and every project's board again, and publishes them under the store's newest event id. */
async function refreshAll(): Promise<void> {
  const last = lastEvent()
  heldAt = holdStamp()
  readTo = Math.max(readTo, last)
  await readDesks()
  await Promise.all([...desks.values()].map(refresh))
  builtTo = Math.max(builtTo, last)
  publish()
}

function refreshQuotas(): void {
  const saved = readIssueRunners()
  const runners = new Set([...desks.values()].map(desk => effectiveIssueRunner(desk, saved).effective))
  if (runners.has('codex')) {
    void refreshCodexUsage()?.then(publish)
  }
  if (runners.has('claude') && readAccount()?.account === 'subscription') {
    void refreshClaudeUsage()?.then(publish)
  }
}

async function sampleLoad(): Promise<void> {
  refreshQuotas()
  const [proc, card] = await Promise.all([
    Promise.all(['/proc/meminfo', '/proc/stat'].map(path => readFile(path, 'utf8').catch(() => ''))),
    host.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total', '--format=csv,noheader,nounits']),
  ])
  gpu = card ?? ''
  codeNow = await codeDigest(CHECKOUT)
  // The month's spend grows as runs end and the Chat answers.
  await refreshAccount()
  const sample = loadOf(proc.join('\n'), lastCpu)
  lastCpu = sample.cpu
  load = sample.load
  publish()
}

/**
 * Follows the store, reads every board again each REREAD_MS, and samples the machine's load while a page is open, and
 * stops when the last one closes.
 */
function watch(): void {
  if (clients.size > 0 && sampling === null) {
    sampling = setInterval(() => void sampleLoad(), LOAD_MS)
    following = setInterval(() => void catchUp().catch(error => console.error(error)), EVENTS_MS)
    rereading = setInterval(() => {
      rereadDue = true
    }, REREAD_MS)
    void sampleLoad()
  } else if (clients.size === 0 && sampling !== null) {
    clearInterval(sampling)
    clearInterval(following ?? undefined)
    clearInterval(rereading ?? undefined)
    sampling = null
    following = null
    rereading = null
  }
}

/** Whether a line comment's path names a file plainly: not empty, not too long, and on one line. */
function plainPath(path: unknown): path is string {
  return typeof path === 'string' && path !== '' && path.length <= MAX_PATH && !/[\0\n\r]/.test(path)
}

// lib/models.py, which picks each run's model and effort.
const MODELS = join(HERE, '..', 'lib', 'models.py')
// The longest span the History column asks for.
const HISTORY_DAYS = 90
// The most merged reviews one span reads.
const HISTORY_LIMIT = 300

/** The reviews a project merged in [since, until), in milliseconds, each with what the runs behind it cost. */
function historyOf(desk: Desk, since: number, until: number): HistoryRow[] {
  return mergedRows(mergedReviews(desk.repo, since, until, HISTORY_LIMIT), desk.id, since, until,
    reviewCosts(desk.repo))
}

export const loop: Loop = { publish, toast, fail, refresh, refreshAll }

async function body(req: IncomingMessage): Promise<Record<string, unknown> | null> {
  let text = ''
  for await (const chunk of req) {
    text += String(chunk)
    if (text.length > MAX_BODY) {
      return null
    }
  }
  try {
    const parsed: unknown = JSON.parse(text || '{}')
    return typeof parsed === 'object' && parsed !== null ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

function reply(res: ServerResponse, status: number, data?: unknown): void {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(data === undefined ? '' : JSON.stringify(data))
}

export async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  // The page loads nothing from elsewhere, pictures included, and no other page may frame it to steer a click onto
  // Merge.
  res.setHeader('Content-Security-Policy', "default-src 'self'; img-src 'self'; frame-ancestors 'none'")
  res.setHeader('X-Content-Type-Options', 'nosniff')
  if (!await ownPeer(req.socket)) {
    reply(res, 403, { error: 'another user' })
    return
  }
  const refused = refusal(req.method, req.headers, PORT)
  if (refused !== null) {
    reply(res, 403, { error: refused })
    return
  }
  const url = new URL(req.url ?? '/', 'http://localhost')
  const path = url.pathname
  const asked = (name: string) => url.searchParams.get(name) ?? ''
  const page = STATIC[path]
  if (req.method === 'GET' && page) {
    res.writeHead(200, { 'Content-Type': page[1], 'Cache-Control': 'no-cache' })
    res.end(await readFile(join(HERE, page[0])))
  } else if (req.method === 'GET' && path === '/api/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })
    // A page that reconnects names the last event id it saw, as EventSource's Last-Event-ID or the page's `after`, and
    // no id it saw is newer than the store. Unless the server's own boards are as new as the store, as they are not in
    // a server that just started, the first state waits for them to catch up, so the page never draws a board older
    // than the events it saw.
    if (!current()) {
      await catchUp()
    }
    if (req.socket.destroyed) {
      return
    }
    clients.add(res)
    send(res, 'state', snapshot(), builtTo)
    send(res, 'chat', chatSnapshot())
    req.on('close', () => {
      clients.delete(res)
      watch()
    })
    watch()
  } else if (req.method === 'GET' && path === '/api/pr') {
    const desk = desks.get(asked('desk'))
    const preview = desk && /^\d+$/.test(asked('number')) ? await previewOf(desk, Number(asked('number'))) : null
    reply(res, preview ? 200 : 404, preview ?? { error: 'no such PR' })
  } else if (req.method === 'GET' && path === '/api/issue') {
    const desk = desks.get(asked('desk'))
    const preview = desk && /^\d+$/.test(asked('number')) ? issuePreviewOf(desk, Number(asked('number'))) : null
    // Delete Issue shows only on an issue the store would delete.
    if (desk && preview) {
      preview.deletable = await deletable(desk, preview.number)
    }
    reply(res, preview ? 200 : 404, preview ?? { error: 'no such issue' })
  } else if (req.method === 'GET' && path === '/api/source') {
    // An item's stored title and Markdown description, or with `comment` that comment's Markdown, which Edit puts in
    // its box.
    const desk = desks.get(asked('desk'))
    const comment = asked('comment')
    const source = !desk ? null : comment ? /^\d+$/.test(comment) ? commentSourceOf(desk, Number(comment)) : null
      : /^\d+$/.test(asked('number')) ? sourceOf(desk, Number(asked('number'))) : null
    reply(res, source ? 200 : 404, source ?? { error: comment ? 'no such comment' : 'no such issue or review' })
  } else if (req.method === 'GET' && path === '/api/chore-text') {
    // A chore's skill and the project's profile for it, for the panel a Chores row opens: only a chore the project's
    // chores.every lists.
    const desk = desks.get(asked('desk'))
    const text = desk ? await choreTextOf(desk, asked('name')) : null
    reply(res, text ? 200 : 404, text ?? { error: 'no such chore' })
  } else if (req.method === 'GET' && path === '/api/history') {
    const since = Number(asked('since'))
    const until = Number(asked('until'))
    const shown = asked('desks').split(',').flatMap(id => desks.get(id) ?? [])
    if (!Number.isFinite(since) || !Number.isFinite(until) || until <= since
      || until - since > HISTORY_DAYS * 86_400_000) {
      reply(res, 400, { error: `a span of up to ${HISTORY_DAYS} days` })
      return
    }
    reply(res, 200, { rows: shown.flatMap(desk => historyOf(desk, since, until)) })
  } else if (req.method === 'GET' && path === '/api/resources') {
    // What uses the machine's memory, swap, and CPU, for the Resources view, which reads it again while it shows.
    const deskOf = (repo: string) => [...desks.values()].find(desk => desk.repo === repo)?.id ?? null
    reply(res, 200, await readResources(argv => host.run(argv), heldRuns(), deskOf))
  } else if (req.method === 'GET' && path === '/api/issue-template') {
    // The body the New Issue form starts from in the project.
    const desk = desks.get(asked('desk'))
    reply(res, desk ? 200 : 404, desk ? { template: await issueTemplate(desk) } : { error: 'no such project' })
  } else if (req.method === 'GET' && path === '/api/hovercard') {
    const desk = desks.get(asked('desk'))
    const card = desk && /^\d+$/.test(asked('number')) ? hovercardOf(desk, Number(asked('number'))) : null
    reply(res, card ? 200 : 404, card ?? { error: 'no such issue or PR' })
  } else if (req.method === 'GET' && path === '/api/relations') {
    const desk = desks.get(asked('desk'))
    const relations = desk && /^\d+$/.test(asked('number')) ? relationsOf(desk, Number(asked('number'))) : null
    reply(res, relations ? 200 : 404, relations ?? { error: 'no such issue' })
  } else if (req.method === 'GET' && path === '/api/check-log') {
    // A check's log, as lib/checks.py writes it, for the Checks tab's Details link.
    const desk = desks.get(asked('desk'))
    const check = Number(asked('check'))
    const log = desk && /^\d+$/.test(asked('check')) && storedCheck(desk.repo, check)
      ? await readFile(checkLogPath(desk.repo, check)).catch(() => null) : null
    if (log === null) {
      reply(res, 404, { error: 'no log for that check yet' })
    } else {
      res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-cache' })
      res.end(log)
    }
  } else if (req.method === 'GET' && path === '/api/log') {
    const desk = desks.get(asked('desk'))
    const log = desk && /^\d+$/.test(asked('number')) ? await agentLogOf(desk, Number(asked('number'))) : null
    reply(res, log ? 200 : 404, log ?? { error: 'no agent session ran for this issue here' })
  } else if (req.method === 'GET' && path === '/api/asset') {
    const desk = desks.get(asked('desk'))
    const type = pictureType(asked('ref'), asked('path'))
    const bytes = desk && type ? await pictureOf(desk, asked('ref'), asked('path')) : null
    if (bytes === null || type === null) {
      reply(res, 404, { error: 'no such picture' })
    } else {
      res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'private, max-age=86400' })
      res.end(bytes)
    }
  } else if (req.method === 'GET' && path.startsWith('/api/asset/')) {
    // A picture `agent-desk shots` saved, named by the hash of its content, which never changes.
    const asset = assetFile(path.slice('/api/asset/'.length))
    const bytes = asset ? await readFile(asset.path).catch(() => null) : null
    if (asset === null || bytes === null) {
      reply(res, 404, { error: 'no such picture' })
    } else {
      res.writeHead(200, { 'Content-Type': asset.type, 'Cache-Control': 'private, max-age=31536000, immutable' })
      res.end(bytes)
    }
  } else if (req.method === 'POST' && path === '/api/account-key' && !loopback(req.socket.remoteAddress)) {
    // The key may cross no network, so the server reads none from another address, not even its body.
    reply(res, 403, { error: 'the server takes an API key only from this machine' })
  } else if (req.method === 'POST' && path.startsWith('/api/')) {
    const sent = await body(req)
    const desk = desks.get(String(sent?.desk ?? ''))
    if (sent === null) {
      reply(res, 400, { error: 'bad request' })
    } else if (path === '/api/hold' && ['running', 'manual', 'held', 'resume'].includes(String(sent.state))) {
      // Auto, Manual, and Hold act on the project named, or on every project when none is; Resume acts on every
      // project.
      reply(res, 202)
      void changeHold(loop, String(sent.state), PROJECT_MODES.has(String(sent.state)) ? desk : undefined)
    } else if (path === '/api/slots') {
      // The Runners sidebar's steppers set a project's agent or gpu slots, as `{ agent: n }` or `{ gpu: n }`, and each
      // Reset drops its kind's override, as `{ reset: 'agent' }` or `{ reset: 'gpu' }`, the way `agent-desk slots`
      // does from a shell. A gpu count stays at or under the agent count.
      if (desk === undefined) {
        reply(res, 404, { error: 'no such project' })
        return
      }
      const kind = sent.gpu !== undefined || sent.reset === 'gpu' ? 'gpu' : 'agent'
      const agents = agentSlotsOf(desk.repo, desk.agentSlots ?? DEFAULT_AGENT_SLOTS)
      const most = kind === 'gpu' ? agents.count : MAX_SLOTS
      const reset = sent.reset === true || sent.reset === kind
      if (!reset && !(Number.isInteger(sent[kind]) && Number(sent[kind]) >= 0 && Number(sent[kind]) <= most)) {
        reply(res, 400, { error: `${kind} slots are a whole number from 0 to ${most}` })
        return
      }
      const done = await changeSlots(loop, desk, kind, reset ? null : Number(sent[kind]))
      reply(res, done ? 200 : 502, done ? { agent: agentSlotsOf(desk.repo, desk.agentSlots ?? DEFAULT_AGENT_SLOTS),
        gpu: gpuSlotsOf(desk.repo, desk.gpuSlots ?? null) } : { error: 'the slots command failed' })
    } else if (path === '/api/issue-runner') {
      const targets = sent.all === true && sent.desk === undefined ? [...desks.values()] : desk ? [desk] : []
      if (targets.length === 0) {
        reply(res, 404, { error: 'no such project' })
        return
      }
      const runner = sent.runner
      if (runner !== 'default' && !isIssueRunner(runner)) {
        reply(res, 400, { error: 'choose Claude Code, Codex, Local, or the project default' })
        return
      }
      const done = await changeIssueRunner(loop, targets, runner)
      if (done && clients.size > 0) {
        refreshQuotas()
      }
      reply(res, done ? 200 : 502, done ? undefined : { error: 'could not save the issue runner' })
    } else if (path === '/api/usage-limit') {
      // One weekly usage limit covers every project, so the page names none: on at a whole percentage, or off.
      const percent = sent.percent
      if (typeof sent.on !== 'boolean' || (sent.on && !(Number.isInteger(percent) && Number(percent) >= 0
        && Number(percent) <= 100))) {
        reply(res, 400, { error: 'the weekly usage limit is a whole percentage from 0 to 100' })
        return
      }
      const done = await changeUsageLimit(loop, sent.on, sent.on ? Number(percent) : undefined)
      reply(res, done ? 200 : 502, done ? readUsage() : { error: 'the usage limit command failed' })
    } else if (path === '/api/account') {
      // One account covers every project, so the page names none: `subscription` or `api`.
      if (sent.account !== 'subscription' && sent.account !== 'api') {
        reply(res, 400, { error: 'the account is subscription or api' })
        return
      }
      const refused = await switchAccount(loop, sent.account)
      reply(res, refused === null ? 200 : 409, refused === null ? readAccount() : { error: refused })
    } else if (path === '/api/account-key') {
      // `{ key }` saves a key and `{ clear: true }` removes it. The key goes to lib/account.py's standard input alone,
      // and neither the answer nor the log holds it: a refusal says why in words of the script's or the server's own.
      const clear = sent.clear === true
      if (!clear && (typeof sent.key !== 'string' || sent.key.trim() === '' || sent.key.length > MAX_KEY)) {
        reply(res, 400, { error: `an API key is text of up to ${MAX_KEY} characters` })
        return
      }
      const refused = clear ? await clearKey(loop) : await setKey(loop, String(sent.key))
      reply(res, refused === null ? 200 : 400, refused === null ? readAccount() : { error: refused })
    } else if (path === '/api/api-budget') {
      // The monthly API budget: on at a positive number of dollars, or off, keeping its amount.
      if (typeof sent.on !== 'boolean' || (sent.on && !budgetAmount(sent.dollars))) {
        reply(res, 400, { error: 'the API budget is a positive number of dollars, with at most two decimals' })
        return
      }
      const refused = await changeBudget(loop, sent.on, sent.on ? Number(sent.dollars) : undefined)
      reply(res, refused === null ? 200 : 502, refused === null ? readAccount() : { error: refused })
    } else if (path === '/api/refresh') {
      reply(res, 202)
      void (desk ? refresh(desk) : refreshAll())
    } else if (path === '/api/chat') {
      // A message for the chat, with what the page has selected, which must name a served project's issue or review.
      const context = contextOf(sent.context)
      if (typeof sent.text !== 'string' || sent.text.trim() === '' || sent.text.length > MAX_MESSAGE
        || context === undefined) {
        reply(res, 400, { error: 'a chat message is text, with the selected issue or review or none' })
        return
      }
      const refused = await sendChat(sent.text.trim(), context)
      reply(res, refused === null ? 202 : 409, refused === null ? {} : { error: refused })
    } else if (path === '/api/chat-stop') {
      stopChat()
      reply(res, 200, {})
    } else if (path === '/api/chat-clear') {
      await clearChat()
      reply(res, 200, {})
    } else if (desk === undefined) {
      reply(res, 404, { error: 'no such project' })
    } else if (path === '/api/act') {
      // The page names a row and an action; the row comes from the board here, and the action must be one it offers.
      const action = String(sent.action) as RowAction
      const row = desk.board ? rowsOf(desk.board).find(one => one.key === sent.key) : undefined
      if (row === undefined || !row.actions.includes(action)) {
        reply(res, 409, { error: 'that row has moved; refresh' })
        return
      }
      // A close from the comment box carries a reason and a comment, which the store writes together, and waits for
      // the store, so the box keeps its text when the close fails. A close as a duplicate names an issue the project
      // has, or the server refuses it with the reason.
      if (action === 'close' && (sent.reason !== undefined || sent.comment !== undefined)) {
        const asked = closeReasonOf(desk, row, sent.reason, sent.duplicateOf)
        if ('error' in asked) {
          reply(res, 400, asked)
          return
        }
        const comment = typeof sent.comment === 'string' && sent.comment.length <= MAX_COMMENT ? sent.comment.trim()
          : ''
        const done = await close(loop, desk, row, asked.reason, comment || undefined)
        reply(res, done ? 200 : 502, done ? {} : { error: 'the store refused the close' })
        void refresh(desk)
        return
      }
      reply(res, 202)
      void act(loop, desk, row, action)
    } else if (path === '/api/place') {
      // A drop names a queued or waiting issue's row, which offers Move Up or Move Down, and its new place from 1.
      const row = desk.board ? desk.board.queued.find(one => one.key === sent.key) : undefined
      const to = Number(sent.to)
      if (row === undefined || !inOrder(row) || !row.actions.some(one => one === 'up' || one === 'down')) {
        reply(res, 409, { error: 'that row has moved; refresh' })
        return
      }
      if (!Number.isInteger(to) || to < 1) {
        reply(res, 400, { error: 'a place counts from 1' })
        return
      }
      reply(res, 202)
      void place(loop, desk, row, to)
    } else if (path === '/api/sort-order') {
      // The Queued heading's Sort button, for a project whose board shows two or more queued or waiting issues.
      if (!desk.board || !sortable(desk.board.queued)) {
        reply(res, 409, { error: 'that order has changed; refresh' })
        return
      }
      reply(res, 202)
      void sortOrder(loop, desk)
    } else if (path === '/api/list-place') {
      // A drop names an issue's row in Open, Blocked, Needs Design, Backlog, or Ideas, which offers Move Up or Move
      // Down, and the issue of the same section it lands right above, as `before`, or right below, as `after`.
      const section = listSection({ key: String(sent.key) })
      const rows = desk.board && section ? desk.board[section].filter(one => listSection(one) !== null) : []
      const row = rows.find(one => one.key === sent.key)
      const side = sent.before !== undefined ? 'before' : 'after'
      const beside = Number(sent[side])
      const named = [sent.before, sent.after].filter(one => one !== undefined).length
      if (row === undefined || !row.actions.some(one => one === 'up' || one === 'down')) {
        reply(res, 409, { error: 'that row has moved; refresh' })
        return
      }
      if (named !== 1 || !Number.isInteger(beside) || beside === row.number) {
        reply(res, 400, { error: 'a drop names one other issue to land before or after' })
        return
      }
      if (!rows.some(one => one.number === beside)) {
        reply(res, 409, { error: `#${beside} has moved; refresh` })
        return
      }
      reply(res, 202)
      void listPlace(loop, desk, row, side === 'before' ? { before: beside } : { after: beside })
    } else if (path === '/api/sort-list') {
      // A Sort button on the Open, Blocked, Needs Design, Backlog, or Ideas heading, for a project whose board shows
      // two or more of the section's issues in the list order.
      const group = Object.hasOwn(LIST_GROUPS, String(sent.group)) ? LIST_GROUPS[String(sent.group)] : undefined
      if (group === undefined) {
        reply(res, 400, { error: 'no such group' })
        return
      }
      if (!desk.board || !listSortable(desk.board[group[0]])) {
        reply(res, 409, { error: 'that order has changed; refresh' })
        return
      }
      reply(res, 202)
      void sortList(loop, desk, ...group)
    } else if (path === '/api/chore' && typeof sent.name === 'string') {
      const times = sent.times === 'forever' ? 'forever'
        : Number.isInteger(sent.times) && Number(sent.times) >= 1 && Number(sent.times) <= 99 ? String(sent.times) : '1'
      reply(res, 202)
      void runChore(loop, desk, sent.name, times)
    } else if (path === '/api/chore-stop' && typeof sent.name === 'string') {
      reply(res, 202)
      void stopChore(loop, desk, sent.name).then(publish)
    } else if (path === '/api/chore-schedule' && typeof sent.name === 'string') {
      // A schedule change goes through a PR, which the page waits for.
      const change = scheduleRequest(sent)
      const skill = sent.name
      if (typeof change === 'string') {
        reply(res, 400, { error: change })
      } else if (!desk.chores.includes(skill)) {
        reply(res, 404, { error: `no chore named ${sent.name}` })
      } else {
        const done = await changeSchedule(desk, skill, change).catch((error: unknown) => String(error instanceof Error
          ? error.message : error))
        // The page shows the answer: a failure in the schedule's dialog, and the PR it opened in a toast.
        if (typeof done === 'string') {
          reply(res, 502, { error: done })
        } else {
          desk.schedulePr = done.pr
          reply(res, 200, done)
        }
      }
    } else if (path === '/api/line-comment') {
      const number = Number(sent.number)
      const line = Number(sent.line)
      const head = typeof sent.head === 'string' ? sent.head : null
      if (!Number.isInteger(number) || number < 1 || !Number.isInteger(line) || line < 1 || !plainPath(sent.path)
        || typeof sent.text !== 'string' || sent.text.trim() === '' || sent.text.length > MAX_COMMENT) {
        reply(res, 400, { error: 'a line comment names a file, a line, and its text' })
        return
      }
      // A review that is merged or closed, a file its diff doesn't change, or a head that isn't one of its commits
      // takes no line comment.
      const posted = await postLineComment(desk, number, sent.path, line, head, sent.text)
      reply(res, 'error' in posted ? posted.status : 201, 'error' in posted ? { error: posted.error }
        : { comment: posted })
    } else if (path === '/api/task') {
      if (!Number.isInteger(sent.task) || typeof sent.checked !== 'boolean' || typeof sent.version !== 'string') {
        reply(res, 400, { error: 'a tick names a task, whether it is checked, and the description\'s version' })
        return
      }
      const done = await tickTask(desk, Number(sent.number), Number(sent.task), sent.checked, sent.version)
      if ('error' in done) {
        reply(res, done.status, { error: done.error })
        return
      }
      reply(res, 200, done)
      void refresh(desk)
    } else if (path === '/api/edit') {
      const done = await editItem(desk, Number(sent.number), sent)
      if ('error' in done) {
        reply(res, done.status, { error: done.error })
        return
      }
      reply(res, 200, done)
      void refresh(desk)
    } else if (path === '/api/comment-edit') {
      // A comment's new text from its Edit, which the store takes only on a comment you wrote and didn't send.
      const done = await editComment(desk, sent)
      if ('error' in done) {
        reply(res, done.status, { error: done.error })
        return
      }
      reply(res, 200, done)
      void refresh(desk)
    } else if (path === '/api/issue-create') {
      // The New Issue form's issue, which the page selects once the board it reads after shows it.
      const done = await createIssue(desk, sent)
      if ('error' in done) {
        reply(res, done.status, { error: done.error })
        return
      }
      reply(res, 201, done)
      void refresh(desk)
    } else if (path === '/api/issue-delete') {
      // A delete the page's dialog confirmed, which the store refuses for an issue with work behind it.
      const done = await deleteIssue(desk, sent)
      if ('error' in done) {
        reply(res, done.status, { error: done.error })
        return
      }
      reply(res, 200, done)
      void refresh(desk)
    } else if (path === '/api/review' && REVIEW_ACTIONS.includes(sent.action as ReviewAction)) {
      const text = typeof sent.text === 'string' ? sent.text : ''
      const done = text.length > MAX_COMMENT ? { status: 400, data: { error: 'that comment is too long' } }
        : await reviewAction(loop, desk, Number(sent.number), sent.action as ReviewAction, text)
      reply(res, done.status, done.data)
    } else if ((path === '/api/comment' || path === '/api/markdown') && typeof sent.text === 'string'
      && sent.text.trim() !== '' && sent.text.length <= MAX_COMMENT) {
      if (path === '/api/markdown') {
        reply(res, 200, { html: markdownOf(desk, sent.text) })
        return
      }
      // `toAgent` sends the comment to the agent, as an open issue's box offers, and the store puts @agent in front of
      // a text without the mention.
      const number = Number(sent.number)
      const posted = Number.isInteger(number) && number > 0
        ? await postComment(desk, number, sent.text, sent.toAgent === true) : null
      if (posted !== null && 'error' in posted) {
        reply(res, posted.status, { error: posted.error })
      } else {
        reply(res, posted ? 201 : 502, posted ? { comment: posted } : { error: 'the store refused the comment' })
      }
    } else {
      reply(res, 404, { error: 'not found' })
    }
  } else {
    reply(res, 404, { error: 'not found' })
  }
}

if (import.meta.main) {
  codeStarted = await codeDigest(CHECKOUT)
  only = process.argv.slice(2)
  const listed = parseProjects(await readFile(PROJECTS, 'utf8').catch(() => ''))
  const missing = only.filter(repo => !servedProjects(listed, [repo]).length)
  if (missing.length) {
    console.error(`agent-desk: ${missing.join(', ')} not in ${PROJECTS}; run agent-desk install in its checkout.`)
    process.exit(2)
  }
  await loadStored()
  await readDesks()
  await loadChat()
  await refreshAccount()
  const server = createServer((req, res) => {
    handle(req, res).catch(error => {
      console.error(error)
      if (!res.headersSent) {
        reply(res, 500, { error: 'server error' })
      }
    })
  })
  server.on('connection', socket => void ownPeer(socket))
  // An open page holds its event stream; a comment every half minute keeps idle connections from timing out.
  setInterval(() => {
    for (const client of clients) {
      client.write(': keep-alive\n\n')
    }
  }, 30_000)
  server.listen(PORT, '127.0.0.1', () => {
    console.log(`agent-desk at http://127.0.0.1:${PORT} for ${[...desks.keys()].join(', ') || 'no projects'}`)
  })
}
