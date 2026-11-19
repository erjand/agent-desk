import { expect, mock, test } from 'claude-code/testing'
import type { TestBody } from 'claude-code/testing'

import type { Row } from '../types'
import { EVENT_MS, QUIET_MS, fitRow, pageTop } from './register'
import type { Host } from './board.ts'
import { BOARD, PANE, seed } from './pane.fixtures.ts'
import { DESK, NO_DESK, deskFound, deskMissing } from './run.ts'

test('the pane draws every section of a stored board, on every surface', async ($, on) => {
  seed(on)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: /review {2}Model fresh food/ })).toBeDefined()
    // A row shows the number of what it opens, a PR's in teal.
    expect(await ui.find({ type: 'Text', text: /^#61 +review {2}Model fresh food/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^#58 +Model tents/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^#43 +agent-1 +Model drinks/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Working$/ })).toBeUndefined()
    // Times sit right-justified in the buttons' column.
    expect(await ui.find({ type: 'Text', text: /^12m $/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Oct 23 14:05 $/ })).toBeDefined()
    expect(await ui.find({ key: 'open-pr-61' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^- Needs Design \(1\)$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Windows builds/ })).toBeDefined()
    expect(await ui.find({ key: 'open-decide-19' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /done Fri Oct 23 07:33/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^untested-one +3d +never +now, once/ })).toBeDefined()
    expect(await ui.find({ key: 'merge-pr-61' })).toBeDefined()
    expect(await ui.find({ key: 'open-runner-agent-1' })).toBeDefined()
    expect(await ui.find({ key: 'open-runner-agent-2' })).toBeUndefined()
    expect(await ui.find({ key: 'resume-stopped-47' })).toBeDefined()
    expect(await ui.find({ key: 'ready-next-64' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /waiting on #43/ })).toBeDefined()
    // A parked issue shows in Blocked with why it waits, and has no Agent-ready button.
    expect(await ui.find({ type: 'Text', text: /^- Blocked \(1\)$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^#50 +Switch the GPS clock {2}\(on hold\)/ })).toBeDefined()
    expect(await ui.find({ key: 'ready-blocked-50' })).toBeUndefined()
    // An idea has its own section and no Agent-ready button.
    expect(await ui.find({ type: 'Text', text: /^- Ideas \(1\)$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^#70 +Study the sprite pipeline/ })).toBeDefined()
    expect(await ui.find({ key: 'ready-idea-70' })).toBeUndefined()
    // A backlog issue has its own section with Move to Open and no Agent-ready button, and an Open issue has Move to
    // Backlog beside Agent-ready.
    expect(await ui.find({ type: 'Text', text: /^- Backlog \(1\)$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^#72 +Rig the night lights/ })).toBeDefined()
    expect(await ui.find({ key: 'ready-backlog-72' })).toBeUndefined()
    expect(await ui.find({ key: 'to-open-backlog-72', text: 'Move to Open' })).toBeDefined()
    expect(await ui.find({ key: 'to-backlog-next-64', text: 'Move to Backlog' })).toBeDefined()
    await ui.unmount()
  }
})

test('the GitHub section lists each touchpoint last, a failing one in the warning tone and a healthy one dim',
  async ($, on) => {
  const github: Row[] = [
    { key: 'github-main', text: `${'main'.padEnd(15)}matches origin, last push Nov 10 11:58`, title: 'main',
      note: 'matches origin, last push Nov 10 11:58', notice: 'muted', actions: [] },
    { key: 'github-backup', text: `${'backup'.padEnd(15)}last commit Nov 10 11:50; the push to origin failed: down`,
      title: 'backup', note: 'last commit Nov 10 11:50; the push to origin failed: down', notice: 'warning',
      actions: [] },
  ]
  seed(on, { ...BOARD, github })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /^- GitHub$/ })).toBeDefined()
  const healthy = await ui.find({ type: 'Text', text: /^ *main +matches origin, last push Nov 10 11:58/ })
  expect(healthy?.props.dimColor).toBe(true)
  const failing = await ui.find({ type: 'Text', text: /^ *backup +last commit Nov 10 11:50; the push/ })
  expect([failing?.props.color, failing?.props.dimColor]).toEqual(['warning', false])
  await ui.unmount()
})

test('a board stored before the GitHub group existed draws no GitHub heading', async ($, on) => {
  seed(on)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /^- GitHub$/ })).toBeUndefined()
  await ui.unmount()
})

test('the header shows no board age', async ($, on) => {
  seed(on, BOARD)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'refresh', text: 'Refresh' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Updated|09:15/ })).toBeUndefined()
  await ui.unmount()
})

/**
 * Starts a session that shows the pane in /repo, with the store's event file holding `event()` and each other program
 * answered by `answer`, or with no output; returns the clock, which moves only when the test moves it, and the event
 * file's reads.
 */
async function startPane($: Parameters<TestBody>[0], on: Parameters<TestBody>[1], event: () => string,
  answer: (argv: string[]) => string | null = () => null) {
  mock.store(on, { 'inbox-sessions': ['session-1'] })
  mock.env(on, { HOME: '/home/me' })
  const clock = mock.clock(on, { now: 1_000_000 })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'session-1' }))
  on('command.register', () => ({ value: { command: 'agent-desk' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  const files: string[] = []
  on('fs.read', (_, e) => {
    files.push(e.path)
    return { value: `${event()}\n` }
  })
  on('process.run', (_, e) => {
    const line = e.argv.join(' ')
    const stdout = answer([...e.argv]) ?? (line.includes('--git-common-dir') ? '/repo/.git\n'
      : line.includes('--show-toplevel') ? '/repo\n' : line === 'cat /repo/.agent-desk.json' ? '{}' : '')
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await clock.settle()
  return { clock, files }
}

const isBoardRead = (argv: string[]) => argv.join(' ').includes('board --json')

// An empty store as `agent-desk board --json` prints it.
const EMPTY_STORE = JSON.stringify({ repo: 'me/trail', at: '09:15 +0000', machine: 'here', runs: [], merged: [],
  issues: [], reviews: [], queue: { queue: [], dropped: [] } })

test('the pane reads the board when the store\'s newest event id moves, and once a quiet minute', async ($, on) => {
  seed(on)
  let event = '7'
  let reads = 0
  const { clock, files } = await startPane($, on, () => event, argv => {
    reads += isBoardRead(argv) ? 1 : 0
    return isBoardRead(argv) ? EMPTY_STORE : null
  })

  // A session that shows the pane reads the board once at start, noting the id the store's file holds.
  expect(reads).toBe(1)
  expect(files[0]).toBe('/home/me/.local/state/agent-desk/desk.sqlite-event')

  // Each second it reads the file, and reads the board only once the id moved.
  await clock.advance(EVENT_MS * 3)
  expect(reads).toBe(1)
  expect(files.length).toBeGreaterThan(3)
  event = '8'
  await clock.advance(EVENT_MS)
  expect(reads).toBe(2)
  await clock.advance(EVENT_MS * 3)
  expect(reads).toBe(2)

  // With no event for a minute, it reads the board anyway, for the times and the files beside the store.
  await clock.advance(QUIET_MS)
  expect(reads).toBe(3)
})

test('a board read that fails is tried again on the next tick, without waiting for the quiet minute', async ($, on) => {
  seed(on)
  let event = '7'
  let reads = 0
  let busy = false
  const { clock } = await startPane($, on, () => event, argv => {
    reads += isBoardRead(argv) ? 1 : 0
    // A store too busy to read prints nothing.
    return isBoardRead(argv) && !busy ? EMPTY_STORE : null
  })
  expect(reads).toBe(1)

  // The id moves while the store is busy, so the read it starts fails and the id still counts as unseen.
  busy = true
  event = '8'
  await clock.advance(EVENT_MS)
  expect(reads).toBe(2)
  await clock.advance(EVENT_MS)
  expect(reads).toBe(3)

  // Once a read succeeds, the id counts as seen, and the pane waits for it to move again.
  busy = false
  await clock.advance(EVENT_MS)
  expect(reads).toBe(4)
  await clock.advance(EVENT_MS * 3)
  expect(reads).toBe(4)
})

test('the read after each button shows its row where the store puts it, Fix\'s PR in Queued from the comment the '
  + 'dispatcher hasn\'t answered', async ($, on) => {
  let event = 1
  let queued = false
  type Item = Record<string, unknown> & { number: number }
  const issue = (number: number, title: string, status: string, more: Record<string, unknown> = {}): Item =>
    ({ number, title, status, labels: [], blockedBy: [], createdAt: `2026-11-${10 + (number % 10)}T00:00:00Z`,
      ...(status === 'in-progress' ? { comments: [], landed: null } : {}), ...more })
  const review = (number: number, title: string, branch: string, more: Record<string, unknown> = {}): Item =>
    ({ number, title, branch, base: 'main', head: `h${number}`, draft: false, createdAt: '2026-11-02T00:00:00Z',
      mergeable: 'MERGEABLE', checks: [{ name: 'test', status: 'passed' }], ...more })
  // The store as `agent-desk board --json` prints it, which each command below changes the way the store does before
  // the command returns, and the merge queue's file.
  const issues = [issue(64, 'Tidy the sound board', 'open'), issue(19, 'Windows builds', 'needs-design'),
    issue(50, 'Switch the GPS clock', 'on-hold'),
    issue(44, 'Mark the PR ready', 'in-progress', { comments: [{ body: 'The agent finished on `agent/issue-44`.',
      createdAt: '2026-11-03T00:00:00Z' }] }),
    issue(59, 'Model trail mix', 'in-progress'), issue(70, 'Study the sprite pipeline', 'open', { labels: ['idea'] })]
  const reviews = [review(61, 'Model fresh food', 'agent/issue-60'),
    review(45, 'Mark the PR ready', 'agent/issue-44', { draft: true }),
    review(62, 'Model trail mix', 'agent/issue-59', { mergeable: 'CONFLICTING' })]
  const runs: Record<string, unknown>[] = []
  const asked: number[] = []
  const store = () => JSON.stringify({ repo: 'me/trail', at: '09:15 +0000', machine: 'here', runs, asked, merged: [],
    issues, reviews, queue: { queue: queued ? [{ pr: 61, note: 'queued' }] : [], dropped: [] } })
  const find = (items: Item[], number: string) => items.find(one => one.number === Number(number)) as Item
  const { clock } = await startPane($, on, () => String(event), argv => {
    if (isBoardRead(argv)) {
      return store()
    }
    if (argv.join(' ').includes('merges add 61')) {
      queued = true
    }
    const [noun, verb, number = '', ...flags] = argv.slice(4)
    const line = `${noun} ${verb} ${flags.join(' ')}`
    if (line === 'issue edit --add-label agent-ready') {
      find(issues, number).status = 'queued'
    } else if (line === 'issue edit --remove-label needs-design' || line === 'issue edit --remove-label on-hold') {
      find(issues, number).status = 'open'
    } else if (line === 'issue edit --remove-label idea') {
      find(issues, number).labels = []
    } else if (noun === 'issue' && verb === 'view') {
      return JSON.stringify({ status: find(issues, number).status, blockedBy: [] })
    } else if (noun === 'review' && verb === 'ready') {
      find(reviews, number).draft = false
    } else if (line === 'review comment --to-agent --body-file -') {
      // The store holds the comment, and the board lists its review as asked until the dispatcher answers it.
      asked.push(Number(number))
    }
    return null
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // Presses a button, and its Confirm when it asks for one, then lets a later read land as well.
  const press = async (key: string) => {
    await ui.press({ key })
    const confirm = `confirm-${key.replace(/^[a-z]+-/, '')}`
    if (await ui.find({ key: confirm })) {
      await ui.press({ key: confirm })
    }
    await clock.settle()
    event += 1
    await clock.advance(EVENT_MS)
  }
  expect(await ui.find({ key: 'ready-next-64' })).toBeDefined()
  expect(await ui.find({ key: 'merge-pr-61' })).toBeDefined()

  await press('ready-next-64')
  expect(await ui.find({ key: 'ready-next-64' })).toBeUndefined()
  expect(await ui.find({ key: 'open-queued-64' })).toBeDefined()

  await press('designed-decide-19')
  expect(await ui.find({ key: 'open-decide-19' })).toBeUndefined()
  expect(await ui.find({ key: 'ready-next-19' })).toBeDefined()

  // Move to Open asks to confirm, then the idea's row moves from Ideas to Open.
  await ui.press({ key: 'to-open-idea-70' })
  expect(await ui.find({ key: 'ready-next-70' })).toBeUndefined()
  await press('confirm-idea-70')
  expect(await ui.find({ key: 'to-open-idea-70' })).toBeUndefined()
  expect(await ui.find({ key: 'ready-next-70' })).toBeDefined()

  await press('unblock-blocked-50')
  expect(await ui.find({ key: 'open-blocked-50' })).toBeUndefined()
  expect(await ui.find({ key: 'ready-next-50' })).toBeDefined()

  await press('undraft-draft-45')
  expect(await ui.find({ key: 'open-draft-45' })).toBeUndefined()
  expect(await ui.find({ key: 'merge-pr-45' })).toBeDefined()

  await press('fix-pr-62')
  expect(await ui.find({ key: 'fix-pr-62' })).toBeUndefined()
  expect(await ui.find({ key: 'open-rerun-pr-62' })).toBeDefined()
  // The dispatcher answers the comment with a request for #59's run, and the PR stays in Queued.
  asked.splice(0)
  runs.push({ id: 7, kind: 'agent', target: '59', status: 'requested', unit: null, started_at: null })
  event += 1
  await clock.advance(EVENT_MS)
  expect(await ui.find({ key: 'open-rerun-pr-62' })).toBeDefined()

  // Merge writes the queue before its command returns, and the read after it shows the PR in Merging.
  await ui.press({ key: 'merge-pr-61' })
  await ui.press({ key: 'confirm-pr-61' })
  await clock.settle()
  expect(await ui.find({ key: 'open-merging-61' })).toBeDefined()

  // Merging holds no move of its own: once the queue lets the PR go, the next read puts it back in Needs You.
  queued = false
  event += 1
  await clock.advance(EVENT_MS)
  expect(await ui.find({ key: 'open-merging-61' })).toBeUndefined()
  expect(await ui.find({ key: 'merge-pr-61' })).toBeDefined()
  expect(await ui.find({ key: 'open-queued-64' })).toBeDefined()
  await ui.unmount()
})

test('without the agent-desk command, the pane says how to install it and draws no sections', async ($, on) => {
  const asked: string[][] = []
  const found = { run: async (argv: string[]) => { asked.push(argv); return '/home/me/.local/bin/agent-desk\n' } }
  expect(await deskFound(found as unknown as Host, {})).toBe(true)
  expect(asked).toEqual([['bash', '-c', `command -v "${DESK}"`]])
  expect(await deskFound({ run: async () => null } as unknown as Host, {})).toBe(false)

  seed(on, deskMissing('09:20'))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /isn't on PATH/ }))?.text).toBe(NO_DESK)
  expect(NO_DESK).toContain('https://github.com/erjand/agent-desk')
  expect(await ui.find({ type: 'Text', text: /Runners/ })).toBeUndefined()
  await ui.unmount()
})

test('pressing a heading collapses its section, and pressing it again expands it', async ($, on) => {
  seed(on)
  on('store.set', () => ({ value: undefined }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'ready-next-64' })).toBeDefined()
  await ui.press({ key: 'toggle-Open' })
  expect(await ui.find({ type: 'Text', text: /^\+ Open \(1\)$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-64' })).toBeUndefined()
  // The other sections stay as they were.
  expect(await ui.find({ key: 'merge-pr-61' })).toBeDefined()
  await ui.press({ key: 'toggle-Open' })
  expect(await ui.find({ key: 'ready-next-64' })).toBeDefined()
  await ui.unmount()
})

test('a long section shows a page of issues, and its arrows page through the rest', async ($, on) => {
  const open: Row[] = Array.from({ length: 8 }, (_, index) => ({ key: `next-${100 + index}`,
    text: `Issue ${100 + index}`, number: 100 + index, actions: ['open', 'ready'] }))
  seed(on, { ...BOARD, next: open })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /^1-5 of 8 {7}$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-104' })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-105' })).toBeUndefined()
  await ui.press({ key: 'down-Open' })
  expect(await ui.find({ type: 'Text', text: /^4-8 of 8 {7}$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-107' })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-102' })).toBeUndefined()
  await ui.press({ key: 'up-Open' })
  expect(await ui.find({ key: 'ready-next-100' })).toBeDefined()
  await ui.unmount()
})

test('a long section jumps to its last page and back to its first', async ($, on) => {
  const open: Row[] = Array.from({ length: 13 }, (_, index) => ({ key: `next-${100 + index}`,
    text: `Issue ${100 + index}`, number: 100 + index, actions: ['open', 'ready'] }))
  seed(on, { ...BOARD, next: open })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'last-Open' })
  expect(await ui.find({ type: 'Text', text: /^9-13 of 13 {5}$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-112' })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-107' })).toBeUndefined()
  await ui.press({ key: 'first-Open' })
  expect(await ui.find({ type: 'Text', text: /^1-5 of 13 {6}$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-100' })).toBeDefined()
  await ui.unmount()
})

test('Blocked pages its issues like Open', async ($, on) => {
  const blocked: Row[] = Array.from({ length: 7 }, (_, index) => ({ key: `blocked-${200 + index}`,
    text: `Issue ${200 + index}  (blocked by #1)`, number: 200 + index, url: `https://example.test/${200 + index}`,
    actions: ['open'] }))
  seed(on, { ...BOARD, blocked })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /^- Blocked \(7\)$/ })).toBeDefined()
  expect(await ui.find({ key: 'open-blocked-204' })).toBeDefined()
  expect(await ui.find({ key: 'open-blocked-205' })).toBeUndefined()
  await ui.press({ key: 'down-Blocked' })
  expect(await ui.find({ key: 'open-blocked-206' })).toBeDefined()
  expect(await ui.find({ key: 'open-blocked-201' })).toBeUndefined()
  await ui.unmount()
})

test('paging stays within the section', () => {
  expect(pageTop(0, 5, 20)).toBe(5)
  expect(pageTop(12, 5, 20)).toBe(15)
  expect(pageTop(2, -5, 20)).toBe(0)
  expect(pageTop(0, 5, 4)).toBe(0)
})

test('Report asks this session for a resource report', async ($, on) => {
  seed(on, BOARD, { load: { ramUsed: 44, ramTotal: 60, swapUsed: 28, swapTotal: 30, cpu: 34 } })
  const commands: string[] = []
  on('command.run', (_, e) => {
    commands.push(`/${e.command} ${e.args}`)
    return { text: '' }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'report' })
  expect(commands).toEqual(['/agent-desk:resource-report Report what is using this machine\'s memory and CPU, and '
    + 'what could be freed.'])
  await ui.unmount()
})

test('pressing a row opens its link, so linked rows have no Open button', async ($, on) => {
  seed(on)
  const commands: string[][] = []
  on('process.run', (_, e) => {
    commands.push([...e.argv])
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'log-failed-9', label: 'Log' })).toBeDefined()
  await ui.press({ key: 'open-failed-9' })
  await ui.press({ key: 'open-pr-61' })
  expect(commands).toEqual([['xdg-open', 'https://example.test/43'], ['xdg-open', 'https://example.test/61']])
  await ui.unmount()
})

// The web page's addresses for an issue and a review, as board.ts's linkOf writes them.
const PAGE_ISSUE: Row = { key: 'next-64', text: 'Tidy the sound board', number: 64,
  url: 'http://127.0.0.1:7480/#trail:64', actions: ['open', 'ready', 'close'] }
const PAGE_REVIEW: Row = { key: 'pr-61', text: 'review  Model fresh food', number: 61, pr: true, issue: 60,
  url: 'http://127.0.0.1:7480/#trail:61', actions: ['open', 'merge'] }
const PROBE = ['bash', '-c', 'exec 3<>"/dev/tcp/$1/$2"', '_', '127.0.0.1', '7480']

test('Open shows a row\'s item on the web page once its server answers at the page\'s port', async ($, on) => {
  seed(on, { ...BOARD, needs: [PAGE_REVIEW], next: [PAGE_ISSUE] })
  const commands: string[][] = []
  on('process.run', (_, e) => {
    commands.push([...e.argv])
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'open-next-64' })
  await ui.press({ key: 'open-pr-61' })
  expect(commands).toEqual([PROBE, ['xdg-open', 'http://127.0.0.1:7480/#trail:64'], PROBE,
    ['xdg-open', 'http://127.0.0.1:7480/#trail:61']])
  expect(await ui.find({ type: 'Text', text: /isn't running/ })).toBeUndefined()
  await ui.unmount()
})

test('Open without the web page says how to start it and how to read the item in a terminal', async ($, on) => {
  seed(on, { ...BOARD, needs: [PAGE_REVIEW], next: [PAGE_ISSUE] })
  const commands: string[][] = []
  on('process.run', (_, e) => {
    commands.push([...e.argv])
    const refused = e.argv[2] === PROBE[2]
    return { value: { exitCode: refused ? 1 : 0, stdout: '', stderr: refused ? 'Connection refused' : '',
      isStdoutTruncated: false, isStderrTruncated: false } }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'open-next-64' })
  expect(await ui.find({ type: 'Text',
    text: "The web page isn't running: run agent-desk web on, or agent-desk issue view 64" })).toBeDefined()
  await ui.press({ key: 'open-pr-61' })
  expect(await ui.find({ type: 'Text',
    text: "The web page isn't running: run agent-desk web on, or agent-desk review view 61" })).toBeDefined()
  expect(commands).toEqual([PROBE, PROBE])
  await ui.unmount()
})

test('Log copies the command that prints a failed run\'s journal', async ($, on) => {
  seed(on)
  const copied: string[] = []
  on('ui.copy', (_, e) => {
    copied.push(e.text)
    return { value: { isCopied: true } }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'log-failed-9' })
  expect(copied).toEqual(['journalctl --user -u agent-desk-agent-me-trail-43-9.service'])
  expect(await ui.find({ type: 'Text', text: /^Copied: journalctl --user -u agent-desk-agent-me-trail-43-9/ }))
    .toBeDefined()
  await ui.unmount()
})

test('a row cut to fit keeps its note in parentheses and cuts the title before it', () => {
  const blocked = 'Cut the atlas world\'s tunnel, mine, and cave entrances  (blocked by #197, #213)'
  expect(fitRow(blocked, 200)).toBe(blocked)
  expect(fitRow(blocked, 40)).toBe('Cut the atlas…  (blocked by #197, #213)')
  // Text with no note, or a note too long for the row, is cut at the end as before.
  expect(fitRow('Model drinks for the store, café, and homes', 20)).toBe('Model drinks for th…')
  expect(fitRow(blocked, 30)).toBe('Cut the atlas world\'s tunnel,…')
  // Only a note at the very end stays whole.
  expect(fitRow('Fix (the) trail markers on the ridge', 20)).toBe('Fix (the) trail mar…')
})

test('Refresh reads Refreshing… while a refresh runs', async ($, on) => {
  const held: Record<string, unknown> = { refreshing: true }
  seed(on, BOARD, held)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'refresh', text: 'Refreshing…' })).toBeDefined()
  await ui.unmount()

  held.refreshing = false
  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'refresh', text: 'Refresh' })).toBeDefined()
  await after.unmount()
})
