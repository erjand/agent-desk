// What the web server's modules share: the projects it serves, and the host they run programs through. The event
// stream and board reads stay in server.ts, which imports every module here, so the controls and the card actions take
// those as a Loop.
import { execFile } from 'node:child_process'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Board } from '../inbox/types'
import type { Host } from '../inbox/hooks/board.ts'
import type { IssueRunner } from './hold.ts'

export const HERE = dirname(fileURLToPath(import.meta.url))
// The agent-desk checkout whose command the server runs: AGENT_DESK_HOME, or else the one the server runs from, so the
// command matches the server even where PATH lacks agent-desk, as in its systemd service.
export const DESK_HOME = process.env.AGENT_DESK_HOME || dirname(HERE)
export const DESK = join(DESK_HOME, 'bin', 'agent-desk')
// What the board keeps between runs, such as the failed runs the person dismissed. The pane keeps its own in the mod's
// store, so a run dismissed in one still shows in the other.
export const STATE = join(process.env.XDG_STATE_HOME ?? join(homedir(), '.local', 'state'), 'agent-desk', 'web.json')

/** One registered project's inbox. */
export type Desk = {
  /** The repository's name, which the page addresses the project by. */
  id: string
  repo: string
  owner: string
  title: string
  main: string
  chores: string[]
  /** The configured issue runner, or null when issueRunner is invalid. */
  issueRunner?: IssueRunner | null
  /** The agent slots the project's `.agent-desk.json` sets, as hold.ts's configAgentSlots reads them. */
  agentSlots?: number
  /** The gpu slots the project's `.agent-desk.json` sets, or null for none, as hold.ts's configGpuSlots reads them. */
  gpuSlots?: number | null
  board: Board | null
  /** When the last refresh landed, in milliseconds. */
  at: number
  refreshing: boolean
  /** The project's label colors, as six hex digits by label name, from the store. */
  labels: Record<string, string>
  /** The model and effort each chore and agents run with, by skill, and `agent`. */
  models: Record<string, { model: string; effort: string }>
  /** The state of each PR a chore opened, by number: OPEN, MERGED, or CLOSED. */
  chorePrs: Record<number, string>
  /** The open PR that changes the chores' schedule, from agent/chore-schedule, by number. */
  schedulePr: number | null
  /** What the model table was read for, as the config's and models.py's modified times. */
  modelsFor: string
  /** The chores the page started that GitHub doesn't list as running yet, with when, by chore name. */
  starting: Map<string, number>
  /** The Needs You and Needs Design rows the last refresh showed; a row missing from it raises a toast. */
  known: Set<string> | null
  /** Counts the reads of the board begun, so one that a later read or a button's command overtook doesn't land. */
  refreshes: number
  /** The buttons' commands under way, while which no read of the board starts. */
  commands: number
}

/** What the hold and chore controls and the card actions ask of the server's event stream and board reads. */
export type Loop = {
  publish: () => void
  toast: (text: string) => void
  fail: (desk: Desk, text: string) => void
  refresh: (desk: Desk) => Promise<void>
  refreshAll: () => Promise<void>
}

let stored: Record<string, unknown> = {}

async function save(): Promise<void> {
  await mkdir(dirname(STATE), { recursive: true })
  await writeFile(STATE, JSON.stringify(stored))
}

/** Reads what the board kept between runs; none on a first run, or when the file can't be read. */
export async function loadStored(): Promise<void> {
  try {
    stored = JSON.parse(await readFile(STATE, 'utf8'))
  } catch {
    stored = {}
  }
}

// agent-desk's scripts read GH_REPO ahead of the checkout they run in, so a server started inside an agent-desk run,
// which sets it to that run's project, would read every desk's board from that one project. The store stamps a write
// from inside an agent-desk run as the agent's, or the desk's when AGENT_DESK_AUTHOR says so, and the server writes for
// the person at the page.
const REDIRECTS = ['GH_REPO', 'AGENT_DESK_RUN', 'AGENT_DESK_AUTHOR']

/**
 * The server's environment for the programs it runs, less what would point them away from a desk's own repository or
 * stamp what they write as another author's than `you`, with AGENT_DESK_HOME naming the server's checkout, which the
 * board's commands run agent-desk from.
 */
export function childEnv(env: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  return { ...Object.fromEntries(Object.entries(env).filter(([name]) => !REDIRECTS.includes(name))),
    AGENT_DESK_HOME: DESK_HOME }
}

export const host: Host = {
  run: (argv, cwd, stdin) => new Promise(resolve => {
    const child = execFile(argv[0] ?? '', argv.slice(1),
      { cwd, env: childEnv(), timeout: 30_000, maxBuffer: 64 * 1_048_576 },
      (error, stdout) => resolve(error ? null : stdout))
    // A child that exits before reading its input closes the pipe, and the write fails with EPIPE; its exit status
    // already reports the failure.
    child.stdin?.on('error', () => {})
    if (stdin === undefined) child.stdin?.end()
    else child.stdin?.end(stdin)
  }),
  now: async () => Date.now(),
  get: async key => stored[key],
  set: async (key, value) => {
    stored[key] = value
    await save()
  },
}

export const desks = new Map<string, Desk>()
