// Run with: node --test web/
// First, so the server's modules read the temporary folders it sets.
import { TEST_HOME } from './test-env.ts'
import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'

import { shortPath, splitSkill } from './chore-text.ts'
import { getFrom } from './server-fixture.ts'
import { desks } from './state.ts'
import type { Desk } from './state.ts'

/** Writes `text` to `path`, making its folders. */
function put(path: string, text: string): void {
  mkdirSync(join(path, '..'), { recursive: true })
  writeFileSync(path, text)
}

test('a skill\'s description comes from its front matter, on one line, quoted, or folded over later lines', () => {
  assert.deepEqual(splitSkill('---\nname: audit\ndescription: Audit one area.\n---\n\n# Audit\n'),
    { description: 'Audit one area.', body: '\n# Audit\n' })
  assert.equal(splitSkill('---\ndescription: "Say \\"hi\\"."\nname: x\n---\nBody').description, 'Say "hi".')
  assert.equal(splitSkill("---\ndescription: 'It''s here.'\n---\n").description, "It's here.")
  assert.equal(splitSkill('---\nname: add-tests\ndescription:\n  Scan the repository\n  for files.\n---\nBody')
    .description, 'Scan the repository for files.')
  assert.equal(splitSkill('---\ndescription: >-\n  Folded\n  text.\nother: 1\n---\n').description, 'Folded text.')
  assert.deepEqual(splitSkill('﻿---\r\nname: x\r\n---\r\nBody\r\n'), { description: '', body: 'Body\n' })
  // A command file without front matter is all body.
  assert.deepEqual(splitSkill('Tidy one file.\n'), { description: '', body: 'Tidy one file.\n' })
})

test('a path in the home folder shows as ~', () => {
  assert.equal(shortPath('/home/me/.claude/skills/audit/SKILL.md', '/home/me'), '~/.claude/skills/audit/SKILL.md')
  assert.equal(shortPath('/home/meadow/x', '/home/me'), '/home/meadow/x')
  assert.equal(shortPath('/srv/x', '/home/me'), '/srv/x')
})

test('/api/chore-text reads only a chore the project lists, from wherever Claude Code finds its skill, with the '
  + 'project\'s profile', async () => {
  const main = join(TEST_HOME, 'barn')
  const home = join(TEST_HOME, 'claude')
  const plugin = join(TEST_HOME, 'plugin')
  put(join(main, '.agent-desk.json'), '{}\n')
  put(join(home, 'skills', 'refactor', 'SKILL.md'),
    '---\nname: refactor\ndescription: Find one `refactor`.\n---\n\n# Refactor\n\nStep **one**.\n')
  put(join(main, '.claude', 'skills', 'audit', 'SKILL.md'), '---\ndescription: Audit one area.\n---\n# Audit\n')
  put(join(main, '.agent-skills', 'audit', 'PROFILE.md'), '# Audit Profile\n\n## Harm\n')
  put(join(plugin, '.claude-plugin', 'plugin.json'), '{"name": "tools"}\n')
  put(join(plugin, 'skills', 'lint', 'SKILL.md'), '---\ndescription: Lint it.\n---\nLint.\n')
  put(join(home, 'commands', 'tidy.md'), 'Tidy one file.\n')
  // A skill the project doesn't list as a chore, which no request may read.
  put(join(home, 'skills', 'secret', 'SKILL.md'), '---\ndescription: Not a chore.\n---\nNo.\n')
  const saved = { home: process.env.CLAUDE_CONFIG_DIR, plugins: process.env.CLAUDE_CODE_PLUGIN_DIRS }
  process.env.CLAUDE_CONFIG_DIR = home
  process.env.CLAUDE_CODE_PLUGIN_DIRS = plugin
  desks.set('barn', { id: 'barn', repo: 'me/barn', main,
    chores: ['refactor', 'audit', 'tools:lint', 'tidy', 'gone', '../secret'] } as Desk)
  const read = async (name: string, desk = 'barn') => {
    const got = await getFrom(`/api/chore-text?desk=${desk}&name=${encodeURIComponent(name)}`)
    return { status: got.status, data: JSON.parse(got.body.toString()) as Record<string, unknown> }
  }
  try {
    const refactor = await read('refactor')
    assert.equal(refactor.status, 200)
    assert.match(String(refactor.data.description), /Find one <code>refactor<\/code>\./)
    assert.match(String(refactor.data.html), /<h1[^>]*>Refactor<\/h1>/)
    assert.match(String(refactor.data.html), /<strong>one<\/strong>/)
    assert.doesNotMatch(String(refactor.data.html), /description:/)
    assert.equal(refactor.data.path, shortPath(join(home, 'skills', 'refactor', 'SKILL.md')))
    assert.equal(refactor.data.warning, null)
    assert.equal(refactor.data.profile, null)

    // The checkout's own skill, and its profile from .agent-skills.
    const audit = await read('audit')
    assert.equal(audit.data.path, shortPath(join(main, '.claude', 'skills', 'audit', 'SKILL.md')))
    assert.match(String(audit.data.profile), /<h2[^>]*>Harm<\/h2>/)
    assert.equal(audit.data.profilePath, '.agent-skills/audit/PROFILE.md')

    const lint = await read('tools:lint')
    assert.equal(lint.data.path, shortPath(join(plugin, 'skills', 'lint', 'SKILL.md')))
    assert.match(String(lint.data.description), /Lint it\./)

    const tidy = await read('tidy')
    assert.equal(tidy.data.path, shortPath(join(home, 'commands', 'tidy.md')))
    assert.equal(tidy.data.description, '')
    assert.match(String(tidy.data.html), /Tidy one file\./)

    // A skill Claude Code can't find shows the warning agent-desk chores status gives.
    const gone = await read('gone')
    assert.equal(gone.status, 200)
    assert.equal(gone.data.html, '')
    assert.match(String(gone.data.warning), /^Warning: Claude Code finds no skill named gone, so the gone chore fails/)

    // A name the project doesn't list, a path, even one the config lists, and another project's chore are not found.
    for (const [name, desk] of [['secret', 'barn'], ['../secret', 'barn'], ['/etc/passwd', 'barn'],
      ['refactor', 'other']] as const) {
      const got = await read(name, desk)
      assert.equal(got.status, 404, name)
      assert.equal(JSON.stringify(got.data).includes('Not a chore'), false, name)
    }
  } finally {
    desks.delete('barn')
    process.env.CLAUDE_CONFIG_DIR = saved.home
    process.env.CLAUDE_CODE_PLUGIN_DIRS = saved.plugins
    if (saved.home === undefined) delete process.env.CLAUDE_CONFIG_DIR
    if (saved.plugins === undefined) delete process.env.CLAUDE_CODE_PLUGIN_DIRS
  }
})
