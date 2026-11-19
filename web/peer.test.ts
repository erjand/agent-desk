// Run with: node --test web/
// First, so the server's modules read the temporary folders it sets.
import './test-env.ts'
import assert from 'node:assert/strict'
import type { Socket } from 'node:net'
import { test } from 'node:test'

import { OWN_SOCKET } from './own-socket.ts'
import { ownPeer, refusal, socketUid, tableAddress } from './peer.ts'
import { JSON_POST, postTo } from './server-fixture.ts'

test('the page and its own JSON posts are served', () => {
  assert.equal(refusal('GET', { host: 'localhost:7480' }, 7480), null)
  assert.equal(refusal('POST', JSON_POST, 7480), null)
  assert.equal(refusal('POST', { ...JSON_POST, host: 'localhost:7480', origin: 'http://localhost:7480' }, 7480), null)
})

test('a request through another host name, as DNS rebinding sends, is refused', () => {
  assert.equal(refusal('GET', { host: 'attacker.example:7480' }, 7480), 'unknown host')
  assert.equal(refusal('GET', {}, 7480), 'unknown host')
  assert.equal(refusal('GET', { host: '127.0.0.1:7481' }, 7480), 'unknown host')
})

test('a post from another site, or one a plain form can send, is refused', () => {
  assert.equal(refusal('POST', { ...JSON_POST, origin: 'https://attacker.example' }, 7480), 'cross-origin request')
  assert.equal(refusal('POST', { ...JSON_POST, origin: undefined }, 7480), 'cross-origin request')
  assert.equal(refusal('POST', { ...JSON_POST, 'content-type': 'text/plain' }, 7480), 'not JSON')
  assert.equal(refusal('POST', { ...JSON_POST, 'content-type': 'application/x-www-form-urlencoded' }, 7480),
    'not JSON')
})

// Sample lines of /proc/net/tcp and /proc/net/tcp6 on x86: 127.0.0.1:50000 connected to the server on port 7480, the
// server's own end of it, and a closed connection from port 50001 in TIME_WAIT.
const TCP = [
  '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode',
  '   0: 0100007F:1D38 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 101 1 0 100 0 0 10 0',
  '   1: 0100007F:C350 0100007F:1D38 01 00000000:00000000 00:00000000 00000000  1000        0 102 1 0 20 4 30 10 -1',
  '   2: 0100007F:1D38 0100007F:C350 01 00000000:00000000 00:00000000 00000000  1000        0 103 1 0 20 4 30 10 -1',
  '   3: 0100007F:C351 0100007F:1D38 06 00000000:00000000 03:00001770 00000000     0        0 0 3 0',
].join('\n')
const TCP6_LOCAL = '0000000000000000FFFF00000100007F'
const TCP6 = [
  '  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when' +
    ' retrnsmt   uid  timeout inode',
  `   0: ${TCP6_LOCAL}:C350 ${TCP6_LOCAL}:1D38 01 00000000:00000000 00:00000000 00000000  1001        0 104 1 0 20 4`,
].join('\n')

test('the socket tables write an IPv4 or IPv4-mapped IPv6 address as hex words in the host\'s byte order', () => {
  assert.deepEqual(tableAddress('127.0.0.1', true), { table: '/proc/net/tcp', hex: '0100007F' })
  assert.deepEqual(tableAddress('127.0.0.1', false), { table: '/proc/net/tcp', hex: '7F000001' })
  assert.deepEqual(tableAddress('::ffff:127.0.0.1', true), { table: '/proc/net/tcp6', hex: TCP6_LOCAL })
  assert.deepEqual(tableAddress('::FFFF:127.0.0.1', false),
    { table: '/proc/net/tcp6', hex: '00000000000000000000FFFF7F000001' })
  assert.equal(tableAddress('::1', true), null)
  assert.equal(tableAddress('', true), null)
})

test('the peer\'s uid is the one the socket table gives its end of the connection to the server', () => {
  assert.equal(socketUid(TCP, '0100007F', 50000, 7480), 1000)
  assert.equal(socketUid(TCP6, TCP6_LOCAL, 50000, 7480), 1001)
  // The same socket seen from another server port, a port the table doesn't list, and a closed connection match none.
  assert.equal(socketUid(TCP, '0100007F', 50000, 7481), null)
  assert.equal(socketUid(TCP, '0100007F', 50002, 7480), null)
  assert.equal(socketUid(TCP, '0100007F', 50001, 7480), null)
  assert.equal(socketUid(TCP, TCP6_LOCAL, 50000, 7480), null)
  assert.equal(socketUid('', '0100007F', 50000, 7480), null)
})

test('a connection from this user\'s process is served, and one the socket table doesn\'t list is not', async () => {
  assert.equal(await ownPeer(OWN_SOCKET), true)
  const unlisted = { remoteAddress: '127.0.0.1', remotePort: 1, localPort: 2 } as Socket
  assert.equal(await ownPeer(unlisted), false)
  assert.equal(await ownPeer({ remoteAddress: '::1', remotePort: 1, localPort: 2 } as Socket), false)
  assert.equal(await ownPeer({} as Socket), false)
  // The uid check comes before the header checks, so another account learns nothing from which header it got wrong.
  for (const headers of [JSON_POST, { host: 'attacker.example:7480' }]) {
    const { status, data } = await postTo('/api/ready', { repo: 'me/live', number: 1 }, headers, unlisted)
    assert.deepEqual([status, data], [403, { error: 'another user' }])
  }
})
