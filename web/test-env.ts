// Points the server's config and state folders at a new temporary folder before its modules read them, so a test file
// that imports this first reads and writes no projects file, store, or saved state of the person running it.
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export const TEST_HOME = mkdtempSync(join(tmpdir(), 'agent-desk-web-'))
process.env.XDG_CONFIG_HOME = join(TEST_HOME, 'config')
process.env.XDG_STATE_HOME = join(TEST_HOME, 'state')
