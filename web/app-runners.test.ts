// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { drawWith, ui } from './app-state.js'
import { AgentSlotsRow, GpuSlotsRow, PROJECT_WIDTH_LIMIT, ProjectCell, projectWidth, Runner, Runners, runnerCard,
  SlotRow } from './app-runners.js'

// The posts the page sent, which the stubbed fetch answers with an empty 200.
const posted: { path: string; data: unknown }[] = []
Object.assign(globalThis, {
  fetch: async (path: string, init: { body: string }) => {
    posted.push({ path, data: JSON.parse(init.body) })
    return { ok: true, status: 200, json: async () => ({}) }
  },
})
drawWith(() => undefined)

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
  ui.slotsSaving.clear()
  posted.length = 0
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

test('a project\'s agent stepper shows its count, and with an override the config\'s count and Reset', async () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', board: null, cards: [] }] }
  const buttons = (drawn: Drawn) => elementsOf(drawn).filter(one => one.type === 'button')
  const plain = AgentSlotsRow({ desk: 'game', agents: { count: 3, config: 3, override: false }, tone: 'busy' })
  assert.equal(squash(textOf(plain)), 'agent − 3 + slots')
  assert.deepEqual(buttons(plain).map(one => one.props['aria-label']), ['One agent slot fewer', 'One agent slot more'])
  assert.equal(classOf(cellsOf(plain)[0] as Element), 'runner working')

  const lowered = AgentSlotsRow({ desk: 'game', agents: { count: 1, config: 3, override: true }, tone: 'full' })
  assert.equal(squash(textOf(lowered)), 'agent − 1 of 3 + slot Reset')
  const [fewer, more, reset] = buttons(lowered) as Element[]
  await (fewer?.props.onClick as () => Promise<void>)()
  await (more?.props.onClick as () => Promise<void>)()
  await (reset?.props.onClick as () => Promise<void>)()
  assert.deepEqual(posted, [{ path: '/api/slots', data: { desk: 'game', agent: 0 } },
    { path: '/api/slots', data: { desk: 'game', agent: 2 } },
    { path: '/api/slots', data: { desk: 'game', reset: 'agent' } }])
  assert.equal(ui.slotsSaving.size, 0, 'the stepper takes presses again once the server answers')

  // − stops at 0, and every button waits while a change is on its way.
  const none = () => AgentSlotsRow({ desk: 'game', agents: { count: 0, config: 3, override: true }, tone: 'full' })
  assert.deepEqual(buttons(none()).map(one => one.props.disabled), [true, false, false])
  ui.slotsSaving.add('game')
  assert.deepEqual(buttons(none()).map(one => one.props.disabled), [true, true, true])
})

test('the stepper leads its project\'s group, which a project at 0 agent slots keeps', () => {
  const runner = (key: string, kind: string, number: number) => ({ key, runner: kind, number, title: key, text: key })
  const game = { id: 'game', title: 'Game', cards: [], agentSlots: { count: 0, config: 2, override: true },
    board: { error: null, runners: [runner('unit-1', 'agent', 4)], checks: [], slots: [] } }
  const tools = { id: 'tools', title: 'Tools', cards: [], agentSlots: { count: 0, config: 2, override: true },
    board: { error: null, runners: [], checks: [], slots: [] } }
  ui.server = { desks: [game, tools] }
  const groups = elementsOf(Runners({ shown: new Set() })).filter(one => one.type === 'tbody')
  // Lowered below the agents working, the group keeps their rows and shows none free.
  assert.deepEqual(groups.map(one => squash(textOf(one))),
    ['Game agent − 0 of 2 + slots Reset agent #4 unit-1', 'Tools agent − 0 of 2 + slots Reset'])
  const rows = elementsOf(groups[0] as Drawn).filter(one => one.type === 'tr')
  assert.deepEqual(rows.map(one => squash(textOf(cellsOf(one)[0]?.props.children as Drawn))), ['Game', ''])
})

test('a project\'s gpu stepper shows the agent count while unset, never more, and steps and resets its own kind',
  async () => {
  ui.server = { desks: [{ id: 'game', title: 'Game', board: null, cards: [] }] }
  const buttons = (drawn: Drawn) => elementsOf(drawn).filter(one => one.type === 'button') as Element[]
  const agents = { count: 3, config: 3, override: false }
  const unset = GpuSlotsRow({ desk: 'game', agents, gpu: { count: null, config: null, override: false }, busy: 1 })
  assert.equal(squash(textOf(unset)), 'gpu − 3 + slots')
  assert.deepEqual(buttons(unset).map(one => one.props.disabled), [false, true])
  assert.equal(classOf(cellsOf(unset)[0] as Element), 'runner working')

  // An override above a lowered agent count shows at the agent count, and its tone turns full with every slot busy.
  const lowered = { count: 1, config: 3, override: true }
  const over = GpuSlotsRow({ desk: 'game', agents: lowered, gpu: { count: 2, config: null, override: true }, busy: 1 })
  assert.equal(squash(textOf(over)), 'gpu − 1 of 1 + slot Reset')
  assert.equal(classOf(cellsOf(over)[0] as Element), 'runner full')

  const set = GpuSlotsRow({ desk: 'game', agents, gpu: { count: 1, config: 2, override: true }, busy: 0 })
  assert.equal(squash(textOf(set)), 'gpu − 1 of 2 + slot Reset')
  assert.equal(buttons(set)[2]?.props.title, 'Back to the 2 gpu slots .agent-desk.json sets')
  for (const button of buttons(set)) {
    await (button.props.onClick as () => Promise<void>)()
  }
  assert.deepEqual(posted, [{ path: '/api/slots', data: { desk: 'game', gpu: 0 } },
    { path: '/api/slots', data: { desk: 'game', gpu: 2 } },
    { path: '/api/slots', data: { desk: 'game', reset: 'gpu' } }])
})

test('the gpu stepper sits under the agent stepper, and each agent slot working a gpu issue is marked', () => {
  const runner = (key: string, number: number, labels: string[]) => ({ key, runner: 'agent', number, title: key,
    text: key, labels })
  ui.server = { desks: [{ id: 'game', title: 'Game', cards: [], agentSlots: { count: 3, config: 3, override: false },
    gpuSlots: { count: 1, config: null, override: true },
    board: { error: null, runners: [runner('unit-1', 4, ['gpu']), runner('unit-2', 5, ['bug'])], checks: [],
      slots: [{ kind: 'agent', busy: 2, total: 3 }] } }] }
  const [group] = elementsOf(Runners({ shown: new Set() })).filter(one => one.type === 'tbody')
  assert.equal(squash(textOf(group as Drawn)), 'Game agent − 3 + slots gpu − 1 of 3 + slot Reset '
    + 'agent gpu #4 unit-1 agent #5 unit-2 agent free')
  const rows = elementsOf(group as Drawn).filter(one => one.type === 'tr')
  assert.equal(classOf(cellsOf(rows[1] as Element)[1] as Element), 'runner full')
  const marks = (row: Element | undefined) => elementsOf(row as Drawn).filter(one => classOf(one) === 'gpu-mark').length
  assert.deepEqual([marks(rows[2]), marks(rows[3])], [1, 0])
})
