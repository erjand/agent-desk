// The Chat column: a conversation with a Claude Code session about the desk, which chat.ts runs on the server and
// streams over the page's event stream. Each message carries the selected card, and a marker shows where that changed
// between messages. Stop ends a reply in progress, and Clear Chat, after a confirm, starts a new session.
import { html } from './vendor/preact-htm.js'
import { deskOf, draw, post, ui } from './app-state.js'

// How near the bottom, in pixels, the log counts as at the bottom, where a streaming reply keeps it.
const STICK_PX = 24

/**
 * What a card means to the chat: its project's issue, with the issue's review when it has one, or the review a card
 * shows alone; or null for no card, or one with no number, such as a chore's.
 */
export function chatContext(card) {
  if (!card || card.number === undefined || card.chore) {
    return null
  }
  const review = card.pr !== undefined && card.number === card.pr
  return { desk: card.desk, kind: review ? 'review' : 'issue', number: card.number,
    ...!review && card.pr !== undefined ? { review: card.pr } : {} }
}

/** How the chat names what a message was about, such as `#380 in agent-desk` or `review #405 in agent-desk`. */
function contextLabel(context) {
  return `${context.kind === 'review' ? 'review ' : ''}#${context.number} in ${deskOf(context.desk)?.title
    ?? context.desk}`
}

function sameContext(a, b) {
  return a === null || b === null ? a === b
    : a.desk === b.desk && a.kind === b.kind && a.number === b.number
}

/**
 * The chat's rows: its items, less any empty stretch of reply, with a marker before each message sent about another
 * card than the message before it, and before the first message when it names a card.
 */
export function chatRows(items) {
  const rows = []
  let last
  for (const item of items) {
    if (item.role === 'claude' && !item.text.trim()) {
      continue
    }
    if (item.role === 'you') {
      if (last === undefined ? item.context !== null : !sameContext(last, item.context)) {
        rows.push({ role: 'marker', text: item.context ? `Now looking at ${contextLabel(item.context)}`
          : 'Nothing selected now' })
      }
      last = item.context
    }
    rows.push(item)
  }
  return rows
}

/** Takes a change the server sent: the items from `from` on replace the page's, with the session and running flag. */
export function applyChat(patch) {
  ui.chat.items = [...ui.chat.items.slice(0, patch.from), ...patch.items]
  ui.chat.running = patch.running
  ui.chat.session = patch.session
}

/** Sends the message typed in the box about the selected card, and empties the box once the server takes it. */
export async function sendChat(text, card) {
  const message = text.trim()
  if (message === '' || ui.chat.running || ui.chat.sending) {
    return
  }
  ui.chat.sending = true
  ui.chat.stick = true
  draw()
  const sent = await post('/api/chat', { text: message, context: chatContext(card) })
  ui.chat.sending = false
  if (sent !== null && ui.chat.draft.trim() === message) {
    ui.chat.draft = ''
  }
  draw()
}

/** Keeps the log at its newest line while the person leaves it at the bottom, and still once they scroll up. */
function onChatScroll(log) {
  ui.chat.stick = log.scrollTop + log.clientHeight >= log.scrollHeight - STICK_PX
}

async function clear() {
  ui.chat.confirmClear = false
  draw()
  await post('/api/chat-clear', {})
}

/** One row of the chat: a marker, the person's message, a stretch of reply, a tool call's line, or a note. */
export function ChatRow({ row }) {
  if (row.role === 'marker') {
    return html`<div class="chat-marker"><span>${row.text}</span></div>`
  }
  if (row.role === 'you') {
    return html`<div class="chat-you">${row.text}</div>`
  }
  if (row.role === 'claude') {
    return html`<div class="chat-claude markdown" dangerouslySetInnerHTML=${{ __html: row.html }} />`
  }
  if (row.role === 'tool') {
    return html`<div class="chat-tool" title=${row.text}>${row.text}</div>`
  }
  return html`<div class=${row.error ? 'chat-note error' : 'chat-note'}>${row.text}</div>`
}

/**
 * The Chat column: its heading with Clear Chat, the conversation, and the box, with what it sends the message about
 * and Send, or Stop while a reply comes, under it. Enter sends, and Shift+Enter starts a new line.
 */
export function Chat({ card }) {
  const c = ui.chat
  const rows = chatRows(c.items)
  const busy = c.running || c.sending
  const context = chatContext(card)
  const send = () => void sendChat(c.draft, card)
  return html`<aside class="chat-side" aria-label="Chat">
    <div class="side-head">
      <h2>Chat</h2>
      ${c.confirmClear ? html`<span class="muted">Start a new session?</span>
        <button type="button" class="small danger" onClick=${() => void clear()}>Clear</button>
        <button type="button" class="small" onClick=${() => { c.confirmClear = false; draw() }}>Cancel</button>`
        : html`<button type="button" class="small" title="Forget this conversation and start a new session"
          disabled=${c.items.length === 0 && c.session === null} onClick=${() => { c.confirmClear = true; draw() }}>
          Clear Chat</button>`}
    </div>
    <div class="chat-log" role="log" onScroll=${event => onChatScroll(event.currentTarget)}>
      ${rows.length === 0 ? html`<p class="muted chat-empty">Ask Claude Code about the desk: its issues, reviews, runs,
        merge queue, or this machine. It reads the desk's live state, acts on what you ask, and asks you first before
        anything it can't undo.</p>`
        : rows.map((row, at) => html`<${ChatRow} key=${at} row=${row} />`)}
      ${c.running && rows.at(-1)?.role !== 'claude' ? html`<p class="chat-waiting">Claude is working…</p>` : null}
    </div>
    <div class="chat-input">
      <textarea rows="3" placeholder="Ask about the desk" aria-label="Message" value=${c.draft}
        onInput=${event => { c.draft = event.target.value; draw() }}
        onKeyDown=${event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault()
            send()
          } else if (event.key === 'Escape') {
            event.target.blur()
          }
        }} />
      <span class="chat-context muted">${context ? `About ${contextLabel(context)}` : 'No card selected'}</span>
      <div class="chat-buttons">
        ${c.running ? html`<button type="button" class="small danger" title="End the reply now"
          onClick=${() => void post('/api/chat-stop', {})}>Stop</button>`
          : html`<button type="button" class="small primary" disabled=${busy || c.draft.trim() === ''}
            onClick=${send}>Send</button>`}
      </div>
    </div>
  </aside>`
}
