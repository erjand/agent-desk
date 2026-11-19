// Run with: node --test web/
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'

import { codeDigest, codeNotice } from './code.ts'

const root = mkdtempSync(join(tmpdir(), 'agent-desk-code-'))
after(() => rmSync(root, { recursive: true, force: true }))

function write(path: string, text: string): void {
  mkdirSync(join(root, path, '..'), { recursive: true })
  writeFileSync(join(root, path), text)
}

write('web/server.ts', 'serve()\n')
write('inbox/hooks/board.ts', 'board()\n')

test('a change to a module of the server or the board changes the digest, and a test, a page file, or a skill does not',
  async () => {
    const started = await codeDigest(root)
    assert.match(started ?? '', /^[0-9a-f]{64}$/)
    write('web/server.test.ts', 'test()\n')
    write('web/app.js', 'page()\n')
    write('inbox/skills/work-issue/SKILL.md', 'skill\n')
    assert.equal(await codeDigest(root), started)
    write('inbox/hooks/board.ts', 'board(2)\n')
    assert.notEqual(await codeDigest(root), started)
    write('inbox/hooks/board.ts', 'board()\n')
    assert.equal(await codeDigest(root), started)
    write('web/code.ts', 'new()\n')
    assert.notEqual(await codeDigest(root), started)
    rmSync(join(root, 'web', 'code.ts'))
  })

test('a checkout without the server\'s folders has no digest', async () => {
  assert.equal(await codeDigest(join(root, 'missing')), null)
})

test('the notice says to restart the server once its code differs from what the server started with', async () => {
  const started = await codeDigest(root)
  assert.equal(codeNotice(started, started), null)
  assert.equal(codeNotice(started, null), null)
  assert.equal(codeNotice(null, started), null)
  write('inbox/hooks/board.ts', 'board(3)\n')
  assert.equal(codeNotice(started, await codeDigest(root)), 'Restart the web server.')
})
