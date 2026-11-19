// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { mergedRows } from './history.ts'
import { storeFixture } from './store-fixture.ts'
import { mergedReviews, reviewCosts, useStore } from './store.ts'

const since = Date.parse('2026-10-30T00:00:00Z')
const until = Date.parse('2026-10-31T00:00:00Z')

/**
 * org/game's store: review 70 from agent/issue-41, 72 from a chore, and 74 from a hand-made branch merged in the span,
 * 60 merged the day before, and 76 still open. Each run records the review it opened or updated, and org/other's run
 * names a review 70 of its own.
 */
const STORE = `
for number, branch, at in ((70, "agent/issue-41", "2026-10-30T18:44:44Z"),
                           (72, "agent/chore-refactor-one-1", "2026-10-30T18:54:06Z"),
                           (74, "fix-typo", "2026-10-30T19:00:00Z"), (60, "agent/issue-30", "2026-10-29T10:00:00Z"),
                           (76, "agent/issue-50", None)):
    store.create("org/game", "review", f"Review {number}", number=number, branch=branch)
    if at:
        store.merge("org/game", number, "abc")
        db.execute("UPDATE items SET closed_at = ? WHERE project = 'org/game' AND number = ?", (at, number))
for project, kind, target, cost, review in (("org/game", "agent", "41", 5.5, 70), ("org/game", "fix", "70", 0.25, 70),
                                            ("org/game", "agent", "41", 3, None),
                                            ("org/game", "chore", "singleshot-refactor-one", 0.5, 72),
                                            ("org/game", "agent", "41", None, 70), ("org/other", "agent", "8", 9, 70)):
    run = store.request_run(project, kind, target)
    store.run_result(run, model="opus", effort="high", minutes=16.4, cost=cost, review=review)
    if kind == "fix":
        call = store.gpu_asked(run, "/src/game-issue-41", False, 1000, held_by="game-issue-7")
        store.gpu_got(call, 1540)
        store.gpu_released(call, 1900)
    if target == "41" and cost == 5.5:
        call = store.gpu_asked(run, "/src/game-issue-41", False, 3000, got=3000)
        store.gpu_released(call, 3060)
`

test('a merged review in the span stands under its agent issue, with what every recorded run behind it cost', () => {
  useStore(storeFixture(STORE))
  const rows = mergedRows(mergedReviews('org/game', since, until, 300), 'game', since, until, reviewCosts('org/game'))
  assert.deepEqual(rows.map(row => [row.number, row.pr, row.title, row.at, row.cost, row.runs]), [
    [74, 74, 'Review 74', '2026-10-30T19:00:00Z', undefined, 0],
    [72, 72, 'Review 72', '2026-10-30T18:54:06Z', 0.5, 1],
    [41, 70, 'Review 70', '2026-10-30T18:44:44Z', 5.75, 3],
  ])
  assert.deepEqual(mergedReviews('org/game', since, until, 1).map(review => review.number), [74])
  // Review 70's runs held the GPU 7 minutes and queued 9; the other reviews' runs never used it.
  assert.deepEqual(rows.map(row => [row.gpuHeld, row.gpuQueued, row.gpu]), [
    [undefined, undefined, undefined], [undefined, undefined, undefined],
    [7, 9, 'Held GPU for 7 mins, waited 9 mins for it']])
  assert.equal('gpuHeld' in rows[0], false)
})

test('a row links to the issue its agent branch worked, and a hand-made branch links only its review', () => {
  const review = (number: number, branch: string) => ({ number, title: 'T', mergedAt: '2026-10-30T18:00:00Z', branch })
  const rows = mergedRows([review(70, 'agent/issue-41'), review(74, 'fix-typo'), review(60, 'agent/issue-60')],
    'game', since, until, new Map())
  assert.deepEqual(rows.map(row => [row.number, row.url, row.issueUrl]), [
    [41, '#game:70', '#game:41'],
    [74, '#game:74', undefined],
    [60, '#game:60', undefined],
  ])
  assert.equal('issueUrl' in rows[1], false)
})

test('a store no command has made yet reads as no merges and no costs', () => {
  useStore('/nonexistent/desk.sqlite')
  assert.deepEqual(mergedReviews('org/game', since, until, 300), [])
  assert.deepEqual(reviewCosts('org/game'), new Map())
})
