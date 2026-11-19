// A store for the web tests, made by lib/store.py itself so the tests read the schema the scripts write.
import { execFileSync } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const LIB = join(import.meta.dirname, '..', 'lib')

/**
 * Runs `python` with `store`, a lib/store.py Store on the database at `path`, and `db`, its sqlite3 connection, for
 * rows the Store has no method to write, such as a merge's time. Makes a new database when `path` is absent, and
 * returns its path.
 */
export function storeFixture(python: string, path = join(mkdtempSync(join(tmpdir(), 'agent-desk-store-')),
  'desk.sqlite')): string {
  const script = ['import sys', `sys.path.insert(0, ${JSON.stringify(LIB)})`, 'from store import Store',
    'store = Store(sys.argv[1])', 'db = store.db', python, 'store.close()'].join('\n')
  // A branch check runs as the desk, which would otherwise author every row the fixture writes.
  execFileSync('python3', ['-B', '-c', script, path],
    { env: { ...process.env, AGENT_DESK_RUN: '', AGENT_DESK_AUTHOR: '' } })
  return path
}
