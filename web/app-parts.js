// The drawing pieces the list, the Runners sidebar, the card detail, the History column, and the hovercards share:
// a project's chip and its column's width, the icons, an issue's and PR's numbers, the labels in their GitHub colors,
// a card's tags, and the label picker, and a body's cleaned HTML beside its writer's initial.
import { html, useEffect, useRef, useState } from './vendor/preact-htm.js'
import { clickedItem, deskOf, PROJECT_HUES, ui, visit } from './app-state.js'

/**
 * A project's hue, as the index of its --project-<n> color in app.css, by its place on the page: the first six
 * projects each take their own, and later ones repeat them. A project the page doesn't list has none, as -1.
 */
export function hueOf(desk) {
  const index = (ui.server?.desks ?? []).findIndex(one => one.id === desk)
  return index < 0 ? -1 : index % PROJECT_HUES
}

export function Chip({ desk }) {
  const hue = hueOf(desk)
  const title = deskOf(desk)?.title ?? desk
  return html`<span class=${hue < 0 ? 'chip' : `chip project-${hue}`}>${title}</span>`
}

/**
 * A card's numbers in their column: its issue's in gray, then its PR's in green after an arrow when it has both, as
 * `#348 → #349`. A PR without an issue, as a chore's, shows its own number alone. Given the page's link to its item, a
 * number shows the item in the detail view, or with a modifier opens the page at it in a new tab, and pressing the link
 * leaves the press on the row around it alone.
 */
export function Numbers({ issue, pr, issueUrl, prUrl }) {
  const follow = event => {
    event.stopPropagation()
    const item = clickedItem(event, event.currentTarget.href)
    if (item) {
      event.preventDefault()
      void visit(item.desk, item.number)
    }
  }
  const link = (href, title, cls, text) => (href
    ? html`<a class=${cls} href=${href} target="_blank" rel="noopener" title=${title} onClick=${follow}>${text}</a>`
    : html`<span class=${cls}>${text}</span>`)
  return html`<span class="num">
    ${issue !== undefined ? link(issueUrl, `Show issue #${issue}`, undefined, `#${issue}`) : null}
    ${issue !== undefined && pr !== undefined ? html`<span class="arrow">→</span>` : null}
    ${pr !== undefined ? link(prUrl, `Show PR #${pr}`, 'pr', `#${pr}`) : null}
  </span>`
}

// Each icon's strokes on a 16-pixel square, drawn in the color of the text around it, after GitHub's octicons.
const ICONS = {
  issue: html`<circle cx="8" cy="8" r="6.25" /><circle cx="8" cy="8" r="1.25" fill="currentColor" stroke="none" />`,
  pr: html`<circle cx="4" cy="3.5" r="1.75" /><circle cx="4" cy="12.5" r="1.75" /><circle cx="12" cy="12.5" r="1.75" />
    <path d="M4 5.25v5.5M12 10.75V6.5a2 2 0 0 0-2-2H7M8.75 2.75 7 4.5l1.75 1.75" />`,
  merged: html`<circle cx="4" cy="3.5" r="1.75" /><circle cx="4" cy="12.5" r="1.75" /><circle cx="12" cy="9" r="1.75" />
    <path d="M4 5.25v5.5M4 5.25c0 2.5 2.5 3.75 6.25 3.75" />`,
  closed: html`<circle cx="8" cy="8" r="6.25" /><path d="M5.5 8.25 7.25 10l3.25-3.75" />`,
  failed: html`<circle cx="8" cy="8" r="6.25" /><path d="M5.75 5.75l4.5 4.5M10.25 5.75l-4.5 4.5" />`,
  stopped: html`<circle cx="8" cy="8" r="6.25" /><path d="M6.5 5.5v5M9.5 5.5v5" />`,
  working: html`<circle cx="8" cy="8" r="6.25" /><circle cx="8" cy="8" r="3" fill="currentColor" stroke="none" />`,
  waiting: html`<circle cx="8" cy="8" r="6.25" /><path d="M8 4.75V8l2.25 1.5" />`,
  blocked: html`<circle cx="8" cy="8" r="6.25" /><path d="M3.6 3.6l8.8 8.8" />`,
  refresh: html`<path d="M13.25 8a5.25 5.25 0 1 1-1.54-3.71l1.54 1.46" /><path d="M13.25 2.5v3.25H10" />`,
  idea: html`<path d="M8 1.75a4.25 4.25 0 0 0-2.5 7.7v1.8h5v-1.8A4.25 4.25 0 0 0 8 1.75zM6 13.75h4" />`,
  backlog: html`<rect x="2" y="2.75" width="12" height="3.5" rx="0.75" />
    <path d="M3.25 6.25v6.5a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1v-6.5M6.5 9h3" />`,
  trash: html`<path d="M6.25 1.75h3.5M2.75 4h10.5M4.25 4l.6 8.8a1.25 1.25 0 0 0 1.25 1.2h3.8a1.25 1.25 0 0 0 1.25-1.2l.6-8.8
    M6.75 6.75v4.5M9.25 6.75v4.5" />`,
}

export function Icon({ name, tone }) {
  return html`<svg class=${`icon ${tone}`} viewBox="0 0 16 16" width="16" height="16" fill="none"
    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true">${ICONS[name]}</svg>`
}

/** A card's icon and its tone: what kind of work it is, and how it stands. */
export function iconOf(card) {
  if (card.chore) {
    return ['waiting', 'attention']
  }
  if (card.stage === 'needs') {
    return { review: ['pr', 'open'], conflicts: ['pr', 'danger'], failing: ['pr', 'danger'], draft: ['pr', 'muted'],
      stopped: ['stopped', 'attention'], failed: ['failed', 'danger'], 'timed out': ['stopped', 'attention'],
      merged: ['closed', 'done'] }[card.state]
      ?? ['issue', 'open']
  }
  if (card.stage === 'merging' && card.state === 'stopped') {
    return ['stopped', 'attention']
  }
  return { working: ['working', 'runners'], merging: ['pr', 'merging'], queued: ['waiting', 'attention'], design: ['issue', 'design'],
    blocked: ['blocked', 'muted'], open: ['issue', 'open'], backlog: ['backlog', 'backlog'],
    idea: ['idea', 'ideas'], merged: ['merged', 'done'] }[
    card.stage] ?? ['issue', 'open']
}

/** A label by name in its project's GitHub color, or GitHub's gray for one the project doesn't list. */
export function labelOf(desk, name) {
  return { name, color: deskOf(desk)?.labels?.[name] ?? 'ededed' }
}

// The labels a card in the list leaves off, since its group says as much.
const FLOW_LABELS = new Set(['agent-ready', 'in-progress', 'waiting', 'needs-design', 'idea', 'backlog'])

/**
 * A card's or runner's labels beside its title, in their GitHub colors, then its note. A note that names a label, as
 * `serial` and `on hold` do, shows as that label.
 */
export function tagsOf(desk, labels = [], note, { noteApart = false } = {}) {
  const noteLabel = labelNamed(desk, note)
  const shown = labels.filter(name => !FLOW_LABELS.has(name))
  const names = noteLabel && !shown.includes(noteLabel) ? [...shown, noteLabel] : shown
  return [...names.map(name => html`<${Label} key=${name} label=${labelOf(desk, name)} />`),
    note && !noteLabel && !noteApart ? html`<span class="note" key="note">${note}</span>` : null]
}

/** The label a note names, as `on hold` names `on-hold`, when the project has one; or null. */
export function labelNamed(desk, note) {
  const named = note?.replace(/ /g, '-')
  return named && deskOf(desk)?.labels?.[named] !== undefined ? named : null
}

/** The width of the list's project column: the longest project title, with room for the chip's border. */
export function chipWidth() {
  return Math.max(0, ...(ui.server?.desks ?? []).map(desk => desk.title.length)) + 3
}

/**
 * A label in its GitHub color. The label hands its color to app.css as red, green, and blue and as hue, saturation, and
 * lightness, and app.css draws it as GitHub does in each theme.
 */
export function Label({ label }) {
  const value = parseInt(label.color, 16)
  const [r, g, b] = [value >> 16, (value >> 8) & 255, value & 255]
  const [high, low] = [Math.max(r, g, b) / 255, Math.min(r, g, b) / 255]
  const l = (high + low) / 2
  const chroma = high - low
  const sat = chroma === 0 ? 0 : chroma / (1 - Math.abs(2 * l - 1))
  const hue = chroma === 0 ? 0 : high === r / 255 ? 60 * (((g - b) / 255 / chroma + 6) % 6)
    : high === g / 255 ? 60 * ((b - r) / 255 / chroma + 2) : 60 * ((r - g) / 255 / chroma + 4)
  return html`<span class="label" style=${{ '--label-r': r, '--label-g': g, '--label-b': b,
    '--label-h': Math.round(hue), '--label-s': Math.round(sat * 100), '--label-l': Math.round(l * 100) }}>
    ${label.name}</span>`
}

// The labels the New Issue picker never offers: Agent-Ready, which hands the issue to an agent, and in-progress, which
// a run sets as it claims an issue.
export const UNOFFERED = new Set(['agent-ready', 'in-progress'])
// The labels the detail view's picker never offers: UNOFFERED, and the other labels the store keeps as an issue's
// workflow state in place of a label, so the picker could never show one pressed or take it off. The card's buttons
// and the agent's runs move those, and item-edit.ts refuses them.
export const STATE_LABELS = new Set([...UNOFFERED, 'waiting', 'needs-design', 'on-hold'])

/**
 * The project's labels a picker offers, by name, with any label picked or typed, leaving out those in `left`, sorted
 * by name.
 */
export function offeredLabels(projectLabels, picked, left = UNOFFERED) {
  return [...new Set([...Object.keys(projectLabels ?? {}), ...picked])].filter(name => !left.has(name))
    .sort((a, b) => a.localeCompare(b))
}

/**
 * A label picker: project `desk`'s labels in their colors, those in `picked` pressed, each pressed to call `onToggle`
 * with its name, and a box holding `typed` that adds a new one through `onAdd`, on Enter or its Add button. `left`
 * names the labels it leaves out, and `onKeyDown` takes the keys pressed anywhere in it.
 */
export function LabelPicker({ desk, picked, left, typed, disabled, onToggle, onType, onAdd, onKeyDown }) {
  const names = offeredLabels(deskOf(desk)?.labels, picked, left)
  return html`<div class="label-picker" onKeyDown=${onKeyDown}>
    ${names.length > 0 ? html`<div class="labels">${names.map(name => html`<button type="button" key=${name}
      class="label-pick" aria-pressed=${String(picked.includes(name))} disabled=${disabled}
      onClick=${() => onToggle(name)}><${Label} label=${labelOf(desk, name)} /></button>`)}</div>`
      : html`<p class="muted">This project has no labels yet.</p>`}
    <div class="label-new">
      <input type="text" placeholder="New label" aria-label="New label" value=${typed} disabled=${disabled}
        onInput=${event => onType(event.target.value)}
        onKeyDown=${event => {
          if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey) {
            event.preventDefault()
            onAdd()
          }
        }} />
      <button type="button" class="small" disabled=${disabled} onClick=${onAdd}>Add</button>
    </div>
  </div>`
}

/**
 * A body's HTML, kept to what a description shows: the server's renderer cleans it already, and this drops anything
 * that runs code or leaves for an address other than a web page, the server's own picture route, or a link on the
 * page, besides.
 */
export function clean(markup) {
  const doc = new DOMParser().parseFromString(markup, 'text/html')
  doc.querySelectorAll('script, style, iframe, object, embed, form, link, meta, base, frame, frameset')
    .forEach(node => node.remove())
  for (const node of doc.body.querySelectorAll('*')) {
    for (const { name, value } of [...node.attributes]) {
      const lower = name.toLowerCase()
      const address = ['href', 'src', 'action', 'formaction', 'xlink:href', 'poster'].includes(lower)
      if (lower.startsWith('on') || lower === 'style' || lower === 'srcset'
        || (address && !/^(https?:|\/api\/asset[?/]|#)/i.test(value.trim()))) {
        node.removeAttribute(name)
      }
    }
    if (node.tagName === 'A') {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener')
    }
  }
  return doc.body.innerHTML
}

/**
 * The picture a click on a body opens in the viewer: a plain left click on a picture the renderer wrapped in a link to
 * itself, as `image` in markdown.ts writes it. A click with a modifier key, or with another button, goes to the browser,
 * which opens the link in a new tab or window.
 */
export function viewedPicture(event) {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey
    || event.altKey) {
    return null
  }
  const link = event.target?.closest?.('a.picture-link')
  const picture = link?.querySelector('img')
  return picture ? { link, src: picture.getAttribute('src'), alt: picture.getAttribute('alt') ?? '' } : null
}

/** Opens the picture `event` clicked in the viewer through `show`, in place of following its link. */
export function openPicture(event, show) {
  const picture = viewedPicture(event)
  if (picture) {
    event.preventDefault()
    show(picture)
  }
}

/**
 * A picture over the page at its natural size, shrunk to fit the window. Escape, the close button, or a click outside
 * the picture closes it and returns focus to the link the picture opened from. Keys stay inside it, so the page's own
 * keys, such as Escape closing the issue, wait until it closes.
 */
function PictureViewer({ picture, onClose }) {
  const dialog = useRef(null)
  useEffect(() => dialog.current.showModal(), [])
  const closed = () => {
    onClose()
    picture.link.focus()
  }
  return html`<dialog ref=${dialog} class="picture-viewer" aria-label=${picture.alt || 'Picture'} onClose=${closed}
    onKeyDown=${event => event.stopPropagation()}
    onClick=${event => event.target === dialog.current && dialog.current.close()}>
    <img src=${picture.src} alt=${picture.alt} />
    <button type="button" class="picture-close" title="Close (Esc)" aria-label="Close"
      onClick=${() => dialog.current.close()}>
      <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"
        stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>
  </dialog>`
}

export function Markup({ markup, empty = 'No description provided.' }) {
  const [picture, setPicture] = useState(null)
  if (!markup.trim()) {
    return html`<p class="muted"><em>${empty}</em></p>`
  }
  return html`<div class="markdown" onClick=${event => {
    openPicture(event, setPicture)
    followAnchor(event)
  }}
    dangerouslySetInnerHTML=${{ __html: markup }} />
    ${picture ? html`<${PictureViewer} picture=${picture} onClose=${() => setPicture(null)} />` : null}`
}

/**
 * Follows a plain click on a link to a place in the conversation, as an answer comment's `#issue-body` links its
 * Decisions Needed section, by scrolling to it in place of opening a tab. A link to an item, `#<project>:<number>`,
 * goes to the page's own handler.
 */
function followAnchor(event) {
  const link = event.defaultPrevented ? null : event.target?.closest?.('a[href^="#"]')
  const anchor = link?.getAttribute('href').slice(1)
  if (anchor && /^[\w-]+$/.test(anchor)) {
    event.preventDefault()
    scrollToAnchor(link, anchor)
  }
}

/** Scrolls the conversation around `from` to the element with `anchor`: a Decisions Needed section, else a comment. */
export function scrollToAnchor(from, anchor) {
  const timeline = from.closest('.timeline')
  const target = timeline?.querySelector(`.decisions[data-anchor="${anchor}"]`)
    ?? timeline?.querySelector(`[data-anchor="${anchor}"]`)
  target?.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

/** What a comment's 👀 says: that the agent picked the comment up in run `run`. */
function reactionTitle(run) {
  return `The agent picked this up in run ${run}.`
}

/**
 * The 👀 under a comment that sent work to the agent once the dispatcher asked for its run, as the GitHub agent
 * workflow's reaction showed it; nothing under any other comment.
 */
export function Reaction({ run }) {
  if (run === undefined) {
    return null
  }
  const said = reactionTitle(run)
  return html`<div class="reactions"><span class="reaction" role="img" title=${said} aria-label=${said}>👀</span></div>`
}

/** How many --avatar-<n> colors app-detail.css defines. */
const AVATAR_HUES = 8

/**
 * An author's circle: the first letter or digit of their login, uppercased, or ? for a login with neither, and the
 * index of its --avatar-<n> color, which an FNV-1a hash of the login picks, so a login keeps its color everywhere.
 */
export function avatarOf(login) {
  const initial = /[\p{L}\p{N}]/u.exec(login)?.[0].toUpperCase() ?? '?'
  let hash = 0x811c9dc5
  for (const char of login) {
    hash = Math.imul(hash ^ char.codePointAt(0), 0x01000193)
  }
  return { initial, hue: (hash >>> 0) % AVATAR_HUES }
}

/** An author's initial in a circle of their color, hidden from screen readers since their name follows it. */
export function Avatar({ person, size = 40 }) {
  const { initial, hue } = avatarOf(person.login)
  const style = { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.45)}px` }
  return html`<span class=${`avatar avatar-${hue}`} aria-hidden="true" style=${style}>${initial}</span>`
}
