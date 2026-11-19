// The header's hold controls: which new runs the dispatcher starts, through lib/hold.py, and the weekly usage limit.
import { execFile } from 'node:child_process'
import { readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { childEnv, DESK_HOME, host, STATE } from './state.ts'
import type { Desk, Loop } from './state.ts'

// lib/hold.py, which holds new runs, and the state it keeps.
const HOLD = join(DESK_HOME, 'lib', 'hold.py')
const HOLD_STATE = join(dirname(STATE), 'hold.json')
// lib/usage.py, which sets the weekly usage limit that hold.json keeps as `usageLimit`, and the plan's usage it last
// read, which the dispatcher, the chore timer, and the command keep fresh while the limit is on.
const USAGE = join(DESK_HOME, 'lib', 'usage.py')
const USAGE_STATE = join(dirname(STATE), 'usage.json')
// How long a reading holds runs when no newer one comes, as usage.py's STALE_SECONDS.
const STALE_S = 3600

// The modes, each of which one project can also take on its own, which lib/hold.py keeps by repository.
export const PROJECT_MODES = new Set(['running', 'manual', 'held'])

/**
 * Which new runs the dispatcher starts: running, which is Auto; manual, where only the issues picked from the desk
 * start; or held, where new runs wait. Held carries the mode Resume goes back to. `projects` holds the modes projects
 * have of their own, by repository; the rest take `state`.
 */
export function readHold(): { state: string; since: string | null; before: string; projects: Record<string, string> } {
  try {
    const { state, since, before, projects } = JSON.parse(readFileSync(HOLD_STATE, 'utf8')) as { state?: string
      since?: string; before?: string; projects?: Record<string, string> }
    const own = Object.fromEntries(Object.entries(projects ?? {}).filter(([, mode]) => PROJECT_MODES.has(mode)))
    if (state === 'manual' || state === 'held') {
      return { state, since: since ?? null, before: before === 'manual' ? 'manual' : 'running', projects: own }
    }
    return { state: 'running', since: null, before: 'running', projects: own }
  } catch {
    // No hold file means every new run starts.
  }
  return { state: 'running', since: null, before: 'running', projects: {} }
}

/**
 * When lib/hold.py last saved its state, or 0 before it saved any. It changes with every mode set from the page, the
 * command line, or a run, the way the dispatcher's hold_stamp watches it.
 */
export function holdStamp(): number {
  try {
    return statSync(HOLD_STATE).mtimeMs
  } catch {
    return 0
  }
}

// How long lib/hold.py may take to change the mode.
const HOLD_MS = 60_000

/** Runs lib/hold.py with a command, past host.run's 30 seconds, and says whether it succeeded. */
function runHold(...args: string[]): Promise<boolean> {
  return new Promise(resolve => {
    execFile('python3', ['-B', HOLD, ...args], { env: childEnv(), timeout: HOLD_MS }, error => resolve(!error))
  })
}

/**
 * Moves to Auto or Manual, holds new runs, or resumes the mode Hold left. With a project, Auto, Manual, or Hold changes
 * that project alone.
 */
export async function changeHold(loop: Loop, asked: string, desk?: Desk): Promise<void> {
  const state = asked === 'resume' ? readHold().before : asked
  const done = await runHold(...(asked === 'resume' ? ['resume'] : desk ? ['set', state, desk.repo] : ['set', state]))
  if (!done) {
    loop.toast('Couldn\'t change which new runs start.')
  } else if (state === 'running' || state === 'manual') {
    loop.toast(`${desk ? `${desk.title} in ` : ''}${state === 'manual' ? 'Manual: runs start when you press Start'
      : 'Auto: every new run starts'}.`)
  }
  loop.publish()
  if (state === 'running' || state === 'manual') {
    void loop.refreshAll()
  }
}

/**
 * The weekly usage limit and the plan's last reading, for the header: whether the limit is on, its percentage, or null
 * before one was set, and the reading's percentage, when it was read, and when its window resets, each null without a
 * reading. `problem` says why the reading doesn't count, as lib/usage.py's check does, or is null while it counts: an
 * error, no reading, or a reading over an hour old or from a window that has reset. The page reads no usage itself.
 */
export type Usage = { on: boolean; limit: number | null; percent: number | null; readAt: number | null
  resetsAt: string | null; problem: string | null }

/** The weekly usage limit lib/usage.py keeps and the reading it last took, at `now` in seconds since the epoch. */
export function readUsage(now = Date.now() / 1000): Usage {
  const json = (path: string): Record<string, unknown> => {
    try {
      const data: unknown = JSON.parse(readFileSync(path, 'utf8'))
      return data !== null && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : {}
    } catch {
      return {}
    }
  }
  const limit = json(HOLD_STATE).usageLimit as { on?: unknown; percent?: unknown } | undefined
  const set = typeof limit?.on === 'boolean' && Number.isInteger(limit.percent) && Number(limit.percent) >= 0
    && Number(limit.percent) <= 100
  const data = json(USAGE_STATE)
  const percent = typeof data.percent === 'number' ? data.percent : null
  const readAt = typeof data.read_at === 'number' ? data.read_at : null
  const resetsAt = typeof data.resets_at === 'string' ? data.resets_at : null
  const resets = resetsAt === null ? NaN : Date.parse(resetsAt) / 1000
  const fresh = percent !== null && readAt !== null && now - readAt >= 0 && now - readAt < STALE_S
    && (Number.isNaN(resets) || now < resets)
  const problem = fresh ? null : typeof data.error === 'string' && data.error ? data.error
    : readAt === null ? 'no reading yet' : 'the last reading is out of date'
  return { on: set && limit.on === true, limit: set ? Number(limit.percent) : null, percent, readAt, resetsAt, problem }
}

/**
 * Turns the weekly usage limit on at `percent`, or off, through lib/usage.py as `agent-desk usage-limit` does, and
 * publishes the header's new state. Turning it on reads the plan's usage when the last reading is due. Says whether
 * the command took it.
 */
export async function changeUsageLimit(loop: Loop, on: boolean, percent?: number): Promise<boolean> {
  const done = await host.run(['python3', '-B', USAGE, on ? String(percent) : 'off'])
  loop.publish()
  return done !== null
}
