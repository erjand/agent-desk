// The web server's checks of who may send it a request: the Host, Origin, and content type a request carries, and the
// account of the process at the other end of its connection, from the kernel's socket tables.
import { readFile } from 'node:fs/promises'
import type { IncomingMessage } from 'node:http'
import { isIPv4 } from 'node:net'
import type { Socket } from 'node:net'
import { endianness } from 'node:os'

/**
 * Why a request is refused, or null to serve it. Any page in the browser can send requests to this machine, so only
 * this server's own page may: the Host header must name this server, which stops a DNS-rebinding site, and a POST
 * must come from the page's origin as JSON, which no other site can send without a CORS preflight this server never
 * answers.
 */
export function refusal(method: string | undefined, headers: IncomingMessage['headers'], port: number): string | null {
  const hosts = [`127.0.0.1:${port}`, `localhost:${port}`]
  if (!hosts.includes(headers.host ?? '')) {
    return 'unknown host'
  }
  if (method === 'GET' || method === 'HEAD') {
    return null
  }
  if (!hosts.map(one => `http://${one}`).includes(headers.origin ?? '')) {
    return 'cross-origin request'
  }
  return (headers['content-type'] ?? '').startsWith('application/json') ? null : 'not JSON'
}

/**
 * Where the kernel's socket tables list a socket at `address`: the table, and the address as that table prints it, in
 * hex words of four bytes each in the host's byte order, so `127.0.0.1` reads `0100007F` on x86. Only an IPv4 address
 * and an IPv4-mapped IPv6 one, such as `::ffff:127.0.0.1`, have one; any other address gets null.
 */
export function tableAddress(address: string, littleEndian = endianness() === 'LE'): { table: string; hex: string } |
  null {
  const mapped = address.toLowerCase().startsWith('::ffff:')
  const ipv4 = mapped ? address.slice('::ffff:'.length) : address
  if (!isIPv4(ipv4)) {
    return null
  }
  const bytes = [...(mapped ? [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff] : []), ...ipv4.split('.').map(Number)]
  let hex = ''
  for (let at = 0; at < bytes.length; at += 4) {
    const word = bytes.slice(at, at + 4)
    hex += (littleEndian ? word.reverse() : word).map(byte => byte.toString(16).padStart(2, '0')).join('')
  }
  return { table: mapped ? '/proc/net/tcp6' : '/proc/net/tcp', hex: hex.toUpperCase() }
}

/**
 * The uid of the socket a kernel socket table, the text of `/proc/net/tcp` or `/proc/net/tcp6`, lists at local
 * address `hex` and `port` and connected to `serverPort`, or null when it lists none. A socket in TIME_WAIT has closed
 * and belongs to no one, so it never matches.
 */
export function socketUid(table: string, hex: string, port: number, serverPort: number): number | null {
  for (const line of table.split('\n')) {
    const [, local = '', remote = '', state, , , , uid = ''] = line.trim().split(/\s+/)
    const [localHex, localPort = ''] = local.split(':')
    const remotePort = remote.split(':')[1] ?? ''
    if (localHex === hex && Number.parseInt(localPort, 16) === port && Number.parseInt(remotePort, 16) === serverPort &&
      state !== '06' && /^\d+$/.test(uid)) {
      return Number(uid)
    }
  }
  return null
}

const verdicts = new WeakMap<Socket, Promise<boolean>>()

/**
 * Whether the process at the other end of `socket`, a connection the server accepted, runs as the server's user. Any
 * account on the machine can reach 127.0.0.1 and send the page's own headers, so the kernel's socket table decides:
 * the socket the peer connected from must belong to this uid, and a socket the table doesn't list is refused. The
 * server asks once per connection, when it accepts it, and every request on it reuses the answer.
 */
export function ownPeer(socket: Socket): Promise<boolean> {
  let verdict = verdicts.get(socket)
  if (verdict === undefined) {
    const { remoteAddress = '', remotePort, localPort } = socket
    const at = tableAddress(remoteAddress)
    verdict = at === null || remotePort === undefined || localPort === undefined ? Promise.resolve(false)
      : readFile(at.table, 'utf8').then(table => socketUid(table, at.hex, remotePort, localPort) === process.getuid?.(),
        () => false)
    verdicts.set(socket, verdict)
  }
  return verdict
}
