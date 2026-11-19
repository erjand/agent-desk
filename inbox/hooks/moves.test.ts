import { expect, test } from 'claude-code/testing'

import type { Board, Row } from '../types'
import { announce, inOrder, mergingRow, moveInOrder, moveToBacklog, moveToClosed, moveToFixing, moveToMerging,
  moveToOpen, moveToRestarted, moveToRetried, moveToReview, moveToUnpicked, withOrderMoves } from './moves.ts'

// The board the moves start from. The sections these tests read match the board in pane.fixtures.ts.
const BOARD: Board = {
  at: '09:15',
  runners: [],
  needs: [
    { key: 'pr-61', text: 'review  Model fresh food', number: 61, pr: true, issue: 60, url: 'https://example.test/61',
      actions: ['open', 'merge'] },
    { key: 'pr-62', text: 'conflicts  Model trail mix', number: 62, pr: true, issue: 59, url: 'https://example.test/62',
      actions: ['open', 'merge', 'fix'] },
    { key: 'failed-9', text: 'failed  agent: Model drinks', url: 'https://example.test/runs/9', run: 9,
      title: 'Model drinks', note: 'agent', actions: ['log', 'retry', 'dismiss'] },
    { key: 'stopped-47', text: 'stopped  Mark the trail', number: 47, url: 'https://example.test/47',
      resume: 'cd /x && claude --resume abc', actions: ['open', 'resume'] },
  ],
  merging: [],
  design: [{ key: 'decide-19', text: 'Windows builds', number: 19, url: 'https://example.test/19',
    actions: ['open'] }],
  designTotal: 1,
  queued: [{ key: 'waiting-45', text: 'Model trail food  (waiting on #43)', number: 45, actions: ['open'] }],
  next: [{ key: 'next-64', text: 'Tidy the sound board', number: 64,
    actions: ['open', 'ready', 'to-backlog', 'close'] }],
  blocked: [{ key: 'blocked-50', text: 'Switch the GPS clock  (on hold)', number: 50, url: 'https://example.test/50',
    actions: ['open'] }],
  backlog: [{ key: 'backlog-72', text: 'Rig the night lights', number: 72, url: 'https://example.test/72',
    actions: ['open', 'to-open', 'close'] }],
  ideas: [{ key: 'idea-70', text: 'Study the sprite pipeline', number: 70, url: 'https://example.test/70',
    actions: ['open', 'close'] }],
  merged: [{ key: 'merged-58', text: 'Model tents', number: 58, pr: true, url: 'https://example.test/58', time: 'Oct 23 14:05',
    actions: ['open'] }],
  chores: [],
  error: null,
}

test('a rerun run leaves Needs You at once', () => {
  const failed = BOARD.needs.find(one => one.key === 'failed-9') as Row
  expect(moveToRetried(BOARD, failed).needs.some(one => one.key === 'failed-9')).toBe(false)
})

test('a closed issue\'s card leaves the board at once', () => {
  const idea = BOARD.ideas[0] as Row
  const moved = moveToClosed(BOARD, idea)
  expect(moved.ideas).toEqual([])
  expect(moved.next).toEqual(BOARD.next)
  expect(moveToClosed(BOARD, BOARD.backlog[0] as Row).backlog).toEqual([])
})

test('a closed queued or waiting issue leaves Queued, and the issues left in the start order get their new moves', () => {
  const row = (key: string): Row => ({ key, text: key, number: Number(key.split('-')[1]),
    actions: ['open', 'to-open', 'close'] })
  const pr: Row = { key: 'rerun-pr-81', text: 'Other  (back to its PR)', number: 81, pr: true, actions: ['open'] }
  const ordered = [row('queued-1'), row('waiting-2'), row('queued-3')]
    .map((one, index) => ({ ...one, actions: withOrderMoves(one.actions, index, 3) }))
  const board = { ...BOARD, queued: [...ordered, pr] }
  const keys = (shown: Board) => shown.queued.map(one => [one.key, one.actions])
  expect(keys(moveToClosed(board, ordered[2] as Row))).toEqual([['queued-1', ['open', 'to-open', 'close', 'down']],
    ['waiting-2', ['open', 'to-open', 'close', 'up', 'top']], ['rerun-pr-81', ['open']]])
  expect(keys(moveToClosed(board, ordered[0] as Row))).toEqual([['waiting-2', ['open', 'to-open', 'close', 'down']],
    ['queued-3', ['open', 'to-open', 'close', 'up', 'top']], ['rerun-pr-81', ['open']]])
  // A close of a card outside Queued leaves Queued as it was.
  expect(moveToClosed(board, BOARD.ideas[0] as Row).queued).toBe(board.queued)
})

test('a designed issue moves to the top of Open', () => {
  const design = BOARD.design[0] as Row
  const moved = moveToOpen(BOARD, design)
  expect(moved.design).toEqual([])
  expect(moved.designTotal).toBe(0)
  expect(moved.next[0]).toMatchObject({ key: 'next-19', number: 19, actions: ['open', 'ready', 'to-backlog', 'close'] })
  expect(moved.next.slice(1)).toEqual(BOARD.next)
})

test('an idea moved to Open leaves Ideas for the top of Open, with its labels until the next read', () => {
  const idea: Row = { ...BOARD.ideas[0] as Row, labels: ['idea'] }
  const moved = moveToOpen(BOARD, idea)
  expect(moved.ideas).toEqual([])
  expect(moved.designTotal).toBe(BOARD.designTotal)
  expect(moved.next[0]).toMatchObject({ key: 'next-70', number: 70, labels: ['idea'],
    actions: ['open', 'ready', 'to-backlog', 'close'] })
  expect(moved.next.slice(1)).toEqual(BOARD.next)
})

test('a backlog issue moved to Open leaves Backlog for the top of Open, with its labels until the next read', () => {
  const later: Row = { ...BOARD.backlog[0] as Row, labels: ['backlog'] }
  const moved = moveToOpen(BOARD, later)
  expect(moved.backlog).toEqual([])
  expect(moved.next[0]).toMatchObject({ key: 'next-72', number: 72, labels: ['backlog'],
    actions: ['open', 'ready', 'to-backlog', 'close'] })
  expect(moved.next.slice(1)).toEqual(BOARD.next)
})

test('an Open issue moved to Backlog leaves Open for the top of Backlog, with Move to Open in place of Agent-ready',
  () => {
  const open: Row = { ...BOARD.next[0] as Row, title: 'Tidy the sound board', labels: ['enhancement'] }
  const moved = moveToBacklog(BOARD, open)
  expect(moved.next).toEqual([])
  expect(moved.backlog[0]).toEqual({ key: 'backlog-64', text: 'Tidy the sound board', number: 64, url: undefined,
    title: 'Tidy the sound board', labels: ['enhancement'], actions: ['open', 'to-open', 'close'] })
  expect(moved.backlog.slice(1)).toEqual(BOARD.backlog)
  // Moving it back leaves Backlog as it was.
  expect(moveToOpen(moved, moved.backlog[0] as Row).backlog).toEqual(BOARD.backlog)
})

test('a queued issue moved to Open leaves Queued for the top of Open, and a PR row with its number stays', () => {
  const queued: Row = { key: 'queued-81', text: 'Pack the tents', number: 81, title: 'Pack the tents',
    actions: ['open', 'to-open'] }
  const pr: Row = { key: 'rerun-pr-81', text: 'Other  (back to its PR)', number: 81, pr: true, actions: ['open'] }
  const moved = moveToOpen({ ...BOARD, queued: [queued, pr] }, queued)
  expect(moved.queued).toEqual([pr])
  expect(moved.next[0]).toMatchObject({ key: 'next-81', text: 'Pack the tents', number: 81,
    actions: ['open', 'ready', 'to-backlog', 'close'] })
})

test('a queued or waiting issue moves up, down, or to the top of the start order, and the other rows stay after',
  () => {
  const row = (key: string): Row => ({ key, text: key, number: Number(key.split('-')[1]),
    actions: ['open', 'to-open'] })
  const pr: Row = { key: 'rerun-pr-81', text: 'Other  (back to its PR)', number: 81, pr: true, actions: ['open'] }
  const ordered = [row('queued-1'), row('waiting-2'), row('queued-3')]
    .map((one, index) => ({ ...one, actions: withOrderMoves(one.actions, index, 3) }))
  const board = { ...BOARD, queued: [...ordered, pr] }
  const keys = (shown: Board) => shown.queued.map(one => [one.key, one.actions.slice(2)])
  expect(keys(board)).toEqual([['queued-1', ['down']], ['waiting-2', ['up', 'down', 'top']],
    ['queued-3', ['up', 'top']], ['rerun-pr-81', []]])
  expect(keys(moveInOrder(board, ordered[2] as Row, 'top'))).toEqual([['queued-3', ['down']],
    ['queued-1', ['up', 'down', 'top']], ['waiting-2', ['up', 'top']], ['rerun-pr-81', []]])
  expect(keys(moveInOrder(board, ordered[1] as Row, 'up')).map(([key]) => key))
    .toEqual(['waiting-2', 'queued-1', 'queued-3', 'rerun-pr-81'])
  expect(keys(moveInOrder(board, ordered[1] as Row, 'down')).map(([key]) => key))
    .toEqual(['queued-1', 'queued-3', 'waiting-2', 'rerun-pr-81'])
  // A move past either end, or of a row outside the order, changes nothing.
  expect(keys(moveInOrder(board, ordered[0] as Row, 'up'))).toEqual(keys(board))
  expect(keys(moveInOrder(board, ordered[2] as Row, 'down'))).toEqual(keys(board))
  expect(moveInOrder(board, pr, 'top')).toBe(board)
  expect([inOrder(ordered[0] as Row), inOrder(ordered[1] as Row), inOrder(pr)]).toEqual([true, true, false])
})

test('a queued or waiting issue moves to a place in the start order from 1, and a place past the end puts it last',
  () => {
  const row = (key: string): Row => ({ key, text: key, number: Number(key.split('-')[1]), actions: ['open'] })
  const pr: Row = { key: 'rerun-pr-81', text: 'Other  (back to its PR)', number: 81, pr: true, actions: ['open'] }
  const ordered = [row('queued-1'), row('waiting-2'), row('queued-3'), row('queued-4')]
    .map((one, index) => ({ ...one, actions: withOrderMoves(one.actions, index, 4) }))
  const board = { ...BOARD, queued: [...ordered, pr] }
  const keys = (shown: Board) => shown.queued.map(one => one.key)
  expect(keys(moveInOrder(board, ordered[0] as Row, 3))).toEqual(['waiting-2', 'queued-3', 'queued-1', 'queued-4',
    'rerun-pr-81'])
  expect(keys(moveInOrder(board, ordered[3] as Row, 2))).toEqual(['queued-1', 'queued-4', 'waiting-2', 'queued-3',
    'rerun-pr-81'])
  expect(keys(moveInOrder(board, ordered[1] as Row, 9))).toEqual(['queued-1', 'queued-3', 'queued-4', 'waiting-2',
    'rerun-pr-81'])
  // The moved row takes the moves its new place allows.
  expect(moveInOrder(board, ordered[3] as Row, 1).queued[0]?.actions).toEqual(['open', 'down'])
})

test('an unblocked issue moves from Blocked to the top of Open', () => {
  const blocked: Row = { ...BOARD.blocked[0] as Row, title: 'Switch the GPS clock' }
  const moved = moveToOpen(BOARD, blocked)
  expect(moved.blocked).toEqual([])
  expect(moved.next[0]).toMatchObject({ key: 'next-50', text: 'Switch the GPS clock', number: 50,
    actions: ['open', 'ready', 'to-backlog', 'close'] })
  expect(moved.next.slice(1)).toEqual(BOARD.next)
  expect(moved.design).toEqual(BOARD.design)
})

test('a PR sent back to fix its conflicts moves from Needs You to Queued as fixing conflicts', () => {
  const pr = BOARD.needs.find(one => one.key === 'pr-62') as Row
  const moved = moveToFixing(BOARD, pr)
  expect(moved.needs.some(one => one.key === 'pr-62')).toBe(false)
  expect(moved.queued.at(-1)).toMatchObject({ key: 'rerun-pr-62', text: 'Model trail mix  (fixing conflicts)',
    number: 62, actions: ['open'] })
})

test('a draft sent back to fix its failed checks waits in Queued as back to its PR', () => {
  const draft: Row = { key: 'draft-71', text: 'draft  Serve the drinks  (test failed: Drinks.cs:4: error)',
    number: 71, pr: true, issue: 70, url: 'https://example.test/71', title: 'Serve the drinks', state: 'draft',
    note: 'test failed: Drinks.cs:4: error', actions: ['open', 'fix'] }
  const moved = moveToFixing({ ...BOARD, needs: [draft, ...BOARD.needs] }, draft)
  expect(moved.needs).toEqual(BOARD.needs)
  expect(moved.queued.at(-1)).toMatchObject({ key: 'rerun-pr-71', text: 'Serve the drinks  (back to its PR)',
    number: 71, issue: 70, pr: true })
})

test('a draft sent back to fix its conflicts with main waits in Queued as fixing conflicts', () => {
  const draft: Row = { key: 'draft-75', text: 'draft  Stack the crates  (conflicts with main)', number: 75, pr: true,
    issue: 74, url: 'https://example.test/75', title: 'Stack the crates', state: 'draft', note: 'conflicts with main',
    conflicts: true, actions: ['open', 'fix'] }
  const moved = moveToFixing({ ...BOARD, needs: [draft, ...BOARD.needs] }, draft)
  expect(moved.needs).toEqual(BOARD.needs)
  expect(moved.queued.at(-1)).toMatchObject({ key: 'rerun-pr-75', text: 'Stack the crates  (fixing conflicts)',
    number: 75, issue: 74, pr: true, note: 'fixing conflicts' })
})

test('a draft marked ready shows in its place as a PR to review', () => {
  const draft: Row = { key: 'draft-73', text: 'draft  Pour the drinks', number: 73, pr: true, issue: 72,
    url: 'https://example.test/73', title: 'Pour the drinks', state: 'draft', since: '2026-11-08T09:00:00Z',
    actions: ['open', 'undraft'] }
  const drafted = { ...BOARD, needs: [draft, ...BOARD.needs] }
  const review = { key: 'pr-73', text: 'review  Pour the drinks', number: 73, pr: true, issue: 72,
    url: 'https://example.test/73', title: 'Pour the drinks', state: 'review', since: '2026-11-08T09:00:00Z',
    actions: ['open', 'merge'] }
  expect(moveToReview(drafted, draft).needs).toEqual([review, ...BOARD.needs])
})

test('a PR queued to merge joins the end of the queue', () => {
  const queued: Row = { key: 'merging-70', text: 'Model rope  (checks running)', number: 70, pr: true,
    note: 'checks running', actions: ['open'] }
  const shown = moveToMerging({ ...BOARD, merging: [queued] }, BOARD.needs[0] as Row)
  expect(shown.merging.map(row => row.key)).toEqual(['merging-70', 'merging-61'])
  expect(shown.merging[1]?.text).toBe('Model fresh food  (queued)')
  expect(shown.merged).toEqual(BOARD.merged)
  expect(shown.needs.some(row => row.key === 'pr-61')).toBe(false)
})

test('a continued issue moves to Queued as continuing', () => {
  const stopped: Row = { key: 'stopped-1297', text: 'stopped  Bake the lightmaps', number: 1297,
    title: 'Bake the lightmaps', state: 'stopped', actions: ['open', 'restart', 'close'] }
  const shown: Board = { ...BOARD, needs: [stopped] }
  expect(moveToRestarted(shown, stopped).queued.at(-1)).toEqual({ key: 'restart-1297',
    text: 'Bake the lightmaps  (continuing)', number: 1297, url: undefined, title: 'Bake the lightmaps',
    note: 'continuing', actions: ['open'] })
  expect(moveToRestarted(shown, stopped).needs).toEqual([])
})

test('a stopped merge queue says so as a toast, and its review keeps its state in Merging', () => {
  expect(announce({ key: 'merge-stopped', text: 'merge queue stopped: the main checkout has uncommitted changes to '
    + 'README.md, which #275 changes. Commit, stash, or undo them.', notice: 'warning', actions: [] }))
    .toBe('The merge queue stopped: the main checkout has uncommitted changes to README.md, which #275 changes. '
      + 'Commit, stash, or undo them.')
  const ref = { number: 275, pr: true, url: 'https://x/275' }
  expect(mergingRow('Fix', 'README.md uncommitted in the main checkout', ref, 'stopped').state).toBe('stopped')
  expect('state' in mergingRow('Fix', 'queued', ref)).toBe(false)
})

test('an unpicked issue keeps its place in Queued, reads the note the board kept for it, and shows Start', () => {
  const picked: Row = { key: 'queued-46', text: 'Pave the trail  (picked; waits for a free agent slot)', number: 46,
    title: 'Pave the trail', note: 'picked; waits for a free agent slot', unpicked: 'waits for Start',
    actions: ['open', 'unpick', 'to-open', 'up', 'top'] }
  const shown = { ...BOARD, queued: [...BOARD.queued, picked] }
  const moved = moveToUnpicked(shown, picked)
  expect(moved.queued.map(row => row.key)).toEqual(['waiting-45', 'queued-46'])
  expect(moved.queued[1]).toEqual({ key: 'queued-46', text: 'Pave the trail  (waits for Start)', number: 46,
    title: 'Pave the trail', note: 'waits for Start', pick: 46, actions: ['open', 'start', 'to-open', 'up', 'top'] })
  expect(moved.queued[0]).toBe(BOARD.queued[0])
  // On Hold, an issue nobody picked reads no note.
  const held = moveToUnpicked(shown, { ...picked, note: 'picked; starts on Resume', unpicked: '' })
  expect([held.queued[1]?.text, held.queued[1]?.note]).toEqual(['Pave the trail', undefined])
})
