// The Runners sidebar: one table of the shown projects' dispatcher slots under its title, rows with the steppers that
// set each project's agent and gpu slots, one row per slot with what works in it or `free`, and each project's local
// sessions started with `agent-desk start`, which take no slot, after its slots.
import { html } from './vendor/preact-htm.js'
import { Chip, chipWidth, Numbers } from './app-parts.js'
import { deskOf, draw, post, runnerProjects, select, ui, visit, worksIn } from './app-state.js'

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
 * waits, in Working's orange when the slot works, and in gray when it is free. An agent slot working an issue labelled
 * `gpu` marks it, so the agent slots' marks count the gpu slots in use.
 */
function RunnerCell({ kind, busy, tone, gpu = false }) {
  return html`<td class=${`runner ${tone === 'full' ? 'full' : busy ? 'working' : ''}`}>${kind}${gpu
    ? html` <span class="gpu-mark" title="Works an issue labelled gpu, in one of its project's gpu slots">gpu</span>`
    : null}</td>`
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
    <${RunnerCell} kind=${kind} busy=${true} tone=${tone} gpu=${kind === 'agent' && worksIn('gpu', row)} />
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
 * Sets a project's slots of `kind`, agent or gpu, to `count` through the server, as `agent-desk slots <repo>
 * <kind>=<n>` does, or with null drops the override so the project follows its `.agent-desk.json` again. The steppers
 * wait for the answer before they take another press, and the server's next state shows the new count.
 */
export async function setSlots(desk, kind, count) {
  ui.slotsSaving.add(desk)
  draw()
  const sent = count === null ? { desk, reset: kind } : { desk, [kind]: count }
  await post('/api/slots', sent, deskOf(desk)?.title ?? desk)
  ui.slotsSaving.delete(desk)
  draw()
}

/**
 * A stepper row of a project's slots of `kind`: the kind in its tone, − and + around the count, which − stops at 0
 * and + at `most`, and, when an override sets the count, the config's count beside it, such as `1 of 3`, and Reset,
 * which drops the override and says what the count goes back to.
 */
function SlotsRow({ desk, kind, count, config, override, most, back, tone, chip }) {
  const saving = ui.slotsSaving.has(desk)
  return html`<tr class=${`runner-row slot-control ${kind}`}>
    <${ProjectCell} desk=${desk} chip=${chip} />
    <${RunnerCell} kind=${kind} busy=${tone === 'busy'} tone=${tone} />
    <td class="slots" colspan="3"><div class="slot-line"><span class="stepper">
      <button type="button" aria-label=${`One ${kind} slot fewer`} title=${`One ${kind} slot fewer`}
        disabled=${saving || count === 0} onClick=${() => setSlots(desk, kind, count - 1)}>−</button>
      <span class="slot-count">${count}${override ? html`<span class="muted"> of ${config}</span>` : null}</span>
      <button type="button" aria-label=${`One ${kind} slot more`} title=${`One ${kind} slot more`}
        disabled=${saving || count >= most} onClick=${() => setSlots(desk, kind, count + 1)}>+</button>
    </span><span class="muted">${count === 1 ? 'slot' : 'slots'}</span>
    ${override ? html`<button type="button" class="reset" disabled=${saving} title=${back}
      onClick=${() => setSlots(desk, kind, null)}>Reset</button>` : null}</div></td>
  </tr>`
}

// The most agent slots the stepper sets, as server.ts's MAX_SLOTS.
const MAX_AGENT_SLOTS = 20

/**
 * A project's agent stepper row, first in its group: − and + around the agent count, with the config's count and
 * Reset when an override sets it, as SlotsRow draws them.
 */
export function AgentSlotsRow({ desk, agents, tone, chip = null }) {
  return html`<${SlotsRow} desk=${desk} kind="agent" count=${agents.count} config=${agents.config}
    override=${agents.override} most=${MAX_AGENT_SLOTS}
    back=${`Back to the ${agents.config} agent slots .agent-desk.json sets`} tone=${tone} chip=${chip} />`
}

/**
 * A project's gpu stepper row, under its agent stepper: how many of its agent runs may work issues labelled `gpu` at
 * once, which never shows above the agent count, and an unset count shows as the agent count. Its tone counts the
 * agent slots that work such an issue, `busy` of them.
 */
export function GpuSlotsRow({ desk, agents, gpu, busy, chip = null }) {
  const count = Math.min(gpu.count ?? agents.count, agents.count)
  const config = Math.min(gpu.config ?? agents.count, agents.count)
  const tone = busy >= count ? 'full' : busy > 0 ? 'busy' : 'free'
  const back = gpu.config === null ? 'Back to the agent count, since .agent-desk.json sets no gpu slots'
    : `Back to the ${gpu.config} gpu slots .agent-desk.json sets`
  return html`<${SlotsRow} desk=${desk} kind="gpu" count=${count} config=${config} override=${gpu.override}
    most=${agents.count} back=${back} tone=${tone} chip=${chip} />`
}

/**
 * The Runners sidebar, in History's column: its title, then one table with a header row. Each shown project's rows sit
 * together in the page's order: the agent and GPU steppers, one row per slot, and each local
 * session. On All a Project column names each group on its first row and a rule separates
 * one group from the next; a project's tab shows one group and leaves both out. `shown` holds the ids of the cards the
 * list shows, which a pressed row selects.
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
      ${projects.map(({ desk, slots, rows, locals, agents, gpu }) => {
        const top = agents ? (gpu ? 2 : 1) : 0
        return html`<tbody key=${desk}>
        ${agents ? html`<${AgentSlotsRow} desk=${desk} agents=${agents}
          tone=${slots.find(one => one.kind === 'agent')?.tone ?? 'free'} chip=${chipAt(0)} />` : null}
        ${agents && gpu ? html`<${GpuSlotsRow} desk=${desk} agents=${agents} gpu=${gpu} busy=${gpu.busy}
          chip=${chipAt(1)} />` : null}
        ${rows.map((slot, at) => html`<${SlotRow} key=${slot.row?.key ?? `${slot.kind}-${at}`} desk=${desk} slot=${slot}
          shown=${shown} chip=${chipAt(top + at)} />`)}
        ${locals.map((row, at) => html`<${Runner} key=${row.key} kind="local" row=${row} desk=${desk} shown=${shown}
          chip=${chipAt(top + rows.length + at)} />`)}
        ${top + rows.length + locals.length === 0 ? html`<tr class="runner-row free">
          <${ProjectCell} desk=${desk} chip=${chipAt(0)} /><td class="muted" colspan="4">no slots</td>
        </tr>` : null}
      </tbody>`
      })}
    </table>`}
  </aside>`
}
