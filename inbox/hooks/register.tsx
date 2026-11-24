import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Board, Row, RowAction, SectionView } from '../types'
import { askToFix, cancelRequest, dismissRun, labelBacklog, labelReady, listOrderFailure, listSortFailure, markDesigned,
  markReady, moveBacklogToOpen, moveIdeaToOpen, moveInListOrder, moveInStartOrder, orderFailure, pauseIssue,
  queueMerge, queuedName, queuedNote, restartIssue, retryRun, SORT_FAILURE, sortListOrder, sortStartOrder,
  startChore, startRow, stopCommand, unblockIssue, unpauseIssue, unpickFailure, unpickIssue, unqueueFailure,
  unqueueIssue } from './actions.ts'
import { choreColumns, cite, fetchBoard } from './board.ts'
import type { Host, Project } from './board.ts'
import { gaugesOf, loadOf } from './load.ts'
import { deskFound, deskMissing } from './run.ts'
import type { CpuTimes } from './load.ts'
import { announce, listMoveOf, listNumbers, listSection, listSortable, moveInList, moveInOrder, moveToBacklog,
  moveToFixing, moveToMerging, moveToOpen, moveToQueued, moveToRestarted, moveToRetried, moveToReview, moveToUnpicked,
  prName, sortable } from './moves.ts'

const PANE = 'agent-desk'
// How often the pane reads the store's newest event id, from the file the store saves it in beside its database: one
// small read and no process, so every session's pane can afford it each second.
export const EVENT_MS = 1_000
// How long the pane goes without an event before it reads the board anyway, for what changes outside the store: the
// rows' times, and the merge queue, the fixes' plan, the desk's mode, and the chores table, which commands save in
// files of their own.
export const QUIET_MS = 60_000
// Open, Blocked, Needs Design, Backlog, and Ideas each show this many issues at a time; their arrows page through the
// rest.
const BACKLOG_ROWS = 5
// The width of a pager's count, as wide as "999-999 of 999" and a space.
const PAGER_COUNT_WIDTH = 15
// The sections that page their issues instead of listing them all.
const PAGED = new Set(['Open', 'Blocked', 'Needs Design', 'Backlog', 'Ideas'])
// The sections the person collapsed, kept across sessions in $.store.
const COLLAPSED_KEY = 'collapsed-sections'
const TOAST_MS = 8_000
// The sessions that show the pane, kept across sessions in $.store: each one that ran /agent-desk, until its pane is
// closed by hand. Only they open the pane at start, refresh, and toast, so the person's other sessions stay quiet. The
// newest MAX_SHOWING of them.
const SHOWING_KEY = 'inbox-sessions'
const MAX_SHOWING = 100

// Change the tag when Board's fields change: it declines what an earlier version stored.
const board = atom({ plugin: 'agent-desk', key: 'board' } as const, null, { shape: 'board-12' })
const known = atom({ plugin: 'agent-desk', key: 'known' } as const, null)
// The row key and action of the row's button in CONFIRMS that waits for its confirm, as `<key> <action>`, or a
// section's title and ` sort` while its heading's Sort waits for its confirm.
const confirm = atom({ plugin: 'agent-desk', key: 'confirm' } as const, null)
const sortConfirm = (title: string) => `${title} sort`
const note = atom({ plugin: 'agent-desk', key: 'note' } as const, null)
const view = atom({ plugin: 'agent-desk', key: 'view' } as const, null)
// Whether a read of the board is under way; the Refresh button reads Refreshing… until it lands.
const refreshing = atom({ plugin: 'agent-desk', key: 'refreshing' } as const, null)
// The machine's memory and CPU use, sampled every LOAD_MS while the pane shows, so the person sees agents
// exhausting it.
const load = atom({ plugin: 'agent-desk', key: 'load' } as const, null)
const LOAD_MS = 10_000
const OPEN_VIEW: SectionView = { collapsed: [], offsets: {} }

type Engine = EngineInterface

// The repository's checkout and its main checkout, set at session start; null outside a project with a
// .agent-desk.json.
let root: string | null = null
let main: string | null = null
// The file the store saves its newest event id in, as lib/store.py names it under the state folder; null when the
// session has no HOME to find it by.
let eventFile: string | null = null

async function run($: Engine, argv: string[], cwd = root ?? undefined, stdin?: string): Promise<string | null> {
  try {
    const result = await $.process.run(argv, { cwd, timeoutMs: 30_000, ...(stdin === undefined ? {} : { stdin }) })
    return result.exitCode === 0 ? result.stdout : null
  } catch {
    return null
  }
}

/** The board's host in this session: its processes, its clock, and the mod's store. */
function hostOf($: Engine): Host {
  return { run: (argv, cwd, stdin) => run($, argv, cwd, stdin), now: () => $.clock.now(),
    get: key => $.store.get(key), set: (key, value) => $.store.set(key, value) }
}

/** This session's project: its checkout and main checkout, once session start found them. */
function project(): Project {
  return { root: root ?? undefined, main: main ?? undefined }
}

/**
 * A row's description cut to `room` columns, ending in an ellipsis where it's cut. A note in parentheses at its end,
 * such as why an issue waits or which PR its agent went back to, stays whole while the text before it gives way.
 */
export function fitRow(text: string, room: number): string {
  const cut = (part: string, width: number) => (part.length <= width ? part
    : `${part.slice(0, Math.max(width - 1, 0)).trimEnd()}…`)
  const note = text.match(/ {2}\([^()]*\)$/)?.[0] ?? ''
  // The title keeps a few columns, so a note too long for the row is cut like any other text.
  if (text.length <= room || note === '' || room - note.length < 6) {
    return cut(text, room)
  }
  return `${cut(text.slice(0, -note.length), room - note.length)}${note}`
}

// Counts the reads begun, so one that lands after a later one began, or after a button's command began, is dropped.
let refreshes = 0
// The reads of the board under way.
let reading = 0
// The buttons' commands under way. No read starts while one runs, so none draws the board without the button's move;
// the read after the command shows what it wrote.
let commands = 0
// The store's newest event id when the last read that succeeded began, null when its file was missing, and when the
// last read began. The next read starts once the id moves or QUIET_MS passes, so a read that failed is tried again on
// the next tick.
let seenEvent: string | null | undefined
let lastRead = 0
// This session's watch of the store, running while it shows the inbox.
let ticking: { cancel: () => void } | null = null
let sampling: { cancel: () => void } | null = null
// The CPU counters at the last sample, which the next one's use is measured against.
let lastCpu: CpuTimes | null = null

/**
 * Runs `/agent-desk:resource-report` in this session, as the person would, for the report on what uses the machine's
 * memory and CPU and what could be freed. It runs once the session is idle, and its answer lands in the chat, where
 * the person says what to free.
 */
async function askForReport($: Engine): Promise<void> {
  await $.command.run({ command: 'agent-desk:resource-report',
    args: 'Report what is using this machine\'s memory and CPU, and what could be freed.' })
  await update($, note, () => 'Asked this session for a resource report; it starts once the session is idle.')
}

/** Samples the machine's load into the header. */
async function sampleLoad($: Engine): Promise<void> {
  const proc = await run($, ['cat', '/proc/meminfo', '/proc/stat'])
  if (proc !== null) {
    const sample = loadOf(proc, lastCpu)
    lastCpu = sample.cpu
    await update($, load, () => sample.load)
  }
}

/** Starts this session's inbox: the pane, a read of the board now, and one whenever the store changes after. */
async function show($: Engine, focus: boolean): Promise<void> {
  if (ticking === null) {
    ticking = $.clock.every(EVENT_MS, () => void watchStore($))
  }
  if (sampling === null) {
    sampling = $.clock.every(LOAD_MS, () => void sampleLoad($))
    void sampleLoad($)
  }
  void refresh($)
  await $.ui.open({ id: PANE, title: 'agent-desk', ...(focus ? { focus: true } : {}) })
}

/** The sessions that show the inbox, as stored. */
async function showing($: Engine): Promise<string[]> {
  const stored = await $.store.get(SHOWING_KEY)
  return Array.isArray(stored) ? stored.filter((one): one is string => typeof one === 'string') : []
}

/**
 * Where lib/store.py saves the newest event id: `desk.sqlite-event` in agent-desk's state folder, under
 * $XDG_STATE_HOME or else ~/.local/state; null without either variable.
 */
async function eventFileOf($: Engine): Promise<string | null> {
  const state = await $.env.get('XDG_STATE_HOME') || await homeState($)
  return state ? `${state}/agent-desk/desk.sqlite-event` : null
}

async function homeState($: Engine): Promise<string | undefined> {
  const home = await $.env.get('HOME')
  return home ? `${home}/.local/state` : undefined
}

/** The store's newest event id as its file holds it, or null when the file is missing or unread. */
async function newestEvent($: Engine): Promise<string | null> {
  if (eventFile === null) {
    return null
  }
  try {
    return (await $.fs.read(eventFile)).trim()
  } catch {
    return null
  }
}

/**
 * Reads the board again when the store's newest event id moved since the last good read began, or when QUIET_MS passed
 * without one. It waits while a read or a button's command is under way, and the tick after that one ends looks again.
 */
async function watchStore($: Engine): Promise<void> {
  if (root === null || reading > 0 || commands > 0) {
    return
  }
  if (await newestEvent($) !== seenEvent || await $.clock.now() - lastRead >= QUIET_MS) {
    await refresh($)
  }
}

/** Runs a button's command, dropping any read under way, which began before the command could write. */
async function command<T>(work: () => Promise<T>): Promise<T> {
  commands += 1
  refreshes += 1
  try {
    return await work()
  } finally {
    commands -= 1
  }
}

/**
 * Reads the board from the local store, noting the store's newest event id first, so a write after it reads again. The
 * id counts as seen once the read succeeds.
 */
async function refresh($: Engine): Promise<void> {
  if (root === null || commands > 0) {
    return
  }
  const mine = ++refreshes
  reading += 1
  let fetched: Board
  let event: string | null
  try {
    event = await newestEvent($)
    lastRead = await $.clock.now()
    await update($, refreshing, () => true)
    // The board's read runs the command, so a read that failed looks for it, and without it the pane says how to
    // install it.
    const read = await fetchBoard(hostOf($), project())
    fetched = read.error === null || await deskFound(hostOf($), project()) ? read : deskMissing(read.at)
  } finally {
    reading -= 1
    if (mine === refreshes) {
      await update($, refreshing, () => false)
    }
  }
  if (mine !== refreshes) {
    return
  }
  if (fetched.error === null) {
    seenEvent = event
  }
  await update($, board, () => fetched)
  if (fetched.error !== null) {
    return
  }
  // The newest issues lead each list, so a new one is among the rows drawn; the line counting the rest is no item.
  const watched = [...fetched.needs, ...fetched.design].filter(row => !row.key.endsWith('-more'))
  const keys = watched.map(row => row.key)
  const before = await read($, known)
  if (before !== null) {
    const seen = new Set(before)
    for (const row of watched.filter(one => !seen.has(one.key))) {
      $.ui.toast(announce(row), { timeoutMs: TOAST_MS })
    }
  }
  await update($, known, () => keys)
}

// The host and port of the web page's address for an item, as board.ts's linkOf writes it.
const PAGE_ADDRESS = /^https?:\/\/([^/:]+):(\d+)\/#/

/**
 * Whether the web page's server listens where `url` points: bash opens a connection to its port, which fails at once
 * when nothing listens there. An address of any other form counts as served.
 */
async function serves($: Engine, url: string): Promise<boolean> {
  const found = PAGE_ADDRESS.exec(url)
  return !found || (await run($, ['bash', '-c', 'exec 3<>"/dev/tcp/$1/$2"', '_', found[1], found[2]])) !== null
}

/**
 * What Open says when the web page isn't served: the command that starts it, and the one that prints the item in a
 * terminal instead, short enough for the header's note.
 */
function notServing(row: Pick<Row, 'number' | 'pr'>): string {
  return `The web page isn't running: run agent-desk web on${row.number === undefined ? ''
    : `, or agent-desk ${row.pr ? 'review' : 'issue'} view ${row.number}`}`
}

async function act($: Engine, row: Row, action: RowAction, surface: string): Promise<void> {
  const say = (text: string | null) => update($, note, () => text)
  const here = project()
  // Narrowed by each branch's test, which a command's closure keeps.
  const { number, run: runId } = row
  if (CONFIRMS.has(action) && !(await confirmed($, row, action))) {
    // Stop Command's question, the command and how long it has run, shows in the header's note while it waits.
    if (action === 'stop-command') {
      await say(row.stopAsk ?? null)
    }
    return
  }
  if (action === 'open' && row.url) {
    const shown = await serves($, row.url)
    if (shown) {
      await run($, ['xdg-open', row.url])
    }
    await say(shown ? null : notServing(row))
  } else if (action === 'log' && row.log) {
    const copied = await $.ui.copy({ text: row.log, surface: surface as never })
    await say(copied.isCopied ? `Copied: ${row.log}` : `Run in a terminal: ${row.log}`)
  } else if (action === 'resume' && row.resume) {
    const copied = await $.ui.copy({ text: row.resume, surface: surface as never })
    await say(copied.isCopied ? `Copied: ${row.resume}` : `Run in a terminal: ${row.resume}`)
    await refresh($)
  } else if (action === 'ready' && number) {
    // Moves the issue to Queued at once. The store saves the label before the command returns, so the read after shows
    // the issue queued or its agent at work; a label or request the store refused puts it back.
    await update($, board, shown => (shown ? moveToQueued(shown, row) : shown))
    const done = await command(() => labelReady(hostOf($), here, number, row.labels))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        queued: shown.queued.filter(one => one.key !== `queued-${row.number}`),
        next: shown.next.some(one => one.key === row.key) ? shown.next : [row, ...shown.next] } : shown))
    }
    await say(done ? `Labelled #${row.number} agent-ready.` : `Couldn't label #${row.number}.`)
    await refresh($)
  } else if (action === 'designed' && number) {
    // Moves the issue to Open at once. The store takes the label off before the command returns, so the read after
    // shows the issue in Open, or in Blocked when an open blocker holds it; an edit the store refused puts it back.
    const before = await read($, board)
    await update($, board, shown => (shown ? moveToOpen(shown, row) : shown))
    const done = await command(() => markDesigned(hostOf($), here, number))
    if (!done) {
      await update($, board, () => before)
    }
    await say(done ? `Moved #${row.number} to Open.` : `Couldn't take needs-design off #${row.number}.`)
    await refresh($)
  } else if (action === 'to-open' && number) {
    // Moves the idea, the backlog issue, or the queued issue, to Open at once. The store takes the label off, or the
    // dispatcher cancels the issue's waiting runs and sets it open, before the command returns, so the read after shows
    // the issue in Open, or where its state puts it; an edit the store refused, or a run that started, puts it back.
    const label = row.key.startsWith('idea-') ? 'idea' : row.key.startsWith('backlog-') ? 'backlog' : null
    const before = await read($, board)
    await update($, board, shown => (shown ? moveToOpen(shown, row) : shown))
    const done = await command(() => (label === 'idea' ? moveIdeaToOpen : label === 'backlog' ? moveBacklogToOpen
      : unqueueIssue)(hostOf($), here, number))
    if (!done) {
      await update($, board, () => before)
    }
    await say(done ? `Moved #${row.number} to Open.`
      : label ? `Couldn't take ${label} off #${row.number}.` : unqueueFailure(number))
    await refresh($)
  } else if (action === 'to-backlog' && number) {
    // Moves the issue to the top of Backlog at once. The store saves the label before the command returns, so the read
    // after shows the issue in Backlog; a label the store refused puts it back.
    const before = await read($, board)
    await update($, board, shown => (shown ? moveToBacklog(shown, row) : shown))
    const done = await command(() => labelBacklog(hostOf($), here, number))
    if (!done) {
      await update($, board, () => before)
    }
    await say(done ? `Moved #${row.number} to Backlog.` : `Couldn't label #${row.number} backlog.`)
    await refresh($)
  } else if ((action === 'up' || action === 'down' || action === 'top' || action === 'bottom') && number
    && listSection(row) !== null) {
    // Moves the issue in its section at once, beside the issue the move passes or to the section's end. The store
    // writes the list order before the command returns, so the read after shows it; a move the store refused, as for
    // an issue that closed since, puts the section back. The section's page stays where it was, so a move across its
    // edge carries the row to the next or previous page.
    const before = await read($, board)
    const to = before && listMoveOf(before, row, action)
    if (!to) {
      return
    }
    await update($, board, shown => (shown ? moveInList(shown, row, action) : shown))
    const done = await command(() => moveInListOrder(hostOf($), here, number, to))
    if (!done) {
      await update($, board, () => before)
    }
    await say(done ? null : listOrderFailure(number))
    await refresh($)
  } else if ((action === 'up' || action === 'down' || action === 'top' || action === 'bottom') && number) {
    // Moves the issue in Queued at once. The store writes the new start order before the command returns, so the read
    // after shows it; a move the store refused, as for an issue that started since, puts the order back.
    const before = await read($, board)
    await update($, board, shown => (shown ? moveInOrder(shown, row, action) : shown))
    const done = await command(() => moveInStartOrder(hostOf($), here, number, action))
    if (!done) {
      await update($, board, () => before)
    }
    await say(done ? null : orderFailure(number))
    await refresh($)
  } else if (action === 'unblock' && number) {
    // Moves the issue to Open at once. The store frees it before the command returns, so the read after shows it in
    // Open; an edit the store refused puts it back in Blocked.
    await update($, board, shown => (shown ? moveToOpen(shown, row) : shown))
    const done = await command(() => unblockIssue(hostOf($), here, number))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        next: shown.next.filter(one => one.key !== `next-${row.number}`),
        blocked: shown.blocked.some(one => one.key === row.key) ? shown.blocked : [row, ...shown.blocked] } : shown))
    }
    await say(done ? `Moved #${row.number} to Open.` : `Couldn't unblock #${row.number}.`)
    await refresh($)
  } else if (action === 'pause' && (row.issue ?? number)) {
    // The dispatcher puts the issue on hold before the command returns and stops its run, so the read after shows it
    // in Blocked once the run's unit is gone.
    const issue = row.issue ?? number ?? 0
    const done = await command(() => pauseIssue(hostOf($), here, issue))
    await say(done ? `Paused #${issue}: it waits on hold until Resume.`
      : `Couldn't pause #${issue}: no agent run works it.`)
    await refresh($)
  } else if (action === 'stop-command' && (row.issue ?? number)) {
    const issue = row.issue ?? number ?? 0
    const done = await command(() => stopCommand(hostOf($), here, issue))
    await say(done ? `Stopped the command #${issue}'s agent waited on; the agent goes on with its failure.`
      : `Couldn't stop #${issue}'s command: its run waits on none.`)
    await refresh($)
  } else if (action === 'unpause' && number) {
    const done = await command(() => unpauseIssue(hostOf($), here, number))
    await say(done ? `Queued #${number} at the top of the start order.`
      : `Couldn't resume #${number}: it isn't on hold.`)
    await refresh($)
  } else if (action === 'start' && (row.pick !== undefined || row.starts?.length)) {
    const done = await command(() => startRow(hostOf($), here, row))
    await say(done ? `Started ${queuedName(row)}.` : `Couldn't start ${queuedName(row)}.`)
    await refresh($)
  } else if (action === 'unpick' && number) {
    // Shows Start on the row at once. lib/hold.py drops the pick before the command returns, so the read after shows
    // the row unpicked; a pick its run claimed meanwhile puts the row back.
    await update($, board, shown => (shown ? moveToUnpicked(shown, row) : shown))
    const done = await command(() => unpickIssue(hostOf($), here, number))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        queued: shown.queued.map(one => (one.key === row.key ? row : one)) } : shown))
    }
    await say(done ? `Unpicked #${number}.` : unpickFailure(number))
    await refresh($)
  } else if (action === 'merge' && number) {
    // Moves the PR to Merging at once. The queue saves it before the command returns, so the read after shows it
    // there until it merges; a PR the queue couldn't take goes back.
    await update($, board, shown => (shown ? moveToMerging(shown, row) : shown))
    const done = await command(() => queueMerge(hostOf($), here, number))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        merging: shown.merging.filter(one => one.key !== `merging-${row.number}`),
        needs: shown.needs.some(one => one.key === row.key) ? shown.needs : [row, ...shown.needs] } : shown))
    }
    await say(queuedNote(row, done))
    await refresh($)
  } else if (action === 'fix' && number) {
    // Moves the PR to Queued at once, where the read after the command shows it from the comment the store holds for
    // the dispatcher; a comment the store refused puts it back.
    await update($, board, shown => (shown ? moveToFixing(shown, row) : shown))
    const done = await command(() => askToFix(hostOf($), here, number))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        queued: shown.queued.filter(one => one.key !== `rerun-pr-${row.number}`),
        needs: shown.needs.some(one => one.key === row.key) ? shown.needs : [row, ...shown.needs] } : shown))
    }
    await say(done ? `Asked ${prName(row)}'s agent to fix it.`
      : `Couldn't comment on ${prName(row)}.`)
    await refresh($)
  } else if (action === 'undraft' && number) {
    // Shows the draft as a ready PR at once. The store clears the draft flag before the command returns, so the read
    // after shows the PR to review; a review the store couldn't mark ready goes back to its draft card.
    await update($, board, shown => (shown ? moveToReview(shown, row) : shown))
    const done = await command(() => markReady(hostOf($), here, number))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        needs: shown.needs.map(one => (one.key === `pr-${row.number}` ? row : one)) } : shown))
    }
    await say(done ? `Marked ${prName(row)} ready for review.` : `Couldn't mark ${prName(row)} ready.`)
    await refresh($)
  } else if (action === 'retry' && runId !== undefined) {
    // Takes the run out of Needs You at once. The dispatcher's request is in the store before the command returns, so
    // the read after shows the run waiting or working; a rerun the dispatcher refused puts it back.
    await update($, board, shown => (shown ? moveToRetried(shown, row) : shown))
    const done = await command(() => retryRun(hostOf($), here, runId))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        needs: shown.needs.some(one => one.key === row.key) ? shown.needs : [row, ...shown.needs] } : shown))
    }
    const verb = row.continues ? ['Continuing', 'continue'] : ['Rerunning', 'rerun']
    await say(done ? `${verb[0]} ${row.note}: ${row.title}.` : `Couldn't ${verb[1]} ${row.note}: ${row.title}.`)
    await refresh($)
  } else if (action === 'restart' && number) {
    // Moves the issue to Queued at once. The dispatcher's request is in the store before the command returns, so the
    // read after shows the agent waiting or working; a start the dispatcher or lib/hold.py refused puts it back.
    await update($, board, shown => (shown ? moveToRestarted(shown, row) : shown))
    const done = await command(() => restartIssue(hostOf($), here, row))
    if (!done) {
      await update($, board, shown => (shown ? { ...shown,
        queued: shown.queued.filter(one => one.key !== `restart-${row.number}`),
        needs: shown.needs.some(one => one.key === row.key) ? shown.needs : [row, ...shown.needs] } : shown))
    }
    await say(done ? `Continuing #${row.number}'s agent.` : `Couldn't continue #${row.number}'s agent.`)
    await refresh($)
  } else if (action === 'cancel' && row.request !== undefined) {
    // The dispatcher ends the request in the store before the command returns, so the read after shows it gone.
    const request = row.request
    const done = await command(() => cancelRequest(hostOf($), here, request))
    await say(done ? `Cancelled the run that waited for ${queuedName(row)}.`
      : `Couldn't cancel the run that waited for ${queuedName(row)}; it may have started.`)
    await refresh($)
  } else if (action === 'dismiss' && runId !== undefined) {
    await dismissRun(hostOf($), runId)
    await update($, board, shown => (shown ? { ...shown, needs: shown.needs.filter(one => one.key !== row.key) } : shown))
  }
}

/** Whether the row's action was already asked to confirm, which clears the ask; otherwise asks for the confirm. */
async function confirmed($: Engine, row: Row, action: RowAction): Promise<boolean> {
  const asking = `${row.key} ${action}`
  const asked = (await read($, confirm)) === asking
  await update($, confirm, () => (asked ? null : asking))
  return asked
}

/**
 * Sorts the project's start order into its default, as the Queued heading's Sort asks once confirmed: the issues that
 * can start now first, blockers ahead of what they block, then by number. The store writes the order before the
 * command returns, so the read after shows it, and after a refused sort the read shows the store's order.
 */
async function sortOrder($: Engine): Promise<void> {
  await update($, confirm, () => null)
  const done = await command(() => sortStartOrder(hostOf($), project()))
  await update($, note, () => (done ? 'Sorted the start order.' : SORT_FAILURE))
  await refresh($)
}

/**
 * Puts a list section's issues back in number order, highest first, as its heading's Sort asks once confirmed. The
 * store writes the list order before the command returns, so the read after shows it, and after a refused sort the
 * read shows the store's order.
 */
async function sortList($: Engine, title: string, rows: Row[]): Promise<void> {
  await update($, confirm, () => null)
  const done = await command(() => sortListOrder(hostOf($), project(), listNumbers(rows)))
  await update($, note, () => (done ? `Sorted ${title} by number.` : listSortFailure(title)))
  await refresh($)
}

async function runChore($: Engine, name: string): Promise<void> {
  // Shows the chore starting at once; the refresh after it reads the run once GitHub lists it, a few seconds on.
  await update($, board, shown => {
    if (!shown) {
      return shown
    }
    const [, , last = 0, next = 0] = choreColumns(shown.chores[0])
    return { ...shown, chores: shown.chores.map((line, index) => (index > 0 && line.split(' ')[0] === name
      ? `${line.slice(0, last)}${'starting now'.padEnd(next - last)}after this run` : line)) }
  })
  const done = await startChore(hostOf($), project(), name)
  await update($, note, () => (done ? `Started ${name}; it waits for a free slot.` : `Couldn't start ${name}.`))
  await $.clock.sleep(done ? 5_000 : 0)
  await refresh($)
}

/** The first row a paged section shows after moving `by` rows from `top`, kept within its `total` rows. */
export function pageTop(top: number, by: number, total: number): number {
  return Math.max(0, Math.min(top + by, total - BACKLOG_ROWS))
}

/** Collapses an expanded section or expands a collapsed one, and keeps the choice for later sessions. */
async function toggleSection($: Engine, title: string): Promise<void> {
  const shown = (await read($, view)) ?? OPEN_VIEW
  const collapsed = shown.collapsed.includes(title)
    ? shown.collapsed.filter(one => one !== title) : [...shown.collapsed, title]
  await update($, view, current => ({ ...(current ?? OPEN_VIEW), collapsed }))
  await $.store.set(COLLAPSED_KEY, collapsed)
}

/** Pages a section's window of issues up or down. */
async function pageSection($: Engine, title: string, by: number, total: number): Promise<void> {
  await update($, view, current => {
    const shown = current ?? OPEN_VIEW
    return { ...shown, offsets: { ...shown.offsets, [title]: pageTop(shown.offsets[title] ?? 0, by, total) } }
  })
}

export const LABELS: Record<RowAction, string> = { open: 'Open', log: 'Log', merge: 'Merge', ready: 'Agent-ready',
  resume: 'Take Over', retry: 'Retry', dismiss: 'Dismiss', fix: 'Fix', close: 'Close', start: 'Start',
  restart: 'Continue',
  designed: 'Designed', 'to-open': 'Move to Open', 'to-backlog': 'Move to Backlog', unblock: 'Unblock',
  undraft: 'Ready', cancel: 'Cancel run', up: 'Move Up', down: 'Move Down', top: 'Move to Top',
  bottom: 'Move to Bottom', unpick: 'Unpick', pause: 'Pause', unpause: 'Resume', 'stop-command': 'Stop Command' }
// The actions that move an issue or PR forward, drawn as each row's main button. Take Over, which copies a command,
// leaves Continue the stopped row's main button.
const FORWARD = new Set<RowAction>(['merge', 'ready', 'restart', 'retry', 'fix', 'start', 'undraft', 'unpause'])
// The actions that swap their button for Confirm and Cancel before they act: every one that changes an issue, a run,
// or a review, as on the web page. Close has no button in the pane and confirms on the web page.
export const CONFIRMS = new Set<RowAction>(['merge', 'fix', 'dismiss', 'ready', 'start', 'unpick', 'retry', 'restart',
  'close', 'designed', 'to-open', 'to-backlog', 'unblock', 'undraft', 'cancel', 'pause', 'unpause', 'stop-command'])
// The actions that act at once: Open and Log, which show or copy, Take Over, which copies a command, and the moves in
// the start order and the list order. Every action in LABELS sits in this list or in CONFIRMS, which a test checks.
export const INSTANT = new Set<RowAction>(['open', 'log', 'resume', 'up', 'down', 'top', 'bottom'])

/** Whether an action draws as its row's main button. */
export function isMain(action: RowAction): boolean {
  return FORWARD.has(action)
}

/** An action's button label on a row: Retry reads Continue on a run whose session it carries on. */
export function labelOf(row: Row, action: RowAction): string {
  return action === 'retry' && row.continues ? LABELS.restart : LABELS[action]
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    // Every session loads the mod, headless agent runs among them, which have nobody to show it to; a project turns
    // it on with a .agent-desk.json in its main checkout.
    root = null
    const common = e.isInteractive
      ? (await run($, ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], e.cwd))?.trim() : undefined
    const top = common ? common.replace(/\/\.git\/?$/, '') : null
    const config = top ? await run($, ['cat', `${top}/.agent-desk.json`], top) : null
    if (top === null || config === null) {
      return result
    }
    let settings: { title?: string } = {}
    try {
      settings = JSON.parse(config)
    } catch {
      return result
    }
    main = top
    root = (await run($, ['git', 'rev-parse', '--show-toplevel'], e.cwd))?.trim() ?? e.cwd
    eventFile = await eventFileOf($)
    const kept = await $.store.get(COLLAPSED_KEY)
    const collapsed = Array.isArray(kept) ? kept.filter((one): one is string => typeof one === 'string') : []
    await update($, view, current => ({ ...(current ?? OPEN_VIEW), collapsed }))
    const title = settings.title ?? main.split('/').at(-1) ?? 'project'
    await $.command.register({ name: 'agent-desk',
      description: `Show the ${title} agent-desk pane in this session: agent runners, PRs, and the queue` })
    // A pane left from before a reload in a session that never ran /agent-desk closes rather than go stale.
    if ((await showing($)).includes(await $.session.id())) {
      void show($, false)
    } else {
      void $.ui.close({ id: PANE })
    }
    return result
  })

  on('command.run', { command: 'agent-desk' }, async $ => {
    if (main === null) {
      return { text: 'This checkout has no .agent-desk.json, so it has no agent-desk pane.' }
    }
    const id = await $.session.id()
    await $.store.set(SHOWING_KEY, [...(await showing($)).filter(one => one !== id), id].slice(-MAX_SHOWING))
    await show($, true)
    return { text: 'agent-desk opened in this session. It opens here again when the session resumes.' }
  })

  // Closing the pane by hand stops this session's refreshes, and it no longer opens the inbox at start.
  on('ui.close', async ($, e, next) => {
    const result = await next(e)
    if (e.id === PANE && e.origin.kind === 'person') {
      ticking?.cancel()
      ticking = null
      sampling?.cancel()
      sampling = null
      const id = await $.session.id()
      await $.store.set(SHOWING_KEY, (await showing($)).filter(one => one !== id))
    }
    return result
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const shown = await read($, board)
    const pending = await read($, confirm)
    const message = await read($, note)
    const layout = (await read($, view)) ?? OPEN_VIEW
    const loading = (await read($, refreshing)) ?? false
    const width = Math.max(30, e.props.bodyColumns ?? 80)

    // The board follows the store within a second, so the header shows no age; it says Loading… until the first read.
    const stamp = shown ? '' : 'Loading… '
    const machine = await read($, load)
    const gauges = machine === null ? [] : gaugesOf(machine)
    const fit = (text: string, room: number) => (text.length <= room ? text : `${text.slice(0, Math.max(room - 1, 0))}…`)

    type Action = { key: string; label: string; onPress: () => unknown; primary?: boolean }
    // A row with a link opens it when pressed, so Open needs no button of its own, and Close stays on the web page,
    // which has room for it on every issue. Log shows only with a command to copy. The action that moves the work
    // forward draws in the accent color, and Dismiss and Cancel stay plain.
    const buttonsOf = (row: Row): Action[] => row.actions.flatMap<Action>(action => {
      if ((row.url && action === 'open') || (action === 'log' && !row.log) || action === 'close') {
        return []
      }
      // The confirm's Cancel takes a key of its own, apart from the row's Cancel run, whose key is `cancel-<row>`.
      if (CONFIRMS.has(action) && pending === `${row.key} ${action}`) {
        return [
          { key: `confirm-${row.key}`, label: `Confirm ${labelOf(row, action).toLowerCase()}`,
            onPress: () => act($, row, action, e.surface), primary: isMain(action) },
          { key: `cancel-confirm-${row.key}`, label: 'Cancel', onPress: async () => {
            await update($, confirm, () => null)
            // Stop Command's question goes with its confirm.
            if (action === 'stop-command') {
              await update($, note, () => null)
            }
          } },
        ]
      }
      return [{ key: `${action}-${row.key}`, label: labelOf(row, action), onPress: () => act($, row, action, e.surface),
        primary: isMain(action) }]
    })
    // A button draws its label in brackets and a space each side, with a space after it.
    const buttonsWidth = (actions: Action[]) => actions.reduce((sum, one) => sum + one.label.length + 5, 0)
    // The header's note keeps clear of the Refresh button at its widest label, so it never wraps mid-read.
    const refreshWidth = buttonsWidth([{ key: '', label: 'Refreshing…', onPress: () => null }])

    // Every row lines up in three columns across the sections: the issue or PR number, the description, cut to fit,
    // and the buttons, flush right. A row's time, how long ago or for how long, sits right-justified before them. The
    // chores' table spans the first two, and its Run buttons sit flush right in the third.
    const rows = shown && !shown.error
      ? [shown.runners, shown.needs, shown.queued, shown.next, shown.blocked ?? [], shown.design, shown.backlog,
        shown.ideas, shown.merged].flat() : []
    const numberOf = cite
    // Teal, which no heading or state in the pane takes, and plain issue numbers keep the two kinds apart.
    const PR_HUE = '#00afaf'
    const numberWidth = Math.max(0, ...rows.map(row => numberOf(row).length)) + 1
    const actionWidth = Math.max(buttonsWidth([{ key: '', label: 'Run', onPress: () => null }]),
      ...rows.map(row => buttonsWidth(buttonsOf(row)) + (row.time ? row.time.length + 1 : 0)))
    const textWidth = Math.max(10, width - 2 - actionWidth)
    const descWidth = Math.max(5, textWidth - numberWidth - 1)
    const numberCell = (row: Row) => {
      const cell = numberOf(row).padEnd(numberWidth)
      return row.pr ? <Text color={PR_HUE}>{cell}</Text> : cell
    }

    // The number and description of a row with a link are one plain Button: the pointer inverts it, the rest of the
    // row turns blue while the pointer is anywhere on it, and a press opens the link.
    const line = (row: Row) => (
      <Box key={row.key} flexDirection="row">
        <Box width={textWidth}>
          {row.url ? (
            <Button key={`open-${row.key}`} plain onPress={() => act($, row, 'open', e.surface)}>
              <Text dimColor={row.dim} hover={{ color: 'suggestion' }}>{numberCell(row)}{fitRow(row.text, descWidth)}</Text>
            </Button>
          ) : (
            <Text dimColor={row.dim || row.notice === 'muted'} color={row.notice === 'warning' ? 'warning' : undefined}>
              {numberCell(row)}{fitRow(row.text, descWidth)}
            </Text>
          )}
        </Box>
        <Box width={actionWidth} flexDirection="row">
          <Box flexGrow={1} justifyContent="flex-end">{row.time && <Text dimColor>{row.time} </Text>}</Box>
          {buttonsOf(row).map(one => (
            <Button key={one.key} label={one.label} variant={one.primary ? 'primary' : undefined}
              onPress={one.onPress} />
          ))}
        </Box>
      </Box>
    )

    // Each section's heading has its own color, mostly from the person's theme. Runners takes a deep orange
    // (256-color 202) and Needs You a plain red, since the theme draws `claude` and `error` too close to pink, and
    // 208 read yellow beside Queued. Blocked takes a tan (256-color 137), a hue no theme color or row state uses.
    // bashBorder is the theme's pink, and Merging takes a teal (256-color 37) apart from them all. GitHub takes a
    // steel blue (256-color 67), which its rows' quiet and warning tones read apart from. Backlog takes a slate violet
    // (256-color 103), quieter than Open's blue, for work set aside.
    const HUES: Record<string, string> = { 'Runners': '#ff5f00', 'Needs You': '#ff3b30',
      'Queued': 'warning', 'Needs Design': 'merged', 'Open': 'suggestion', 'Blocked': '#af875f', 'Backlog': '#8787af',
      'Ideas': 'success',
      'Merging': '#00afaf', 'Recently Merged': 'inactive',
      'Chores': 'bashBorder', 'GitHub': '#5f87af' }
    // A heading collapses or expands its section when pressed; - marks an expanded one and + a collapsed one. A paged
    // section counts its issues in the heading.
    const isCollapsed = (title: string) => layout.collapsed.includes(title)
    const heading = (title: string, count?: number) => (
      <Button key={`toggle-${title}`} plain onPress={() => toggleSection($, title)}>
        <Text bold color={HUES[title] ?? 'claude'}>
          {`${isCollapsed(title) ? '+' : '-'} ${title}${count ? ` (${count})` : ''}`}
        </Text>
      </Button>
    )

    // A paged section shows BACKLOG_ROWS issues at a time, with a line under them that pages through the rest. The
    // count takes a fixed width, room for three digits in each number, so the arrows after it stay put while paging,
    // and they sit clear of the rows' own buttons. The count takes its section's color. ⤒ and ⤓ jump to the first
    // and last page.
    const pager = (title: string, top: number, shownRows: number, total: number) => (
      <Box flexDirection="row" marginLeft={2}>
        <Text color={HUES[title]}>{`${top + 1}-${top + shownRows} of ${total}`.padEnd(PAGER_COUNT_WIDTH)}</Text>
        <Button key={`first-${title}`} label="⤒" variant="primary" onPress={() => pageSection($, title, -total, total)} />
        <Button key={`up-${title}`} label="▲" variant="primary"
          onPress={() => pageSection($, title, -BACKLOG_ROWS, total)} />
        <Button key={`down-${title}`} label="▼" variant="primary"
          onPress={() => pageSection($, title, BACKLOG_ROWS, total)} />
        <Button key={`last-${title}`} label="⤓" variant="primary" onPress={() => pageSection($, title, total, total)} />
      </Box>
    )

    // Queued's heading has a Sort button beside it while two or more queued or waiting issues hold the start order,
    // and each list section's heading while two or more of its issues hold the list order. A sort replaces any order
    // set by hand, so it asks to confirm first; the heading itself still collapses the section. Queued's buttons keep
    // the keys `sort-order`, `confirm-sort-order`, and `cancel-sort-order`, and a list section's end in its title.
    const sortButton = (title: string, id: string, sort: () => unknown) => (pending === sortConfirm(title) ? [
      <Button key={`confirm-sort-${id}`} label="Confirm sort" variant="primary" onPress={sort} />,
      <Button key={`cancel-sort-${id}`} label="Cancel" onPress={() => update($, confirm, () => null)} />,
    ] : <Button key={`sort-${id}`} label="Sort" onPress={() => update($, confirm, () => sortConfirm(title))} />)
    const orderSort = () => sortButton('Queued', 'order', () => sortOrder($))
    const listSort = (title: string, rows: Row[]) => (listSortable(rows)
      ? sortButton(title, title, () => sortList($, title, rows)) : undefined)

    const section = (title: string, rows: Row[], beside?: unknown) => {
      const paged = PAGED.has(title) && rows.length > BACKLOG_ROWS
      const top = paged ? pageTop(layout.offsets[title] ?? 0, 0, rows.length) : 0
      const visible = paged ? rows.slice(top, top + BACKLOG_ROWS) : rows
      const head = heading(title, PAGED.has(title) ? rows.length : undefined)
      return (
        <Box flexDirection="column" marginTop={1}>
          {beside ? <Box flexDirection="row">{head}<Text> </Text>{beside}</Box> : head}
          {!isCollapsed(title) && (rows.length === 0 ? <Text dimColor>  nothing</Text>
            : visible.map(row => <Box marginLeft={2}>{line(row)}</Box>))}
          {!isCollapsed(title) && paged && pager(title, top, visible.length, rows.length)}
        </Box>
      )
    }

    // chores.sh status prints a header row, then one row per chore in columns: the chore (at least 14 wide), every
    // (6), last run (28), and next, each followed by a space. A chore that isn't running has a Run button, dim until
    // the pointer reaches it, since it starts a headless run.
    const choreTable = (lines: string[]) => {
      const [header, ...chores] = lines
      const room = textWidth - 1
      const [, , lastStart = 0, nextStart = 0] = choreColumns(header)
      const cells = (text: string) => [text.slice(0, lastStart), text.slice(lastStart, nextStart),
        text.slice(nextStart)]
      const lastColor = (cell: string) => (cell.startsWith('done') ? 'success'
        : /^(running|starting)/.test(cell) ? 'warning'
        : /^(failure|cancelled|timed_out)/.test(cell) ? 'error' : undefined)
      const nextColor = (cell: string) => (cell.startsWith('now') ? 'success'
        : cell.startsWith('waiting') ? HUES['Needs You'] : undefined)
      return (
        <Box flexDirection="column" marginTop={1}>
          {heading('Chores')}
          {!isCollapsed('Chores') && header !== undefined && <Text color="text" mono>  {fit(header, room)}</Text>}
          {!isCollapsed('Chores') && header !== undefined && (
            <Text color="subtle" mono>  {'─'.repeat(Math.min(Math.max(...lines.map(one => one.length)), room))}</Text>
          )}
          {!isCollapsed('Chores') && chores.map(row => {
            const [name = '', last = '', next = ''] = cells(fit(row, room))
            const chore = row.split(' ')[0] ?? ''
            return (
              <Box flexDirection="row" marginLeft={2}>
                <Box width={textWidth}>
                  <Text mono>
                    {name}<Text color={lastColor(last)}>{last}</Text><Text color={nextColor(next)}>{next}</Text>
                  </Text>
                </Box>
                <Box width={actionWidth} justifyContent="flex-end">
                  {!/^(running|starting)/.test(last) && (
                    <Button key={`run-chore-${chore}`} label="Run" dimColor onPress={() => runChore($, chore)} />
                  )}
                </Box>
              </Box>
            )
          })}
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        {/* The header's two rows: the Refresh button with the latest action's note at the right, then the machine's
            gauges, which keep their row before the first sample so the sections below don't jump. */}
        <Box flexDirection="row" justifyContent="space-between">
          <Box flexDirection="row">
            {stamp !== '' && <Text dimColor>{stamp}</Text>}
            <Button key="refresh" label={loading ? 'Refreshing…' : 'Refresh'} dimColor={loading}
              onPress={() => refresh($)} />
          </Box>
          {message !== null && (
            <Text color="suggestion">{fit(message.replace(/\.$/, ''), width - stamp.length - refreshWidth - 1)}</Text>
          )}
        </Box>
        <Box flexDirection="row" justifyContent="space-between">
          <Box flexDirection="row">
            {gauges.length === 0 ? <Text> </Text> : gauges.map((one, index) => (
              <Text key={`gauge-${one.text.split(' ')[0]}`} color={one.color}>
                {`${index === 0 ? '' : '  '}${one.text}`}
              </Text>
            ))}
          </Box>
          {gauges.length > 0 && <Button key="report" label="Report" onPress={() => askForReport($)} />}
        </Box>
        {shown?.error && <Text color="error">{shown.error}</Text>}
        {shown && !shown.error && (
          <Box flexDirection="column">
            {section('Runners', shown.runners.length > 0 ? shown.runners
              : [{ key: 'runners-none', text: 'nothing running', actions: [] }])}
            {section('Needs You', shown.needs)}
            {section('Merging', shown.merging)}
            {section('Queued', shown.queued, sortable(shown.queued) ? orderSort() : undefined)}
            {section('Open', shown.next, listSort('Open', shown.next))}
            {/* A board stored before Blocked existed has none until the next refresh. */}
            {section('Blocked', shown.blocked ?? [], listSort('Blocked', shown.blocked ?? []))}
            {section('Needs Design', shown.design, listSort('Needs Design', shown.design))}
            {section('Backlog', shown.backlog, listSort('Backlog', shown.backlog))}
            {section('Ideas', shown.ideas, listSort('Ideas', shown.ideas))}
            {section('Recently Merged', shown.merged)}
            {choreTable(shown.chores)}
            {/* Where the desk touches GitHub, last, so it never pushes the work down. A board stored before the
                group existed has none until the next refresh. */}
            {(shown.github?.length ?? 0) > 0 && section('GitHub', shown.github ?? [])}
          </Box>
        )}
      </Box>
    )
  })
}
