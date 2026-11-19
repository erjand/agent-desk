// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, beforeEach, test, type TestContext } from 'node:test'

import { ui } from './app-state.js'

/** The few DOM calls Preact makes to draw a hovercard, kept as a tree whose text a test reads. */
class FakeNode {
  nodeType: number
  localName: string
  data = ''
  parentNode: FakeNode | null = null
  childNodes: FakeNode[] = []
  style = { setProperty: () => {} }

  constructor(nodeType: number, localName = '') {
    this.nodeType = nodeType
    this.localName = localName
  }

  get firstChild() {
    return this.childNodes[0] ?? null
  }

  get nextSibling() {
    const siblings = this.parentNode?.childNodes ?? []
    return siblings[siblings.indexOf(this) + 1] ?? null
  }

  get textContent(): string {
    return this.nodeType === 3 ? this.data : this.childNodes.map(child => child.textContent).join('')
  }

  insertBefore(node: FakeNode, before: FakeNode | null) {
    node.parentNode?.removeChild(node)
    const at = before === null ? -1 : this.childNodes.indexOf(before)
    this.childNodes.splice(at === -1 ? this.childNodes.length : at, 0, node)
    node.parentNode = this
    return node
  }

  appendChild(node: FakeNode) {
    return this.insertBefore(node, null)
  }

  removeChild(node: FakeNode) {
    this.childNodes.splice(this.childNodes.indexOf(node), 1)
    node.parentNode = null
    return node
  }

  contains(node: unknown): boolean {
    return node === this || this.childNodes.some(child => child.contains(node))
  }

  setAttribute() {}
  removeAttribute() {}
  addEventListener() {}
  removeEventListener() {}
}

const document = {
  body: new FakeNode(1, 'body'),
  createElement: (name: string) => new FakeNode(1, name),
  createElementNS: (_space: string, name: string) => new FakeNode(1, name),
  createTextNode: (data: string) => Object.assign(new FakeNode(3), { data }),
}
Object.assign(globalThis, {
  document,
  location: { href: 'http://127.0.0.1:7480/', origin: 'http://127.0.0.1:7480' },
  window: { innerHeight: 900, innerWidth: 1400 },
})
const { hideHovercard, hoverRoot, onLinkOut, onLinkOver } = await import('./app-hovercard.js')

const desks = [{ id: 'barn', repo: 'erin/barn' }, { id: 'loft', repo: 'erin/loft' }]

/** A link on the page to `href`, as the pointer finds it. */
function link(href: string) {
  const anchor = {
    href: new URL(href, 'http://127.0.0.1:7480/').href,
    isConnected: true,
    closest: () => anchor,
    contains: (node: unknown) => node === anchor,
    getBoundingClientRect: () => ({ top: 100, bottom: 120, left: 40 }),
  }
  return anchor
}

type Link = ReturnType<typeof link>

/** Each `/api/hovercard` read the page made, as `<desk>:<number>`. */
let reads: string[] = []
/** The hovercard each read returns, by `<desk>:<number>`, or a failed read when it has none. */
let served = new Map<string, object>()

function card(number: number, title: string, at = new Date().toISOString()) {
  return { kind: 'issue', number, title, state: 'open', at, excerpt: '', labels: [] }
}

/** Lets the hovercard's read finish and the card redraw with what it returned. */
async function settle(t: TestContext) {
  for (let turn = 0; turn < 5; turn += 1) {
    await new Promise(resolve => setImmediate(resolve))
  }
  t.mock.timers.tick(0)
}

/** Rests the pointer on `anchor` long enough for its hovercard to show, then lets its read finish. */
async function rest(t: TestContext, anchor: Link) {
  onLinkOver({ target: anchor })
  t.mock.timers.tick(400)
  await settle(t)
}

const shown = () => hoverRoot.textContent

beforeEach(t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.parse('2026-11-16T12:00:00Z') })
  ui.server = { desks } as typeof ui.server
  reads = []
  served = new Map()
  Object.assign(globalThis, { fetch: async (path: string) => {
    const asked = new URL(path, 'http://127.0.0.1:7480').searchParams
    const id = `${asked.get('desk')}:${asked.get('number')}`
    reads.push(id)
    const found = served.get(id)
    return found ? { ok: true, json: async () => found } : { ok: false, status: 404 }
  } })
})

afterEach(() => {
  hideHovercard()
  ui.server = null
})

test('a link to an item on the page shows its hovercard once the pointer rests on it, and reads it once', async t => {
  served.set('barn:4', card(4, 'Paint the fence'))
  const anchor = link('#barn:4')
  onLinkOver({ target: anchor })
  t.mock.timers.tick(399)
  assert.equal(shown(), '')
  assert.deepEqual(reads, [])
  t.mock.timers.tick(1)
  assert.equal(shown(), 'Reading #4…')
  await settle(t)
  assert.match(shown(), /^erin\/barn on .*Paint the fence #4$/)
  assert.deepEqual(reads, ['barn:4'])
})

test('a GitHub link to an issue or a PR in a project the page shows has a hovercard, whatever its case or tail',
  async t => {
  const hrefs = {
    'https://github.com/erin/barn/issues/11': 'barn:11',
    'https://github.com/Erin/Loft/pull/12/files': 'loft:12',
    'https://github.com/erin/barn/pull/13?w=1': 'barn:13',
    'https://github.com/erin/barn/issues/14#issuecomment-9': 'barn:14',
  }
  for (const [href, id] of Object.entries(hrefs)) {
    reads = []
    await rest(t, link(href))
    assert.deepEqual(reads, [id], href)
    hideHovercard()
  }
})

test('a link to no item, or to an item in a project the page does not show, has no hovercard', async t => {
  for (const href of [
    'https://github.com/erin/shed/issues/15',
    'https://github.com/erin/barnyard/issues/15',
    'https://github.com/erin/barn/issues/15abc',
    'https://github.com/erin/barn/issues',
    'https://github.com/erin/barn/commit/15',
    'https://example.com/erin/barn/issues/15',
    '#shed:15',
    '#barn',
    'http://example.com/#barn:15',
  ]) {
    await rest(t, link(href))
    assert.equal(shown(), '', href)
  }
  assert.deepEqual(reads, [])
})

test('a hovercard reads its item again only once the one it read is a minute old, and keeps it while it reads',
  async t => {
  served.set('barn:21', card(21, 'Mend the gate'))
  const anchor = link('#barn:21')
  await rest(t, anchor)
  hideHovercard()
  // The second card shows 400ms after the pointer rests, a minute to the millisecond after the first was read.
  t.mock.timers.tick(60_000 - 400)
  await rest(t, anchor)
  hideHovercard()
  assert.deepEqual(reads, ['barn:21'])
  served.set('barn:21', card(21, 'Mend the north gate'))
  t.mock.timers.tick(1)
  onLinkOver({ target: anchor })
  t.mock.timers.tick(400)
  assert.match(shown(), /Mend the gate #21$/)
  await settle(t)
  assert.match(shown(), /Mend the north gate #21$/)
  assert.deepEqual(reads, ['barn:21', 'barn:21'])
})

test('a hovercard that cannot be read says so, and keeps the card it read before', async t => {
  await rest(t, link('#barn:31'))
  assert.equal(shown(), 'Could not read #31.')
  hideHovercard()

  served.set('barn:32', card(32, 'Oil the hinge'))
  const anchor = link('#barn:32')
  await rest(t, anchor)
  hideHovercard()
  served.delete('barn:32')
  t.mock.timers.tick(60_001)
  await rest(t, anchor)
  assert.match(shown(), /Oil the hinge #32$/)
  assert.deepEqual(reads, ['barn:31', 'barn:32', 'barn:32'])
})

test('a hovercard names the year an item opened only when it is not this one', async t => {
  served.set('barn:41', card(41, 'This year', '2026-03-04T10:00:00Z'))
  served.set('barn:42', card(42, 'Last year', '2025-03-04T10:00:00Z'))
  await rest(t, link('#barn:41'))
  assert.doesNotMatch(shown(), /2026/)
  hideHovercard()
  await rest(t, link('#barn:42'))
  assert.match(shown(), /2025/)
})

test('a pointer that leaves a link before its card shows forgets it, and one that leaves after hides it soon',
  async t => {
  served.set('barn:51', card(51, 'Sweep the loft'))
  const anchor = link('#barn:51')
  onLinkOver({ target: anchor })
  t.mock.timers.tick(200)
  onLinkOut({ target: anchor, relatedTarget: null })
  t.mock.timers.tick(1000)
  assert.equal(shown(), '')
  assert.deepEqual(reads, [])
  // A pointer back on the link at once waits afresh for the card.
  onLinkOver({ target: anchor })
  onLinkOut({ target: anchor, relatedTarget: null })
  await rest(t, anchor)
  assert.match(shown(), /Sweep the loft/)
  onLinkOut({ target: anchor, relatedTarget: null })
  t.mock.timers.tick(249)
  assert.match(shown(), /Sweep the loft/)
  t.mock.timers.tick(1)
  assert.equal(shown(), '')
})

test('a pointer that crosses from a link onto its card, or back onto the link, keeps the card', async t => {
  served.set('barn:61', card(61, 'Stack the hay'))
  const anchor = link('#barn:61')
  await rest(t, anchor)
  onLinkOut({ target: anchor, relatedTarget: hoverRoot.firstChild })
  t.mock.timers.tick(1000)
  assert.match(shown(), /Stack the hay/)

  onLinkOut({ target: anchor, relatedTarget: null })
  t.mock.timers.tick(100)
  onLinkOver({ target: anchor })
  t.mock.timers.tick(1000)
  assert.match(shown(), /Stack the hay/)
})

test('a pointer that moves to another link while a card shows swaps the card at once', async t => {
  served.set('barn:71', card(71, 'Feed the hens'))
  served.set('loft:72', card(72, 'Fix the ladder'))
  await rest(t, link('#barn:71'))
  onLinkOver({ target: link('#loft:72') })
  await settle(t)
  assert.match(shown(), /^erin\/loft on .*Fix the ladder #72$/)
})
