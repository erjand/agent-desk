// The History column: the PRs the shown projects merged over a span, with what their runs cost and how long they held
// and queued for the GPU, which it reads from the server and searches, sorts, and pages through.
import { html } from './vendor/preact-htm.js'
import { Chip, Numbers } from './app-parts.js'
import { deskOf, draw, listedCard, remember, select, shownDesks, ui, visit } from './app-state.js'

// The History column's spans, the one it starts on, and how many rows a page holds.
const HISTORY_RANGES = [['24h', '24 h', 1], ['3d', '3 days', 3], ['7d', '7 days', 7], ['30d', '30 days', 30],
  ['custom', 'Custom', 0]]
const HISTORY_PAGE = 20
// How long a read of the column's rows stays fresh while it shows.
const HISTORY_MS = 60_000
// The column's sortable columns: the key each sorts by, its heading, and whether it sorts as a number, newest or
// largest first.
const HISTORY_COLUMNS = [['at', 'Time', true], ['desk', 'Project', false], ['number', 'Number', true],
  ['title', 'Item', false], ['cost', 'Cost', true]]
// What the Cost heading and the totals' dollars say when the pointer rests on them.
const COST_NOTE = 'What Claude Code reported the tokens of the runs behind each PR would cost at API list prices; a '
  + 'subscription doesn\'t bill it, and a run cut off at its time limit or for sleep adds nothing.'
// What the totals' GPU minutes say when the pointer rests on them.
const GPU_NOTE = 'How long the agent-desk gpu calls of the runs behind these PRs held a GPU slot, and how long they '
  + 'queued for one behind other runs. Each PR\'s Cost says its own when the pointer rests on it.'

/** What a row's Cost cell says when the pointer rests on it: its runs, and their GPU minutes when they used the GPU. */
export function costTitle(row) {
  return [`${row.runs} run${row.runs === 1 ? '' : 's'}`, row.gpu].filter(Boolean).join(' · ')
}

/** Whole minutes as the totals line reads them, such as `1 min` or `13 mins`. */
function mins(minutes) {
  const whole = Math.round(minutes)
  return `${whole} ${whole === 1 ? 'min' : 'mins'}`
}

/** The totals line's GPU minutes over the rows whose runs used the GPU; null when none did. */
function gpuTotal(rows) {
  const used = rows.filter(row => row.gpuHeld !== undefined)
  if (used.length === 0) {
    return null
  }
  const queued = used.reduce((sum, row) => sum + (row.gpuQueued ?? 0), 0)
  return `Held GPU for ${mins(used.reduce((sum, row) => sum + row.gpuHeld, 0))}`
    + (Math.round(queued) > 0 ? `, waited ${mins(queued)} for it` : '')
}

/** The span the History column shows, in milliseconds: a preset ending now, or the custom days, whole days. */
function historySpan() {
  const h = ui.history
  if (h.range === 'custom') {
    const from = Date.parse(`${h.from}T00:00`)
    const to = Date.parse(`${h.to}T00:00`) + 86_400_000
    return Number.isNaN(from) || Number.isNaN(to) || to <= from ? null : { since: from, until: to }
  }
  const days = HISTORY_RANGES.find(([id]) => id === h.range)?.[2] ?? 1
  // A preset's end rounds up to the minute, so redraws in the same minute ask for the same span.
  const until = Math.ceil(Date.now() / 60_000) * 60_000
  return { since: until - days * 86_400_000, until }
}

/** The column's rows for the shown projects and span, read again once HISTORY_MS old; null while the first loads. */
function historyRows() {
  const span = historySpan()
  if (span === null) {
    return null
  }
  const desks = shownDesks().map(desk => desk.id).join(',')
  const key = `${desks}|${span.since}|${span.until}`
  const read = ui.history.read
  const fresh = read?.key === key && (read.status === 'loading' || Date.now() - read.at < HISTORY_MS)
  if (!fresh && desks) {
    ui.history.read = { key, status: 'loading', at: Date.now(), rows: read?.key === key ? read.rows : read?.rows }
    void (async () => {
      try {
        const res = await fetch(`/api/history?desks=${encodeURIComponent(desks)}&since=${span.since}&until=${span.until}`)
        const data = res.ok ? await res.json() : null
        if (ui.history.read?.key === key) {
          ui.history.read = data ? { key, status: 'ready', at: Date.now(), rows: data.rows }
            : { key, status: 'error', at: Date.now(), rows: ui.history.read.rows }
        }
      } catch {
        if (ui.history.read?.key === key) {
          ui.history.read = { ...ui.history.read, status: 'error', at: Date.now() }
        }
      }
      draw()
    })()
  }
  return ui.history.read?.rows ?? null
}

/** Sets one of the column's choices, keeps it for later visits, and starts again from its first page. */
function setHistory(field, value) {
  ui.history[field] = value
  ui.history.page = 0
  remember(`history-${field}`, value)
  draw()
}

/** Sorts by a column, or flips the order when it already sorts by it; time and numbers start newest or largest. */
function sortHistory(key, numeric) {
  const sort = ui.history.sort
  setHistory('sort', sort.key === key ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: numeric ? 'desc' : 'asc' })
}

/** A row's time, as the hour and minute today, or the day, then the hour and minute, before today. */
function historyTime(at) {
  const date = new Date(at)
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return new Date().toDateString() === date.toDateString() ? time
    : `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`
}

/** Everything the History search matches a row by: its title, issue and PR numbers, and project, as lowercase text. */
function historyText(row) {
  return [row.title, row.number, `#${row.number}`, row.pr, `PR #${row.pr}`, row.desk, deskOf(row.desk)?.title]
    .filter(part => part !== undefined).join(' ').toLowerCase()
}

/** The History column's search box, which keeps to the rows of the span shown; Escape clears it. */
function HistorySearch() {
  const set = query => {
    ui.history.query = query
    ui.history.page = 0
    draw()
  }
  return html`<div class="search">
    <div class="search-field">
      <input type="search" placeholder="Search by title, number, or project" value=${ui.history.query}
        aria-label="Search the history" onInput=${event => set(event.target.value)}
        onKeyDown=${event => {
          if (event.key === 'Escape') {
            event.preventDefault()
            set('')
            event.target.blur()
          }
        }} />
      ${ui.history.query && html`<button type="button" class="clear-search" title="Clear the search"
        aria-label="Clear the search" onClick=${event => {
          const box = event.currentTarget.closest('.search-field').querySelector('input')
          set('')
          box.focus()
        }}>
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>`}
    </div>
  </div>`
}

/**
 * The PRs the shown projects merged over a span, with what the runs behind each cost, and a line of totals. The search
 * keeps the rows whose title, numbers, or project hold every word, and the totals add up those. Headings sort. Each
 * row's Number column links its issue, when an agent's branch worked one, and its PR, as the list draws them, and
 * pressing elsewhere on the row opens its card when the board shows one, or else shows the PR in the detail view.
 */
export function History() {
  const h = ui.history
  const all = historyRows()
  const many = shownDesks().length > 1
  const columns = HISTORY_COLUMNS.filter(([key]) => many || key !== 'desk')
  const words = h.query.toLowerCase().split(/\s+/).filter(Boolean)
  const rows = (all ?? []).filter(row => words.length === 0 || words.every(word => historyText(row).includes(word)))
  const { key, dir } = h.sort
  const numeric = HISTORY_COLUMNS.find(([one]) => one === key)?.[2]
  const value = row => (key === 'at' ? Date.parse(row.at) : row[key])
  rows.sort((a, b) => {
    const [x, y] = [value(a), value(b)]
    const order = x === undefined || y === undefined ? (x === undefined) - (y === undefined)
      : numeric ? x - y : String(x).localeCompare(String(y))
    return (dir === 'asc' ? order : -order) || Date.parse(b.at) - Date.parse(a.at)
  })
  const pages = Math.max(1, Math.ceil(rows.length / HISTORY_PAGE))
  const page = Math.min(h.page, pages - 1)
  const shown = rows.slice(page * HISTORY_PAGE, (page + 1) * HISTORY_PAGE)
  const span = `${page * HISTORY_PAGE + 1}–${page * HISTORY_PAGE + shown.length} of ${rows.length}`
  const cost = rows.reduce((sum, row) => sum + (row.cost ?? 0), 0)
  const runs = rows.reduce((sum, row) => sum + row.runs, 0)
  const gpu = gpuTotal(rows)
  const open = row => {
    const card = listedCard(row.desk, row.number)
    if (card) {
      select(card.id)
    } else {
      void visit(row.desk, row.pr)
    }
  }
  const status = ui.history.read?.status
  return html`<aside class="history" aria-label="History">
    <${HistorySearch} />
    <div class="history-head">
      <h2>History</h2>
      <select aria-label="Span" value=${h.range} onChange=${event => setHistory('range', event.target.value)}>
        ${HISTORY_RANGES.map(([id, title]) => html`<option value=${id}>${title}</option>`)}
      </select>
      ${status === 'loading' ? html`<span class="muted">Reading…</span>` : null}
    </div>
    ${h.range === 'custom' ? html`<div class="history-dates">
      <input type="date" aria-label="From" value=${h.from} max=${h.to || undefined}
        onChange=${event => setHistory('from', event.target.value)} />
      <span class="muted">to</span>
      <input type="date" aria-label="To" value=${h.to} min=${h.from || undefined}
        onChange=${event => setHistory('to', event.target.value)} />
    </div>` : null}
    ${all === null ? html`<p class="muted">${historySpan() === null ? 'Pick the first and last day.'
      : status === 'error' ? 'Could not read the history.' : 'Reading the history…'}</p>` : html`
      <p class="history-totals muted">${words.length > 0 ? `${rows.length} of ${all.length}` : rows.length} merged,
        from ${runs} run${runs === 1 ? '' : 's'} ·${' '}<span title=${COST_NOTE}>$${cost.toFixed(0)} at API prices</span>${
        gpu ? html` ·${' '}<span title=${GPU_NOTE}>${gpu}</span>` : null}</p>
      <table class="history-table">
        <thead><tr>${columns.map(([id, title, isNumber]) => html`<th key=${id} class=${id}
          title=${id === 'cost' ? COST_NOTE : undefined}
          aria-sort=${key === id ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
          <button type="button" onClick=${() => sortHistory(id, isNumber)}>${title}${key === id
            ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}</button></th>`)}</tr></thead>
        <tbody>${shown.map(row => html`<tr key=${`${row.kind}:${row.at}:${row.number ?? row.title}`}
          onClick=${() => open(row)} title=${row.title}>
          ${columns.map(([id]) => {
            if (id === 'at') {
              return html`<td class="time">${historyTime(row.at)}</td>`
            }
            if (id === 'desk') {
              return html`<td><${Chip} desk=${row.desk} /></td>`
            }
            if (id === 'number') {
              return html`<td class="number"><${Numbers} issue=${row.issueUrl ? row.number : undefined} pr=${row.pr}
                issueUrl=${row.issueUrl} prUrl=${row.url} /></td>`
            }
            if (id === 'title') {
              return html`<td class="title">${row.title}</td>`
            }
            return html`<td class="cost" title=${costTitle(row)}>${row.cost === undefined
              ? '' : `$${row.cost.toFixed(2)}`}</td>`
          })}
        </tr>`)}</tbody>
      </table>
      ${rows.length === 0 ? html`<p class="muted">${words.length > 0 && all.length > 0
        ? `Nothing in this span matches “${h.query.trim()}”.` : 'Nothing in this span.'}</p>`
        : html`<div class="history-pages">
        <button type="button" class="small" disabled=${page === 0}
          onClick=${() => { ui.history.page = page - 1; draw() }}>Previous</button>
        <span class="muted">${span}</span>
        <button type="button" class="small" disabled=${page >= pages - 1}
          onClick=${() => { ui.history.page = page + 1; draw() }}>Next</button>
      </div>`}`}
  </aside>`
}
