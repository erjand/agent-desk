// Run with: node --test web/
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'

// state.ts places hold.json under XDG_STATE_HOME when it loads, so the folder is set before hold.ts is imported.
const home = mkdtempSync(join(tmpdir(), 'agent-desk-hold-'))
process.env.XDG_STATE_HOME = home
const { readHold, readUsage } = await import('./hold.ts')
const folder = join(home, 'agent-desk')
const file = join(folder, 'hold.json')

after(() => rmSync(home, { recursive: true, force: true }))

function hold(data: unknown): void {
  mkdirSync(folder, { recursive: true })
  writeFileSync(file, typeof data === 'string' ? data : JSON.stringify(data))
}

const auto = { state: 'running', since: null, before: 'running', projects: {} }

test('without a hold file, or with one that isn\'t JSON, every new run starts', () => {
  rmSync(file, { force: true })
  assert.deepEqual(readHold(), auto)
  hold('{not json')
  assert.deepEqual(readHold(), auto)
})

test('Hold keeps when it began and the mode Resume goes back to', () => {
  hold({ state: 'held', since: '2026-11-11T07:00:00-0600', before: 'manual' })
  assert.deepEqual(readHold(), { ...auto, state: 'held', since: '2026-11-11T07:00:00-0600', before: 'manual' })
})

test('Resume goes back to Auto from any mode but Manual', () => {
  hold({ state: 'held', before: 'held' })
  assert.equal(readHold().before, 'running')
})

test('a state the desk doesn\'t know, such as the sleep it no longer has, reads as Auto', () => {
  hold({ state: 'sleep', since: '2026-11-11T07:00:00-0600', before: 'manual', requeue: [{ run: 1 }] })
  assert.deepEqual(readHold(), auto)
})

test('a project keeps a mode of its own only when it is Auto, Manual, or Hold', () => {
  const projects = { 'org/game': 'manual', 'org/site': 'held', 'org/tools': 'running', 'org/old': 'sleep',
    'org/odd': 'paused' }
  const own = { 'org/game': 'manual', 'org/site': 'held', 'org/tools': 'running' }
  hold({ state: 'running', projects })
  assert.deepEqual(readHold().projects, own)
  hold({ state: 'held', projects })
  assert.deepEqual(readHold().projects, own)
})

const usageFile = join(folder, 'usage.json')

function usage(data: unknown): void {
  mkdirSync(folder, { recursive: true })
  writeFileSync(usageFile, JSON.stringify(data))
}

test('the weekly usage limit reads off with no percentage before one is set, then as hold.json keeps it', () => {
  rmSync(usageFile, { force: true })
  hold({ state: 'running' })
  assert.deepEqual(readUsage(), { on: false, limit: null, percent: null, readAt: null, resetsAt: null,
    problem: 'no reading yet' })
  hold({ state: 'held', usageLimit: { on: false, percent: 70 } })
  assert.deepEqual([readUsage().on, readUsage().limit], [false, 70])
  hold({ state: 'running', usageLimit: { on: true, percent: 80 } })
  assert.deepEqual([readUsage().on, readUsage().limit], [true, 80])
  // A limit lib/usage.py would refuse reads as never set.
  for (const odd of [{ on: true, percent: 120 }, { on: 'yes', percent: 80 }, { on: true, percent: 80.5 }, 'on']) {
    hold({ state: 'running', usageLimit: odd })
    assert.deepEqual([readUsage().on, readUsage().limit], [false, null], JSON.stringify(odd))
  }
})

test('a reading counts for an hour and until its window resets, and says why once it doesn\'t', () => {
  const now = 1_800_000_000
  const resetsAt = new Date((now + 86_400) * 1000).toISOString()
  hold({ state: 'running', usageLimit: { on: true, percent: 80 } })
  usage({ percent: 81.4, resets_at: resetsAt, read_at: now - 300, tried_at: now - 300, error: null })
  assert.deepEqual(readUsage(now), { on: true, limit: 80, percent: 81.4, readAt: now - 300, resetsAt, problem: null })
  usage({ percent: 81.4, resets_at: resetsAt, read_at: now - 3600, tried_at: now - 60, error: null })
  assert.equal(readUsage(now).problem, 'the last reading is out of date')
  usage({ percent: 81.4, resets_at: new Date((now - 60) * 1000).toISOString(), read_at: now - 300, error: null })
  assert.equal(readUsage(now).problem, 'the last reading is out of date')
  // A failed fetch keeps the last good reading, which still counts while it is fresh, and names its error once not.
  const refused = 'the usage endpoint answered HTTP 401'
  usage({ percent: 81.4, resets_at: resetsAt, read_at: now - 300, tried_at: now, error: refused })
  assert.equal(readUsage(now).problem, null)
  usage({ percent: 81.4, resets_at: resetsAt, read_at: now - 4000, tried_at: now, error: refused })
  assert.equal(readUsage(now).problem, refused)
  usage({ tried_at: now, error: 'the usage endpoint can\'t be reached (timed out)' })
  assert.deepEqual(readUsage(now), { on: true, limit: 80, percent: null, readAt: null, resetsAt: null,
    problem: 'the usage endpoint can\'t be reached (timed out)' })
  writeFileSync(usageFile, '{not json')
  assert.equal(readUsage(now).problem, 'no reading yet')
})
