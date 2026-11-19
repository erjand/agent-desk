import type { TestBody } from 'claude-code/testing'

import type { Board } from '../types'

// The board, pane, and stored state the pane's tests in inbox.test.tsx and buttons.test.tsx start from.

export const BOARD: Board = {
  at: '09:15',
  runners: [
    { key: 'runner-agent-1', text: 'agent-1   Model drinks', number: 43, time: '12m', url: 'https://example.test/job/7',
      actions: ['open'] },
    { key: 'runner-agent-2', text: 'agent-2   idle', actions: [] },
    { key: 'runner-tests', text: 'tests     idle', actions: [] },
  ],
  needs: [
    { key: 'pr-61', text: 'review  Model fresh food', number: 61, pr: true, issue: 60, url: 'https://example.test/61',
      actions: ['open', 'merge'] },
    { key: 'pr-62', text: 'conflicts  Model trail mix', number: 62, pr: true, issue: 59, url: 'https://example.test/62',
      actions: ['open', 'merge', 'fix'] },
    { key: 'failed-9', text: 'failed  agent: Model drinks', url: 'https://example.test/43', run: -9,
      log: 'journalctl --user -u agent-desk-agent-me-trail-43-9.service', title: 'Model drinks', note: 'agent',
      actions: ['open', 'log', 'retry', 'dismiss'] },
    { key: 'stopped-47', text: 'stopped  Mark the trail', number: 47, url: 'https://example.test/47',
      resume: 'cd /x && claude --resume abc', actions: ['open', 'resume'] },
  ],
  merging: [],
  design: [{ key: 'decide-19', text: 'Windows builds', number: 19, url: 'https://example.test/19',
    actions: ['open'] }],
  designTotal: 1,
  queued: [{ key: 'waiting-45', text: 'Model trail food  (waiting on #43)', number: 45, actions: ['open'] }],
  next: [{ key: 'next-64', text: 'Tidy the sound board', number: 64, url: 'https://example.test/64',
    actions: ['open', 'ready', 'to-backlog', 'close'] }],
  blocked: [{ key: 'blocked-50', text: 'Switch the GPS clock  (on hold)', number: 50, url: 'https://example.test/50',
    actions: ['open'] }],
  backlog: [{ key: 'backlog-72', text: 'Rig the night lights', number: 72, url: 'https://example.test/72',
    actions: ['open', 'to-open', 'close'] }],
  ideas: [{ key: 'idea-70', text: 'Study the sprite pipeline', number: 70, url: 'https://example.test/70',
    actions: ['open', 'close'] }],
  merged: [{ key: 'merged-58', text: 'Model tents', number: 58, pr: true, url: 'https://example.test/58', time: 'Oct 23 14:05',
    actions: ['open'] }],
  chores: [
    'Chore          Every  Last run                     Next',
    'audit          3d     done Fri Oct 23 07:33        Mon Oct 26 07:33',
    'untested-one   3d     never                        now, once agent runners are free',
  ],
  error: null,
}

export const PANE = {
  plugin: 'agent-desk',
  component: 'Pane',
  requestId: 'agent-desk',
  props: {
    title: 'agent-desk',
    isFocused: false,
    bodyColumns: 100,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

// Holds the board as a refresh stored it, with its version, and any other keys `held` names as they stand when read;
// it leaves the rest to the kit's own state. Returns a function that puts the stored board back.
export function seed(on: Parameters<TestBody>[1], shown: Board = BOARD, held: Record<string, unknown> = {}) {
  let stored = { value: { shape: 'board-12', value: shown } as unknown, version: 1 }
  on('state.get', (_, e, next) => (e.key === 'board' ? { value: stored }
    : e.key in held ? { value: { value: held[e.key], version: 1 } } : next(e)))
  on('state.set', (_, e, next) => {
    if (e.key !== 'board') {
      return next(e)
    }
    if (e.ifVersion !== undefined && e.ifVersion !== stored.version) {
      return { value: { isSet: false, version: stored.version } }
    }
    stored = { value: e.value, version: stored.version + 1 }
    return { value: { isSet: true, version: stored.version } }
  })
  return () => {
    stored = { value: { shape: 'board-12', value: shown }, version: stored.version + 1 }
  }
}
