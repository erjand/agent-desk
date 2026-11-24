// Run with: node --test web/
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { type TestContext, test } from 'node:test'

import { buildDemo, DEMO_PROJECTS, DEMO_SESSION } from './demo.ts'

const DESK = join(import.meta.dirname, '..', 'bin', 'agent-desk')

/** A new temporary folder, removed when the test `t` ends. */
function scratch(t: TestContext): string {
  const folder = mkdtempSync(join(tmpdir(), 'agent-desk-demo-test-'))
  t.after(() => rmSync(folder, { recursive: true, force: true }))
  return folder
}

test('the demo lists only its made-up projects, and each one\'s board reads cards from the demo\'s own store', t => {
  const root = join(scratch(t), 'demo')
  const { config, state } = buildDemo(root)
  const listed = readFileSync(join(config, 'agent-desk', 'projects'), 'utf8').trim().split('\n').map(line => {
    const [repo = '', path = ''] = line.split(' ')
    return { repo, path }
  })
  assert.deepEqual(listed.map(one => one.repo), DEMO_PROJECTS.map(one => one.repo))

  // The server's programs run without the variables that point them at another repository, as childEnv leaves them.
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    !['GH_REPO', 'AGENT_DESK_RUN', 'AGENT_DESK_AUTHOR'].includes(name)))
  const boards = listed.map(({ path }) => JSON.parse(execFileSync(DESK, ['board', '--json'],
    { cwd: path, env: { ...env, XDG_CONFIG_HOME: config, XDG_STATE_HOME: state }, encoding: 'utf8' })))
  assert.deepEqual(boards.map(board => board.repo), listed.map(one => one.repo))
  for (const board of boards) {
    assert.ok(board.issues.length > 0, `${board.repo} has issues`)
    assert.ok(board.reviews.length > 0, `${board.repo} has reviews`)
  }
  const states = new Set(boards.flatMap(board => board.issues.map((one: { status: string }) => one.status)))
  assert.deepEqual([...states].sort(), ['in-progress', 'needs-design', 'on-hold', 'open', 'queued'])
  assert.deepEqual(boards.flatMap(board => board.runs.map((one: { kind: string; status: string }) =>
    `${one.kind} ${one.status}`)).sort(), ['agent failed', 'agent requested', 'agent running', 'agent running',
    'agent timed-out', 'chore running'])
  // Kiln's run on #8 stalled 14 minutes ago in a deadlocked test.
  const stalled = boards[2]?.runs.find((one: { stalled?: unknown }) => one.stalled)
  assert.deepEqual([stalled?.target, stalled?.stalled?.tool, stalled?.stalled?.summary],
    ['8', 'Bash', 'pytest tests/test_logger.py -x'])
  assert.equal(boards[0]?.merged.length, 1)
  // Orchard's overrides lower its agent slots below its config's and cap its gpu runs, as `agent-desk slots` reads
  // them.
  const slots = execFileSync('python3', ['-B', join(import.meta.dirname, '..', 'lib', 'hold.py'), 'slots',
    'demo/orchard'], { env: { ...env, XDG_CONFIG_HOME: config, XDG_STATE_HOME: state }, encoding: 'utf8' })
  assert.match(slots, /agent\s+2\s+config\s+3\s+override/)
  assert.match(slots, /gpu\s+1\s+config\s+-\s+override/)
  assert.deepEqual(boards.map(board => [board.slots.agent, board.slots.gpu]), [[2, 1], [3, null], [3, null]])
  // The store's backup failed its push, so every board's GitHub rows end with the backup's in the warning tone.
  assert.deepEqual(boards.map(board => board.backup?.error?.startsWith('the push to')), [true, true, true])
  // The header's account control reads the subscription's week and the API key's month, with no key set.
  const account = JSON.parse(execFileSync('python3', ['-B', join(import.meta.dirname, '..', 'lib', 'account.py'),
    'json'], { env: { ...env, XDG_CONFIG_HOME: config, XDG_STATE_HOME: state }, encoding: 'utf8' }))
  assert.deepEqual(account, { account: 'subscription', spent: 63.2, budget: { on: true, dollars: 200 }, held: false,
    key: { set: false, ending: null } })
  assert.equal(JSON.parse(readFileSync(join(state, 'agent-desk', 'usage.json'), 'utf8')).percent, 41.3)
})

test('the demo\'s Orchard #2 has an agent session whose beats and crash fall between its transcript\'s lines', t => {
  const root = join(scratch(t), 'demo')
  const { state, transcripts } = buildDemo(root)
  const worktree = join(root, 'projects', 'orchard-issue-2').replace(/[^a-zA-Z0-9]/g, '-')
  const log = execFileSync('python3', ['-B', join(import.meta.dirname, '..', 'lib', 'agent_log.py'),
    join(state, 'agent-desk', 'beats', DEMO_SESSION)],
  { input: readFileSync(join(transcripts, worktree, `${DEMO_SESSION}.jsonl`)), encoding: 'utf8' }).split('\n')
  const beats = log.flatMap((line, at) => (line.startsWith('\x1e') ? [at] : []))
  assert.equal(beats.length, 9)
  assert.ok(beats[0]! > 0 && beats.at(-1)! < log.filter(Boolean).length - 1, 'agent lines come before and after')
  // The lint run failed, the test and bench runs took minutes, and a crash goes in plain among them.
  assert.ok(log.some(line => / ← Bash \d+ s, failed: error: line too long/.test(line)))
  assert.ok(log.some(line => / ← Bash 16 min$/.test(line)))
  assert.ok(log.some(line => /^\d\d:\d\d:\d\d A process under the run crashed: tally-native \(SIGSEGV\)$/.test(line)))
})

test('the demo builds only in an empty or new folder', t => {
  const root = scratch(t)
  writeFileSync(join(root, 'notes.txt'), 'mine')
  assert.throws(() => buildDemo(root), /isn't empty/)
})
