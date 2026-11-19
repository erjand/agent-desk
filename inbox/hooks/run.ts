// What the board's modules share to run programs through a Host: agent-desk's paths and the store reader.
// Like board.ts, it imports no Claude Code module and no Node module, so the pane and the web server share it.
import type { Board } from '../types'
import type { Host, Project, StoreBoard } from './board.ts'

// The agent-desk command, for bash inside double quotes: the one on PATH, or $AGENT_DESK_HOME/bin/agent-desk when that
// variable names a checkout, as the web server sets it to its own.
export const DESK = '${AGENT_DESK_HOME:+$AGENT_DESK_HOME/bin/}agent-desk'
// What the pane says when it can't find that command. A plugin installed from a marketplace holds only the pane and
// skills, so the command, lib/, and the web page come from a clone.
export const NO_DESK = "The agent-desk command isn't on PATH. Clone https://github.com/erjand/agent-desk and run "
  + './install.sh in the clone; README.md\'s Install section says more.'
// The checkout that command runs, for bash inside double quotes, which holds the lib/ scripts below.
const ROOT = '${AGENT_DESK_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v agent-desk)")")")}'
// lib/hold.py, which says which new runs start on this machine: in Manual, only those Start picked here.
export const HOLD = `"${ROOT}/lib/hold.py"`
// lib/dispatch.py, which starts runs as systemd units on this machine and lists the ones that wait or work.
export const DISPATCH = `"${ROOT}/lib/dispatch.py"`
// lib/failures.py, which prints the lines of a check's log that say what failed.
export const FAILURES = `"${ROOT}/lib/failures.py"`

/** Whether the agent-desk command runs here, from PATH or from $AGENT_DESK_HOME. */
export async function deskFound(host: Host, project: Project): Promise<boolean> {
  return (await run(host, project, ['bash', '-c', `command -v "${DESK}"`])) !== null
}

/** The board the pane shows without the agent-desk command: no rows, and how to install it. */
export function deskMissing(at: string): Board {
  return { at, runners: [], needs: [], merging: [], design: [], designTotal: 0, queued: [], next: [], blocked: [],
    backlog: [], ideas: [], merged: [], chores: [], error: NO_DESK }
}

export function run(host: Host, project: Project, argv: string[], cwd = project.root, stdin?: string):
  Promise<string | null> {
  return stdin === undefined ? host.run(argv, cwd) : host.run(argv, cwd, stdin)
}

export function parsed<T>(out: string | null): T | null {
  if (out === null || out.trim() === '') {
    return null
  }
  try {
    return JSON.parse(out) as T
  } catch {
    return null
  }
}

/**
 * The project's issues, reviews, checks, and runs as the local store holds them, in one read by `agent-desk board
 * --json` in the project's checkout; null when the command failed or printed something else.
 */
export async function readStore(host: Host, project: Project): Promise<StoreBoard | null> {
  const read = parsed<StoreBoard>(await run(host, project, ['bash', '-c', `exec "${DESK}" board --json`]))
  return read !== null && Array.isArray(read.issues) && Array.isArray(read.reviews) && Array.isArray(read.merged)
    ? read : null
}
