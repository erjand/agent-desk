// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { helpSections, REVIEW, SECTIONS } from './app-help.js'
import { REVIEW_KEYS } from './app-review.js'
import { KEYS } from './app-state.js'

// The names SECTIONS gives the action keys, each card action's by itself and each review button's after REVIEW.
const named = SECTIONS.flatMap(([, rows]) => rows.filter(row => typeof row === 'string'))

test('every action key names its section, once, and every named action has a key', () => {
  const actions = [...Object.keys(KEYS), ...Object.keys(REVIEW_KEYS).map(action => `${REVIEW}${action}`)]
  for (const action of actions) {
    assert.equal(named.filter(one => one === action).length, 1, `${action} has one row in SECTIONS`)
  }
  assert.deepEqual(named.filter(one => !actions.includes(one)), [], 'every row in SECTIONS names a key')
})

test('no two rows in the dialog show the same key, and every row says what its key does', () => {
  const rows = helpSections().flatMap(([, rows]) => rows)
  const keys = rows.map(([key]) => key)
  assert.deepEqual(keys.filter((key, at) => keys.indexOf(key) !== at), [], 'keys shown twice')
  for (const [key, what] of rows) {
    assert.ok(typeof key === 'string' && key !== '', `a row shows a key: ${what}`)
    assert.ok(typeof what === 'string' && what !== '', `${key} says what it does`)
  }
})

test('the dialog shows its sections in order, each with its keys in order', () => {
  assert.deepEqual(helpSections().map(([title, rows]) => [title, rows.map(([key]) => key)]), [
    ['Move Around', ['/', 'j / ↓', 'k / ↑', '← / →', 'o / Enter', '1-9']],
    ['Issues', ['a', 's', 'z', 'e', 'g', 'n', 'b', 'u', 'q', 'c']],
    ['Start Order', ['[', ']', '{']],
    ['Runs', ['r', 't', 'd', 'x', 'w']],
    ['Reviews', ['m', 'f', 'p', 'l']],
    ['Panels', ['\\', 'h', '`', 'i', '?']],
    ['Confirm and Cancel', ['y', 'Esc', 'A-Z']],
  ])
})
