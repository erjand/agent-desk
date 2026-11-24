// Run with: node --test web/
// Tests of the chat's server side, web/chat.ts, against a fake claude command that writes a transcript and streams a
// reply the way `claude -p --output-format stream-json --include-partial-messages` does.
// First, so the server's modules read the temporary folders it sets.
import './test-env.ts'
import assert from 'node:assert/strict'
import { execSync } from 'node:child_process'
import { chmodSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import type { Socket } from 'node:net'

import { applyEvent, chatFile, chatFolder, chatSnapshot, claudeCommand, clearChat, loadChat, messageOf, onChat,
  promptOf, sendChat, stopChat, summarize, transcriptFolder, transcriptItems } from './chat.ts'
import type { ChatContext, ChatItem, ChatPatch, StreamTurn } from './chat.ts'
import { JSON_POST, postTo } from './server-fixture.ts'
import { desks } from './state.ts'
import type { Desk } from './state.ts'
import { TEST_HOME } from './test-env.ts'

// The fake claude: it logs its arguments, its folder, and the ANTHROPIC_API_KEY it got, keeps the session's transcript
// where Claude Code does, and streams a reply in two deltas a moment apart, then a tool call, then the result, which
// reports the session's cost so far as FAKE_CLAUDE_COST, or $0.25.
// FAKE_CLAUDE_MODE `hang` stops after the first delta until it is killed, and `fail` prints to standard error and exits
// 1 without a result.
const FAKE = `#!${process.execPath}
const fs = require('node:fs')
const path = require('node:path')
const args = process.argv.slice(2)
fs.appendFileSync(process.env.FAKE_CLAUDE_LOG, JSON.stringify({ args, cwd: process.cwd(),
  apiKey: process.env.ANTHROPIC_API_KEY ?? null }) + '\\n')
if (process.env.FAKE_CLAUDE_MODE === 'fail') {
  process.stderr.write('No conversation found with that session ID')
  process.exit(1)
}
const value = name => args.includes(name) ? args[args.indexOf(name) + 1] : undefined
const session = value('--session-id') || value('--resume')
const prompt = args[args.indexOf('--') + 1]
const folder = path.join(process.env.CLAUDE_CONFIG_DIR, 'projects', process.cwd().replace(/[^a-zA-Z0-9]/g, '-'))
fs.mkdirSync(folder, { recursive: true })
const keep = entry => fs.appendFileSync(path.join(folder, session + '.jsonl'), JSON.stringify(entry) + '\\n')
const say = event => process.stdout.write(JSON.stringify({ session_id: session, ...event }) + '\\n')
const delta = text => say({ type: 'stream_event', event: { type: 'content_block_delta', index: 0,
  delta: { type: 'text_delta', text } } })
keep({ type: 'user', message: { role: 'user', content: prompt } })
say({ type: 'system', subtype: 'init' })
say({ type: 'stream_event', event: { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } } })
delta('Two issues ')
if (process.env.FAKE_CLAUDE_MODE === 'hang') {
  setInterval(() => {}, 60000)
} else {
  setTimeout(() => {
    delta('need you.')
    const tool = { type: 'tool_use', name: 'Bash', input: { command: 'agent-desk issue list\\n--json number' } }
    keep({ type: 'assistant', message: { content: [{ type: 'text', text: 'Two issues need you.' }, tool] } })
    say({ type: 'assistant', message: { content: [{ type: 'text', text: 'Two issues need you.' }] } })
    say({ type: 'assistant', message: { content: [tool] } })
    say({ type: 'result', subtype: 'success', is_error: false, result: 'Two issues need you.',
      total_cost_usd: Number(process.env.FAKE_CLAUDE_COST ?? '0.25') })
  }, 300)
}
`

const BIN = join(TEST_HOME, 'bin')
const LOG = join(TEST_HOME, 'claude.log')
mkdirSync(BIN, { recursive: true })
writeFileSync(join(BIN, 'claude'), FAKE)
chmodSync(join(BIN, 'claude'), 0o755)
process.env.AGENT_DESK_CLAUDE = join(BIN, 'claude')
process.env.CLAUDE_CONFIG_DIR = join(TEST_HOME, 'claude')
process.env.FAKE_CLAUDE_LOG = LOG
writeFileSync(LOG, '')

desks.set('yard', { id: 'yard', repo: 'me/yard', owner: 'me', title: 'Yard', main: '/srv/yard', chores: [],
  board: null, at: 0, refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {}, schedulePr: null,
  modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk)

const patches: ChatPatch[] = []
onChat(patch => patches.push(patch))

/** Each run of the fake claude: its arguments, the folder it ran in, and the ANTHROPIC_API_KEY it got. */
function runs(): { args: string[]; cwd: string; apiKey: string | null }[] {
  return readFileSync(LOG, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line))
}

async function until(check: () => boolean): Promise<void> {
  const end = Date.now() + 5_000
  while (!check()) {
    assert.ok(Date.now() < end, 'timed out waiting')
    await new Promise(resolve => setTimeout(resolve, 20))
  }
}

const idle = () => until(() => !chatSnapshot().running && patches.at(-1)?.running === false)
const issue: ChatContext = { desk: 'yard', repo: 'me/yard', kind: 'issue', number: 12 }
const review: ChatContext = { desk: 'yard', repo: 'me/yard', kind: 'review', number: 30 }
const reply = (text: string) => ({ role: 'claude', text, html: `<p>${text}</p>\n` })
const tool = { role: 'tool', text: '→ Bash: agent-desk issue list' }

test('a first message starts a session in the chat folder, keeps its id, and streams the reply as it comes', async () => {
  assert.equal(await sendChat('What needs me?', issue), null)
  await idle()
  const [run] = runs()
  assert.equal(run?.cwd, chatFolder())
  assert.ok(chatFolder().startsWith(join(TEST_HOME, 'state', 'agent-desk')))
  const session = run?.args[run.args.indexOf('--session-id') + 1]
  assert.match(session ?? '', /^[0-9a-f-]{36}$/)
  assert.deepEqual(JSON.parse(readFileSync(chatFile(), 'utf8')), { session })
  assert.ok(!run?.args.includes('--resume'))
  for (const flag of ['-p', '--include-partial-messages', '--verbose']) {
    assert.ok(run?.args.includes(flag), flag)
  }
  assert.deepEqual(['--output-format', '--permission-mode', '--disallowedTools'].map(flag =>
    run?.args[run.args.indexOf(flag) + 1]), ['stream-json', 'auto', 'ScheduleWakeup,CronCreate,Monitor'])
  // The system prompt names the desk's commands, its projects, and the skills the chat can run.
  const prompt = run?.args[run.args.indexOf('--append-system-prompt') + 1] ?? ''
  for (const part of ['issue view|list', 'dispatch.py runs', 'merges status', '/agent-desk:resource-report',
    '/agent-desk:create-issue', 'me/yard, titled Yard, checked out at /srv/yard', 'wait for the person\'s yes']) {
    assert.ok(prompt.includes(part), part)
  }
  assert.equal(run?.args.at(-1), promptOf('What needs me?', issue))
  // The page saw the message, then the reply's first delta while it was still coming, then the whole reply.
  assert.deepEqual(patches[0], { session, running: true, from: 0,
    items: [{ role: 'you', text: 'What needs me?', context: issue }] })
  assert.ok(patches.some(patch => patch.running && patch.items.at(-1)?.text === 'Two issues '))
  assert.deepEqual(chatSnapshot(), { session, running: false, from: 0, items: [
    { role: 'you', text: 'What needs me?', context: issue }, reply('Two issues need you.'), tool] })
})

test('the next message resumes the session, and a reload reads the conversation back from its transcript',
  async () => {
    const session = chatSnapshot().session
    assert.equal(await sendChat('And this one?', review), null)
    await idle()
    const run = runs().at(-1)
    assert.equal(run?.args[run.args.indexOf('--resume') + 1], session)
    assert.ok(!run?.args.includes('--session-id'))
    const before = chatSnapshot().items
    await loadChat()
    assert.deepEqual(chatSnapshot(), { session, running: false, from: 0, items: before })
    assert.deepEqual(before.filter(item => item.role === 'you'), [
      { role: 'you', text: 'What needs me?', context: issue }, { role: 'you', text: 'And this one?', context: review }])
  })

test('a message while a reply comes is refused, and Stop ends the reply with every process of its turn', async () => {
  process.env.FAKE_CLAUDE_MODE = 'hang'
  try {
    assert.equal(await sendChat('Why is the queue stuck?', null), null)
    await until(() => chatSnapshot().items.at(-1)?.text === 'Two issues ')
    assert.equal(chatSnapshot().running, true)
    assert.match(await sendChat('Hello?', null) ?? '', /still coming/)
    stopChat()
    await idle()
    assert.deepEqual(chatSnapshot().items.at(-1), { role: 'note', text: 'Stopped.' })
  } finally {
    delete process.env.FAKE_CLAUDE_MODE
  }
})

test('a turn whose claude fails shows what it said', async () => {
  process.env.FAKE_CLAUDE_MODE = 'fail'
  try {
    await sendChat('Still there?', null)
    await idle()
    assert.deepEqual(chatSnapshot().items.at(-1), { role: 'note', error: true,
      text: 'Claude Code ended with status 1: No conversation found with that session ID' })
  } finally {
    delete process.env.FAKE_CLAUDE_MODE
  }
})

test('a first message whose claude never starts leaves no session to resume', async () => {
  await clearChat()
  process.env.FAKE_CLAUDE_MODE = 'fail'
  try {
    await sendChat('Hello?', null)
    await idle()
    assert.equal(chatSnapshot().session, null)
    await until(() => readFileSync(chatFile(), 'utf8') === JSON.stringify({ session: null }))
  } finally {
    delete process.env.FAKE_CLAUDE_MODE
  }
  await sendChat('Hello again', null)
  await idle()
  assert.ok(runs().at(-1)?.args.includes('--session-id'))
})

test('each turn bills the account set as it starts, and never the server\'s ANTHROPIC_API_KEY', async () => {
  const hold = join(TEST_HOME, 'state', 'agent-desk', 'hold.json')
  const key = join(TEST_HOME, 'config', 'agent-desk', 'anthropic-api-key')
  const account = (name: string) => {
    mkdirSync(dirname(hold), { recursive: true })
    writeFileSync(hold, JSON.stringify({ state: 'running', account: name }))
  }
  process.env.ANTHROPIC_API_KEY = 'sk-ant-from-the-server'
  try {
    await clearChat()
    const before = runs().length
    account('api')
    await sendChat('On the key?', null)
    await idle()
    assert.equal(runs().length, before)
    const note = chatSnapshot().items.at(-1)
    assert.equal(note?.role, 'note')
    assert.match(note?.text ?? '', /^Claude Code didn't start: the account is api, but no API key is set/)
    assert.equal(chatSnapshot().session, null)
    mkdirSync(dirname(key), { recursive: true })
    writeFileSync(key, 'sk-ant-made-up-KEYS', { mode: 0o600 })
    await sendChat('On the key now?', null)
    await idle()
    const api = runs().at(-1)
    assert.equal(api?.apiKey, null)
    const settings = JSON.parse(api?.args[(api?.args.indexOf('--settings') ?? 0) + 1] ?? '{}')
    assert.equal(execSync(settings.apiKeyHelper, { encoding: 'utf8' }), 'sk-ant-made-up-KEYS')
    assert.ok(!JSON.stringify(api?.args).includes('sk-ant-made-up-KEYS'))
    account('subscription')
    await sendChat('On the plan?', null)
    await idle()
    const plan = runs().at(-1)
    assert.equal(plan?.apiKey, null)
    assert.ok(!plan?.args.includes('--settings'))
  } finally {
    delete process.env.ANTHROPIC_API_KEY
    rmSync(hold, { force: true })
    rmSync(key, { force: true })
  }
})

test('each turn records what it cost, and a turn while the API budget holds is refused, saying why', async () => {
  const hold = join(TEST_HOME, 'state', 'agent-desk', 'hold.json')
  const key = join(TEST_HOME, 'config', 'agent-desk', 'anthropic-api-key')
  const ledger = join(TEST_HOME, 'state', 'agent-desk', 'chat-spend.jsonl')
  let session: string | null = null
  // The turns of this test's session, since an earlier test's last turn may still be on its way to the ledger.
  const turns = (): { session: string; account: string; reported: number; cost: number }[] => {
    try {
      return readFileSync(ledger, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line))
        .filter(one => one.session === session)
    } catch {
      return []
    }
  }
  mkdirSync(dirname(hold), { recursive: true })
  mkdirSync(dirname(key), { recursive: true })
  writeFileSync(key, 'sk-ant-made-up-KEYS', { mode: 0o600 })
  writeFileSync(hold, JSON.stringify({ state: 'running', account: 'api', apiBudget: { on: true, dollars: 1 } }))
  try {
    await clearChat()
    process.env.FAKE_CLAUDE_COST = '0.6'
    await sendChat('What did that cost?', null)
    await idle()
    session = chatSnapshot().session
    await until(() => turns().length === 1)
    // Claude Code reports a resumed session's total so far, so the second turn cost 0.90 of its 1.50.
    process.env.FAKE_CLAUDE_COST = '1.5'
    await sendChat('And now?', null)
    await idle()
    await until(() => turns().length === 2)
    assert.deepEqual(turns().map(one => [one.session, one.account, one.reported, Math.round(one.cost * 100) / 100]),
      [[session, 'api', 0.6, 0.6], [session, 'api', 1.5, 0.9]])
    const before = runs().length
    await sendChat('One more?', null)
    await idle()
    assert.equal(runs().length, before)
    const note = chatSnapshot().items.at(-1)
    assert.equal(note?.role, 'note')
    assert.match(note?.text ?? '',
      /^Claude Code didn't start: the API budget holds new turns: \$[0-9.]+ of \$1\.00 this month;/)
  } finally {
    delete process.env.FAKE_CLAUDE_COST
    rmSync(hold, { force: true })
    rmSync(key, { force: true })
    rmSync(ledger, { force: true })
  }
})

test('Clear Chat forgets the session, and the next message starts a new one', async () => {
  const old = chatSnapshot().session
  await clearChat()
  assert.deepEqual(patches.at(-1), { session: null, running: false, from: 0, items: [] })
  assert.deepEqual(JSON.parse(readFileSync(chatFile(), 'utf8')), { session: null })
  await loadChat()
  assert.deepEqual(chatSnapshot().items, [])
  await sendChat('Start over', null)
  await idle()
  const run = runs().at(-1)
  const session = run?.args[run.args.indexOf('--session-id') + 1]
  assert.ok(session && session !== old)
  assert.equal(chatSnapshot().session, session)
})

test('the chat\'s routes take a message only with a served project\'s item or none, and only from the user', async () => {
  await clearChat()
  const bad = [{ text: '' }, { text: 'Hi', context: { desk: 'nope', kind: 'issue', number: 1 } },
    { text: 'Hi', context: { desk: 'yard', kind: 'pr', number: 1 } },
    { text: 'Hi', context: { desk: 'yard', kind: 'issue', number: 0 } }, { text: 'x'.repeat(70_000) }]
  for (const sent of bad) {
    assert.equal((await postTo('/api/chat', sent)).status, 400, JSON.stringify(sent).slice(0, 80))
  }
  const other = { remoteAddress: '127.0.0.1', remotePort: 1, localPort: 2 } as Socket
  for (const path of ['/api/chat', '/api/chat-stop', '/api/chat-clear']) {
    assert.deepEqual(await postTo(path, { text: 'Hi' }, JSON_POST, other), { status: 403,
      data: { error: 'another user' } })
  }
  assert.equal(chatSnapshot().items.length, 0)
  const sent = await postTo('/api/chat', { text: ' Hi ', context: { desk: 'yard', kind: 'issue', number: 12,
    review: 30 } })
  assert.equal(sent.status, 202)
  assert.deepEqual(chatSnapshot().items[0], { role: 'you', text: 'Hi',
    context: { desk: 'yard', repo: 'me/yard', kind: 'issue', number: 12, review: 30 } })
  assert.equal((await postTo('/api/chat', { text: 'Again' })).status, 409)
  await idle()
  assert.equal((await postTo('/api/chat-clear', {})).status, 200)
  assert.equal(chatSnapshot().session, null)
})

test('a message keeps what the person had selected in a tag the transcript reads back', () => {
  assert.deepEqual(messageOf(promptOf('What now?', issue)), { text: 'What now?', context: issue })
  assert.deepEqual(messageOf(promptOf('What now?', null)), { text: 'What now?', context: null })
  assert.deepEqual(messageOf('No tag'), { text: 'No tag', context: null })
})

test('a transcript reads back as the chat showed it, leaving out skills\' text, tool results, and subagents', () => {
  const lines = [
    { type: 'user', message: { content: `<command-message>agent-desk:resource-report</command-message>
<command-name>/agent-desk:resource-report</command-name>
<command-args>${promptOf('', issue).trim()}</command-args>` } },
    { type: 'user', isMeta: true, message: { content: [{ type: 'text', text: 'The skill\'s instructions' }] } },
    { type: 'assistant', message: { content: [{ type: 'thinking', thinking: 'hmm' },
      { type: 'tool_use', name: 'Read', input: { file_path: '/proc/meminfo' } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', content: 'MemTotal' }] } },
    { type: 'assistant', isSidechain: true, message: { content: [{ type: 'text', text: 'A subagent' }] } },
    { type: 'user', isCompactSummary: true, message: { content: 'The summary' } },
    { type: 'assistant', message: { content: [{ type: 'text', text: 'See #12.' }] } },
    { type: 'user', message: { content: [{ type: 'text', text: '[Request interrupted by user]' }] } },
  ].map(line => JSON.stringify(line)).join('\n')
  assert.deepEqual(transcriptItems(`${lines}\nnot json\n`), [
    { role: 'you', text: '/agent-desk:resource-report', context: issue },
    { role: 'tool', text: '→ Read: /proc/meminfo' },
    { role: 'claude', text: 'See #12.', html: '<p>See <a class="issue-link" href="#yard:12">#12</a>.</p>\n' },
    { role: 'note', text: 'Stopped.' },
  ])
})

test('a stream without partial messages still shows the reply\'s text, and a failed result its error', () => {
  const items: ChatItem[] = []
  const turn: StreamTurn = { streamed: 0, desk: 'yard', result: false, session: null }
  assert.equal(applyEvent(items, turn, { type: 'assistant', message: { content: [{ type: 'text', text: 'Hi.' }] } }),
    0)
  assert.equal(applyEvent(items, turn, { type: 'system', subtype: 'init', session_id: 'abc' }), null)
  assert.equal(turn.session, 'abc')
  assert.equal(applyEvent(items, turn, { type: 'result', is_error: true, subtype: 'error_max_turns' }), 1)
  assert.deepEqual(items, [reply('Hi.'), { role: 'note', text: 'Claude Code: the reply ended with error_max_turns',
    error: true }])
  assert.equal(turn.result, true)
})

// The cases of tests/test_agent_log.py's SummarizeTests, so the chat's tool lines read as the Agent Log's.
test('a tool call\'s line names its most telling input\'s first line, cut short', () => {
  assert.equal(summarize({ command: 'git add -A\ngit commit' }), 'git add -A')
  assert.equal(summarize({ description: 'List' }), 'List')
  assert.equal(summarize({ todos: [] }), '')
  const long = summarize({ command: 'x'.repeat(500) })
  assert.equal(long.length, 160)
  assert.ok(long.endsWith('…'))
})

test('a command with a description shows the description, then its first line, cut whole', () => {
  assert.equal(summarize({ command: 'cargo test -p gpu --lib\necho done', description: 'Run the stuck-device test' }),
    'Run the stuck-device test · cargo test -p gpu --lib')
  const long = summarize({ command: 'x'.repeat(500), description: 'Build' })
  assert.equal(long.length, 160)
  assert.ok(long.startsWith('Build · xxx'))
  assert.ok(long.endsWith('…'))
})

test('a command opening with a heredoc shows the first program after it', () => {
  for (const [command, shown] of [
    ['python3 - <<\'EOF\'\nimport os\nEOF\ncargo test -p gpu --lib', 'cargo test -p gpu --lib'],
    ['git commit -m "$(cat <<\'EOF\'\nFix it\nEOF\n)" && git push', 'git push'],
    ['cat <<-END >notes\n\tline\n\tEND\n\n# then build\nmake', 'make'],
    ['paste <<A <<"B"\n1\nA\n2\nB\njust test', 'just test'],
  ] as const) {
    assert.equal(summarize({ command }), shown, command)
  }
})

test('a heredoc with no program after it, a described heredoc, and a here-string show the first line', () => {
  assert.equal(summarize({ command: 'git commit -m "$(cat <<\'EOF\'\nFix it\nEOF\n)"' }),
    'git commit -m "$(cat <<\'EOF\'')
  assert.equal(summarize({ command: 'python3 - <<\'EOF\'\nx\nEOF\ncargo test', description: 'Probe the device' }),
    'Probe the device · python3 - <<\'EOF\'')
  assert.equal(summarize({ command: 'grep x <<< "$text"\nls' }), 'grep x <<< "$text"')
})

test('the claude command is AGENT_DESK_CLAUDE, then claude on PATH, then the native installer\'s', () => {
  assert.equal(claudeCommand({ AGENT_DESK_CLAUDE: '/opt/claude' }), '/opt/claude')
  assert.equal(claudeCommand({ PATH: `/nowhere:${BIN}` }), join(BIN, 'claude'))
  assert.match(claudeCommand({ PATH: '/nowhere' }), /\/\.local\/bin\/claude$/)
  assert.match(transcriptFolder('/a/b.c'), /\/projects\/-a-b-c$/)
})
