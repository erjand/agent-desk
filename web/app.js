// The agent-desk web page: draws the cards and runners server.ts streams over /api/events, and posts its buttons' and
// keys' presses back. Every project's work shows as one card per issue, grouped by stage in a list, with the selected
// card's issue, PR, and actions in a panel beside it. Preact draws it from `ui`, and draw() draws it again after any
// change; Preact keeps whatever didn't change, so a redraw leaves scroll positions, open lists, and pictures alone.
// The modules beside it draw the rest: app-state.js holds `ui` and what changes it, app-parts.js the chips, icons, and
// labels, app-item.js each card's row and the Detailed button, app-header.js the header, app-detail.js the selected
// card's panel, app-runners.js the Runners sidebar, app-github.js the GitHub sidebar, app-history.js the History
// column, app-chat.js the Chat column, app-hovercard.js the hovercards, app-new-issue.js the New Issue form,
// app-resources.js the Resources view, app-drag.js dragging a card to a new place in its group, app-order.js the
// groups' Sort buttons, and app-help.js the keyboard shortcuts dialog.
import { html, render } from './vendor/preact-htm.js'
import { applyChat, Chat } from './app-chat.js'
import { choreCard, choreId } from './app-chore.js'
import { barHeight, Detail, LOG_MS, onDetailScroll, reviewKey, showTabTop } from './app-detail.js'
import { GitHub } from './app-github.js'
import { Help } from './app-help.js'
import { History } from './app-history.js'
import { hideHovercard, hoverRoot, onLinkOut, onLinkOver } from './app-hovercard.js'
import { DetailedToggle, Item, noteOf } from './app-item.js'
import { closeNewIssue, NewIssue, openNewIssue, showCreated } from './app-new-issue.js'
import { Header, placeTip } from './app-header.js'
import { GroupSort, SORTED } from './app-order.js'
import { closeResources, Resources } from './app-resources.js'
import { followMark, markFileInView } from './app-review.js'
import { onUsageOutside } from './app-usage.js'
import { Chip, chipWidth } from './app-parts.js'
import { Runners } from './app-runners.js'
import { act, clickedItem, closeDetail, deskOf, draw, drawWith, foldAll, hotkeyOf, itemHref, KEYS, pageItemOf, post,
  press, ready, remember, rememberFolds, select, showRun, shownDesks, STAGE_NAMES, STAGES, toast, toggleSide, ui,
  visit } from './app-state.js'

// The most characters of a note the list's note column makes room for; a longer note clips.
const NOTE_CHARS = 40

const root = document.getElementById('app')
let drawn = null
// Where the pointer last was, which picks the list row a redraw holds still, and the tab the last draw showed.
let pointer = null
let drawnTab = null
// The heading a fold from its stuck place holds where it shows, as a mark for holdList(), until the next draw.
let foldMark = null
// Whether the next draw shows the list from its top, as it does after every group folds.
let listTop = false

/**
 * Where the list's rows sit on screen: the row under the pointer first, then each one in view, top down. A stuck
 * heading stays put whatever moves above it, so it marks nothing.
 */
function listMarks() {
  const list = root.querySelector('.list')
  if (list === null) {
    return []
  }
  const view = list.getBoundingClientRect()
  const shown = [...list.querySelectorAll('[data-key]')].filter(node => {
    const box = node.getBoundingClientRect()
    return box.bottom > view.top && box.top < view.bottom
  })
  const hovered = pointer && document.elementFromPoint(pointer.x, pointer.y)?.closest('.list [data-key]')
  return (hovered ? [hovered, ...shown.filter(node => node !== hovered)] : shown).filter(node => !stuck(node))
    .map(markOf)
}

/** A list row's mark: its key, its group, and where it sits on screen. */
function markOf(node) {
  return { key: node.dataset.key, group: groupOf(node), top: node.getBoundingClientRect().top }
}

/** Whether a list row is a group's heading that sticks below the search box, away from the top of its box. */
function stuck(node) {
  const box = node.closest('.box')
  return node.matches('.box-head') && box !== null
    && node.getBoundingClientRect().top - box.getBoundingClientRect().top > box.clientTop + 0.5
}

/** The group a list row sits in, by its heading's key. */
function groupOf(node) {
  return node.closest('.box')?.querySelector('.box-head')?.dataset.key
}

/**
 * Scrolls the list so the first marked row still there sits where it did, whatever moved above it. A row that moved to
 * another group, as an issue does from Open to Queued, holds nothing still; the next marked row keeps the list in
 * place.
 */
function holdList(marks) {
  const list = root.querySelector('.list')
  for (const mark of marks) {
    const node = list?.querySelector(`[data-key="${CSS.escape(mark.key)}"]`)
    if (node && groupOf(node) === mark.group) {
      list.scrollTop += node.getBoundingClientRect().top - mark.top
      return
    }
  }
}

/**
 * Draws the page again. Rows added to the list, dropped from it, or moved within it leave the rest where they were on
 * screen: the row under the pointer, or else the first row in view, holds still, and the list scrolls to keep it so.
 * draw() from app-state.js runs it.
 */
function drawPage() {
  if (panelId() !== drawn) {
    // A newly selected card starts at its top, with its header in view.
    ui.stuck = false
    ui.pinned = false
  }
  // A search filters the list from its top, so only a redraw that leaves the search alone holds rows still.
  const marks = foldMark ? [foldMark] : drawnTab === ui.tab && !ui.searched && !listTop ? listMarks() : []
  foldMark = null
  if (ui.searched || listTop) {
    root.querySelector('.list')?.scrollTo(0, 0)
    ui.searched = false
    listTop = false
  }
  render(html`<${App} />`, root)
  holdList(marks)
  drawnTab = ui.tab
  if (ui.reveal) {
    root.querySelector('.item.selected')?.scrollIntoView({ block: 'nearest' })
    ui.reveal = false
  }
  const panel = root.querySelector('.detail')
  if (panelId() !== drawn) {
    panel?.scrollTo(0, 0)
    drawn = panelId()
  } else if (ui.tabTop && panel) {
    showTabTop(panel)
  }
  ui.tabTop = false
  if (panel) {
    barHeight(panel)
    // A redraw can move the diffs without a scroll, as Expand All or another review does. The tree follows the mark
    // once a jump's scroll has pinned the bars above it, which the next frame's scroll event does.
    markFileInView(panel)
    requestAnimationFrame(() => {
      const shown = root.querySelector('.detail')
      if (shown) {
        followMark(shown)
      }
    })
  }
  const log = root.querySelector('.agent-log')
  if (log && ui.logStick) {
    log.scrollTop = log.scrollHeight
  }
  const chat = root.querySelector('.chat-log')
  if (chat && ui.chat.stick) {
    chat.scrollTop = chat.scrollHeight
  }
  if (ui.chat.focus) {
    ui.chat.focus = false
    root.querySelector('.chat-input textarea')?.focus()
  }
}

/** Queued's cards for a project's chores that are starting, or whose run no runner has taken yet. */
function choreCards(desk) {
  const board = desk.board
  const taken = name => board.runners.some(row => row.title?.endsWith(name))
  return (board.choreList ?? []).filter(chore => /^(running|starting)/.test(chore.last) && !taken(chore.name))
    .map(chore => choreCard(desk, chore, { stage: 'queued',
      note: [chore.last === 'starting' ? 'chore, starting' : 'chore, waiting for a runner',
        queueNote(desk.repeats?.[chore.name])].filter(Boolean).join(' · ') }))
}

/** The card of the Chores row whose id is `id`, among the shown projects' chores, or undefined. */
function choreRowCard(id) {
  for (const desk of shownDesks().filter(ready)) {
    const chore = (desk.board.choreList ?? []).find(one => choreId(desk.id, one.name) === id)
    if (chore) {
      return choreCard(desk, chore, { note: `chore, next ${chore.next}` })
    }
  }
  return undefined
}

const since = card => (card.since ? Date.parse(card.since) : Infinity)

/** The search box's words, lowercased; a card shows while every word appears somewhere in what it says. */
function searchWords() {
  return ui.query.toLowerCase().split(/\s+/).filter(Boolean)
}

/**
 * Everything the search matches a card by: its title, numbers, project, labels, state, note, runner, and group, as
 * lowercase text.
 */
function searchText(card) {
  const desk = deskOf(card.desk)
  return [card.title, card.number, card.number !== undefined && `#${card.number}`, card.pr, card.pr !== undefined
    && `#${card.pr}`, card.desk, desk?.title, card.state, card.note, card.runner, STAGE_NAMES[card.stage],
  ...(card.labels ?? [])].filter(part => part !== undefined && part !== false).join(' ').toLowerCase()
}

function matches(card, words) {
  const text = searchText(card)
  return words.every(word => text.includes(word))
}

/**
 * The list's groups for the shown projects: Needs You longest waiting first, and the rest as each project's board lists
 * them, so Open, Blocked, Needs Design, Backlog, and Ideas follow its list order. On All, each project's cards stay
 * together in project order. The History column lists merges.
 */
function groups() {
  const words = searchWords()
  const cards = shownDesks().filter(ready).flatMap(desk => [...desk.cards, ...choreCards(desk)])
    .filter(card => words.length === 0 || matches(card, words))
  return STAGES.map(([stage, title, hue]) => {
    const list = cards.filter(card => card.stage === stage)
    if (stage === 'needs') {
      list.sort((a, b) => since(a) - since(b))
    }
    return { stage, title, hue, cards: list }
  })
}

/** The cards the keys step through: those in open groups, in order. */
function stepped() {
  return groups().filter(group => !shut(group.stage)).flatMap(group => group.cards)
}

/** Whether a group shows folded: as the person left it, except that a search opens every group with a match. */
function shut(stage) {
  return ui.query.trim() === '' && ui.collapsed.has(stage)
}

/** The card the panel shows, or null while the panel is closed. */
function current() {
  return ui.closed ? null : chosen()
}

/** What the panel shows: the New Issue form, the Resources view, the card's id, or null for nothing. */
function panelId() {
  return ui.newIssue.open ? 'new-issue' : ui.resources.open ? 'resources' : current()?.id ?? null
}

/** Whether the New Issue form or the Resources view takes the panel in place of the card. */
function panelTaken() {
  return ui.newIssue.open || ui.resources.open
}

/**
 * The selected card, or the item a link showed that the list has no card for, or the chore a Chores row opened, or the
 * first card when the selected one isn't shown.
 */
function chosen() {
  if (ui.server === null) {
    return null
  }
  const all = groups().flatMap(group => group.cards)
  return all.find(card => card.id === ui.selected) ?? (ui.visiting?.id === ui.selected ? ui.visiting : null)
    ?? choreRowCard(ui.selected) ?? stepped()[0] ?? all[0] ?? null
}

/**
 * Folds or opens a group. A group folded from its stuck heading keeps the heading where it showed, so the list scrolls
 * back to it instead of leaving the next group's rows in its place.
 */
function toggleGroup(stage) {
  const head = root.querySelector(`.list [data-key="group:${stage}"]`)
  foldMark = head && stuck(head) ? markOf(head) : null
  if (ui.collapsed.has(stage)) {
    ui.collapsed.delete(stage)
  } else {
    ui.collapsed.add(stage)
  }
  rememberFolds()
  draw()
}

/** The width of the list's note column, in the notes' characters: its longest note up to NOTE_CHARS, with its tile. */
function noteWidth() {
  const longest = Math.max(0, ...groups().flatMap(group => group.cards).map(card => noteOf(card)?.length ?? 0))
  return Math.min(longest, NOTE_CHARS) + 3
}

/**
 * A group's box: its heading, which collapses it, and its cards. The Sort button of Queued, Open, Blocked, Needs
 * Design, Backlog, and Ideas, which sorts the group's order in the store, sits beside its heading.
 */
function Group({ group, selected }) {
  const folded = shut(group.stage)
  const searching = ui.query.trim() !== ''
  if (group.cards.length === 0 && (group.stage !== 'needs' || searching)) {
    return null
  }
  const head = html`<button type="button" class="box-head" aria-expanded=${String(!folded)}
    data-key=${`group:${group.stage}`} onClick=${() => toggleGroup(group.stage)}>
    <span class="caret">${folded ? '▸' : '▾'}</span>
    <span style=${{ color: `var(--${group.hue})` }}>${group.title}</span>
    <span class="counter">${group.cards.length}</span>
  </button>`
  return html`<section class="box">
    ${SORTED.has(group.stage) ? html`<div class="box-heading">${head}<${GroupSort} stage=${group.stage} /></div>`
      : head}
    ${folded ? null : group.cards.length === 0 ? html`<div class="empty">Nothing needs you.</div>`
      : group.cards.map(card => html`<${Item} key=${card.id} card=${card} selected=${card.id === selected} />`)}
  </section>`
}

function lastClass(cell) {
  return cell.startsWith('done') ? 'success' : /^(running|starting)/.test(cell) ? 'warning'
    : /^(failure|cancelled|timed_out)/.test(cell) ? 'error' : undefined
}

function nextClass(cell) {
  return cell.startsWith('now') ? 'success' : cell.startsWith('waiting') ? 'needs-text' : undefined
}

/** How a chore's queue reads: how many more runs follow the one going now, or that it loops. */
function queueNote(left) {
  return left === undefined ? null : left === 'forever' ? 'looping' : `${left} more queued`
}

/**
 * A chore's run buttons: for one not running, how many times to run it, and Run and Loop; for one with runs queued
 * after the one going now, what's queued and Stop.
 */
function ChoreControls({ desk, chore }) {
  const id = `${desk.id}:${chore.name}`
  const left = desk.repeats?.[chore.name]
  const busy = /^(running|starting)/.test(chore.last)
  const run = times => void post('/api/chore', { desk: desk.id, name: chore.name, times })
  return html`
    ${left === undefined ? null : html`<span class="note">${queueNote(left)}</span>
      <button type="button" class="small danger" title="Run no more after the run going now"
        onClick=${() => void post('/api/chore-stop', { desk: desk.id, name: chore.name })}>Stop</button>`}
    ${busy ? null : html`
      <input type="number" class="times" min="1" max="99" value=${ui.choreTimes.get(id) ?? 1}
        aria-label=${`Times to run ${chore.name}`} title="Times to run it, one after another"
        onInput=${event => ui.choreTimes.set(id, Math.max(1, Math.min(99, Number(event.target.value) || 1)))} />
      <button type="button" class="small" onClick=${() => run(ui.choreTimes.get(id) ?? 1)}>Run</button>
      <button type="button" class="small" title="Run it again each time a run ends, until you stop it"
        onClick=${() => run('forever')}>Loop</button>`}`
}

/** A chore's days between runs and whether the timer skips it, counting the change its open schedule PR makes. */
function scheduleOf(desk, chore) {
  const pending = desk.schedule?.[chore.name]
  return { every: pending?.every ?? (Number.parseInt(chore.every, 10) || 1),
    paused: pending?.paused ?? chore.next === 'paused', pending }
}

/** The change waiting on its PR, as `7d → 3d, paused in #52`, on a line of its own under the chore's values. */
function PendingSchedule({ desk, chore }) {
  const pending = desk.schedule?.[chore.name]
  if (!pending) {
    return null
  }
  const to = [pending.every === undefined ? null : `${chore.every} → ${pending.every}d`,
    pending.paused === undefined ? null : pending.paused ? 'paused' : 'resumed'].filter(Boolean).join(', ')
  return html`<span class="c-pending" title="The schedule once its PR merges">${to} in <a
    href=${itemHref(desk.id, pending.pr)}>#${pending.pr}</a>
    </span>`
}

/** Asks the server to open or add to the schedule PR, keeping the editor open with the server's error when it fails. */
async function saveSchedule() {
  const asked = ui.schedule
  asked.saving = true
  asked.error = null
  draw()
  let sent = {}
  try {
    const res = await fetch('/api/chore-schedule', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ desk: asked.desk, name: asked.name,
        ...asked.every === undefined ? {} : { every: asked.every },
        ...asked.paused === undefined ? {} : { paused: asked.paused } }) })
    sent = await res.json().catch(() => ({}))
    sent.ok = res.ok
  } catch {
    sent = { ok: false, error: 'the agent-desk server is not answering' }
  }
  if (ui.schedule !== asked) {
    return
  }
  if (sent.ok) {
    ui.schedule = null
    toast(`Opened #${sent.pr}: ${sent.title}.`)
  } else {
    asked.saving = false
    asked.error = sent.error ?? 'the server refused'
    draw()
  }
}

/**
 * A chore's schedule buttons: Edit for its days between runs and Pause or Resume. Each change asks for a confirm, then
 * opens a PR that edits the project's config, or adds to the one open.
 */
function ScheduleControls({ desk, chore }) {
  const id = `${desk.id}:${chore.name}`
  const now = scheduleOf(desk, chore)
  const asked = ui.schedule?.id === id ? ui.schedule : null
  const cancel = () => {
    ui.schedule = null
    draw()
  }
  const start = change => {
    ui.schedule = { id, desk: desk.id, name: chore.name, confirm: change.paused !== undefined, saving: false,
      error: null, ...change }
    draw()
  }
  if (!asked) {
    return html`<button type="button" class="small" title="Change the days between its runs"
        onClick=${() => start({ every: now.every })}>Edit</button>
      <button type="button" class="small"
        title=${now.paused ? 'Let the timer start it again' : 'Keep the timer from starting it'}
        onClick=${() => start({ paused: !now.paused })}>${now.paused ? 'Resume' : 'Pause'}</button>`
  }
  const error = asked.error ? html`<span class="schedule-error" title=${asked.error}>${asked.error}</span>` : null
  if (asked.saving) {
    return html`<span class="note">Opening the PR…</span>`
  }
  if (!asked.confirm) {
    const set = value => { asked.every = Math.max(1, Math.min(365, Math.round(Number(value)) || 1)) }
    return html`${error}<label class="days">every <input type="number" class="times" min="1" max="365"
        value=${asked.every} aria-label=${`Days between ${chore.name}'s runs`}
        onInput=${event => set(event.target.value)}
        onKeyDown=${event => {
          if (event.key === 'Enter') { set(event.target.value); asked.confirm = true; draw() }
          if (event.key === 'Escape') cancel()
        }} /> days</label>
      <button type="button" class="small primary" onClick=${() => { asked.confirm = true; draw() }}>Save</button>
      <button type="button" class="small" onClick=${cancel}>Cancel</button>`
  }
  const days = asked.every === 1 ? 'day' : `${asked.every} days`
  if (asked.paused === undefined && asked.every === now.every) {
    return html`<span class="note">${chore.name} already runs every ${days}</span>
      <button type="button" class="small" onClick=${() => { asked.confirm = false; draw() }}>Back</button>`
  }
  const what = asked.paused === undefined ? `run ${chore.name} every ${days}`
    : `${asked.paused ? 'pause' : 'resume'} ${chore.name}`
  return html`${error}<span class="note">${now.pending ? `Add to #${now.pending.pr}` : 'Open a PR'}: ${what}</span>
    <button type="button" class="small primary" onClick=${() => void saveSchedule()}>Confirm</button>
    <button type="button" class="small" onClick=${cancel}>Cancel</button>`
}

/** The model and effort a chore runs with; one left to the user's own settings reads as their default. */
function ModelTag({ info }) {
  if (!info) {
    return null
  }
  return html`<span class="model-tag" title="The model and effort this chore runs with">
    ${info.model || 'your model'} · ${info.effort || 'your effort'}</span>`
}

/** How a chore's newest runs went, from the run log: how many, how long on average, and their PRs. */
function statsOf(info) {
  if (!info?.runs) {
    return null
  }
  const prs = [info.merged && `${info.merged} merged`, info.closed && `${info.closed} closed`,
    info.open && `${info.open} open`].filter(Boolean).join(', ')
  return [`last ${info.runs} run${info.runs === 1 ? '' : 's'}`, info.minutes !== null && `${info.minutes} min avg`, prs]
    .filter(Boolean).join(' · ')
}

/**
 * The shown projects' chores, each with its model, when it ran and runs next, how its runs went, and its buttons. A
 * click on a row outside its buttons and inputs opens the chore's page in the panel.
 */
function Chores() {
  const words = searchWords()
  const rows = shownDesks().filter(ready).flatMap(desk => (desk.board.choreList ?? []).map(chore => ({ desk, chore })))
    .filter(({ desk, chore }) => words.every(word => `chore ${chore.name} ${desk.id} ${desk.title}`.toLowerCase()
      .includes(word)))
  if (rows.length === 0) {
    return null
  }
  const folded = shut('chores')
  const open = current()?.id
  return html`<section class="box chores" style=${{ '--chip-col': ui.tab === 'all' ? `${chipWidth()}ch` : '0px' }}>
    <button type="button" class="box-head" aria-expanded=${String(!folded)} data-key="group:chores"
      onClick=${() => toggleGroup('chores')}>
      <span class="caret">${folded ? '▸' : '▾'}</span><span style=${{ color: 'var(--chores)' }}>Chores</span>
      <span class="counter">${rows.length}</span>
    </button>
    ${folded ? null : rows.map(({ desk, chore }) => html`<div key=${`${desk.id}:${chore.name}`}
      class=${choreId(desk.id, chore.name) === open ? 'chore-row selected' : 'chore-row'}
      data-key=${`chore:${desk.id}:${chore.name}`} onClick=${event => {
        if (!event.target.closest('button, input, select, textarea, a, label')) {
          select(choreId(desk.id, chore.name))
        }
      }}>
      <span class="chip-cell">${ui.tab === 'all' ? html`<${Chip} desk=${desk.id} />` : null}</span>
      <strong class="c-name">${chore.name}</strong>
      <span class="c-model"><${ModelTag} info=${desk.choreInfo?.[chore.name]} /></span>
      <span class="c-stats" title=${statsOf(desk.choreInfo?.[chore.name]) ?? ''}>
        ${statsOf(desk.choreInfo?.[chore.name])}</span>
      <span class="c-every">every ${chore.every}</span>
      <span class=${`c-last ${lastClass(chore.last) ?? ''}`} title=${chore.last}>${chore.last}</span>
      <span class="c-next" title=${`next ${chore.next}`}>next <span class=${nextClass(chore.next)}>${chore.next}</span>
        </span>
      <${PendingSchedule} desk=${desk} chore=${chore} />
      <span class="c-controls"><span class="chore-controls">
        ${ui.schedule?.id === `${desk.id}:${chore.name}` ? null
          : html`<${ChoreControls} desk=${desk} chore=${chore} />`}
        <${ScheduleControls} desk=${desk} chore=${chore} /></span></span>
    </div>`)}
  </section>`
}

/**
 * The box that filters the list as the person types, which `/` focuses with its text selected, so typing replaces it:
 * Enter selects the first card that matches, and Esc, or the X while it holds text, clears the search.
 */
function Search() {
  const set = query => {
    ui.query = query
    ui.searched = true
    draw()
  }
  return html`<div class="search">
    <div class="search-field">
      <input type="search" placeholder="Filter by title, number, project, label, or state" value=${ui.query}
        aria-label="Filter the list" onInput=${event => set(event.target.value)}
        onKeyDown=${event => {
          if (event.key === 'Escape') {
            event.preventDefault()
            set('')
            event.target.blur()
          } else if (event.key === 'Enter') {
            event.preventDefault()
            const first = stepped()[0]
            event.target.blur()
            if (first) {
              select(first.id)
            }
          }
        }} />
      ${ui.query && html`<button type="button" class="clear-search" title="Clear the search"
        aria-label="Clear the search" onClick=${event => {
          const box = event.currentTarget.closest('.search-field').querySelector('input')
          set('')
          box.focus()
        }}>
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>`}
    </div>
    <kbd>/</kbd>
    <${DetailedToggle} />
  </div>`
}

function App() {
  if (ui.server === null) {
    return html`<${Header} /><p class="muted pad">Connecting…</p>`
  }
  if (ui.server.desks.length === 0) {
    return html`<${Header} /><p class="muted pad">No projects are registered; run agent-desk install in one.</p>`
  }
  // The New Issue form and the Resources view take the panel in place of the card, which the list leaves unselected
  // meanwhile.
  const card = panelTaken() ? null : current()
  const shown = new Set(groups().flatMap(group => group.cards.map(one => one.id)))
  return html`<${Header} selected=${current()?.desk} />
    <main class=${ui.side ? 'split with-side' : 'split'}>
      <div class=${ui.detailed ? 'list detailed' : 'list'} role="listbox" aria-label="Work"
        style=${{ '--chip-width': `${chipWidth()}ch`, '--note-width': `${noteWidth()}ch` }}>
        <${Search} />
        ${groups().map(group => html`<${Group} key=${group.stage} group=${group} selected=${card?.id} />`)}
        ${ui.query.trim() !== '' && groups().every(group => group.cards.length === 0)
          ? html`<p class="muted no-match">Nothing matches “${ui.query.trim()}”.</p>` : null}
        <${Chores} />
      </div>
      <div class="detail" onScroll=${event => onDetailScroll(event.currentTarget)}>${ui.newIssue.open
        ? html`<${NewIssue} />` : ui.resources.open ? html`<${Resources} />`
          : card ? html`<${Detail} key=${card.id} card=${card} visible=${shown} />` : null}
      </div>
      ${ui.side === 'runners' ? html`<${Runners} shown=${shown} />`
        : ui.side === 'history' ? html`<${History} />` : ui.side === 'chat' ? html`<${Chat} card=${card} />`
          : ui.side === 'github' ? html`<${GitHub} />` : null}
    </main>
    <div class="toasts" aria-live="polite">${ui.toasts.map(one => html`<div class="toast" key=${one.id}>
      ${one.text}</div>`)}</div>
    ${ui.help ? html`<${Help} />` : null}`
}

/**
 * The keys: j and k step through the cards, `-` and `+` fold and open every group, a card's action keys press its
 * buttons, and digits pick a tab.
 */
function onKey(event) {
  const key = hotkeyOf(event)
  if (key === null) {
    return
  }
  // While the New Issue form or the Resources view shows, no card is open, so a card's keys press nothing.
  const card = panelTaken() ? null : current()
  if (key === 'Escape' && ui.newIssue.open && !ui.help) {
    closeNewIssue()
    event.preventDefault()
    return
  } else if (key === 'Escape' && ui.resources.open && !ui.help) {
    closeResources()
    event.preventDefault()
    return
  } else if (key === 'Escape') {
    if (!ui.confirming && !ui.reviewConfirm && !ui.help && card) {
      closeDetail()
      return
    }
    ui.confirming = null
    ui.reviewConfirm = null
    ui.help = false
  } else if ((key === 'ArrowLeft' || key === 'ArrowRight') && card) {
    // The open card's tabs, in the order they show; the arrows stop at either end.
    const tabs = [...root.querySelectorAll('.gh-main .tabnav button')]
    const at = tabs.findIndex(tab => tab.getAttribute('aria-current') === 'true')
    tabs[Math.max(0, Math.min(tabs.length - 1, at + (key === 'ArrowRight' ? 1 : -1)))]?.click()
    event.preventDefault()
    return
  } else if (key === '/') {
    const box = root.querySelector('.search input')
    box?.focus()
    box?.select()
  } else if (key === '?') {
    ui.help = !ui.help
  } else if (key === 'h') {
    toggleSide('history')
  } else if (key === '\\') {
    toggleSide('runners')
  } else if (key === '`') {
    toggleSide('chat')
  } else if (key === 'g') {
    toggleSide('github')
  } else if (key === '-' || key === '+' || key === '=') {
    // `=` is `+` without Shift on a US layout, and the numpad's keys send `+` and `-` themselves.
    foldAll(key === '-')
    listTop = key === '-'
  } else if (key === 'i') {
    openNewIssue(current()?.desk)
    event.preventDefault()
    return
  } else if (ui.confirming && card?.id === ui.confirming.id
    && (key === 'y' || key === 'Enter' || key === KEYS[ui.confirming.action])) {
    void act(card, ui.confirming.action)
  } else if (reviewKey(card, key)) {
    event.preventDefault()
    return
  } else if (['j', 'k', 'ArrowDown', 'ArrowUp'].includes(key)) {
    const cards = stepped()
    const at = cards.findIndex(one => one.id === card?.id)
    const next = cards[Math.max(0, Math.min(cards.length - 1, at + (key === 'j' || key === 'ArrowDown' ? 1 : -1)))]
    if (next) {
      ui.selected = next.id
      ui.newIssue.open = false
      ui.resources.open = false
      ui.closed = false
      remember('inbox-closed', false)
      ui.confirming = null
      ui.reviewConfirm = null
      ui.detailTab = null
      ui.reveal = true
      remember('inbox-selected', next.id)
    }
  } else if (key === 'o' || key === 'Enter') {
    // A failed run's card shows its Agent Log; any other card shows in the panel, opening it when closed.
    const shown = card ?? chosen()
    if (shown?.run !== undefined) {
      showRun(shown)
    } else if (shown) {
      select(shown.id)
    }
    return
  } else if (/^[1-9]$/.test(key)) {
    const tabs = ['all', ...(ui.server?.desks ?? []).map(desk => desk.id)]
    if (tabs[Number(key) - 1] === undefined) {
      return
    }
    ui.tab = tabs[Number(key) - 1]
    remember('inbox-tab', ui.tab)
  } else {
    const action = Object.keys(KEYS).find(one => KEYS[one] === key)
    if (!card || !action || !card.actions.includes(action)) {
      return
    }
    press(card, action)
    event.preventDefault()
    return
  }
  event.preventDefault()
  draw()
}

// How long the page waits before it opens its event stream again after the server dropped it, in milliseconds.
const RECONNECT_MS = 2_000
// The page's one event stream, and the store event id of the newest state it drew.
let events = null
let lastId = ''
// Whether the page opened from a link to an item, which it shows once the first state names the item's project.
let linked = location.hash !== ''

/**
 * Opens the page's event stream, closing any it had. EventSource retries a dropped stream with the last id it saw; once
 * it gives up, the page opens a new one that names that id, so the server answers with the boards after it.
 */
function connect() {
  events?.close()
  const stream = new EventSource(lastId ? `/api/events?after=${encodeURIComponent(lastId)}` : '/api/events')
  events = stream
  stream.addEventListener('state', event => {
    lastId = event.lastEventId || lastId
    ui.storeEvent = Number(lastId) || 0
    ui.server = JSON.parse(event.data)
    ui.connected = true
    if (ui.tab !== 'all' && !ui.server.desks.some(desk => desk.id === ui.tab)) {
      ui.tab = 'all'
    }
    // A created issue's card shows once its project's board lists it.
    if (ui.created === null || !showCreated()) {
      draw()
    }
    if (linked) {
      linked = false
      const item = pageItemOf(location.href)
      history.replaceState(null, '', location.pathname)
      if (item) {
        void visit(item.desk, item.number)
      }
    }
  })
  stream.addEventListener('toast', event => toast(JSON.parse(event.data).text))
  stream.addEventListener('chat', event => {
    applyChat(JSON.parse(event.data))
    draw()
  })
  // The header says the page is disconnected until a state arrives again.
  stream.addEventListener('error', () => {
    ui.connected = false
    draw()
    if (stream.readyState === EventSource.CLOSED && events === stream) {
      setTimeout(connect, RECONNECT_MS)
    }
  })
}

document.addEventListener('keydown', onKey)
// A narrower panel can wrap the compact bar's buttons, which moves where the tabs pin.
window.addEventListener('resize', () => {
  const panel = root.querySelector('.detail')
  if (panel) {
    barHeight(panel)
    markFileInView(panel)
  }
})
// A narrow window scrolls the page, which carries the panel, instead of the panel itself.
window.addEventListener('scroll', () => {
  const panel = root.querySelector('.detail')
  if (panel) {
    onDetailScroll(panel)
  }
})
document.addEventListener('mouseover', onLinkOver)
// A button's tooltip near the window's right edge shifts left to stay in the window.
document.addEventListener('mouseover', placeTip)
document.addEventListener('focusin', placeTip)
document.addEventListener('mouseout', onLinkOut)
// A hovercard stays where its link was, so a scroll, a click, or a key closes it.
document.addEventListener('scroll', hideHovercard, true)
document.addEventListener('mousedown', event => {
  if (!hoverRoot.contains(event.target)) {
    hideHovercard()
  }
})
// The header's account popover closes on a press outside it.
document.addEventListener('mousedown', onUsageOutside)
document.addEventListener('keydown', hideHovercard)
// A plain click on a link to an item on the page, such as `#123` in a body, shows the item in the detail view; a click
// with a modifier opens the page in a new tab at that item.
document.addEventListener('click', event => {
  const anchor = event.target.closest?.('a[href]')
  const item = anchor ? clickedItem(event, anchor.href) : null
  if (item) {
    event.preventDefault()
    hideHovercard()
    void visit(item.desk, item.number)
  }
})
// A working agent's log reads again while it shows; logOf() asks the server only once the last read is LOG_MS old.
setInterval(() => {
  if (ui.detailTab === 'log' && current()?.stage === 'working') {
    draw()
  }
}, LOG_MS)
document.addEventListener('pointermove', event => {
  pointer = { x: event.clientX, y: event.clientY }
})
document.documentElement.addEventListener('pointerleave', () => {
  pointer = null
})
drawWith(drawPage)
draw()
connect()