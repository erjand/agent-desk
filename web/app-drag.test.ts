// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { canDrag, dragClass, dragProps, dropAt, inStartOrder } from './app-drag.js'
import { ui } from './app-state.js'

type Card = { id: string; desk: string; stage: string; key: string; number?: number; actions: string[] }

/** A queued or waiting issue's card in `desk`, at `index` of `count` in the start order, with the moves it shows. */
function issue(desk: string, number: number, index: number, count: number, kind = 'queued'): Card {
  const moves = [...(index > 0 ? ['up'] : []), ...(index < count - 1 ? ['down'] : []), ...(index > 0 ? ['top'] : [])]
  return { id: `${desk}:${number}`, desk, stage: 'queued', key: `${kind}-${number}`, number,
    actions: ['open', 'to-open', ...moves] }
}

const barn = [issue('barn', 1, 0, 4), issue('barn', 2, 1, 4, 'waiting'), issue('barn', 3, 2, 4), issue('barn', 4, 3, 4)]
const rerun: Card = { id: 'barn:7', desk: 'barn', stage: 'queued', key: 'rerun-7', number: 7, actions: ['open'] }
const fix: Card = { id: 'barn:pr-8', desk: 'barn', stage: 'queued', key: 'rerun-pr-8', number: 8, actions: ['open'] }
const chore: Card = { id: 'barn:chore-audit', desk: 'barn', stage: 'queued', key: '', actions: [] }
const loft = [issue('loft', 1, 0, 2), issue('loft', 5, 1, 2)]

afterEach(() => {
  ui.server = null
  ui.toasts = []
})

test('a queued or waiting issue drags when it shows Move Up or Move Down, and no other Queued card does', () => {
  assert.deepEqual(barn.map(canDrag), [true, true, true, true])
  assert.deepEqual([rerun, fix, chore].map(card => [inStartOrder(card), canDrag(card)]),
    [[false, false], [false, false], [false, false]])
  // An issue alone in its project's order has no place to trade, so it shows no moves and doesn't drag.
  assert.equal(canDrag(issue('shed', 1, 0, 1)), false)
  assert.equal(canDrag({ ...barn[0] as Card, stage: 'open' }), false)
})

test('a drop above or below a card takes the place the line marks, counted from 1 with the dragged card left out',
  () => {
  const [one, two, three, four] = barn as [Card, Card, Card, Card]
  // Down the list: #1 onto #3's bottom half lands third, and onto #4's top half lands third too.
  assert.deepEqual(dropAt(barn, one, three, true), { id: 'barn:3', edge: 'below', place: 3 })
  assert.deepEqual(dropAt(barn, one, four, false), { id: 'barn:4', edge: 'above', place: 3 })
  assert.deepEqual(dropAt(barn, one, four, true), { id: 'barn:4', edge: 'below', place: 4 })
  // Up the list: #4 onto #1's top half lands first, and onto #2's bottom half lands third.
  assert.deepEqual(dropAt(barn, four, one, false), { id: 'barn:1', edge: 'above', place: 1 })
  assert.deepEqual(dropAt(barn, four, two, true), { id: 'barn:2', edge: 'below', place: 3 })
  assert.deepEqual(dropAt(barn, three, two, false), { id: 'barn:2', edge: 'above', place: 2 })
  // A drop at the place the card holds, or on the card itself, does nothing.
  assert.equal(dropAt(barn, two, one, true), null)
  assert.equal(dropAt(barn, two, three, false), null)
  assert.equal(dropAt(barn, two, two, true), null)
})

test("a drop on another project's card, or on a Queued card outside the start order, does nothing", () => {
  const [one] = barn as [Card]
  assert.equal(dropAt(barn, one, loft[1] as Card, false), null)
  assert.equal(dropAt(barn, one, loft[0] as Card, true), null)
  for (const other of [rerun, fix, chore]) {
    assert.equal(dropAt([...barn, other], one, other, false), null, other.key)
    assert.equal(dropAt([...barn, other], one, other, true), null, other.key)
  }
})

/** A drag event over a row 40 pixels tall at the top of the page, at `y`, that records what the handlers did to it. */
function dragEvent(y: number) {
  const event = { clientY: y, prevented: false, relatedTarget: null,
    dataTransfer: { effectAllowed: '', dropEffect: '', data: '', setData: (_kind: string, text: string) => {
      event.dataTransfer.data = text
    } },
    currentTarget: { getBoundingClientRect: () => ({ top: 0, height: 40 }), contains: () => false },
    preventDefault: () => {
      event.prevented = true
    } }
  return event
}

test('a drag shows the line only where a drop lands, and the drop posts one place for its issue', async () => {
  const sent: { path: string; body: Record<string, unknown> }[] = []
  const real = globalThis.fetch
  globalThis.fetch = (async (path: string, init: { body: string }) => {
    sent.push({ path, body: JSON.parse(init.body) })
    return { ok: true, json: async () => ({}) }
  }) as unknown as typeof fetch
  ui.server = { desks: [{ id: 'barn', cards: [...barn, rerun, fix] }, { id: 'loft', cards: loft }] }
  const [one, two, three] = barn as [Card, Card, Card]
  try {
    assert.equal(dragProps(rerun).draggable, undefined)
    assert.equal(dragProps(one).draggable, 'true')
    const start = dragEvent(10)
    dragProps(one).onDragStart(start)
    assert.deepEqual([start.dataTransfer.effectAllowed, start.dataTransfer.data], ['move', '#1'])
    assert.equal(dragClass(one), 'dragging')
    // Over another project's card, or a run sent back to its PR, the browser refuses the drop and no line shows.
    for (const other of [loft[1] as Card, rerun, fix]) {
      const over = dragEvent(30)
      dragProps(other).onDragEnter(over)
      dragProps(other).onDragOver(over)
      assert.equal(over.prevented, false, other.key)
      assert.equal(dragClass(other), '', other.key)
    }
    // Entering a row where the drop lands takes the drop and shows the line, as passing over it does.
    const enter = dragEvent(30)
    dragProps(three).onDragEnter(enter)
    assert.deepEqual([enter.prevented, enter.dataTransfer.dropEffect, dragClass(three)], [true, 'move', 'drop-below'])
    const over = dragEvent(30)
    dragProps(three).onDragOver(over)
    assert.deepEqual([over.prevented, over.dataTransfer.dropEffect, dragClass(three)], [true, 'move', 'drop-below'])
    // The line follows the pointer to the top half of #2, where #1 would stay put, and goes.
    dragProps(two).onDragOver(dragEvent(5))
    assert.deepEqual([dragClass(two), dragClass(three)], ['', ''])
    dragProps(three).onDragOver(dragEvent(30))
    dragProps(three).onDrop(dragEvent(30))
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(sent, [{ path: '/api/place', body: { desk: 'barn', key: 'queued-1', to: 3 } }])
    assert.deepEqual([dragClass(one), dragClass(three)], ['', ''])
    // A drag that ends anywhere else sends nothing.
    dragProps(two).onDragStart(dragEvent(10))
    dragProps(fix).onDrop(dragEvent(10))
    dragProps(two).onDragStart(dragEvent(10))
    dragProps(two).onDragEnd()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(sent.length, 1)
    assert.equal(dragClass(two), '')
  } finally {
    globalThis.fetch = real
  }
})
