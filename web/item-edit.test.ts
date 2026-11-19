// Run with: node --test web/
// Tests of an issue's or a review's Edit and task boxes: the title and description the page reads, and the writes
// that tick a task, rename an item, and rewrite its description through the store as you.
// First, so the server's modules read the temporary folders it sets.
import { TEST_HOME } from './test-env.ts'
import assert from 'node:assert/strict'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { after, test } from 'node:test'

import { LABEL_RULE, MAX_LABEL } from './issue-create.ts'
import { editItem, sourceOf, tickTask } from './item-edit.ts'
import { bodyVersion, issuePreviewOf, MAX_COMMENT, MAX_TITLE, previewOf } from './review.ts'
import { getFrom, postTo } from './server-fixture.ts'
import { desks, host } from './state.ts'
import type { Desk } from './state.ts'
import { storeFixture } from './store-fixture.ts'
import { storedLabels, storePath } from './store.ts'

/** The review in the store, which fixes issue #1. */
const REVIEW = 2

const MAIN = join(TEST_HOME, 'yard')
mkdirSync(MAIN, { recursive: true })
mkdirSync(dirname(storePath()), { recursive: true })
storeFixture(['p = "me/yard"', 'store.create(p, "issue", "Paint the fence")',
  'store.create(p, "review", "Paint it", "Fixes #1.", branch="agent/issue-1", fixes=[1])'].join('\n'), storePath())
const desk = { id: 'yard', repo: 'me/yard', owner: 'me', title: 'Yard', main: MAIN, chores: [], board: null, at: 0,
  refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {}, schedulePr: null, modelsFor: '',
  known: null, refreshes: 0, commands: 0 } as Desk

// The board each write refreshes reads nothing.
const real = host.run
host.run = async () => null
after(async () => {
  // The board reads each write set off finish before the stand-in goes.
  await new Promise(resolve => setTimeout(resolve, 50))
  host.run = real
})

/** A new issue in the store with `body`, by its number. */
function newIssue(body: string): number {
  storeFixture(`store.create("me/yard", "issue", "Tasks", ${JSON.stringify(body)})`, storePath())
  const db = new DatabaseSync(storePath(), { readOnly: true })
  try {
    return Number((db.prepare('SELECT MAX(number) AS number FROM items WHERE project = ?').get('me/yard') as
      { number: number }).number)
  } finally {
    db.close()
  }
}

test('a tick rewrites the Nth task item\'s marker in the stored description, past markers in code blocks',
  async () => {
  const body = '```\n- [ ] fenced\n```\n\n    - [ ] indented\n\n- [ ] first\n- [x] second\n'
  const number = newIssue(body)
  const done = await tickTask(desk, number, 0, true, bodyVersion(body))
  const ticked = body.replace('- [ ] first', '- [x] first')
  assert.deepEqual(done, { version: bodyVersion(ticked) })
  assert.equal(issuePreviewOf(desk, number)?.version, bodyVersion(ticked))
  const unticked = ticked.replace('- [x] second', '- [ ] second')
  assert.deepEqual(await tickTask(desk, number, 1, false, bodyVersion(ticked)), { version: bodyVersion(unticked) })
  const db = new DatabaseSync(storePath(), { readOnly: true })
  try {
    assert.equal((db.prepare('SELECT body FROM items WHERE project = ? AND number = ?').get('me/yard', number) as
      { body: string }).body, unticked)
  } finally {
    db.close()
  }
})

test('a tick on a description changed since the page read it, or on no task or no issue, changes nothing',
  async () => {
  const body = '- [ ] first\n- [ ] second'
  const number = newIssue(body)
  assert.deepEqual(await tickTask(desk, number, 0, true, bodyVersion('- [ ] first')),
    { status: 409, error: `#${number}'s description changed since the page read it` })
  assert.deepEqual(await tickTask(desk, number, 2, true, bodyVersion(body)),
    { status: 409, error: `#${number}'s description has no task 3` })
  assert.deepEqual(await tickTask(desk, REVIEW, 0, true, bodyVersion('Fixes #1.')), { status: 404, error: 'no such issue' })
  assert.deepEqual(await tickTask(desk, 999, 0, true, ''), { status: 404, error: 'no such issue' })
  assert.equal(issuePreviewOf(desk, number)?.version, bodyVersion(body))
})

test('the page\'s tick goes through /api/task, which refuses a stale version or a malformed request', async () => {
  desks.set('yard', desk)
  try {
    const body = '- [ ] first'
    const number = newIssue(body)
    assert.deepEqual(await postTo('/api/task', { desk: 'yard', number, task: 0, checked: true, version: 'old' }),
      { status: 409, data: { error: `#${number}'s description changed since the page read it` } })
    assert.equal((await postTo('/api/task', { desk: 'yard', number, task: '0', checked: true,
      version: bodyVersion(body) })).status, 400)
    assert.deepEqual(await postTo('/api/task', { desk: 'yard', number, task: 0, checked: true,
      version: bodyVersion(body) }), { status: 200, data: { version: bodyVersion('- [x] first') } })
    assert.equal(issuePreviewOf(desk, number)?.version, bodyVersion('- [x] first'))
  } finally {
    desks.delete('yard')
  }
})

test('Edit renames an issue or a review and rewrites its description, even to nothing', async () => {
  const body = '- [ ] first\n\nSome words.'
  const number = newIssue(body)
  assert.deepEqual(sourceOf(desk, number), { number, title: 'Tasks', body, version: bodyVersion(body) })
  assert.deepEqual(await editItem(desk, number, { title: '  Tick the\nboxes  ' }), { version: bodyVersion(body) })
  assert.equal(sourceOf(desk, number)?.title, 'Tick the boxes')
  // A title that starts with a dash stays a title, never one of the store's options.
  await editItem(desk, number, { title: '--repo other/game' })
  assert.equal(sourceOf(desk, number)?.title, '--repo other/game')
  const rewritten = '- [ ] first\n- [ ] second'
  assert.deepEqual(await editItem(desk, number, { body: rewritten, version: bodyVersion(body) }),
    { version: bodyVersion(rewritten) })
  assert.equal(issuePreviewOf(desk, number)?.version, bodyVersion(rewritten))
  // A tick reads the new version.
  assert.deepEqual(await tickTask(desk, number, 1, true, bodyVersion(rewritten)),
    { version: bodyVersion('- [ ] first\n- [x] second') })
  assert.deepEqual(await editItem(desk, number, { body: '', version: bodyVersion('- [ ] first\n- [x] second') }),
    { version: bodyVersion('') })
  assert.equal(sourceOf(desk, number)?.body, '')
  // A review's title and description edit the same way.
  const review = sourceOf(desk, REVIEW)
  assert.ok(review)
  await editItem(desk, REVIEW, { title: 'Paint the walls' })
  assert.deepEqual(await editItem(desk, REVIEW, { body: 'Fixes #1.\n\nNow with paint.', version: review.version }),
    { version: bodyVersion('Fixes #1.\n\nNow with paint.') })
  assert.equal((await previewOf(desk, REVIEW))?.title, 'Paint the walls')
  assert.equal(sourceOf(desk, REVIEW)?.body, 'Fixes #1.\n\nNow with paint.')
  assert.equal(sourceOf(desk, 999), null)
})

test('Edit refuses a stale description, a bad title or description, and an unknown item, and changes nothing',
  async () => {
  const body = 'The bridge falls.'
  const number = newIssue(body)
  const version = bodyVersion(body)
  assert.deepEqual(await editItem(desk, number, { body: 'The bridge stands.', version: bodyVersion('Older.') }),
    { status: 409, error: `#${number}'s description changed since the page read it` })
  assert.deepEqual(await editItem(desk, number, { title: ' \n ' }), { status: 400, error: 'a title can\'t be empty' })
  assert.deepEqual(await editItem(desk, number, { title: 'x'.repeat(MAX_TITLE + 1) }),
    { status: 400, error: `a title holds at most ${MAX_TITLE} characters` })
  assert.deepEqual(await editItem(desk, number, { body: 'x'.repeat(MAX_COMMENT + 1), version }),
    { status: 400, error: `a description holds at most ${MAX_COMMENT} characters` })
  const malformed = { status: 400,
    error: 'an edit names a new title, a new description with the version it replaces, or labels to add or remove' }
  assert.deepEqual(await editItem(desk, number, { body: 'No version.' }), malformed)
  assert.deepEqual(await editItem(desk, number, { title: 'Both', body: 'At once.', version }), malformed)
  assert.deepEqual(await editItem(desk, number, {}), malformed)
  assert.deepEqual(await editItem(desk, 999, { title: 'Nothing' }), { status: 404, error: 'no such issue or review' })
  assert.deepEqual(await editItem(desk, 999, { body: '', version: bodyVersion('') }),
    { status: 404, error: 'no such issue or review' })
  assert.deepEqual(sourceOf(desk, number), { number, title: 'Tasks', body, version })
  // The longest title fits.
  assert.deepEqual(await editItem(desk, number, { title: 'x'.repeat(MAX_TITLE) }), { version })
})

test('the page\'s Edit reads the source through /api/source and writes through /api/edit', async () => {
  desks.set('yard', desk)
  try {
    const body = 'Fix the *bridge*.'
    const number = newIssue(body)
    const read = await getFrom(`/api/source?desk=yard&number=${number}`)
    assert.equal(read.status, 200)
    assert.deepEqual(JSON.parse(read.body.toString()), { number, title: 'Tasks', body, version: bodyVersion(body) })
    assert.equal((await getFrom('/api/source?desk=yard&number=999')).status, 404)
    assert.equal((await getFrom(`/api/source?desk=other&number=${number}`)).status, 404)
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, title: 'The bridge' }),
      { status: 200, data: { version: bodyVersion(body) } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, body: 'Fixed.', version: bodyVersion(body) }),
      { status: 200, data: { version: bodyVersion('Fixed.') } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, body: 'Again.', version: bodyVersion(body) }),
      { status: 409, data: { error: `#${number}'s description changed since the page read it` } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, title: '' }),
      { status: 400, data: { error: 'a title can\'t be empty' } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number: 999, title: 'Nothing' }),
      { status: 404, data: { error: 'no such issue or review' } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'other', number, title: 'Elsewhere' }),
      { status: 404, data: { error: 'no such project' } })
    assert.deepEqual(sourceOf(desk, number), { number, title: 'The bridge', body: 'Fixed.',
      version: bodyVersion('Fixed.') })
  } finally {
    desks.delete('yard')
  }
})

/** Issue `number`'s workflow state in the store. */
function stateOf(number: number): string {
  const db = new DatabaseSync(storePath(), { readOnly: true })
  try {
    return (db.prepare('SELECT state FROM items WHERE project = ? AND number = ?').get('me/yard', number) as
      { state: string }).state
  } finally {
    db.close()
  }
}

test('the Labels picker adds and takes off an issue\'s or a review\'s labels, and returns the labels after',
  async () => {
  const number = newIssue('Fix the gate.')
  assert.deepEqual(await editItem(desk, number, { addLabels: ['bug'] }), { labels: ['bug'] })
  // A new name, one starting with a dash, and one the item carries already all add as labels.
  assert.deepEqual(await editItem(desk, number, { addLabels: ['--repo other/game', 'bug', 'web'] }),
    { labels: ['--repo other/game', 'bug', 'web'] })
  assert.deepEqual(await editItem(desk, number, { removeLabels: ['--repo other/game', 'missing'] }),
    { labels: ['bug', 'web'] })
  assert.deepEqual(await editItem(desk, number, { addLabels: ['idea'], removeLabels: ['bug'] }),
    { labels: ['idea', 'web'] })
  assert.deepEqual(storedLabels('me/yard', number), ['idea', 'web'])
  // The longest name fits, and a review takes labels the same way.
  assert.deepEqual(await editItem(desk, number, { addLabels: ['x'.repeat(MAX_LABEL)] }),
    { labels: ['idea', 'web', 'x'.repeat(MAX_LABEL)] })
  assert.deepEqual(await editItem(desk, REVIEW, { addLabels: ['web'] }), { labels: ['web'] })
  assert.deepEqual(await editItem(desk, REVIEW, { removeLabels: ['web'] }), { labels: [] })
})

test('the Labels picker refuses the labels the store keeps as a state, a bad name, and an unknown item', async () => {
  const number = newIssue('Fix the latch.')
  await editItem(desk, number, { addLabels: ['bug'] })
  for (const state of ['agent-ready', 'in-progress', 'waiting', 'needs-design', 'on-hold']) {
    for (const sent of [{ addLabels: [state] }, { removeLabels: [state] }, { addLabels: ['web', state] }]) {
      assert.deepEqual(await editItem(desk, number, sent), { status: 400, error: `the page leaves ${state} to the`
        + ' card\'s buttons and the agent\'s runs, since the store keeps it as the issue\'s state' }, state)
    }
  }
  for (const name of ['', ' bug', 'bug ', 'a,b', 'two\nlines', 'x'.repeat(MAX_LABEL + 1), 7, null]) {
    assert.deepEqual(await editItem(desk, number, { addLabels: [name] }), { status: 400, error: LABEL_RULE },
      String(name))
    assert.deepEqual(await editItem(desk, number, { removeLabels: [name] }), { status: 400, error: LABEL_RULE },
      String(name))
  }
  const malformed = { status: 400,
    error: 'an edit names a new title, a new description with the version it replaces, or labels to add or remove' }
  assert.deepEqual(await editItem(desk, number, { addLabels: 'bug' }), malformed)
  assert.deepEqual(await editItem(desk, number, { addLabels: [], removeLabels: [] }), malformed)
  assert.deepEqual(await editItem(desk, number, { title: 'Both', addLabels: ['web'] }), malformed)
  assert.deepEqual(await editItem(desk, number, { body: 'Both', version: bodyVersion('Fix the latch.'),
    removeLabels: ['bug'] }), malformed)
  assert.deepEqual(await editItem(desk, number, { addLabels: ['web'], removeLabels: ['web'] }),
    { status: 400, error: 'a label is added or removed, not both' })
  assert.deepEqual(await editItem(desk, 999, { addLabels: ['web'] }), { status: 404, error: 'no such issue or review' })
  assert.deepEqual(storedLabels('me/yard', number), ['bug'])
  assert.equal(stateOf(number), 'open')
})

test('the page\'s Labels picker writes through /api/edit, where backlog goes on as a label, as issue edit puts it',
  async () => {
  desks.set('yard', desk)
  try {
    const number = newIssue('Oil the hinge.')
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, addLabels: ['backlog'] }),
      { status: 200, data: { labels: ['backlog'] } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, removeLabels: ['backlog'] }),
      { status: 200, data: { labels: [] } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, addLabels: ['agent-ready'] }),
      { status: 400, data: { error: 'the page leaves agent-ready to the card\'s buttons and the agent\'s runs, since'
        + ' the store keeps it as the issue\'s state' } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number, addLabels: ['a,b'] }),
      { status: 400, data: { error: LABEL_RULE } })
    assert.deepEqual(await postTo('/api/edit', { desk: 'yard', number: 999, addLabels: ['web'] }),
      { status: 404, data: { error: 'no such issue or review' } })
    assert.equal(stateOf(number), 'open')
  } finally {
    desks.delete('yard')
  }
})
