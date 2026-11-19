// The Resources view, which the header's gauges pill opens in the panel in place of the card, as the New Issue form
// does: what uses the machine's memory, swap, and CPU, as /api/resources reads it when the view opens and every
// RESOURCES_MS while it shows. It lists the memory free, the share the dispatcher keeps for the person, and what running
// runs claimed, each agent-desk unit with a link to its card and its claim, the runs the dispatcher holds for memory,
// the apps using the most memory outside agent-desk, and /tmp. Its Audit with Claude button asks the Chat for
// /agent-desk:resource-report with the figures the view shows. Esc, its close button, or picking a card closes it.
import { html } from './vendor/preact-htm.js'
import { sendChat } from './app-chat.js'
import { Chip } from './app-parts.js'
import { draw, itemHref, listedCard, toggleSide, ui } from './app-state.js'

// How often the view reads the machine again while it shows, in milliseconds.
export const RESOURCES_MS = 10_000
// The skill the Audit with Claude button runs in the Chat.
const AUDIT_SKILL = '/agent-desk:resource-report'
// The tables' columns of figures, which align right.
const FIGURES = new Set(['Memory', 'Claimed', 'Swap', 'CPU'])
// The tips of the runs table's headings.
const UNIT_TIPS = {
  Memory: 'Memory the unit holds, counting file cache the kernel can take back. A claim compares against the memory '
    + 'the run\'s programs allocated themselves (anonymous memory), so a run can show more here than its claim takes up',
  Claimed: 'The memory each run\'s claim counts for now. Hover a figure for what it claimed at start and what it holds '
    + 'back',
}

let timer = null

/** Reads /api/resources into the view, keeping its last reading with the error when a read fails. */
export async function readResources() {
  const view = ui.resources
  try {
    const res = await fetch('/api/resources')
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(data.error ?? `the server answered ${res.status}`)
    }
    view.data = data
    view.error = null
  } catch (error) {
    view.error = error instanceof Error ? error.message : String(error)
  }
  if (view.open) {
    draw()
  }
}

/** Shows the view in the panel, opening the panel when it's closed, and reads the machine now and every RESOURCES_MS. */
export function openResources() {
  ui.resources.open = true
  ui.newIssue.open = false
  ui.confirming = null
  ui.reviewConfirm = null
  draw()
  timer ??= setInterval(() => {
    if (ui.resources.open) {
      void readResources()
    } else {
      clearInterval(timer)
      timer = null
    }
  }, RESOURCES_MS)
  return readResources()
}

/** Closes the view; the panel shows the selected card again, and the reads stop at their next turn. */
export function closeResources() {
  ui.resources.open = false
  draw()
}

/** The gauges pill's press: shows the view, or closes it when it shows. */
export function toggleResources() {
  return ui.resources.open ? closeResources() : openResources()
}

/** Bytes as GB with one decimal, or MB under one GB, as the gauges read memory. */
export function sizeOf(bytes) {
  if (bytes === null || bytes === undefined) {
    return '–'
  }
  const gb = bytes / 1024 ** 3
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`
}

/** Seconds of CPU time, compactly: `45s`, `12m 05s`, or `3h 07m`. */
export function cpuOf(seconds) {
  if (seconds === null || seconds === undefined) {
    return '–'
  }
  const whole = Math.round(seconds)
  const pad = value => String(value).padStart(2, '0')
  if (whole < 60) {
    return `${whole}s`
  }
  if (whole < 3600) {
    return `${Math.floor(whole / 60)}m ${pad(whole % 60)}s`
  }
  return `${Math.floor(whole / 3600)}h ${pad(Math.floor(whole / 60) % 60)}m`
}

/**
 * The memory summary's figures, each as [name, value, tip, and what it is out of, or null]: the total, the memory free,
 * the share the dispatcher keeps for the person and what is left for runs above it, what running runs claimed and the
 * memory free less it, as the dispatcher's memory gate counts, and the swap free.
 */
export function memoryFacts(memory) {
  const spare = memory.reserve === null ? null : Math.max(0, memory.free - memory.reserve)
  const claimed = memory.claimed ?? null
  const afterClaims = claimed === null ? null : Math.max(0, memory.free - claimed)
  return [
    ['Total', sizeOf(memory.total), 'The memory this machine has', null],
    ['Free', sizeOf(memory.free), 'Memory the kernel can hand out now without swapping, as MemAvailable reads', null],
    ['Kept for you', sizeOf(memory.reserve),
      'Free memory the dispatcher leaves you: it starts a run only when the run fits on top of this', null],
    ['Free for runs', sizeOf(spare), 'Free memory above what the dispatcher keeps for you', null],
    ['Claimed by runs', sizeOf(claimed), 'Memory running runs claimed and haven\'t taken up yet. A run claims what it '
      + 'needs when it starts, and its claim shrinks once it passes its settle time', null],
    ['Free after claims', sizeOf(afterClaims), 'Free memory less what running runs claimed, the free figure a waiting '
      + 'run\'s reason gives: a run starts once this covers its need and what the dispatcher keeps for you', null],
    ['Swap free', memory.swapTotal > 0 ? sizeOf(memory.swapFree) : 'no swap', 'Swap the kernel can still move memory to',
      memory.swapTotal > 0 ? `of ${sizeOf(memory.swapTotal)}` : null],
  ]
}

/** A run's project and what it works on, linked to its card when the board lists one. */
function Target({ one }) {
  const card = one.desk && one.number !== null ? listedCard(one.desk, one.number) : undefined
  return html`<span class="res-target">
    ${one.desk ? html`<${Chip} desk=${one.desk} />` : one.repo ? html`<span class="muted">${one.repo}</span>` : null}
    ${card ? html`<a href=${itemHref(one.desk, one.number)} title=${card.title}>${one.label}</a>`
      : one.label ? html`<span>${one.label}</span>` : null}
    ${card ? html`<span class="res-title" title=${card.title}>${card.title}</span>` : null}
  </span>`
}

/** A table of rows under a heading, with a note in place of the table when it has none, and `tips` for its headings. */
function Section({ title, note, empty, head, rows, tips = {} }) {
  return html`<section class="res-section">
    <h2>${title}</h2>
    ${note ? html`<p class="muted res-note">${note}</p>` : null}
    ${rows.length === 0 ? html`<p class="muted res-empty">${empty}</p>` : html`<table class="res-table">
      <thead><tr>${head.map(name => html`<th key=${name} class=${FIGURES.has(name) ? 'num' : undefined}
        title=${tips[name]}>${name}</th>`)}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`}
  </section>`
}

/**
 * What a run's claim holds, as its Claimed figure's tip: what it claimed at start, when it may shrink or how far it
 * has, and what it holds back beyond what the run's programs hold.
 */
export function claimTip(claim) {
  const settle = claim.peak !== null
    ? `Past its settle time of ${claim.settle} min, it counts ${sizeOf(claim.counted)}, from its peak so far of `
      + `${sizeOf(claim.peak)}.`
    : claim.settle !== null
      ? `It may shrink after its settle time of ${claim.settle} min, when nearly every past run of its kind had reached `
        + 'its peak.'
      : 'It has no settle time yet, so it counts in full while the run lasts.'
  return `Claimed ${sizeOf(claim.claimed)} at start. ${settle} It holds back ${sizeOf(claim.held)} beyond the `
    + `${sizeOf(claim.used)} the run's programs hold.`
}

/** The agent-desk units at work, each with its kind, project, target, usage, and memory claim. */
export function unitRows(units) {
  return units.map(one => html`<tr key=${one.unit}>
    <td class="res-kind" title=${one.description || one.unit}>${one.kind}</td>
    <td><${Target} one=${one} /></td>
    <td class="num">${sizeOf(one.memory)}</td>
    <td class="num" title=${one.claim ? claimTip(one.claim) : 'No memory claim'}>${one.claim
      ? sizeOf(one.claim.counted) : '–'}</td>
    <td class="num">${sizeOf(one.swap)}</td>
    <td class="num">${cpuOf(one.cpu)}</td>
  </tr>`)
}

/** The requests the dispatcher holds for memory, each with why it waits. */
function waitRows(waiting) {
  return waiting.map((one, index) => html`<tr key=${`${one.repo}:${one.kind}:${one.label}:${index}`}>
    <td class="res-kind">${one.kind}</td><td><${Target} one=${one} /></td>
    <td>${one.reason}</td>
  </tr>`)
}

/** The apps outside agent-desk using the most memory, with how many processes each has among the ones read. */
function appRows(apps) {
  return apps.map(one => html`<tr key=${one.name}>
    <td class="res-app" title=${one.name}>${one.name}${one.processes > 1
      ? html` <span class="muted">×${one.processes}</span>` : null}</td>
    <td class="num">${sizeOf(one.memory)}</td><td class="num">${sizeOf(one.swap)}</td>
    <td class="num">${cpuOf(one.cpu)}</td>
  </tr>`)
}

/** When the view last read the machine, as the local time of day with seconds. */
function readAt(stamp) {
  const at = new Date(stamp)
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit',
    second: '2-digit' })
}

/**
 * The message Audit with Claude sends: the skill, with the view's last reading as its arguments, so the report starts
 * from the figures the person saw: the memory free, the share the dispatcher keeps, what running runs claimed, and
 * each run held for memory.
 */
export function auditMessage(data) {
  if (!data) {
    return AUDIT_SKILL
  }
  const { memory, waiting } = data
  const spare = memory.reserve === null ? null : Math.max(0, memory.free - memory.reserve)
  const swap = memory.swapTotal > 0 ? `${sizeOf(memory.swapFree)} of ${sizeOf(memory.swapTotal)} swap free`
    : 'no swap'
  const reserve = memory.reserve === null ? 'the dispatcher\'s reserve unread'
    : `${sizeOf(memory.reserve)} kept for me by the dispatcher, ${sizeOf(spare)} free for runs above it`
  const claimed = memory.claimed === null || memory.claimed === undefined ? ''
    : `, ${sizeOf(memory.claimed)} claimed by running runs, ${sizeOf(Math.max(0, memory.free - memory.claimed))} free `
      + 'after claims'
  const held = waiting.length === 0 ? 'The dispatcher holds no run for memory.'
    : ['Waiting for memory:', ...waiting.map(one => `- ${one.kind} ${one.label} in ${one.repo}${one.reason
      ? `: ${one.reason}` : ''}`)].join('\n')
  return `${AUDIT_SKILL} The Resources view read at ${readAt(data.at)}: ${sizeOf(memory.free)} of `
    + `${sizeOf(memory.total)} memory free, ${reserve}${claimed}, ${swap}.\n${held}`
}

/** Opens the Chat when it's hidden and sends it the audit, about no card. */
export function audit() {
  if (ui.side !== 'chat') {
    toggleSide('chat')
  }
  return sendChat(auditMessage(ui.resources.data), null)
}

/** Audit with Claude, which waits while the Chat replies, as the Chat's own Send does. */
function AuditButton() {
  const busy = ui.chat.running || ui.chat.sending
  return html`<button type="button" disabled=${busy} onClick=${() => void audit()}
    title=${busy ? 'Claude is still replying in the Chat; wait for the reply to end'
      : `Open the Chat and run ${AUDIT_SKILL} on these figures, to see what could be freed`}>
    Audit with Claude</button>`
}

function CloseView() {
  return html`<button type="button" class="close-detail" title="Close (Esc)" aria-label="Close"
    onClick=${closeResources}>
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"
      stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>`
}

/** The view in the panel. */
export function Resources() {
  const { data, error } = ui.resources
  return html`<article class="resources">
    <div class="gh-header">
      <div class="gh-title"><h1>Resources</h1><div class="actions"><${AuditButton} /><${CloseView} />
        </div></div>
      <p class="muted res-at">${data ? `Read at ${readAt(data.at)}; reads again every ${RESOURCES_MS / 1000} seconds`
        : error ? '' : 'Reading the machine…'}</p>
    </div>
    ${error ? html`<p class="notice error res-error" role="status">The machine couldn't be read: ${error}</p>` : null}
    ${data ? html`
      <section class="res-section">
        <h2>Memory</h2>
        <dl class="res-facts">${memoryFacts(data.memory).map(([name, value, tip, of]) => html`<div key=${name}
          title=${tip}><dt>${name}</dt><dd>${value}${of ? html` <span class="res-of">${of}</span>` : null}</dd>
          </div>`)}</dl>
      </section>
      <${Section} title="Agent-desk runs" empty="No agent-desk unit is running."
        note="Each unit counts every process its run started." head=${['Kind', 'Works on', 'Memory', 'Claimed', 'Swap', 'CPU']}
        tips=${UNIT_TIPS} rows=${unitRows(data.units)} />
      <${Section} title="Waiting for memory" empty="The dispatcher holds no run for memory."
        head=${['Kind', 'Works on', 'Waits for']} rows=${waitRows(data.waiting)} />
      <${Section} title="Everything else"
        note=${`The ${data.apps.reduce((sum, one) => sum + one.processes, 0)} processes outside agent-desk using the`
          + ' most memory and swap, by app.'}
        empty="No process outside agent-desk was read." head=${['App', 'Memory', 'Swap', 'CPU']}
        rows=${appRows(data.apps)} />
      <section class="res-section">
        <h2>Temp files</h2>
        <p class="res-tmp"><strong>${sizeOf(data.tmp)}</strong> in /tmp, a tmpfs that sits in RAM and swap.</p>
      </section>` : null}
  </article>`
}
