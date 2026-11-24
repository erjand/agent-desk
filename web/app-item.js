// A card's row in the list, and the search bar's Detailed button that switches every row between two views. Compact
// draws a row on one line. Detailed moves its labels, run kind, and note to a second line under its title, which
// app.css lays out from the `detailed` class on the list, so a row's markup stays the same in both views.
import { html } from './vendor/preact-htm.js'
import { dragClass, dragProps } from './app-drag.js'
import { Chip, Icon, iconOf, labelNamed, Numbers, tagsOf } from './app-parts.js'
import { age, draw, pickDetailTab, remember, select, ui } from './app-state.js'

export const DETAILED_TITLE = 'Show or hide a second line under each card for its labels, run kind, and note'

/** Switches the list between Compact and Detailed, and remembers the choice in this browser. */
export function toggleDetailed() {
  ui.detailed = !ui.detailed
  remember('inbox-detailed', ui.detailed)
  draw()
}

/** The button after the search box's `/` hint that switches the list between Compact and Detailed. */
export function DetailedToggle() {
  return html`<button type="button" class="detailed-toggle" aria-pressed=${String(ui.detailed)}
    title=${DETAILED_TITLE} onClick=${toggleDetailed}>Detailed</button>`
}

function timeOf(card) {
  return card.stage === 'needs' && card.since ? age(card.since) : card.time ?? ''
}

/**
 * A card in the list: its icon, its project on All, its number, title, state, and labels, then its note and time. The
 * run kind, labels, and note sit together in `.more`, which Detailed draws as the row's second line.
 */
export function Item({ card, selected }) {
  const [icon, tone] = iconOf(card)
  // A note, such as `checks failed` or `waiting on #295`, sits in a column of its own before the time; one that names
  // a label shows as that label. A review the merge queue stopped on, and a run that stalls, draw the note in the
  // warning tone.
  const note = noteOf(card)
  const warn = (card.stage === 'merging' && card.state === 'stopped') || card.stalled
  const noteClass = warn ? 'note warning' : 'note'
  const classes = ['item', selected ? 'selected' : '', dragClass(card)].filter(Boolean).join(' ')
  // Working names the run's kind, such as `fix` or `check`, but not `agent`, which most of them are.
  const runner = card.runner !== 'agent' ? card.runner : undefined
  const tags = tagsOf(card.desk, card.labels, card.note, { noteApart: true })
  // A card with nothing for the second line marks it empty, so Detailed draws no blank line under its title.
  const more = runner || note || tags.some(Boolean) ? 'more' : 'more empty'
  return html`<div role="option" class=${classes} aria-selected=${String(selected)} data-key=${card.id}
    ...${dragProps(card)} onClick=${() => select(card.id)}>
    <${Icon} name=${icon} tone=${tone} />
    ${ui.tab === 'all' ? html`<span class="chip-cell"><${Chip} desk=${card.desk} /></span>` : null}
    <${Numbers} issue=${card.number !== card.pr ? card.number : undefined} pr=${card.pr} />
    <span class="text" title=${card.title}>${card.title}</span>
    ${card.subs ? html`<${SubsBadge} card=${card} />` : null}
    ${card.state ? html`<span class=${`badge ${card.state}`}>${card.state}</span>` : null}
    <span class=${more}>
      ${runner ? html`<span class="note runner">${runner}</span>` : null}
      <span class="tags">${tags}</span>
      ${note ? html`<span class="note-cell"><span class=${noteClass} title=${note}>${note}</span></span>` : null}
    </span>
    <span class="time">${timeOf(card)}</span>
  </div>`
}

/**
 * A parent issue's sub-issue progress beside its title, as GitHub draws it: a ring that fills as its sub-issues close,
 * and how many are done. Clicking it opens the card's Dependencies tab.
 */
function SubsBadge({ card }) {
  const { done, total } = card.subs
  const turn = 2 * Math.PI * 5
  return html`<button type="button" class=${done === total ? 'subs-badge done' : 'subs-badge'}
    title=${`${done} of ${total} sub-issues done`} aria-label=${`${done} of ${total} sub-issues done`}
    onClick=${event => {
      event.stopPropagation()
      select(card.id)
      pickDetailTab('deps')
    }}>
    <svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">
      <circle cx="7" cy="7" r="5" fill="none" stroke="var(--border)" stroke-width="2" />
      <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="2"
        stroke-dasharray=${`${(turn * done) / total} ${turn}`} transform="rotate(-90 7 7)" />
    </svg>${`${done}/${total}`}</button>`
}

/** A card's note for its own column, or null for none or one that shows as a label. */
export function noteOf(card) {
  return card.note && !labelNamed(card.desk, card.note) ? card.note : null
}
