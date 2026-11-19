// What the notes agents post on an issue say about its agent. Like board.ts, it imports no Claude Code module and no
// Node module, so the pane and the web server share it.

/** Whether an agent started on an issue after `at`, an ISO 8601 time, by the "An agent started" notes it posted. */
export function startedSince(comments: { body: string; createdAt: string }[], at: string): boolean {
  return comments.some(one => one.body.startsWith('An agent started') && Date.parse(one.createdAt) > Date.parse(at))
}

/** The command that resumes an issue's agent, from the newest "An agent started" note among its comments. */
export function resumeOf(comments: string[]): string | undefined {
  const found = comments.filter(one => one.includes('claude --resume')).at(-1)
    ?.match(/claude --resume ([0-9a-f-]+)`? in `([^`]+)`/)
  return found ? `cd ${found[2]} && claude --resume ${found[1]}` : undefined
}

/**
 * Whether an issue's newest agent note says its agent finished, as one does when it leaves its PR a draft, such as
 * when the test check fails for a reason outside the issue: then the issue waits on the person, not on a resume.
 */
export function finishedLast(comments: string[]): boolean {
  return comments.filter(one => /^(An agent started|The agent finished|The agent stopped)/.test(one)).at(-1)
    ?.startsWith('The agent finished') === true
}

/**
 * The session an issue's newest agent note says works it on a machine, from the note `agent-desk start` posts: its
 * process and the machine's name. Undefined when the newest note came from a runner, or says the agent finished or
 * stopped.
 */
export function localSessionOf(comments: string[]): { pid: number; host: string } | undefined {
  const found = comments.filter(one => /^(An agent started|The agent finished|The agent stopped)/.test(one)).at(-1)
    ?.match(/^An agent started on `[^`]+` in a local session \(pid (\d+) on `([^`]+)`\)/)
  return found ? { pid: Number(found[1]), host: found[2] ?? '' } : undefined
}

/**
 * Whether a local session still works its issue: the note names this machine, and its process, whose command line
 * `ps` gave as `args` (null once it's gone), is still Claude Code's rather than another program that took its pid.
 */
export function workedHere(session: { pid: number; host: string } | undefined, host: string | undefined,
  args: string | null): boolean {
  return session !== undefined && session.host === host && args !== null && /\bclaude\b/.test(args)
}
