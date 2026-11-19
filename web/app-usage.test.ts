// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { ui } from './app-state.js'
import { closeUsage, onUsageOutside, percentOf, readingLine, saveUsage, UsageLimit, UsagePopover, usageView, weekAt }
  from './app-usage.js'

// The posts the page sent, which the stubbed fetch answers with an empty 200.
const posted: { path: string; data: unknown }[] = []
Object.assign(globalThis, {
  document: { title: '', querySelector: () => null, getElementById: () => null },
  fetch: async (path: string, init: { body: string }) => {
    posted.push({ path, data: JSON.parse(init.body) })
    return { ok: true, status: 200, json: async () => ({}) }
  },
})

afterEach(() => {
  ui.server = null
  ui.usage = { open: false, draft: null, error: null }
  posted.length = 0
})

// Wednesday the 18th of November 2026 at 04:00, local time.
const wednesday = new Date(2026, 10, 18, 4, 0)
const reading = { percent: 41.9, readAt: new Date(2026, 10, 15, 14, 2).getTime() / 1000,
  resetsAt: wednesday.toISOString(), problem: null }
const under = { on: true, limit: 80, ...reading }
const over = { ...under, percent: 80 }

test('a time in the week reads its day and its local time on a 24-hour clock', () => {
  assert.equal(weekAt(wednesday), 'Wed 04:00')
  assert.equal(weekAt(wednesday.toISOString()), 'Wed 04:00')
  assert.equal(weekAt('soon'), null)
})

test('off, the button is a small Usage Limit control whose tooltip names the percentage it would hold at', () => {
  assert.deepEqual(usageView({ ...under, on: false }), { text: 'Usage Limit', tone: 'off',
    tip: 'The weekly usage limit is off; on, it holds new runs in every project at 80%' })
  assert.equal(usageView({ ...under, on: false, limit: null }).tip,
    'Set a weekly plan usage limit that holds new runs in every project')
})

test('under the limit, the button reads the week\'s usage rounded down and the limit, and its tooltip the reset', () => {
  assert.deepEqual(usageView(under), { text: 'Week 41% / 80%', tone: 'under',
    tip: 'Weekly plan usage is 41%, under the 80% limit; resets Wed 04:00' })
  // 79.9% reads 79%, so a reading under the limit never shows as at it.
  assert.equal(usageView({ ...under, percent: 79.9 }).text, 'Week 79% / 80%')
  assert.equal(usageView({ ...under, percent: 79.9 }).tone, 'under')
})

test('at or over the limit, the button takes the Hold tone, and its tooltip names the limit and the reset', () => {
  assert.deepEqual(usageView(over), { text: 'Week 80% / 80%', tone: 'over',
    tip: 'Weekly plan usage is 80%, at the 80% limit, so new agent, chore, and fix runs wait; resets Wed 04:00' })
  assert.equal(usageView({ ...over, percent: 83 }).text, 'Week 83% / 80%')
  assert.equal(usageView({ ...over, percent: 97, resetsAt: null }).tip,
    'Weekly plan usage is 97%, at the 80% limit, so new agent, chore, and fix runs wait')
})

test('while the reading doesn\'t count, the button says it can\'t read usage, and its tooltip why', () => {
  const failed = { ...over, problem: 'the usage endpoint answered HTTP 401' }
  assert.deepEqual(usageView(failed), { text: 'Week: can\'t read', tone: 'unread',
    tip: 'Can\'t read weekly plan usage, so new runs start: the usage endpoint answered HTTP 401. The limit is 80%.' })
  // Off, a reading that doesn't count matters to nothing.
  assert.equal(usageView({ ...failed, on: false }).tone, 'off')
})

test('the popover names the last reading, when it was read, and when the week resets', () => {
  assert.equal(readingLine(under), 'Last read 41% at Sun 14:02; the week resets Wed 04:00.')
  assert.equal(readingLine({ ...under, percent: null, readAt: null }), 'No reading yet.')
  // At or over the limit, it says the runs wait.
  const held = /New agent, chore, and fix runs wait until usage falls under the limit\./
  assert.match(JSON.stringify(UsagePopover({ usage: over })), held)
  assert.doesNotMatch(JSON.stringify(UsagePopover({ usage: under })), held)
})

test('a percentage is a whole number from 0 to 100', () => {
  assert.deepEqual(['0', '80', ' 100 ', '7'].map(percentOf), [0, 80, 100, 7])
  for (const bad of ['', '101', '-1', '50.5', '1e2', 'eighty', '0x10', null]) {
    assert.equal(percentOf(bad), null, String(bad))
  }
})

test('a bad percentage is refused in the popover before any save', () => {
  ui.server = { usage: under }
  ui.usage = { open: true, draft: '120', error: null }
  saveUsage(true, '120')
  assert.equal(ui.usage.error, 'Enter a whole percentage from 0 to 100.')
  assert.deepEqual(posted, [])
  assert.equal(ui.server.usage.limit, 80, 'the header keeps the saved limit')
  const popover = JSON.stringify(UsagePopover({ usage: ui.server.usage }))
  assert.match(popover, /"aria-invalid":"true"/)
  assert.match(popover, /Enter a whole percentage from 0 to 100\./)
})

test('saving turns the limit on at a percentage or off, and the header shows it at once', async () => {
  ui.server = { usage: { ...under, on: false } }
  saveUsage(true, '75')
  assert.deepEqual([ui.server.usage.on, ui.server.usage.limit], [true, 75])
  assert.equal(usageView(ui.server.usage).text, 'Week 41% / 75%', 'the button reads the new limit')
  saveUsage(true, '75')
  saveUsage(false, '75')
  assert.deepEqual([ui.server.usage.on, ui.server.usage.limit], [false, 75], 'off keeps the percentage for on')
  await new Promise(resolve => setTimeout(resolve))
  assert.deepEqual(posted, [{ path: '/api/usage-limit', data: { on: true, percent: 75 } },
    { path: '/api/usage-limit', data: { on: false } }], 'a save that changes nothing posts nothing')
})

test('the percentage waits while the limit is off, and the switch shows whether it is on', () => {
  const field = (usage: unknown) => {
    const text = JSON.stringify(UsagePopover({ usage }))
    return { disabled: /"id":"usage-percent"[^}]*"disabled":true/.test(text),
      checked: /"id":"usage-on"[^}]*"checked":true/.test(text), role: /"role":"switch"/.test(text) }
  }
  assert.deepEqual(field({ ...under, on: false }), { disabled: true, checked: false, role: true })
  assert.deepEqual(field(under), { disabled: false, checked: true, role: true })
  // With no limit set yet, the percentage offers 80.
  assert.match(JSON.stringify(UsagePopover({ usage: { ...under, on: false, limit: null } })), /"value":"80"/)
})

test('the button opens the popover as a dialog, and a press outside closes it and saves a valid percentage typed', () => {
  ui.server = { usage: under }
  const button = () => JSON.stringify(UsageLimit())
  assert.match(button(), /"aria-expanded":"false"/)
  ui.usage = { open: true, draft: null, error: null }
  assert.match(button(), /"aria-expanded":"true","aria-controls":"usage-limit"/)
  const [, popover] = (UsageLimit() as { props: { children: { type: unknown }[] } }).props.children
  assert.equal(popover.type, UsagePopover)
  assert.match(JSON.stringify(UsagePopover({ usage: under })),
    /"id":"usage-limit","class":"usage-pop","role":"dialog","aria-label":"Weekly usage limit"/)
  assert.doesNotMatch(button(), /"data-tip"/, 'the tooltip hides while the popover shows')
  ui.usage.draft = '60'
  onUsageOutside({ target: { closest: () => null } })
  assert.equal(ui.usage.open, false)
  assert.deepEqual(posted, [{ path: '/api/usage-limit', data: { on: true, percent: 60 } }])
  // Escape closes it and drops what was typed.
  ui.usage = { open: true, draft: '50', error: null }
  closeUsage(true)
  assert.equal(ui.usage.open, false)
  assert.equal(posted.length, 1)
})
