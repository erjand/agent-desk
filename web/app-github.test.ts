// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { GitHub, githubFailing, groupsOf } from './app-github.js'
import { ui } from './app-state.js'

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
})

// A drawn element, with its function components drawn in place.
type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]
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
const classOf = (one: Element) => String(one.props.class ?? '')

// One GitHub row as the board words it.
const row = (key: string, note: string, notice = 'muted') => ({ key, title: key.replace('github-', ''), note, notice })
const backup = row('github-backup', 'last commit Nov 10 11:50, last push Nov 10 11:10')
const game = { id: 'game', title: 'Game', board: { error: null, needs: [], github: [
  row('github-main', 'matches origin'), row('github-agent-branches', '10 pushed'), backup] } }
const tools = { id: 'tools', title: 'Tools', board: { error: null, needs: [], github: [
  row('github-main', '1 commit ahead of origin; push failed: down; retrying in 30s', 'warning'), backup] } }

/** Each body row of the sidebar's table: its cells' classes and its text. */
function rowsOf(drawn: Drawn) {
  return elementsOf(drawn).filter(one => one.type === 'tr' && classOf(one) === 'github-row')
    .map(one => ({ cells: elementsOf(one).filter(cell => cell.type === 'td').map(classOf),
      chips: elementsOf(one).filter(cell => classOf(cell).split(' ').includes('chip')).length,
      text: squash(textOf(one)) }))
}

test("on All the sidebar shows githubRows' rows in their order and tones, a project's chip on its group's first row",
  () => {
    ui.server = { desks: [game, tools] }
    const drawn = GitHub()
    assert.equal(squash(textOf(elementsOf(drawn).find(one => classOf(one) === 'side-head') as Drawn)),
      'GitHub 1 failing')
    assert.deepEqual(elementsOf(drawn).filter(one => one.type === 'th').map(one => squash(textOf(one))),
      ['Project', 'Push', 'Status'])
    assert.deepEqual(elementsOf(drawn).filter(one => one.type === 'tbody').length, 3)
    assert.deepEqual(rowsOf(drawn), [
      { cells: ['desk', 'g-name', 'g-note muted'], chips: 1, text: 'Game main matches origin' },
      { cells: ['desk', 'g-name', 'g-note muted'], chips: 0, text: 'agent-branches 10 pushed' },
      { cells: ['desk', 'g-name', 'g-note warning'], chips: 1,
        text: 'Tools main 1 commit ahead of origin; push failed: down; retrying in 30s' },
      { cells: ['desk', 'g-name', 'g-note muted'], chips: 0,
        text: 'backup last commit Nov 10 11:50, last push Nov 10 11:10' },
    ])
  })

test("a project's tab shows its own rows and the backup, with no Project column", () => {
  ui.server = { desks: [game, tools] }
  ui.tab = 'game'
  const drawn = GitHub()
  assert.deepEqual(elementsOf(drawn).filter(one => one.type === 'th').map(one => squash(textOf(one))),
    ['Push', 'Status'])
  assert.deepEqual(rowsOf(drawn).map(({ cells, text }) => [cells, text]), [
    [['g-name', 'g-note muted'], 'main matches origin'],
    [['g-name', 'g-note muted'], 'agent-branches 10 pushed'],
    [['g-name', 'g-note muted'], 'backup last commit Nov 10 11:50, last push Nov 10 11:10'],
  ])
  assert.equal(squash(textOf(elementsOf(drawn).find(one => classOf(one) === 'side-head') as Drawn)), 'GitHub')
})

test('the failing count follows the tab, and a sidebar with no board read says so', () => {
  ui.server = { desks: [game, tools] }
  assert.equal(githubFailing(), 1)
  ui.tab = 'game'
  assert.equal(githubFailing(), 0)
  ui.server = { desks: [{ id: 'game', title: 'Game', board: null }] }
  ui.tab = 'all'
  assert.equal(githubFailing(), 0)
  assert.match(squash(textOf(GitHub())), /No board has been read yet\./)
})

test('the rows group in runs of one project each, in their order', () => {
  const a = { id: 'a' }
  const b = { id: 'b' }
  const rows = [{ desk: a, row: 1 }, { desk: a, row: 2 }, { desk: b, row: 3 }, { desk: null, row: 4 }]
  assert.deepEqual(groupsOf(rows).map(group => group.map(one => one.row)), [[1, 2], [3], [4]])
})
