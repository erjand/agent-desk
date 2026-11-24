// The New Issue form, which fills the panel in place of the selected card as GitHub's new-issue page fills its page: a
// project picker, a title, a Markdown body with Write and Preview tabs that starts from the project's task template, a
// label picker, Parent and Blocked By fields, an Agent-Ready box, and Create, which files the issue through
// /api/issue-create and then selects its card. The draft stays in `ui.newIssue` until a create or a reload, so closing
// the form and opening it again shows it.
import { html } from './vendor/preact-htm.js'
import { MarkdownBox } from './app-edit.js'
import { LabelPicker, UNOFFERED } from './app-parts.js'
import { deskOf, draw, emptyDraft, listedCard, pickTab, post, rememberFolds, select, toast, ui } from './app-state.js'

// The key the body's draft, its preview, and whether it previews go under in drafts, previewing, and rendered.
export const NEW_ISSUE = 'new-issue'
const PLACEHOLDER = 'Describe the work: its goal, what done looks like, and anything an agent needs to know.'

/**
 * The project the picker starts on: the open project's tab, or on All the selected card's project, `selected`, or
 * else the first of `desks`, the projects in the page's order.
 */
export function defaultProject(tab, desks, selected) {
  const ids = desks.map(desk => desk.id)
  return ids.includes(tab) ? tab : ids.includes(selected) ? selected : ids[0] ?? null
}

/** Whether `body` is a body the person hasn't written: empty, or `shown`, the template it last took, untouched. */
export function untouched(body, shown) {
  return body.trim() === '' || body === shown
}

/** Whether the draft holds nothing the person wrote or picked. */
function blank(form) {
  return form.title.trim() === '' && untouched(ui.drafts.get(NEW_ISSUE) ?? '', form.template)
    && form.labels.length === 0 && form.parent.trim() === '' && form.blockedBy.trim() === ''
}

/**
 * Reads the task template of `desk` and puts it in the body, once the form still files in `desk` and the person
 * hasn't written the body: it is empty, or holds the template it last took untouched, such as another project's.
 */
async function fillTemplate(desk) {
  let template
  try {
    const res = await fetch(`/api/issue-template?desk=${encodeURIComponent(desk)}`)
    template = res.ok ? (await res.json()).template : null
  } catch {
    template = null
  }
  const form = ui.newIssue
  const body = ui.drafts.get(NEW_ISSUE) ?? ''
  if (typeof template !== 'string' || form.desk !== desk || !untouched(body, form.template)) {
    return
  }
  form.template = template
  if (body !== template) {
    ui.drafts.set(NEW_ISSUE, template)
    ui.rendered.delete(NEW_ISSUE)
    ui.previewing.delete(NEW_ISSUE)
    draw()
  }
}

/** Files the form in `desk`, swapping the body for its template while the person hasn't written the body. */
export function pickProject(desk) {
  ui.newIssue.desk = desk
  draw()
  if (untouched(ui.drafts.get(NEW_ISSUE) ?? '', ui.newIssue.template)) {
    return fillTemplate(desk)
  }
}

/**
 * Opens the form in the panel, focused on its title, with Agent-Ready unchecked. A blank draft starts on the project
 * defaultProject picks, with `selected` the selected card's project; a draft with text keeps the project it had. A
 * body the person hasn't written takes the project's task template.
 */
export function openNewIssue(selected) {
  const form = ui.newIssue
  if (blank(form) || deskOf(form.desk) === undefined) {
    form.desk = defaultProject(ui.tab, ui.server?.desks ?? [], selected)
  }
  form.open = true
  ui.resources.open = false
  form.focus = true
  form.agentReady = false
  ui.confirming = null
  ui.reviewConfirm = null
  draw()
  if (form.desk !== null && untouched(ui.drafts.get(NEW_ISSUE) ?? '', form.template)) {
    return fillTemplate(form.desk)
  }
}

/**
 * The issue numbers in a Parent or Blocked By field's `text`, such as `#12, #15` or `12 15`: each number, or the word
 * as typed when it isn't one, for the server to refuse with its reason.
 */
export function issueNumbers(text) {
  return text.split(/[\s,]+/).filter(word => word !== '')
    .map(word => /^#?\d+$/.test(word) ? Number(word.replace('#', '')) : word)
}

/** The parent the Parent field's `text` names for the server: null when empty, its one number, or else the text. */
function parentOf(text) {
  const numbers = issueNumbers(text)
  return numbers.length === 0 ? null : numbers.length === 1 ? numbers[0] : text.trim()
}

/** Closes the form and keeps its draft. */
export function closeNewIssue() {
  ui.newIssue.open = false
  draw()
}

function toggleLabel(name) {
  const form = ui.newIssue
  form.labels = form.labels.includes(name) ? form.labels.filter(one => one !== name) : [...form.labels, name]
  draw()
}

/** Picks the label typed in the new label box, once it is a plain name; Agent-Ready stays on the card's button. */
function addTyped() {
  const form = ui.newIssue
  const name = form.typed.trim()
  if (name === '') {
    return
  }
  if (UNOFFERED.has(name)) {
    toast(`The form doesn't add ${name} as a label; ${name === 'agent-ready' ? 'check Agent-Ready below'
      : 'a run sets it'}.`)
    return
  }
  if (/[\p{Cc},]/u.test(name)) {
    toast('A label is a name on one line, without commas.')
    return
  }
  form.typed = ''
  if (!form.labels.includes(name)) {
    form.labels = [...form.labels, name]
  }
  draw()
}

/**
 * Files the draft as a new issue in its project, then clears the form and selects the new card once the board shows
 * it. A create the server refuses keeps the draft, and the toast gives the reason after the project's name, as the
 * store words it for a parent or blocker it doesn't hold.
 */
export async function createNewIssue() {
  const form = ui.newIssue
  const desk = form.desk
  if (form.creating || form.title.trim() === '' || deskOf(desk) === undefined) {
    return
  }
  form.creating = true
  draw()
  const project = deskOf(desk)?.title ?? desk
  const sent = await post('/api/issue-create', { desk, title: form.title, body: ui.drafts.get(NEW_ISSUE) ?? '',
    labels: form.labels, agentReady: form.agentReady, parent: parentOf(form.parent),
    blockedBy: issueNumbers(form.blockedBy) }, project)
  form.creating = false
  if (!sent) {
    draw()
    return
  }
  ui.newIssue = emptyDraft()
  ui.drafts.delete(NEW_ISSUE)
  ui.previewing.delete(NEW_ISSUE)
  ui.rendered.delete(NEW_ISSUE)
  ui.created = { desk, number: sent.number }
  if (ui.tab !== 'all' && ui.tab !== desk) {
    pickTab(desk)
  }
  toast(`${project}: created #${sent.number}.`)
  if (!showCreated()) {
    draw()
  }
}

/**
 * Selects the issue the last create filed on its Issue tab, opening its group, once its project's board lists its
 * card. Returns whether it did; the page calls it again with each state until it does.
 */
export function showCreated() {
  const made = ui.created
  const card = made ? listedCard(made.desk, made.number) : undefined
  if (card === undefined) {
    return false
  }
  ui.created = null
  if (ui.collapsed.delete(card.stage)) {
    rememberFolds()
  }
  select(card.id)
  ui.detailTab = 'issue'
  ui.reveal = true
  draw()
  return true
}

/** Puts the cursor in the title once after the form opens; Preact hands over the field before it joins the page. */
function focusTitle(node) {
  if (node && ui.newIssue.focus) {
    ui.newIssue.focus = false
    queueMicrotask(() => node.focus())
  }
}

function CloseForm() {
  return html`<button type="button" class="close-detail" title="Close (Esc)" aria-label="Close"
    onClick=${closeNewIssue}>
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"
      stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>`
}

/**
 * The New Issue form in the panel. Create waits for a title; Ctrl+Enter creates from any of its fields, and Esc,
 * Cancel, or the X closes the form and keeps its draft.
 */
export function NewIssue() {
  const form = ui.newIssue
  const desks = ui.server?.desks ?? []
  const creatable = form.title.trim() !== '' && !form.creating
  return html`<article class="new-issue" onKeyDown=${event => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeNewIssue()
    } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      void createNewIssue()
    }
  }}>
    <div class="gh-header">
      <div class="gh-title"><h1>New Issue</h1><div class="actions"><${CloseForm} /></div></div>
    </div>
    <label class="field"><span class="field-name">Project</span>
      <select value=${form.desk ?? ''} disabled=${form.creating}
        onChange=${event => void pickProject(event.target.value)}>${desks.map(desk => html`<option key=${desk.id} value=${desk.id}>${desk.title}</option>`)}</select>
    </label>
    <label class="field"><span class="field-name">Title</span>
      <input type="text" class="title-field" placeholder="Title" value=${form.title} disabled=${form.creating}
        ref=${focusTitle} onInput=${event => {
          const was = form.title.trim() !== ''
          form.title = event.target.value
          if (was !== (form.title.trim() !== '')) {
            draw()
          }
        }} />
    </label>
    <div class="field"><span class="field-name">Description</span>
      <div class="comment">
        <${MarkdownBox} desk=${form.desk} id=${NEW_ISSUE} placeholder=${PLACEHOLDER}
          label="Description of the new issue" disabled=${form.creating}>
          <div class="comment-foot"><span class="muted">Markdown works here.</span></div>
        </${MarkdownBox}>
      </div>
    </div>
    <div class="field"><span class="field-name">Labels</span><${LabelPicker} desk=${form.desk} picked=${form.labels}
      typed=${form.typed} disabled=${form.creating} onToggle=${toggleLabel} onAdd=${addTyped}
      onType=${text => { form.typed = text }} /></div>
    <div class="field-row">
      <label class="field"><span class="field-name">Parent</span>
        <input type="text" class="number-field" value=${form.parent} disabled=${form.creating}
          onInput=${event => { form.parent = event.target.value }} /></label>
      <label class="field"><span class="field-name">Blocked By</span>
        <input type="text" class="number-field" value=${form.blockedBy}
          disabled=${form.creating} onInput=${event => { form.blockedBy = event.target.value }} /></label>
    </div>
    <div class="new-issue-foot">
      <label class="agent-ready-box"><input type="checkbox" checked=${form.agentReady} disabled=${form.creating}
        onChange=${event => {
          form.agentReady = event.target.checked
          draw()
        }} /> Agent-Ready: start an agent on it now</label>
      <button type="button" disabled=${form.creating} onClick=${closeNewIssue}>Cancel</button>
      <button type="button" class="primary" disabled=${!creatable} onClick=${() => void createNewIssue()}>
        ${form.creating ? 'Creating…' : 'Create'} <kbd>Ctrl+Enter</kbd></button>
    </div>
  </article>`
}
