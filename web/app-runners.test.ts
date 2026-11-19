// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { ui } from './app-state.js'
import { PROJECT_WIDTH_LIMIT, ProjectCell, projectWidth, Runner, Runners, runnerCard, SlotRow } from './app-runners.js'

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
})

// The text a drawn element holds, in order, with a space around each element's, since htm drops the space between
// tags.
type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]
function textOf(node: Drawn): string {
  if (node === null || node === undefined || typeof node === 'boolean') {
    return ''
  }
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }
  if (Array.isArray(node)) {
    return node.map(textOf).join('')
  }
  if (typeof node.type === 'function') {
    return textOf((node.type as (props: unknown) => Drawn)(node.props))
  }
  return ` ${textOf(node.props?.children as Drawn)} `
}
const squash = (text: string) => text.replace(/\s+/g, ' ').trim()

test("a busy row names its issue's card, or its PR's when it works on a PR alone", () => {
  assert.equal(runnerCard('game', { number: 12 }), 'game:12')
  assert.equal(runnerCard('game', { number: 30, issue: 12, pr: true }), 'game:12')
  assert.equal(runnerCard('game', { number: 30, pr: true }), 'game:pr-30')
})

test('a row with no number, or a dimmed one, names no card', () => {
  assert.equal(runnerCard('game', {}), null)
  assert.equal(runnerCard('game', { number: 12, dim: true }), null)
})

// The elements a drawn tree holds, with each component drawn, in order, for checking classes and cells.
type Element = { type: string; props: Record<string, unknown> }
function elementsOf(node: Drawn): Element[] {
  if (node === null || node === undefined || typeof node !== 'object') {
    return []
  }
  if (Array.isArray(node)) {
    return node.flatMap(elementsOf)
  }
  if (typeof node.type === 'function') {
    return elementsOf((node.type as (props: unknown) => Drawn)(node.props))
  }
  return [node as Element, ...elementsOf(node.props?.children as Drawn)]
}
const cellsOf = (row: Drawn) => elementsOf(row).filter(one => one.type === 'td' || one.type === 'th')
const classOf = (one: Element) => String(one.props.class ?? '')
const headOf = (drawn: Drawn) => elementsOf(drawn).filter(one => one.type === 'th')
  .map(one => squash(textOf(one.props.children as Drawn)))

test('a busy row shows its kind, number, title, and time, and a free one its kind and a muted free', () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', board: null, cards: [] }] }
  const row = { key: 'unit-1', number: 12, title: 'Mow the lawn', time: '5m', text: 'agent     Mow the lawn' }
  const busy = Runner({ kind: 'agent', row, desk: 'game', shown: new Set() })
  assert.equal(squash(textOf(busy)), 'agent #12 Mow the lawn 5m')
  assert.deepEqual(cellsOf(busy).map(classOf), ['runner working', 'number', 'title', 'time'])
  const free = SlotRow({ desk: 'game', slot: { kind: 'fix', busy: false }, shown: new Set() })
  assert.equal(squash(textOf(free)), 'fix free')
  assert.deepEqual(cellsOf(free).map(classOf), ['runner ', 'number', 'title muted', 'time'])
  assert.ok(!classOf(elementsOf(free)[0] as Element).includes('pressable'))
  assert.equal(squash(textOf(SlotRow({ desk: 'game', slot: { kind: 'check', busy: true }, shown: new Set() }))),
    'check busy')
})

test('a kind with every slot busy shows its Runner cells in the attention tone', () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', board: null, cards: [] }] }
  const row = { key: 'unit-1', number: 12, title: 'Mow the lawn', text: 'chore     Mow the lawn' }
  const named = SlotRow({ desk: 'game', slot: { kind: 'chore', busy: true, row, tone: 'full' }, shown: new Set() })
  const bare = SlotRow({ desk: 'game', slot: { kind: 'chore', busy: true, tone: 'full' }, shown: new Set() })
  assert.equal(classOf(cellsOf(named)[0] as Element), 'runner full')
  assert.equal(classOf(cellsOf(bare)[0] as Element), 'runner full')
})

test('pressing a busy row selects its card when the list shows it, and its title is a button for the keyboard', () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', board: null, cards: [] }] }
  const row = { key: 'unit-1', number: 12, title: 'Mow the lawn', text: 'agent     Mow the lawn' }
  const drawn = Runner({ kind: 'agent', row, desk: 'game', shown: new Set(['game:12']) })
  assert.ok(elementsOf(drawn).some(one => one.type === 'button'))
  drawn.props.onClick()
  assert.equal(ui.selected, 'game:12')
})

test('the sidebar is one table with headers, a Project column and rules on All, and locals in their group', () => {
  const runner = (key: string, kind: string, number: number) => ({ key, runner: kind, number, title: key, text: key })
  const board = (runners: object[], slots: object[]) => ({ error: null, runners, checks: [], slots })
  const game = { id: 'game', title: 'Game', cards: [], board: board([runner('unit-1', 'agent', 4),
    runner('local-7', 'local', 7)], [{ kind: 'agent', busy: 1, total: 2 }]) }
  const tools = { id: 'tools', title: 'Tools', cards: [], board: board([], [{ kind: 'agent', busy: 0, total: 1 }]) }
  ui.server = { desks: [game, tools] }
  const all = Runners({ shown: new Set() })
  assert.equal(elementsOf(all).filter(one => one.type === 'table').length, 1)
  assert.ok(classOf(elementsOf(all).find(one => one.type === 'table') as Element).includes('all'))
  assert.deepEqual(headOf(all), ['Project', 'Runner', 'Item', 'Title', 'Time'])
  // Each project's rows sit in a group of their own, its chip on the first row and its local session last.
  const groups = elementsOf(all).filter(one => one.type === 'tbody')
  assert.deepEqual(groups.map(one => squash(textOf(one))),
    ['Game agent #4 unit-1 agent free local #7 local-7', 'Tools agent free'])
  const gameRows = elementsOf(groups[0] as Drawn).filter(one => one.type === 'tr')
  assert.deepEqual(gameRows.map(one => squash(textOf(cellsOf(one)[0]?.props.children as Drawn))), ['Game', '', ''])
  assert.equal(squash(textOf(all)), 'Runners Project Runner Item Title Time Game agent #4 unit-1 agent free '
    + 'local #7 local-7 Tools agent free')

  ui.tab = 'game'
  const alone = Runners({ shown: new Set() })
  const table = elementsOf(alone).find(one => one.type === 'table') as Element
  assert.ok(!classOf(table).includes('all'))
  assert.deepEqual(headOf(alone), ['Runner', 'Item', 'Title', 'Time'])
  assert.equal(elementsOf(alone).filter(one => one.type === 'col').length, 4)
  assert.equal(squash(textOf(alone)), 'Runners Runner Item Title Time agent #4 unit-1 agent free local #7 local-7')
})

test('a project with no slots and no sessions keeps its group with a muted no slots', () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', cards: [], board: { error: null, runners: [], checks: [],
    slots: [] } }] }
  assert.equal(squash(textOf(Runners({ shown: new Set() }))), 'Runners Project Runner Item Title Time Game no slots')
})

test('the Project column fits the longest project title up to its cap', () => {
  const desk = (id: string, title: string) => ({ id, title, board: null, cards: [] })
  ui.server = { desks: [desk('game', 'Game'), desk('tools', 'Tools')] }
  assert.equal(projectWidth(), 8)
  ui.server = { desks: [desk('game', 'Game'), desk('demo', 'Demo Storefront Portal')] }
  assert.equal(projectWidth(), PROJECT_WIDTH_LIMIT)
  const side = (drawn: Drawn) => elementsOf(drawn).find(one => one.type === 'aside') as Element
  assert.deepEqual(side(Runners({ shown: new Set() })).props.style, { '--chip-width': `${PROJECT_WIDTH_LIMIT}ch` })
})

test("a group's first Project cell holds its chip and the full title to hover, and the cells under it neither", () => {
  ui.server = { desks: [{ id: 'demo', title: 'Demo Storefront Portal', board: null, cards: [] }] }
  const first = ProjectCell({ desk: 'demo', chip: true }) as Element
  assert.equal(first.props.title, 'Demo Storefront Portal')
  assert.equal(squash(textOf(first)), 'Demo Storefront Portal')
  const under = ProjectCell({ desk: 'demo', chip: false }) as Element
  assert.equal(under.props.title, undefined)
  assert.equal(squash(textOf(under)), '')
  assert.equal(ProjectCell({ desk: 'demo', chip: null }), null)
})
