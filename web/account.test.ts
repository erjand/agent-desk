// Run with: node --test web/
import './test-env.ts'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, rmSync, statSync } from 'node:fs'
import { createServer, connect } from 'node:net'
import type { AddressInfo, Socket } from 'node:net'
import { networkInterfaces } from 'node:os'
import { join } from 'node:path'
import { afterEach, test } from 'node:test'

import { accountOf, budgetAmount, readAccount, refreshAccount } from './account.ts'
import { loopback } from './peer.ts'
import { postTo } from './server-fixture.ts'
import { TEST_HOME } from './test-env.ts'

// A made-up key, which no reply, state, or log may hold beyond its last four characters.
const KEY = 'sk-ant-api03-made-up-for-web-tests-WXYZ'
const KEY_FILE = join(TEST_HOME, 'config', 'agent-desk', 'anthropic-api-key')
const HOLD = join(TEST_HOME, 'state', 'agent-desk', 'hold.json')
const hold = () => (existsSync(HOLD) ? JSON.parse(readFileSync(HOLD, 'utf8')) : {})

afterEach(() => {
  rmSync(KEY_FILE, { force: true })
  rmSync(HOLD, { force: true })
})

/** Runs `act` with the server's console output caught, and gives what it wrote. */
async function logged(act: () => Promise<void>): Promise<string> {
  const lines: unknown[] = []
  const { log, error, warn } = console
  console.log = console.error = console.warn = (...args: unknown[]) => { lines.push(...args) }
  try {
    await act()
  } finally {
    Object.assign(console, { log, error, warn })
  }
  return lines.map(String).join('\n')
}

test('the reading names the account, the spend, the budget, and whether a key is set, and nothing else', async () => {
  const before = Math.floor(Date.now() / 1000)
  assert.ok(await refreshAccount())
  const { readAt, ...read } = readAccount() ?? { readAt: 0 }
  assert.deepEqual(read, { account: 'subscription', spent: 0, held: false, budget: null,
    key: { set: false, ending: null } })
  assert.ok(readAt >= before && readAt <= Date.now() / 1000, 'the reading carries when the server read it')
  // A field the script might add never reaches the page, and an ending never runs past four characters.
  assert.deepEqual(accountOf(JSON.stringify({ account: 'api', spent: 3, held: false, budget: { on: true, dollars: 5,
    extra: 1 }, key: { set: true, ending: KEY, key: KEY }, other: KEY })), { account: 'api', spent: 3, held: false,
    budget: { on: true, dollars: 5 }, key: { set: true, ending: 'WXYZ' } })
  for (const bad of ['', 'nope', '{}', JSON.stringify({ account: 'plan', spent: 0, held: false, budget: null,
    key: { set: false } })]) {
    assert.equal(accountOf(bad), null, bad)
  }
})

test('the key saves through lib/account.py, and no reply or log holds it', async () => {
  const output = await logged(async () => {
    const saved = await postTo('/api/account-key', { key: `  ${KEY}\n` })
    assert.equal(saved.status, 200)
    assert.deepEqual(saved.data.key, { set: true, ending: 'WXYZ' })
    assert.doesNotMatch(JSON.stringify(saved.data), /made-up-for-web-tests/)
    assert.equal(readFileSync(KEY_FILE, 'utf8'), KEY)
    assert.equal(statSync(KEY_FILE).mode & 0o777, 0o600)
    assert.doesNotMatch(JSON.stringify(readAccount()), /made-up-for-web-tests/, 'the page\'s state holds no key')
    // A key the script refuses says why without the key.
    const spaced = await postTo('/api/account-key', { key: 'sk-ant made up with spaces' })
    assert.deepEqual([spaced.status, spaced.data.error], [400, 'The API key has spaces or line breaks inside it, so '
      + 'nothing changed. Paste the key alone'])
    assert.equal(readFileSync(KEY_FILE, 'utf8'), KEY, 'the saved key stays')
    for (const bad of [{ key: '' }, { key: '   ' }, { key: 42 }, {}, { key: 'x'.repeat(600) }]) {
      const refused = await postTo('/api/account-key', bad)
      assert.deepEqual([refused.status, refused.data.error], [400, 'an API key is text of up to 512 characters'])
    }
  })
  assert.doesNotMatch(output, /made-up/)
})

test('the account switches through lib/account.py, refusing API while no key is set', async () => {
  const refused = await postTo('/api/account', { account: 'api' })
  assert.equal(refused.status, 409)
  assert.match(String(refused.data.error), /No API key is set, so the account stays as it was/)
  assert.equal(hold().account ?? 'subscription', 'subscription')
  for (const bad of [{ account: 'plan' }, {}, { account: ['api'] }]) {
    assert.equal((await postTo('/api/account', bad)).status, 400, JSON.stringify(bad))
  }
  await postTo('/api/account-key', { key: KEY })
  const api = await postTo('/api/account', { account: 'api' })
  assert.deepEqual([api.status, api.data.account, hold().account], [200, 'api', 'api'])
  assert.doesNotMatch(JSON.stringify(api.data), /made-up-for-web-tests/)
  // Clearing the key moves the account back to the subscription.
  const cleared = await postTo('/api/account-key', { clear: true })
  assert.deepEqual([cleared.status, cleared.data.account, cleared.data.key, existsSync(KEY_FILE)],
    [200, 'subscription', { set: false, ending: null }, false])
  const back = await postTo('/api/account', { account: 'subscription' })
  assert.deepEqual([back.status, back.data.account], [200, 'subscription'])
})

test('the budget saves through lib/account.py, and a budget that isn\'t a positive number of dollars is refused',
  async () => {
    const on = await postTo('/api/api-budget', { on: true, dollars: 49.5 })
    assert.deepEqual([on.status, on.data.budget, hold().apiBudget], [200, { on: true, dollars: 49.5 },
      { on: true, dollars: 49.5 }])
    const off = await postTo('/api/api-budget', { on: false })
    assert.deepEqual([off.status, hold().apiBudget], [200, { on: false, dollars: 49.5 }], 'off keeps the amount')
    for (const bad of [{ on: true, dollars: 0 }, { on: true, dollars: -5 }, { on: true, dollars: '200' },
      { on: true, dollars: 12.345 }, { on: true, dollars: 1e12 }, { on: true }, { on: 'yes', dollars: 20 }, {}]) {
      const refused = await postTo('/api/api-budget', bad)
      assert.deepEqual([refused.status, refused.data.error], [400,
        'the API budget is a positive number of dollars, with at most two decimals'], JSON.stringify(bad))
    }
    assert.deepEqual(hold().apiBudget, { on: false, dollars: 49.5 }, 'a refused budget changes nothing')
    assert.deepEqual([200, 0.01, 49.99, 1234.5].map(budgetAmount), [true, true, true, true])
  })

test('a loopback address is 127.0.0.0/8, ::1, or a mapped 127 address', () => {
  assert.deepEqual(['127.0.0.1', '127.1.2.3', '::1', '::ffff:127.0.0.1', '::FFFF:127.0.0.9'].map(loopback),
    [true, true, true, true, true])
  assert.deepEqual(['192.168.1.5', '10.0.0.1', '::ffff:192.168.1.5', 'fe80::1', '', undefined, '127.example']
    .map(loopback), [false, false, false, false, false, false, false])
})

/** The server's end of a connection this process opened to `address`, one of this machine's own non-loopback ones. */
async function connectionFrom(address: string): Promise<Socket> {
  const server = createServer()
  const accepted = new Promise<Socket>(resolve => server.once('connection', resolve))
  await new Promise<void>(listening => server.listen(0, address, listening))
  const client = connect((server.address() as AddressInfo).port, address)
  const socket = await accepted
  server.close()
  client.unref()
  socket.unref()
  return socket
}

test('the server takes a key only from a loopback address, and refuses it otherwise with a message', async t => {
  const address = Object.values(networkInterfaces()).flat()
    .find(one => one?.family === 'IPv4' && !one.internal)?.address
  if (address === undefined) {
    t.skip('this machine has no IPv4 address but loopback')
    return
  }
  const socket = await connectionFrom(address)
  assert.equal(socket.remoteAddress, address)
  const output = await logged(async () => {
    const refused = await postTo('/api/account-key', { key: KEY }, undefined, socket)
    assert.deepEqual([refused.status, refused.data.error], [403, 'the server takes an API key only from this machine'])
    const cleared = await postTo('/api/account-key', { clear: true }, undefined, socket)
    assert.equal(cleared.status, 403)
  })
  assert.equal(existsSync(KEY_FILE), false)
  assert.doesNotMatch(output, /made-up/)
  // The same connection may still switch the account, which holds no key.
  assert.equal((await postTo('/api/account', { account: 'subscription' }, undefined, socket)).status, 200)
})
