// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { applyChat, Chat, chatContext, chatRows, ChatRow } from './app-chat.js'
import { toggleSide, ui } from './app-state.js'

const fresh = () => ({ items: [], session: null, running: false, draft: '', sending: false, confirmClear: false,
  stick: true, focus: false })

afterEach(() => {
  ui.server = null
  ui.chat = fresh()
  ui.side = null
})

// The text a drawn element holds, in order, with a space around each element's, since htm drops the space between
// tags.
type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]
function textOf(node: Drawn): string {
  if (node === null || node === undefined || typeof node === 'boolean') {
    return ''
  }
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }
  if (Array.isArray(node)) {
    return node.map(textOf).join('')
  }
  if (typeof node.type === 'function') {
    return textOf((node.type as (props: unknown) => Drawn)(node.props))
  }
  return ` ${textOf(node.props?.children as Drawn)} `
}
const squash = (text: string) => text.replace(/\s+/g, ' ').trim()

// The elements a drawn tree holds, with each component drawn, in order.
type Element = { type: string; props: Record<string, unknown> }
function elementsOf(node: Drawn): Element[] {
  if (node === null || node === undefined || typeof node !== 'object') {
    return []
  }
  if (Array.isArray(node)) {
    return node.flatMap(elementsOf)
  }
  if (typeof node.type === 'function') {
    return elementsOf((node.type as (props: unknown) => Drawn)(node.props))
  }
  return [node as Element, ...elementsOf(node.props?.children as Drawn)]
}
const buttons = (drawn: Drawn) => elementsOf(drawn).filter(one => one.type === 'button')
  .map(one => ({ text: squash(textOf(one.props.children as Drawn)), disabled: Boolean(one.props.disabled) }))
const classed = (drawn: Drawn, name: string) => elementsOf(drawn)
  .filter(one => String(one.props.class ?? '').split(' ').includes(name))

const desks = [{ id: 'yard', title: 'Yard', board: null, cards: [] }]
const issue = { desk: 'yard', repo: 'me/yard', kind: 'issue', number: 12 }
const review = { desk: 'yard', repo: 'me/yard', kind: 'review', number: 30 }

test('a card tells the chat its issue, with the issue\'s review, or the review it shows alone, and a chore none', () => {
  assert.deepEqual(chatContext({ desk: 'yard', number: 12 }), { desk: 'yard', kind: 'issue', number: 12 })
  assert.deepEqual(chatContext({ desk: 'yard', number: 12, pr: 30 }),
    { desk: 'yard', kind: 'issue', number: 12, review: 30 })
  assert.deepEqual(chatContext({ desk: 'yard', number: 30, pr: 30 }), { desk: 'yard', kind: 'review', number: 30 })
  assert.equal(chatContext({ desk: 'yard', title: 'audit', chore: {} }), null)
  assert.equal(chatContext(null), null)
})

test('a marker shows before the first message about a card and wherever the card changes between messages', () => {
  ui.server = { desks }
  const you = (text: string, context: object | null) => ({ role: 'you', text, context })
  const rows = chatRows([you('a', issue), { role: 'claude', text: 'A.', html: '' }, you('b', issue),
    { role: 'claude', text: ' ', html: '' }, you('c', review), you('d', null), you('e', null)])
  assert.deepEqual(rows.map(row => row.role === 'marker' ? `[${row.text}]` : row.text), ['[Now looking at #12 in Yard]',
    'a', 'A.', 'b', '[Now looking at review #30 in Yard]', 'c', '[Nothing selected now]', 'd', 'e'])
  assert.deepEqual(chatRows([you('a', null)]).map(row => row.role), ['you'])
})

test('each row draws as its kind: the message, the reply\'s HTML, a tool call\'s line, a note, and a marker', () => {
  assert.equal(squash(textOf(ChatRow({ row: { role: 'you', text: 'What needs me?' } }))), 'What needs me?')
  const claude = ChatRow({ row: { role: 'claude', text: 'Hi', html: '<p>Hi</p>' } }) as Element
  assert.deepEqual([claude.props.class, claude.props.dangerouslySetInnerHTML], ['chat-claude markdown',
    { __html: '<p>Hi</p>' }])
  const line = ChatRow({ row: { role: 'tool', text: '→ Bash: ls' } }) as Element
  assert.deepEqual([line.props.class, line.props.title], ['chat-tool', '→ Bash: ls'])
  assert.equal((ChatRow({ row: { role: 'note', text: 'Failed', error: true } }) as Element).props.class,
    'chat-note error')
  assert.equal(squash(textOf(ChatRow({ row: { role: 'marker', text: 'Now looking at #12 in Yard' } }))),
    'Now looking at #12 in Yard')
})

test('an empty chat says what it does, offers no starter questions, and sends nothing until a message is typed', () => {
  ui.server = { desks }
  const drawn = Chat({ card: null })
  assert.match(squash(textOf(classed(drawn, 'chat-empty')[0]?.props.children as Drawn)), /^Ask Claude Code/)
  assert.deepEqual(buttons(drawn), [{ text: 'Clear Chat', disabled: true }, { text: 'Send', disabled: true }])
  assert.equal(squash(textOf(classed(drawn, 'chat-context')[0]?.props.children as Drawn)), 'No card selected')
  ui.chat.draft = 'Hello'
  assert.deepEqual(buttons(Chat({ card: null })).at(-1), { text: 'Send', disabled: false })
})

test('a selected card names itself under the box and adds no button', () => {
  ui.server = { desks }
  const drawn = Chat({ card: { desk: 'yard', number: 12, pr: 30 } })
  assert.deepEqual(buttons(drawn).map(one => one.text), ['Clear Chat', 'Send'])
  assert.equal(squash(textOf(classed(drawn, 'chat-context')[0]?.props.children as Drawn)), 'About #12 in Yard')
})

test('while a reply comes, Stop takes Send\'s place and the chat says Claude is working', () => {
  ui.server = { desks }
  applyChat({ session: 's', running: true, from: 0, items: [{ role: 'you', text: 'Why?', context: null }] })
  let drawn = Chat({ card: null })
  assert.deepEqual(buttons(drawn).at(-1), { text: 'Stop', disabled: false })
  assert.equal(classed(drawn, 'chat-waiting').length, 1)
  // The reply's first words take the working line's place, and the next patch grows them.
  applyChat({ session: 's', running: true, from: 1, items: [{ role: 'claude', text: 'Because', html: 'Because' }] })
  applyChat({ session: 's', running: true, from: 1, items: [{ role: 'claude', text: 'Because it', html: 'Because it' },
    { role: 'tool', text: '→ Bash: ls' }] })
  assert.deepEqual(ui.chat.items.map(item => item.text), ['Why?', 'Because it', '→ Bash: ls'])
  drawn = Chat({ card: null })
  assert.equal(classed(drawn, 'chat-waiting').length, 1)
  applyChat({ session: 's', running: false, from: 3, items: [] })
  drawn = Chat({ card: null })
  assert.equal(classed(drawn, 'chat-waiting').length, 0)
  assert.deepEqual(buttons(drawn).at(-1), { text: 'Send', disabled: true })
})

test('Clear Chat asks to confirm before it starts a new session', () => {
  ui.server = { desks }
  applyChat({ session: 's', running: false, from: 0, items: [{ role: 'you', text: 'Hi', context: null }] })
  const clear = elementsOf(Chat({ card: null })).find(one => one.type === 'button'
    && squash(textOf(one.props.children as Drawn)) === 'Clear Chat')
  ;(clear?.props.onClick as () => void)()
  assert.equal(ui.chat.confirmClear, true)
  const drawn = Chat({ card: null })
  assert.match(squash(textOf(classed(drawn, 'side-head')[0]?.props.children as Drawn)),
    /^Chat Start a new session\? Clear Cancel$/)
})

test('the Chat takes the side column in place of Runners or History, and its box takes the cursor as it opens', () => {
  ui.side = 'runners'
  toggleSide('chat')
  assert.deepEqual([ui.side, ui.chat.focus], ['chat', true])
  toggleSide('chat')
  assert.deepEqual([ui.side, ui.chat.focus], [null, false])
})
