// Run with: node --test web/
import assert from 'node:assert/strict'
import { mkdtempSync, renameSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'

import { storeFixture } from './store-fixture.ts'
import { choreReviews, hashedColor, labelColors, lastEvent, reviewCosts, storedAnswers, storedComments,
  storedLineComments, storeOpens, storeProblem, useStore } from './store.ts'

test('a store that can\'t be read logs the first error of a streak, names it until it reads, and reopens when replaced',
  t => {
  const folder = mkdtempSync(join(tmpdir(), 'agent-desk-store-'))
  const path = join(folder, 'desk.sqlite')
  const logged = t.mock.method(console, 'error', () => undefined)
  useStore(path)

  // No store yet reads as empty, with nothing wrong.
  assert.equal(lastEvent(), 0)
  assert.equal(storeProblem(), null)

  // A file that isn't a database fails every read, of every query, and only the first is logged.
  writeFileSync(path, 'not a database, '.repeat(512))
  assert.equal(lastEvent(), 0)
  assert.equal(lastEvent(), 0)
  assert.deepEqual(reviewCosts('me/app'), new Map())
  assert.match(storeProblem() ?? '', /not a database/)
  assert.equal(logged.mock.callCount(), 1)
  assert.match(String(logged.mock.calls[0]?.arguments[0]), /can't read the store at .*desk\.sqlite: .*not a database/)
  const opened = storeOpens()

  // A store renamed over it is a new file, which the next read opens.
  const fresh = storeFixture('store.create("me/app", "issue", "Paint the fence")')
  renameSync(fresh, path)
  assert.ok(lastEvent() > 0)
  assert.equal(storeProblem(), null)
  assert.equal(storeOpens(), opened + 1)

  // Reads of the same file keep its handle.
  lastEvent()
  assert.equal(storeOpens(), opened + 1)
})

test('label colors come from the import, with a color hashed from its name for a label the import never saw', () => {
  useStore(storeFixture(['p = "me/app"', 'store.create(p, "issue", "Paint the fence", labels=["enhancement", "paint"])',
    'store.create("me/other", "issue", "Elsewhere", labels=["other"])',
    'store.import_label_colors(p, {"enhancement": "A2EEEF", "on-hold": "d4c5f9", "broken": "red"})'].join('\n')))
  assert.deepEqual(labelColors('me/app'), { enhancement: 'a2eeef', 'on-hold': 'd4c5f9', broken: hashedColor('broken'),
    paint: hashedColor('paint') })
  assert.deepEqual(labelColors('me/other'), { other: hashedColor('other') })
  assert.equal(storeProblem(), null)
})

test('a hashed color is six hex digits, the same for a name each time, and differs between names', () => {
  const colors = ['enhancement', 'bug', 'serial:web', 'idea', 'é'].map(hashedColor)
  for (const color of colors) {
    assert.match(color, /^[0-9a-f]{6}$/)
  }
  assert.equal(hashedColor('bug'), colors[1])
  assert.equal(new Set(colors).size, colors.length)
})

test('a store from before label colors reads its labels with hashed colors, and names no problem', () => {
  const path = storeFixture('store.create("me/app", "issue", "Paint the fence", labels=["paint"])')
  const db = new DatabaseSync(path)
  db.exec('DROP TABLE label_colors')
  db.close()
  useStore(path)
  assert.deepEqual(labelColors('me/app'), { paint: hashedColor('paint') })
  assert.equal(storeProblem(), null)
})

test('chore reviews are the reviews on agent/chore-* branches, each OPEN, CLOSED, or MERGED', () => {
  useStore(storeFixture(['p = "me/app"', 'store.create(p, "review", "Audit", branch="agent/chore-audit")',
    'store.create(p, "review", "Schedule", branch="agent/chore-schedule")',
    'store.create(p, "review", "Comments", branch="agent/chore-comment-one")', 'store.merge(p, 3, "abc123")',
    'store.create(p, "review", "Tests", branch="agent/chore-untested-one")', 'store.set_state(p, 4, "closed")',
    'store.create(p, "review", "Fix the gate", branch="agent/issue-9")',
    'store.create(p, "issue", "agent/chore-audit")',
    'store.create("me/other", "review", "Audit", branch="agent/chore-audit")'].join('\n')))
  assert.deepEqual(choreReviews('me/app').map(one => ({ ...one })), [
    { number: 1, branch: 'agent/chore-audit', state: 'OPEN' },
    { number: 2, branch: 'agent/chore-schedule', state: 'OPEN' },
    { number: 3, branch: 'agent/chore-comment-one', state: 'MERGED' },
    { number: 4, branch: 'agent/chore-untested-one', state: 'CLOSED' }])
})

// Writes the dispatcher's answer to the comment just added, whose `comment-added` event is the store's newest.
const ANSWER = 'answer = lambda n, c, how, run=None: store.comment_answered(p, n, db.execute("SELECT MAX(id) FROM events")'
  + '.fetchone()[0], how, c, run)'

test('a comment the dispatcher answered with a run names it, and one answered another way, or not yet, names none', () => {
  useStore(storeFixture(['p = "me/app"', ANSWER, 'store.create(p, "issue", "Paint the fence")',
    'store.create(p, "review", "Fence", branch="agent/issue-1")',
    'store.comment_answered(None, None, db.execute("SELECT MAX(id) FROM events").fetchone()[0], "start")',
    'answer(1, store.comment(p, 1, "@agent paint it", to_agent=True), "run", 157)',
    'store.comment(p, 1, "Looks right.")',
    'answer(1, store.comment(p, 1, "@agent again", to_agent=True), "ignored")',
    'line = store.comment(p, 2, "Oil it.", path="gate.txt", line=1, head_sha="abc")',
    'store.comment(p, 2, "Paint it.", path="gate.txt", line=2, head_sha="abc")',
    'answer(2, store.comment(p, 2, "Please fix.", to_agent=True, quotes=[line]), "run", 158)',
    'answer(2, store.comment(p, 2, "Wrong branch.", to_agent=True), "note")'].join('\n')))
  const issue = storedComments('me/app', 1)
  assert.deepEqual([...storedAnswers('me/app', 1).runs], [[issue[0]?.id, 157]])
  assert.equal(storedAnswers('me/app', 1).waiting, false)
  const [asked] = storedComments('me/app', 2)
  assert.deepEqual([...storedAnswers('me/app', 2).runs], [[asked?.id, 158]])
  // The line comment the answered comment quoted names it, and the one it left out names none.
  assert.deepEqual(storedLineComments('me/app', 2).map(one => one.sentIn), [asked?.id, null])
  assert.equal(storedAnswers('me/app', 2).waiting, false)
  assert.deepEqual(storedAnswers('me/app', 9), { runs: new Map(), waiting: false })
  assert.equal(storeProblem(), null)
})

test('a comment sent to the agent waits for its answer only once the dispatcher answers comments', () => {
  const path = storeFixture(['p = "me/app"', 'store.create(p, "issue", "Paint the fence")',
    'store.comment(p, 1, "@agent paint it", to_agent=True)', 'store.comment(p, 1, "Not for the agent.")'].join('\n'))
  useStore(path)
  // A comment from before the dispatcher's first start never gets an answer.
  assert.equal(storedAnswers('me/app', 1).waiting, false)
  storeFixture(['p = "me/app"', ANSWER,
    'store.comment_answered(None, None, db.execute("SELECT MAX(id) FROM events").fetchone()[0], "start")',
    'store.comment(p, 1, "@agent now", to_agent=True)'].join('\n'), path)
  assert.equal(storedAnswers('me/app', 1).waiting, true)
  // Its answer ends the wait, and leaves the comment from before the first start unanswered.
  storeFixture(['p = "me/app"', ANSWER,
    'answer(1, db.execute("SELECT MAX(id) FROM comments").fetchone()[0], "run", 160)'].join('\n'), path)
  assert.equal(storedAnswers('me/app', 1).waiting, false)
  assert.deepEqual([...storedAnswers('me/app', 1).runs.values()], [160])
})
