// Run with: node --test web/
// Tests of the routes that change a project's start order or list order through the server: Move Up, Move Down, Move
// to Top, and Move to Bottom on a queued card, a drop that places it, and Queued's Sort, and a drop and the Sort in the
// groups that follow the list order. Each runs agent-desk against the test's store.
// First, so the server's modules read the temporary folders it sets.
import './test-env.ts'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'

import { loop, rebuild } from './server.ts'
import { postTo } from './server-fixture.ts'
import { desks, host } from './state.ts'
import type { Desk } from './state.ts'
import { storeFixture } from './store-fixture.ts'
import { storePath } from './store.ts'

test('Move Up, Move Down, Move to Top, Move to Bottom, and a drop on a queued card move its issue in the store\'s start '
  + 'order, and a move the store refuses changes nothing', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-loft-'))
  storeFixture(['p = "me/loft"', 'store.create(p, "issue", "Paint the barn", labels=["agent-ready"])',
    'w = store.create(p, "issue", "Fix the door")', 'store.set_state(p, w, "waiting")',
    'store.create(p, "issue", "Oil the hinge", labels=["agent-ready"])',
    'store.create(p, "issue", "Sweep the loft", labels=["agent-ready"])'].join('\n'), storePath())
  const desk = { id: 'loft', repo: 'me/loft', owner: 'me', title: 'Loft', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('loft', desk)
  const real = host.run
  const asked: string[][] = []
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    asked.push(argv)
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/loft' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const order = () => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return db.prepare('SELECT number FROM items WHERE project = ? AND rank IS NOT NULL ORDER BY rank').all('me/loft')
        .map(row => row.number)
    } finally {
      db.close()
    }
  }
  const keys = () => (desk.board?.queued ?? []).map(row => row.key)
  const press = async (key: string, action: string) => {
    assert.equal((await postTo('/api/act', { desk: 'loft', key, action })).status, 202, `${action} ${key}`)
    await settled()
  }
  try {
    await rebuild(desk)
    assert.deepEqual(keys(), ['queued-1', 'waiting-2', 'queued-3', 'queued-4'])
    // The first card offers no Move Up or Move to Top, and the last no Move Down or Move to Bottom.
    assert.equal((await postTo('/api/act', { desk: 'loft', key: 'queued-1', action: 'up' })).status, 409)
    assert.equal((await postTo('/api/act', { desk: 'loft', key: 'queued-4', action: 'down' })).status, 409)
    await press('queued-4', 'top')
    assert.deepEqual(order(), [4, 1, 2, 3])
    await press('queued-1', 'down')
    await press('queued-3', 'up')
    assert.deepEqual(order(), [4, 2, 3, 1])
    assert.deepEqual(keys(), ['queued-4', 'waiting-2', 'queued-3', 'queued-1'])
    assert.deepEqual(desk.board?.queued.map(row => row.actions.slice(3)), [['down', 'bottom'],
      ['up', 'down', 'top', 'bottom'],
      ['up', 'down', 'top', 'bottom'], ['up', 'top']])
    assert.ok(asked.some(argv => argv.slice(-4).join(' ') === 'queue order 4 --top'))
    // Move to Bottom puts the card last, and the last card refuses it.
    assert.equal((await postTo('/api/act', { desk: 'loft', key: 'queued-1', action: 'bottom' })).status, 409)
    await press('waiting-2', 'bottom')
    assert.deepEqual(order(), [4, 3, 1, 2])
    assert.ok(asked.some(argv => argv.slice(-4).join(' ') === 'queue order 2 --bottom'))
    await press('waiting-2', 'top')
    await press('queued-4', 'top')
    assert.deepEqual(order(), [4, 2, 3, 1])
    // A drop places the card at a number from 1, and one past the end puts it last.
    const drop = async (key: string, to: unknown) => (await postTo('/api/place', { desk: 'loft', key, to })).status
    assert.equal(await drop('queued-1', 1), 202)
    await settled()
    assert.deepEqual(order(), [1, 4, 2, 3])
    assert.deepEqual(keys(), ['queued-1', 'queued-4', 'waiting-2', 'queued-3'])
    assert.ok(asked.some(argv => argv.slice(-5).join(' ') === 'queue order 1 --to 1'))
    assert.equal(await drop('queued-1', 9), 202)
    await settled()
    assert.deepEqual(order(), [4, 2, 3, 1])
    // A drop with no place, or of a row outside Queued's start order, sends nothing.
    const sent = asked.length
    assert.deepEqual([await drop('queued-1', 0), await drop('queued-1', 'two'), await drop('next-1', 1),
      await drop('queued-9', 1)], [400, 400, 409, 409])
    assert.equal(asked.length, sent)
    // #3 started after the board's read, so the store refuses its move and the order stays.
    const db = new DatabaseSync(storePath())
    try {
      db.prepare("UPDATE items SET state = 'in-progress', rank = NULL WHERE project = ? AND number = 3").run('me/loft')
    } finally {
      db.close()
    }
    await press('queued-3', 'top')
    assert.deepEqual(order(), [4, 2, 1])
    assert.deepEqual(keys(), ['queued-4', 'waiting-2', 'queued-1'])
    // A drop the store refuses changes nothing in it either.
    const later = new DatabaseSync(storePath())
    try {
      later.prepare("UPDATE items SET state = 'in-progress', rank = NULL WHERE project = ? AND number = 1")
        .run('me/loft')
    } finally {
      later.close()
    }
    assert.equal(await drop('queued-1', 1), 202)
    await settled()
    assert.deepEqual(order(), [4, 2])
    assert.deepEqual(keys(), ['queued-4', 'waiting-2'])
  } finally {
    host.run = real
    desks.delete('loft')
    rmSync(main, { recursive: true, force: true })
  }
})

test('the Queued heading\'s Sort sorts the project\'s start order in the store, and a sort the store refuses toasts '
  + 'and leaves the store\'s order', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-granary-'))
  // By hand, #2 waits behind #1 in their serial group, and #4 sits ahead of its blocker #3.
  storeFixture(['p = "me/granary"',
    'store.create(p, "issue", "Hang the lantern", labels=["agent-ready", "serial:loft"])',
    'store.create(p, "issue", "Sand the loft floor", labels=["agent-ready", "serial:loft"])',
    'store.create(p, "issue", "Fix the ladder", labels=["agent-ready"])',
    'store.create(p, "issue", "Climb to the loft", labels=["agent-ready"], blocked_by=[3])',
    'store.move(p, 4, "top")', 'store.move(p, 2, "top")'].join('\n'), storePath())
  const desk = { id: 'granary', repo: 'me/granary', owner: 'me', title: 'Granary', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('granary', desk)
  const real = host.run
  const fail = loop.fail
  const asked: string[] = []
  const failed: string[] = []
  let refuse = false
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    asked.push(argv.slice(4).join(' '))
    if (refuse && argv.includes('--sort')) {
      return null
    }
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/granary' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  loop.fail = (_, text) => failed.push(text)
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const keys = () => (desk.board?.queued ?? []).map(row => row.key)
  const sort = async () => {
    const status = (await postTo('/api/sort-order', { desk: 'granary' })).status
    await settled()
    return status
  }
  try {
    await rebuild(desk)
    assert.deepEqual(keys(), ['queued-2', 'queued-4', 'queued-1', 'queued-3'])
    // The issues that can start now lead, by number, and the gated ones follow, each after its blocker.
    assert.equal(await sort(), 202)
    assert.ok(asked.includes('queue order --sort -R me/granary'))
    assert.deepEqual(keys(), ['queued-1', 'queued-3', 'queued-2', 'queued-4'])
    assert.deepEqual(failed, [])
    // A sort the store refuses toasts the way a refused move does, and the list shows the store's order.
    execFileSync(join(import.meta.dirname, '..', 'bin', 'agent-desk'), ['queue', 'order', '-R', 'me/granary', '4',
      '--top'], { env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..') }, stdio: 'pipe' })
    refuse = true
    assert.equal(await sort(), 202)
    assert.deepEqual(failed, ['Couldn\'t sort the start order; the list shows the store\'s order.'])
    assert.deepEqual(keys(), ['queued-4', 'queued-1', 'queued-3', 'queued-2'])
    // A project with fewer than two queued or waiting issues has no order to sort, and the server sends nothing.
    const sent = asked.length
    desk.board = desk.board && { ...desk.board, queued: desk.board.queued.slice(0, 1) }
    assert.equal(await sort(), 409)
    assert.equal(asked.length, sent)
  } finally {
    host.run = real
    loop.fail = fail
    desks.delete('granary')
    rmSync(main, { recursive: true, force: true })
  }
})

test('a drop in Backlog or Open moves its issue beside another of the group in the store\'s list order, the server '
  + 'refuses a drop beside an issue outside the row\'s group or for a row that left it, and a refused move toasts',
  async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-silo-'))
  // Each issue takes the top of the list order when it is filed, so Backlog lists #4 first and Open #6.
  storeFixture(['p = "me/silo"', 'for title in ["Patch the roof", "Clear the gutters", "Paint the door", '
    + '"Hang a vane"]:\n    store.create(p, "issue", title, labels=["backlog"])',
    'store.create(p, "issue", "Count the grain")', 'store.create(p, "issue", "Seal the hatch")'].join('\n'),
  storePath())
  const desk = { id: 'silo', repo: 'me/silo', owner: 'me', title: 'Silo', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('silo', desk)
  const real = host.run
  const fail = loop.fail
  const asked: string[] = []
  const failed: string[] = []
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    if (argv[4] === 'list') {
      asked.push(argv.slice(4).join(' '))
    }
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/silo' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  loop.fail = (_, text) => failed.push(text)
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const backlog = () => (desk.board?.backlog ?? []).map(row => row.key)
  const drop = async (key: string, beside: Record<string, unknown>) => {
    const status = (await postTo('/api/list-place', { desk: 'silo', key, ...beside })).status
    await settled()
    return status
  }
  try {
    await rebuild(desk)
    assert.deepEqual(backlog(), ['backlog-4', 'backlog-3', 'backlog-2', 'backlog-1'])
    assert.deepEqual(desk.board?.next.map(row => row.key), ['next-6', 'next-5'])
    // Dropped on the top half of #4, #1 lands first; dropped on the bottom half of #2, #3 lands last.
    assert.equal(await drop('backlog-1', { before: 4 }), 202)
    assert.deepEqual(backlog(), ['backlog-1', 'backlog-4', 'backlog-3', 'backlog-2'])
    assert.equal(await drop('backlog-3', { after: 2 }), 202)
    assert.deepEqual(backlog(), ['backlog-1', 'backlog-4', 'backlog-2', 'backlog-3'])
    assert.equal(await drop('next-5', { before: 6 }), 202)
    assert.deepEqual(desk.board?.next.map(row => row.key), ['next-5', 'next-6'])
    assert.deepEqual(asked, ['list order 1 --before 4', 'list order 3 --after 2', 'list order 5 --before 6'])
    assert.deepEqual(failed, [])
    // A drop beside an issue of another group, or of a row the board doesn't show in its group, sends nothing, as
    // does one beside the issue itself, beside no issue, or both before and after one.
    asked.length = 0
    assert.deepEqual([await drop('next-5', { before: 4 }), await drop('backlog-5', { before: 4 }),
      await drop('queued-1', { before: 4 }), await drop('backlog-9', { after: 4 })], [409, 409, 409, 409])
    assert.deepEqual([await drop('backlog-1', { before: 1 }), await drop('backlog-1', {}),
      await drop('backlog-1', { before: 'four' }), await drop('backlog-1', { before: 4, after: 2 })],
    [400, 400, 400, 400])
    // A row that left its group since the board's read, as when #2 moved to Open, is refused the same way.
    const shown = desk.board
    desk.board = shown && { ...shown, backlog: shown.backlog.filter(row => row.key !== 'backlog-2') }
    assert.equal((await postTo('/api/list-place', { desk: 'silo', key: 'backlog-2', before: 1 })).status, 409)
    desk.board = shown
    assert.deepEqual(asked, [])
    // #4 closed after the board's read, so the store refuses the drop beside it, which toasts and puts #3 back.
    const db = new DatabaseSync(storePath())
    try {
      db.prepare("UPDATE items SET state = 'closed' WHERE project = ? AND number = 4").run('me/silo')
    } finally {
      db.close()
    }
    assert.equal(await drop('backlog-3', { after: 4 }), 202)
    assert.deepEqual(asked, ['list order 3 --after 4'])
    assert.deepEqual(failed, ['Couldn\'t move #3 in the list order: it may have closed or been queued.'])
    assert.deepEqual(backlog(), ['backlog-1', 'backlog-2', 'backlog-3'])
  } finally {
    host.run = real
    loop.fail = fail
    desks.delete('silo')
    rmSync(main, { recursive: true, force: true })
  }
})

test('the Sort button of Open, Blocked, Needs Design, Backlog, or Ideas puts the group back in number order, highest '
  + 'first, and a sort the store refuses toasts', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-mill-'))
  storeFixture(['p = "me/mill"', 'for title in ["Grease the wheel", "Mend the sail", "Sweep the floor"]:\n'
    + '    store.create(p, "issue", title, labels=["idea"])', 'store.create(p, "issue", "Turn the stones")',
  'store.list_move(p, 1, where="top")', 'store.list_move(p, 3, where="bottom")'].join('\n'), storePath())
  const desk = { id: 'mill', repo: 'me/mill', owner: 'me', title: 'Mill', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('mill', desk)
  const real = host.run
  const fail = loop.fail
  const asked: string[] = []
  const failed: string[] = []
  let refuse = false
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    if (argv[4] === 'list') {
      asked.push(argv.slice(4).join(' '))
      if (refuse) {
        return null
      }
    }
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/mill' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  loop.fail = (_, text) => failed.push(text)
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const ideas = () => (desk.board?.ideas ?? []).map(row => row.key)
  const sort = async (group: unknown) => {
    const status = (await postTo('/api/sort-list', { desk: 'mill', group })).status
    await settled()
    return status
  }
  try {
    await rebuild(desk)
    assert.deepEqual(ideas(), ['idea-1', 'idea-2', 'idea-3'])
    assert.equal(await sort('idea'), 202)
    assert.deepEqual(asked, ['list order --reset 1 2 3'])
    assert.deepEqual(ideas(), ['idea-3', 'idea-2', 'idea-1'])
    assert.deepEqual(failed, [])
    // A group the page doesn't sort, or one with fewer than two issues in the list order, sends nothing.
    asked.length = 0
    assert.deepEqual([await sort('queued'), await sort('toString'), await sort('open'), await sort('backlog')],
      [400, 400, 409, 409])
    assert.deepEqual(asked, [])
    // A sort the store refuses toasts with the group's heading, and the list shows the store's order.
    refuse = true
    assert.equal(await sort('idea'), 202)
    assert.deepEqual(failed, ['Couldn\'t sort Ideas; the list shows the store\'s order.'])
    assert.deepEqual(ideas(), ['idea-3', 'idea-2', 'idea-1'])
  } finally {
    host.run = real
    loop.fail = fail
    desks.delete('mill')
    rmSync(main, { recursive: true, force: true })
  }
})
