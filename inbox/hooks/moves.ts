// How the board changes the moment the person presses a button, before the board's next read shows the move. Like
// board.ts, it imports no Claude Code module and no Node module, so the pane and the web server share it.
import type { Board, Row, RowAction } from '../types'

/** A PR row's name in a toast or a note, with the issue an agent PR's branch works: PR #189 (#164), or PR #190. */
export function prName(row: Pick<Row, 'number' | 'issue'>): string {
  return `PR #${row.number}${row.issue === undefined ? '' : ` (#${row.issue})`}`
}

/** What a new row in Needs you says as a toast. */
export function announce(row: Row): string {
  if (row.key.startsWith('pr-')) {
    return row.text.startsWith('conflicts') ? `${prName(row)} has merge conflicts` : `${prName(row)} is ready for review`
  }
  if (row.key.startsWith('decide-')) {
    return `#${row.number} needs a decision`
  }
  if (row.key === 'merge-stopped') {
    return `The ${row.text}`
  }
  if (row.key.startsWith('stopped-')) {
    return `#${row.number}'s agent stopped before its PR was ready`
  }
  if (row.key.startsWith('draft-')) {
    return `${prName(row)} is a draft its agent finished`
  }
  if (row.key.startsWith('stacked-')) {
    return `#${row.number}'s work merged with its base PR; close the issue`
  }
  return `An agent run failed: ${row.text.replace(/^failed\s+/, '')}`
}

/** The board with an issue moved from Open, Blocked, or Needs Design to Queued. */
export function moveToQueued(shown: Board, row: Row): Board {
  const queued: Row = { key: `queued-${row.number}`, text: row.title ?? row.text, number: row.number, url: row.url,
    title: row.title, actions: ['open'] }
  return { ...shown, next: shown.next.filter(one => one.number !== row.number),
    design: shown.design.filter(one => one.number !== row.number),
    blocked: shown.blocked.filter(one => one.number !== row.number),
    queued: [...shown.queued.filter(one => one.key !== queued.key), queued] }
}

/**
 * The board with an issue moved from Needs Design, once Designed took its label off, from Blocked, once Unblock freed
 * it, from Ideas or Backlog, once Move to Open took its label off, or from Queued, once Move to Open cancelled its
 * waiting run, to the top of Open, as the newest issue there. After Designed or Move to Open, the next refresh moves it to Blocked when
 * an open blocker holds it. It keeps its labels until that refresh, so the web page's panel reads the issue again once
 * the store's labels reach the board.
 */
export function moveToOpen(shown: Board, row: Row): Board {
  const open: Row = { key: `next-${row.number}`, text: row.title ?? row.text, number: row.number, url: row.url,
    title: row.title, ...(row.labels ? { labels: row.labels } : {}),
    actions: ['open', 'ready', 'to-backlog', 'close'] }
  return { ...shown, design: shown.design.filter(one => one.number !== row.number),
    blocked: shown.blocked.filter(one => one.number !== row.number),
    backlog: shown.backlog.filter(one => one.number !== row.number),
    ideas: shown.ideas.filter(one => one.number !== row.number),
    queued: shown.queued.filter(one => one.pr || one.number !== row.number),
    designTotal: Math.max(0, shown.designTotal - (shown.design.some(one => one.number === row.number) ? 1 : 0)),
    next: [open, ...shown.next.filter(one => one.key !== open.key)] }
}

/**
 * The board with an issue moved from Open to the top of Backlog, as the newest issue there, once Move to Backlog
 * labelled it backlog. It keeps its labels until the next refresh adds backlog to them.
 */
export function moveToBacklog(shown: Board, row: Row): Board {
  const later: Row = { key: `backlog-${row.number}`, text: row.title ?? row.text, number: row.number, url: row.url,
    title: row.title, ...(row.labels ? { labels: row.labels } : {}), actions: ['open', 'to-open', 'close'] }
  return { ...shown, next: shown.next.filter(one => one.number !== row.number),
    backlog: [later, ...shown.backlog.filter(one => one.key !== later.key)] }
}

/** Merging's row for a PR in the merge queue, with what it waits on, and `stopped` for one the queue stopped on. */
export function mergingRow(title: string, note: string, ref: Pick<Row, 'number' | 'issue' | 'pr' | 'url'>,
  state?: 'stopped'): Row {
  return { key: `merging-${ref.number}`, text: `${title}  (${note})`, ...ref, title, ...(state ? { state } : {}), note,
    actions: ['open'] }
}

/** The board with a PR moved from Needs You to the end of the merge queue in Merging. */
export function moveToMerging(shown: Board, row: Row): Board {
  const merging = mergingRow(row.title ?? row.text.replace(/^review\s+/, ''), 'queued',
    { number: row.number, issue: row.issue, pr: true, url: row.url })
  return { ...shown, needs: shown.needs.filter(one => one.key !== row.key),
    merging: [...(shown.merging ?? []).filter(one => one.key !== merging.key), merging] }
}

/** Queued's row for a ready PR whose agent went back to it and waits for a free agent runner. */
export function fixingRow(title: string, conflicts: boolean, ref: Pick<Row, 'number' | 'issue' | 'pr' | 'url'>): Row {
  const note = conflicts ? 'fixing conflicts' : 'back to its PR'
  return { key: `rerun-pr-${ref.number}`, text: `${title}  (${note})`, ...ref, title, note, actions: ['open'] }
}

/**
 * The board with a conflicted or failing PR, or a draft whose checks fail or that conflicts with main, moved from Needs
 * You to Queued, once its agent was asked to fix it.
 */
export function moveToFixing(shown: Board, row: Row): Board {
  const conflicts = row.state === 'draft' ? row.conflicts === true : row.state !== 'failing'
  const queued = fixingRow(row.title ?? row.text.replace(/^(conflicts|failing|draft)\s+/, ''), conflicts,
    { number: row.number, issue: row.issue, pr: row.pr, url: row.url })
  return { ...shown, needs: shown.needs.filter(one => one.key !== row.key),
    queued: [...shown.queued.filter(one => one.key !== queued.key), queued] }
}

/**
 * The board with a draft PR, once Ready marked it ready for review, shown in its place in Needs You as a PR to review
 * and merge. The next refresh shows it with conflicts instead when it has them.
 */
export function moveToReview(shown: Board, row: Row): Board {
  const title = row.title ?? row.text.replace(/^draft\s+/, '')
  const review: Row = { key: `pr-${row.number}`, text: `review  ${title}`, number: row.number, issue: row.issue,
    pr: row.pr, url: row.url, title, state: 'review', since: row.since, actions: ['open', 'merge'] }
  return { ...shown, needs: shown.needs.map(one => (one.key === row.key ? review : one)) }
}

/** The board with a failed run left out of Needs You, once it was asked to run again. */
export function moveToRetried(shown: Board, row: Row): Board {
  return { ...shown, needs: shown.needs.filter(one => one.key !== row.key) }
}

/** The board with a stopped issue moved from Needs You to Queued, once Continue started its agent again. */
export function moveToRestarted(shown: Board, row: Row): Board {
  const title = row.title ?? row.text.replace(/^stopped\s+/, '')
  const queued: Row = { key: `restart-${row.number}`, text: `${title}  (continuing)`, number: row.number, url: row.url,
    title, note: 'continuing', actions: ['open'] }
  return { ...shown, needs: shown.needs.filter(one => one.key !== row.key),
    queued: [...shown.queued.filter(one => one.key !== queued.key), queued] }
}

/**
 * The board with a picked queued or waiting issue's row unpicked in place, once Unpick dropped its pick: it reads the
 * note the board kept for it in `unpicked`, and shows Start, which picks the issue again, in place of Unpick.
 */
export function moveToUnpicked(shown: Board, row: Row): Board {
  const { unpicked, note: _note, ...rest } = row
  const title = row.title ?? row.text
  const unpickedRow: Row = { ...rest, text: unpicked ? `${title}  (${unpicked})` : title,
    ...(unpicked ? { note: unpicked } : {}), ...(row.number === undefined ? {} : { pick: row.number }),
    actions: row.actions.map(one => (one === 'unpick' ? 'start' : one)) }
  return { ...shown, queued: shown.queued.map(one => (one.key === row.key ? unpickedRow : one)) }
}

/**
 * The board with a closed issue's card taken out of whichever section showed it. A queued or waiting issue leaves the
 * start order too, and the issues left in it get the moves their new places allow.
 */
export function moveToClosed(shown: Board, row: Row): Board {
  const without = (rows: Row[]) => rows.filter(one => one.key !== row.key)
  const queued = without(shown.queued)
  const ordered = queued.filter(inOrder)
  return { ...shown, needs: without(shown.needs), design: without(shown.design), next: without(shown.next),
    blocked: without(shown.blocked), backlog: without(shown.backlog), ideas: without(shown.ideas),
    queued: queued.length === shown.queued.length ? shown.queued : [...ordered.map((one, index) => ({ ...one,
      actions: withOrderMoves(one.actions, index, ordered.length) })), ...queued.filter(one => !inOrder(one))] }
}

/** The moves of an issue in its project's start order: one place up, one place down, or to the top. */
export type OrderMove = 'up' | 'down' | 'top'
const ORDER_MOVES = new Set<RowAction>(['up', 'down', 'top'])

/** Whether a Queued row stands for an issue with a place in its project's start order: a queued or waiting one. */
export function inOrder(row: Pick<Row, 'key'>): boolean {
  return row.key.startsWith('queued-') || row.key.startsWith('waiting-')
}

/**
 * A row's actions with the moves its place in an order of `count` rows allows, after the rest: Move Up and Move to Top
 * on every row but the first, and Move Down on every row but the last.
 */
export function withOrderMoves(actions: RowAction[], index: number, count: number): RowAction[] {
  const moves: RowAction[] = [...(index > 0 ? ['up'] : []), ...(index < count - 1 ? ['down'] : []),
    ...(index > 0 ? ['top'] : [])] as RowAction[]
  return [...actions.filter(one => !ORDER_MOVES.has(one)), ...moves]
}

/**
 * The board with a queued or waiting issue moved in Queued one place up, one place down, to the top of the issues in
 * the start order, which lead Queued, or to a place among them from 1, the last for a place past the end, each with
 * the moves its new place allows.
 */
export function moveInOrder(shown: Board, row: Row, where: OrderMove | number): Board {
  const ordered = shown.queued.filter(inOrder)
  const at = ordered.findIndex(one => one.key === row.key)
  if (at < 0) {
    return shown
  }
  const to = typeof where === 'number' ? Math.max(0, Math.min(ordered.length, where) - 1)
    : where === 'top' ? 0 : where === 'up' ? Math.max(0, at - 1) : Math.min(ordered.length - 1, at + 1)
  const moved = [...ordered]
  moved.splice(to, 0, ...moved.splice(at, 1))
  return { ...shown, queued: [...moved.map((one, index) => ({ ...one,
    actions: withOrderMoves(one.actions, index, moved.length) })), ...shown.queued.filter(one => !inOrder(one))] }
}
