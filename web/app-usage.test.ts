// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { ui } from './app-state.js'
import { amountOf, apiView, closeUsage, headerDollars, keyLine, onUsageOutside, percentOf, readingLine,
  saveBudget, saveUsage, sendKey, spendLine, UsageLimit, UsagePopover, weekAt } from './app-usage.js'

// The posts the page sent, which the stubbed fetch answers with `answer`, an empty 200 unless a test sets one.
const posted: { path: string; data: unknown }[] = []
let answer: unknown = {}
Object.assign(globalThis, {
  document: { title: '', querySelector: () => null, getElementById: () => null },
  fetch: async (path: string, init: { body: string }) => {
    posted.push({ path, data: JSON.parse(init.body) })
    return { ok: true, status: 200, json: async () => answer }
  },
})

const CLOSED = { open: false, draft: null, error: null, budgetDraft: null, budgetError: null, confirm: null,
  keyOpen: false, keyError: null }

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
  ui.issueRunnerSaving = false
  ui.usage = { ...CLOSED }
  posted.length = 0
  answer = {}
})

// A drawn element, with its function components drawn in place.
type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]
type Element = { type?: unknown; props: Record<string, unknown> }
function draw(node: Drawn): Drawn {
  if (Array.isArray(node)) {
    return node.map(draw)
  }
  if (node === null || typeof node !== 'object') {
    return node
  }
  if (typeof node.type === 'function') {
    return draw((node.type as (props: unknown) => Drawn)(node.props))
  }
  return { ...node, props: { ...node.props, children: draw(node.props?.children as Drawn) } }
}
function textOf(node: Drawn): string {
  if (Array.isArray(node)) {
    return node.map(textOf).join(' ')
  }
  if (node === null || node === undefined || typeof node === 'boolean') {
    return ''
  }
  return typeof node === 'object' ? textOf(node.props?.children as Drawn) : String(node)
}
const squash = (text: string) => text.replace(/\s+/g, ' ').trim()
/** The drawn elements under `node` that `match` picks. */
function all(node: Drawn, match: (one: Element) => boolean): Element[] {
  if (Array.isArray(node)) {
    return node.flatMap(one => all(one, match))
  }
  if (node === null || typeof node !== 'object') {
    return []
  }
  const one = node as Element
  return [...match(one) ? [one] : [], ...all(one.props?.children as Drawn, match)]
}
/** The drawn button whose text is `text`. */
function button(node: Drawn, text: string): Element | undefined {
  return all(node, one => one.type === 'button' && squash(textOf(one)) === text)[0]
}
/** The drawn popover's text and elements, for the server's state in ui.server. */
const popover = () => draw(UsagePopover({ usage: ui.server.usage, account: ui.server.account ?? null }))

// Wednesday the 18th of November 2026 at 04:00, local time.
const wednesday = new Date(2026, 10, 18, 4, 0)
const reading = { percent: 41.9, readAt: new Date(2026, 10, 15, 14, 2).getTime() / 1000,
  resetsAt: wednesday.toISOString(), problem: null }
const under = { on: true, limit: 80, ...reading }
const over = { ...under, percent: 80 }
const subscription = { account: 'subscription', spent: 63.2, budget: { on: true, dollars: 200 }, held: false,
  key: { set: false, ending: null }, readAt: reading.readAt }
const keyed = { ...subscription, key: { set: true, ending: '4f2a' } }
const api = { ...keyed, account: 'api' }

test('a time in the week reads its day and its local time on a 24-hour clock', () => {
  assert.equal(weekAt(wednesday), 'Wed 04:00')
  assert.equal(weekAt(wednesday.toISOString()), 'Wed 04:00')
  assert.equal(weekAt('soon'), null)
})

test('the API reads its month\'s spend in whole dollars rounded down and the budget, and the Hold tone while it holds',
  () => {
    assert.deepEqual(apiView(api), { text: '$63 / $200', tone: 'under',
      tip: 'The API key\'s spend is $63.20 this month, under the $200.00 budget' })
    assert.deepEqual(apiView({ ...api, spent: 199.99 }).text, '$199 / $200')
    assert.deepEqual(apiView({ ...api, spent: 201.4, held: true }), { text: '$201 / $200', tone: 'over',
      tip: 'The API key\'s spend is $201.40 this month, at the $200.00 budget, so new agent, chore, and fix runs and '
        + 'the Chat\'s turns wait' })
    assert.deepEqual(apiView({ ...api, budget: { on: false, dollars: 200 } }), { text: '$63', tone: 'under',
      tip: 'The API key\'s spend is $63.20 this month; the API budget is off' })
    assert.deepEqual(apiView({ ...api, budget: null }).text, '$63')
    assert.deepEqual([headerDollars(49.5), headerDollars(1200), headerDollars(0.4, true)], ['$49.50', '$1,200', '$0'])
  })

const desk = (id: string, effective: string) => ({ id, title: id,
  issueRunner: { config: effective, override: null, effective } })
const claudeDesk = desk('claude-project', 'claude')
const header = () => all(draw(UsageLimit()), one => one.type === 'button')[0]
const headerText = () => squash(textOf(header()))

test('the header follows the selected project and shows each represented account once on All', () => {
  const claude = desk('first', 'claude')
  const codex = desk('second', 'codex')
  const local = desk('third', 'local')
  const shortReset = new Date(2026, 10, 15, 18, 10)
  const week = { usedPercent: 73, windowDurationMins: 10080, resetsAt: wednesday.getTime() / 1000 }
  const short = { usedPercent: 25, windowDurationMins: 300, resetsAt: shortReset.getTime() / 1000 }
  const usage = { ...under, fiveHour: { percent: 99, resetsAt: shortReset.toISOString() } }
  ui.server = { usage, account: subscription, codexUsage: { billing: 'subscription', windows: [week, short] },
    desks: [claude, codex, local, claudeDesk] }
  assert.equal(headerText(), 'Claude Weekly 58% left · 5-hour 1% left Codex Weekly 27% left · 5-hour 75% left Local Model provider')
  assert.equal(header().props.class, 'usage-button under', 'a high five-hour percentage does not hold Claude runs')
  assert.match(squash(textOf(popover())), /Claude Weekly 58% left, resets Wed 04:00\. 5-hour 1% left, resets Sun 18:10\./)
  assert.match(squash(textOf(popover())), /Codex Weekly 27% left, resets Wed 04:00\. 5-hour 75% left, resets Sun 18:10\./)
  ui.tab = claude.id
  assert.equal(headerText(), 'Claude Weekly 58% left · 5-hour 1% left')
  ui.server.usage = { ...usage, problem: 'the last reading is out of date' }
  assert.equal(headerText(), 'Claude 5-hour 1% left')
  ui.server.usage = under
  assert.equal(headerText(), 'Claude Weekly 58% left')
  ui.tab = codex.id
  assert.equal(headerText(), 'Codex Weekly 27% left · 5-hour 75% left')
  assert.doesNotMatch(squash(textOf(popover())), /Last read 41%/)
  ui.server.codexUsage.windows = [week]
  assert.equal(headerText(), 'Codex Weekly 27% left')
  assert.doesNotMatch(squash(textOf(popover())), /5-hour/)
  ui.server.codexUsage.windows = [{ ...short, windowDurationMins: 345 }]
  assert.equal(headerText(), 'Codex 5 hrs 45 min 75% left')
  ui.tab = local.id
  assert.equal(headerText(), 'Local Model provider')
  assert.doesNotMatch(squash(textOf(popover())), /58%|27%|41%/)
})

test('API billing and unavailable readings omit subscription percentages', () => {
  const codex = desk('codex-project', 'codex')
  ui.server = { usage: over, account: api, codexUsage: { billing: 'api' }, desks: [claudeDesk, codex] }
  assert.equal(headerText(), 'Claude API $63 / $200 Codex API key')
  assert.match(squash(textOf(popover())), /Codex Bills the API key from the Codex CLI sign-in/)
  assert.doesNotMatch(squash(textOf(popover())), /Last read 80%/)
  ui.server.account = subscription
  assert.equal(headerText(), 'Claude Weekly 20% left Codex API key')
  assert.equal(header().props.class, 'usage-button over')
  ui.server.usage = { ...over, on: false, problem: 'the last reading is out of date' }
  ui.server.codexUsage = { billing: 'subscription', windows: [] }
  assert.equal(headerText(), 'Claude Quota unavailable Codex Quota unavailable')
  assert.match(squash(textOf(popover())), /Claude Subscription quota unavailable.*Codex Subscription quota unavailable/)
  assert.doesNotMatch(squash(textOf(popover())), /Last read 80%/)
})

test('the popover names the last reading, when it was read, and when the week resets', () => {
  assert.equal(readingLine(under), 'Last read 41% at Sun 14:02; the week resets Wed 04:00.')
  assert.equal(readingLine({ ...under, percent: null, readAt: null }), 'Weekly usage unavailable.')
  assert.equal(spendLine(subscription), 'Last read $63.20 at Sun 14:02.')
  assert.equal(spendLine({ ...subscription, readAt: undefined }), 'Last read $63.20.')
  // At or over the limit on the subscription, it says the runs wait; on the API key, the limit holds nothing.
  const held = /New agent, chore, and fix runs wait until usage falls under the limit\./
  ui.server = { usage: over, account: subscription }
  assert.match(textOf(popover()), held)
  ui.server = { usage: under, account: subscription }
  assert.doesNotMatch(textOf(popover()), held)
  ui.server = { usage: over, account: api }
  assert.doesNotMatch(textOf(popover()), held)
  assert.match(squash(textOf(popover())), /The desk bills the API key, so the limit holds no runs\./)
})

test('a percentage is a whole number from 0 to 100', () => {
  assert.deepEqual(['0', '80', ' 100 ', '7'].map(percentOf), [0, 80, 100, 7])
  for (const bad of ['', '101', '-1', '50.5', '1e2', 'eighty', '0x10', null]) {
    assert.equal(percentOf(bad), null, String(bad))
  }
})

test('a budget is a positive number of dollars with at most two decimals', () => {
  assert.deepEqual(['200', '49.50', ' $200 ', '0.5', '1'].map(amountOf), [200, 49.5, 200, 0.5, 1])
  for (const bad of ['', '0', '0.00', '-5', '49.505', '1e3', 'two hundred', '$', '200$', null]) {
    assert.equal(amountOf(bad), null, String(bad))
  }
})

test('a bad percentage is refused in the popover before any save', () => {
  ui.server = { usage: under, account: subscription }
  ui.usage = { ...CLOSED, open: true, draft: '120' }
  saveUsage(true, '120')
  assert.equal(ui.usage.error, 'Enter a whole percentage from 0 to 100.')
  assert.deepEqual(posted, [])
  assert.equal(ui.server.usage.limit, 80, 'the header keeps the saved limit')
  const [field] = all(popover(), one => one.props.id === 'usage-percent')
  assert.equal(field.props['aria-invalid'], 'true')
  assert.match(textOf(popover()), /Enter a whole percentage from 0 to 100\./)
})

test('a budget that isn\'t a positive number of dollars is refused in the popover before any save', () => {
  ui.server = { usage: under, account: subscription }
  for (const bad of ['0', '-20', 'lots', '12.345']) {
    ui.usage = { ...CLOSED, open: true, budgetDraft: bad }
    saveBudget(true, bad)
    assert.equal(ui.usage.budgetError, 'Enter a positive number of dollars, such as 200 or 49.50.', bad)
    const [field] = all(popover(), one => one.props.id === 'budget-dollars')
    assert.equal(field.props['aria-invalid'], 'true')
  }
  assert.deepEqual(posted, [])
  assert.deepEqual(ui.server.account.budget, { on: true, dollars: 200 }, 'the header keeps the saved budget')
})

test('saving turns the weekly limit on at a percentage or off', async () => {
  ui.server = { usage: { ...under, on: false } }
  saveUsage(true, '75')
  assert.deepEqual([ui.server.usage.on, ui.server.usage.limit], [true, 75])
  assert.equal(all(popover(), one => one.props.id === 'usage-percent')[0]?.props.value, '75')
  saveUsage(true, '75')
  saveUsage(false, '75')
  assert.deepEqual([ui.server.usage.on, ui.server.usage.limit], [false, 75], 'off keeps the percentage for on')
  await new Promise(resolve => setTimeout(resolve))
  assert.deepEqual(posted, [{ path: '/api/usage-limit', data: { on: true, percent: 75 } },
    { path: '/api/usage-limit', data: { on: false } }], 'a save that changes nothing posts nothing')
})

test('saving turns the budget on at its dollars or off, and the header shows it at once', async () => {
  ui.server = { usage: under, account: { ...api, budget: null } }
  answer = null
  saveBudget(true, '$49.50')
  assert.deepEqual(ui.server.account.budget, { on: true, dollars: 49.5 })
  assert.equal(apiView(ui.server.account).text, '$63 / $49.50')
  saveBudget(true, '49.5')
  saveBudget(false, '49.5')
  assert.deepEqual(ui.server.account.budget, { on: false, dollars: 49.5 }, 'off keeps the amount for on')
  await new Promise(resolve => setTimeout(resolve))
  assert.deepEqual(posted, [{ path: '/api/api-budget', data: { on: true, dollars: 49.5 } },
    { path: '/api/api-budget', data: { on: false } }], 'a save that changes nothing posts nothing')
})

test('the fields wait while their switch is off, and each switch shows whether it is on', () => {
  const state = (usage: unknown, account: unknown) => {
    ui.server = { usage, account }
    const drawn = popover()
    const one = (id: string) => all(drawn, element => element.props.id === id)[0]?.props ?? {}
    return [one('usage-on').checked, one('usage-percent').disabled, one('budget-on').checked,
      one('budget-dollars').disabled, one('usage-on').role]
  }
  assert.deepEqual(state({ ...under, on: false }, { ...api, budget: { on: false, dollars: 200 } }),
    [false, true, false, true, 'switch'])
  assert.deepEqual(state(under, api), [true, false, true, false, 'switch'])
  // With no limit or budget set yet, the fields offer 80% and $200.
  ui.server = { usage: { ...under, on: false, limit: null }, account: { ...api, budget: null } }
  const values = all(popover(), one => one.type === 'input').map(one => [one.props.id, one.props.value])
  assert.deepEqual(values.filter(([id]) => id === 'usage-percent' || id === 'budget-dollars'),
    [['usage-percent', '80'], ['budget-dollars', '200']])
})

test('choosing the other account asks to confirm, and the confirm switches it through the server', async () => {
  ui.server = { usage: under, account: keyed, desks: [claudeDesk] }
  ui.usage = { ...CLOSED, open: true }
  const choice = (label: string) => all(popover(), one => one.type === 'button'
    && one.props['aria-pressed'] !== undefined && squash(textOf(one)) === label)[0]
  assert.deepEqual([choice('Subscription')?.props['aria-pressed'], choice('API')?.props['aria-pressed']],
    ['true', 'false'])
  ;(choice('Subscription')?.props.onClick as () => void)()
  assert.equal(ui.usage.confirm, null, 'the account in use asks nothing')
  ;(choice('API')?.props.onClick as () => void)()
  assert.equal(ui.usage.confirm, 'api')
  assert.match(squash(textOf(popover())), /Bill the API key from the next run on\?/)
  assert.deepEqual(posted, [], 'nothing switches before the confirm')
  // Cancel drops the confirm.
  ;(button(popover(), 'Cancel')?.props.onClick as () => void)()
  assert.equal(ui.usage.confirm, null)
  ;(choice('API')?.props.onClick as () => void)()
  answer = api
  ;(button(popover(), 'Switch to API')?.props.onClick as () => void)()
  await new Promise(resolve => setTimeout(resolve))
  assert.deepEqual(posted, [{ path: '/api/account', data: { account: 'api' } }])
  assert.equal(ui.server.account.account, 'api', 'the header shows the server\'s answer')
  assert.equal(headerText(), 'Claude API $63 / $200')
})

test('API waits for a key, with a line saying to set one first', () => {
  ui.server = { usage: under, account: subscription }
  const drawn = popover()
  const choice = button(drawn, 'API')
  assert.equal(choice?.props.disabled, true)
  assert.equal(choice?.props['aria-describedby'], 'account-no-key')
  assert.match(squash(textOf(drawn)), /Set a key first to bill the API\./)
  ui.server = { usage: under, account: keyed }
  assert.equal(button(popover(), 'API')?.props.disabled, false)
  assert.doesNotMatch(squash(textOf(popover())), /Set a key first/)
})

test('the key\'s row says whether a key is set, and Clear Key shows once one is and asks to confirm', async () => {
  assert.equal(keyLine(subscription), 'No key set')
  assert.equal(keyLine(keyed), 'Key ending in 4f2a')
  ui.server = { usage: under, account: subscription }
  assert.ok(button(popover(), 'Set Key'))
  assert.equal(button(popover(), 'Clear Key'), undefined)
  assert.match(squash(textOf(popover())), /No key set/)
  ui.server = { usage: under, account: keyed }
  assert.match(squash(textOf(popover())), /Key ending in 4f2a/)
  ;(button(popover(), 'Clear Key')?.props.onClick as () => void)()
  assert.equal(ui.usage.confirm, 'clear')
  assert.match(squash(textOf(popover())), /Remove the API key\?/)
  assert.deepEqual(posted, [])
  answer = subscription
  const [, confirm] = all(popover(), one => one.type === 'button' && squash(textOf(one)) === 'Clear Key')
  ;(confirm?.props.onClick as () => void)()
  await new Promise(resolve => setTimeout(resolve))
  assert.deepEqual(posted, [{ path: '/api/account-key', data: { clear: true } }])
  assert.equal(ui.server.account.key.set, false)
})

test('Set Key opens a password field the browser doesn\'t autofill, and Save sends the key once and keeps none',
  async () => {
    ui.server = { usage: under, account: subscription }
    ui.usage = { ...CLOSED, open: true, keyOpen: true }
    const [field] = all(popover(), one => one.props.id === 'account-key')
    assert.deepEqual([field.type, field.props.type, field.props.autocomplete, field.props['data-1p-ignore'],
      field.props['data-lpignore'], field.props.value], ['input', 'password', 'off', 'true', 'true', undefined])
    assert.equal(all(popover(), one => one.type === 'form' && all(one, inner => inner.props.id === 'account-key')
      .length > 0).length, 0, 'no form holds the field, so the browser offers to save no password')
    const typed = { value: '  sk-ant-api03-made-up-for-tests-4f2a  ' }
    answer = keyed
    await sendKey(typed)
    assert.deepEqual(posted, [{ path: '/api/account-key', data: { key: 'sk-ant-api03-made-up-for-tests-4f2a' } }])
    assert.equal(typed.value, '', 'the field empties')
    assert.equal(ui.usage.keyOpen, false)
    assert.deepEqual(ui.server.account.key, { set: true, ending: '4f2a' })
    assert.doesNotMatch(JSON.stringify(ui), /made-up-for-tests/, 'the page\'s state holds no key')
    // An empty field sends nothing.
    ui.usage.keyOpen = true
    await sendKey({ value: '   ' })
    assert.equal(posted.length, 1)
    assert.equal(ui.usage.keyError, 'Paste an API key first.')
  })

test('the billing popover shows saved runner choices and sets the runner for the active tab', async () => {
  const game = { id: 'game', title: 'Game', issueRunner: { config: 'claude', override: null, effective: 'claude' } }
  const site = { id: 'site', title: 'Site', issueRunner: { config: 'codex', override: null, effective: 'codex' } }
  ui.server = { usage: under, account: api, desks: [game, site] }
  ui.usage = { ...CLOSED, open: true }
  const byId = (id: string) => all(popover(), one => one.props.id === id)[0]
  const choose = (value: string) => (byId('issue-runner')?.props.onChange as
    (event: { currentTarget: { value: string } }) => Promise<void> | undefined)({ currentTarget: { value } })

  const options = (value: string) =>
    all(byId('issue-runner'), one => one.type === 'option' && one.props.value === value)
  assert.deepEqual(all(popover(), one => one.type === 'select').map(one => one.props.id), ['issue-runner'])
  assert.equal(byId('issue-runner')?.props['aria-label'], 'Agent runner for all projects')
  assert.equal(byId('issue-runner')?.props.value, 'default', 'projects can follow different configured defaults')
  site.issueRunner = { ...site.issueRunner, override: 'codex' }
  assert.equal(byId('issue-runner')?.props.value, 'mixed')
  assert.equal(squash(textOf(options('mixed')[0])), 'Mixed settings')
  assert.equal(options('mixed')[0]?.props.disabled, true)
  const [local] = options('local')
  assert.equal(textOf(local), 'Local')
  assert.equal(local?.props.disabled, undefined)
  const saving = choose('default')
  assert.equal(byId('issue-runner')?.props['aria-disabled'], 'true')
  await choose('claude')
  await saving
  assert.equal(byId('issue-runner')?.props['aria-disabled'], undefined)
  await choose('codex')
  await choose('local')

  game.issueRunner = { ...game.issueRunner, override: 'local', effective: 'local' }
  site.issueRunner = { ...site.issueRunner, override: 'local', effective: 'local' }
  assert.equal(byId('issue-runner')?.props.value, 'local')
  assert.match(squash(textOf(popover())), /Local Uses the provider and model in \.agent-desk\.json\./)

  assert.equal(options('mixed').length, 0)
  ui.tab = 'site'
  site.issueRunner = { ...site.issueRunner, override: 'codex', effective: 'codex' }
  assert.equal(byId('issue-runner')?.props['aria-label'], 'Agent runner for Site')
  assert.match(squash(textOf(byId('issue-runner'))), /Use project default \(Codex\)/)
  assert.match(squash(textOf(popover())), /Codex Subscription quota unavailable\. Claude billing/)
  await choose('claude')
  assert.deepEqual(posted, [{ path: '/api/issue-runner', data: { all: true, runner: 'default' } },
    { path: '/api/issue-runner', data: { all: true, runner: 'codex' } },
    { path: '/api/issue-runner', data: { all: true, runner: 'local' } },
    { path: '/api/issue-runner', data: { desk: 'site', runner: 'claude' } }])
  assert.equal(ui.server.account.account, 'api')
})

test('the button opens the popover as a dialog, and a press outside closes it and saves the valid values typed', () => {
  ui.server = { usage: under, account: subscription }
  const shown = () => all(draw(UsageLimit()), one => one.type === 'button')[0]?.props ?? {}
  assert.deepEqual([shown()['aria-expanded'], shown()['data-tip']], ['false', undefined])
  ui.usage = { ...CLOSED, open: true }
  assert.deepEqual([shown()['aria-expanded'], shown()['aria-controls'], shown()['data-tip']],
    ['true', 'usage-limit', undefined], 'the button shows no tooltip')
  const [dialog] = all(popover(), one => one.props.id === 'usage-limit')
  assert.deepEqual([dialog.props.role, dialog.props['aria-label']], ['dialog', 'Agent runner and billing'])
  ui.usage = { ...CLOSED, open: true, draft: '60', budgetDraft: '150' }
  onUsageOutside({ target: { closest: () => null } })
  assert.equal(ui.usage.open, false)
  assert.deepEqual(posted, [{ path: '/api/usage-limit', data: { on: true, percent: 60 } },
    { path: '/api/api-budget', data: { on: true, dollars: 150 } }])
  // A value the popover refuses isn't saved.
  ui.usage = { ...CLOSED, open: true, budgetDraft: '0' }
  onUsageOutside({ target: { closest: () => null } })
  assert.equal(posted.length, 2)
  // Escape closes it and drops what was typed, and a confirm waiting.
  ui.usage = { ...CLOSED, open: true, draft: '50', budgetDraft: '90', confirm: 'api' }
  closeUsage(true)
  assert.deepEqual(ui.usage, CLOSED)
  assert.equal(posted.length, 2)
})

test('a field\'s change saves nothing once Escape has closed the popover, as the browser fires one when it loses focus',
  () => {
    ui.server = { usage: under, account: api }
    ui.usage = { ...CLOSED, open: true, draft: '50', budgetDraft: '90' }
    const drawn = popover()
    const change = (id: string) => (all(drawn, one => one.props.id === id)[0]?.props.onChange as
      (event: unknown) => void)
    closeUsage(true)
    change('usage-percent')({ currentTarget: { value: '50' } })
    change('budget-dollars')({ currentTarget: { value: '90' } })
    assert.deepEqual(posted, [])
    // While the popover is open, a change saves.
    ui.usage = { ...CLOSED, open: true }
    change('budget-dollars')({ currentTarget: { value: '90' } })
    assert.deepEqual(posted, [{ path: '/api/api-budget', data: { on: true, dollars: 90 } }])
  })
