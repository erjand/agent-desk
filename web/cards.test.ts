// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { Board } from '../inbox/types'
import { cardsOf } from './cards.ts'

const EMPTY: Board = { at: '09:15', runners: [], needs: [], merging: [], design: [], designTotal: 0, queued: [], next: [],
  blocked: [], backlog: [], ideas: [], merged: [], chores: [], error: null }

test('an issue and its PR make one card, which takes the stage that asks the most of the person', () => {
  const cards = cardsOf('game', { ...EMPTY,
    needs: [{ key: 'pr-40', text: 'review  Fix', title: 'Fix the bridge', state: 'review', number: 40, pr: true,
      issue: 31, url: 'https://x/pull/40', since: '2026-10-28T10:00:00Z', actions: ['open', 'merge'] }],
    runners: [{ key: 'runner-test-1', text: 'test-1    Fix', runner: 'test-1', state: 'working', title: 'Fix',
      number: 40, pr: true, issue: 31, actions: ['open'] }],
    merged: [{ key: 'merged-38', text: 'Old', title: 'Old', number: 38, pr: true, issue: 30,
      since: '2026-10-27T10:00:00Z', time: 'Oct 27 10:00', actions: ['open'] }] })
  assert.deepEqual(cards.map(card => [card.id, card.stage, card.number, card.pr, card.state]), [
    ['game:31', 'needs', 31, 40, 'review'],
    ['game:30', 'merged', 30, 38, undefined],
  ])
  assert.equal(cards[0]?.key, 'pr-40')
  assert.deepEqual(cards[0]?.actions, ['open', 'merge'])
})

test('a backlog issue makes a card in Backlog, after Open and before Ideas', () => {
  const row = (key: string, number: number) => ({ key, text: `Issue ${number}`, number, actions: ['open'] as const })
  const cards = cardsOf('game', { ...EMPTY, next: [row('next-3', 3)], backlog: [row('backlog-2', 2)],
    ideas: [row('idea-1', 1)] })
  assert.deepEqual(cards.map(card => [card.id, card.stage]), [['game:3', 'open'], ['game:2', 'backlog'],
    ['game:1', 'idea']])
})

test('a chore request that waits for Start makes a Queued card of its own, which keeps its request', () => {
  const cards = cardsOf('game', { ...EMPTY, queued: [{ key: 'request-790', text: 'chore: audit  (waits for Start)',
    title: 'chore: audit', note: 'waits for Start', request: 790, starts: [790], actions: ['start', 'cancel'] }] })
  assert.deepEqual(cards.map(card => [card.id, card.stage, card.number, card.request, card.actions]), [
    ['game:run-request-790', 'queued', undefined, 790, ['start', 'cancel']]])
})

test('a PR in the merge queue stays in Merging while a runner tests it', () => {
  const cards = cardsOf('game', { ...EMPTY,
    merging: [{ key: 'merging-40', text: 'Fix the bridge  (checks running)', title: 'Fix the bridge', number: 40,
      pr: true, issue: 31, note: 'checks running', actions: ['open'] }],
    runners: [{ key: 'runner-test-1', text: 'test-1    Fix', runner: 'test-1', state: 'working', title: 'Fix',
      number: 40, pr: true, issue: 31, actions: ['open'] }] })
  assert.deepEqual(cards.map(card => [card.id, card.stage, card.note]), [['game:31', 'merging', 'checks running']])
})

test('a review sent back to its agent keeps its PR as its card moves from Needs You to Queued and Working', () => {
  const review = { key: 'pr-640', text: 'review  Fix', title: 'Fix the bridge', state: 'review', number: 640,
    pr: true, issue: 588, url: 'https://x/640', actions: ['open', 'merge'] as const }
  const queued = { key: 'rerun-588', text: 'Fix the bridge  (back to its PR)', title: 'Fix the bridge', number: 640,
    pr: true, issue: 588, url: 'https://x/640', note: 'back to its PR', request: 7, actions: ['open', 'cancel'] as const }
  const working = { key: 'unit-7', text: 'agent     Fix the bridge', runner: 'agent', state: 'working',
    title: 'Fix the bridge', number: 640, pr: true, issue: 588, url: 'https://x/640', actions: ['open'] as const }
  const stages = [{ ...EMPTY, needs: [{ ...review, actions: [...review.actions] }] },
    { ...EMPTY, queued: [{ ...queued, actions: [...queued.actions] }] },
    { ...EMPTY, runners: [{ ...working, actions: [...working.actions] }] }]
  assert.deepEqual(stages.map(board => cardsOf('game', board).map(card => [card.id, card.stage, card.number, card.pr])),
    [[['game:588', 'needs', 588, 640]], [['game:588', 'queued', 588, 640]], [['game:588', 'working', 588, 640]]])
  // Once the review closed, the issue's rows name the issue alone, and so does its card.
  const closed = { ...EMPTY, runners: [{ ...working, number: 588, pr: undefined, issue: undefined,
    actions: [...working.actions] }] }
  assert.deepEqual(cardsOf('game', closed).map(card => [card.id, card.number, card.pr]), [['game:588', 588, undefined]])
})

test('a notice row, such as main ahead of origin, makes no card', () => {
  const cards = cardsOf('game', { ...EMPTY,
    merging: [{ key: 'merging-40', text: 'Fix the bridge', title: 'Fix the bridge', number: 40, pr: true,
      actions: ['open'] },
    { key: 'origin', text: 'main is 2 commits ahead of origin', notice: 'muted', actions: [] }] })
  assert.deepEqual(cards.map(card => card.id), ['game:pr-40'])
})

test('Needs You waits longest first, and a working card names its runner and keeps a PR another section knows', () => {
  const cards = cardsOf('game', { ...EMPTY,
    needs: [
      { key: 'failed-9', text: 'failed  agent: X', title: 'X', state: 'failed', run: 9,
        actions: ['log', 'retry', 'dismiss'] },
      { key: 'stopped-5', text: 'stopped  Y', title: 'Y', state: 'stopped', number: 5, since: '2026-10-28T09:00:00Z',
        actions: ['open'] },
      { key: 'pr-8', text: 'review  Z', title: 'Z', state: 'review', number: 8, pr: true, since: '2026-10-27T09:00:00Z',
        actions: ['open', 'merge'] },
    ],
    runners: [
      { key: 'runner-agent-1', text: 'agent-1   W', runner: 'agent-1', state: 'working', title: 'W', number: 7,
        note: 'serial', time: '4m', actions: ['open'] },
      { key: 'runner-agent-2', text: 'agent-2   idle', runner: 'agent-2', state: 'idle', actions: [] },
      { key: 'runner-agent-3', text: 'agent-3   other: V', runner: 'agent-3', state: 'elsewhere', title: 'V',
        number: 3, dim: true, project: 'other', actions: ['open'] },
    ],
    queued: [{ key: 'rerun-pr-12', text: 'W  (back to its PR)', title: 'W', note: 'back to its PR', number: 12,
      pr: true, issue: 7, actions: ['open'] }] })
  assert.deepEqual(cards.map(card => [card.id, card.stage]), [
    ['game:pr-8', 'needs'], ['game:5', 'needs'], ['game:run-9', 'needs'], ['game:7', 'working'],
  ])
  assert.deepEqual([cards[3]?.runner, cards[3]?.state, cards[3]?.pr, cards[3]?.note, cards[3]?.time],
    ['agent-1', undefined, 12, 'serial', '4m'])
})

test('a run that failed after its agent opened a PR keeps a card apart from the PR\'s', () => {
  const cards = cardsOf('game', { ...EMPTY, needs: [
    { key: 'failed-77', text: 'failed  agent: Bend', title: 'Bend', state: 'failed', number: 321, run: 77,
      since: '2026-10-28T15:58:00Z', actions: ['log', 'retry', 'dismiss'] },
    { key: 'pr-353', text: 'review  Bend', title: 'Bend', state: 'review', number: 353, pr: true, issue: 321,
      since: '2026-10-28T16:14:00Z', actions: ['open', 'merge'] },
  ] })
  assert.deepEqual(cards.map(card => [card.id, card.state, card.actions]), [
    ['game:run-77', 'failed', ['log', 'retry', 'dismiss']],
    ['game:321', 'review', ['open', 'merge']],
  ])
})

test('a stopped issue keeps its issue\'s card with Continue, apart from the failed run Continue reruns', () => {
  const cards = cardsOf('game', { ...EMPTY, needs: [
    { key: 'failed-32', text: 'failed  agent: Bake', title: 'Bake', state: 'failed', number: 1297, run: 32,
      since: '2026-11-08T10:00:00Z', actions: ['log', 'retry', 'dismiss'] },
    { key: 'failed-33', text: 'timed out  agent: Light', title: 'Light', state: 'timed out', number: 1298, run: 33,
      continues: true, since: '2026-11-08T10:30:00Z', actions: ['log', 'retry', 'dismiss'] },
    { key: 'stopped-1297', text: 'stopped  Bake', title: 'Bake', state: 'stopped', number: 1297, rerun: 32,
      since: '2026-11-08T11:00:00Z', actions: ['open', 'restart', 'close'] },
    { key: 'stopped-1391', text: 'stopped  Pack', title: 'Pack', state: 'stopped', number: 1391,
      resume: 'cd /w && claude --resume abc', since: '2026-11-08T11:30:00Z',
      actions: ['open', 'resume', 'restart', 'close'] },
  ] })
  assert.deepEqual(cards.map(card => [card.id, card.state, card.continues, card.actions]), [
    ['game:run-32', 'failed', undefined, ['log', 'retry', 'dismiss']],
    ['game:run-33', 'timed out', true, ['log', 'retry', 'dismiss']],
    ['game:1297', 'stopped', undefined, ['open', 'restart', 'close']],
    ['game:1391', 'stopped', undefined, ['open', 'resume', 'restart', 'close']],
  ])
})

test("a review the merge queue stopped on keeps its card and state, and the stop's notice in Needs You makes none", () => {
  const cards = cardsOf('game', { ...EMPTY,
    needs: [{ key: 'merge-stopped', text: 'merge queue stopped: the main checkout has uncommitted changes to README.md',
      notice: 'warning', actions: [] }],
    merging: [{ key: 'merging-275', text: 'Fix  (README.md uncommitted in the main checkout)', title: 'Fix',
      number: 275, pr: true, issue: 264, state: 'stopped', note: 'README.md uncommitted in the main checkout',
      actions: ['open'] }] })
  assert.deepEqual(cards.map(card => [card.id, card.stage, card.state, card.note]),
    [['game:264', 'merging', 'stopped', 'README.md uncommitted in the main checkout']])
})
