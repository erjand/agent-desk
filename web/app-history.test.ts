// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { ui } from './app-state.js'
import { History } from './app-history.js'

// The paths the page read, and the rows the stubbed server answers each read with.
const asked: string[] = []
let answer: { ok: boolean; rows: unknown[] } = { ok: true, rows: [] }
Object.assign(globalThis, {
  fetch: async (path: string) => {
    asked.push(path)
    const { ok, rows } = answer
    return { ok, status: ok ? 200 : 500, json: async () => ({ rows }) }
  },
})

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
  ui.history = { query: '', range: '24h', from: '', to: '', sort: { key: 'at', dir: 'desc' }, page: 0, read: null }
  asked.length = 0
  answer = { ok: true, rows: [] }
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

// The elements a drawn tree holds, with each component drawn, in order.
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
const textsOf = (node: Drawn, type: string) => elementsOf(node).filter(one => one.type === type)
  .map(one => squash(textOf(one.props.children as Drawn)))
// Each row's Item cell, top to bottom.
const titles = (node: Drawn) => elementsOf(node).filter(one => one.type === 'td' && one.props.class === 'title')
  .map(one => squash(textOf(one.props.children as Drawn)))
const totals = (node: Drawn) => squash(textOf(elementsOf(node).find(one => one.props.class === 'history-totals muted')
  ?.props.children as Drawn))
// Presses a heading's sort button, or Previous or Next, by its text.
function press(node: Drawn, text: string) {
  const button = elementsOf(node).find(one => one.type === 'button' && squash(textOf(one.props.children as Drawn))
    .replace(/ [▲▼]$/, '') === text)
  assert.ok(button, `no ${text} button`)
  ;(button.props.onClick as () => void)()
}

const desk = (id: string, title: string) => ({ id, title, repo: `org/${id}`, cards: [] })
const row = (number: number, title: string, at: string, more: Record<string, unknown> = {}) => ({
  kind: 'pr', desk: 'game', number, pr: number + 100, title, at, cost: 1, runs: 1,
  url: `https://github.com/org/game/pull/${number + 100}`, ...more })

/** The History column once its first read answered with `rows` for the projects `desks` names. */
async function drawn(rows: unknown[], desks = [desk('game', 'Game')]) {
  ui.server = { desks } as typeof ui.server
  answer = { ok: true, rows }
  History()
  await new Promise(resolve => setImmediate(resolve))
  return History()
}

test('the column reads the shown projects\' merges over the span and lists them newest first with their totals',
  async () => {
    const column = await drawn([
      row(1, 'Plant the seeds', '2026-11-16T08:00:00Z', { cost: 2.5, runs: 2 }),
      row(2, 'Water the garden', '2026-11-16T10:00:00Z', { cost: 4, runs: 1 }),
    ], [desk('game', 'Game'), desk('site', 'Site')])
    assert.equal(asked.length, 1)
    assert.match(asked[0], /^\/api\/history\?desks=game%2Csite&since=\d+&until=\d+$/)
    const [since, until] = asked[0].match(/since=(\d+)&until=(\d+)/)!.slice(1).map(Number)
    assert.equal(until - since, 86_400_000)
    assert.deepEqual(titles(column), ['Water the garden', 'Plant the seeds'])
    assert.equal(totals(column), '2 merged, from 3 runs · $7 at API prices')
    // A second draw within the minute reads nothing more.
    History()
    assert.equal(asked.length, 1)
  })

test('the Project column shows only while more than one project shows', async () => {
  const one = await drawn([row(1, 'Plant the seeds', '2026-11-16T08:00:00Z')])
  assert.deepEqual(textsOf(one, 'th'), ['Time ▼', 'Number', 'Item', 'Cost'])
  ui.history.read = null
  const two = await drawn([row(1, 'Plant the seeds', '2026-11-16T08:00:00Z')], [desk('game', 'Game'),
    desk('site', 'Site')])
  assert.deepEqual(textsOf(two, 'th'), ['Time ▼', 'Project', 'Number', 'Item', 'Cost'])
})

test('each PR\'s Cost names the GPU minutes its runs held and queued, and the totals add them up', async () => {
  const column = await drawn([
    row(1, 'Plant the seeds', '2026-11-16T08:00:00Z', { gpuHeld: 12.4, gpuQueued: 9.2,
      gpu: 'Held GPU for 12 mins, waited 9 mins for it', runs: 2 }),
    row(2, 'Water the garden', '2026-11-16T09:00:00Z', { gpuHeld: 0.2, gpuQueued: 0, gpu: 'Held GPU for <1 min' }),
    row(3, 'Mow the lawn', '2026-11-16T10:00:00Z'),
  ])
  const cells = elementsOf(column).filter(one => one.type === 'td' && one.props.class === 'cost')
  assert.deepEqual(cells.map(one => one.props.title), ['1 run', '1 run · Held GPU for <1 min',
    '2 runs · Held GPU for 12 mins, waited 9 mins for it'])
  assert.equal(totals(column), '3 merged, from 4 runs · $3 at API prices · Held GPU for 13 mins, waited 9 mins for it')
})

test('the totals leave the GPU out while no PR\'s runs used it', async () => {
  assert.equal(totals(await drawn([row(1, 'Plant the seeds', '2026-11-16T08:00:00Z')])),
    '1 merged, from 1 run · $1 at API prices')
})

test('the search keeps the rows that hold every word, by title, number, PR number, or project title', async () => {
  const rows = [row(1, 'Plant the seeds', '2026-11-16T08:00:00Z', { cost: 3 }),
    row(2, 'Water the seeds', '2026-11-16T09:00:00Z'),
    row(3, 'Mow the lawn', '2026-11-16T10:00:00Z', { desk: 'site' })]
  await drawn(rows, [desk('game', 'Garden Game'), desk('site', 'Site')])
  const search = (query: string) => {
    ui.history.query = query
    return History()
  }
  assert.deepEqual(titles(search('SEEDS plant')), ['Plant the seeds'])
  assert.equal(totals(search('seeds plant')), '1 of 3 merged, from 1 run · $3 at API prices')
  assert.deepEqual(titles(search('#2')), ['Water the seeds'])
  assert.deepEqual(titles(search('PR #103')), ['Mow the lawn'])
  assert.deepEqual(titles(search('garden')), ['Water the seeds', 'Plant the seeds'])
  const none = search('  weeds ')
  assert.deepEqual(titles(none), [])
  assert.ok(squash(textOf(none)).includes('Nothing in this span matches “weeds”.'))
})

test('a heading sorts by its column, a second press flips it, and a tie goes newest first', async () => {
  const column = await drawn([row(1, 'Bravo', '2026-11-16T08:00:00Z', { cost: 2 }),
    row(2, 'alpha', '2026-11-16T09:00:00Z', { cost: 10 }),
    row(3, 'Charlie', '2026-11-16T10:00:00Z', { cost: 2 })])
  press(column, 'Item')
  assert.deepEqual(ui.history.sort, { key: 'title', dir: 'asc' })
  assert.deepEqual(titles(History()), ['alpha', 'Bravo', 'Charlie'])
  press(History(), 'Item')
  assert.deepEqual(titles(History()), ['Charlie', 'Bravo', 'alpha'])
  // A number column starts largest first, and sorts 10 above 2.
  press(History(), 'Cost')
  assert.deepEqual(ui.history.sort, { key: 'cost', dir: 'desc' })
  assert.deepEqual(titles(History()), ['alpha', 'Charlie', 'Bravo'])
  press(History(), 'Cost')
  assert.deepEqual(titles(History()), ['Charlie', 'Bravo', 'alpha'])
})

test('the column pages through twenty rows at a time and starts again from the first page on a new sort', async () => {
  const rows = Array.from({ length: 45 }, (_, i) => row(i + 1, `Merge ${i + 1}`,
    new Date(Date.UTC(2026, 10, 16, 0, i)).toISOString()))
  const first = await drawn(rows)
  assert.equal(titles(first).length, 20)
  assert.equal(titles(first)[0], 'Merge 45')
  assert.ok(squash(textOf(first)).includes('Previous 1–20 of 45 Next'))
  press(first, 'Next')
  press(History(), 'Next')
  const last = History()
  assert.deepEqual(titles(last), ['Merge 5', 'Merge 4', 'Merge 3', 'Merge 2', 'Merge 1'])
  assert.ok(squash(textOf(last)).includes('41–45 of 45'))
  const next = elementsOf(last).find(one => one.type === 'button' && squash(textOf(one.props.children as Drawn)) === 'Next')
  assert.equal(next?.props.disabled, true)
  press(last, 'Time')
  assert.equal(ui.history.page, 0)
  assert.equal(titles(History())[0], 'Merge 1')
})

test('a page past the last, as a narrower search leaves, shows the last page', async () => {
  await drawn(Array.from({ length: 25 }, (_, i) => row(i + 1, `Merge ${i + 1}`,
    new Date(Date.UTC(2026, 10, 16, 0, i)).toISOString())))
  ui.history.page = 1
  ui.history.query = 'merge 2'
  // Merge 2, 12, and 20 to 25 fit one page.
  assert.deepEqual(titles(History()), ['Merge 25', 'Merge 24', 'Merge 23', 'Merge 22', 'Merge 21', 'Merge 20',
    'Merge 12', 'Merge 2'])
})

test('a custom span reads whole days, and one that ends before it starts asks for the days', async () => {
  ui.history.range = 'custom'
  ui.history.from = '2026-11-10'
  ui.history.to = '2026-11-12'
  await drawn([])
  const [since, until] = asked[0].match(/since=(\d+)&until=(\d+)/)!.slice(1).map(Number)
  assert.equal(since, new Date(2026, 10, 10).getTime())
  assert.equal(until, new Date(2026, 10, 13).getTime())
  ui.history.to = '2026-11-09'
  asked.length = 0
  assert.ok(squash(textOf(History())).includes('Pick the first and last day.'))
  assert.deepEqual(asked, [])
})

test('a read the server refuses says so', async () => {
  ui.server = { desks: [desk('game', 'Game')] } as typeof ui.server
  answer = { ok: false, rows: [] }
  History()
  await new Promise(resolve => setImmediate(resolve))
  assert.ok(squash(textOf(History())).includes('Could not read the history.'))
})
