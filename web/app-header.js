// The page's header: its name, when the boards last read, Refresh, the buttons that set which new runs the runners
// take, app-usage.js's weekly usage limit, a row at the right end with the machine's gauges in a pill, the History,
// Runners, and Chat buttons, and the help button, the restart notice under that row, then the notices and the project
// tabs. The gauges pill opens app-resources.js's Resources view in the panel, and the row's New Issue button opens
// app-new-issue.js's form there.
import { html } from './vendor/preact-htm.js'
import { openNewIssue } from './app-new-issue.js'
import { hueOf, Icon } from './app-parts.js'
import { toggleResources } from './app-resources.js'
import { deskOf, draw, pickTab, post, ready, shownDesks, toggleSide, ui } from './app-state.js'
import { UsageLimit } from './app-usage.js'

// The page's own colors for the gauges' healthy green, warning, and red, which the server sends in the pane's colors.
const GAUGE_COLORS = { '#5f875f': 'var(--gauge-ok)', warning: 'var(--warning)', '#ff3b30': 'var(--error)' }

/** The gauges in groups the header splits with dividers: memory and swap, then CPU, then each GPU's use and memory. */
export function gaugeGroups(gauges) {
  const kind = one => (/^(RAM|swap)\b/.test(one.text) ? 0 : /^CPU\b/.test(one.text) ? 1 : 2)
  return [0, 1, 2].map(which => gauges.filter(one => kind(one) === which)).filter(group => group.length > 0)
}

/**
 * The machine's gauges in their groups and colors, in a pill the header bar draws beside the History button whichever
 * sidebar shows, or none before the server sends any. Pressing it shows the Resources view in the panel, or closes it.
 */
function Gauges() {
  const groups = gaugeGroups(ui.server?.gauges ?? [])
  if (groups.length === 0) {
    return null
  }
  return html`<button type="button" class="gauges" title="Show what uses the machine's resources"
    aria-pressed=${String(ui.resources.open)} onClick=${() => void toggleResources()}>${groups.flatMap((group, index) => [
    index > 0 ? html`<span class="divider" aria-hidden="true">|</span>` : null,
    ...group.map(one => html`<span style=${{ color: GAUGE_COLORS[one.color] ?? one.color }}>${one.text}</span>`)])}
  </button>`
}

/** When the desk started holding or pausing, as the local time of day. */
function sinceOf(stamp) {
  const at = stamp ? new Date(stamp.replace(/([+-]\d\d)(\d\d)$/, '$1:$2')) : null
  const time = at && !Number.isNaN(at.getTime()) && at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return time ? ` since ${time}` : ''
}

// The modes new runs start in, each with its name and what it does: Auto starts every run, Manual those you press
// Start on, and Hold none.
export const MODES = [
  ['running', 'Auto', 'Start every new run as runners free up, with chores and conflict fixes'],
  ['manual', 'Manual', 'Start agents, fixes, and chores only when you press Start on them in Queued'],
  ['held', 'Hold', 'Let running agents and chores finish, and start no new runs'],
]

// The room a button's tooltip keeps from the window's right edge, as the bar's padding and app.css set it.
const TIP_MARGIN = 16

/** The room for a tooltip from its button's left edge to the right margin of a window `width` pixels wide. */
export function tipRoom(left, width) {
  return `${Math.round(width - TIP_MARGIN - left)}px`
}

/**
 * Gives the button with a tooltip under the pointer or the keyboard focus its tooltip's room, which app.css shifts the
 * tooltip left by as much as it overflows.
 */
export function placeTip(event) {
  const button = event.target.closest?.('button[data-tip]')
  if (button) {
    button.style.setProperty('--tip-room', tipRoom(button.getBoundingClientRect().left,
      document.documentElement.clientWidth))
  }
}

/** A project's mode: its own mode or the desk's. */
export function modeOf(hold, desk) {
  return hold.projects?.[desk.repo] ?? hold.state
}

/**
 * Which new runs the dispatcher starts, and the buttons that change it: Auto, Manual, and Hold, for the project whose
 * tab shows or, on All, for every project. Resume goes back to the mode Hold left, each project's own.
 */
function HoldControls() {
  const hold = ui.server?.hold ?? { state: 'running', before: 'running', projects: {} }
  const desk = ui.tab === 'all' ? undefined : deskOf(ui.tab)
  const set = state => void post('/api/hold', { state, ...(desk && state !== 'resume' ? { desk: desk.id } : {}) })
  const back = MODES.find(([state]) => state === hold.before)?.[1] ?? 'Auto'
  const resume = html`<button type="button" class="primary" title=${`Go back to ${back}`}
    onClick=${() => set('resume')}>Resume</button>`
  // On a project's tab the buttons show and change its mode; on All, the mode every project shares, if they do.
  const modes = new Set(desk ? [modeOf(hold, desk)] : (ui.server?.desks ?? []).map(one => modeOf(hold, one)))
  const current = modes.size === 1 ? [...modes][0] : null
  const where = desk ? ` in ${desk.title}` : ' in every project'
  const held = current === 'held'
  return html`<span class="hold">
    <span class="modes" role="group" aria-label=${`New runs${where}`}>${MODES.map(([state, name, title]) => html`
      <button type="button" key=${state} data-mode=${state} data-tip=${`${title}${where}`}
        aria-description=${`${title}${where}`} aria-pressed=${String(current === state)}
        onClick=${() => current !== state && set(state)}>${name}</button>`)}</span>
    ${held ? html`<span class="hold-state held">Holding new runs${desk && hold.projects?.[desk.repo]
      ? '' : sinceOf(hold.since)}</span>` : null}
    ${hold.state === 'held' && !desk ? resume : null}</span>`
}

/**
 * The notices under the header's bar, each text once with its tone. A store the server can't read says why, since no
 * change shows until it can. A project's board that couldn't be read says why. A project's notice rows, such as a
 * merge queue stopped on uncommitted changes in Needs You or main ahead of origin in Merging, follow in their tone. A
 * server that runs older code than its checkout holds says so under the gauges' row instead.
 */
export function headerNotices(shown) {
  return new Map([
    ...ui.server?.store ? [[`The store can't be read: ${ui.server.store}. Changes show again once it can.`, 'error']]
      : [],
    ...shown.flatMap(desk => [
      ...desk.board?.error ? [[`${desk.title}: ${desk.board.error}`, 'error']] : [],
      ...(ready(desk) ? [...desk.board.needs, ...desk.board.merging ?? []] : []).filter(row => row.notice)
        .map(row => [`${desk.title}: ${row.text}`, row.notice])])])
}

/** The page's header; New Issue starts its project picker on `selected`, the selected card's project, on All. */
export function Header({ selected } = {}) {
  const desks = ui.server?.desks ?? []
  const shown = shownDesks()
  const stamps = shown.map(desk => desk.board?.at).filter(Boolean).sort()
  const busy = shown.some(desk => desk.refreshing)
  const total = desks.reduce((sum, desk) => sum + (ready(desk) ? desk.board.needs.length : 0), 0)
  document.title = `${total > 0 ? `(${total}) ` : ''}agent-desk`
  const notices = headerNotices(shown)
  const tabs = [{ id: 'all', title: 'All', count: 0 },
    ...desks.map(desk => ({ id: desk.id, title: desk.title, count: ready(desk) ? desk.board.needs.length : 0 }))]
  return html`<header>
    <div class="bar">
      <strong class="name">agent-desk</strong>
      <button type="button" class=${busy ? 'refresh spinning' : 'refresh'} aria-label="Refresh" data-tip="Refresh"
        disabled=${busy || !ui.connected}
        onClick=${() => void post('/api/refresh', ui.tab === 'all' ? {} : { desk: ui.tab })}>
        <${Icon} name="refresh" tone="" /></button>
      <span class=${ui.connected ? 'muted' : 'offline'}>${!ui.connected ? 'Disconnected; reconnecting…'
        : stamps.length > 0 ? `Updated ${stamps.at(-1)}` : 'Loading…'}</span>
      <${HoldControls} />
      <${UsageLimit} />
      <span class="meters">
        <span class="side-row">
          <${Gauges} />
          <button type="button" title="File a new issue in a project" disabled=${desks.length === 0}
            onClick=${() => openNewIssue(selected)}>
            New Issue <kbd>i</kbd></button>
          <button type="button" aria-pressed=${String(ui.side === 'history')} title="Show or hide the History column"
            onClick=${() => { toggleSide('history'); draw() }}>
            History <kbd>h</kbd></button>
          <button type="button" aria-pressed=${String(ui.side === 'runners')} title="Show or hide the Runners column"
            onClick=${() => { toggleSide('runners'); draw() }}>
            Runners <kbd>${'\\'}</kbd></button>
          <button type="button" aria-pressed=${String(ui.side === 'chat')}
            title="Show or hide the chat with Claude Code about the desk"
            onClick=${() => { toggleSide('chat'); draw() }}>
            Chat <kbd>${'`'}</kbd></button>
          <button type="button" class="help-button" title="Keyboard shortcuts" aria-label="Keyboard shortcuts"
            onClick=${() => { ui.help = !ui.help; draw() }}>?</button>
        </span>
        ${ui.server?.code ? html`<span class="code-notice warning" role="status">${ui.server.code}</span>` : null}
      </span>
    </div>
    ${[...notices].map(([text, kind]) => html`<p key=${text} class=${`notice ${kind}`}>${text}</p>`)}
    <nav aria-label="Projects">${tabs.map(one => {
      // An open project's tab shows its chip's hue in its underline and tint.
      const hue = one.id === 'all' ? -1 : hueOf(one.id)
      return html`<button type="button" aria-current=${String(one.id === ui.tab)}
        style=${hue < 0 ? undefined : { '--tab-hue': `var(--project-${hue})` }} onClick=${() => pickTab(one.id)}>
        ${one.title}${tabMode(one.id)}
        ${one.count > 0 ? html`<span class="count" title="Needs You">${one.count}</span>` : null}</button>`
    })}</nav>
  </header>`
}

/** A project tab's mode beside its title. All shows none. */
export function tabMode(id) {
  const hold = ui.server?.hold
  const desk = id === 'all' ? undefined : deskOf(id)
  if (!hold || !desk) {
    return null
  }
  const mode = modeOf(hold, desk)
  const name = MODES.find(([state]) => state === mode)?.[1] ?? mode
  return html`<span class=${`tab-mode ${mode}`}>${name}</span>`
}
