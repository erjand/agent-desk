// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { gaugeGroups, Header, headerNotices, modeOf, tabMode, tipRoom } from './app-header.js'
import { Runners } from './app-runners.js'
import { ui } from './app-state.js'

afterEach(() => {
  ui.server = null
  ui.resources = { open: false, data: null, error: null }
  ui.side = null
  ui.tab = 'all'
  ui.connected = false
})

// Header sets the page's title, which a test has no document for.
Object.assign(globalThis, { document: { title: '' } })

// A drawn element, with its function components drawn in place, and the elements under it whose class names `name`.
type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]
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
function find(node: Drawn, name: string): { props: Record<string, unknown> }[] {
  if (Array.isArray(node)) {
    return node.flatMap(one => find(one, name))
  }
  if (node === null || typeof node !== 'object') {
    return []
  }
  const own = String(node.props?.class ?? '').split(' ').includes(name) ? [node as { props: Record<string, unknown> }]
    : []
  return [...own, ...find(node.props?.children as Drawn, name)]
}
function textOf(node: Drawn): string {
  if (Array.isArray(node)) {
    return node.map(textOf).join(' ')
  }
  if (node === null || node === undefined || typeof node === 'boolean') {
    return ''
  }
  return typeof node === 'object' ? textOf(node.props?.children as Drawn) : String(node)
}
const squash = (text: string) => text.replace(/\s+/g, ' ').trim()
const gauges = [{ text: 'RAM 40%', color: '#5f875f' }, { text: 'CPU 12%', color: 'warning' }]

/**
 * The header bar's right row, each part as its class or, for a button, its text and pressed state, and the text of the
 * gauges the row draws, with the pill's title.
 */
function row() {
  const [drawn] = find(draw(Header()), 'side-row')
  const parts = (drawn.props.children as Drawn[]).flat()
    .filter((one): one is { props: Record<string, unknown> } => typeof one === 'object' && one !== null)
    .map(one => (one.props.class === 'gauges' ? 'gauges'
      : `${squash(textOf(one))} ${String(one.props['aria-pressed'])}`))
  const pills = find(drawn, 'gauges')
  return { parts, gauges: pills.map(one => squash(textOf(one))), title: pills.map(one => one.props.title) }
}
const title = ["Show what uses the machine's resources"]

test('with no sidebar, the row draws the gauges, New Issue, History, Runners, Chat, GitHub, and help, none pressed', () => {
  ui.server = { desks: [], gauges }
  assert.deepEqual(row(), { parts: ['gauges', 'New Issue i undefined', 'History h false', 'Runners \\ false',
    'Chat ` false', 'GitHub g false', '? undefined'],
    gauges: ['RAM 40% | CPU 12%'], title })
})

test('with History shown, the pill keeps the gauges and History is pressed', () => {
  ui.server = { desks: [], gauges }
  ui.side = 'history'
  assert.deepEqual(row(), { parts: ['gauges', 'New Issue i undefined', 'History h true', 'Runners \\ false',
    'Chat ` false', 'GitHub g false', '? undefined'],
    gauges: ['RAM 40% | CPU 12%'], title })
})

test("with Runners shown, the pill keeps the gauges in their colors, and the sidebar's head draws none", () => {
  ui.server = { desks: [], gauges }
  ui.side = 'runners'
  assert.deepEqual(row(), { parts: ['gauges', 'New Issue i undefined', 'History h false', 'Runners \\ true',
    'Chat ` false', 'GitHub g false', '? undefined'],
    gauges: ['RAM 40% | CPU 12%'], title })
  const colors = find(draw(Header()), 'gauges').flatMap(one => (one.props.children as Drawn[]).flat())
    .filter((one): one is { props: Record<string, unknown> } => typeof one === 'object' && one !== null)
    .map(one => one.props.style)
  assert.deepEqual(colors, [{ color: 'var(--gauge-ok)' }, undefined, { color: 'var(--warning)' }])
  const side = draw(Runners({ shown: new Set() }))
  assert.deepEqual(find(side, 'gauges'), [])
  assert.equal(squash(textOf(find(side, 'side-head'))), 'Runners')
})

// A project whose board reads `notices`, one GitHub row each in its tone, a failing one in the warning tone.
function githubDesk(id: string, notices: string[]) {
  return { id, title: id, board: { error: null, needs: [], github: notices.map((notice, at) => ({ key: `github-${at}`,
    title: `row ${at}`, note: notice, notice })) } }
}

test("the GitHub button counts the failing rows in the warning tone, whether or not the sidebar shows", () => {
  ui.server = { desks: [githubDesk('game', ['warning', 'muted']), githubDesk('tools', ['warning'])], gauges }
  const counter = () => find(find(draw(Header()), 'side-row'), 'counter').map(one => [one.props.class,
    squash(textOf(one as Drawn))])
  assert.deepEqual(row().parts.slice(-2), ['GitHub 2 g false', '? undefined'])
  assert.deepEqual(counter(), [['counter warning', '2']])
  ui.side = 'github'
  assert.deepEqual(row().parts.slice(-2), ['GitHub 2 g true', '? undefined'])
  assert.deepEqual(counter(), [['counter warning', '2']])
  // A project's tab counts only its own rows.
  ui.tab = 'tools'
  assert.deepEqual(counter(), [['counter warning', '1']])
  ui.server = { desks: [githubDesk('game', ['muted'])], gauges }
  ui.tab = 'all'
  assert.deepEqual(counter(), [])
})

test('the GitHub button shows the GitHub sidebar in place of the others, hides it again, and remembers each', () => {
  ui.server = { desks: [], gauges }
  ui.side = 'runners'
  const saved: [string, string][] = []
  Object.assign(globalThis, { localStorage: { setItem: (key: string, value: string) => saved.push([key, value]) } })
  const github = () => ((find(draw(Header()), 'side-row')[0]!.props.children as Drawn[]).flat()
    .filter((one): one is { props: Record<string, unknown> } => typeof one === 'object' && one !== null
      && squash(textOf(one)).startsWith('GitHub')))[0]!
  ;(github().props.onClick as () => void)()
  assert.equal(ui.side, 'github')
  ;(github().props.onClick as () => void)()
  assert.equal(ui.side, null)
  assert.deepEqual(saved, [['side-shown', '"github"'], ['side-shown', 'null']])
  Reflect.deleteProperty(globalThis, 'localStorage')
})

test('the gauges pill is a button that shows the Resources view, pressed while it shows, and closes it again', t => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  const asked: string[] = []
  Object.assign(globalThis, { fetch: async (path: string) => {
    asked.push(path)
    return { ok: true, status: 200, json: async () => ({}) }
  } })
  ui.server = { desks: [], gauges }
  const pill = () => find(draw(Header()), 'gauges')[0] as { type: string; props: Record<string, unknown> }
  assert.equal(pill().type, 'button')
  assert.equal(pill().props['aria-pressed'], 'false')
  ;(pill().props.onClick as () => void)()
  assert.equal(ui.resources.open, true)
  assert.deepEqual(asked, ['/api/resources'])
  assert.equal(pill().props['aria-pressed'], 'true')
  ;(pill().props.onClick as () => void)()
  assert.equal(ui.resources.open, false)
})

test('the bar draws no pill before the server sends gauges', () => {
  ui.server = { desks: [] }
  assert.deepEqual(row().parts, ['New Issue i undefined', 'History h false', 'Runners \\ false', 'Chat ` false',
    'GitHub g false', '? undefined'])
})

test('the restart notice takes a line under the page\'s name, and the Runners sidebar draws none', () => {
  ui.server = { desks: [], gauges, code: 'Restart the web server.' }
  ui.side = 'runners'
  const [drawn] = find(draw(Header()), 'brand')
  const classes = (drawn.props.children as Drawn[]).flat()
    .filter((one): one is { props: Record<string, unknown> } => typeof one === 'object' && one !== null)
    .map(one => one.props.class)
  assert.deepEqual(classes, ['name', 'code-notice warning'])
  assert.deepEqual(find(find(draw(Header()), 'meters'), 'code-notice'), [])
  assert.deepEqual(find(draw(Runners({ shown: new Set() })), 'code-notice'), [])
})

test('the gauges split into memory and swap, then CPU, then the rest, leaving out empty groups', () => {
  const gauge = (text: string) => ({ text, color: 'warning' })
  assert.deepEqual(gaugeGroups([gauge('CPU 12%'), gauge('GPU 3%'), gauge('RAM 40%'), gauge('swap 0%')]),
    [[gauge('RAM 40%'), gauge('swap 0%')], [gauge('CPU 12%')], [gauge('GPU 3%')]])
  assert.deepEqual(gaugeGroups([gauge('CPU 12%')]), [[gauge('CPU 12%')]])
  assert.deepEqual(gaugeGroups([]), [])
})

test("a project's mode is its own or the desk's", () => {
  const hold = { state: 'running', projects: { 'me/game': 'manual' } }
  assert.equal(modeOf(hold, { repo: 'me/game' }), 'manual')
  assert.equal(modeOf(hold, { repo: 'me/tools' }), 'running')
})

test("a button's tooltip has the room from the button's left edge to the window's right margin", () => {
  assert.equal(tipRoom(16, 390), '358px')
  assert.equal(tipRoom(214.4, 390), '160px')
  assert.equal(tipRoom(600, 1400), '784px')
})

const desks = [{ id: 'game', repo: 'me/game' }, { id: 'tools', repo: 'me/tools' }]
const pill = (id: string) => {
  const { props } = tabMode(id)
  return [props.class, props.children]
}

test("a project's tab names its mode while every project shares it, and All names none", () => {
  ui.server = { desks, hold: { state: 'running', projects: {} } }
  assert.deepEqual(pill('game'), ['tab-mode running', 'Auto'])
  assert.deepEqual(pill('tools'), ['tab-mode running', 'Auto'])
  assert.equal(tabMode('all'), null)
})

test("a project's tab names its own mode while the projects' modes differ", () => {
  ui.server = { desks, hold: { state: 'running', projects: { 'me/game': 'held', 'me/tools': 'manual' } } }
  assert.deepEqual(pill('game'), ['tab-mode held', 'Hold'])
  assert.deepEqual(pill('tools'), ['tab-mode manual', 'Manual'])
  assert.equal(tabMode('all'), null)
})

test('no tab names a mode before the server says it, or for a project it does not list', () => {
  ui.server = { desks }
  assert.equal(tabMode('game'), null)
  ui.server = { desks, hold: { state: 'running', projects: {} } }
  assert.equal(tabMode('gone'), null)
})

test("the restart notice leaves the notices under the bar, and the store's and boards' errors stay", () => {
  const desk = { id: 'game', title: 'Game', board: { error: 'The store is locked' } }
  ui.server = { code: 'Restart the web server.', store: 'locked', desks: [desk] }
  assert.deepEqual([...headerNotices([desk])], [
    ["The store can't be read: locked. Changes show again once it can.", 'error'],
    ['Game: The store is locked', 'error'],
  ])
  ui.server = { code: 'Restart the web server.', desks: [] }
  assert.deepEqual([...headerNotices([])], [])
})

test('a stopped merge queue in Needs You and main ahead of origin in Merging show as notices in their tones', () => {
  const stop = 'merge queue stopped: the main checkout has uncommitted changes to README.md, which #275 changes. '
    + 'Commit, stash, or undo them.'
  const desk = { id: 'demo', title: 'demo', board: {
    needs: [{ key: 'merge-stopped', text: stop, notice: 'warning', actions: [] },
      { key: 'pr-12', text: 'review  Fix', actions: ['open'] }],
    merging: [{ key: 'merging-275', text: 'Fix  (README.md uncommitted)', state: 'stopped', actions: ['open'] },
      { key: 'origin', text: 'main is 2 commits ahead of origin', notice: 'muted', actions: [] }] } }
  ui.server = { desks: [desk] }
  assert.deepEqual([...headerNotices([desk])], [[`demo: ${stop}`, 'warning'],
    ['demo: main is 2 commits ahead of origin', 'muted']])
  // The stop needs you, so the project's tab and the page's title count it beside the review's card.
  const [count] = find(draw(Header()), 'count')
  assert.equal(squash(textOf(count)), '2')
  assert.equal(document.title, '(2) agent-desk')
})

/** The header bar's parts from the left, each as its tag and class or, for a button, its accessible name. */
function bar() {
  const [drawn] = find(draw(Header()), 'bar')
  return (drawn.props.children as Drawn[]).flat()
    .filter((one): one is { type: unknown; props: Record<string, unknown> } => typeof one === 'object' && one !== null)
    .map(one => (one.type === 'button' ? `button ${String(one.props['aria-label'])}`
      : `${String(one.type)}.${String(one.props.class)}`))
}
const refresh = () => find(draw(Header()), 'refresh')[0].props

test('the bar reads the name, the Refresh icon button, the status, then the mode buttons', () => {
  ui.connected = true
  ui.server = { desks: [{ id: 'game', repo: 'me/game', title: 'Game', board: { at: '12:04', needs: [] } }],
    hold: { state: 'running', projects: {} } }
  assert.deepEqual(bar(), ['span.brand', 'button Refresh', 'span.muted', 'span.hold', 'span.meters'])
  const [status] = find(draw(Header()), 'muted')
  assert.equal(squash(textOf(status)), 'Updated 12:04')
  assert.equal(refresh()['data-tip'], 'Refresh')
  const [button] = find(draw(Header()), 'refresh')
  assert.equal(squash(textOf(button)), '', 'the button shows no text')
  assert.equal(find(button, 'icon').length, 1, 'the button shows its icon')
})

test("the Refresh icon spins and the button waits while a shown project's board reads, and the status keeps its time",
  () => {
    ui.connected = true
    const game = { id: 'game', repo: 'me/game', title: 'Game', board: { at: '12:04', needs: [] } }
    ui.server = { desks: [game], hold: { state: 'running', projects: {} } }
    assert.equal(refresh().class, 'refresh')
    assert.equal(refresh().disabled, false)
    ui.server = { ...ui.server, desks: [{ ...game, refreshing: true }] }
    assert.equal(refresh().class, 'refresh spinning')
    assert.equal(refresh().disabled, true)
    assert.equal(squash(textOf(find(draw(Header()), 'muted')[0])), 'Updated 12:04')
  })

test('each mode button names its mode for the CSS, and on All with mixed modes none is pressed and no text explains it',
  () => {
    ui.connected = true
    const boards = desks.map(one => ({ ...one, title: one.id, board: null }))
    ui.server = { desks: boards,
      hold: { state: 'running', projects: { 'me/game': 'held', 'me/tools': 'manual' } } }
    const [group] = find(draw(Header()), 'modes')
    const buttons = (group.props.children as Drawn[]).flat()
      .filter((one): one is { props: Record<string, unknown> } => typeof one === 'object' && one !== null)
    assert.deepEqual(buttons.map(one => [one.props['data-mode'], one.props['aria-pressed']]),
      [['running', 'false'], ['manual', 'false'], ['held', 'false']])
    const [hold] = find(draw(Header()), 'hold')
    assert.equal(squash(textOf(hold)), 'Auto Manual Hold')
    ui.tab = 'game'
    const [pressed] = find(draw(Header()), 'modes').flatMap(one => (one.props.children as Drawn[]).flat())
      .filter((one): one is { props: Record<string, unknown> } => typeof one === 'object' && one !== null
        && one.props['aria-pressed'] === 'true')
    assert.equal(pressed.props['data-mode'], 'held')
  })

test('the billing control sits beside the mode buttons and shows the sole account on All and on its project\'s tab',
  () => {
    ui.connected = true
    ui.server = { desks: [{ id: 'game', repo: 'me/game', title: 'Game', board: { at: '12:04', needs: [] },
      issueRunner: { config: 'claude', override: null, effective: 'claude' } }],
      hold: { state: 'running', projects: {} },
      usage: { on: true, limit: 80, percent: 81.2, readAt: 1, resetsAt: null, problem: null },
      account: { account: 'subscription', spent: 63.2, budget: { on: true, dollars: 200 }, held: false,
        key: { set: false, ending: null } } }
    assert.deepEqual(bar(), ['span.brand', 'button Refresh', 'span.muted', 'span.hold', 'span.usage', 'span.meters'])
    const control = () => {
      const [button] = find(draw(Header()), 'usage-button')
      return { text: squash(textOf(button)), class: button.props.class, tip: button.props['data-tip'],
        described: button.props['aria-description'],
        expanded: button.props['aria-expanded'], popup: button.props['aria-haspopup'] }
    }
    const all = control()
    assert.deepEqual(all, { text: 'Claude Weekly 18% left', class: 'usage-button over',
      tip: undefined, described: 'Claude. Weekly 18% left.', expanded: 'false', popup: 'dialog' })
    ui.tab = 'game'
    assert.deepEqual(control(), all)
  })

test('the bar draws no usage limit before the server says its state', () => {
  ui.server = { desks: [], hold: { state: 'running', projects: {} } }
  assert.deepEqual(bar(), ['span.brand', 'button Refresh', 'span.offline', 'span.hold', 'span.meters'])
})
