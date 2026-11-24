// Run with: node --test web/
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { afterEach, test } from 'node:test'

import { DETAILED_TITLE, DetailedToggle, Item, toggleDetailed } from './app-item.js'
import { startsDetailed, ui } from './app-state.js'

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
  ui.detailed = false
  Reflect.deleteProperty(globalThis, 'localStorage')
})

// A drawn element, with its function components drawn in place.
type Node = { type?: unknown; props?: Record<string, unknown> }
type Drawn = Node | string | number | null | undefined | boolean | Drawn[]
function draw(node: Drawn): Drawn {
  if (Array.isArray(node)) {
    return node.map(draw)
  }
  if (node === null || typeof node !== 'object') {
    return node
  }
  if (typeof node.type === 'function') {
    return draw((node.type as (props: unknown) => Drawn)(node.props))
  }
  return { ...node, props: { ...node.props, children: draw(node.props?.children as Drawn) } }
}

// An element's children that are elements, flattened.
function kids(node: Node): Node[] {
  return [node.props?.children].flat(Infinity as 1)
    .filter((one): one is Node => typeof one === 'object' && one !== null)
}

// The name an element goes by in a row: its first class, or `icon` for the icon's svg.
function nameOf(node: Node): string {
  return String(node.props?.class ?? '').split(' ')[0] ?? ''
}

/** A row's parts: those on line 1, in order, and those in `.more`, which Detailed draws as line 2, with its class. */
function lines(card: object): { one: string[]; two: string[]; more: string } {
  const row = draw(Item({ card, selected: false })) as Node
  const parts = kids(row)
  const more = parts.find(one => nameOf(one) === 'more')!
  return { one: parts.map(nameOf).filter(name => name !== 'more'), two: kids(more).map(nameOf),
    more: String(more.props?.class) }
}

function storage(stored: Map<string, string>) {
  Object.assign(globalThis, { localStorage: { getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value) } })
}

test('the Detailed button shows which view is on, and a press switches the list and remembers it', () => {
  const stored = new Map<string, string>()
  storage(stored)
  const button = () => DetailedToggle() as Node
  assert.equal(button().props?.['aria-pressed'], 'false')
  assert.equal(button().props?.title, DETAILED_TITLE)
  toggleDetailed()
  assert.equal(ui.detailed, true)
  assert.equal(button().props?.['aria-pressed'], 'true')
  assert.equal(stored.get('inbox-detailed'), 'true')
  toggleDetailed()
  assert.equal(ui.detailed, false)
  assert.equal(stored.get('inbox-detailed'), 'false')
})

test('the list starts in Detailed only when this browser remembers choosing it', () => {
  const stored = new Map<string, string>()
  storage(stored)
  assert.equal(startsDetailed(), false, 'nothing stored starts in Compact')
  stored.set('inbox-detailed', 'true')
  assert.equal(startsDetailed(), true)
  for (const unreadable of ['false', 'tru', '"detailed"', '1', '{']) {
    stored.set('inbox-detailed', unreadable)
    assert.equal(startsDetailed(), false, `${unreadable} starts in Compact`)
  }
  Reflect.deleteProperty(globalThis, 'localStorage')
  assert.equal(startsDetailed(), false, 'a page without storage starts in Compact')
})

test("a row keeps its title, state, and time on line 1, and its labels, run kind, and note in line 2's part", () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', labels: { bug: 'd73a4a' } }] }
  const card = { id: 'game:7', desk: 'game', stage: 'working', number: 7, pr: 8, title: 'Fix the jump',
    subs: { done: 1, total: 2 }, state: 'failing', runner: 'fix', labels: ['bug'], note: 'checks failed' }
  assert.deepEqual(lines(card), { one: ['icon', 'chip-cell', 'num', 'text', 'subs-badge', 'badge', 'time'],
    two: ['note', 'tags', 'note-cell'], more: 'more' })
  // A project's own tab draws no project chip.
  ui.tab = 'game'
  assert.deepEqual(lines(card).one, ['icon', 'num', 'text', 'subs-badge', 'badge', 'time'])
})

test('a row with no labels, run kind, or note marks its second line empty', () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', labels: { 'on-hold': 'fbca04' } }] }
  const bare = { id: 'game:3', desk: 'game', stage: 'open', number: 3, title: 'Tidy', runner: 'agent', labels: [] }
  assert.equal(lines(bare).more, 'more empty')
  for (const extra of [{ labels: ['bug'] }, { runner: 'fix' }, { note: 'waiting on #2' }, { note: 'on hold' }]) {
    assert.equal(lines({ ...bare, ...extra }).more, 'more', JSON.stringify(extra))
  }
})

test('a stalled run draws its note in the warning tone, and a working one in the plain one', () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', labels: {} }] }
  const card = { id: 'game:7', desk: 'game', stage: 'working', number: 7, title: 'Fix the jump', runner: 'agent',
    note: 'stalled 12 min in Bash: cargo test …' }
  const noteOf = (one: object) => {
    const cell = kids(kids(draw(Item({ card: one, selected: false })) as Node).find(part => nameOf(part) === 'more')!)
      .find(part => nameOf(part) === 'note-cell')!
    return String(kids(cell)[0]?.props?.class)
  }
  assert.equal(noteOf({ ...card, stalled: true }), 'note warning')
  assert.equal(noteOf(card), 'note')
})

// The body of every rule in app.css whose selector is exactly `selector`, joined, outside or inside an at-rule.
function rules(selector: string): string {
  const css = readFileSync(new URL('app.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  const found = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(([, at]) => at!.trim().split(/\s*,\s*/)
    .includes(selector)).map(([, , body]) => body)
  assert.ok(found.length > 0, `app.css has a ${selector} rule`)
  return found.join(';')
}

test('Compact lays line 2 out as the row itself, and Detailed draws it under the title', () => {
  assert.match(rules('.item .more'), /display:\s*contents/)
  assert.match(rules('.list.detailed .item'), /display:\s*grid/)
  const column = (part: string) => /grid-area:\s*([^;]+)/.exec(rules(`.list.detailed .item > ${part}`))![1]!.trim()
  for (const [part, at] of [['.icon', 1], ['.chip-cell', 2], ['.num', 3], ['.text', 4], ['.subs-badge', 5],
    ['.badge', 6], ['.time', 8]] as const) {
    assert.equal(column(part), `1 / ${at}`, `${part} sits on line 1`)
  }
  assert.match(column('.more'), /^2 \/ 4 /, 'line 2 starts in the title column')
  assert.match(rules('.list.detailed .item > .more.empty'), /display:\s*none/)
  // The note shows on line 2 even where a narrow list hides Compact's note column.
  assert.match(rules('.list.detailed .item .note-cell'), /display:\s*flex/)
  assert.match(rules('.item .note-cell'), /display:\s*none/)
  // The title keeps one line with an ellipsis in both views.
  assert.match(rules('.item .text'), /white-space:\s*nowrap/)
  assert.match(rules('.item .text'), /text-overflow:\s*ellipsis/)
  assert.doesNotMatch(rules('.list.detailed .item > .text'), /white-space|text-overflow|overflow:/)
})
