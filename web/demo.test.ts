// Run with: node --test web/
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { buildDemo, DEMO_PROJECTS } from './demo.ts'

const DESK = join(import.meta.dirname, '..', 'bin', 'agent-desk')

test('the demo lists only its made-up projects, and each one\'s board reads cards from the demo\'s own store', () => {
  const root = join(mkdtempSync(join(tmpdir(), 'agent-desk-demo-test-')), 'demo')
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
    `${one.kind} ${one.status}`)).sort(), ['agent running', 'agent timed-out', 'chore running'])
  assert.equal(boards[0]?.merged.length, 1)
})

test('the demo builds only in an empty or new folder', () => {
  const root = mkdtempSync(join(tmpdir(), 'agent-desk-demo-test-'))
  writeFileSync(join(root, 'notes.txt'), 'mine')
  assert.throws(() => buildDemo(root), /isn't empty/)
})
