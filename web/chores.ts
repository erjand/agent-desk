// The chores' controls and stats: starting and stopping a chore from the page, how its newest runs went, and how many
// more runs it has queued.
import { execFile } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { readFile, writeFile } from 'node:fs/promises'
import { basename, dirname, join } from 'node:path'

import type { Board } from '../inbox/types'
import { itemLink } from './review.ts'
import { childEnv, DESK, host, STATE } from './state.ts'
import type { Desk, Loop } from './state.ts'
import { choreRuns } from './store.ts'
import type { ChoreRun } from './store.ts'

// How many of a chore's newest runs its stats count.
const STAT_RUNS = 10

// When to read the board again after starting a chore, in milliseconds after the start: GitHub lists a new run a few
// seconds on, or later when it's busy.
const CHORE_CHECKS = [5_000, 15_000, 40_000]

// How long a chore the page started shows as starting while GitHub doesn't list its run.
const STARTING_MS = 180_000

/**
 * The board with each chore the page started shown as starting, until GitHub lists its run or STARTING_MS passes;
 * drops the chores it lists running or that expired from `starting`.
 */
export function withStarting(board: Board, starting: Map<string, number>, now: number): Board {
  const list = board.choreList ?? []
  for (const [name, at] of starting) {
    if (now - at > STARTING_MS || list.some(chore => chore.name === name && chore.last.startsWith('running'))) {
      starting.delete(name)
    }
  }
  return starting.size === 0 ? board : { ...board, choreList: list.map(chore => (starting.has(chore.name)
    ? { ...chore, last: 'starting', next: 'after this run' } : chore)) }
}

/** How a chore's newest runs went: how many, how long on average, and what became of the PRs they opened. */
type ChoreStats = { runs: number; minutes: number | null; merged: number; closed: number; open: number }

/** The stats of a chore's newest STAT_RUNS runs, from the store's runs and their reviews' states. */
export function choreStats(runs: Pick<ChoreRun, 'minutes' | 'review'>[], prs: Record<number, string>): ChoreStats {
  const newest = runs.slice(-STAT_RUNS)
  const timed = newest.map(run => run.minutes).filter((minutes): minutes is number => minutes !== null)
  const states = newest.map(run => (run.review === null ? undefined : prs[run.review]))
  return { runs: newest.length,
    minutes: timed.length > 0 ? Math.round(timed.reduce((sum, one) => sum + one, 0) / timed.length) : null,
    merged: states.filter(state => state === 'MERGED').length,
    closed: states.filter(state => state === 'CLOSED').length, open: states.filter(state => state === 'OPEN').length }
}

/** Each chore's model and effort, and how its newest runs went, by its skill, the name its row shows. */
export function choreInfoOf(desk: Desk): Record<string, { model: string; effort: string } & ChoreStats> {
  const runs = choreRuns(desk.repo)
  return Object.fromEntries(desk.chores.map(skill => [skill, { ...(desk.models[skill] ?? { model: '', effort: '' }),
    ...choreStats(runs.filter(run => run.skill === skill), desk.chorePrs) }]))
}

// Where lib/chore_repeat.py keeps how many more times each project's chores run.
const REPEATS = join(dirname(STATE), 'chore-repeat.json')

/** How many more runs each of a project's chores has queued, or `forever`, by its skill. */
export function repeatsOf(text: string, repo: string): Record<string, number | 'forever'> {
  try {
    return { ...(JSON.parse(text) as Record<string, Record<string, number | 'forever'>>)[repo] }
  } catch {
    return {}
  }
}

export function readRepeats(repo: string): Record<string, number | 'forever'> {
  try {
    return repeatsOf(readFileSync(REPEATS, 'utf8'), repo)
  } catch {
    return {}
  }
}

/** Runs agent-desk chore for a chore: start it `times` times or until stopped, or stop its queue. */
function deskChore(desk: Desk, skill: string, times: string): Promise<string | null> {
  return host.run([DESK, 'chore', skill, times], desk.main)
}

/** Stops a chore's queue, so it runs no more after the run going now. */
export async function stopChore(loop: Loop, desk: Desk, name: string): Promise<void> {
  const done = await deskChore(desk, name, 'stop')
  if (done === null) {
    loop.fail(desk, `Couldn't stop ${name}'s queue.`)
  }
}

/**
 * Starts a chore `times` times, one run after another, or `forever` until stopped. It shows the chore starting at
 * once, then reads the board until GitHub lists its run.
 */
export async function runChore(loop: Loop, desk: Desk, name: string, times: string): Promise<void> {
  desk.starting.set(name, Date.now())
  if (desk.board !== null) {
    desk.board = withStarting(desk.board, desk.starting, Date.now())
  }
  loop.publish()
  const done = (await deskChore(desk, name, times)) !== null
  if (!done) {
    loop.fail(desk, `Couldn't start ${name}.`)
    desk.starting.delete(name)
    await loop.refresh(desk)
    return
  }
  let waited = 0
  for (const at of CHORE_CHECKS) {
    await new Promise(resolve => setTimeout(resolve, at - waited))
    waited = at
    await loop.refresh(desk)
    if (!desk.starting.has(name)) {
      return
    }
  }
}

// The schedule controls: each chore's days between runs, and whether the timer skips it, change through a PR that
// edits the project's .agent-desk.json on one branch, in its own worktree beside the main checkout.
const SCHEDULE_SLUG = 'chore-schedule'
export const SCHEDULE_BRANCH = `agent/${SCHEDULE_SLUG}`
const CONFIG_FILE = '.agent-desk.json'
// The most days a chore may wait between runs.
const MAX_DAYS = 365
// How long one git or gh step may take; a new worktree runs the project's worktree.setup, which may build.
const STEP_MS = 10 * 60_000

/** A change to one chore's schedule: its days between runs, whether it's paused, or both. */
type ScheduleChange = { every?: number; paused?: boolean }

/** One JSON value's place in the config's text, and for an object or array, its members' or items' places. */
type Spot = { start: number; end: number; members?: { key: string; start: number; value: Spot }[]; items?: Spot[] }

/** Where each value in a JSON text sits, for text JSON.parse accepts. */
function spotsOf(text: string): Spot {
  let at = 0
  const skip = () => {
    while (/\s/.test(text[at] ?? '')) at++
  }
  const word = (): string => {
    const start = at++
    while (text[at] !== '"') at += text[at] === '\\' ? 2 : 1
    at++
    return JSON.parse(text.slice(start, at)) as string
  }
  const value = (): Spot => {
    skip()
    const start = at
    if (text[at] === '{' || text[at] === '[') {
      const object = text[at++] === '{'
      const members: NonNullable<Spot['members']> = []
      const items: Spot[] = []
      skip()
      while (text[at] !== '}' && text[at] !== ']') {
        if (object) {
          skip()
          const keyStart = at
          const key = word()
          skip()
          at++
          members.push({ key, start: keyStart, value: value() })
        } else {
          items.push(value())
        }
        skip()
        if (text[at] === ',') at++
        skip()
      }
      at++
      return object ? { start, end: at, members } : { start, end: at, items }
    }
    if (text[at] === '"') {
      word()
    } else {
      at += /^[-+.\w]+/.exec(text.slice(at))?.[0].length ?? 1
    }
    return { start, end: at }
  }
  return value()
}

/** The text to cut to drop list[index] with the comma that joins it to its neighbors. */
function cutOf(list: { start: number; end: number }[], index: number): [number, number] {
  const one = list[index] as { start: number; end: number }
  const before = list[index - 1]
  const after = list[index + 1]
  return before ? [before.end, one.end] : after ? [one.start, after.start] : [one.start, one.end]
}

/** The space before a spot, to start a new member or item the same way; a space where it starts on the same line. */
function gapBefore(text: string, start: number): string {
  const gap = /\s*$/.exec(text.slice(0, start))?.[0] ?? ''
  return gap.includes('\n') ? gap : ' '
}

/**
 * The config's text with one chore's days between runs or paused state changed, and every other character kept, so
 * the file's own layout survives. Pausing adds the skill to `chores.paused`, making the list after `chores.every` when
 * there is none; resuming takes it off and drops a list left empty. Throws for a skill missing from `chores.every`.
 */
export function editConfig(text: string, skill: string, change: ScheduleChange): string {
  const config = JSON.parse(text) as { chores?: { every?: Record<string, number>; paused?: unknown } }
  const every = config.chores?.every ?? {}
  if (!Object.hasOwn(every, skill)) {
    throw new Error(`${CONFIG_FILE} has no chore named ${skill}`)
  }
  if (change.every !== undefined && !(Number.isInteger(change.every) && change.every >= 1
    && change.every <= MAX_DAYS)) {
    throw new Error(`a chore runs every 1 to ${MAX_DAYS} days`)
  }
  const chores = spotsOf(text).members?.find(one => one.key === 'chores')?.value
  const everyMember = chores?.members?.find(one => one.key === 'every')
  const day = everyMember?.value.members?.find(one => one.key === skill)?.value
  if (!chores?.members || !everyMember || !day) {
    throw new Error(`${CONFIG_FILE} has no chore named ${skill}`)
  }
  const edits: { start: number; end: number; text: string }[] = []
  if (change.every !== undefined && change.every !== every[skill]) {
    edits.push({ start: day.start, end: day.end, text: String(change.every) })
  }
  const listed = Array.isArray(config.chores?.paused) ? config.chores.paused as unknown[] : []
  const pausedIndex = chores.members.findIndex(one => one.key === 'paused')
  const paused = chores.members[pausedIndex]
  const quoted = JSON.stringify(skill)
  if (change.paused === true && !listed.includes(skill)) {
    const items = paused?.value.items
    if (!paused) {
      const gap = gapBefore(text, everyMember.start)
      edits.push({ start: everyMember.value.end, end: everyMember.value.end, text: `,${gap}"paused": [${quoted}]` })
    } else if (!items || items.length === 0) {
      edits.push({ start: paused.value.start, end: paused.value.end, text: `[${quoted}]` })
    } else {
      const last = items[items.length - 1] as Spot
      edits.push({ start: last.end, end: last.end, text: `,${gapBefore(text, last.start)}${quoted}` })
    }
  } else if (change.paused === false && paused?.value.items && listed.includes(skill)) {
    const items = paused.value.items
    const members = chores.members.map(one => ({ start: one.start, end: one.value.end }))
    const [start, end] = items.length === 1 ? cutOf(members, pausedIndex) : cutOf(items, listed.indexOf(skill))
    edits.push({ start, end, text: '' })
  }
  return edits.sort((a, b) => b.start - a.start)
    .reduce((edited, one) => edited.slice(0, one.start) + one.text + edited.slice(one.end), text)
}

/**
 * The config's text with chore `from` renamed to `to` wherever the config names it: a key of `chores.every` or
 * `models.chores`, or an item of `chores.paused`. Every other character stays, as editConfig keeps them, so each name
 * keeps its place. Throws when the config names no chore `from`, or names `to` already.
 */
export function renameConfig(text: string, from: string, to: string): string {
  const names = choreNames(text)
  if (!names.includes(from)) {
    throw new Error(`${CONFIG_FILE} has no chore named ${from}`)
  }
  if (names.includes(to)) {
    throw new Error(`${CONFIG_FILE} has a chore named ${to} already`)
  }
  const top = spotsOf(text)
  const member = (spot: Spot | undefined, key: string) => spot?.members?.find(one => one.key === key)?.value
  const chores = member(top, 'chores')
  const keyEnd = (start: number) => start + (/^"(?:[^"\\]|\\.)*"/.exec(text.slice(start))?.[0].length ?? 0)
  const keys = [member(chores, 'every'), member(member(top, 'models'), 'chores')]
    .flatMap(spot => spot?.members ?? []).filter(one => one.key === from)
    .map(one => ({ start: one.start, end: keyEnd(one.start) }))
  const items = (member(chores, 'paused')?.items ?? [])
    .filter(item => text[item.start] === '"' && JSON.parse(text.slice(item.start, item.end)) === from)
  return [...keys, ...items].sort((a, b) => b.start - a.start)
    .reduce((edited, one) => edited.slice(0, one.start) + JSON.stringify(to) + edited.slice(one.end), text)
}

/** The parts of a config the schedule reads, as JSON.parse reads them. */
type ScheduleConfig = { chores?: { every?: Record<string, number>; paused?: unknown }; models?: { chores?: unknown } }

function configOf(text: string): ScheduleConfig {
  try {
    const config: unknown = JSON.parse(text)
    return typeof config === 'object' && config !== null ? config as ScheduleConfig : {}
  } catch {
    return {}
  }
}

/** A config's chores, each once, in the order `chores.every`, `chores.paused`, and `models.chores` name them. */
export function choreNames(text: string): string[] {
  const { chores, models } = configOf(text)
  const keys = (value: unknown) => (typeof value === 'object' && value !== null && !Array.isArray(value)
    ? Object.keys(value) : [])
  const paused = Array.isArray(chores?.paused) ? chores.paused.filter(one => typeof one === 'string') : []
  return [...new Set([...keys(chores?.every), ...paused, ...keys(models?.chores)])]
}

/**
 * The chores `after` renames from `before`, as [old, new] pairs. renameConfig keeps each name in its place, so the
 * names only `before` has pair in order with the names only `after` has; none when their counts differ.
 */
export function renamesOf(before: string, after: string): [string, string][] {
  const old = choreNames(before)
  const now = choreNames(after)
  const gone = old.filter(name => !now.includes(name))
  const added = now.filter(name => !old.includes(name))
  return gone.length === added.length ? gone.map((name, index) => [name, added[index] ?? name]) : []
}

/** A chore's days between runs and whether it's paused in a config's text, by skill; empty for text that isn't JSON. */
function schedulesOf(text: string): Map<string, { every: number; paused: boolean }> {
  const { chores } = configOf(text)
  const paused = Array.isArray(chores?.paused) ? chores.paused : []
  return new Map(Object.entries(chores?.every ?? {}).map(([skill, every]) => [skill,
    { every, paused: paused.includes(skill) }]))
}

/**
 * How each chore's schedule differs from `before` to `after`, by skill: its new days, its paused state, or both. A
 * renamed chore compares with its old name's schedule.
 */
export function scheduleDiff(before: string, after: string): Map<string, ScheduleChange> {
  const renamed = new Map(renamesOf(before, after))
  const old = new Map([...schedulesOf(before)].map(([skill, was]) => [renamed.get(skill) ?? skill, was]))
  return new Map([...schedulesOf(after)].flatMap(([skill, now]) => {
    const was = old.get(skill)
    const change: ScheduleChange = { ...was?.every === now.every ? {} : { every: now.every },
      ...(was?.paused ?? false) === now.paused ? {} : { paused: now.paused } }
    return Object.keys(change).length === 0 ? [] : [[skill, change] as const]
  }))
}

/** A change in words, as `audit every 3 days, pause audit`. */
function inWords(skill: string, change: ScheduleChange): string {
  const days = change.every === undefined ? []
    : [`${skill} every ${change.every === 1 ? 'day' : `${change.every} days`}`]
  const state = change.paused === undefined ? [] : [`${change.paused ? 'pause' : 'resume'} ${skill}`]
  return [...days, ...state].join(', ')
}

/** The schedule PR's title and body, for every change its branch makes to main's config. */
export function scheduleText(main: string, branch: string): { title: string; body: string } {
  const renames = renamesOf(main, branch)
  const changes = [...scheduleDiff(main, branch)]
  const was = schedulesOf(main)
  const oldName = new Map(renames.map(([from, to]) => [to, from]))
  const lines = [...renames.map(([from, to]) => `- \`${from}\` renamed to \`${to}\``),
    ...changes.map(([skill, change]) => {
      const old = was.get(oldName.get(skill) ?? skill)
      const days = change.every === undefined ? [] : [`every ${old?.every ?? '?'} days → ${change.every} days`]
      const state = change.paused === undefined ? [] : [change.paused ? 'paused' : 'resumed']
      return `- \`${skill}\`: ${[...days, ...state].join(', ')}`
    })]
  const words = [...renames.map(([from, to]) => `rename ${from} to ${to}`),
    ...changes.map(([skill, change]) => inWords(skill, change))]
  return {
    title: `Change the chore schedule: ${words.join(', ') || 'no change from main'}`,
    body: [`Changes the chores' schedule in \`${CONFIG_FILE}\`, from the agent-desk web page or`
      + ' `agent-desk chores rename`:', '', ...lines.length > 0 ? lines : ['- No change from main.'], '',
      'The new schedule takes effect once this PR merges and the sync updates the main checkout.',
      ...renames.length > 0 ? ['', '`agent-desk chores rename` moved each renamed chore\'s runs to its new name'
        + ' already.'] : [], '', '<sub>Posted by agent-desk</sub>', '<!-- agent-desk -->'].join('\n'),
  }
}

/**
 * Each chore's change the open schedule PR makes, by skill, with the PR's number: none without an open PR, or once
 * its worktree is gone.
 */
export function pendingOf(desk: Desk): Record<string, ScheduleChange & { pr: number }> {
  const pr = desk.schedulePr
  if (pr === null) {
    return {}
  }
  try {
    const main = readFileSync(join(desk.main, CONFIG_FILE), 'utf8')
    const branch = readFileSync(join(scheduleWorktree(desk.main), CONFIG_FILE), 'utf8')
    return Object.fromEntries([...scheduleDiff(main, branch)].map(([skill, change]) => [skill, { ...change, pr }]))
  } catch {
    return {}
  }
}

/** The schedule branch's worktree beside a main checkout, as `agent-desk worktree` names it. */
export function scheduleWorktree(main: string): string {
  return join(dirname(main), `${basename(main)}-${SCHEDULE_SLUG}`)
}

/** How a git or gh step ended: whether it ran clean, and what it printed. */
export type StepResult = { ok: boolean; out: string; err: string }

/** Runs the schedule's git and agent-desk steps; the tests swap `run` for a fake store and worktree command. */
export const steps = {
  run: (argv: string[], cwd: string): Promise<StepResult> => new Promise(resolve => {
    execFile(argv[0] ?? '', argv.slice(1), { cwd, env: { ...childEnv(), GIT_TERMINAL_PROMPT: '0' }, timeout: STEP_MS,
      maxBuffer: 16 * 1_048_576 }, (error, out, err) => resolve({ ok: !error, out: String(out),
      err: String(err) || (error ? error.message : '') }))
  }),
}

/** A step's output, or an error naming the step and the last lines it printed. */
async function must(argv: string[], cwd: string): Promise<string> {
  const done = await steps.run(argv, cwd)
  if (!done.ok) {
    const said = (done.err || done.out).trim().split('\n').slice(-3).join(' ')
    const step = argv[0] === 'git' ? argv.slice(0, 2) : ['agent-desk', ...argv.slice(1, argv[1] === 'review' ? 3 : 2)]
    throw new Error(`${step.join(' ')} failed: ${said || 'no output'}`)
  }
  return done.out
}

// The change each project's schedule PR is making now, by project, so a second save waits for the first.
const scheduling = new Map<string, Promise<unknown>>()

/** The project a schedule change edits: the page's desk, or the one lib/chore_rename.py names. */
type ScheduleDesk = Pick<Desk, 'id' | 'repo' | 'main'>

/** A schedule review: its number, its link on the page, and its title. */
type ScheduleReview = { pr: number; url: string; title: string }

/** An edit to the config's text on the schedule branch: the edited text, and the commit's words for the change. */
type ConfigEdit = (text: string) => { edited: string; words: string }

/**
 * Commits a change to a chore's schedule on the schedule branch and pushes it: to the open schedule review, which it
 * retitles, or to a new review of main, both through `agent-desk review`. The branch works in its own worktree, made
 * with `agent-desk worktree`, or reset to the local main when no PR is open, and the config it compares against comes
 * from the local main too, so nothing fetches. A step that fails throws its error, and leaves no branch or commit it
 * made behind.
 */
export function changeSchedule(desk: ScheduleDesk, skill: string, change: ScheduleChange): Promise<ScheduleReview> {
  return scheduled(desk, text => {
    const edited = editConfig(text, skill, change)
    if (edited === text) {
      throw new Error(`${skill} already runs that way`)
    }
    return { edited, words: inWords(skill, scheduleDiff(text, edited).get(skill) ?? change) }
  })
}

/**
 * Renames a chore in the config's `chores.every`, `chores.paused`, and `models.chores` through the schedule review,
 * as changeSchedule changes its schedule, for `agent-desk chores rename`.
 */
export function renameChore(desk: ScheduleDesk, from: string, to: string): Promise<ScheduleReview> {
  return scheduled(desk, text => ({ edited: renameConfig(text, from, to), words: `rename ${from} to ${to}` }))
}

function scheduled(desk: ScheduleDesk, edit: ConfigEdit): Promise<ScheduleReview> {
  const before = scheduling.get(desk.id) ?? Promise.resolve()
  const done = before.catch(() => undefined).then(() => scheduleOnce(desk, edit))
  scheduling.set(desk.id, done)
  return done
}

async function scheduleOnce(desk: ScheduleDesk, edit: ConfigEdit): Promise<ScheduleReview> {
  const dest = scheduleWorktree(desk.main)
  const listed = JSON.parse(await must([DESK, 'review', 'list', '--repo', desk.repo, '--head', SCHEDULE_BRANCH,
    '--state', 'open', '--json', 'number', '--limit', '1'], desk.main) || '[]') as { number: number }[]
  const open = listed[0]
  const existed = existsSync(dest)
  if (existed) {
    const branch = (await must(['git', 'branch', '--show-current'], dest)).trim()
    if (branch !== SCHEDULE_BRANCH) {
      throw new Error(`${dest} is on ${branch || 'no branch'}, not ${SCHEDULE_BRANCH}`)
    }
    // Files the project's worktree.setup leaves untracked don't count; the commit takes the config alone.
    if ((await must(['git', 'status', '--porcelain', '--untracked-files=no'], dest)).trim() !== '') {
      throw new Error(`${dest} has uncommitted changes`)
    }
    if ((await must(['git', 'rev-list', '-1', 'HEAD', '--not', '--remotes=origin'], dest)).trim() !== '') {
      throw new Error(`${dest} has commits that aren't on origin`)
    }
  } else {
    await must([DESK, 'worktree', SCHEDULE_SLUG], desk.main)
  }
  // Without an open PR the branch starts again from main, so a later PR leaves out a closed one's commits; a step that
  // fails then removes the worktree and branch. With one, a failed step puts the branch back where the change found it.
  const start = open ? (await must(['git', 'rev-parse', 'HEAD'], dest)).trim() : ''
  const undo = async (pushed: boolean) => {
    if (open) {
      await steps.run(['git', 'reset', '-q', '--hard', start], dest)
      return
    }
    if (pushed) {
      await steps.run(['git', 'push', '-q', 'origin', '--delete', SCHEDULE_BRANCH], dest)
    }
    await steps.run(['git', 'worktree', 'remove', '--force', dest], desk.main)
    await steps.run(['git', 'branch', '-q', '-D', SCHEDULE_BRANCH], desk.main)
  }
  let pushed = false
  try {
    if (!open) {
      await must(['git', 'checkout', '-q', '-B', SCHEDULE_BRANCH, 'refs/heads/main'], dest)
    }
    const file = join(dest, CONFIG_FILE)
    const { edited, words } = edit(await readFile(file, 'utf8'))
    await writeFile(file, edited)
    await must(['git', 'commit', '-q', '-m', `Change the chore schedule: ${words}`, '--', CONFIG_FILE], dest)
    await must(['git', 'push', '-q', ...open ? [] : ['--force'], '-u', 'origin', `HEAD:${SCHEDULE_BRANCH}`], dest)
    pushed = true
    const main = await must(['git', 'show', `refs/heads/main:${CONFIG_FILE}`], dest)
    const { title, body } = scheduleText(main, edited)
    if (open) {
      await must([DESK, 'review', 'edit', String(open.number), '--repo', desk.repo, '--title', title, '--body', body],
        dest)
      return { pr: open.number, url: itemLink(desk, open.number), title }
    }
    const made = (await must([DESK, 'review', 'create', '--repo', desk.repo, '--base', 'main', '--branch',
      SCHEDULE_BRANCH, '--title', title, '--body', body], dest)).trim().split('\n').pop() ?? ''
    const pr = Number(made.replace(/^#/, ''))
    return { pr, url: itemLink(desk, pr), title }
  } catch (error) {
    // A commit pushed to an open PR stays: only the PR's title and body missed it.
    if (!(open && pushed)) {
      await undo(pushed)
    }
    throw error
  }
}

/** The change a POST to /api/chore-schedule asks for, or why it's refused. */
export function scheduleRequest(sent: Record<string, unknown>): ScheduleChange | string {
  const change: ScheduleChange = {}
  if (sent.every !== undefined) {
    if (!Number.isInteger(sent.every) || Number(sent.every) < 1 || Number(sent.every) > MAX_DAYS) {
      return `a chore runs every 1 to ${MAX_DAYS} days`
    }
    change.every = Number(sent.every)
  }
  if (sent.paused !== undefined) {
    if (typeof sent.paused !== 'boolean') {
      return 'paused is true or false'
    }
    change.paused = sent.paused
  }
  return Object.keys(change).length === 0 ? 'no change named' : change
}
