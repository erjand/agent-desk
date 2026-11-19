// Run with: node --test web/
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'

import { changeSchedule, choreInfoOf, choreNames, editConfig, pendingOf, renameChore, renameConfig, renamesOf,
  SCHEDULE_BRANCH, scheduleDiff, scheduleRequest, scheduleText, scheduleWorktree, steps } from './chores.ts'
import type { StepResult } from './chores.ts'
import { main } from './chore-rename.ts'
import type { Desk } from './state.ts'
import { storeFixture } from './store-fixture.ts'
import { choreRuns, useStore } from './store.ts'

// A config laid out by hand: a slots table with extra spaces, and a chore table on one line.
const CONFIG = `{
  "title": "Agents",
  "slots": { "agent": 2,   "chore": 1 },
  "chores": {
    "every": { "singleshot-untested-one": 7, "singleshot-refactor-one": 7, "audit": 14 },
    "gapHours": 18,
    "maxAuditIssues": 5
  }
}
`

test('a chore\'s stats read its finished runs from the store, by its skill, with what became of their reviews', () => {
  // Two refactor runs that ended, one still running, one cancelled, and an audit run; another project's refactor run.
  useStore(storeFixture(`
for project, target, minutes, review, end in (("org/game", "singleshot-refactor-one", 10, 11, "succeeded"),
                                             ("org/game", "singleshot-refactor-one", 20, 12, "failed"),
                                             ("org/game", "singleshot-refactor-one", 5, None, None),
                                             ("org/game", "singleshot-refactor-one", 5, None, "cancelled"),
                                             ("org/game", "audit", 30, None, "succeeded"),
                                             ("org/other", "singleshot-refactor-one", 90, 13, "succeeded")):
    run = store.request_run(project, "chore", target)
    store.run_started(run, f"unit-{run}")
    store.run_result(run, model="sonnet", effort="medium", minutes=minutes, review=review)
    if end:
        store.run_ended(run, end)
`))
  const desk = { repo: 'org/game', chores: ['singleshot-refactor-one', 'audit', 'singleshot-untested-one'],
    models: { 'singleshot-refactor-one': { model: 'opus', effort: 'high' } }, chorePrs: { 11: 'MERGED', 12: 'OPEN' } }
  assert.deepEqual(choreInfoOf(desk as unknown as Desk), {
    'singleshot-refactor-one': { model: 'opus', effort: 'high', runs: 2, minutes: 15, merged: 1, closed: 0, open: 1 },
    audit: { model: '', effort: '', runs: 1, minutes: 30, merged: 0, closed: 0, open: 0 },
    'singleshot-untested-one': { model: '', effort: '', runs: 0, minutes: null, merged: 0, closed: 0, open: 0 },
  })
})

test('a chore run imported from the old log counts as older than the runs the dispatcher recorded before it', () => {
  useStore(storeFixture(`
run = store.request_run("org/game", "chore", "audit")
store.run_started(run, "unit-audit")
store.run_ended(run, "succeeded", 0)
store.run_result(run, minutes=10)
store.import_run("org/game", "chore", "audit", "2026-10-01T10:00:00Z", "2026-10-01T09:30:00Z", "succeeded", 0,
                 {"model": None, "effort": None, "minutes": 30, "turns": None, "cost": None, "review": None,
                  "labels": None, "start": None}, set())
`))
  assert.deepEqual(choreRuns('org/game').map(run => run.minutes), [30, 10])
})

test('changing a chore\'s days edits that number alone and keeps the rest of the file as it was', () => {
  const edited = editConfig(CONFIG, 'audit', { every: 3 })
  assert.equal(edited, CONFIG.replace('"audit": 14', '"audit": 3'))
  assert.equal(editConfig(CONFIG, 'audit', { every: 14 }), CONFIG)
})

test('pausing makes chores.paused after chores.every, and pausing another adds to it the way it\'s laid out', () => {
  const one = editConfig(CONFIG, 'singleshot-refactor-one', { paused: true })
  assert.equal(one, CONFIG.replace('"audit": 14 },', '"audit": 14 },\n    "paused": ["singleshot-refactor-one"],'))
  const two = editConfig(one, 'audit', { paused: true })
  assert.equal(two,
    CONFIG.replace('"audit": 14 },', '"audit": 14 },\n    "paused": ["singleshot-refactor-one", "audit"],'))
  assert.equal(editConfig(two, 'audit', { paused: true }), two)
  const stacked = '{ "chores": { "every": { "a": 1, "b": 2 }, "paused": [\n  "a"\n] } }'
  assert.equal(editConfig(stacked, 'b', { paused: true }),
    '{ "chores": { "every": { "a": 1, "b": 2 }, "paused": [\n  "a",\n  "b"\n] } }')
  assert.equal(editConfig('{"chores":{"paused":[],"every":{"a":1}}}', 'a', { paused: true }),
    '{"chores":{"paused":["a"],"every":{"a":1}}}')
})

test('resuming takes a chore off chores.paused, and drops the list once it empties, back to the file it was', () => {
  const two = editConfig(editConfig(CONFIG, 'singleshot-refactor-one', { paused: true }), 'audit', { paused: true })
  const one = editConfig(two, 'singleshot-refactor-one', { paused: false })
  assert.equal(one, CONFIG.replace('"audit": 14 },', '"audit": 14 },\n    "paused": ["audit"],'))
  assert.equal(editConfig(one, 'audit', { paused: false }), CONFIG)
  assert.equal(editConfig(CONFIG, 'audit', { paused: false }), CONFIG)
  const last = '{ "chores": { "every": { "a": 1 }, "paused": ["a"] } }'
  assert.equal(editConfig(last, 'a', { paused: false }), '{ "chores": { "every": { "a": 1 } } }')
  const first = '{ "chores": { "paused": ["a"], "every": { "a": 1 } } }'
  assert.equal(editConfig(first, 'a', { paused: false }), '{ "chores": { "every": { "a": 1 } } }')
})

test('days and a pause change together, and the edited text reads as the config it says', () => {
  const edited = editConfig(CONFIG, 'singleshot-untested-one', { every: 30, paused: true })
  const chores = JSON.parse(edited).chores
  assert.deepEqual(chores.every, { 'singleshot-untested-one': 30, 'singleshot-refactor-one': 7, audit: 14 })
  assert.deepEqual(chores.paused, ['singleshot-untested-one'])
})

test('a chore missing from chores.every, or days outside 1 to 365, is refused', () => {
  assert.throws(() => editConfig(CONFIG, 'gapHours', { every: 3 }), /no chore named gapHours/)
  assert.throws(() => editConfig(CONFIG, 'untested-one', { paused: true }), /no chore named untested-one/)
  assert.throws(() => editConfig('{ "title": "x" }', 'audit', { every: 3 }), /no chore named audit/)
  assert.throws(() => editConfig(CONFIG, 'audit', { every: 0 }), /1 to 365/)
  assert.throws(() => editConfig(CONFIG, 'audit', { every: 366 }), /1 to 365/)
  assert.throws(() => editConfig(CONFIG, 'audit', { every: 2.5 }), /1 to 365/)
})

test('the schedule route takes whole days from 1 to 365 and a true or false pause, and refuses anything else', () => {
  assert.deepEqual(scheduleRequest({ every: 3 }), { every: 3 })
  assert.deepEqual(scheduleRequest({ paused: false }), { paused: false })
  assert.deepEqual(scheduleRequest({ every: 365, paused: true }), { every: 365, paused: true })
  for (const sent of [{}, { every: 0 }, { every: 366 }, { every: '3' }, { every: 1.5 }, { paused: 'yes' }]) {
    assert.equal(typeof scheduleRequest(sent), 'string', JSON.stringify(sent))
  }
})

test('the PR\'s title and body list every change its branch makes to main, by the names the rows show', () => {
  const branch = editConfig(editConfig(CONFIG, 'audit', { every: 3 }), 'singleshot-refactor-one', { paused: true })
  const { title, body } = scheduleText(CONFIG, branch)
  assert.equal(title, 'Change the chore schedule: pause singleshot-refactor-one, audit every 3 days')
  assert.match(body, /- `singleshot-refactor-one`: paused\n- `audit`: every 14 days → 3 days\n/)
  assert.match(body, /<!-- agent-desk -->$/)
  assert.equal(scheduleText(CONFIG, editConfig(CONFIG, 'audit', { every: 1 })).title,
    'Change the chore schedule: audit every day')
  assert.equal(scheduleText(CONFIG, CONFIG).title, 'Change the chore schedule: no change from main')
})

test('a rename changes the chore\'s name in every, paused, and its models, and keeps every other character', () => {
  const config = CONFIG.replace('"maxAuditIssues": 5', '"maxAuditIssues": 5,\n    "paused": [ "audit",\n'
    + '      "singleshot-refactor-one" ]').replace('"title": "Agents",',
    '"title": "Agents",\n  "models": { "chores": { "singleshot-refactor-one": { "model": "sonnet" } } },')
  const renamed = renameConfig(config, 'singleshot-refactor-one', 'refactor')
  assert.equal(renamed, config.replaceAll('"singleshot-refactor-one"', '"refactor"'))
  assert.deepEqual(choreNames(renamed), ['singleshot-untested-one', 'refactor', 'audit'])
  assert.equal(renameConfig(CONFIG, 'audit', 'deep-audit'), CONFIG.replace('"audit": 14', '"deep-audit": 14'))
  assert.throws(() => renameConfig(CONFIG, 'tidy', 'refactor'), /no chore named tidy/)
  assert.throws(() => renameConfig(CONFIG, 'audit', 'singleshot-refactor-one'),
    /has a chore named singleshot-refactor-one already/)
})

test('the PR names a rename, and compares the renamed chore\'s schedule with its old name\'s', () => {
  const renamed = renameConfig(CONFIG, 'singleshot-untested-one', 'add-tests')
  assert.deepEqual(renamesOf(CONFIG, renamed), [['singleshot-untested-one', 'add-tests']])
  assert.deepEqual(scheduleDiff(CONFIG, renamed), new Map())
  const branch = editConfig(renamed, 'add-tests', { every: 3 })
  const { title, body } = scheduleText(CONFIG, branch)
  assert.equal(title, 'Change the chore schedule: rename singleshot-untested-one to add-tests, add-tests every 3 days')
  assert.match(body, /- `singleshot-untested-one` renamed to `add-tests`\n- `add-tests`: every 7 days → 3 days\n/)
  assert.match(body, /`agent-desk chores rename` moved each renamed chore's runs to its new name already\./)
  assert.doesNotMatch(scheduleText(CONFIG, editConfig(CONFIG, 'audit', { every: 3 })).body, /renamed/)
})

// A project on disk: a bare origin, and a main checkout with the config on main.
const home = mkdtempSync(join(tmpdir(), 'agent-desk-schedule-'))
after(() => rmSync(home, { recursive: true, force: true }))

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
}

function project(name: string): Desk {
  const origin = join(home, `${name}.git`)
  const main = join(home, name)
  execFileSync('git', ['init', '-q', '--bare', '-b', 'main', origin])
  execFileSync('git', ['clone', '-q', origin, main], { stdio: 'ignore' })
  git(main, 'config', 'user.name', 'Test')
  git(main, 'config', 'user.email', 'test@example.com')
  git(main, 'checkout', '-q', '-b', 'main')
  writeFileSync(join(main, '.agent-desk.json'), CONFIG)
  git(main, 'add', '.agent-desk.json')
  git(main, 'commit', '-q', '-m', 'Add the config')
  git(main, 'push', '-q', '-u', 'origin', 'main')
  return { id: name, repo: `me/${name}`, main, chores: ['singleshot-untested-one', 'singleshot-refactor-one', 'audit'],
    schedulePr: null } as unknown as Desk
}

/**
 * The store as the steps see it: `agent-desk review` answers from `prs`, `agent-desk worktree` adds the branch's
 * worktree the way lib/worktree.sh does, and git runs for real. `fail` makes the first step whose words start with it
 * fail, with `agent-desk` standing for the command's path.
 */
function fakeStore(prs: { number: number; url: string; title: string; body: string }[], fail: string[] = []) {
  const real = steps.run
  const ran: string[][] = []
  steps.run = async (argv, cwd): Promise<StepResult> => {
    ran.push(argv)
    const words = argv[0]?.endsWith('/agent-desk') ? ['agent-desk', ...argv.slice(1)] : argv
    if (fail.length > 0 && fail.every((word, index) => words[index] === word)) {
      return { ok: false, out: '', err: `remote: ${words.slice(0, 2).join(' ')} refused\n` }
    }
    const value = (flag: string) => argv[argv.indexOf(flag) + 1] ?? ''
    if (words[0] === 'agent-desk' && words[1] === 'review' && words[2] === 'list') {
      return { ok: true, out: JSON.stringify(prs.slice(-1).map(({ number }) => ({ number }))), err: '' }
    }
    if (words[0] === 'agent-desk' && words[1] === 'review' && words[2] === 'create') {
      const number = 52 + prs.length
      prs.push({ number, url: '', title: value('--title'), body: value('--body') })
      return { ok: true, out: `${number}\n`, err: '' }
    }
    if (words[0] === 'agent-desk' && words[1] === 'review' && words[2] === 'edit') {
      const pr = prs.find(one => one.number === Number(argv[3]))
      Object.assign(pr ?? {}, { title: value('--title'), body: value('--body') })
      return { ok: true, out: '', err: '' }
    }
    if (argv[1] === 'worktree' && argv[0]?.endsWith('agent-desk')) {
      const local = git(cwd, 'branch', '--list', SCHEDULE_BRANCH).trim()
      git(cwd, 'worktree', 'add', '-q', ...local ? [scheduleWorktree(cwd), SCHEDULE_BRANCH]
        : ['-b', SCHEDULE_BRANCH, scheduleWorktree(cwd), 'refs/heads/main'])
      return { ok: true, out: '', err: '' }
    }
    return real(argv, cwd)
  }
  return { ran, restore: () => { steps.run = real } }
}

// The config on the schedule branch, which the review reads, after checking that the push left origin's copy there too.
function onBranch(desk: Desk): string {
  const head = git(desk.main, 'rev-parse', SCHEDULE_BRANCH).trim()
  assert.equal(git(desk.main, 'ls-remote', '--heads', 'origin', SCHEDULE_BRANCH).split('\t')[0], head)
  return git(desk.main, 'show', `${SCHEDULE_BRANCH}:.agent-desk.json`)
}

test('a first change opens a PR from a new worktree, and the next adds a commit and retitles it', async () => {
  const desk = project('first')
  const prs: { number: number; url: string; title: string; body: string }[] = []
  const github = fakeStore(prs)
  try {
    const made = await changeSchedule(desk, 'audit', { every: 3 })
    assert.deepEqual(made, { pr: 52, url: '#first:52',
      title: 'Change the chore schedule: audit every 3 days' })
    assert.equal(onBranch(desk), CONFIG.replace('"audit": 14', '"audit": 3'))
    // The main checkout keeps main's config, while the row reads the change from the worktree.
    assert.equal(readFileSync(join(desk.main, '.agent-desk.json'), 'utf8'), CONFIG)
    assert.equal(git(desk.main, 'status', '--porcelain'), '')
    desk.schedulePr = made.pr
    assert.deepEqual(pendingOf(desk), { audit: { every: 3, pr: 52 } })

    const more = await changeSchedule(desk, 'singleshot-refactor-one', { paused: true })
    assert.equal(more.pr, 52)
    assert.equal(prs.length, 1)
    assert.equal(prs[0]?.title, 'Change the chore schedule: pause singleshot-refactor-one, audit every 3 days')
    onBranch(desk)
    assert.deepEqual(git(desk.main, 'log', '--format=%s', `main..${SCHEDULE_BRANCH}`).trim().split('\n'),
      ['Change the chore schedule: pause singleshot-refactor-one', 'Change the chore schedule: audit every 3 days'])
    assert.deepEqual(pendingOf(desk),
      { 'singleshot-refactor-one': { paused: true, pr: 52 }, audit: { every: 3, pr: 52 } })
  } finally {
    github.restore()
  }
})

test('a change cuts its branch from the local main and compares against it, fetching nothing', async () => {
  const desk = project('local')
  // The merge queue moved main on this machine, and origin's main hasn't caught up.
  writeFileSync(join(desk.main, '.agent-desk.json'),
    CONFIG.replace('"singleshot-refactor-one": 7', '"singleshot-refactor-one": 9'))
  git(desk.main, 'commit', '-q', '-am', 'Run refactor-one every 9 days')
  const prs: { number: number; url: string; title: string; body: string }[] = []
  const github = fakeStore(prs)
  try {
    const made = await changeSchedule(desk, 'audit', { every: 3 })
    assert.equal(made.title, 'Change the chore schedule: audit every 3 days')
    assert.deepEqual(JSON.parse(onBranch(desk)).chores.every,
      { 'singleshot-untested-one': 7, 'singleshot-refactor-one': 9, audit: 3 })
    assert.ok(!github.ran.some(argv => argv[0] === 'git' && (argv[1] === 'fetch' || argv[1] === 'pull')))
  } finally {
    github.restore()
  }
})

test('a failed push leaves no worktree or branch behind, and says which step failed and why', async () => {
  const desk = project('push')
  const github = fakeStore([], ['git', 'push'])
  try {
    await assert.rejects(changeSchedule(desk, 'audit', { every: 3 }), /git push failed: remote: git push refused/)
    assert.equal(existsSync(scheduleWorktree(desk.main)), false)
    assert.equal(git(desk.main, 'branch', '--list', SCHEDULE_BRANCH), '')
    assert.equal(git(desk.main, 'ls-remote', '--heads', 'origin', SCHEDULE_BRANCH), '')
  } finally {
    github.restore()
  }
})

test('a review the store couldn\'t open takes its pushed branch back off origin', async () => {
  const desk = project('create')
  const github = fakeStore([], ['agent-desk', 'review', 'create'])
  try {
    await assert.rejects(changeSchedule(desk, 'audit', { paused: true }), /agent-desk review create failed/)
    assert.equal(existsSync(scheduleWorktree(desk.main)), false)
    assert.equal(git(desk.main, 'branch', '--list', SCHEDULE_BRANCH), '')
    assert.equal(git(desk.main, 'ls-remote', '--heads', 'origin', SCHEDULE_BRANCH), '')
  } finally {
    github.restore()
  }
})

test('a failed push to an open PR takes its commit back, and the PR\'s worktree stays', async () => {
  const desk = project('again')
  const prs: { number: number; url: string; title: string; body: string }[] = []
  let github = fakeStore(prs)
  try {
    await changeSchedule(desk, 'audit', { every: 3 })
  } finally {
    github.restore()
  }
  const dest = scheduleWorktree(desk.main)
  const head = git(dest, 'rev-parse', 'HEAD')
  github = fakeStore(prs, ['git', 'push'])
  try {
    await assert.rejects(changeSchedule(desk, 'audit', { every: 9 }), /git push failed/)
    assert.equal(git(dest, 'rev-parse', 'HEAD'), head)
    assert.equal(git(dest, 'status', '--porcelain'), '')
    assert.equal(prs[0]?.title, 'Change the chore schedule: audit every 3 days')
  } finally {
    github.restore()
  }
})

test('a change to a chore the config lacks, or one it already has, makes nothing', async () => {
  const desk = project('unknown')
  const github = fakeStore([])
  try {
    await assert.rejects(changeSchedule(desk, 'nightly', { every: 3 }), /no chore named nightly/)
    await assert.rejects(changeSchedule(desk, 'audit', { every: 14 }), /audit already runs that way/)
    assert.equal(existsSync(scheduleWorktree(desk.main)), false)
    assert.equal(git(desk.main, 'branch', '--list', SCHEDULE_BRANCH), '')
    assert.ok(!github.ran.some(argv => argv[1] === 'push'))
  } finally {
    github.restore()
  }
})

test('without an open PR, a leftover worktree starts again from main, leaving a closed PR\'s commits out', async () => {
  const desk = project('closed')
  const prs: { number: number; url: string; title: string; body: string }[] = []
  let github = fakeStore(prs)
  try {
    await changeSchedule(desk, 'audit', { every: 3 })
  } finally {
    github.restore()
  }
  // The PR closed unmerged, before the cleanup removed its worktree.
  github = fakeStore([])
  try {
    const made = await changeSchedule(desk, 'audit', { paused: true })
    assert.equal(made.title, 'Change the chore schedule: pause audit')
    assert.equal(JSON.parse(onBranch(desk)).chores.every.audit, 14)
  } finally {
    github.restore()
  }
})

test('a commit someone left in the schedule worktree, or a change to a file there, stops the change and stays',
  async () => {
  const desk = project('kept')
  const prs: { number: number; url: string; title: string; body: string }[] = []
  const github = fakeStore(prs)
  try {
    await changeSchedule(desk, 'audit', { every: 3 })
    const dest = scheduleWorktree(desk.main)
    writeFileSync(join(dest, 'notes.md'), 'by hand\n')
    git(dest, 'add', 'notes.md')
    await assert.rejects(changeSchedule(desk, 'audit', { every: 9 }), /has uncommitted changes/)
    git(dest, 'commit', '-q', '-m', 'Notes by hand')
    const head = git(dest, 'rev-parse', 'HEAD')
    await assert.rejects(changeSchedule(desk, 'audit', { every: 9 }), /has commits that aren't on origin/)
    assert.equal(git(dest, 'rev-parse', 'HEAD'), head)
    assert.equal(prs[0]?.title, 'Change the chore schedule: audit every 3 days')
  } finally {
    github.restore()
  }
})

test('a rename joins the open schedule PR, and so does the script agent-desk chores rename runs',
  async () => {
  const desk = project('rename')
  const prs: { number: number; url: string; title: string; body: string }[] = []
  const github = fakeStore(prs)
  const written: string[] = []
  const write = process.stdout.write
  try {
    await changeSchedule(desk, 'audit', { every: 3 })
    const made = await renameChore(desk, 'singleshot-refactor-one', 'refactor')
    assert.deepEqual(made, { pr: 52, url: '#rename:52',
      title: 'Change the chore schedule: rename singleshot-refactor-one to refactor, audit every 3 days' })
    assert.deepEqual(JSON.parse(onBranch(desk)).chores.every, { 'singleshot-untested-one': 7, refactor: 7, audit: 3 })
    assert.equal(git(desk.main, 'log', '-1', '--format=%s', SCHEDULE_BRANCH).trim(),
      'Change the chore schedule: rename singleshot-refactor-one to refactor')
    await assert.rejects(renameChore(desk, 'singleshot-refactor-one', 'refactor'), /no chore named/)

    process.stdout.write = ((text: string) => written.push(text)) as typeof process.stdout.write
    const status = await main([desk.repo, desk.main, 'singleshot-untested-one', 'add-tests'])
    process.stdout.write = write
    assert.equal(status, 0)
    assert.deepEqual(written, ['52\n'])
    assert.equal(prs.length, 1)
    assert.match(prs[0]?.title ?? '', /^Change the chore schedule: rename singleshot-untested-one to add-tests, rename /)
    assert.equal(readFileSync(join(desk.main, '.agent-desk.json'), 'utf8'), CONFIG)
  } finally {
    process.stdout.write = write
    github.restore()
  }
})
