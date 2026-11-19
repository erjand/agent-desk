// The header's weekly usage limit, beside the mode buttons: the plan's 7-day usage from lib/usage.py's last reading
// while the limit is on, or a small Usage Limit button while it is off, and a popover that turns the limit on or off
// and sets its percentage through the server, which runs `agent-desk usage-limit`'s code. One limit covers every
// project, so it reads and acts the same on every tab.
import { html } from './vendor/preact-htm.js'
import { draw, post, ui } from './app-state.js'

// The percentage the popover offers before a limit was ever set.
const DEFAULT_LIMIT = 80

// What the popover says at or over the limit.
const HELD = 'New agent, chore, and fix runs wait until usage falls under the limit.'

const two = number => String(number).padStart(2, '0')

/** A time in the coming week, in local time, such as `Wed 04:00`, as lib/usage.py writes it; null for no time. */
export function weekAt(at) {
  const date = at instanceof Date ? at : new Date(at)
  if (Number.isNaN(date.getTime())) {
    return null
  }
  return `${date.toLocaleDateString('en-US', { weekday: 'short' })} ${two(date.getHours())}:${two(date.getMinutes())}`
}

/** A reading as a whole percentage, rounded down as lib/usage.py shows it, so one under the limit never reads at it. */
const shown = percent => `${Math.floor(percent)}%`

/** Whether the limit holds new runs: it is on and the reading counts and is at or over it. */
function overLimit(usage) {
  return Boolean(usage?.on) && usage.problem === null && usage.percent !== null && usage.percent >= usage.limit
}

/**
 * What the header's button reads, its tone for the CSS, and its tooltip: Usage Limit while the limit is off, the
 * week's usage and the limit while it is on, such as `Week 41% / 80%`, in the Hold amber at or over the limit, and that
 * it can't read usage while the reading doesn't count.
 */
export function usageView(usage) {
  if (!usage?.on) {
    return { text: 'Usage Limit', tone: 'off', tip: usage?.limit === null || usage?.limit === undefined
      ? 'Set a weekly plan usage limit that holds new runs in every project'
      : `The weekly usage limit is off; on, it holds new runs in every project at ${usage.limit}%` }
  }
  if (usage.problem !== null) {
    return { text: 'Week: can\'t read', tone: 'unread',
      tip: `Can't read weekly plan usage, so new runs start: ${usage.problem}. The limit is ${usage.limit}%.` }
  }
  const resets = usage.resetsAt ? weekAt(usage.resetsAt) : null
  const reset = resets ? `; resets ${resets}` : ''
  const text = `Week ${shown(usage.percent)} / ${usage.limit}%`
  if (overLimit(usage)) {
    return { text, tone: 'over',
      tip: `Weekly plan usage is ${shown(usage.percent)}, at the ${usage.limit}% limit, so new agent, chore, and fix `
        + `runs wait${reset}` }
  }
  return { text, tone: 'under',
    tip: `Weekly plan usage is ${shown(usage.percent)}, under the ${usage.limit}% limit${reset}` }
}

/** The popover's line about the last reading: its percentage, when it was read, and when its window resets. */
export function readingLine(usage) {
  if (usage.percent === null || usage.readAt === null) {
    return 'No reading yet.'
  }
  const resets = usage.resetsAt ? weekAt(usage.resetsAt) : null
  return `Last read ${shown(usage.percent)} at ${weekAt(usage.readAt * 1000)}${resets ? `; the week resets ${resets}`
    : ''}.`
}

/** A typed percentage as a whole number from 0 to 100, or null for anything else. */
export function percentOf(text) {
  const trimmed = String(text ?? '').trim()
  return /^\d{1,3}$/.test(trimmed) && Number(trimmed) <= 100 ? Number(trimmed) : null
}

/**
 * Saves the limit on at the typed percentage, or off, unless the percentage is refused or nothing changed. The header
 * shows the new setting at once, and the server's next state confirms it.
 */
export function saveUsage(on, text) {
  const usage = ui.server?.usage
  if (!usage) {
    return
  }
  const percent = percentOf(text)
  if (on && percent === null) {
    ui.usage.error = 'Enter a whole percentage from 0 to 100.'
    draw()
    return
  }
  ui.usage.error = null
  ui.usage.draft = null
  if (on !== usage.on || (on && percent !== usage.limit)) {
    ui.server.usage = { ...usage, on, limit: on ? percent : usage.limit }
    void post('/api/usage-limit', on ? { on, percent } : { on })
  }
  draw()
}

/**
 * Closes the popover, and with `focus`, gives the keyboard back to its button. A press outside it saves a valid
 * percentage typed and not yet saved, as leaving the box would; Escape drops it.
 */
export function closeUsage(focus = false) {
  if (!ui.usage.open) {
    return
  }
  const { draft } = ui.usage
  if (!focus && draft !== null && ui.server?.usage?.on && percentOf(draft) !== null) {
    saveUsage(true, draft)
  }
  ui.usage = { open: false, draft: null, error: null }
  draw()
  if (focus) {
    document.querySelector('.usage-button')?.focus()
  }
}

/** Closes the popover on a press outside it. */
export function onUsageOutside(event) {
  if (ui.usage.open && !event.target?.closest?.('.usage')) {
    closeUsage()
  }
}

function toggle() {
  if (ui.usage.open) {
    closeUsage()
    return
  }
  ui.usage = { open: true, draft: null, error: null }
  draw()
  setTimeout(() => document.getElementById('usage-on')?.focus())
}

function onKeyDown(event) {
  if (event.key === 'Escape' && ui.usage.open) {
    event.stopPropagation()
    event.preventDefault()
    closeUsage(true)
  }
}

function onFocusOut(event) {
  if (ui.usage.open && event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) {
    closeUsage()
  }
}

/** The popover: the on/off switch, the percentage, which waits while the limit is off, and the last reading. */
export function UsagePopover({ usage }) {
  const draft = ui.usage.draft ?? String(usage.limit ?? DEFAULT_LIMIT)
  const error = ui.usage.error
  return html`<div id="usage-limit" class="usage-pop" role="dialog" aria-label="Weekly usage limit">
    <label class="usage-switch"><input id="usage-on" type="checkbox" role="switch" checked=${usage.on}
      onChange=${event => saveUsage(event.currentTarget.checked, draft)} />
      <span>Hold new runs at a weekly usage limit</span></label>
    <form class="usage-percent" onSubmit=${event => { event.preventDefault(); saveUsage(true, draft) }}>
      <label for="usage-percent">Limit</label>
      <input id="usage-percent" type="number" min="0" max="100" step="1" inputmode="numeric" value=${draft}
        disabled=${!usage.on} aria-invalid=${String(error !== null)}
        aria-describedby=${error === null ? 'usage-scope' : 'usage-error usage-scope'}
        onInput=${event => { ui.usage.draft = event.currentTarget.value; ui.usage.error = null; draw() }}
        onChange=${event => saveUsage(true, event.currentTarget.value)} />
      <span aria-hidden="true">%</span>
    </form>
    ${error === null ? null : html`<p id="usage-error" class="usage-error" role="alert">${error}</p>`}
    <p class="muted">${readingLine(usage)}${usage.on && usage.problem !== null
      ? ` Can't read usage, so new runs start: ${usage.problem}.` : ''}</p>
    ${overLimit(usage) ? html`<p class="usage-held">${HELD}</p>` : null}
    <p id="usage-scope" class="muted">One limit covers every project on this machine.</p>
  </div>`
}

/** The header's control, or none before the server says the limit's state. */
export function UsageLimit() {
  const usage = ui.server?.usage
  if (!usage) {
    return null
  }
  const view = usageView(usage)
  const open = ui.usage.open
  return html`<span class="usage" onKeyDown=${onKeyDown} onFocusOut=${onFocusOut}>
    <button type="button" class=${`usage-button ${view.tone}`} data-tip=${open ? undefined : view.tip}
      aria-description=${view.tip} aria-haspopup="dialog" aria-expanded=${String(open)}
      aria-controls=${open ? 'usage-limit' : undefined}
      onClick=${toggle}>${view.text}</button>
    ${open ? html`<${UsagePopover} usage=${usage} />` : null}
  </span>`
}
