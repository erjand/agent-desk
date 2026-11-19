// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { actionLabel, byNumber, clickedItem, firstTab, FORWARD, githubRows, hotkeyOf, itemHref, LABELS, listedCard,
  runnerProjects, select, showRun, slotRows, startCollapsed, toggleSide, ui } from './app-state.js'

const card = (desk: string, number: number) => ({ id: `${desk}:${number}`, desk, number })

test('a group sorts by issue number, highest first, or lowest first when flipped', () => {
  const cards = [card('game', 12), card('game', 40), card('game', 3)]
  assert.deepEqual(byNumber(cards, false).map(one => one.number), [40, 12, 3])
  assert.deepEqual(byNumber(cards, true).map(one => one.number), [3, 12, 40])
  assert.deepEqual(cards.map(one => one.number), [12, 40, 3])
})

test('on All, each project keeps its cards together in project order and sorts them within', () => {
  const cards = [card('app', 5), card('app', 9), card('game', 40), card('game', 2), card('tools', 7)]
  assert.deepEqual(byNumber(cards, false).map(one => one.id), ['app:9', 'app:5', 'game:40', 'game:2', 'tools:7'])
  assert.deepEqual(byNumber(cards, true).map(one => one.id), ['app:5', 'app:9', 'game:2', 'game:40', 'tools:7'])
})

test('a plain left click on a link to an item on the page names it, and a modified click or other link none', () => {
  Object.assign(globalThis, { location: new URL('http://localhost:7480/') })
  ui.server = { desks: [{ id: 'game', cards: [] }] }
  const click = (extra = {}) => ({ defaultPrevented: false, button: 0, ctrlKey: false, metaKey: false, shiftKey: false,
    altKey: false, ...extra })
  const href = new URL(itemHref('game', 41), location.href).href
  assert.equal(href, 'http://localhost:7480/#game:41')
  assert.deepEqual(clickedItem(click(), href), { desk: 'game', number: 41 })
  for (const extra of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 },
    { defaultPrevented: true }]) {
    assert.equal(clickedItem(click(extra), href), null)
  }
  assert.equal(clickedItem(click(), 'http://localhost:7480/#other:41'), null)
  assert.equal(clickedItem(click(), 'https://github.com/org/game/issues/41'), null)
})

test('the list\'s card for an item matches its issue or PR, and a merged card the list leaves out matches none', () => {
  ui.server = { desks: [{ id: 'game', cards: [{ id: 'game:1', stage: 'needs', number: 1, pr: 3 },
    { id: 'game:4', stage: 'merged', number: 4, pr: 5 }, { id: 'game:pr-6', stage: 'merging', number: 6, pr: 6 }] }] }
  assert.equal(listedCard('game', 1)?.id, 'game:1')
  assert.equal(listedCard('game', 3)?.id, 'game:1')
  assert.equal(listedCard('game', 6)?.id, 'game:pr-6')
  assert.equal(listedCard('game', 4), undefined)
  assert.equal(listedCard('game', 5), undefined)
  assert.equal(listedCard('other', 1), undefined)
})

test('a failed run\'s card shows its Agent Log when the run worked an issue, and its first tab otherwise', () => {
  Object.assign(ui, { closed: true, detailTab: 'issue', confirming: null })
  showRun({ id: 'game:run-7', desk: 'game', number: 41, run: 7 })
  assert.equal(ui.selected, 'game:run-7')
  assert.equal(ui.closed, false)
  assert.equal(ui.detailTab, 'log')
  showRun({ id: 'game:run-8', desk: 'game', number: 52, pr: 52, run: 8 })
  assert.equal(ui.selected, 'game:run-8')
  assert.equal(ui.detailTab, null)
  showRun({ id: 'game:run-9', desk: 'game', run: 9 })
  assert.equal(ui.selected, 'game:run-9')
  assert.equal(ui.detailTab, null)
})

test('a card in Needs You or Merging with a review opens on Review, and any other card on Issue', () => {
  assert.equal(firstTab({ stage: 'needs' }, true), 'review')
  assert.equal(firstTab({ stage: 'merging' }, true), 'review')
  assert.equal(firstTab({ stage: 'open' }, true), 'issue')
  assert.equal(firstTab({ stage: 'working' }, true), 'issue')
  assert.equal(firstTab({ stage: 'needs' }, false), 'issue')
  assert.equal(firstTab({ stage: 'merging' }, false), 'issue')
  assert.equal(firstTab({ stage: 'open' }, false), 'issue')
  // Selecting a card, as a click, j and k, or a link does, goes back to its first tab.
  ui.detailTab = 'files'
  select('game:41')
  assert.equal(ui.detailTab, null)
})

test('the GitHub group lists each project\'s main and branch rows, healthy, waiting, or failed, and the backup once',
  () => {
  const row = (key: string, note: string, notice = 'muted') => ({ key, title: key.slice(7), note, notice, actions: [] })
  const backup = row('github-backup', 'last commit Nov 10 11:50, last push Nov 10 11:10')
  const game = { id: 'game', board: { error: null, github: [row('github-main', 'matches origin'),
    row('github-agent-branches', '10 pushed, 2 wait for the next push'), backup] } }
  const tools = { id: 'tools', board: { error: null, github: [
    row('github-main', '1 commit ahead of origin; push failed: down; retrying in 30s', 'warning'),
    row('github-agent-branches', "3 pushed; couldn't push agent/issue-8: [rejected]", 'warning'), backup] } }
  // A project whose host is none has the backup row alone, and a board stored before the group has none.
  const local = { id: 'local', board: { error: null, github: [backup] } }
  const older = { id: 'older', board: { error: null } }
  const shown = githubRows([game, tools, local, older])
  assert.deepEqual(shown.map(({ desk, row }) => [desk?.id ?? null, row.key, row.notice]), [
    ['game', 'github-main', 'muted'], ['game', 'github-agent-branches', 'muted'],
    ['tools', 'github-main', 'warning'], ['tools', 'github-agent-branches', 'warning'],
    [null, 'github-backup', 'muted']])
  assert.deepEqual(githubRows([older]), [])
  assert.deepEqual(githubRows([local]).map(({ desk, row }) => [desk, row.note]),
    [[null, 'last commit Nov 10 11:50, last push Nov 10 11:10']])
})

test('the sidebar shows the tab\'s project, or every project on All in page order, with its slots and sessions', () => {
  const row = (key: string, runner: string) => ({ key, text: key, runner, title: key, actions: [] })
  const board = (runners: object[], checks: object[], slots: object[]) => ({ error: null, runners, checks, slots })
  const game = { id: 'game', board: board([row('unit-1', 'agent'), row('unit-2', 'fix'), row('local-7', 'local'),
    row('unit-3', 'chore')], [row('check-a', 'check')], [{ kind: 'agent', busy: 1, total: 3 },
    { kind: 'chore', busy: 1, total: 1 }, { kind: 'fix', busy: 1, total: 1 }, { kind: 'check', busy: 1, total: 2 }]) }
  const tools = { id: 'tools', board: board([], [], [{ kind: 'agent', busy: 0, total: 3 }]) }
  const loading = { id: 'later', board: null }
  const broken = { id: 'broken', board: { ...board([], [], []), error: 'no store' } }
  ui.server = { desks: [game, loading, tools, broken] }
  ui.tab = 'all'
  const shown = runnerProjects()
  assert.deepEqual(shown.map(one => one.desk), ['game', 'tools'])
  assert.deepEqual(shown[0]?.slots.map(one => `${one.kind} ${one.tone}`),
    ['agent busy', 'chore full', 'fix full', 'check busy'])
  // The busy agent gets a row, as the chore, fix, and check do; the local session takes no slot.
  assert.deepEqual(shown[0]?.rows.map(one => `${one.kind} ${one.row?.key ?? (one.busy ? 'busy' : 'free')}`),
    ['agent unit-1', 'agent free', 'agent free', 'chore unit-3', 'fix unit-2', 'check check-a', 'check free'])
  assert.deepEqual(shown[0]?.locals.map(one => one.key), ['local-7'])
  assert.deepEqual(shown[1]?.rows.map(one => one.busy), [false, false, false])
  assert.deepEqual(shown[1]?.locals, [])
  ui.tab = 'tools'
  assert.deepEqual(runnerProjects().map(one => one.desk), ['tools'])
  ui.tab = 'later'
  assert.deepEqual(runnerProjects(), [])
  ui.tab = 'all'
})

test('a kind\'s busy slots lead in board order, then its free ones, and a kind at 0 has none', () => {
  const row = (key: string, runner: string, labels?: string[]) => ({ key, runner, ...(labels ? { labels } : {}) })
  const shape = (rows: { kind: string, busy: boolean, row?: { key: string } }[]) => rows
    .map(one => `${one.kind} ${one.row?.key ?? (one.busy ? 'busy' : 'free')}`)
  const runners = [row('fix-1', 'fix'), row('agent-1', 'agent'), row('agent-2', 'agent', ['benchmark']),
    row('check-1', 'check'), row('local-1', 'local')]

  // Busy rows lead each kind in the order the board lists them, and free ones fill to the total.
  assert.deepEqual(shape(slotRows([{ kind: 'agent', busy: 2, total: 3 }, { kind: 'check', busy: 1, total: 2 }],
    runners)), ['agent agent-1', 'agent agent-2', 'agent free', 'check check-1', 'check free'])

  // A kind counting more busy runs than rows match shows the rest busy with no detail, never free.
  assert.deepEqual(shape(slotRows([{ kind: 'chore', busy: 2, total: 3 }, { kind: 'fix', busy: 2, total: 2 }],
    runners)), ['chore busy', 'chore busy', 'chore free', 'fix fix-1', 'fix busy'])

  // A kind set to 0 has no slots on the board, so it has no rows, and an empty board none at all.
  assert.deepEqual(shape(slotRows([{ kind: 'fix', busy: 1, total: 1 }], runners)), ['fix fix-1'])
  assert.deepEqual(slotRows([], runners), [])

  // A benchmark slot's rows are the agents' on benchmark issues.
  assert.deepEqual(shape(slotRows([{ kind: 'benchmark', busy: 1, total: 2 }], runners)),
    ['benchmark agent-2', 'benchmark free'])

  // More runs than slots, after the config shrinks, show a row for each and none free.
  assert.deepEqual(shape(slotRows([{ kind: 'agent', busy: 3, total: 2 }], [...runners, row('agent-3', 'agent')])),
    ['agent agent-1', 'agent agent-2', 'agent agent-3'])
})

test('Runners and History share one place: showing one hides the other, and the choice is remembered', () => {
  const stored = new Map<string, string>()
  Object.assign(globalThis, { localStorage: { getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value) } })
  ui.side = null
  toggleSide('runners')
  assert.equal(ui.side, 'runners')
  toggleSide('history')
  assert.equal(ui.side, 'history')
  assert.equal(stored.get('side-shown'), '"history"')
  toggleSide('history')
  assert.equal(ui.side, null)
  assert.equal(stored.get('side-shown'), 'null')
})

test('a letter key matches in either case, and other keys, chords, and keys typed in a field are left alone', () => {
  const on = (selector: string | null) => ({ closest: (wanted: string) => (selector !== null
    && wanted.split(', ').includes(selector) ? {} : null) })
  const press = (key: string, extra: object = {}) => hotkeyOf({ key, ctrlKey: false, metaKey: false, altKey: false,
    shiftKey: false, target: on(null), ...extra })
  assert.equal(press('m'), 'm')
  assert.equal(press('M', { shiftKey: true }), 'm')
  assert.equal(press('Y'), 'y')
  assert.equal(press('J'), 'j')
  assert.equal(press('L'), 'l')
  for (const key of ['?', '\\', '/', '1', '9', 'Enter', 'Escape', 'ArrowDown', 'ArrowLeft']) {
    assert.equal(press(key), key)
  }
  for (const extra of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
    assert.equal(press('M', extra), null)
    assert.equal(press('m', extra), null)
  }
  for (const field of ['input', 'textarea', 'select']) {
    assert.equal(press('M', { target: on(field) }), null)
  }
  assert.equal(press('Enter', { target: on('button') }), null)
  assert.equal(press('o', { target: on('button') }), 'o')
})

test('Needs Design, Backlog, and Ideas start collapsed, and a page that remembered its groups before Backlog '
  + 'collapses Backlog once', () => {
  assert.deepEqual([...startCollapsed(null, [])], ['design', 'backlog', 'idea'])
  const before = ['merging', 'needs', 'working', 'queued', 'open', 'blocked', 'design', 'idea']
  assert.deepEqual([...startCollapsed(['queued'], before)], ['queued', 'backlog'])
  assert.deepEqual([...startCollapsed(['queued'], [...before, 'backlog'])], ['queued'])
  assert.deepEqual([...startCollapsed([], [...before, 'backlog'])], [])
})

test('a stopped card names Take Over and Continue, with Continue its main button, and Resume only unpauses', () => {
  assert.equal(LABELS.resume, 'Take Over')
  assert.equal(LABELS.restart, 'Continue')
  assert.ok(FORWARD.has('restart'))
  assert.ok(!FORWARD.has('resume'))
  assert.deepEqual(Object.entries(LABELS).filter(([, label]) => label === 'Resume').map(([action]) => action),
    ['unpause'])
})

test('Retry reads Continue on a timed-out agent run whose session it carries on, and Retry on any other', () => {
  const failed = { id: 'game:run-9', actions: ['log', 'retry', 'dismiss'] }
  assert.equal(actionLabel(failed, 'retry'), 'Retry')
  assert.equal(actionLabel({ ...failed, continues: true }, 'retry'), 'Continue')
  assert.equal(actionLabel({ ...failed, continues: true }, 'dismiss'), 'Dismiss')
})
