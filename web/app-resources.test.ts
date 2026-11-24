// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { audit, auditMessage, claimTip, closeResources, cpuOf, memoryFacts, openResources, Resources, RESOURCES_MS,
  sizeOf }
  from './app-resources.js'
import { select, ui } from './app-state.js'

// The reads the view asked for, and the bodies of the posts the page sent, which the stubbed fetch answers with
// `answer`.
const asked: string[] = []
const posted: unknown[] = []
let answer: { ok: boolean; status: number; data: unknown } = { ok: true, status: 200, data: {} }
Object.assign(globalThis, {
  document: { title: '' },
  fetch: async (path: string, init?: { body?: string }) => {
    asked.push(path)
    if (init?.body !== undefined) {
      posted.push(JSON.parse(init.body))
    }
    return { ok: answer.ok, status: answer.status, json: async () => answer.data }
  },
})

afterEach(() => {
  ui.server = null
  ui.resources = { open: false, data: null, error: null }
  ui.newIssue.open = false
  ui.side = null
  ui.chat = { items: [], session: null, running: false, draft: '', sending: false, confirmClear: false, stick: true,
    focus: false }
  asked.length = 0
  posted.length = 0
  answer = { ok: true, status: 200, data: {} }
})

// A drawn element, with its function components drawn in place, and its text with a space around each element's.
type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]
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
  return typeof node === 'object' ? ` ${textOf(node.props?.children as Drawn)} ` : String(node)
}
function elements(node: Drawn, type: string): { props: Record<string, unknown> }[] {
  if (Array.isArray(node)) {
    return node.flatMap(one => elements(one, type))
  }
  if (node === null || typeof node !== 'object') {
    return []
  }
  const own = node.type === type ? [node as { props: Record<string, unknown> }] : []
  return [...own, ...elements(node.props?.children as Drawn, type)]
}
const squash = (text: string) => text.replace(/\s+/g, ' ').trim()
const GB = 1024 ** 3

const reading = {
  at: '2026-11-16T14:00:00Z',
  memory: { total: 64 * GB, free: 12.5 * GB, reserve: 8 * GB, claimed: 9.6 * GB, swapTotal: 32 * GB,
    swapFree: 4 * GB },
  units: [
    { unit: 'agent-desk-agent-me-fence-12-7.service', description: 'agent-desk agent run 12 in me/fence',
      kind: 'agent', desk: 'fence', repo: 'me/fence', label: '#12', number: 12, pr: false, memory: 3.2 * GB,
      swap: 0.5 * GB, cpu: 754,
      claim: { claimed: 9.8 * GB, counted: 9.8 * GB, peak: null, settle: null, used: 0.2 * GB, held: 9.6 * GB } },
    { unit: 'agent-desk-fix-me-gone-40-9.service', description: 'agent-desk fix run 40 in me/gone', kind: 'fix',
      desk: null, repo: 'me/gone', label: 'PR #40', number: 40, pr: true, memory: 900 * 1024 ** 2, swap: 0,
      cpu: 45, claim: { claimed: 3 * GB, counted: 3 * GB, peak: null, settle: 12.5, used: 3.5 * GB, held: 0 } },
    { unit: 'agent-desk-dispatch.service', description: 'agent-desk dispatcher', kind: 'dispatch', desk: null,
      repo: null, label: '', number: null, pr: false, memory: 40 * 1024 ** 2, swap: null, cpu: 11_520, claim: null },
  ],
  waiting: [{ desk: 'fence', repo: 'me/fence', kind: 'fix', label: 'PR #30', number: 30, pr: true,
    reason: 'memory: 5.3 GB free of 3 GB + 8 GB reserve' }],
  apps: [{ name: 'firefox', processes: 3, memory: 3.5 * GB, swap: 0.6 * GB, cpu: 330 },
    { name: 'zed-editor', processes: 1, memory: 0.25 * GB, swap: 2 * GB, cpu: 4000 }],
  tmp: 5.1 * GB,
}

/** A page with one project, whose board lists issue 12 as a card. */
function withBoard() {
  ui.server = { desks: [{ id: 'fence', title: 'Fence', repo: 'me/fence', board: {},
    cards: [{ id: 'fence:12', desk: 'fence', stage: 'working', number: 12, title: 'Paint the fence' }] }] }
}

test('sizes read in GB with one decimal, or MB under one GB, and CPU time compactly', () => {
  assert.equal(sizeOf(12.54 * GB), '12.5 GB')
  assert.equal(sizeOf(900 * 1024 ** 2), '900 MB')
  assert.equal(sizeOf(null), '–')
  assert.equal(cpuOf(45.4), '45s')
  assert.equal(cpuOf(754), '12m 34s')
  assert.equal(cpuOf(11_520), '3h 12m')
  assert.equal(cpuOf(null), '–')
})

test('the memory figures say what the dispatcher keeps for you, what is free for runs above it, and what runs claimed',
  () => {
  assert.deepEqual(memoryFacts(reading.memory).map(([name, value, , of]) => [name, value, of]), [
    ['Total', '64.0 GB', null], ['Free', '12.5 GB', null], ['Kept for you', '8.0 GB', null],
    ['Free for runs', '4.5 GB', null], ['Claimed by runs', '9.6 GB', null], ['Free after claims', '2.9 GB', null],
    ['Swap free', '4.0 GB', 'of 32.0 GB']])
  assert.deepEqual(memoryFacts({ ...reading.memory, free: 6 * GB, reserve: null, claimed: null, swapTotal: 0 })
    .slice(2).map(([, value, , of]) => [value, of]), [['–', null], ['–', null], ['–', null], ['–', null],
    ['no swap', null]])
  // Claims over the memory free leave none, as the dispatcher's memory gate reads it.
  assert.equal(memoryFacts({ ...reading.memory, claimed: 20 * GB })[5]?.[1], '0 MB')
  // Each figure says what it counts.
  assert.ok(memoryFacts(reading.memory).every(([, , tip]) => typeof tip === 'string' && tip.length > 0))
  assert.match(String(memoryFacts(reading.memory)[5]?.[2]), /the free figure a waiting run's reason gives/)
})

test('a claim\'s tip says what it claimed at start, when it may shrink or how far it has, and what it holds back',
  () => {
  const claim = { claimed: 9.8 * GB, counted: 9.8 * GB, peak: null, settle: null, used: 0.2 * GB, held: 9.6 * GB }
  assert.equal(claimTip(claim), 'Claimed 9.8 GB at start. It has no settle time yet, so it counts in full while the '
    + 'run lasts. It holds back 9.6 GB beyond the 205 MB the run\'s programs hold.')
  assert.equal(claimTip({ ...claim, settle: 45 }), 'Claimed 9.8 GB at start. It may shrink after its settle time of '
    + '45 min, when nearly every past run of its kind had reached its peak. It holds back 9.6 GB beyond the 205 MB '
    + 'the run\'s programs hold.')
  assert.equal(claimTip({ ...claim, settle: 45, counted: 4.5 * GB, peak: 2.5 * GB, used: 2 * GB, held: 2.5 * GB }),
    'Claimed 9.8 GB at start. Past its settle time of 45 min, it counts 4.5 GB, from its peak so far of 2.5 GB. '
      + 'It holds back 2.5 GB beyond the 2.0 GB the run\'s programs hold.')
})

test('the view draws each group, with a run linked to its card when the board lists one', () => {
  withBoard()
  ui.resources = { open: true, data: reading, error: null }
  const drawn = draw(Resources())
  const text = squash(textOf(drawn))
  for (const heading of ['Resources', 'Memory', 'Agent-desk runs', 'Waiting for memory', 'Everything else',
    'Temp files']) {
    assert.ok(text.includes(heading), heading)
  }
  const rows = elements(drawn, 'tr').map(row => squash(textOf(row)))
  assert.deepEqual(rows, [
    'Kind Works on Memory Claimed Swap CPU',
    'agent Fence #12 Paint the fence 3.2 GB 9.8 GB 512 MB 12m 34s',
    'fix me/gone PR #40 900 MB 3.0 GB 0 MB 45s',
    'dispatch 40 MB – – 3h 12m',
    'Kind Works on Waits for',
    'fix Fence PR #30 memory: 5.3 GB free of 3 GB + 8 GB reserve',
    'App Memory Swap CPU',
    'firefox × 3 3.5 GB 614 MB 5m 30s',
    'zed-editor 256 MB 2.0 GB 1h 06m',
  ])
  // The Memory heading says it counts file cache, which claims leave out, and each claim says what it holds.
  const heads = elements(drawn, 'th')
  assert.match(String(heads.find(head => textOf(head as Drawn).trim() === 'Memory')?.props.title),
    /file cache the kernel can take back.*anonymous memory/)
  assert.ok(heads.find(head => textOf(head as Drawn).trim() === 'Claimed')?.props.title)
  const claimed = elements(drawn, 'td').map(cell => cell.props.title).filter(tip => typeof tip === 'string'
    && /claim/i.test(tip))
  assert.deepEqual(claimed, [claimTip(reading.units[0]!.claim!), claimTip(reading.units[1]!.claim!),
    'No memory claim'])
  // Only the run whose card the board lists links to it, by the page's link to the item.
  assert.deepEqual(elements(drawn, 'a').map(link => link.props.href), ['#fence:12'])
  assert.ok(text.includes('The 4 processes outside agent-desk using the most memory and swap, by app.'))
  assert.ok(text.includes('5.1 GB in /tmp'))
})

test('a view with nothing to list says so in each group', () => {
  ui.resources = { open: true, data: { ...reading, units: [], waiting: [], apps: [] }, error: null }
  const text = squash(textOf(draw(Resources())))
  assert.ok(text.includes('No agent-desk unit is running.'))
  assert.ok(text.includes('The dispatcher holds no run for memory.'))
  assert.ok(text.includes('No process outside agent-desk was read.'))
})

test('before its first reading the view says it reads, and a failed read says why over the last reading', () => {
  ui.resources = { open: true, data: null, error: null }
  assert.ok(squash(textOf(draw(Resources()))).includes('Reading the machine…'))
  ui.resources = { open: true, data: reading, error: 'the server answered 500' }
  const text = squash(textOf(draw(Resources())))
  assert.ok(text.includes("The machine couldn't be read: the server answered 500"))
  assert.ok(text.includes('Agent-desk runs'))
})

test('opening the view closes the New Issue form, reads the machine now and every 10 seconds, and stops once closed',
  async t => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  answer = { ok: true, status: 200, data: reading }
  ui.newIssue.open = true
  await openResources()
  assert.equal(ui.resources.open, true)
  assert.equal(ui.newIssue.open, false)
  assert.deepEqual(ui.resources.data, reading)
  t.mock.timers.tick(RESOURCES_MS)
  assert.equal(asked.length, 2)
  closeResources()
  t.mock.timers.tick(RESOURCES_MS)
  t.mock.timers.tick(RESOURCES_MS)
  assert.equal(asked.length, 2)
})

test('a failed read keeps the last reading and says why; selecting a card closes the view', async t => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  ui.resources = { open: false, data: reading, error: null }
  answer = { ok: false, status: 500, data: { error: 'server error' } }
  await openResources()
  assert.equal(ui.resources.error, 'server error')
  assert.deepEqual(ui.resources.data, reading)
  select('fence:12')
  assert.equal(ui.resources.open, false)
})

test('the audit message runs the report skill on the memory free, the reserve, the claims, and each run held for memory', () => {
  const message = auditMessage(reading)
  const [first, ...rest] = message.split('\n')
  assert.match(first ?? '', /^\/agent-desk:resource-report The Resources view read at .+: 12\.5 GB of 64\.0 GB memory /)
  assert.ok(first?.endsWith('memory free, 8.0 GB kept for me by the dispatcher, 4.5 GB free for runs above it, '
    + '9.6 GB claimed by running runs, 2.9 GB free after claims, 4.0 GB of 32.0 GB swap free.'), first)
  assert.deepEqual(rest,
    ['Waiting for memory:', '- fix PR #30 in me/fence: memory: 5.3 GB free of 3 GB + 8 GB reserve'])
  const quiet = auditMessage({ ...reading, waiting: [],
    memory: { ...reading.memory, reserve: null, claimed: null, swapTotal: 0 } })
  assert.ok(quiet.includes("memory free, the dispatcher's reserve unread, no swap.\n"), quiet)
  assert.ok(quiet.endsWith('The dispatcher holds no run for memory.'), quiet)
  assert.equal(auditMessage(null), '/agent-desk:resource-report')
})

test('Audit with Claude opens the Chat and sends the audit about no card', async () => {
  ui.resources = { open: true, data: reading, error: null }
  ui.side = 'runners'
  await audit()
  assert.equal(ui.side, 'chat')
  assert.deepEqual(posted, [{ text: auditMessage(reading), context: null }])
  assert.equal(asked.at(-1), '/api/chat')
  // A Chat already showing stays open.
  await audit()
  assert.equal(ui.side, 'chat')
})

test('Audit with Claude is disabled, and says why in its title, while the Chat replies', () => {
  ui.resources = { open: true, data: reading, error: null }
  const button = () => elements(draw(Resources()), 'button')
    .find(one => squash(textOf(one as Drawn)) === 'Audit with Claude')
  assert.equal(button()?.props.disabled, false)
  assert.match(String(button()?.props.title), /run \/agent-desk:resource-report/)
  for (const state of [{ running: true, sending: false }, { running: false, sending: true }]) {
    Object.assign(ui.chat, state)
    assert.equal(button()?.props.disabled, true)
    assert.equal(button()?.props.title, 'Claude is still replying in the Chat; wait for the reply to end')
  }
})
