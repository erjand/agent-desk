// Run with: node --test web/
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'

import { DESK, HOLD } from '../inbox/hooks/run.ts'
import { childEnv, DESK_HOME, HERE, host } from './state.ts'

test('the programs the server runs lose GH_REPO and the author of a run, and keep the rest of its environment', () => {
  const env = childEnv({ GH_REPO: 'acme/web-app', HOME: '/home/a', GH_HOST: 'x', AGENT_DESK_RUN: '7',
    AGENT_DESK_AUTHOR: 'desk' })
  assert.deepEqual(env, { HOME: '/home/a', GH_HOST: 'x', AGENT_DESK_HOME: DESK_HOME })
})

test('a server started inside an agent-desk run runs its programs without that run\'s project', async () => {
  const saved = process.env.GH_REPO
  process.env.GH_REPO = 'acme/web-app'
  process.env.AGENT_DESK_TEST_KEPT = 'kept'
  try {
    const out = await host.run(['sh', '-c', 'echo "${GH_REPO-unset} $AGENT_DESK_TEST_KEPT"'])
    assert.equal(out, 'unset kept\n')
  } finally {
    if (saved === undefined) delete process.env.GH_REPO
    else process.env.GH_REPO = saved
    delete process.env.AGENT_DESK_TEST_KEPT
  }
})

/** What bash makes of the board's agent-desk command and lib/hold.py path in an environment. */
function resolved(env: NodeJS.ProcessEnv): string[] {
  return execFileSync('bash', ['-c', `printf '%s\\n' "${DESK}" ${HOLD}`], { env, encoding: 'utf8' }).trim().split('\n')
}

test('the board runs agent-desk from PATH, and lib/ from the checkout that command links into', () => {
  const dir = mkdtempSync(join(tmpdir(), 'agent-desk-path-'))
  try {
    const checkout = join(dir, 'some checkout')
    mkdirSync(join(checkout, 'bin'), { recursive: true })
    writeFileSync(join(checkout, 'bin', 'agent-desk'), '#!/bin/sh\n')
    chmodSync(join(checkout, 'bin', 'agent-desk'), 0o755)
    mkdirSync(join(dir, 'bin'))
    symlinkSync(join(checkout, 'bin', 'agent-desk'), join(dir, 'bin', 'agent-desk'))
    assert.deepEqual(resolved({ PATH: `${join(dir, 'bin')}:/usr/bin:/bin` }),
      ['agent-desk', join(checkout, 'lib', 'hold.py')])
    assert.deepEqual(resolved({ PATH: '/usr/bin:/bin', AGENT_DESK_HOME: checkout }),
      [join(checkout, 'bin', 'agent-desk'), join(checkout, 'lib', 'hold.py')])
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('the server runs the board\'s agent-desk commands from its own checkout, or from AGENT_DESK_HOME', async () => {
  assert.equal(DESK_HOME, process.env.AGENT_DESK_HOME || dirname(HERE))
  const out = await host.run(['bash', '-c', `printf '%s\\n' "${DESK}" ${HOLD}`])
  assert.equal(out, `${join(DESK_HOME, 'bin', 'agent-desk')}\n${join(DESK_HOME, 'lib', 'hold.py')}\n`)
})
