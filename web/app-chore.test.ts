// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { choreCard, choreId, ChoreText } from './app-chore.js'

type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]

/** Every element in a drawn tree, depth first, including the components it names but doesn't draw. */
function all(node: Drawn): { type?: unknown; props: Record<string, unknown> }[] {
  if (Array.isArray(node)) {
    return node.flatMap(all)
  }
  if (node === null || typeof node !== 'object' || !node.props) {
    return []
  }
  return [{ type: node.type, props: node.props }, ...all(node.props.children as Drawn)]
}

/** The text a drawn tree holds, without the components it names. */
function textOf(node: Drawn): string {
  if (Array.isArray(node)) {
    return node.map(textOf).join('')
  }
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }
  return node && typeof node === 'object' && node.props ? textOf(node.props.children as Drawn) : ''
}

const settle = () => new Promise(resolve => setTimeout(resolve, 0))

/** Draws ChoreText for a chore whose read gives `answer`, once the read ends. */
async function drawn(name: string, answer: Record<string, unknown>) {
  const real = globalThis.fetch
  const asked: string[] = []
  globalThis.fetch = (async (url: string) => {
    asked.push(String(url))
    return { ok: true, json: async () => answer }
  }) as unknown as typeof fetch
  const parser = (globalThis as { DOMParser?: unknown }).DOMParser
  Object.assign(globalThis, { DOMParser: class {
    parseFromString = (markup: string) => ({ querySelectorAll: () => [],
      body: { querySelectorAll: () => [], innerHTML: markup } })
  } })
  try {
    const first = ChoreText({ desk: 'barn', name })
    await settle()
    return { first, tree: ChoreText({ desk: 'barn', name }) as Drawn, asked }
  } finally {
    globalThis.fetch = real
    Object.assign(globalThis, { DOMParser: parser })
  }
}

test('a Chores row\'s card and Queued\'s card for the same chore share an id, so the panel stays on it', () => {
  const desk = { id: 'barn' }
  const chore = { name: 'audit', every: '7d', last: 'never', next: 'now' }
  assert.equal(choreCard(desk, chore).id, choreId('barn', 'audit'))
  assert.equal(choreCard(desk, chore, { stage: 'queued' }).id, 'barn:chore-audit')
  assert.deepEqual(choreCard(desk, chore, { note: 'chore, next now' }), { id: 'barn:chore-audit', desk: 'barn',
    title: 'audit', key: '', actions: [], chore, note: 'chore, next now' })
})

test('a chore\'s page reads its text, then shows its description, its skill and path, and the profile', async () => {
  const { first, tree, asked } = await drawn('tools:audit', { name: 'tools:audit', description: '<p>Audit it.</p>',
    html: '<h1>Audit</h1>', path: '~/.claude/skills/audit/SKILL.md', warning: null, profile: '<h2>Harm</h2>',
    profilePath: '.agent-skills/audit/PROFILE.md' })
  assert.match(textOf(first as Drawn), /Reading/)
  assert.deepEqual(asked, ['/api/chore-text?desk=barn&name=tools%3Aaudit'])
  const elements = all(tree)
  assert.equal(elements.filter(one => one.props.class === 'chore-lead').length, 1)
  const boxes = elements.filter(one => one.props.markup !== undefined && one.props.path !== undefined)
  assert.deepEqual(boxes.map(one => [one.props.markup, one.props.path]), [
    ['<h1>Audit</h1>', '~/.claude/skills/audit/SKILL.md'],
    ['<h2>Harm</h2>', '.agent-skills/audit/PROFILE.md']])
  assert.match(textOf(tree), /This project's profile/)
  assert.equal(elements.some(one => one.props.class === 'chore-warning'), false)
})

test('a chore whose skill Claude Code can\'t find shows the warning in place of the text, and no profile heading',
  async () => {
    const warning = 'Warning: Claude Code finds no skill named gone, so the gone chore fails each run.'
    const { tree } = await drawn('gone', { name: 'gone', description: '', html: '', path: '', warning, profile: null,
      profilePath: null })
    const elements = all(tree)
    assert.equal(textOf(elements.find(one => one.props.class === 'chore-warning')?.props.children as Drawn), warning)
    assert.equal(elements.some(one => one.props.path !== undefined), false)
    assert.doesNotMatch(textOf(tree), /profile/)
  })
