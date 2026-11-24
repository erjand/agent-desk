// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, mock, test } from 'node:test'

import { GroupSort, SORT_TITLE, sortableDesks, sortGroup, sortTitle } from './app-order.js'
import { ui } from './app-state.js'

type Card = { id: string; desk: string; stage: string; key: string; actions: string[] }
type Element = { type: unknown; props: Record<string, unknown> }

/** A project's desk with cards under `keys`, queued unless a key names its stage as `<stage>:<key>`, and a board. */
function desk(id: string, title: string, keys: string[]) {
  const cards: Card[] = keys.map(named => {
    const [stage, key] = named.includes(':') ? named.split(':') as [string, string] : ['queued', named]
    return { id: `${id}:${key}`, desk: id, stage, key, actions: ['open'] }
  })
  return { id, title, board: { error: null }, cards }
}

const orchard = desk('orchard', 'Orchard', ['queued-4', 'waiting-5', 'rerun-7', 'open:next-8', 'open:next-9',
  'backlog:backlog-10'])
// One issue in the order, and a rerun, which holds no place in it.
const kiln = desk('kiln', 'Kiln', ['queued-1', 'rerun-2'])
const lighthouse = desk('lighthouse', 'Lighthouse', ['queued-1', 'queued-2'])

/** The buttons under a drawn element, as [text, element]. */
function buttons(node: unknown): [string, Element][] {
  if (Array.isArray(node)) {
    return node.flatMap(buttons)
  }
  if (node === null || typeof node !== 'object') {
    return []
  }
  const element = node as Element
  const children = element.props?.children
  const text = [children].flat().filter(one => typeof one === 'string').join('').trim()
  return [...element.type === 'button' ? [[text, element] as [string, Element]] : [], ...buttons(children)]
}

function press(node: unknown, label: string): unknown {
  const found = buttons(node).find(([text]) => text === label)
  assert.ok(found, `no ${label} button`)
  return (found[1].props.onClick as () => unknown)()
}

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
  ui.sortConfirm = null
  ui.toasts = []
  mock.restoreAll()
  mock.timers.reset()
})

test('Queued\'s Sort shows while a shown project has two or more queued or waiting issues, and hides with fewer',
  () => {
  ui.server = { desks: [kiln] } as never
  assert.equal(GroupSort(), null)
  ui.server = { desks: [orchard, kiln] } as never
  const shown = buttons(GroupSort())
  assert.deepEqual(shown.map(([text]) => text), ['Sort'])
  assert.equal(shown[0]?.[1].props.title, SORT_TITLE)
  assert.deepEqual(sortableDesks().map(one => one.id), ['orchard'])
  // A project's tab shows only its own issues.
  ui.tab = 'kiln'
  assert.equal(GroupSort(), null)
  // A project whose board failed to read shows no cards to sort.
  ui.tab = 'all'
  ui.server = { desks: [{ ...orchard, board: { error: 'no store' } }] } as never
  assert.equal(GroupSort(), null)
})

test('Sort asks to confirm, Cancel sorts nothing, and Sort then sorts each shown project with an order to sort',
  async () => {
  ui.server = { desks: [orchard, kiln, lighthouse] } as never
  const posts: [string, unknown][] = []
  mock.method(globalThis, 'fetch', async (path: string, init: { body: string }) => {
    posts.push([path, JSON.parse(init.body)])
    return new Response('{}', { status: 202 })
  })
  press(GroupSort(), 'Sort')
  assert.equal(ui.sortConfirm, 'queued')
  assert.deepEqual(buttons(GroupSort()).map(([text]) => text), ['Sort', 'Cancel'])
  press(GroupSort(), 'Cancel')
  assert.equal(ui.sortConfirm, null)
  assert.deepEqual(posts, [])
  press(GroupSort(), 'Sort')
  await press(GroupSort(), 'Sort')
  assert.equal(ui.sortConfirm, null)
  assert.deepEqual(posts, [['/api/sort-order', { desk: 'orchard' }], ['/api/sort-order', { desk: 'lighthouse' }]])
})

test('a sort the server refuses toasts why after its project\'s name', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  ui.server = { desks: [orchard] } as never
  const refused = JSON.stringify({ error: 'that order has changed; refresh' })
  mock.method(globalThis, 'fetch', async () => new Response(refused, { status: 409 }))
  await sortGroup()
  assert.deepEqual(ui.toasts.map(one => one.text), ['Orchard: that order has changed; refresh.'])
})

/** The text under a drawn element, each piece apart by a space. */
function text(node: unknown): string {
  if (Array.isArray(node)) {
    return node.map(text).join(' ')
  }
  if (typeof node === 'string') {
    return node
  }
  return node !== null && typeof node === 'object' ? text([(node as Element).props?.children].flat()) : ''
}

test('Open\'s Sort shows while a shown project has two or more of its issues in the list order, and the other groups\' '
  + 'by their own cards', () => {
  ui.server = { desks: [orchard, kiln, desk('mill', 'Mill', ['blocked:stack-wait-3', 'blocked:blocked-4'])] } as never
  const shown = buttons(GroupSort({ stage: 'open' }))
  assert.deepEqual(shown.map(([label]) => label), ['Sort'])
  assert.equal(shown[0]?.[1].props.title, sortTitle('open'))
  assert.match(sortTitle('open'), /^Sort Open by number, highest first/)
  assert.deepEqual(sortableDesks('open').map(one => one.id), ['orchard'])
  // One Backlog issue has no order to sort, and a stack wait holds no place in Blocked's.
  assert.equal(GroupSort({ stage: 'backlog' }), null)
  assert.equal(GroupSort({ stage: 'blocked' }), null)
  // Groups with no hand-set order have no Sort.
  assert.equal(GroupSort({ stage: 'needs' }), null)
  assert.equal(GroupSort({ stage: 'working' }), null)
})

test('a group\'s Sort asks in its own heading, and only that group\'s confirm shows, and its Sort resets each shown '
  + 'project\'s list order in the group', async () => {
  const mill = desk('mill', 'Mill', ['open:next-1', 'open:next-2'])
  ui.server = { desks: [orchard, kiln, mill] } as never
  const posts: [string, unknown][] = []
  mock.method(globalThis, 'fetch', async (path: string, init: { body: string }) => {
    posts.push([path, JSON.parse(init.body)])
    return new Response('{}', { status: 202 })
  })
  press(GroupSort({ stage: 'open' }), 'Sort')
  assert.equal(ui.sortConfirm, 'open')
  assert.equal(text(GroupSort({ stage: 'open' })).replace(/\s+/g, ' ').trim(), 'Sort Open by number? Sort Cancel')
  // Queued's heading keeps its own Sort button while Open's asks.
  assert.deepEqual(buttons(GroupSort()).map(([label]) => label), ['Sort'])
  press(GroupSort({ stage: 'open' }), 'Cancel')
  assert.equal(ui.sortConfirm, null)
  assert.deepEqual(posts, [])
  press(GroupSort({ stage: 'open' }), 'Sort')
  await press(GroupSort({ stage: 'open' }), 'Sort')
  assert.equal(ui.sortConfirm, null)
  assert.deepEqual(posts, [['/api/sort-list', { desk: 'orchard', group: 'open' }],
    ['/api/sort-list', { desk: 'mill', group: 'open' }]])
})

test('a list sort the server refuses toasts why after its project\'s name', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  ui.server = { desks: [orchard] } as never
  const refused = JSON.stringify({ error: 'that order has changed; refresh' })
  mock.method(globalThis, 'fetch', async () => new Response(refused, { status: 409 }))
  await sortGroup('open')
  assert.deepEqual(ui.toasts.map(one => one.text), ['Orchard: that order has changed; refresh.'])
})
