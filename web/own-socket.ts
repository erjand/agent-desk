// A connection from this process for the web tests' fake requests, which the server's handler serves as the user's own.
import { connect, createServer } from 'node:net'
import type { AddressInfo, Socket } from 'node:net'

/** The server's end of a loopback connection this process opened, kept open and never holding a test run open. */
async function ownConnection(): Promise<Socket> {
  const server = createServer()
  const accepted = new Promise<Socket>(resolve => server.once('connection', resolve))
  await new Promise<void>(listening => server.listen(0, '127.0.0.1', listening))
  const client = connect((server.address() as AddressInfo).port, '127.0.0.1')
  const socket = await accepted
  server.close()
  client.unref()
  socket.unref()
  return socket
}

export const OWN_SOCKET = await ownConnection()
