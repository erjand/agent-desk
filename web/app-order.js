// The Sort buttons on the headings of the groups whose order a person sets by hand. Queued's sorts each shown project's
// start order into its default: the issues that can start now first, blockers ahead of what they block, then by
// number, through `agent-desk queue order --sort`. Open's, Blocked's, Needs Design's, Backlog's, and Ideas' put each
// shown project's issues in the group back in number order, highest first, through `agent-desk list order --reset`. A
// sort replaces any order set by hand, so each button asks to confirm first in its heading, and the list shows the new
// order once the server reads the board again.
import { html } from './vendor/preact-htm.js'
import { inListOrder, inStartOrder } from './app-drag.js'
import { draw, post, ready, shownDesks, STAGE_NAMES, ui } from './app-state.js'

export const SORT_TITLE = 'Sort the start order: startable issues first, blockers ahead of what they block, then by '
  + 'number'

/** The groups with a Sort button: Queued, and the groups that list their issues in the list order. */
export const SORTED = new Set(['queued', 'open', 'blocked', 'design', 'backlog', 'idea'])

/** The Sort button's tooltip on a group's heading. */
export function sortTitle(stage) {
  return stage === 'queued' ? SORT_TITLE
    : `Sort ${STAGE_NAMES[stage]} by number, highest first, replacing the order set by hand`
}

/** What a group's confirm asks before its sort runs. */
function question(stage) {
  return stage === 'queued' ? 'Replace the start order?' : `Sort ${STAGE_NAMES[stage]} by number?`
}

/** Whether a card holds a place in `stage`'s order: the start order in Queued, and the list order elsewhere. */
function inOrderOf(stage, card) {
  return card.stage === stage && (stage === 'queued' ? inStartOrder(card) : inListOrder(card))
}

/** The shown projects whose order in a group a sort could change: those with two or more of their issues in it. */
export function sortableDesks(stage = 'queued') {
  return shownDesks().filter(ready).filter(desk => desk.cards.filter(card => inOrderOf(stage, card)).length >= 2)
}

function asking(stage) {
  ui.sortConfirm = stage
  draw()
}

/**
 * Sorts the order in `stage`'s group of each shown project with one to sort; a refused sort toasts after its project's
 * name.
 */
export async function sortGroup(stage = 'queued') {
  const desks = sortableDesks(stage)
  asking(null)
  await Promise.all(desks.map(desk => (stage === 'queued' ? post('/api/sort-order', { desk: desk.id }, desk.title)
    : post('/api/sort-list', { desk: desk.id, group: stage }, desk.title))))
}

/**
 * A group's Sort button, or the confirm that takes its place once pressed; nothing while no shown project has two or
 * more issues in the group's order.
 */
export function GroupSort({ stage = 'queued' } = {}) {
  if (!SORTED.has(stage) || sortableDesks(stage).length === 0) {
    return null
  }
  if (ui.sortConfirm === stage) {
    return html`<span class="order-sort">
      <span class="muted">${question(stage)}</span>
      <button type="button" class="small primary" onClick=${() => void sortGroup(stage)}>Sort</button>
      <button type="button" class="small" onClick=${() => asking(null)}>Cancel</button>
    </span>`
  }
  const title = sortTitle(stage)
  return html`<button type="button" class="sort" title=${title} aria-label=${title}
    onClick=${() => asking(stage)}>Sort</button>`
}
