// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { canDrag, dragClass, dragProps, dropAt, dropPost, EDGE_PX, edgeStep, inListOrder, inStartOrder }
  from './app-drag.js'
import { ui } from './app-state.js'

type Card = { id: string; desk: string; stage: string; key: string; number?: number; actions: string[] }

/** A queued or waiting issue's card in `desk`, at `index` of `count` in the start order, with the moves it shows. */
function issue(desk: string, number: number, index: number, count: number, kind = 'queued'): Card {
  const moves = [...(index > 0 ? ['up'] : []), ...(index < count - 1 ? ['down'] : []), ...(index > 0 ? ['top'] : []),
    ...(index < count - 1 ? ['bottom'] : [])]
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

/** An issue's card in `desk`'s `stage` group under the key `<prefix><number>`, at `index` of `count` in it. */
function listed(desk: string, stage: string, prefix: string, number: number, index: number, count: number): Card {
  return { ...issue(desk, number, index, count), stage, key: `${prefix}${number}` }
}

const open = [listed('barn', 'open', 'next-', 31, 0, 3), listed('barn', 'open', 'next-', 32, 1, 3),
  listed('barn', 'open', 'next-', 30, 2, 3)]
const shelved = [listed('barn', 'backlog', 'backlog-', 40, 0, 2), listed('barn', 'backlog', 'backlog-', 41, 1, 2)]
const loftOpen = [listed('loft', 'open', 'next-', 50, 0, 2), listed('loft', 'open', 'next-', 51, 1, 2)]

test('an issue in Open, Blocked, Needs Design, Backlog, or Ideas drags when it shows Move Up or Move Down', () => {
  const cards = [['open', 'next-'], ['blocked', 'blocked-'], ['design', 'decide-'], ['backlog', 'backlog-'],
    ['idea', 'idea-']].map(([stage, prefix]) => listed('barn', stage as string, prefix as string, 9, 0, 2))
  assert.deepEqual(cards.map(card => [inListOrder(card), canDrag(card)]), cards.map(() => [true, true]))
  // A stack wait in Blocked holds no place in the list order, an issue alone in its group has no moves, and a key
  // from another group's rows holds no place in this one.
  assert.equal(canDrag(listed('barn', 'blocked', 'stack-wait-', 9, 0, 2)), false)
  assert.equal(canDrag(listed('barn', 'idea', 'idea-', 9, 0, 1)), false)
  assert.equal(canDrag(listed('barn', 'open', 'backlog-', 9, 0, 2)), false)
  assert.equal(inListOrder(barn[0] as Card), false)
})

test('a drop in a list group lands among its project\'s cards in that group, and none lands on another group or project',
  () => {
  const [first, second, third] = open as [Card, Card, Card]
  const ordered = [...open]
  assert.deepEqual(dropAt(ordered, third, first, false), { id: 'barn:31', edge: 'above', place: 1 })
  assert.deepEqual(dropAt(ordered, first, third, true), { id: 'barn:30', edge: 'below', place: 3 })
  assert.deepEqual(dropAt(ordered, first, second, true), { id: 'barn:32', edge: 'below', place: 2 })
  assert.equal(dropAt(ordered, first, second, false), null)
  assert.equal(dropAt(ordered, second, first, true), null)
  for (const other of [...shelved, ...loftOpen, barn[0] as Card]) {
    assert.equal(dropAt([...ordered, other], first, other, false), null, other.id)
    assert.equal(dropAt([...ordered, other], first, other, true), null, other.id)
  }
})

test('a drop posts its place from 1 in Queued, and the issue it lands above or below in a list group', () => {
  assert.deepEqual(dropPost(barn[0], { id: 'barn:3', edge: 'below', place: 3, number: 3 }),
    ['/api/place', { desk: 'barn', key: 'queued-1', to: 3 }])
  assert.deepEqual(dropPost(open[2], { id: 'barn:31', edge: 'above', place: 1, number: 31 }),
    ['/api/list-place', { desk: 'barn', key: 'next-30', before: 31 }])
  assert.deepEqual(dropPost(open[0], { id: 'barn:30', edge: 'below', place: 3, number: 30 }),
    ['/api/list-place', { desk: 'barn', key: 'next-31', after: 30 }])
})

test('the list scrolls near its top or bottom edge, faster the nearer, and not to either side of it', () => {
  const box = { top: 100, bottom: 500, left: 0, right: 300 }
  assert.equal(edgeStep(box, 150, 300), 0)
  assert.equal(edgeStep(box, 150, 100 + EDGE_PX), 0)
  assert.equal(edgeStep(box, 150, 500 - EDGE_PX), 0)
  assert.ok(edgeStep(box, 150, 120) < 0)
  assert.ok(edgeStep(box, 150, 480) > 0)
  assert.ok(edgeStep(box, 150, 495) > edgeStep(box, 150, 480))
  // Above or below the list, it scrolls at its fastest, as at the edge itself.
  assert.equal(edgeStep(box, 150, 40), edgeStep(box, 150, 100))
  assert.equal(edgeStep(box, 150, 560), edgeStep(box, 150, 500))
  assert.equal(edgeStep(box, 400, 495), 0)
})

type Listener = { type: string; fn: (event: unknown) => void; once: boolean }

/**
 * A page for the drag: a list 200 pixels tall and 300 wide at the top left of the window, holding `cards` as rows 40
 * pixels tall, whose scrollTop the browser keeps in range and whose scroll reaches the document's listeners. The
 * document finds the row at a point, and `frame()` runs the animation frames the drag asked for.
 */
function page(cards: Card[]) {
  const listeners: Listener[] = []
  const frames = new Map<number, () => void>()
  let next = 1
  let top = 0
  const classes = new Set<string>()
  const fire = (type: string, event: Record<string, unknown>) => {
    for (const listener of listeners.filter(one => one.type === type)) {
      if (listener.once) {
        listeners.splice(listeners.indexOf(listener), 1)
      }
      listener.fn(event)
    }
    return event
  }
  const list = {
    getBoundingClientRect: () => ({ top: 0, bottom: 200, left: 0, right: 300 }),
    get scrollTop() {
      return top
    },
    set scrollTop(value: number) {
      top = Math.max(0, Math.min(cards.length * 40 - 200, value))
      fire('scroll', { target: list })
    },
  }
  const rows = cards.map((card, index) => {
    const row = {
      dataset: { key: card.id },
      closest: (selector: string) => (selector === '.list' ? list : row),
      getBoundingClientRect: () => ({ top: index * 40 - top, height: 40 }),
      setPointerCapture: () => {},
    }
    return row
  })
  const document = {
    documentElement: { classList: { add: (name: string) => classes.add(name),
      remove: (name: string) => classes.delete(name) } },
    getSelection: () => ({ removeAllRanges: () => {} }),
    elementFromPoint: (x: number, y: number) => (x < 0 || x > 300 || y < 0 || y > 200
      ? null : rows[Math.floor((y + top) / 40)] ?? null),
    addEventListener: (type: string, fn: (event: unknown) => void, options?: boolean | { once?: boolean }) => {
      listeners.push({ type, fn, once: typeof options === 'object' && options.once === true })
    },
    removeEventListener: (type: string, fn: (event: unknown) => void) => {
      const at = listeners.findIndex(one => one.type === type && one.fn === fn)
      if (at >= 0) {
        listeners.splice(at, 1)
      }
    },
  }
  Object.assign(globalThis, {
    document,
    requestAnimationFrame: (fn: () => void) => {
      frames.set(next, fn)
      return next++
    },
    cancelAnimationFrame: (id: number) => frames.delete(id),
  })
  const rowOf = (card: Card) => rows[cards.indexOf(card)]
  return {
    list,
    classes,
    listening: () => listeners.length,
    /** Presses the pointer on `card`'s row at (`x`, `y`) with `button`, 0 for the primary one. */
    down: (card: Card, x: number, y: number, button = 0) => dragProps(card).onPointerDown({ button, pointerId: 1,
      clientX: x, clientY: y, currentTarget: rowOf(card) }),
    move: (x: number, y: number) => fire('pointermove', { pointerId: 1, clientX: x, clientY: y }),
    up: (x: number, y: number) => fire('pointerup', { pointerId: 1, clientX: x, clientY: y }),
    /** Presses `key` and says whether the drag took it from the page's own keys. */
    key: (key: string) => {
      const event = { key, stopped: false, preventDefault: () => {}, stopPropagation: () => {
        event.stopped = true
      } }
      fire('keydown', event)
      return event.stopped
    },
    /** Clicks `card`'s row after a release and says whether the click reached the row's own handler. */
    click: (card: Card) => {
      let reached = true
      dragProps(card).onClickCapture({ stopImmediatePropagation: () => {
        reached = false
      }, preventDefault: () => {} })
      return reached
    },
    frame: () => {
      const due = [...frames.values()]
      frames.clear()
      due.forEach(fn => fn())
    },
    frames: () => frames.size,
  }
}

/** Waits out the task that follows a release, when a click that came after it no longer belongs to a drag. */
const settle = () => new Promise(resolve => setTimeout(resolve, 1))

test('a pointer drag shows the line on the card under it, follows a wheel or edge scroll, and drops one place',
  async () => {
  const sent: { path: string; body: Record<string, unknown> }[] = []
  const real = { fetch: globalThis.fetch, document: (globalThis as Record<string, unknown>).document }
  globalThis.fetch = (async (path: string, init: { body: string }) => {
    sent.push({ path, body: JSON.parse(init.body) })
    return { ok: true, json: async () => ({}) }
  }) as unknown as typeof fetch
  ui.server = { desks: [{ id: 'barn', cards: [...barn, rerun, fix] }, { id: 'loft', cards: loft }] }
  const [one, two, three, four] = barn as [Card, Card, Card, Card]
  // The list shows loft's two cards, the rerun, and #1 and #2 of barn; #3, #4, and the fix start off screen below.
  const view = page([loft[0] as Card, loft[1] as Card, rerun, one, two, three, four, fix])
  try {
    assert.equal(dragProps(rerun)['data-drags'], undefined)
    assert.equal(dragProps(one)['data-drags'], 'true')
    // A press that moves less than a few pixels stays a click, which selects the card, and sends nothing.
    view.down(one, 50, 130)
    view.move(52, 131)
    assert.equal(dragClass(one), '')
    view.up(52, 131)
    assert.equal(view.click(one), true)
    assert.equal(view.listening(), 0)
    // A press with another button, or on a card that doesn't drag, starts nothing.
    view.down(one, 50, 130, 2)
    view.down(rerun, 50, 100)
    assert.equal(view.listening(), 0)

    view.down(one, 50, 130)
    // Over another project's card, or a run sent back to its PR, no line shows.
    for (const y of [60, 100]) {
      view.move(50, y)
      assert.deepEqual([dragClass(one), [loft[1], rerun].map(card => dragClass(card as Card))],
        ['dragging', ['', '']], String(y))
      assert.ok(view.classes.has('card-drag'))
    }
    // Over the dragged card itself no line shows either, and the wheel scrolls the list under the still pointer until
    // #3, which started off screen, sits under it, where the line follows.
    view.move(50, 145)
    assert.equal(dragClass(three), '')
    view.list.scrollTop = 80
    assert.deepEqual([dragClass(one), dragClass(three)], ['dragging', 'drop-below'])
    // Near the bottom edge, the list scrolls a step each frame, and the line moves from #4's top half to its bottom
    // half as the list moves under the still pointer.
    view.move(50, 170)
    assert.equal(view.list.scrollTop, 86)
    assert.equal(dragClass(four), 'drop-above')
    view.frame()
    assert.equal(view.list.scrollTop, 92)
    assert.equal(dragClass(four), 'drop-below')
    // Away from the edge, the scroll stops, and the line settles on #2's bottom half, where #1 lands second.
    view.move(50, 100)
    view.frame()
    assert.equal(view.frames(), 0)
    assert.equal(view.list.scrollTop, 92)
    assert.deepEqual([dragClass(two), dragClass(four)], ['drop-below', ''])
    view.up(50, 100)
    // The click that follows the drop selects nothing, and the next click selects again.
    assert.equal(view.click(one), false)
    await settle()
    assert.equal(view.click(one), true)
    assert.deepEqual(sent, [{ path: '/api/place', body: { desk: 'barn', key: 'queued-1', to: 2 } }])
    assert.deepEqual([dragClass(one), dragClass(two)], ['', ''])
    assert.equal(view.classes.has('card-drag'), false)
    assert.equal(view.listening(), 0)
  } finally {
    globalThis.fetch = real.fetch
    Object.assign(globalThis, { document: real.document })
  }
})

test('Escape cancels a drag, a release outside any row or at its own place drops nothing, and a lost release gives way',
  async () => {
  const sent: string[] = []
  const real = { fetch: globalThis.fetch, document: (globalThis as Record<string, unknown>).document }
  globalThis.fetch = (async (path: string) => {
    sent.push(path)
    return { ok: true, json: async () => ({}) }
  }) as unknown as typeof fetch
  ui.server = { desks: [{ id: 'barn', cards: barn }] }
  const [one, two, three, four] = barn as [Card, Card, Card, Card]
  const view = page([one, two, three, four])
  try {
    // Escape mid-drag hides the line, keeps the page's own Escape from closing the panel, and the release and click
    // that follow drop and select nothing. Escape with no drag under way leaves the key to the page.
    view.down(two, 50, 60)
    view.move(50, 140)
    assert.equal(dragClass(four), 'drop-above')
    assert.equal(view.key('Escape'), true)
    assert.deepEqual([dragClass(two), dragClass(four)], ['', ''])
    assert.equal(view.key('Escape'), false)
    view.up(50, 140)
    assert.equal(view.click(two), false)
    await settle()
    assert.equal(view.click(two), true)
    // A release outside the list, or on the top half of the card below, where #2 already sits, sends nothing.
    view.down(two, 50, 60)
    view.move(50, 140)
    view.move(400, 140)
    assert.equal(dragClass(four), '')
    view.up(400, 140)
    await settle()
    // Near the bottom edge of a list too short to scroll, the edge asks for no more frames.
    view.down(two, 50, 60)
    view.move(50, 190)
    assert.deepEqual([view.list.scrollTop, view.frames()], [0, 0])
    // A press whose release the page never saw gives way to the next one.
    const listening = view.listening()
    view.down(three, 50, 100)
    assert.equal(view.listening(), listening)
    view.move(50, 20)
    assert.deepEqual([dragClass(two), dragClass(three), dragClass(one)], ['', 'dragging', 'drop-above'])
    view.up(50, 20)
    await settle()
    view.down(two, 50, 60)
    view.move(50, 85)
    assert.equal(dragClass(three), '')
    view.up(50, 85)
    await settle()
    assert.deepEqual(sent, ['/api/place'])
    assert.equal(view.listening(), 0)
  } finally {
    globalThis.fetch = real.fetch
    Object.assign(globalThis, { document: real.document })
  }
})

test('a pointer drag in Open shows no line over Backlog\'s or another project\'s cards, and drops beside an Open card',
  async () => {
  const sent: { path: string; body: Record<string, unknown> }[] = []
  const real = { fetch: globalThis.fetch, document: (globalThis as Record<string, unknown>).document }
  globalThis.fetch = (async (path: string, init: { body: string }) => {
    sent.push({ path, body: JSON.parse(init.body) })
    return { ok: true, json: async () => ({}) }
  }) as unknown as typeof fetch
  ui.server = { desks: [{ id: 'barn', cards: [...open, ...shelved] }, { id: 'loft', cards: loftOpen }] }
  const [first, second, third] = open as [Card, Card, Card]
  // Rows from the top, 40 pixels each: barn's three Open cards, loft's first Open card, and barn's first Backlog card.
  const view = page([first, second, third, loftOpen[0] as Card, shelved[0] as Card])
  try {
    view.down(third, 50, 100)
    // Over loft's Open card or barn's Backlog card, no line shows.
    for (const [y, card] of [[130, loftOpen[0]], [170, shelved[0]]] as [number, Card][]) {
      view.move(50, y)
      assert.deepEqual([dragClass(third), dragClass(card)], ['dragging', ''], String(y))
    }
    // On the top half of the first Open card, #30 lands above #31.
    view.move(50, 10)
    assert.equal(dragClass(first), 'drop-above')
    view.up(50, 10)
    await settle()
    // On the bottom half of the second, #31 lands below #32, and on its own place nothing goes.
    view.down(first, 50, 20)
    view.move(50, 70)
    assert.equal(dragClass(second), 'drop-below')
    view.up(50, 70)
    await settle()
    view.down(first, 50, 20)
    view.move(50, 50)
    assert.equal(dragClass(second), '')
    view.up(50, 50)
    await settle()
    assert.deepEqual(sent, [{ path: '/api/list-place', body: { desk: 'barn', key: 'next-30', before: 31 } },
      { path: '/api/list-place', body: { desk: 'barn', key: 'next-31', after: 32 } }])
    assert.equal(view.listening(), 0)
  } finally {
    globalThis.fetch = real.fetch
    Object.assign(globalThis, { document: real.document })
  }
})
