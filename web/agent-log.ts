import { createHash } from 'node:crypto'
import { readdir, realpath, stat } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'

import { transcriptFolder } from './chat.ts'
import { HERE, host } from './state.ts'
import type { Desk } from './state.ts'
import { storePath } from './store.ts'

// The formatter that turns a session's transcript into the log an Actions run shows, merged with the session's beats
// file, the character it leads each beat line with, and how many of the log's newest lines the page gets.
const AGENT_LOG = join(HERE, '..', 'lib', 'agent_log.py')
const BEAT_MARK = '\x1e'
const LOG_LINES = 600

/**
 * An issue's newest agent session, as its log reads: the session, its worktree, when it last wrote, its lines, and the
 * indexes of the lines that are beats, which lib/agent_run.py logs while the agent waits on a quiet tool call.
 */
type Runner = 'claude' | 'codex'

export type AgentLog = { runner: Runner; session: string; worktree: string; at: string; sessions: number; lines: string[];
  beats: number[]; resumeCommand?: string }

/** The beats file lib/agent_run.py writes for a session, in agent-desk's state folder beside the store. */
function beatsFile(session: string): string {
  return join(dirname(storePath()), 'beats', session)
}

/** A merged log's lines with each beat's leading mark taken off, and the indexes of the lines that carried it. */
export function splitBeats(out: string): { lines: string[]; beats: number[] } {
  const lines: string[] = []
  const beats: number[] = []
  for (const line of out.split('\n').filter(Boolean)) {
    if (line.startsWith(BEAT_MARK)) {
      beats.push(lines.length)
    }
    lines.push(line.startsWith(BEAT_MARK) ? line.slice(BEAT_MARK.length) : line)
  }
  return { lines, beats }
}

export function codexLogFolder(state: string, worktree: string): string {
  const key = createHash('sha256').update(resolve(worktree)).digest('hex')
  return join(state, 'codex-logs', key)
}

async function sessionsIn(folder: string, runner: Runner) {
  const names = await readdir(folder).catch(() => [])
  return (await Promise.all(names.filter(name => /^[0-9a-f-]{36}\.jsonl$/.test(name)).map(async name => {
    const path = join(folder, name)
    const info = await stat(path).catch(() => null)
    return info?.isFile() ? { path, session: name.slice(0, -6), at: info.mtimeMs, runner } : null
  }))).filter(one => one !== null)
}

/** Reads the newest Claude or Codex session in the issue's worktree and merges its beats into the log. */
export async function agentLogOf(desk: Desk, number: number): Promise<AgentLog | null> {
  const worktree = `${desk.main}-issue-${number}`
  const cwd = await realpath(worktree).catch(() => worktree)
  const [claude, codex] = await Promise.all([
    sessionsIn(transcriptFolder(worktree, process.env.AGENT_DESK_TRANSCRIPTS || undefined), 'claude'),
    sessionsIn(codexLogFolder(dirname(storePath()), cwd), 'codex'),
  ])
  const dated = [...claude, ...codex].sort((a, b) => b.at - a.at)
  const newest = dated[0]
  if (newest === undefined) {
    return null
  }
  const { session, runner, at, path } = newest
  const [out, initial] = await Promise.all([
    host.run(['bash', '-c', 'python3 -B "$1" "$2" < "$3" | tail -n "$4"', '_', AGENT_LOG,
      beatsFile(session), path, String(LOG_LINES)]),
    runner === 'codex' ? host.run(['head', '-n', '1', '--', path]) : null,
  ])
  let resumeCommand: string | undefined
  try {
    const settings = JSON.parse(initial ?? 'null')?.local_runner
    if ((settings?.provider === 'ollama' || settings?.provider === 'lmstudio') && typeof settings.model === 'string') {
      const model = settings.model.replaceAll("'", "'\\''")
      resumeCommand = `codex resume --oss --local-provider ${settings.provider} --model='${model}' ${session}`
    }
  } catch {
    // A partial transcript can still show its formatted log.
  }
  return { runner, session, worktree, at: new Date(at).toISOString(), sessions: dated.length,
    resumeCommand, ...splitBeats(out ?? '') }
}
