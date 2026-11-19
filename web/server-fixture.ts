// Request helpers for the web tests that call the server's handler as the page would.
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Socket } from 'node:net'
import { Readable } from 'node:stream'

import { handle } from './server.ts'
import { OWN_SOCKET } from './own-socket.ts'

/** The headers of the page's own JSON post. */
export const JSON_POST = { host: '127.0.0.1:7480', origin: 'http://127.0.0.1:7480', 'content-type': 'application/json' }

/**
 * Posts JSON to the server's handler as the page would, or with other `headers`, and returns its status and answer.
 */
export async function postTo(path: string, data: unknown, headers: Record<string, string | undefined> = JSON_POST,
  socket: Socket = OWN_SOCKET): Promise<{ status: number; data: Record<string, unknown> }> {
  const req = Object.assign(Readable.from([JSON.stringify(data)]), { method: 'POST', url: path, headers, socket })
  let status = 0
  let text = ''
  const res = { headersSent: false, setHeader: () => res, writeHead: (code: number) => { status = code; return res },
    end: (sent: string) => { text = sent } }
  await handle(req as unknown as IncomingMessage, res as unknown as ServerResponse)
  return { status, data: text ? JSON.parse(text) : {} }
}

/** Asks the server's handler for `path` as the page would, and returns its status, headers, and body. */
export async function getFrom(path: string):
  Promise<{ status: number; headers: Record<string, string>; body: Buffer }> {
  const req = Object.assign(Readable.from([]),
    { method: 'GET', url: path, headers: { host: '127.0.0.1:7480' }, socket: OWN_SOCKET })
  let status = 0
  const headers: Record<string, string> = {}
  let body = Buffer.alloc(0)
  const res = { headersSent: false, setHeader: (name: string, value: string) => { headers[name] = value; return res },
    writeHead: (code: number, sent: Record<string, string> = {}) => {
      status = code
      Object.assign(headers, sent)
      return res
    },
    end: (sent: string | Buffer) => { body = Buffer.from(sent) } }
  await handle(req as unknown as IncomingMessage, res as unknown as ServerResponse)
  return { status, headers, body }
}
