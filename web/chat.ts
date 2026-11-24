// The web page's chat with a Claude Code session about the desk. Each message the person sends runs one turn of the
// session with `claude -p` in its own folder under the desk's state folder, never in a project's checkout, and the
// reply's events stream into the chat as they arrive. The session's id stays in chat.json beside that folder, so a page
// that reloads, or a server that restarts, reads the conversation back from the session's transcript, and the next
// message continues the session with --resume. Clear Chat forgets the id, and the next message starts a new session.
// The turn runs straight from the server, outside the dispatcher, so the memory gate never holds it. Each turn bills
// the account lib/account.py sets as it starts, as a run does, records what it cost in lib/budget.py's ledger of Chat
// turns, and never starts while the API budget holds new runs.
import { execFile, spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { accessSync, constants } from 'node:fs'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { delimiter, dirname, join } from 'node:path'

import { renderMarkdown } from './markdown.ts'
import { childEnv, DESK_HOME, desks } from './state.ts'

/** What the person had selected on the page as they sent a message: a project's issue or review. */
export type ChatContext = { desk: string; repo: string; kind: 'issue' | 'review'; number: number; review?: number }

/**
 * One line of the chat: the person's message with what they were looking at, a stretch of Claude's reply with its
 * HTML, a tool call summarized as the Agent Log summarizes it, or a note such as a stopped reply or an error.
 */
export type ChatItem =
  | { role: 'you'; text: string; context: ChatContext | null }
  | { role: 'claude'; text: string; html: string }
  | { role: 'tool'; text: string }
  | { role: 'note'; text: string; error?: boolean }

/**
 * A change to the chat for the page: the session, whether a reply is coming, and the items from index `from` on,
 * which replace the page's items from there.
 */
export type ChatPatch = { session: string | null; running: boolean; from: number; items: ChatItem[] }

// The longest message the chat takes, as long as a comment.
export const MAX_MESSAGE = 65_536
// How often the page hears of a reply's progress while it streams.
const FLUSH_MS = 100
// How much of a failed turn's standard error the chat shows.
const STDERR_TAIL = 2_000
// The tools a turn may not use, as the desk's runs forbid them: each would keep `claude -p` alive after the reply.
const DISALLOWED = 'ScheduleWakeup,CronCreate,Monitor'
// The input field that best names what a tool call does, in the order lib/agent_log.py tries them, and the longest
// summary it keeps.
const SUMMARY_FIELDS = ['command', 'file_path', 'pattern', 'skill', 'url', 'query', 'description', 'prompt']
const MAX_SUMMARY = 160
// A heredoc's start, `<<WORD`, `<<-'WORD'`, or `<<"WORD"`, which a here-string's `<<<` is not, as lib/agent_log.py has
// it.
const HEREDOC = /(?<!<)<<(?!<)(-?)\s*(?:'([^']+)'|"([^"]+)"|\\?([\w.-]+))/g
// What a line after a heredoc may start with before its program: the `)"` that closes a `$(cat <<EOF` and the `&&`,
// `||`, `;`, or `|` that chains the next command.
const CLOSER = /^\s*[)}][)}"'`\s]*(?:&&|\|\||[;|&])?\s*/
// The tag each message ends with, naming what the person had selected, which the transcript keeps.
const CONTEXT_TAG = /\s*<desk-context>([\s\S]*?)<\/desk-context>\s*$/

function stateFolder(): string {
  return join(process.env.XDG_STATE_HOME ?? join(homedir(), '.local', 'state'), 'agent-desk')
}

/** The folder the chat's session runs in, under the desk's state folder. */
export function chatFolder(): string {
  return join(stateFolder(), 'chat')
}

/** The file that keeps the chat's session id. */
export function chatFile(): string {
  return join(stateFolder(), 'chat.json')
}

/**
 * Claude Code's folder for the transcripts of sessions run in `cwd`, under `projects`: the path with every other
 * character a dash.
 */
export function transcriptFolder(cwd: string,
  projects = join(process.env.CLAUDE_CONFIG_DIR || join(homedir(), '.claude'), 'projects')): string {
  return join(projects, cwd.replace(/[^a-zA-Z0-9]/g, '-'))
}

/**
 * The claude command a turn runs: AGENT_DESK_CLAUDE, or claude on PATH, or else the native installer's
 * ~/.local/bin/claude, which the web page's systemd service doesn't have on its PATH.
 */
export function claudeCommand(env: NodeJS.ProcessEnv = process.env): string {
  if (env.AGENT_DESK_CLAUDE) {
    return env.AGENT_DESK_CLAUDE
  }
  for (const dir of (env.PATH ?? '').split(delimiter).filter(Boolean)) {
    try {
      accessSync(join(dir, 'claude'), constants.X_OK)
      return join(dir, 'claude')
    } catch {
      // Not in this folder.
    }
  }
  return join(homedir(), '.local', 'bin', 'claude')
}

/**
 * The account a turn bills, as lib/account.py's `chat` verb reads the setting, with the flags that bill it: none on
 * `subscription`, and on `api` the --settings whose apiKeyHelper reads the key's file. Says why instead, without the
 * key, when the account is `api` and no key is set, when the API budget holds new runs, or when the script fails.
 */
export function accountFlags(env: NodeJS.ProcessEnv): Promise<{ account: string; args: string[] } | { error: string }> {
  return new Promise(resolve => {
    execFile('python3', ['-B', join(DESK_HOME, 'lib', 'account.py'), 'chat'], { env, timeout: 30_000 },
      (error, stdout, stderr) => {
        const [account = '', settings = ''] = stdout.split('\n')
        if (error || !account) {
          resolve({ error: stderr.trim().replace(/^agent-desk: /, '') || `lib/account.py failed: ${error?.message}` })
        } else {
          resolve({ account, args: settings ? ['--settings', settings] : [] })
        }
      })
  })
}

/**
 * Records a turn in lib/budget.py's ledger of Chat turns: its session, the account it billed, and `cost`, the
 * total_cost_usd its `result` event reported, which counts the session so far when the turn resumed it. Every turn
 * goes in, on either account, so the ledger knows each session's total before the turn. A failure leaves the turn out
 * of the month's API spend and says so in the server's log.
 */
export function recordCost(session: string, account: string, cost: number, env: NodeJS.ProcessEnv): Promise<void> {
  return new Promise(resolve => {
    execFile('python3', ['-B', join(DESK_HOME, 'lib', 'budget.py'), 'record', session, account, String(cost)],
      { env, timeout: 30_000 }, error => {
        if (error) {
          console.error(`agent-desk: a Chat turn's cost of ${cost} dollars went unrecorded: ${error.message}`)
        }
        resolve()
      })
  })
}

function clip(line: string): string {
  return line.length <= MAX_SUMMARY ? line : `${line.slice(0, MAX_SUMMARY - 1)}…`
}

function firstLine(text: string): string {
  return text.trim().split(/\r\n|\r|\n/)[0] ?? ''
}

/**
 * The line of a command that best names what it runs, as lib/agent_log.py's command_line picks it: its first, unless
 * that line opens a heredoc, as `python3 - <<'EOF'` does. Then it is the first line after the heredoc's end that runs a
 * program, past a `)"` that closes the heredoc's `$(...)` and a `&&` that chains it, or the first line when no such
 * line follows.
 */
export function commandLine(command: string): string {
  const [first = '', ...rest] = command.trim().split(/\r\n|\r|\n/)
  const ends = [...first.matchAll(HEREDOC)]
    .map(found => ({ dash: found[1] !== '', word: found[2] || found[3] || found[4] }))
  if (ends.length === 0) {
    return first
  }
  for (const line of rest) {
    const end = ends[0]
    if (end) {
      if ((end.dash ? line.replace(/^\t+/, '') : line).trim() === end.word) {
        ends.shift()
      }
      continue
    }
    const program = line.replace(CLOSER, '').trim()
    if (program && !program.startsWith('#')) {
      return program
    }
  }
  return first
}

/**
 * What a tool call does, on one line cut to MAX_SUMMARY characters, as the Agent Log has it. A command shows its
 * description, when it has one, and its first line, or else the line commandLine picks. Any other call shows the first
 * line of its most telling input field.
 */
export function summarize(input: unknown): string {
  const fields = typeof input === 'object' && input !== null ? input as Record<string, unknown> : {}
  const { command, description } = fields
  if (typeof command === 'string' && command.trim()) {
    if (typeof description === 'string' && description.trim()) {
      return clip(`${firstLine(description)} · ${firstLine(command)}`)
    }
    return clip(commandLine(command))
  }
  for (const field of SUMMARY_FIELDS) {
    const value = fields[field]
    if (typeof value === 'string' && value.trim()) {
      return clip(firstLine(value))
    }
  }
  return ''
}

/** A tool call's line in the chat: its name and summary after an arrow. */
function toolLine(block: { name?: unknown; input?: unknown }): string {
  const summary = summarize(block.input)
  return `→ ${String(block.name ?? 'tool')}${summary ? `: ${summary}` : ''}`
}

/** A message as a turn sends it: the person's text, then the tag that names what they had selected. */
export function promptOf(text: string, context: ChatContext | null): string {
  return `${text}\n\n<desk-context>${context === null ? 'none' : JSON.stringify(context)}</desk-context>`
}

/** A message as the transcript keeps it, split back into the person's text and what they had selected. */
export function messageOf(prompt: string): { text: string; context: ChatContext | null } {
  const found = CONTEXT_TAG.exec(prompt)
  if (!found) {
    return { text: prompt, context: null }
  }
  let context: ChatContext | null = null
  try {
    const parsed: unknown = JSON.parse(found[1] ?? '')
    context = typeof parsed === 'object' && parsed !== null && 'number' in parsed ? parsed as ChatContext : null
  } catch {
    // `none`, or a tag the person typed themselves.
  }
  return { text: prompt.slice(0, found.index), context }
}

/**
 * A message the page sent's context, checked: a served project and an issue or review number in it, or null for none.
 * Anything else is undefined, which the server refuses.
 */
export function contextOf(sent: unknown): ChatContext | null | undefined {
  if (sent === null || sent === undefined) {
    return null
  }
  if (typeof sent !== 'object') {
    return undefined
  }
  const { desk, kind, number, review } = sent as Record<string, unknown>
  const served = desks.get(String(desk))
  const whole = (value: unknown) => Number.isInteger(value) && Number(value) > 0
  if (!served || (kind !== 'issue' && kind !== 'review') || !whole(number)
    || (review !== undefined && !whole(review))) {
    return undefined
  }
  return { desk: served.id, repo: served.repo, kind, number: Number(number),
    ...review === undefined ? {} : { review: Number(review) } }
}

/** A stretch of Claude's reply, with its Markdown as HTML that links `#123` to the item in `desk`'s project. */
function claudeItem(text: string, desk: string | undefined): ChatItem {
  return { role: 'claude', text, html: renderMarkdown(text, number => (desk ? `#${desk}:${number}` : '')) }
}

/** The text of a user entry's content, which is a string or a list of blocks. */
function contentText(content: unknown): string | null {
  if (typeof content === 'string') {
    return content
  }
  if (!Array.isArray(content) || content.some(block => block?.type === 'tool_result')) {
    return null
  }
  return content.filter(block => block?.type === 'text').map(block => String(block.text ?? '')).join('\n')
}

/**
 * A message as the person typed it, from a user entry: a slash command, which Claude Code keeps as tags, reads back as
 * the command and its arguments.
 */
function typedOf(text: string): string {
  const name = /<command-name>([\s\S]*?)<\/command-name>/.exec(text)
  if (!name) {
    return text
  }
  const args = /<command-args>([\s\S]*?)<\/command-args>/.exec(text)?.[1] ?? ''
  return `${name[1]?.trim()}${args ? ` ${args}` : ''}`
}

/**
 * The chat's items from a session's transcript: each message the person sent, with what they had selected, and each
 * stretch of Claude's reply and tool call. A skill's instructions, a tool's result, a compacted summary, and a
 * subagent's work are left out, as the chat showed none of them.
 */
export function transcriptItems(text: string): ChatItem[] {
  const items: ChatItem[] = []
  let desk: string | undefined
  for (const line of text.split('\n')) {
    let entry: Record<string, unknown>
    try {
      entry = JSON.parse(line)
    } catch {
      continue
    }
    if (typeof entry !== 'object' || entry === null || entry.isSidechain || entry.isMeta || entry.isCompactSummary) {
      continue
    }
    const message = entry.message as { content?: unknown } | undefined
    if (entry.type === 'user') {
      const said = contentText(message?.content)
      if (said === null || said.trim() === '' || said.includes('<local-command-')) {
        continue
      }
      if (said.startsWith('[Request interrupted')) {
        items.push({ role: 'note', text: 'Stopped.' })
        continue
      }
      const { text: typed, context } = messageOf(typedOf(said))
      desk = context?.desk ?? desk
      items.push({ role: 'you', text: typed, context })
    } else if (entry.type === 'assistant' && Array.isArray(message?.content)) {
      for (const block of message.content as { type?: string; text?: string; name?: unknown; input?: unknown }[]) {
        if (block.type === 'text' && block.text?.trim()) {
          items.push(claudeItem(block.text.trim(), desk))
        } else if (block.type === 'tool_use') {
          items.push({ role: 'tool', text: toolLine(block) })
        }
      }
    }
  }
  return items
}

/** A turn's reading of its stream: the text blocks streamed that no whole assistant event has carried yet. */
export type StreamTurn = { streamed: number; desk: string | undefined; result: boolean; session: string | null }

/**
 * Applies one stream-json event of a turn to the chat's items, returning the index of the first item it changed, or
 * null for none. A text block's deltas grow a Claude item as they come; the whole assistant event that follows each
 * block adds what no delta carried, such as a tool call's line, or text from a stream without partial messages. A
 * result that failed adds a note with its error.
 */
export function applyEvent(items: ChatItem[], turn: StreamTurn, event: Record<string, unknown>): number | null {
  if (typeof event.session_id === 'string') {
    turn.session = event.session_id
  }
  if (event.type === 'stream_event') {
    const inner = (event.event ?? {}) as { type?: string; content_block?: { type?: string };
      delta?: { type?: string; text?: string } }
    if (inner.type === 'content_block_start' && inner.content_block?.type === 'text') {
      turn.streamed++
      items.push(claudeItem('', turn.desk))
      return items.length - 1
    }
    if (inner.type === 'content_block_delta' && inner.delta?.type === 'text_delta' && inner.delta.text) {
      const last = items.at(-1)
      if (last?.role !== 'claude') {
        turn.streamed++
        items.push(claudeItem(inner.delta.text, turn.desk))
      } else {
        items[items.length - 1] = claudeItem(last.text + inner.delta.text, turn.desk)
      }
      return items.length - 1
    }
    return null
  }
  if (event.type === 'assistant') {
    const blocks = ((event.message ?? {}) as { content?: unknown }).content
    const from = items.length
    for (const block of Array.isArray(blocks) ? blocks : []) {
      if (block?.type === 'text') {
        if (turn.streamed > 0) {
          turn.streamed--
        } else if (String(block.text ?? '').trim()) {
          items.push(claudeItem(String(block.text).trim(), turn.desk))
        }
      } else if (block?.type === 'tool_use') {
        items.push({ role: 'tool', text: toolLine(block) })
      }
    }
    return items.length > from ? from : null
  }
  if (event.type === 'result') {
    turn.result = true
    if (event.is_error) {
      const said = typeof event.result === 'string' && event.result.trim() ? event.result.trim()
        : `the reply ended with ${String(event.subtype ?? 'an error')}`
      items.push({ role: 'note', text: `Claude Code: ${said}`, error: true })
      return items.length - 1
    }
  }
  return null
}

/** The chat's system prompt for a turn: what the desk is, how to read it, and what the chat may change. */
function systemPrompt(): string {
  const projects = [...desks.values()].map(desk => `- ${desk.repo}, titled ${desk.title}, checked out at ${desk.main}`)
  const config = join(process.env.XDG_CONFIG_HOME ?? join(homedir(), '.config'), 'agent-desk', 'projects')
  const desk = join(DESK_HOME, 'bin', 'agent-desk')
  return `You are the chat on the agent-desk web page, talking with the person who runs this machine's desk.
agent-desk runs headless Claude Code agents on the issues of the projects below, with a local store of issues and
reviews, a dispatcher that starts runs as systemd units, and a merge queue that merges reviews in local git. Its
reference, ${join(DESK_HOME, 'docs', 'reference.md')}, says how every part works.

The projects, from ${config}, one per line as owner/repo and main checkout:
${projects.join('\n') || '- none yet'}

Each message ends with a <desk-context> tag: what the person has selected on the page, as JSON naming the project
(\`desk\` and \`repo\`), the kind, issue or review, its number, and the issue's open review, or \`none\`. "This" means
that item. Read the desk's live state for every answer, since it changes from minute to minute:

- \`${desk} issue view|list\` and \`${desk} review view|list|checks\` with \`-R <owner/repo>\` take the flags
  \`gh issue\` and \`gh pr\` take. Issues and reviews live in agent-desk's store, so never use \`gh\` for them.
- \`${desk} board --json\`, run in a project's main checkout, prints everything the page's board shows.
- \`python3 ${join(DESK_HOME, 'lib', 'dispatch.py')} runs -R <owner/repo>\` prints the runs the dispatcher holds,
  waiting and working.
- \`${desk} merges status\`, in a project's main checkout, prints the merge queue and why it stopped.
- \`${desk} --help\` lists every other command, such as \`queue order\`, \`chores status\`, \`fixes status\`, and
  \`pushes status\`.
- The skill /agent-desk:resource-report reports what uses the machine's memory and CPU and what could be freed, and
  /agent-desk:create-issue files an issue.

You may change what the person asks you to: labels, comments, closing issues, cancelling runs, queueing merges, and
freeing memory or files. Before anything you can't undo, such as deleting files, stopping processes, merging, or
closing a review, say exactly what you will do and wait for the person's yes in their next message. Never edit,
commit, or reset in a project's checkout or worktree: for a change to code, file an issue with
/agent-desk:create-issue. Never add the \`agent-ready\` label or write \`@agent\` unless the person asks you to start
an agent, since either starts a run. End each comment you post with a line \`<sub>Posted by agent-desk chat</sub>\`
and, under it, \`<!-- agent-desk -->\`.

Answer briefly, in Markdown, and name issues and reviews as #<number>.`
}

const chat: { session: string | null; items: ChatItem[]; child: ChildProcess | null; stopped: boolean;
  dirtyFrom: number; timer: NodeJS.Timeout | null } = { session: null, items: [], child: null, stopped: false,
  dirtyFrom: Infinity, timer: null }
let listener: (patch: ChatPatch) => void = () => undefined

/** Hands the chat the function that sends each change to the open pages. */
export function onChat(send: (patch: ChatPatch) => void): void {
  listener = send
}

/** The whole chat, as a page that connects gets it. */
export function chatSnapshot(): ChatPatch {
  return { session: chat.session, running: chat.child !== null, from: 0, items: chat.items }
}

/** Sends the items changed since the last send: at once, or with `soon`, as a stream's deltas ask, within FLUSH_MS. */
function changed(from: number, soon = false): void {
  chat.dirtyFrom = Math.min(chat.dirtyFrom, from)
  if (soon) {
    chat.timer ??= setTimeout(flush, FLUSH_MS)
    return
  }
  flush()
}

function flush(): void {
  if (chat.timer !== null) {
    clearTimeout(chat.timer)
    chat.timer = null
  }
  const from = Math.min(chat.dirtyFrom, chat.items.length)
  chat.dirtyFrom = Infinity
  listener({ session: chat.session, running: chat.child !== null, from, items: chat.items.slice(from) })
}

async function saveSession(session: string | null): Promise<void> {
  chat.session = session
  await mkdir(dirname(chatFile()), { recursive: true })
  await writeFile(chatFile(), JSON.stringify({ session }))
}

/** Reads the chat's session id and its conversation so far from the session's transcript, as the server starts. */
export async function loadChat(): Promise<void> {
  let session: unknown = null
  try {
    session = JSON.parse(await readFile(chatFile(), 'utf8')).session
  } catch {
    // No chat yet.
  }
  chat.session = typeof session === 'string' && /^[0-9a-f-]{36}$/.test(session) ? session : null
  const transcript = chat.session === null ? ''
    : await readFile(join(transcriptFolder(chatFolder()), `${chat.session}.jsonl`), 'utf8').catch(() => '')
  chat.items = transcriptItems(transcript)
}

/**
 * Starts a turn: adds the person's message to the chat and runs `claude -p` on it, continuing the session or starting
 * one, and streams the reply's events into the chat. Returns an error while a reply is still coming.
 */
export async function sendChat(text: string, context: ChatContext | null): Promise<string | null> {
  if (chat.child !== null) {
    return 'a reply is still coming; wait for it or press Stop'
  }
  const resume = chat.session
  const session = resume ?? randomUUID()
  if (resume === null) {
    await saveSession(session)
  }
  await mkdir(chatFolder(), { recursive: true })
  const env = childEnv()
  const claude = claudeCommand(env)
  // The turn's tools find agent-desk and claude even where the server's PATH lacks them, as in its systemd service.
  env.PATH = [join(DESK_HOME, 'bin'), dirname(claude), env.PATH].filter(Boolean).join(delimiter)
  delete env.AGENT_DESK_UNIT
  // Claude Code bills ANTHROPIC_API_KEY ahead of either account, so the turn never has it.
  delete env.ANTHROPIC_API_KEY
  const billed = await accountFlags(env)
  if ('error' in billed) {
    if (resume === null) {
      await saveSession(null)
    }
    chat.items.push({ role: 'you', text, context },
      { role: 'note', text: `Claude Code didn't start: ${billed.error}`, error: true })
    changed(chat.items.length - 2)
    return null
  }
  const argv = ['-p', resume === null ? '--session-id' : '--resume', session, '--output-format', 'stream-json',
    '--verbose', '--include-partial-messages', '--permission-mode', 'auto', '--append-system-prompt', systemPrompt(),
    '--disallowedTools', DISALLOWED, ...billed.args, '--', promptOf(text, context)]
  const child = spawn(claude, argv, { cwd: chatFolder(), env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  chat.child = child
  chat.stopped = false
  chat.items.push({ role: 'you', text, context })
  changed(chat.items.length - 1)
  const turn: StreamTurn = { streamed: 0, desk: context?.desk ?? [...desks.keys()][0], result: false, session: null }
  let pending = ''
  let stderr = ''
  child.stdout?.setEncoding('utf8')
  child.stdout?.on('data', (chunk: string) => {
    pending += chunk
    const lines = pending.split('\n')
    pending = lines.pop() ?? ''
    for (const line of lines) {
      let event: unknown
      try {
        event = JSON.parse(line)
      } catch {
        continue
      }
      if (typeof event === 'object' && event !== null) {
        const { type, total_cost_usd: cost } = event as { type?: unknown; total_cost_usd?: unknown }
        if (type === 'result' && typeof cost === 'number' && Number.isFinite(cost) && cost >= 0) {
          void recordCost(turn.session ?? session, billed.account, cost, env)
        }
        const from = applyEvent(chat.items, turn, event as Record<string, unknown>)
        if (from !== null) {
          changed(from, true)
        }
      }
    }
  })
  child.stderr?.setEncoding('utf8')
  child.stderr?.on('data', (chunk: string) => {
    stderr = (stderr + chunk).slice(-STDERR_TAIL)
  })
  const ended = (code: number | null, problem?: Error) => {
    if (chat.child !== child) {
      return
    }
    chat.child = null
    const from = chat.items.length
    if (chat.stopped) {
      chat.items.push({ role: 'note', text: 'Stopped.' })
    } else if (problem) {
      chat.items.push({ role: 'note', text: `Claude Code didn't start: ${problem.message}`, error: true })
    } else if (!turn.result) {
      chat.items.push({ role: 'note', text: `Claude Code ended with status ${code ?? 'unknown'}${stderr.trim()
        ? `: ${stderr.trim()}` : ''}`, error: true })
    }
    // A new session that Claude Code never started has no transcript to resume, so the next message starts one
    // again; and Claude Code that resumed a session into a new id keeps the conversation under that one.
    if (chat.session === session && resume === null && turn.session === null) {
      void saveSession(null)
    } else if (chat.session === session && turn.session !== null && turn.session !== session) {
      void saveSession(turn.session)
    }
    changed(from)
  }
  child.on('error', problem => ended(null, problem))
  child.on('close', code => ended(code))
  return null
}

/** Ends the reply under way, with every process its turn started. */
export function stopChat(): void {
  const child = chat.child
  if (child === null || child.pid === undefined) {
    return
  }
  chat.stopped = true
  try {
    process.kill(-child.pid, 'SIGTERM')
  } catch {
    child.kill('SIGTERM')
  }
}

/** Ends any reply under way and forgets the session, so the next message starts a new one. */
export async function clearChat(): Promise<void> {
  const child = chat.child
  if (child !== null) {
    stopChat()
    chat.child = null
  }
  chat.items = []
  await saveSession(null)
  changed(0)
}
