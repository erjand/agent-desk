// Run with: node --test web/
// First, so the server's modules read the temporary folders it sets.
import { TEST_HOME } from './test-env.ts'
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, utimesSync, writeFileSync } from 'node:fs'
import { createServer, get } from 'node:http'
import type { AddressInfo } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join, posix } from 'node:path'
import { test } from 'node:test'

import { choreStats, repeatsOf, SCHEDULE_BRANCH, scheduleWorktree, steps, withStarting } from './chores.ts'
import type { Board, Row, RowAction } from '../inbox/types'
import { DESK } from '../inbox/hooks/run.ts'
import { CONFIRMS, FORWARD, KEYS, LABELS } from './app-state.js'
import { act } from './card-actions.ts'
import { catchUp, gpuGauges, handle, loop, parseProjects, servedProjects, splitBeats, STATIC, transcriptFolder }
  from './server.ts'
import { appName, appsOf, claimsOf, inDeskUnit, processesOf, TOP_PROCESSES, topOf, unitUsage, useProc }
  from './resources.ts'
import type { Resources } from './resources.ts'
import { getFrom, postTo } from './server-fixture.ts'
import { desks, host } from './state.ts'
import type { Desk } from './state.ts'
import { storeFixture } from './store-fixture.ts'
import { hashedColor, lastEvent, storePath, useStore } from './store.ts'

const HERE = import.meta.dirname

/** The files a page module imports by relative path, as paths within the web folder. */
function importsOf(file: string): string[] {
  const text = readFileSync(join(HERE, file), 'utf8')
  return [...text.matchAll(/^import [^'"]*['"](\.{1,2}\/[^'"]+)['"]/gm)]
    .map(found => posix.normalize(posix.join(posix.dirname(file), found[1] ?? '')))
}

test('the server serves the page\'s own files and every module the page imports, and nothing else', () => {
  const page = readFileSync(join(HERE, 'index.html'), 'utf8')
  const linked = [...page.matchAll(/(?:src|href)="([^"]+)"/g)].map(found => found[1] ?? '')
  const loaded = new Set<string>(['index.html'])
  const pending = [...linked]
  for (let file = pending.pop(); file !== undefined; file = pending.pop()) {
    if (!loaded.has(file)) {
      loaded.add(file)
      pending.push(...(file.endsWith('.js') ? importsOf(file) : []))
    }
  }
  assert.deepEqual(Object.values(STATIC).map(([file]) => file).sort(), [...loaded].sort())
  for (const [path, [file]] of Object.entries(STATIC)) {
    assert.equal(path, file === 'index.html' ? '/' : `/${file}`)
  }
})

test('the page\'s modules import one another without a cycle', () => {
  const walk = (file: string, above: string[]): void => {
    assert.ok(!above.includes(file), `import cycle: ${[...above, file].join(' → ')}`)
    for (const next of importsOf(file)) {
      walk(next, [...above, file])
    }
  }
  walk('app.js', [])
})

test('the projects file lists each project with its main checkout, which may hold spaces', () => {
  assert.deepEqual(parseProjects('org/game /home/me/game\n\nbad-line\norg/two /home/me/my game\n'), [
    { repo: 'org/game', path: '/home/me/game' },
    { repo: 'org/two', path: '/home/me/my game' },
  ])
})

test('the server serves the projects named, matched without regard to case, or every project when none are', () => {
  const listed = [{ repo: 'org/game', path: '/a' }, { repo: 'me/agent-desk-demo', path: '/b' }]
  assert.deepEqual(servedProjects(listed, []), listed)
  assert.deepEqual(servedProjects(listed, ['Me/Agent-Desk-Demo']), [{ repo: 'me/agent-desk-demo', path: '/b' }])
  assert.deepEqual(servedProjects(listed, ['org/other']), [])
})

test('the GPU gauges show each card\'s use and free memory, and none without nvidia-smi', () => {
  assert.deepEqual(gpuGauges('1, 3003, 16303\n'), [
    { text: 'GPU 1%', color: '#5f875f' },
    { text: 'VRAM 13G free', color: '#5f875f' },
  ])
  assert.deepEqual(gpuGauges('95, 15000, 16303\n75, 2000, 8192\n'), [
    { text: 'GPU0 95%', color: '#ff3b30' },
    { text: 'GPU0 VRAM 1.3G free', color: '#ff3b30' },
    { text: 'GPU1 75%', color: 'warning' },
    { text: 'GPU1 VRAM 6.0G free', color: '#5f875f' },
  ])
  assert.deepEqual(gpuGauges(''), [])
  assert.deepEqual(gpuGauges('[N/A], 3003, 16303\n'), [])
})

test('a chore the page started shows as starting until GitHub lists its run, or three minutes pass', () => {
  const board = { at: '', runners: [], needs: [], merging: [], design: [], designTotal: 0, queued: [], next: [], blocked: [],
    backlog: [], ideas: [], merged: [], chores: [], error: null, choreList: [
      { name: 'audit', every: '1d', last: 'done Mon Oct 26 08:41', next: 'Thu Oct 29 14:01' },
      { name: 'comment-one', every: '1d', last: 'done Tue Oct 27 07:57', next: 'Fri Oct 30 08:01' },
    ] }
  const starting = new Map([['audit', 1_000], ['comment-one', 1_000]])
  const shown = withStarting(board, starting, 60_000)
  assert.deepEqual(shown.choreList?.map(chore => [chore.last, chore.next]),
    [['starting', 'after this run'], ['starting', 'after this run']])
  const running = { name: 'audit', every: '1d', last: 'running for 0 min', next: 'after this run' }
  const listed = { ...board, choreList: [running, ...board.choreList.slice(1)] }
  assert.equal(withStarting(listed, starting, 60_000).choreList?.[0]?.last, 'running for 0 min')
  assert.deepEqual([...starting.keys()], ['comment-one'])
  assert.equal(withStarting(board, starting, 200_000), board)
  assert.equal(starting.size, 0)
})

test('a worktree\'s sessions keep their transcripts in a folder Claude Code names after its path', () => {
  assert.match(transcriptFolder('/home/me/dev/my.game-issue-12'),
    /\/\.claude\/projects\/-home-me-dev-my-game-issue-12$/)
})

test('an issue\'s Agent Log puts the session\'s beats among its lines by time, and marks them', async () => {
  const signIn = process.env.CLAUDE_CONFIG_DIR
  process.env.CLAUDE_CONFIG_DIR = mkdtempSync(join(tmpdir(), 'agent-desk-claude-'))
  const home = mkdtempSync(join(tmpdir(), 'agent-desk-log-'))
  const main = join(home, 'yard')
  const session = '11e614cb-01aa-42f9-b080-35e44d2caf59'
  const folder = transcriptFolder(`${main}-issue-4`)
  mkdirSync(folder, { recursive: true })
  const call = (at: string, command: string) => JSON.stringify({ type: 'assistant', timestamp: at,
    message: { content: [{ type: 'tool_use', name: 'Bash', input: { command } }] } })
  writeFileSync(join(folder, `${session}.jsonl`),
    `${call('2026-11-19T15:00:00Z', 'just test')}\n${call('2026-11-19T15:12:00Z', 'git commit')}\n`)
  desks.set('yard', { id: 'yard', repo: 'me/yard', owner: 'me', title: 'Yard', main } as Desk)
  const read = async () => JSON.parse((await getFrom('/api/log?desk=yard&number=4')).body.toString()) as
    { session: string; lines: string[]; beats: number[] }
  try {
    const plain = await read()
    assert.deepEqual([plain.session, plain.lines.length, plain.beats], [session, 2, []])
    mkdirSync(join(dirname(storePath()), 'beats'), { recursive: true })
    writeFileSync(join(dirname(storePath()), 'beats', session), `${JSON.stringify({ type: 'beat',
      timestamp: '2026-11-19T15:05:00.000Z', text: 'Still in Bash for 5 min: just test · busy: nothing' })}\n`)
    const log = await read()
    assert.deepEqual(log.lines.map(line => line.replace(/^\d\d:\d\d:\d\d /, '')),
      ['→ Bash: just test', 'Still in Bash for 5 min: just test · busy: nothing', '→ Bash: git commit'])
    assert.deepEqual(log.beats, [1])
  } finally {
    desks.delete('yard')
    rmSync(home, { recursive: true, force: true })
    rmSync(process.env.CLAUDE_CONFIG_DIR, { recursive: true, force: true })
    if (signIn === undefined) {
      delete process.env.CLAUDE_CONFIG_DIR
    } else {
      process.env.CLAUDE_CONFIG_DIR = signIn
    }
  }
})

test('a merged log\'s beat lines lose their mark and are listed by index', () => {
  assert.deepEqual(splitBeats('08:00:00 → Bash: x\n\x1e08:05:00 Quiet for 5 min\n08:06:00 done\n'),
    { lines: ['08:00:00 → Bash: x', '08:05:00 Quiet for 5 min', '08:06:00 done'], beats: [1] })
  assert.deepEqual(splitBeats(''), { lines: [], beats: [] })
})

test('a project\'s queued chores read by the names its chores table shows', () => {
  const file = JSON.stringify({ 'org/game': { 'singleshot-comment-one': 'forever', audit: 2 },
    'org/other': { audit: 1 } })
  assert.deepEqual(repeatsOf(file, 'org/game'), { 'singleshot-comment-one': 'forever', audit: 2 })
  assert.deepEqual(repeatsOf(file, 'org/none'), {})
  assert.deepEqual(repeatsOf('not json', 'org/game'), {})
})

test('a chore\'s stats count its newest runs, their average length, and what became of their PRs', () => {
  const run = (minutes: number | null, review: number | null) => ({ minutes, review })
  const runs = [run(100, 1), ...[1, 2, 3, 4, 5, 6, 7, 8].map(n => run(10, 10 + n)), run(20, null), run(null, 30)]
  assert.deepEqual(choreStats(runs, { 11: 'MERGED', 12: 'MERGED', 13: 'CLOSED', 14: 'OPEN', 1: 'MERGED' }),
    { runs: 10, minutes: 11, merged: 2, closed: 1, open: 1 })
  assert.deepEqual(choreStats([], {}), { runs: 0, minutes: null, merged: 0, closed: 0, open: 0 })
})

test('the page labels Ready apart from Agent-Ready, gives each action its own key, and asks to confirm Ready', () => {
  assert.equal(LABELS.undraft, 'Ready')
  assert.notEqual(LABELS.ready, LABELS.undraft)
  assert.equal(new Set(Object.values(KEYS)).size, Object.keys(KEYS).length)
  assert.ok(CONFIRMS.has('undraft'))
  assert.ok(FORWARD.has('undraft'))
})

test('the page loads pictures only from itself', async () => {
  const { headers } = await getFrom('/')
  const policy = headers['Content-Security-Policy'] ?? ''
  const directive = policy.split(';').map((part) => part.trim()).find((part) => part.startsWith('img-src '))
  assert.equal(directive, "img-src 'self'")
})

test('the asset route serves a saved picture by its hash, and 404s a hash the store lacks or a path in its place',
  async () => {
  const folder = join(TEST_HOME, 'state', 'agent-desk', 'assets', 'me__shots')
  mkdirSync(folder, { recursive: true })
  const hash = 'ab'.repeat(32)
  const file = join(folder, `${hash}.jpg`)
  writeFileSync(file, 'jpeg bytes')
  // A file outside the assets folder, which a row names under a hash and under a path in place of one.
  const secret = join(TEST_HOME, 'state', 'agent-desk', 'secret.jpg')
  writeFileSync(secret, 'secret')
  const outside = 'cd'.repeat(32)
  // A link inside the assets folder to the file outside it, which a row names under a third hash.
  const linked = 'ef'.repeat(32)
  const link = join(folder, `${linked}.jpg`)
  symlinkSync(secret, link)
  storeFixture([`db.execute("INSERT INTO assets (hash, project, number, path) VALUES (?, 'me/shots', 1, ?)", `
    + `(${JSON.stringify(hash)}, ${JSON.stringify(file)}))`,
  `db.execute("INSERT INTO assets (hash, project, number, path) VALUES (?, 'me/shots', 1, ?)", `
    + `(${JSON.stringify(outside)}, ${JSON.stringify(secret)}))`,
  `db.execute("INSERT INTO assets (hash, project, number, path) VALUES ('../secret.jpg', 'me/shots', 1, ?)", `
    + `(${JSON.stringify(secret)},))`,
  `db.execute("INSERT INTO assets (hash, project, number, path) VALUES (?, 'me/shots', 1, ?)", `
    + `(${JSON.stringify(linked)}, ${JSON.stringify(link)}))`].join('\n'), storePath())

  const served = await getFrom(`/api/asset/${hash}`)
  assert.equal(served.status, 200)
  assert.equal(served.headers['Content-Type'], 'image/jpeg')
  assert.match(served.headers['Cache-Control'] ?? '', /max-age=31536000, immutable/)
  assert.equal(served.body.toString(), 'jpeg bytes')

  for (const missing of [`/api/asset/${'12'.repeat(32)}`, '/api/asset/..%2Fsecret.jpg', '/api/asset/../secret.jpg',
    `/api/asset/${hash.toUpperCase()}`, `/api/asset/${hash}.jpg`, `/api/asset/${outside}`, `/api/asset/${linked}`,
    '/api/asset/']) {
    const refused = await getFrom(missing)
    assert.equal(refused.status, 404, missing)
    assert.notEqual(refused.body.toString(), 'secret', missing)
  }
})

test('the schedule route refuses bad days and unknown chores before any step, and answers with the PR or the error',
  async () => {
  const home = mkdtempSync(join(tmpdir(), 'agent-desk-route-'))
  const main = join(home, 'game')
  const config = '{ "chores": { "every": { "audit": 14 } } }\n'
  for (const folder of [main, scheduleWorktree(main)]) {
    mkdirSync(folder)
    writeFileSync(join(folder, '.agent-desk.json'), config)
  }
  const desk = { id: 'game', repo: 'me/game', owner: 'me', title: 'Game', main, chores: ['audit'], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('game', desk)
  const ran: string[][] = []
  let refuse = ''
  const real = steps.run
  // The store has review 7 open on the schedule branch, whose worktree stands clean beside main.
  steps.run = async argv => {
    const words = argv[0]?.endsWith('/agent-desk') ? ['agent-desk', ...argv.slice(1)] : argv
    ran.push(words)
    const out = words[2] === 'list' ? '[{"number": 7}]'
      : words[1] === 'branch' ? `${SCHEDULE_BRANCH}\n` : words[1] === 'show' ? config : ''
    return words.join(' ').startsWith(refuse || '-') ? { ok: false, out: '', err: 'database is locked' }
      : { ok: true, out, err: '' }
  }
  try {
    assert.equal((await postTo('/api/chore-schedule', { desk: 'game', name: 'audit', every: 400 })).status, 400)
    assert.equal((await postTo('/api/chore-schedule', { desk: 'game', name: 'audit' })).status, 400)
    assert.equal((await postTo('/api/chore-schedule', { desk: 'game', name: 'nightly', every: 3 })).status, 404)
    assert.deepEqual(ran, [])

    const done = await postTo('/api/chore-schedule', { desk: 'game', name: 'audit', every: 3 })
    assert.deepEqual(done, { status: 200, data: { pr: 7, url: '#game:7',
      title: 'Change the chore schedule: audit every 3 days' } })
    assert.equal(desk.schedulePr, 7)
    assert.ok(ran.some(argv => argv.join(' ')
      .startsWith('agent-desk review edit 7 --repo me/game --title Change the chore schedule: audit')))

    refuse = 'agent-desk review list'
    const failed = await postTo('/api/chore-schedule', { desk: 'game', name: 'audit', paused: true })
    assert.deepEqual(failed, { status: 502, data: { error: 'agent-desk review list failed: database is locked' } })
  } finally {
    steps.run = real
    desks.delete('game')
    rmSync(home, { recursive: true, force: true })
  }
})

/** One server-sent event as a page receives it. */
type Sent = { id: string; event: string; data: Record<string, unknown> }

/**
 * Opens the event stream of the server on `port` as the page does, naming `lastId` as EventSource does when it
 * retries, and collects the events it sends; `next` waits for the first one after those already taken that `wanted`
 * accepts. The Host header names `named`, the port the server believes it serves.
 */
function openStream(port: number, lastId?: string, named = 7480) {
  const sent: Sent[] = []
  const waiting: (() => void)[] = []
  let taken = 0
  const req = get({ port, path: '/api/events', headers: { host: `127.0.0.1:${named}`,
    ...(lastId === undefined ? {} : { 'last-event-id': lastId }) } }, res => {
    let text = ''
    res.on('data', chunk => {
      text += String(chunk)
      const blocks = text.split('\n\n')
      text = blocks.pop() ?? ''
      for (const block of blocks.filter(one => !one.startsWith(':'))) {
        const field = (name: string) => block.split('\n').find(line => line.startsWith(`${name}: `))
          ?.slice(name.length + 2) ?? ''
        sent.push({ id: field('id'), event: field('event'), data: JSON.parse(field('data') || '{}') })
      }
      waiting.splice(0).forEach(wake => wake())
    })
  })
  req.on('error', () => undefined)
  const next = async (wanted: (one: Sent) => boolean, ms = 5_000): Promise<Sent> => {
    const until = Date.now() + ms
    for (;;) {
      const found = sent.slice(taken).findIndex(wanted)
      if (found >= 0) {
        taken += found + 1
        return sent[taken - 1] as Sent
      }
      assert.ok(Date.now() < until, `no such event in ${JSON.stringify(sent.map(one => [one.id, one.event]))}`)
      await new Promise<void>(wake => {
        waiting.push(wake)
        setTimeout(wake, 100)
      })
    }
  }
  return { sent, next, close: () => req.destroy() }
}

/** The titles a sent state's first project shows in its Open column. */
const openTitles = (one: Sent) => ((one.data.desks as { board: Board | null }[])[0]?.board?.next ?? [])
  .map(row => row.title)

test('a change to the store reaches a connected page, and a page that reconnects resumes from the last id it saw',
  async () => {
  // A project whose main checkout names its repository by its origin, as agent-desk board reads it.
  const main = join(TEST_HOME, 'live')
  mkdirSync(main)
  execFileSync('git', ['init', '-q', main])
  execFileSync('git', ['-C', main, 'remote', 'add', 'origin', 'https://github.com/me/live.git'])
  writeFileSync(join(main, '.agent-desk.json'), '{ "title": "Live" }\n')
  mkdirSync(join(TEST_HOME, 'config', 'agent-desk'), { recursive: true })
  writeFileSync(join(TEST_HOME, 'config', 'agent-desk', 'projects'), `me/live ${main}\n`)
  mkdirSync(join(TEST_HOME, 'state', 'agent-desk'), { recursive: true })
  // The issue carries a label the import colored and one it never saw, and chores opened reviews, one of them on the
  // schedule branch.
  const path = storeFixture(['p = "me/live"',
    'store.create(p, "issue", "Paint the fence", labels=["enhancement", "paint"])',
    'store.import_label_colors(p, {"enhancement": "a2eeef"})',
    'store.create(p, "review", "Audit", branch="agent/chore-audit")', 'store.merge(p, 2, "abc123")',
    `store.create(p, "review", "Schedule", branch="${SCHEDULE_BRANCH}")`].join('\n'), storePath())
  // The board reads the store for real, and every command the refreshes run is kept.
  const real = host.run
  const ran: string[][] = []
  host.run = (argv, cwd, stdin) => {
    ran.push(argv)
    return real(argv, cwd, stdin)
  }
  const server = createServer((req, res) => void handle(req, res))
  await new Promise<void>(listening => server.listen(0, '127.0.0.1', listening))
  const port = (server.address() as { port: number }).port
  let page = openStream(port)
  try {
    const first = await page.next(one => one.event === 'state')
    assert.equal(first.id, String(lastEvent()))
    assert.deepEqual(openTitles(first), ['Paint the fence'])
    // The label colors and the chore PRs come from the store.
    assert.deepEqual((first.data.desks as { labels: Record<string, string> }[])[0]?.labels,
      { enhancement: 'a2eeef', paint: hashedColor('paint') })
    assert.deepEqual(desks.get('live')?.chorePrs, { 2: 'MERGED', 3: 'OPEN' })
    assert.equal(desks.get('live')?.schedulePr, 3)

    // A tick or two that find nothing new pass before the change.
    await new Promise(resolve => setTimeout(resolve, 1_200))
    storeFixture('store.create("me/live", "issue", "Fix the gate")', path)
    const changed = await page.next(one => one.event === 'state' && openTitles(one).includes('Fix the gate'))
    assert.equal(changed.id, String(lastEvent()))
    assert.ok(Number(changed.id) > Number(first.id))

    // While no page is open the server reads nothing; the page comes back naming the last id it saw, and its first
    // state already holds what changed meanwhile, under the store's newest id.
    page.close()
    await new Promise(resolve => setTimeout(resolve, 100))
    storeFixture('store.create("me/live", "issue", "Oil the hinge")', path)
    page = openStream(port, changed.id)
    const resumed = await page.next(() => true)
    assert.equal(resumed.event, 'state')
    assert.equal(resumed.id, String(lastEvent()))
    assert.deepEqual(openTitles(resumed).sort(), ['Fix the gate', 'Oil the hinge', 'Paint the fence'])
    // No refresh asked GitHub for anything.
    assert.ok(ran.length > 0)
    assert.deepEqual(ran.filter(argv => argv[0] === 'gh'), [])
  } finally {
    page.close()
    host.run = real
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
    desks.clear()
  }
})

test('a card\'s button that fails toasts why after its project\'s name, one that works sends nothing, and no state '
  + 'carries a note', async () => {
  const row: Row = { key: 'next-80', text: 'Paint the shed', number: 80, title: 'Paint the shed',
    actions: ['open', 'ready', 'close'] }
  const board: Board = { at: '', runners: [], needs: [], merging: [], design: [], designTotal: 0, queued: [],
    next: [row], blocked: [], backlog: [], ideas: [], merged: [], chores: [], error: null }
  const desk = { id: 'game', repo: 'me/game', owner: 'me', title: 'Game', main: TEST_HOME, chores: [], board, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {}, modelsFor: '',
    schedulePr: null, known: null, refreshes: 0, commands: 0 } as Desk
  // Each command answers as `answer` says: '' for one the store took, null for one it refused.
  let answer: string | null = ''
  const real = host.run
  const server = createServer((req, res) => void handle(req, res))
  await new Promise<void>(listening => server.listen(0, '127.0.0.1', listening))
  const page = openStream((server.address() as { port: number }).port)
  const press = async (action: RowAction) => {
    desk.board = board
    await act(loop, desk, row, action)
  }
  try {
    const first = await page.next(one => one.event === 'state')
    assert.equal('note' in first.data, false)
    host.run = async argv => argv[2] === `exec "${DESK}" board --json` ? null : answer

    // Agent-Ready and Close that work move their card and toast nothing, so the first toast is the refused label's.
    await press('ready')
    await press('close')
    answer = null
    await press('ready')
    assert.equal((await page.next(one => one.event === 'toast')).data.text, 'Game: Couldn\'t label #80.')
    await press('close')
    assert.equal((await page.next(one => one.event === 'toast')).data.text, 'Game: Couldn\'t close #80.')
    assert.equal(page.sent.filter(one => one.event === 'toast').length, 2)
    assert.equal(page.sent.some(one => 'note' in one.data), false)
  } finally {
    page.close()
    host.run = real
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
    desks.clear()
  }
})

test('a mode set outside the page reads every board again with no store event, so its Start notes follow the mode',
  async () => {
  const main = join(TEST_HOME, 'yard')
  mkdirSync(main)
  writeFileSync(join(main, '.agent-desk.json'), '{ "title": "Yard" }\n')
  mkdirSync(join(TEST_HOME, 'config', 'agent-desk'), { recursive: true })
  writeFileSync(join(TEST_HOME, 'config', 'agent-desk', 'projects'), `me/yard ${main}\n`)
  // lib/hold.py's state, which agent-desk board --json carries as `hold`, as agent-desk hold and resume write it.
  const held = join(TEST_HOME, 'state', 'agent-desk', 'hold.json')
  mkdirSync(join(TEST_HOME, 'state', 'agent-desk'), { recursive: true })
  const setMode = (mode: Record<string, unknown>, at: number) => {
    writeFileSync(held, JSON.stringify(mode))
    utimesSync(held, at, at)
  }
  const queued = { number: 5, title: 'Rake the leaves', status: 'queued', labels: [], blockedBy: [],
    createdAt: '2026-11-10T09:00:00Z', updatedAt: '2026-11-11T09:00:00Z' }
  let reads = 0
  const real = host.run
  host.run = async argv => {
    if (argv[2] !== `exec "${DESK}" board --json`) {
      return ''
    }
    reads += 1
    return JSON.stringify({ repo: 'me/yard', issues: [queued], reviews: [], merged: [], runs: [], asked: [],
      hold: JSON.parse(readFileSync(held, 'utf8')) })
  }
  const noteOf = () => desks.get('yard')?.board?.queued.find(row => row.key === 'queued-5')?.note
  try {
    setMode({ state: 'manual', picked: [] }, 1_000)
    await catchUp()
    assert.equal(noteOf(), 'waits for Start')
    const before = reads
    await catchUp()
    assert.equal(reads, before)

    // agent-desk hold and hold.py start 5 write no store event.
    setMode({ state: 'held', before: 'manual', picked: ['me/yard#5'] }, 2_000)
    await catchUp()
    assert.equal(reads, before + 1)
    assert.equal(noteOf(), 'picked; starts on Resume')
  } finally {
    host.run = real
    desks.clear()
  }
})

/** A port nothing listens on, from the system's pick of a free one. */
async function freePort(): Promise<number> {
  const probe = createServer()
  await new Promise<void>(listening => probe.listen(0, '127.0.0.1', listening))
  const { port } = probe.address() as AddressInfo
  await new Promise(resolve => probe.close(resolve))
  return port
}

/** Runs `node web/server.ts` on `port` with `env`, resolving once it listens. */
async function startServer(port: number, env: NodeJS.ProcessEnv): Promise<ChildProcess> {
  const child = spawn(process.execPath, [join(HERE, 'server.ts')], { env: { ...env, AGENT_DESK_WEB_PORT: String(port) },
    stdio: ['ignore', 'pipe', 'inherit'] })
  await new Promise<void>((listening, failed) => {
    let out = ''
    child.stdout?.on('data', chunk => {
      out += String(chunk)
      if (out.includes('agent-desk at http://')) {
        listening()
      }
    })
    child.on('exit', code => failed(new Error(`the server exited with ${code} before it listened`)))
  })
  return child
}

test('a page that reconnects to a server that just restarted gets its boards, not empty ones', async () => {
  // A home of its own, with a project, its store, and a gh that answers nothing, for two real servers in turn.
  const home = mkdtempSync(join(tmpdir(), 'agent-desk-restart-'))
  const main = join(home, 'site')
  mkdirSync(main)
  execFileSync('git', ['init', '-q', main])
  execFileSync('git', ['-C', main, 'remote', 'add', 'origin', 'https://github.com/me/site.git'])
  writeFileSync(join(main, '.agent-desk.json'), '{ "title": "Site" }\n')
  mkdirSync(join(home, 'config', 'agent-desk'), { recursive: true })
  writeFileSync(join(home, 'config', 'agent-desk', 'projects'), `me/site ${main}\n`)
  mkdirSync(join(home, 'state', 'agent-desk'), { recursive: true })
  storeFixture('store.create("me/site", "issue", "Paint the fence")', join(home, 'state', 'agent-desk', 'desk.sqlite'))
  mkdirSync(join(home, 'bin'))
  writeFileSync(join(home, 'bin', 'gh'), '#!/bin/sh\nexit 1\n')
  chmodSync(join(home, 'bin', 'gh'), 0o755)
  const env = { ...process.env, XDG_CONFIG_HOME: join(home, 'config'), XDG_STATE_HOME: join(home, 'state'),
    PATH: `${join(home, 'bin')}:${process.env.PATH ?? ''}` }
  const port = await freePort()
  let server = await startServer(port, env)
  let page = openStream(port, undefined, port)
  try {
    const first = await page.next(one => one.event === 'state')
    assert.deepEqual(openTitles(first), ['Paint the fence'])

    // The server stops under the open page and starts again; the page comes back naming the last id it saw, which
    // is the store's newest, and its first state holds the board under that id.
    server.kill()
    await new Promise(resolve => server.once('exit', resolve))
    page.close()
    server = await startServer(port, env)
    page = openStream(port, first.id, port)
    const resumed = await page.next(() => true)
    assert.equal(resumed.event, 'state')
    assert.equal(resumed.id, first.id)
    assert.deepEqual(openTitles(resumed), ['Paint the fence'])
  } finally {
    page.close()
    server.kill()
    rmSync(home, { recursive: true, force: true })
  }
})

// What systemctl, ps, and df print on a machine with one agent run, one branch check, the dispatcher, and a desktop:
// the browser's tabs, which rewrite their command line as one string, an editor, and a process in the agent's unit.
const LISTED = ['agent-desk-agent-me-fence-12-7.service loaded active running agent-desk agent run 12 in me/fence',
  'agent-desk-check-me-fence-1.service loaded active running agent-desk check of agent/issue-30 in me/fence',
  'agent-desk-dispatch.service loaded active running agent-desk dispatcher'].join('\n')
const SHOWN = ['Id=agent-desk-agent-me-fence-12-7.service', 'Description=agent-desk agent run 12 in me/fence',
  'MemoryCurrent=3221225472', 'MemorySwapCurrent=1073741824', 'CPUUsageNSec=125000000000', '',
  'Id=agent-desk-check-me-fence-1.service', 'Description=agent-desk check of agent/issue-30 in me/fence',
  'MemoryCurrent=536870912', 'MemorySwapCurrent=[not set]', 'CPUUsageNSec=9000000000', '',
  'Id=agent-desk-dispatch.service', 'Description=agent-desk dispatcher', 'MemoryCurrent=41943040',
  'MemorySwapCurrent=0', 'CPUUsageNSec=18446744073709551615'].join('\n')
const DESKTOP = '0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-desktop.service'
const PS = [
  `    2       0     0 - kthreadd`,
  ` 100 2097152   300 ${DESKTOP} firefox`,
  ` 101 1048576    20 ${DESKTOP} Isolated Web Co`,
  ` 102  524288    10 ${DESKTOP} Isolated Web Co`,
  ` 200  262144   900 ${DESKTOP} zed-editor`,
  ` 300 4194304    50 0::/user.slice/user-1000.slice/user@1000.service/agentdesk.slice/`
    + 'agent-desk-agent-me-fence-12-7.service claude',
].join('\n')

/** A stand-in for /proc with the machine's memory and each desktop process's swap and command line. */
function procFolder(): string {
  const folder = mkdtempSync(join(tmpdir(), 'agent-desk-proc-'))
  writeFileSync(join(folder, 'meminfo'), ['MemTotal:       67108864 kB', 'MemFree:         1048576 kB',
    'MemAvailable:   12582912 kB', 'SwapTotal:      33554432 kB', 'SwapFree:        8388608 kB'].join('\n'))
  const processes: [number, number, string][] = [
    [100, 524288, '/usr/lib/firefox/firefox\0'],
    [101, 0, '/usr/lib/firefox/firefox -contentproc -isForBrowser 5 tab'],
    [102, 131072, '/usr/lib/firefox/firefox -contentproc -isForBrowser 7 tab'],
    [200, 2097152, '/usr/bin/zed-editor\0--foreground\0'],
  ]
  for (const [pid, swap, cmdline] of processes) {
    mkdirSync(join(folder, String(pid)))
    writeFileSync(join(folder, String(pid), 'status'), `Name:\tx\nVmRSS:\t1 kB\nVmSwap:\t    ${swap} kB\n`)
    writeFileSync(join(folder, String(pid), 'cmdline'), cmdline)
  }
  return folder
}

test('a unit reads its memory, swap, and CPU time from systemctl show, and a figure systemd tracks none of as null',
  () => {
  const usage = unitUsage(SHOWN)
  assert.deepEqual(usage.get('agent-desk-agent-me-fence-12-7.service'), { description: 'agent-desk agent run 12 in me/fence',
    memory: 3221225472, swap: 1073741824, cpu: 125 })
  assert.equal(usage.get('agent-desk-check-me-fence-1.service')?.swap, null)
  assert.equal(usage.get('agent-desk-dispatch.service')?.cpu, null)
})

test('ps lists every process but kernel threads, and a process in an agent-desk unit counts as the desk\'s', () => {
  const listed = processesOf(PS)
  assert.deepEqual(listed.map(one => one.pid), [100, 101, 102, 200, 300])
  assert.deepEqual(listed[3], { pid: 200, memory: 262144 * 1024, cpu: 900, cgroup: DESKTOP, comm: 'zed-editor' })
  assert.deepEqual(listed.filter(one => inDeskUnit(one.cgroup)).map(one => one.pid), [300])
})

test('a failed or garbled claims command leaves the claims unread instead of reading none', () => {
  assert.equal(claimsOf(null), null)
  assert.equal(claimsOf('Traceback (most recent call last):'), null)
  assert.equal(claimsOf('{}'), null)
  assert.deepEqual(claimsOf('[]'), { byUnit: new Map(), held: 0 })
})

test('an app takes the name of the program its command line starts with, so a browser\'s tabs group with it', () => {
  assert.equal(appName('/usr/lib/firefox/firefox\0-contentproc\0', 'Isolated Web Co'), 'firefox')
  assert.equal(appName('/usr/lib/firefox/firefox -contentproc 5 tab', 'Isolated Web Co'), 'firefox')
  assert.equal(appName('C:\\Games\\xalia.exe\0', 'xalia.exe'), 'xalia.exe')
  assert.equal(appName('sshd: me@pts/0', 'sshd'), 'sshd')
  assert.equal(appName('', 'kworker'), 'kworker')
})

test(`the apps count only the ${TOP_PROCESSES} processes outside the desk using the most memory and swap, `
  + 'largest app first', () => {
  const many = Array.from({ length: TOP_PROCESSES + 2 }, (_, index) => ({ pid: index, memory: index, cpu: 1,
    cgroup: DESKTOP, comm: 'x', swap: index === 0 ? 100 : 0, name: index % 2 ? 'odd' : 'even' }))
  const unit = { ...many[1]!, pid: 99, memory: 1000, cgroup: `${DESKTOP}/agent-desk-fix-me-fence-3-9.service` }
  const top = topOf([...many, unit])
  // Process 0 swapped out the most, and processes 1 and 2 use the least; the one in the desk's unit counts with it.
  assert.deepEqual(top.map(one => one.pid), [0, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3])
  assert.deepEqual(appsOf(top).map(one => [one.name, one.processes, one.memory, one.swap]),
    [['even', 8, 70, 100], ['odd', 7, 63, 0]])
})

test('the resources route reads memory, the runs\' claims, the desk\'s units, its runs that wait for memory, the top '
  + 'apps, and /tmp, from the commands and /proc', async () => {
  const proc = procFolder()
  const store = storeFixture(['p = "me/fence"',
    'store.create(p, "issue", "Paint the fence")',
    'agent = store.request_run(p, "agent", "12")',
    'store.run_started(agent, "agent-desk-agent-me-fence-12-7.service")',
    'fix = store.request_run(p, "fix", "30")',
    'store.run_waits(fix, "memory", "memory: 5.3 GB free of 11 GB needed")',
    'serial = store.request_run(p, "agent", "14")',
    'store.run_waits(serial, "serial", "#12\'s serial slot")',
    'store.request_run("me/other", "chore", "audit")',
    'store.run_waits(4, "memory", "memory pressure under 5%, now 7%")',
    'check = store.check_queue(p, "agent/issue-30", "abc123")',
    'store.check_started(check, "/tmp/log")',
    'waiting = store.check_queue(p, "agent/issue-31", "def456")',
    'store.check_waits(waiting, "memory", "memory: 2.0 GB free of 9 GB needed")'].join('\n'))
  useStore(store)
  useProc(proc)
  desks.set('fence', { id: 'fence', repo: 'me/fence', owner: 'me', title: 'Fence' } as Desk)
  const real = host.run
  const asked: string[][] = []
  host.run = async argv => {
    asked.push(argv)
    const command = argv.slice(0, 3).join(' ')
    return command === 'systemctl --user list-units' ? LISTED : command === 'systemctl --user show' ? SHOWN
      : argv[0] === 'ps' ? PS : argv[0] === 'df' ? '        Used\n5368709120\n'
        : argv.at(-1) === 'reserveGB' ? '8\n' : argv.at(-1) === 'claims' ? `${JSON.stringify([
          { pid: 7, unit: 'agent-desk-agent-me-fence-12-7.service', claimed: 9.8, counted: 4.5, peak: 2.5,
            settle: 45, used: 2, held: 2.5 },
          { pid: 8, unit: null, claimed: 2, counted: 2, peak: null, settle: null, used: 0.5, held: 1.5 }])}\n` : null
  }
  try {
    const { status, body } = await getFrom('/api/resources')
    assert.equal(status, 200)
    const read = JSON.parse(body.toString()) as Resources
    assert.deepEqual(read.memory, { total: 64 * 1024 ** 3, free: 12 * 1024 ** 3, reserve: 8 * 1024 ** 3,
      claimed: 4 * 1024 ** 3, swapTotal: 32 * 1024 ** 3, swapFree: 8 * 1024 ** 3 })
    // lib/memory.py's claims join their units by name, and a claim whose unit it couldn't read counts in the total.
    assert.deepEqual(read.units.map(one => one.claim), [{ claimed: 9.8 * 1024 ** 3, counted: 4.5 * 1024 ** 3,
      peak: 2.5 * 1024 ** 3, settle: 45, used: 2 * 1024 ** 3, held: 2.5 * 1024 ** 3 }, null, null])
    // The units name what each works on, largest first, and systemctl shows them all at once.
    assert.deepEqual(read.units.map(one => [one.kind, one.desk, one.repo, one.label, one.number, one.pr, one.memory]),
      [['agent', 'fence', 'me/fence', '#12', 12, false, 3 * 1024 ** 3],
        ['check', 'fence', 'me/fence', '', null, false, 512 * 1024 ** 2],
        ['dispatch', null, null, '', null, false, 40 * 1024 ** 2]])
    assert.equal(read.units[0]?.cpu, 125)
    assert.deepEqual(asked.find(argv => argv[2] === 'show')?.slice(5), ['agent-desk-agent-me-fence-12-7.service',
      'agent-desk-check-me-fence-1.service', 'agent-desk-dispatch.service'])
    // The memory gate's runs and checks, in every project, and not the serial one.
    assert.deepEqual(read.waiting.map(one => [one.desk, one.kind, one.label, one.reason]), [
      ['fence', 'fix', 'PR #30', 'memory: 5.3 GB free of 11 GB needed'],
      [null, 'chore', 'audit', 'memory pressure under 5%, now 7%'],
      ['fence', 'check', '', 'memory: 2.0 GB free of 9 GB needed']])
    // The browser's tabs count with it, and the process in the agent's unit counts with the unit instead.
    assert.deepEqual(read.apps, [
      { name: 'firefox', processes: 3, memory: 3.5 * 1024 ** 3, swap: 0.625 * 1024 ** 3, cpu: 330 },
      { name: 'zed-editor', processes: 1, memory: 0.25 * 1024 ** 3, swap: 2 * 1024 ** 3, cpu: 900 }])
    assert.equal(read.tmp, 5 * 1024 ** 3)
    // It changes nothing: every command it ran reads.
    assert.deepEqual(asked.map(argv => argv[0] === 'systemctl' ? argv.slice(0, 3).join(' ') : argv[0]).sort(),
      ['df', 'ps', 'python3', 'python3', 'systemctl --user list-units', 'systemctl --user show'])
  } finally {
    host.run = real
    desks.delete('fence')
    useProc('/proc')
    useStore(storePath())
    rmSync(proc, { recursive: true, force: true })
  }
})
