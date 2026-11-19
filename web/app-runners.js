// The Runners sidebar: one table of the shown projects' dispatcher slots under its title, one row per slot with what
// works in it or `free`, and each project's local sessions started with `agent-desk start`, which take no slot, after
// its slots.
import { html } from './vendor/preact-htm.js'
import { Chip, chipWidth, Numbers } from './app-parts.js'
import { deskOf, runnerProjects, select, ui, visit } from './app-state.js'

/** The most characters the Project column gives a chip, so a long project title leaves room for Title. */
export const PROJECT_WIDTH_LIMIT = 12

/**
 * The Project column's width in characters: the chip column's width, as `chipWidth()` measures it, up to
 * PROJECT_WIDTH_LIMIT. app.css clips a longer chip with an ellipsis.
 */
export function projectWidth() {
  return Math.min(chipWidth(), PROJECT_WIDTH_LIMIT)
}

/** A busy row's card id, when it works on an issue or PR of a project shown here. */
export function runnerCard(desk, row) {
  if (row.number === undefined || row.dim) {
    return null
  }
  return `${desk}:${row.issue ?? (row.pr ? `pr-${row.number}` : row.number)}`
}

/**
 * A row's Project cell: none when `chip` is null, as on a project's tab, the project's chip on its group's first row
 * on All, with the project's full title to hover when the column clips it, and an empty cell under it.
 */
export function ProjectCell({ desk, chip }) {
  if (chip === null) {
    return null
  }
  if (!chip) {
    return html`<td class="desk"></td>`
  }
  return html`<td class="desk" title=${deskOf(desk)?.title ?? desk}><${Chip} desk=${desk} /></td>`
}

/**
 * A row's Runner cell: the kind in the attention tone when every slot of it is busy, which tells why a queued request
 * waits, in Working's orange when the slot works, and in gray when it is free.
 */
function RunnerCell({ kind, busy, tone }) {
  return html`<td class=${`runner ${tone === 'full' ? 'full' : busy ? 'working' : ''}`}>${kind}</td>`
}

/**
 * One busy run: its kind, its numbers and title, and how long it has worked, after its project's cell on All. Pressing
 * the row, or its title's button by keyboard, selects its card, or shows its issue or PR in the panel when the list
 * has no card for it.
 */
export function Runner({ kind, row, desk, shown, tone, chip = null }) {
  const id = runnerCard(desk, row)
  const open = () => (id === null ? undefined : shown.has(id) ? select(id) : void visit(desk, row.number))
  const title = row.title ?? row.text.slice(10)
  return html`<tr class=${`runner-row busy ${id === null ? '' : 'pressable'}`} title=${row.text} onClick=${open}>
    <${ProjectCell} desk=${desk} chip=${chip} />
    <${RunnerCell} kind=${kind} busy=${true} tone=${tone} />
    <td class="number">
      <${Numbers} issue=${row.pr ? row.issue : row.number} pr=${row.pr ? row.number : undefined} /></td>
    <td class="title">${id === null ? title : html`<button type="button">${title}</button>`}</td>
    <td class="time">${row.time ?? ''}</td>
  </tr>`
}

/**
 * A slot's row: a busy one with its run, as Runner draws it, or with its kind and `busy` when no board row names the
 * run, and a free one with its kind and a muted `free` in the Title column.
 */
export function SlotRow({ desk, slot, shown, chip = null }) {
  if (slot.row) {
    return html`<${Runner} kind=${slot.kind} row=${slot.row} desk=${desk} shown=${shown} tone=${slot.tone}
      chip=${chip} />`
  }
  return html`<tr class=${`runner-row ${slot.busy ? 'busy' : 'free'}`}>
    <${ProjectCell} desk=${desk} chip=${chip} />
    <${RunnerCell} kind=${slot.kind} busy=${slot.busy} tone=${slot.tone} />
    <td class="number"></td>
    <td class="title muted">${slot.busy ? 'busy' : 'free'}</td>
    <td class="time"></td>
  </tr>`
}

/**
 * The Runners sidebar, in History's column: its title, then one table with a header row. Each shown project's rows sit
 * together in the page's order, a row per slot and then a `local` row per local session, or `no slots` when it has
 * neither. On All a Project column names each group on its first row and a rule separates one group from the next; a
 * project's tab shows one group and leaves both out. `shown` holds the ids of the cards the list shows, which a pressed
 * row selects.
 */
export function Runners({ shown }) {
  const projects = runnerProjects()
  const all = ui.tab === 'all'
  const chipAt = at => (all ? at === 0 : null)
  return html`<aside class="runners-side" aria-label="Runners" style=${{ '--chip-width': `${projectWidth()}ch` }}>
    <div class="side-head"><h2>Runners</h2></div>
    ${projects.length === 0 ? html`<p class="muted">No board has been read yet.</p>` : html`<table
      class=${`runners-table ${all ? 'all' : ''}`}>
      <colgroup>
        ${all ? html`<col class="desk" />` : null}
        <col class="runner" /><col class="number" /><col class="title" /><col class="time" />
      </colgroup>
      <thead><tr>
        ${all ? html`<th class="desk">Project</th>` : null}
        <th class="runner">Runner</th>
        <th class="number">Item</th>
        <th class="title">Title</th>
        <th class="time">Time</th>
      </tr></thead>
      ${projects.map(({ desk, rows, locals }) => html`<tbody key=${desk}>
        ${rows.map((slot, at) => html`<${SlotRow} key=${slot.row?.key ?? `${slot.kind}-${at}`} desk=${desk} slot=${slot}
          shown=${shown} chip=${chipAt(at)} />`)}
        ${locals.map((row, at) => html`<${Runner} key=${row.key} kind="local" row=${row} desk=${desk} shown=${shown}
          chip=${chipAt(rows.length + at)} />`)}
        ${rows.length + locals.length === 0 ? html`<tr class="runner-row free">
          <${ProjectCell} desk=${desk} chip=${chipAt(0)} /><td class="muted" colspan="4">no slots</td>
        </tr>` : null}
      </tbody>`)}
    </table>`}
  </aside>`
}
