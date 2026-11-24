// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { actionLabel, clickedItem, CONFIRMS, firstTab, foldAll, FORWARD, githubRows, hotkeyOf, INSTANT, itemHref,
  LABELS, listedCard, runnerProjects, select, showRun, slotRows, STAGES, startCollapsed, startFolds, toggleSide, ui }
  from './app-state.js'

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

test('a project set to 0 agent slots keeps its agent kind, with the agents still working and none free', () => {
  const row = (key: string, runner: string) => ({ key, text: key, runner, title: key, actions: [] })
  const agents = { count: 0, config: 3, override: true }
  const board = (runners: object[]) => ({ error: null, runners, checks: [], slots: [{ kind: 'chore', busy: 0,
    total: 1 }] })
  ui.server = { desks: [
    { id: 'game', agentSlots: agents, board: board([row('unit-1', 'agent'), row('local-7', 'local')]) },
    { id: 'tools', agentSlots: agents, board: board([]) }, { id: 'old', board: board([]) }] }
  ui.tab = 'all'
  const [game, tools, old] = runnerProjects()
  assert.deepEqual(game?.agents, agents)
  assert.deepEqual(game?.slots.map(one => `${one.kind} ${one.busy}/${one.total} ${one.tone}`),
    ['agent 1/0 full', 'chore 0/1 free'])
  assert.deepEqual(game?.rows.map(one => `${one.kind} ${one.row?.key ?? (one.busy ? 'busy' : 'free')}`),
    ['agent unit-1', 'chore free'])
  assert.deepEqual(tools?.rows.map(one => `${one.kind} ${one.busy ? 'busy' : 'free'}`), ['chore free'])
  assert.equal(tools?.slots[0]?.tone, 'full')
  // A server that sends no agent slots leaves the board's kinds as they are.
  assert.equal(old?.agents, null)
  assert.deepEqual(old?.slots.map(one => one.kind), ['chore'])
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

test('- folds every group on the shown tab, Chores and the empty ones too, + opens them all, and the page '
  + 'remembers each', () => {
  const stored = new Map<string, string>()
  Object.assign(globalThis, { localStorage: { getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value) } })
  ui.tab = 'all'
  ui.folds = new Map([['all', new Set(['design'])]])
  foldAll(true)
  const every = [...STAGES.map(([stage]) => stage), 'chores']
  assert.deepEqual([...ui.collapsed].sort(), [...every].sort())
  assert.deepEqual(JSON.parse(stored.get('inbox-folds') ?? 'null').all.sort(), [...every].sort())
  foldAll(false)
  assert.deepEqual([...ui.collapsed], [])
  assert.equal(stored.get('inbox-folds'), '{"all":[]}')
})

test('each tab keeps its own folded groups, and a tab never folded on starts as the page started', () => {
  const stored = new Map<string, string>()
  Object.assign(globalThis, { localStorage: { getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value) } })
  ui.folds = new Map()
  ui.tab = 'me/one'
  const first = [...ui.collapsed]
  foldAll(true)
  ui.tab = 'me/two'
  assert.deepEqual([...ui.collapsed], first)
  foldAll(false)
  ui.tab = 'me/one'
  assert.equal(ui.collapsed.size, STAGES.length + 1)
  ui.tab = 'all'
  assert.deepEqual([...ui.collapsed], first)
  const kept = JSON.parse(stored.get('inbox-folds') ?? 'null')
  assert.equal(kept['me/one'].length, STAGES.length + 1)
  assert.deepEqual(kept['me/two'], [])
})

test('the page reads the folds each tab kept, adds a folded group new since it last ran, and skips what it cannot '
  + 'read', () => {
  const before = ['merging', 'needs', 'working', 'queued', 'open', 'blocked', 'design', 'idea']
  const folds = startFolds({ all: ['queued'], 'me/one': [], 'me/two': 'queued' }, before)
  assert.deepEqual([...folds.keys()], ['all', 'me/one'])
  assert.deepEqual([...folds.get('all') ?? []], ['queued', 'backlog'])
  assert.deepEqual([...folds.get('me/one') ?? []], ['backlog'])
  assert.equal(startFolds(['queued'], before).size, 0)
  assert.equal(startFolds(null, before).size, 0)
})

test('-, +, and = reach the page, Shift held or not, and leave a field or a chord alone', () => {
  const field = { closest: (wanted: string) => (wanted.includes('input') ? {} : null) }
  const press = (key: string, extra: object = {}) => hotkeyOf({ key, ctrlKey: false, metaKey: false, altKey: false,
    shiftKey: false, target: { closest: () => null }, ...extra })
  assert.equal(press('-'), '-')
  assert.equal(press('='), '=')
  assert.equal(press('+', { shiftKey: true }), '+')
  for (const key of ['-', '+', '=']) {
    assert.equal(press(key, { target: field }), null)
    for (const extra of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
      assert.equal(press(key, extra), null)
    }
  }
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

test('every action asks to confirm or sits on the short list of those that act at once, and never both', () => {
  const actions = Object.keys(LABELS)
  assert.deepEqual(actions.filter(action => CONFIRMS.has(action) === INSTANT.has(action)), [])
  assert.deepEqual([...CONFIRMS, ...INSTANT].filter(action => !actions.includes(action)), [])
  assert.deepEqual([...INSTANT].sort(), ['bottom', 'down', 'resume', 'top', 'up'])
})
