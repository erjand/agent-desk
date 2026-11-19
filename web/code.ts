// Whether the server still runs the code its checkout holds. Node loads the server's modules once when it starts, so a
// merge that changes them leaves a running server on the old code until something restarts it: the merge queue
// restarts agent-desk-web.service, and nothing restarts a server started with `agent-desk web run`.
import { createHash } from 'node:crypto'
import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

/** The folders, from the checkout, whose TypeScript modules the server loads when it starts. */
const SERVER_CODE = ['web', 'inbox/hooks']

/** A digest of the server's modules in the checkout at `root`, tests aside, or null when one can't be read. */
export async function codeDigest(root: string): Promise<string | null> {
  const hash = createHash('sha256')
  try {
    for (const folder of SERVER_CODE) {
      const names = (await readdir(join(root, folder)))
        .filter(name => name.endsWith('.ts') && !name.endsWith('.test.ts')).sort()
      for (const name of names) {
        const text = await readFile(join(root, folder, name))
        hash.update(`${folder}/${name} ${text.length}\n`).update(text)
      }
    }
  } catch {
    return null
  }
  return hash.digest('hex')
}

/** The header's notice when the checkout's modules differ from those the server started with, or null. */
export function codeNotice(started: string | null, now: string | null): string | null {
  return started !== null && now !== null && started !== now ? 'Restart the web server.' : null
}
