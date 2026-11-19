// What a card's buttons and a review page's buttons do: each moves its card at once where the button moves one, runs
// its command through agent-desk, and reaches the server's event stream and board reads through a Loop.
import type { Board, Row, RowAction } from '../inbox/types'
import { askToFix, cancelRequest, closeIssue, closeReview, dismissRun, labelBacklog, labelReady, markDesigned,
  markReady, moveBacklogToOpen, moveIdeaToOpen, moveInStartOrder, orderFailure, pauseIssue, queueMerge, queuedName,
  queuedNote, restartIssue, retryRun, startRow, unblockIssue, unpauseIssue, unpickFailure, unpickIssue,
  unqueueFailure, unqueueIssue } from '../inbox/hooks/actions.ts'
import type { CloseReason } from '../inbox/hooks/actions.ts'
import { moveInOrder, moveToBacklog, moveToClosed, moveToFixing, moveToMerging, moveToOpen, moveToQueued,
  moveToRestarted, moveToRetried, moveToReview, moveToUnpicked, prName } from '../inbox/hooks/moves.ts'
import { NOTHING_TO_SEND, sendToAgent } from './review.ts'
import { host } from './state.ts'
import type { Desk, Loop } from './state.ts'
import { storedItem } from './store.ts'

/**
 * Moves the row at once, then makes the move with agent-desk. A read of the board under way when the command starts
 * doesn't land, and none starts while it runs, since either may read the store before the command writes. Each
 * command writes the store or the merge queue before it returns, so the read after it shows the move. A move the store
 * refuses goes back, unless another move changed the board in the meantime.
 */
async function move(loop: Loop, desk: Desk, moved: (board: Board) => Board, make: () => Promise<boolean>):
  Promise<boolean> {
  const before = desk.board
  if (before === null) {
    return false
  }
  desk.board = moved(before)
  const after = desk.board
  loop.publish()
  desk.commands += 1
  desk.refreshes += 1
  let done: boolean
  try {
    done = await make()
  } finally {
    desk.commands -= 1
  }
  if (!done) {
    if (desk.board === after) {
      desk.board = before
    }
  }
  return done
}

/** Closes an issue's card's issue, with a comment when one is given, and returns whether the store closed it. */
export async function close(loop: Loop, desk: Desk, row: Row, reason: CloseReason, comment?: string): Promise<boolean> {
  const project = { root: desk.main, main: desk.main }
  return move(loop, desk, board => moveToClosed(board, row),
    () => closeIssue(host, project, row.number ?? 0, reason, comment))
}

/**
 * The reason a close from an issue's comment box asks for, from the post's `reason` and `duplicateOf`, or why the
 * server refuses it: a close as a duplicate names another issue of the project by its number, with or without #.
 */
export function closeReasonOf(desk: Desk, row: Row, reason: unknown, duplicateOf: unknown):
  { reason: CloseReason } | { error: string } {
  if (reason !== 'duplicate') {
    return { reason: reason === 'not planned' ? 'not planned' : 'completed' }
  }
  const text = String(duplicateOf ?? '').trim().replace(/^#/, '')
  const number = /^\d{1,9}$/.test(text) ? Number(text) : 0
  return number < 1 ? { error: 'a duplicate names the number of the issue it duplicates' }
    : number === row.number ? { error: `#${number} can't be a duplicate of itself` }
    : storedItem(desk.repo, number)?.kind !== 'issue' ? { error: `${desk.repo} has no issue #${number}` }
    : { reason: { duplicateOf: number } }
}

/**
 * Frees a Blocked card's issue of on-hold and its open blockers, moving it to Open at once; a refused edit puts it
 * back in Blocked and toasts.
 */
export async function unblock(loop: Loop, desk: Desk, row: Row): Promise<boolean> {
  const project = { root: desk.main, main: desk.main }
  const number = row.number ?? 0
  const done = await move(loop, desk, board => moveToOpen(board, row), () => unblockIssue(host, project, number))
  if (!done) {
    loop.fail(desk, `Couldn't unblock #${number}.`)
  }
  return done
}

/**
 * Marks a draft card's PR ready for review, showing it at once as a PR to review and merge; a PR the store couldn't
 * mark ready goes back to its draft card and toasts.
 */
export async function undraft(loop: Loop, desk: Desk, row: Row): Promise<boolean> {
  const project = { root: desk.main, main: desk.main }
  const done = await move(loop, desk, board => moveToReview(board, row),
    () => markReady(host, project, row.number ?? 0))
  if (!done) {
    loop.fail(desk, `Couldn't mark ${prName(row)} ready.`)
  }
  return done
}

/**
 * Moves a queued or waiting issue's card to a place in its project's start order, from 1, as a drop on the page does:
 * at once on the board, then in the store. A place the store refuses puts the card back and toasts.
 */
export async function place(loop: Loop, desk: Desk, row: Row, to: number): Promise<boolean> {
  const project = { root: desk.main, main: desk.main }
  const number = row.number ?? 0
  const done = await move(loop, desk, board => moveInOrder(board, row, to),
    () => moveInStartOrder(host, project, number, to))
  if (!done) {
    loop.fail(desk, orderFailure(number))
  }
  await loop.refresh(desk)
  return done
}

/**
 * Carries out a card's button, toasts when its command fails, and reads the project's board again. A button that
 * moves its card moves it at once, and the move takes the place of a message when the command works.
 */
export async function act(loop: Loop, desk: Desk, row: Row, action: RowAction): Promise<void> {
  const project = { root: desk.main, main: desk.main }
  const number = row.number ?? 0
  let done = true
  let failure = ''
  if (action === 'ready') {
    done = await move(loop, desk, board => moveToQueued(board, row),
      () => labelReady(host, project, number, row.labels))
    failure = `Couldn't label #${number}.`
  } else if (action === 'start') {
    done = await startRow(host, project, row)
    failure = `Couldn't start ${queuedName(row)}.`
  } else if (action === 'unpick') {
    done = await move(loop, desk, board => moveToUnpicked(board, row), () => unpickIssue(host, project, number))
    failure = unpickFailure(number)
  } else if (action === 'merge') {
    done = await move(loop, desk, board => moveToMerging(board, row), () => queueMerge(host, project, number))
    failure = queuedNote(row, false)
  } else if (action === 'fix') {
    done = await move(loop, desk, board => moveToFixing(board, row), () => askToFix(host, project, number))
    failure = `Couldn't comment on ${prName(row)}.`
  } else if (action === 'undraft') {
    await undraft(loop, desk, row)
  } else if (action === 'retry' && row.run !== undefined) {
    const id = row.run
    done = await move(loop, desk, board => moveToRetried(board, row), () => retryRun(host, project, id))
    failure = `Couldn't ${row.continues ? 'continue' : 'rerun'} ${row.note}: ${row.title}.`
  } else if (action === 'restart') {
    done = await move(loop, desk, board => moveToRestarted(board, row), () => restartIssue(host, project, row))
    failure = `Couldn't continue #${number}'s agent.`
  } else if (action === 'designed') {
    done = await move(loop, desk, board => moveToOpen(board, row), () => markDesigned(host, project, number))
    failure = `Couldn't take needs-design off #${number}.`
  } else if (action === 'to-open' && row.key.startsWith('idea-')) {
    done = await move(loop, desk, board => moveToOpen(board, row), () => moveIdeaToOpen(host, project, number))
    failure = `Couldn't take idea off #${number}.`
  } else if (action === 'to-open' && row.key.startsWith('backlog-')) {
    done = await move(loop, desk, board => moveToOpen(board, row), () => moveBacklogToOpen(host, project, number))
    failure = `Couldn't take backlog off #${number}.`
  } else if (action === 'to-backlog') {
    done = await move(loop, desk, board => moveToBacklog(board, row), () => labelBacklog(host, project, number))
    failure = `Couldn't label #${number} backlog.`
  } else if (action === 'to-open') {
    done = await move(loop, desk, board => moveToOpen(board, row), () => unqueueIssue(host, project, number))
    failure = unqueueFailure(number)
  } else if (action === 'up' || action === 'down' || action === 'top') {
    done = await move(loop, desk, board => moveInOrder(board, row, action),
      () => moveInStartOrder(host, project, number, action))
    failure = orderFailure(number)
  } else if (action === 'unblock') {
    await unblock(loop, desk, row)
  } else if (action === 'close') {
    done = await close(loop, desk, row, 'completed')
    failure = `Couldn't close #${number}.`
  } else if (action === 'pause') {
    // An agent's row on an issue with a review carries the review's number, and the issue's as `issue`.
    const issue = row.issue ?? number
    done = await pauseIssue(host, project, issue)
    failure = `Couldn't pause #${issue}: no agent run works it.`
  } else if (action === 'unpause') {
    done = await unpauseIssue(host, project, number)
    failure = `Couldn't resume #${number}: it isn't on hold.`
  } else if (action === 'cancel' && row.request !== undefined) {
    done = await cancelRequest(host, project, row.request)
    failure = `Couldn't cancel the run that waited for ${queuedName(row)}; it may have started.`
  } else if (action === 'dismiss' && row.run !== undefined) {
    await dismissRun(host, row.run)
    desk.board = desk.board && { ...desk.board, needs: desk.board.needs.filter(one => one.key !== row.key) }
    loop.publish()
    return
  } else {
    return
  }
  if (!done) {
    loop.fail(desk, failure)
  }
  await loop.refresh(desk)
}

/** What a review's page asks of the server: send its comments to the agent, mark it ready, queue it, or close it. */
export type ReviewAction = 'send' | 'ready' | 'merge' | 'close'
export const REVIEW_ACTIONS: ReviewAction[] = ['send', 'ready', 'merge', 'close']

/**
 * Carries out a button on a review's page, and returns the status and answer the page gets, which the page toasts.
 * Each acts on an open review in the store and reads the project's board again: Send to agent posts the box's text and
 * the unsent line comments as one comment to the agent, Ready clears the draft flag, Merge queues the review in the
 * merge queue, and Close closes it, which starts its cleanup.
 */
export async function reviewAction(loop: Loop, desk: Desk, number: number, action: ReviewAction, text: string):
  Promise<{ status: number; data: Record<string, unknown> }> {
  const item = Number.isInteger(number) && number > 0 ? storedItem(desk.repo, number) : null
  if (item?.kind !== 'review') {
    return { status: 404, data: { error: 'no such review' } }
  }
  if (item.state === 'closed') {
    return { status: 409, data: { error: `#${number} is closed` } }
  }
  const project = { root: desk.main, main: desk.main }
  let said: string
  if (action === 'send') {
    const sent = await sendToAgent(desk, number, text)
    if (typeof sent === 'string') {
      return { status: sent === NOTHING_TO_SEND ? 400 : sent.startsWith('no agent works') ? 409 : 502,
        data: { error: sent } }
    }
    said = `Sent ${sent.sent === 0 ? 'your comment' : `${sent.sent} line comment${sent.sent === 1 ? '' : 's'}`} on`
      + ` #${number} to the agent.`
  } else {
    const done = action === 'ready' ? await markReady(host, project, number)
      : action === 'merge' ? await queueMerge(host, project, number) : await closeReview(host, project, number)
    said = action === 'merge' ? queuedNote({ number }, done)
      : action === 'ready' ? done ? `Marked #${number} ready for review.` : `Couldn't mark #${number} ready.`
      : done ? `Closed #${number}.` : `Couldn't close #${number}.`
    if (!done) {
      return { status: 502, data: { error: said.replace(/\.$/, '') } }
    }
  }
  void loop.refresh(desk)
  return { status: 200, data: { said } }
}
