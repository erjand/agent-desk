import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import type { TestContext } from 'node:test'

import { codexWindows, fetchCodexUsage, readCodexUsage, refreshCodexUsage } from './codex-usage.ts'

const at = 1_750_000_000
const short = { usedPercent: 14, windowDurationMins: 300, resetsAt: at + 300 }
const week = { usedPercent: 82, windowDurationMins: 10080, resetsAt: at + 600 }

test('the Codex bucket returns both windows in duration order and ignores other buckets', () => {
  const caps = { rateLimits: { limitId: 'codex_other', primary: { ...week, usedPercent: 5 } },
    rateLimitsByLimitId: { codex: { primary: short, secondary: week },
      codex_other: { primary: { ...week, usedPercent: 5 } } } }
  assert.deepEqual(codexWindows(caps, at), [week, short])
  assert.deepEqual(codexWindows({ rateLimits: { primary: week } }, at), [week])
  assert.deepEqual(codexWindows({ rateLimits: caps.rateLimits }, at), [])
  assert.deepEqual(codexWindows({ rateLimitsByLimitId: { codex_other: caps.rateLimits } }, at), [])
})

test('a shorter window keeps its actual duration and readings expire at their reset', () => {
  const caps = { rateLimits: { primary: short, secondary: { ...week, resetsAt: at } } }
  assert.deepEqual(codexWindows(caps, at), [short])
  assert.deepEqual(codexWindows(caps, short.resetsAt), [])
  assert.deepEqual(codexWindows({ rateLimits: { primary: { ...short, windowDurationMins: null }, secondary: week } }, at),
    [week])
})

function appServer(t: TestContext, type: string, expiresAt: number): string {
  const root = mkdtempSync(join(tmpdir(), 'desk-codex-test-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const command = join(root, 'codex')
  writeFileSync(command, `#!/usr/bin/env node
const assert = require('node:assert/strict')
const { createInterface } = require('node:readline')
assert.deepEqual(process.argv.slice(2), ['app-server', '--listen', 'stdio://'])
const methods = ['initialize', 'initialized', 'account/read', 'account/rateLimits/read']
const results = { initialize: {}, 'account/read': { account: { type: ${JSON.stringify(type)}, email: 'private' } },
  'account/rateLimits/read': { rateLimits: ${JSON.stringify({ limitId: 'codex',
    primary: { ...short, resetsAt: expiresAt - 30 }, secondary: { ...week, resetsAt: expiresAt } })} } }
createInterface({ input: process.stdin }).on('line', line => {
  const { id, method } = JSON.parse(line)
  assert.equal(method, methods.shift())
  if (method === 'account/rateLimits/read' && ${JSON.stringify(type)} !== 'chatgpt') process.exit(1)
  if (id !== undefined) console.log(JSON.stringify({ id, result: results[method] }))
})
`, { mode: 0o755 })
  return command
}

test('the app-server returns a quota only for its ChatGPT sign-in and exposes no account details', async t => {
  const resetsAt = Math.floor(Date.now() / 1000) + 600
  assert.deepEqual(await fetchCodexUsage(appServer(t, 'chatgpt', resetsAt)),
    { billing: 'subscription', windows: [{ ...week, resetsAt }, { ...short, resetsAt: resetsAt - 30 }] })
  assert.deepEqual(await fetchCodexUsage(appServer(t, 'apiKey', resetsAt)), { billing: 'api' })
  assert.deepEqual(await fetchCodexUsage(appServer(t, 'amazonBedrock', resetsAt)), { billing: 'unavailable' })
  assert.deepEqual(await fetchCodexUsage('/missing-codex'), { billing: 'unavailable' })
})

test('the cache coalesces reads and suppresses expired and stale quotas', async t => {
  const resetsAt = Math.floor(Date.now() / 1000) + 60
  const command = appServer(t, 'chatgpt', resetsAt)
  const path = process.env.PATH
  process.env.PATH = `${join(command, '..')}:${path}`
  t.after(() => { process.env.PATH = path })
  const pending = refreshCodexUsage()
  assert.ok(pending)
  assert.equal(refreshCodexUsage(), null)
  await pending
  assert.equal(refreshCodexUsage(), null)
  assert.deepEqual(readCodexUsage(), { billing: 'subscription',
    windows: [{ ...week, resetsAt }, { ...short, resetsAt: resetsAt - 30 }] })
  assert.deepEqual(readCodexUsage((resetsAt - 30) * 1000), { billing: 'subscription', windows: [{ ...week, resetsAt }] })
  assert.deepEqual(readCodexUsage(resetsAt * 1000), { billing: 'subscription', windows: [] })
  assert.deepEqual(readCodexUsage(Date.now() + 120_000), { billing: 'unavailable' })
})
