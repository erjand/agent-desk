import { expect, test } from 'claude-code/testing'

import type { Row } from '../types'
import { CONFIRMS, INSTANT, isMain, LABELS, labelOf } from './register'
import { withListMoves } from './moves.ts'
import { BOARD, PANE, seed } from './pane.fixtures.ts'
import { DESK, DISPATCH, HOLD } from './run.ts'

// The tests that press a row's button and check the command it runs and where its row moves.

test('a PR with merge conflicts shows Merge, and Fix, which asks its agent to merge main', async ($, on) => {
  seed(on)
  const commands: string[][] = []
  const inputs: (string | undefined)[] = []
  on('process.run', (_, e) => {
    commands.push([...e.argv])
    inputs.push(e.init?.stdin)
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 1_000 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /^#62 +conflicts {2}Model trail mix/ })).toBeDefined()
  expect(await ui.find({ key: 'merge-pr-62' })).toBeDefined()
  await ui.press({ key: 'fix-pr-62' })
  expect(commands).toEqual([])
  expect(await ui.find({ key: 'confirm-pr-62', text: 'Confirm fix' })).toBeDefined()
  await ui.press({ key: 'confirm-pr-62' })
  // The comment goes to the agent through the store, which asks the dispatcher for the run.
  const at = commands.findIndex(argv => argv[4] === 'review' && argv[5] === 'comment')
  expect(commands[at]?.slice(4)).toEqual(['review', 'comment', '62', '--to-agent', '--body-file', '-'])
  expect(inputs[at]).toMatch(/^This PR has merge conflicts/)
  await ui.unmount()
  // The PR moves to Queued at once, with no button to ask again.
  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ type: 'Text', text: /^#62 +Model trail mix {2}\(fixing conflicts\)/ })).toBeDefined()
  expect(await after.find({ key: 'fix-pr-62' })).toBeUndefined()
  expect(await after.find({ key: 'open-pr-62' })).toBeUndefined()
  await after.unmount()
})

test('pressing Merge asks to confirm before anything merges', async ($, on) => {
  seed(on)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'merge-pr-61' })
  expect(await ui.find({ key: 'confirm-pr-61' })).toBeDefined()
  expect(await ui.find({ key: 'merge-pr-61' })).toBeUndefined()
  await ui.press({ key: 'cancel-confirm-pr-61' })
  expect(await ui.find({ key: 'merge-pr-61' })).toBeDefined()
  await ui.unmount()
})

test('pressing Dismiss asks to confirm, and Cancel keeps the run', async ($, on) => {
  seed(on)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'dismiss-failed-9' })
  expect(await ui.find({ key: 'confirm-failed-9', text: 'Confirm dismiss' })).toBeDefined()
  expect(await ui.find({ key: 'dismiss-failed-9' })).toBeUndefined()
  await ui.press({ key: 'cancel-confirm-failed-9' })
  expect(await ui.find({ key: 'dismiss-failed-9' })).toBeDefined()
  await ui.unmount()
})

test('Agent-ready asks to confirm, then moves the issue to Queued before any refresh, and back when labelling fails',
  async ($, on) => {
  const reset = seed(on)
  let exitCode = 0
  const labelled: string[][] = []
  on('process.run', (_, e) => {
    labelled.push([...e.argv])
    return { value: { exitCode, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'ready-next-64' })
  expect(labelled).toEqual([])
  expect(await ui.find({ key: 'confirm-next-64', text: 'Confirm agent-ready' })).toBeDefined()
  await ui.press({ key: 'confirm-next-64' })
  // Agent-ready labels the issue and asks for its run without picking it, so in Manual the run waits for Start.
  expect(labelled).toEqual([
    ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'issue', 'edit', '64', '--add-label', 'agent-ready'],
    ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'request', 'agent', '64', '--env', 'EVENT=issues']])
  await ui.unmount()

  // The stand-in above bypasses the kit's state, which is what redraws a reader, so a fresh mount reads the board.
  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'ready-next-64' })).toBeUndefined()
  expect(await after.find({ key: 'open-queued-64' })).toBeDefined()
  await after.unmount()

  exitCode = 1
  reset()
  const failing = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await failing.press({ key: 'ready-next-64' })
  await failing.press({ key: 'confirm-next-64' })
  await failing.unmount()
  const back = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await back.find({ key: 'ready-next-64' })).toBeDefined()
  expect(await back.find({ key: 'open-queued-64' })).toBeUndefined()
  await back.unmount()
})

test('Retry asks to confirm, then asks the dispatcher for the failed run again and takes it out of Needs You, and '
  + 'puts it back when it refuses', async ($, on) => {
  const reset = seed(on)
  let exitCode = 0
  const asked: string[][] = []
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    return { value: { exitCode, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'retry-failed-9' })
  expect(asked.some(argv => argv[2]?.includes(' again '))).toBe(false)
  expect(await ui.find({ key: 'confirm-failed-9', text: 'Confirm retry' })).toBeDefined()
  await ui.press({ key: 'confirm-failed-9' })
  expect(asked).toContainEqual(['bash', '-c', `exec python3 -B ${DISPATCH} again "$1"`, '_', '9'])
  await ui.unmount()

  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'retry-failed-9' })).toBeUndefined()
  await after.unmount()

  exitCode = 1
  reset()
  const failing = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await failing.press({ key: 'retry-failed-9' })
  await failing.press({ key: 'confirm-failed-9' })
  await failing.unmount()
  const back = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await back.find({ key: 'retry-failed-9' })).toBeDefined()
  await back.unmount()
})

test('the pane leaves Close to the web page, and keeps an issue\'s other buttons', async ($, on) => {
  seed(on)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'ready-next-64' })).toBeDefined()
  expect(await ui.find({ key: 'close-next-64' })).toBeUndefined()
  expect(await ui.find({ key: 'close-idea-70' })).toBeUndefined()
  await ui.unmount()
})

test('Confirm merge queues the PR in Merging before any refresh, and puts it back when queueing fails',
  async ($, on) => {
  const reset = seed(on)
  let exitCode = 0
  const commands: string[][] = []
  on('process.run', (_, e) => {
    commands.push([...e.argv])
    return { value: { exitCode, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'merge-pr-61' })
  await ui.press({ key: 'confirm-pr-61' })
  expect(commands).toContainEqual(['bash', '-c', `exec "${DESK}" merges add 61`])
  expect(commands.some(one => one[0] === 'gh' && one[2] === 'merge')).toBe(false)
  await ui.unmount()

  // The stand-in above bypasses the kit's state, which is what redraws a reader, so a fresh mount reads the board.
  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'merge-pr-61' })).toBeUndefined()
  expect(await after.find({ key: 'open-merging-61' })).toBeDefined()
  await after.unmount()

  exitCode = 1
  reset()
  const failing = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await failing.press({ key: 'merge-pr-61' })
  await failing.press({ key: 'confirm-pr-61' })
  await failing.unmount()
  const back = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await back.find({ key: 'merge-pr-61' })).toBeDefined()
  expect(await back.find({ key: 'open-merging-61' })).toBeUndefined()
  await back.unmount()
})

test('Run starts a chore and shows it starting before any refresh', async ($, on) => {
  seed(on)
  const commands: string[][] = []
  on('process.run', (_, e) => {
    commands.push([...e.argv])
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.sleep', () => ({ value: undefined }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'run-chore-audit' })).toBeDefined()
  await ui.press({ key: 'run-chore-untested-one' })
  // The test session reads no .agent-desk.json, so the chore runs under the name the table shows.
  expect(commands).toEqual([['bash', '-c', `exec "${DESK}" request chore "$1"`, '_', 'untested-one']])
  expect(await ui.find({ type: 'Text', text: /Started untested-one/ })).toBeDefined()
  await ui.unmount()

  // The stand-in above bypasses the kit's state, which is what redraws a reader, so a fresh mount reads the board.
  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ type: 'Text', text: /starting now/ })).toBeDefined()
  expect(await after.find({ key: 'run-chore-untested-one' })).toBeUndefined()
  expect(await after.find({ key: 'run-chore-audit' })).toBeDefined()
  await after.unmount()
})

test('Dismiss hides a failed run and keeps its id for later sessions', async ($, on) => {
  seed(on)
  // Nothing beneath the plugin keeps $.store in a test, so these hooks stand for it.
  const store = new Map<string, unknown>()
  on('store.get', (_, e) => ({ value: store.get(e.key) }))
  on('store.set', (_, e) => {
    store.set(e.key, e.value)
    return { value: undefined }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'dismiss-failed-9' })).toBeDefined()
  await ui.press({ key: 'dismiss-failed-9' })
  expect(store.get('dismissed-runs')).toBeUndefined()
  await ui.press({ key: 'confirm-failed-9' })
  expect(store.get('dismissed-runs')).toEqual([-9])
  await ui.unmount()

  // The stand-in above bypasses the kit's state, which is what redraws a reader, so a fresh mount reads the board.
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await again.find({ key: 'dismiss-failed-9' })).toBeUndefined()
  expect(await again.find({ key: 'merge-pr-61' })).toBeDefined()
  await again.unmount()
})

test('Continue asks to confirm, then moves the stopped issue to Queued before any refresh', async ($, on) => {
  const stopped: Row = { key: 'stopped-1297', text: 'stopped  Bake the lightmaps', number: 1297, title:
    'Bake the lightmaps', url: 'https://example.test/1297', state: 'stopped', rerun: -32,
    actions: ['open', 'restart', 'close'] }
  seed(on, { ...BOARD, needs: [...BOARD.needs, stopped] })
  const asked: string[][] = []
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // A stopped issue with a session to take over shows Take Over, and every stopped issue shows Continue.
  expect(await ui.find({ key: 'resume-stopped-47', text: 'Take Over' })).toBeDefined()
  expect(await ui.find({ key: 'restart-stopped-1297', text: 'Continue' })).toBeDefined()
  await ui.press({ key: 'restart-stopped-1297' })
  expect(await ui.find({ key: 'confirm-stopped-1297', text: 'Confirm continue' })).toBeDefined()
  expect(asked.some(argv => argv[2]?.includes(' again '))).toBe(false)
  await ui.press({ key: 'confirm-stopped-1297' })
  expect(asked).toContainEqual(['bash', '-c', `exec python3 -B ${DISPATCH} again "$1"`, '_', '32'])
  await ui.unmount()

  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'restart-stopped-1297' })).toBeUndefined()
  expect(await after.find({ type: 'Text', text: /Bake the lightmaps {2}\(continuing\)/ })).toBeDefined()
  await after.unmount()
})

test('Start asks to confirm, then picks a fresh start that Retry or Restart left in Queued, and a picked one starts on '
  + 'Resume', async ($, on) => {
  const waits: Row = { key: 'rerun-1297', text: 'Bake the lightmaps  (waits for Start)', number: 1297,
    title: 'Bake the lightmaps', note: 'waits for Start', request: 33, url: 'https://example.test/1297', pick: 1297,
    actions: ['open', 'start', 'cancel'] }
  const picked: Row = { key: 'queued-1298', text: 'Light the bakery  (starts on Resume)', number: 1298,
    title: 'Light the bakery', note: 'starts on Resume', url: 'https://example.test/1298', actions: ['open'] }
  // A row back to its PR and a chore's row wait for Start by their requests.
  const back: Row = { key: 'rerun-1299', text: 'Fix the oven  (back to its PR; waits for Start)', number: 1300,
    issue: 1299, pr: true, title: 'Fix the oven', note: 'back to its PR; waits for Start', request: 35,
    starts: [34, 35], url: 'https://example.test/1300', actions: ['open', 'start', 'cancel'] }
  const chore: Row = { key: 'request-36', text: 'chore: audit  (waits for Start)', title: 'chore: audit',
    note: 'waits for Start', request: 36, starts: [36], actions: ['start', 'cancel'] }
  seed(on, { ...BOARD, queued: [waits, picked, back, chore] })
  const asked: string[][] = []
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /Light the bakery {2}\(starts on Resume\)/ })).toBeDefined()
  expect(await ui.find({ key: 'start-queued-1298' })).toBeUndefined()
  await ui.press({ key: 'start-rerun-1297' })
  expect(asked.some(argv => argv[2]?.includes(HOLD))).toBe(false)
  // The confirm's Cancel and the row's Cancel run show side by side under keys of their own.
  expect(await ui.find({ key: 'confirm-rerun-1297', text: 'Confirm start' })).toBeDefined()
  expect(await ui.find({ key: 'cancel-confirm-rerun-1297', text: 'Cancel' })).toBeDefined()
  expect(await ui.find({ key: 'cancel-rerun-1297', text: 'Cancel run' })).toBeDefined()
  await ui.press({ key: 'confirm-rerun-1297' })
  expect(asked).toContainEqual(['bash', '-c', `exec python3 -B ${HOLD} start "$1"`, '_', '1297'])
  expect(asked.some(argv => argv[2]?.includes('start-runs'))).toBe(false)
  await ui.press({ key: 'start-rerun-1299' })
  await ui.press({ key: 'confirm-rerun-1299' })
  expect(asked).toContainEqual(['bash', '-c', `exec python3 -B ${HOLD} start-runs "$@"`, '_', '34', '35'])
  expect(asked.some(argv => argv[2]?.endsWith('start "$1"') && argv[4] === '1299')).toBe(false)
  await ui.press({ key: 'start-request-36' })
  await ui.press({ key: 'confirm-request-36' })
  expect(asked).toContainEqual(['bash', '-c', `exec python3 -B ${HOLD} start-runs "$@"`, '_', '36'])
  await ui.unmount()
})

test('Unblock asks to confirm, then frees a Blocked issue and moves it to Open, or back when the store refuses',
  async ($, on) => {
  const parked: Row = { key: 'blocked-50', text: 'Switch the GPS clock  (blocked by #43)', number: 50,
    title: 'Switch the GPS clock', note: 'blocked by #43', url: 'https://example.test/50',
    actions: ['open', 'ready', 'unblock', 'close'] }
  const restore = seed(on, { ...BOARD, blocked: [parked] })
  const asked: string[][] = []
  let editExit = 0
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const verb = e.argv[0] === 'bash' && e.argv[4] === 'issue' ? e.argv[5] : undefined
    const stdout = verb === 'view' ? '{"status": "on-hold", "blockedBy": [{"number": 43, "state": "OPEN"}]}' : ''
    const exitCode = verb === 'edit' ? editExit : 0
    return { value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'unblock-blocked-50' })
  expect(await ui.find({ key: 'confirm-blocked-50', text: 'Confirm unblock' })).toBeDefined()
  expect(asked.some(argv => argv[4] === 'issue')).toBe(false)
  await ui.press({ key: 'confirm-blocked-50' })
  expect(asked).toContainEqual(['bash', '-c', `exec "${DESK}" "$@"`, '_', 'issue', 'edit', '50', '--remove-label',
    'on-hold', '--remove-blocked-by', '43'])
  await ui.unmount()

  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'unblock-blocked-50' })).toBeUndefined()
  expect(await after.find({ key: 'ready-next-50' })).toBeDefined()
  expect(await after.find({ type: 'Text', text: /^#50 +Switch the GPS clock$/ })).toBeDefined()
  await after.unmount()

  // An edit gh refuses puts the issue back in Blocked.
  restore()
  editExit = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'unblock-blocked-50' })
  await again.press({ key: 'confirm-blocked-50' })
  expect(await again.find({ key: 'unblock-blocked-50' })).toBeDefined()
  expect(await again.find({ key: 'ready-next-50' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: /Couldn't unblock #50/ })).toBeDefined()
  await again.unmount()
})

test('Move to Backlog asks to confirm, then moves an Open issue to the top of Backlog, or back when the store refuses, '
  + 'and Move to Open asks to confirm, then takes backlog off', async ($, on) => {
  const restore = seed(on, BOARD)
  const asked: string[][] = []
  let editExit = 0
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const edit = e.argv[0] === 'bash' && e.argv[4] === 'issue' && e.argv[5] === 'edit'
    return { value: { exitCode: edit ? editExit : 0, stdout: '', stderr: '', isStdoutTruncated: false,
      isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const edit = (number: number, flag: string) => ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'issue', 'edit',
    String(number), flag, 'backlog']
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'to-backlog-next-64' })
  expect(asked).not.toContainEqual(edit(64, '--add-label'))
  expect(await ui.find({ key: 'confirm-next-64', text: 'Confirm move to backlog' })).toBeDefined()
  await ui.press({ key: 'confirm-next-64' })
  expect(asked).toContainEqual(edit(64, '--add-label'))
  await ui.unmount()

  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'ready-next-64' })).toBeUndefined()
  expect(await after.find({ key: 'to-open-backlog-64' })).toBeDefined()
  expect(await after.find({ type: 'Text', text: /^- Backlog \(2\)$/ })).toBeDefined()
  await after.unmount()

  // Move to Open asks to confirm, then takes backlog off and moves the issue to the top of Open.
  restore()
  const back = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await back.press({ key: 'to-open-backlog-72' })
  expect(asked).not.toContainEqual(edit(72, '--remove-label'))
  await back.press({ key: 'confirm-backlog-72' })
  expect(asked).toContainEqual(edit(72, '--remove-label'))
  expect(await back.find({ key: 'to-open-backlog-72' })).toBeUndefined()
  expect(await back.find({ key: 'to-backlog-next-72' })).toBeDefined()
  await back.unmount()

  // A label the store refuses puts the issue back in Open.
  restore()
  editExit = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'to-backlog-next-64' })
  await again.press({ key: 'confirm-next-64' })
  expect(await again.find({ key: 'ready-next-64' })).toBeDefined()
  expect(await again.find({ key: 'to-open-backlog-64' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: /Couldn't label #64 backlog/ })).toBeDefined()
  await again.unmount()
})

test('Unpick asks to confirm, then drops a picked issue\'s pick and shows Start, or puts the row back when its run '
  + 'claimed the issue', async ($, on) => {
  const picked: Row = { key: 'queued-47', text: 'Pave the trail  (picked; waits for a free agent slot)', number: 47,
    title: 'Pave the trail', note: 'picked; waits for a free agent slot', unpicked: 'waits for Start',
    url: 'https://example.test/47', actions: ['open', 'unpick', 'to-open'] }
  const restore = seed(on, { ...BOARD, queued: [picked] })
  const unpick = ['bash', '-c', `exec python3 -B ${HOLD} unpick "$1"`, '_', '47']
  const asked: string[][] = []
  let exitCode = 0
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const code = e.argv[2] === unpick[2] ? exitCode : 0
    return { value: { exitCode: code, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'start-queued-47' })).toBeUndefined()
  await ui.press({ key: 'unpick-queued-47' })
  expect(asked).not.toContainEqual(unpick)
  expect(await ui.find({ key: 'confirm-queued-47', text: 'Confirm unpick' })).toBeDefined()
  await ui.press({ key: 'confirm-queued-47' })
  expect(asked).toContainEqual(unpick)
  expect(await ui.find({ key: 'unpick-queued-47' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /Pave the trail {2}\(waits for Start\)/ })).toBeDefined()
  // Start, once confirmed, picks the issue again.
  await ui.press({ key: 'start-queued-47' })
  await ui.press({ key: 'confirm-queued-47' })
  expect(asked).toContainEqual(['bash', '-c', `exec python3 -B ${HOLD} start "$1"`, '_', '47'])
  await ui.unmount()

  // A run that claimed the issue since the board's read dropped its pick, so lib/hold.py refuses, and the row goes
  // back.
  restore()
  exitCode = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'unpick-queued-47' })
  await again.press({ key: 'confirm-queued-47' })
  expect(await again.find({ key: 'unpick-queued-47', text: 'Unpick' })).toBeDefined()
  expect(await again.find({ key: 'start-queued-47' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: /Couldn't unpick #47: its run claimed it/ })).toBeDefined()
  await again.unmount()
})

test('Move to Open on a queued issue asks to confirm, then cancels its waiting run and moves it to Open, or back when '
  + 'its run started', async ($, on) => {
  const waiting: Row = { key: 'waiting-45', text: 'Model trail food  (waiting on #43)', number: 45,
    title: 'Model trail food', note: 'waiting on #43', url: 'https://example.test/45', actions: ['open', 'to-open'] }
  const restore = seed(on, { ...BOARD, queued: [waiting] })
  const unqueue = ['bash', '-c', `exec python3 -B ${DISPATCH} unqueue "$1"`, '_', '45']
  const asked: string[][] = []
  let exitCode = 0
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const code = e.argv[2] === unqueue[2] ? exitCode : 0
    return { value: { exitCode: code, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'to-open-waiting-45' })
  expect(await ui.find({ key: 'confirm-waiting-45', text: 'Confirm move to open' })).toBeDefined()
  expect(asked).not.toContainEqual(unqueue)
  await ui.press({ key: 'confirm-waiting-45' })
  expect(asked).toContainEqual(unqueue)
  await ui.unmount()

  const after = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await after.find({ key: 'to-open-waiting-45' })).toBeUndefined()
  expect(await after.find({ key: 'ready-next-45' })).toBeDefined()
  await after.unmount()

  // A run that started before the press refuses the move, and the issue goes back to Queued.
  restore()
  exitCode = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'to-open-waiting-45' })
  await again.press({ key: 'confirm-waiting-45' })
  expect(await again.find({ key: 'to-open-waiting-45' })).toBeDefined()
  expect(await again.find({ key: 'ready-next-45' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: /Couldn't move #45 to Open: its run started/ })).toBeDefined()
  await again.unmount()
})

test('Move Up, Move Down, Move to Top, and Move to Bottom move a queued issue in the start order at once, and back '
  + 'when the store refuses', async ($, on) => {
  const first: Row = { key: 'queued-45', text: 'Model trail food', number: 45, title: 'Model trail food',
    url: 'https://example.test/45', actions: ['open', 'to-open', 'down', 'bottom'] }
  const second: Row = { key: 'waiting-46', text: 'Mark the trail  (waiting on #43)', number: 46,
    title: 'Mark the trail', note: 'waiting on #43', url: 'https://example.test/46',
    actions: ['open', 'to-open', 'up', 'top'] }
  const restore = seed(on, { ...BOARD, queued: [first, second] })
  const top = ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'queue', 'order', '46', '--top']
  const bottom = ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'queue', 'order', '46', '--bottom']
  const asked: string[][] = []
  let exitCode = 0
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const code = e.argv.includes('queue') ? exitCode : 0
    return { value: { exitCode: code, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // The first issue has no Move Up or Move to Top, and the last no Move Down or Move to Bottom.
  expect(await ui.find({ key: 'up-queued-45' })).toBeUndefined()
  expect(await ui.find({ key: 'top-queued-45' })).toBeUndefined()
  expect(await ui.find({ key: 'down-waiting-46' })).toBeUndefined()
  expect(await ui.find({ key: 'bottom-waiting-46' })).toBeUndefined()
  expect(await ui.find({ key: 'top-waiting-46', text: 'Move to Top' })).toBeDefined()
  expect(await ui.find({ key: 'bottom-queued-45', text: 'Move to Bottom' })).toBeDefined()
  // A move needs no confirm.
  await ui.press({ key: 'top-waiting-46' })
  expect(asked).toContainEqual(top)
  expect(await ui.find({ key: 'down-waiting-46', text: 'Move Down' })).toBeDefined()
  expect(await ui.find({ key: 'up-queued-45', text: 'Move Up' })).toBeDefined()
  expect(await ui.find({ key: 'top-waiting-46' })).toBeUndefined()
  // Move to Bottom puts it back last.
  await ui.press({ key: 'bottom-waiting-46', text: 'Move to Bottom' })
  expect(asked).toContainEqual(bottom)
  expect(await ui.find({ key: 'bottom-queued-45', text: 'Move to Bottom' })).toBeDefined()
  expect(await ui.find({ key: 'bottom-waiting-46' })).toBeUndefined()
  await ui.unmount()

  // An issue that started since the board's read refuses the move, and the order goes back.
  restore()
  exitCode = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'top-waiting-46' })
  expect(await again.find({ key: 'top-waiting-46' })).toBeDefined()
  expect(await again.find({ key: 'up-queued-45' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: /Couldn't move #46 in the start order/ })).toBeDefined()
  await again.unmount()
})

const SORT_FIRST: Row = { key: 'waiting-46', text: 'Mark the trail  (waiting on #45)', number: 46,
  title: 'Mark the trail', url: 'https://example.test/46', actions: ['open', 'to-open', 'down'] }
const SORT_SECOND: Row = { key: 'queued-45', text: 'Model trail food', number: 45, title: 'Model trail food',
  url: 'https://example.test/45', actions: ['open', 'to-open', 'up', 'top'] }
const SORT_RERUN: Row = { key: 'rerun-pr-81', text: 'Fix the trail  (fixing conflicts)', number: 81, actions: ['open'] }

test('Queued\'s heading shows no Sort with one issue in the start order, beside a rerun that holds no place in it',
  async ($, on) => {
  seed(on, { ...BOARD, queued: [SORT_FIRST, SORT_RERUN] })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'toggle-Queued' })).toBeDefined()
  expect(await ui.find({ key: 'sort-order' })).toBeUndefined()
  await ui.unmount()
})

test('Queued\'s heading shows Sort with two or more queued or waiting issues, which asks to confirm, then sorts the '
  + 'start order, and says so when the store refuses', async ($, on) => {
  const sort = ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'queue', 'order', '--sort']
  const asked: string[][] = []
  let exitCode = 0
  const reset = seed(on, { ...BOARD, queued: [SORT_FIRST, SORT_SECOND, SORT_RERUN] })
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const code = e.argv.includes('queue') ? exitCode : 0
    return { value: { exitCode: code, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  on('store.set', () => ({ value: undefined }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'sort-order', text: 'Sort' })).toBeDefined()
  // Sort asks to confirm, and Cancel sorts nothing.
  await ui.press({ key: 'sort-order' })
  expect(await ui.find({ key: 'confirm-sort-order', text: 'Confirm sort' })).toBeDefined()
  await ui.press({ key: 'cancel-sort-order' })
  expect(await ui.find({ key: 'sort-order' })).toBeDefined()
  expect(asked).not.toContainEqual(sort)
  await ui.press({ key: 'sort-order' })
  await ui.press({ key: 'confirm-sort-order' })
  expect(asked).toContainEqual(sort)
  expect(await ui.find({ type: 'Text', text: /Sorted the start order/ })).toBeDefined()
  // Pressing the heading itself still collapses the section, and Sort stays beside it.
  await ui.press({ key: 'toggle-Queued' })
  expect(await ui.find({ type: 'Text', text: /^\+ Queued$/ })).toBeDefined()
  expect(await ui.find({ key: 'to-open-queued-45' })).toBeUndefined()
  expect(await ui.find({ key: 'sort-order' })).toBeDefined()
  await ui.press({ key: 'toggle-Queued' })
  await ui.unmount()

  // A sort the store refuses says so, and the list shows the store's order.
  reset()
  exitCode = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'sort-order' })
  await again.press({ key: 'confirm-sort-order' })
  expect(await again.find({ type: 'Text', text: /Couldn't sort the start order/ })).toBeDefined()
  expect(await again.find({ key: 'to-open-waiting-46' })).toBeDefined()
  await again.unmount()
})

test('Continue is a stopped issue\'s main button, and Take Over, which copies a command, is not', () => {
  expect(isMain('restart')).toBe(true)
  expect(isMain('resume')).toBe(false)
})

test('Retry reads Continue on an agent\'s timed-out run, whose session it carries on, and Retry on any other', () => {
  const failed: Row = { key: 'failed-9', text: 'failed  agent: Model drinks', run: -9, actions: ['retry'] }
  expect(labelOf(failed, 'retry')).toBe('Retry')
  expect(labelOf({ ...failed, continues: true }, 'retry')).toBe('Continue')
  expect(labelOf({ ...failed, continues: true }, 'dismiss')).toBe('Dismiss')
})

test('every action swaps its button for Confirm and Cancel or sits on the short list of those that act at once, and '
  + 'never both', () => {
  const actions = Object.keys(LABELS) as (keyof typeof LABELS)[]
  expect(actions.filter(action => CONFIRMS.has(action) === INSTANT.has(action))).toEqual([])
  expect([...INSTANT].sort()).toEqual(['bottom', 'down', 'log', 'open', 'resume', 'top', 'up'])
})

test('Designed, Cancel run, and Resume ask to confirm: the first press and Cancel run nothing, and Confirm runs the '
  + 'command', async ($, on) => {
  const designing: Row = { key: 'decide-19', text: 'Windows builds', number: 19, url: 'https://example.test/19',
    actions: ['open', 'designed'] }
  const waits: Row = { key: 'rerun-1297', text: 'Bake the lightmaps  (waits for Start)', number: 1297,
    title: 'Bake the lightmaps', note: 'waits for Start', request: 33, url: 'https://example.test/1297',
    actions: ['open', 'cancel'] }
  const held: Row = { key: 'blocked-50', text: 'Switch the GPS clock  (on hold)', number: 50,
    url: 'https://example.test/50', actions: ['open', 'unpause'] }
  seed(on, { ...BOARD, design: [designing], queued: [waits], blocked: [held] })
  const asked: string[][] = []
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const cases: [string, string, string, string[]][] = [
    ['designed', 'decide-19', 'Confirm designed',
      ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'issue', 'edit', '19', '--remove-label', 'needs-design']],
    ['cancel', 'rerun-1297', 'Confirm cancel run',
      ['bash', '-c', `exec python3 -B ${DISPATCH} cancel "$1"`, '_', '33']],
    ['unpause', 'blocked-50', 'Confirm resume',
      ['bash', '-c', `exec python3 -B ${DISPATCH} unpause "$1"`, '_', '50']],
  ]
  for (const [action, key, label, command] of cases) {
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await ui.press({ key: `${action}-${key}` })
    expect(await ui.find({ key: `confirm-${key}`, text: label })).toBeDefined()
    await ui.press({ key: `cancel-confirm-${key}` })
    expect(await ui.find({ key: `${action}-${key}` })).toBeDefined()
    await ui.press({ key: `${action}-${key}` })
    expect(asked).not.toContainEqual(command)
    await ui.press({ key: `confirm-${key}` })
    expect(asked).toContainEqual(command)
    await ui.unmount()
  }
})

// Open's rows for the list order's tests, #100 first, each with the moves its place allows.
const listed = (count: number): Row[] => withListMoves(Array.from({ length: count }, (_, index) => ({
  key: `next-${100 + index}`, text: `Issue ${100 + index}`, number: 100 + index, title: `Issue ${100 + index}`,
  url: `https://example.test/${100 + index}`, actions: ['open', 'ready', 'to-backlog', 'close'] })))
const listOrder = (...args: string[]) => ['bash', '-c', `exec "${DESK}" "$@"`, '_', 'list', 'order', ...args]

test('Move Up, Move Down, Move to Top, and Move to Bottom move an Open issue in the list order at once, beside the '
  + 'issue they pass, and back when the store refuses', async ($, on) => {
  const restore = seed(on, { ...BOARD, next: listed(3) })
  const asked: string[][] = []
  let exitCode = 0
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const code = e.argv.includes('list') ? exitCode : 0
    return { value: { exitCode: code, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // The first issue has no Move Up or Move to Top, the last no Move Down or Move to Bottom, and the moves follow the
  // row's other buttons.
  expect(await ui.find({ key: 'up-next-100' })).toBeUndefined()
  expect(await ui.find({ key: 'top-next-100' })).toBeUndefined()
  expect(await ui.find({ key: 'down-next-102' })).toBeUndefined()
  expect(await ui.find({ key: 'bottom-next-102' })).toBeUndefined()
  expect(await ui.find({ key: 'up-next-101', text: 'Move Up' })).toBeDefined()
  // A move needs no confirm and moves the row at once.
  await ui.press({ key: 'down-next-100' })
  expect(asked).toContainEqual(listOrder('100', '--after', '101'))
  expect(await ui.find({ key: 'top-next-100', text: 'Move to Top' })).toBeDefined()
  expect(await ui.find({ key: 'up-next-101' })).toBeUndefined()
  await ui.press({ key: 'bottom-next-100' })
  expect(asked).toContainEqual(listOrder('100', '--after', '102'))
  await ui.press({ key: 'top-next-100' })
  expect(asked).toContainEqual(listOrder('100', '--before', '101'))
  await ui.press({ key: 'up-next-102' })
  expect(asked).toContainEqual(listOrder('102', '--before', '101'))
  expect(asked.some(argv => argv.includes('queue'))).toBe(false)
  await ui.unmount()

  // An issue that closed since the board's read refuses the move, and the section goes back.
  restore()
  exitCode = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'top-next-102' })
  expect(asked).toContainEqual(listOrder('102', '--before', '100'))
  expect(await again.find({ key: 'top-next-102' })).toBeDefined()
  expect(await again.find({ key: 'up-next-100' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: /Couldn't move #102 in the list order/ })).toBeDefined()
  await again.unmount()
})

test('a move across a page\'s edge carries the issue to the next or previous page, and the pane stays on its page',
  async ($, on) => {
  seed(on, { ...BOARD, next: listed(10) })
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false,
    isStderrTruncated: false } }))
  on('clock.now', () => ({ value: 0 }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // The page's last issue moves down onto the next page, and the first page shows the one that took its place.
  await ui.press({ key: 'down-next-104' })
  expect(await ui.find({ type: 'Text', text: /^1-5 of 10 {6}$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-104' })).toBeUndefined()
  expect(await ui.find({ key: 'ready-next-105' })).toBeDefined()
  // On the second page, its first issue moves up onto the first page.
  await ui.press({ key: 'down-Open' })
  expect(await ui.find({ type: 'Text', text: /^6-10 of 10 {5}$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-104' })).toBeDefined()
  await ui.press({ key: 'up-next-104' })
  expect(await ui.find({ type: 'Text', text: /^6-10 of 10 {5}$/ })).toBeDefined()
  expect(await ui.find({ key: 'ready-next-104' })).toBeUndefined()
  expect(await ui.find({ key: 'ready-next-105' })).toBeDefined()
  await ui.unmount()
})

test('a list section\'s heading shows Sort with two or more issues, which asks to confirm, then lists them by number, '
  + 'and says so when the store refuses', async ($, on) => {
  const parked: Row[] = withListMoves([
    { key: 'blocked-50', text: 'Switch the GPS clock  (on hold)', number: 50, actions: ['open', 'unblock'] },
    { key: 'blocked-52', text: 'Mark the trail  (on hold)', number: 52, actions: ['open', 'unblock'] },
    { key: 'stack-wait-53', text: 'Pack the tents  (waiting on PR #186)', number: 53, actions: ['open'] }])
  const reset = seed(on, { ...BOARD, blocked: parked, backlog: [], ideas: [] })
  const asked: string[][] = []
  let exitCode = 0
  on('process.run', (_, e) => {
    asked.push([...e.argv])
    const code = e.argv.includes('list') ? exitCode : 0
    return { value: { exitCode: code, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.now', () => ({ value: 0 }))
  on('store.set', () => ({ value: undefined }))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // Open, Needs Design, Backlog, and Ideas hold fewer than two issues, so their headings have no Sort.
  for (const title of ['Open', 'Needs Design', 'Backlog', 'Ideas']) {
    expect(await ui.find({ key: `sort-${title}` })).toBeUndefined()
  }
  expect(await ui.find({ key: 'sort-Blocked', text: 'Sort' })).toBeDefined()
  // Sort asks to confirm, and Cancel sorts nothing.
  await ui.press({ key: 'sort-Blocked' })
  expect(await ui.find({ key: 'confirm-sort-Blocked', text: 'Confirm sort' })).toBeDefined()
  await ui.press({ key: 'cancel-sort-Blocked' })
  expect(asked.some(argv => argv.includes('--reset'))).toBe(false)
  // The stack wait holds no place in the list order, so the sort leaves it out.
  await ui.press({ key: 'sort-Blocked' })
  await ui.press({ key: 'confirm-sort-Blocked' })
  expect(asked).toContainEqual(listOrder('--reset', '50', '52'))
  expect(await ui.find({ type: 'Text', text: /Sorted Blocked by number/ })).toBeDefined()
  // Pressing the heading itself still collapses the section, and Sort stays beside it.
  await ui.press({ key: 'toggle-Blocked' })
  expect(await ui.find({ type: 'Text', text: /^\+ Blocked \(3\)$/ })).toBeDefined()
  expect(await ui.find({ key: 'sort-Blocked' })).toBeDefined()
  await ui.press({ key: 'toggle-Blocked' })
  await ui.unmount()

  // A sort the store refuses says so, and the list shows the store's order.
  reset()
  exitCode = 1
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await again.press({ key: 'sort-Blocked' })
  await again.press({ key: 'confirm-sort-Blocked' })
  expect(await again.find({ type: 'Text', text: /Couldn't sort Blocked; the list shows the store's order/ })).toBeDefined()
  expect(await again.find({ key: 'unblock-blocked-50' })).toBeDefined()
  await again.unmount()
})
