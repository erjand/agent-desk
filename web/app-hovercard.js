// The hovercards: the card GitHub shows over a link to an issue or a PR, drawn here for the projects the page shows,
// in a root of its own after the page's.
import { html, render } from './vendor/preact-htm.js'
import { Icon, Label } from './app-parts.js'
import { deskOf, pageItemOf, PREVIEW_MS, ui } from './app-state.js'

// A link to an issue or a PR on GitHub, which a hovercard previews when it is in a project the page shows.
const GITHUB_ITEM = /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/(?:issues|pull)\/(\d+)(?:[/?#]|$)/i
// How long the pointer rests on a link before its hovercard shows, and how long the card stays once the pointer leaves
// both, so the pointer can cross from the link to the card.
const HOVER_SHOW_MS = 400
const HOVER_HIDE_MS = 250
// Each state's icon and tone on a hovercard, as GitHub's.
const HOVER_ICONS = {
  issue: { open: ['issue', 'open'], completed: ['closed', 'done'], not_planned: ['blocked', 'muted'],
    duplicate: ['blocked', 'muted'] },
  pr: { open: ['pr', 'open'], draft: ['pr', 'muted'], merged: ['merged', 'done'], closed: ['pr', 'danger'] },
}
// Each issue's and PR's hovercard, by `<desk>:<number>`: { status, at, data }.
const hovercards = new Map()
export const hoverRoot = document.body.appendChild(document.createElement('div'))
// The link the pointer rests on, with its project and number, and its hovercard once it shows.
let hover = null
let hoverTimer = 0

/**
 * The project and number a link to an issue or a PR names, on the page or on GitHub, when the page shows that project;
 * or null.
 */
function itemOf(anchor) {
  const own = pageItemOf(anchor.href)
  if (own) {
    return { desk: deskOf(own.desk), number: own.number }
  }
  const found = GITHUB_ITEM.exec(anchor.href)
  const desk = found && ui.server?.desks.find(one => one.repo.toLowerCase() === found[1].toLowerCase())
  return desk ? { desk, number: Number(found[2]) } : null
}

/** An issue's or a PR's hovercard, read when first shown and again once PREVIEW_MS old. */
function hovercardOf(desk, number) {
  const id = `${desk}:${number}`
  const kept = hovercards.get(id)
  if (kept === undefined || (kept.status !== 'loading' && Date.now() - kept.at > PREVIEW_MS)) {
    hovercards.set(id, { status: 'loading', at: Date.now(), data: kept?.data })
    void (async () => {
      try {
        const res = await fetch(`/api/hovercard?desk=${encodeURIComponent(desk)}&number=${number}`)
        if (!res.ok) {
          throw new Error(String(res.status))
        }
        hovercards.set(id, { status: 'ready', at: Date.now(), data: await res.json() })
      } catch {
        hovercards.set(id, { status: 'error', at: Date.now(), data: kept?.data })
      }
      drawHovercard()
    })()
  }
  return hovercards.get(id)
}

/** When an issue or a PR opened, as GitHub's hovercard dates it: the year only when it isn't this one. */
function openedOn(at) {
  const date = new Date(at)
  return date.toLocaleDateString([], { month: 'short', day: 'numeric',
    ...(date.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' }) })
}

/** The card GitHub shows over a link to an issue or a PR: its project and date, state and title, text, and labels. */
function Hovercard({ desk, number, anchor }) {
  const kept = hovercardOf(desk.id, number)
  const card = kept.data
  const box = anchor.getBoundingClientRect()
  // The card sits under the link, or over it when the link is in the bottom third of the window.
  const place = box.bottom > window.innerHeight * 2 / 3 ? { bottom: `${window.innerHeight - box.top + 8}px` }
    : { top: `${box.bottom + 8}px` }
  const left = `${Math.max(8, Math.min(box.left, window.innerWidth - 360 - 8))}px`
  const [icon, tone] = card ? HOVER_ICONS[card.kind][card.state] ?? HOVER_ICONS[card.kind].open : []
  return html`<div class="hovercard" role="tooltip" style=${{ ...place, left }}
    onMouseEnter=${() => clearTimeout(hoverTimer)} onMouseLeave=${hideHovercardSoon}>
    ${card ? html`
      <div class="hc-context muted">${desk.repo} on ${openedOn(card.at)}</div>
      <div class="hc-title"><${Icon} name=${icon} tone=${tone} />
        <a href=${anchor.href} target="_blank" rel="noopener"><strong>${card.title}</strong>
          <span class="muted"> #${card.number}</span></a></div>
      ${card.excerpt ? html`<p class="hc-excerpt">${card.excerpt}</p>` : null}
      ${card.labels.length > 0 ? html`<div class="labels">${card.labels.map(label =>
        html`<${Label} key=${label.name} label=${label} />`)}</div>` : null}`
      : html`<span class="muted">${kept.status === 'error' ? `Could not read #${number}.`
        : `Reading #${number}…`}</span>`}
  </div>`
}

function drawHovercard() {
  render(hover?.shown && hover.anchor.isConnected ? html`<${Hovercard} ...${hover} />` : null, hoverRoot)
}

export function hideHovercard() {
  clearTimeout(hoverTimer)
  if (hover !== null) {
    hover = null
    drawHovercard()
  }
}

function hideHovercardSoon() {
  clearTimeout(hoverTimer)
  hoverTimer = setTimeout(hideHovercard, HOVER_HIDE_MS)
}

/** Shows a link's hovercard once the pointer rests on it, and keeps the card while the pointer is on its link. */
export function onLinkOver(event) {
  const anchor = event.target.closest?.('a[href]')
  if (!anchor || hoverRoot.contains(anchor)) {
    return
  }
  if (anchor === hover?.anchor) {
    if (hover.shown) {
      clearTimeout(hoverTimer)
    }
    return
  }
  const item = itemOf(anchor)
  if (item === null) {
    return
  }
  clearTimeout(hoverTimer)
  // A pointer that moves from one link to another while a hovercard shows swaps the card at once.
  const next = { ...item, anchor, shown: hover?.shown ?? false }
  hover = next
  if (next.shown) {
    drawHovercard()
  } else {
    hoverTimer = setTimeout(() => {
      next.shown = true
      drawHovercard()
    }, HOVER_SHOW_MS)
  }
}

/** Hides the hovercard once the pointer leaves its link for anywhere but the card, and forgets a card not yet shown. */
export function onLinkOut(event) {
  const anchor = event.target.closest?.('a[href]')
  if (anchor && anchor === hover?.anchor && !anchor.contains(event.relatedTarget)
    && !hoverRoot.contains(event.relatedTarget)) {
    if (hover.shown) {
      hideHovercardSoon()
    } else {
      hideHovercard()
    }
  }
}
