// Run with: node --test web/
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'

// state.ts places hold.json under XDG_STATE_HOME when it loads, so the folder is set before hold.ts is imported.
const home = mkdtempSync(join(tmpdir(), 'agent-desk-hold-'))
process.env.XDG_STATE_HOME = home
const { agentSlotsOf, configAgentSlots, configGpuSlots, DEFAULT_AGENT_SLOTS, gpuSlotsOf, readHold, readUsage } =
  await import('./hold.ts')
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

test('a project\'s agent slots follow its config, or the dispatcher\'s default, until an override sets them', () => {
  const lib = join(import.meta.dirname, '..', 'lib')
  assert.equal(DEFAULT_AGENT_SLOTS, Number(execFileSync('python3', ['-B', '-c',
    'import run_units; print(run_units.DEFAULT_SLOTS["agent"])'], { cwd: lib, encoding: 'utf8' })))
  assert.equal(configAgentSlots({ slots: { agent: 1, chore: 2 } }), 1)
  assert.equal(configAgentSlots({ slots: { agent: 0 } }), 0)
  for (const odd of [{}, { slots: null }, { slots: { chore: 2 } }, { slots: { agent: -1 } }, { slots: { agent: '2' } },
    { slots: { agent: 1.5 } }]) {
    assert.equal(configAgentSlots(odd), DEFAULT_AGENT_SLOTS, JSON.stringify(odd))
  }
  rmSync(file, { force: true })
  assert.deepEqual(agentSlotsOf('org/game', 3), { count: 3, config: 3, override: false })
  hold({ state: 'running', slots: { 'org/game': { agent: 1, chore: 0 }, 'org/site': { chore: 2 } } })
  assert.deepEqual(agentSlotsOf('org/game', 3), { count: 1, config: 3, override: true })
  assert.deepEqual(agentSlotsOf('org/site', 2), { count: 2, config: 2, override: false })
  hold({ state: 'running', slots: { 'org/game': { agent: 'many' } } })
  assert.deepEqual(agentSlotsOf('org/game', 3), { count: 3, config: 3, override: false })
  hold('{not json')
  assert.deepEqual(agentSlotsOf('org/game', 3), { count: 3, config: 3, override: false })
})

test('a project\'s gpu slots stay unset until its config or an override sets them', () => {
  assert.equal(configGpuSlots({ slots: { agent: 3, gpu: 1 } }), 1)
  for (const odd of [{}, { slots: null }, { slots: { agent: 3 } }, { slots: { gpu: -1 } }, { slots: { gpu: null } }]) {
    assert.equal(configGpuSlots(odd), null, JSON.stringify(odd))
  }
  rmSync(file, { force: true })
  assert.deepEqual(gpuSlotsOf('org/game', null), { count: null, config: null, override: false })
  hold({ state: 'running', slots: { 'org/game': { agent: 2, gpu: 0 } } })
  assert.deepEqual(gpuSlotsOf('org/game', null), { count: 0, config: null, override: true })
  assert.deepEqual(gpuSlotsOf('org/site', 2), { count: 2, config: 2, override: false })
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
    problem: 'no reading yet', fiveHour: null })
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
  assert.deepEqual(readUsage(now), { on: true, limit: 80, percent: 81.4, readAt: now - 300, resetsAt, problem: null,
    fiveHour: null })
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
    problem: 'the usage endpoint can\'t be reached (timed out)', fiveHour: null })
  writeFileSync(usageFile, '{not json')
  assert.equal(readUsage(now).problem, 'no reading yet')
})

test('Claude quota windows expire at their own reset and retain their own reading age', () => {
  const now = 1_800_000_000
  const reset = (offset: number) => new Date((now + offset) * 1000).toISOString()
  const weekly = { percent: 40, read_at: now - 120, resets_at: reset(3600) }
  const short = { percent: 75, read_at: now - 60, resets_at: reset(120) }
  usage({ ...weekly, five_hour: short })
  assert.equal(readUsage(now).problem, null)
  assert.deepEqual(readUsage(now).fiveHour, { percent: 75, resetsAt: reset(120) })
  assert.equal(readUsage(now + 120).problem, null)
  assert.equal(readUsage(now + 120).fiveHour, null)
  usage({ ...weekly, resets_at: reset(0), five_hour: short })
  assert.equal(readUsage(now).problem, 'the last reading is out of date')
  assert.equal(readUsage(now).fiveHour?.percent, 75)
  usage({ ...weekly, five_hour: { ...short, read_at: now - 3600 } })
  assert.equal(readUsage(now).problem, null)
  assert.equal(readUsage(now).fiveHour, null)
})
