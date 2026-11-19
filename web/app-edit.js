// The Markdown box that the comment box and Edit share, and Edit itself: the header's title as a field, and an issue's
// or a review's description as a Markdown box, each holding the source the store keeps and saved through /api/edit, a
// comment you wrote as the same Markdown box, saved through /api/comment-edit, and the Labels section's picker, which
// adds and removes each label through /api/edit as it is pressed.
import { html } from './vendor/preact-htm.js'
import { clean, LabelPicker, labelOf, Markup, STATE_LABELS } from './app-parts.js'
import { deskOf, draw, post, toast, ui } from './app-state.js'

/** Shows a Markdown box's text, the draft `id` keys, as the server renders it, or goes back to writing. */
async function togglePreview(desk, id) {
  if (ui.previewing.has(id)) {
    ui.previewing.delete(id)
    draw()
    return
  }
  ui.previewing.add(id)
  ui.rendered.delete(id)
  draw()
  const text = ui.drafts.get(id) ?? ''
  const sent = text.trim() ? await post('/api/markdown', { desk, text }) : { html: '' }
  ui.rendered.set(id, sent ? clean(sent.html) : '')
  draw()
}

/**
 * A Markdown box as GitHub's comment box draws it: Write and Preview tabs over the draft `id` keys, or its preview,
 * with `children`, such as its buttons, under it. The text stays put while the page redraws. `onInput` runs after the
 * draft takes each change, and `textRef` gets the text box.
 */
export function MarkdownBox({ desk, id, placeholder, label, disabled, onInput, onKeyDown, textRef, children }) {
  const previewing = ui.previewing.has(id)
  return html`<div class="comment-tabs">
      <button type="button" aria-current=${String(!previewing)}
        onClick=${() => previewing && void togglePreview(desk, id)}>Write</button>
      <button type="button" aria-current=${String(previewing)}
        onClick=${() => !previewing && void togglePreview(desk, id)}>Preview</button>
    </div>
    <div class="comment-write">
      ${previewing ? html`<div class="comment-preview">${ui.rendered.has(id)
        ? html`<${Markup} markup=${ui.rendered.get(id)} empty="Nothing to preview." />`
        : html`<p class="muted">Rendering…</p>`}</div>`
        : html`<textarea placeholder=${placeholder} value=${ui.drafts.get(id) ?? ''} disabled=${disabled}
          aria-label=${label} ref=${textRef} onKeyDown=${onKeyDown}
          onInput=${event => {
            ui.drafts.set(id, event.target.value)
            onInput?.(event)
          }} />`}
      ${children}
    </div>`
}

/**
 * The key an item's title, description, or comment edit keeps its state and its draft under; `field` is title, body,
 * or commentField's name for a comment.
 */
export function editKey(desk, number, field) {
  return `${desk}:${number}:edit-${field}`
}

/** The field a comment's edit goes by, as editKey and the edit's functions take it. */
export function commentField(id) {
  return `comment-${id}`
}

/** The id of the comment `field` names, or null for a title or description. */
function commentIdOf(field) {
  const found = /^comment-(\d+)$/.exec(field)
  return found ? Number(found[1]) : null
}

/**
 * Whether a comment shows Edit: one you wrote that the store has kept, and that didn't go to the agent, on its own or
 * with a Send to Agent that carried a line comment, since it records what the agent read.
 */
export function commentEditable(comment) {
  return comment.author?.login === 'you' && comment.id > 0 && comment.toAgent !== true && comment.sent !== true
}

/** Whether Edit shows the box for an item's title or description: once its source is read, and while it saves. */
export function editing(desk, number, field) {
  const status = ui.edits.get(editKey(desk, number, field))?.status
  return status === 'ready' || status === 'saving'
}

/**
 * Whether an edit's Save may post: its source is read, it isn't saving, and its text differs from the stored text; a
 * comment's text must hold more than spaces too.
 */
export function canSave(key) {
  const edit = ui.edits.get(key)
  const text = ui.drafts.get(key) ?? ''
  return edit?.status === 'ready' && text !== edit.stored && (!/:edit-comment-\d+$/.test(key) || text.trim() !== '')
}

/** The name a project's toasts start with. */
function projectOf(desk) {
  return deskOf(desk)?.title ?? desk
}

function fieldName(field) {
  return field === 'title' ? 'title' : field === 'body' ? 'description' : 'comment'
}

/**
 * Opens Edit on an item's title, description, or comment, with the text and version the store holds, which it reads
 * first.
 */
async function startEdit(desk, number, field) {
  const key = editKey(desk, number, field)
  if (ui.edits.has(key)) {
    return
  }
  ui.edits.set(key, { status: 'reading' })
  draw()
  const comment = commentIdOf(field)
  try {
    const res = await fetch(`/api/source?desk=${encodeURIComponent(desk)}&${comment === null ? `number=${number}`
      : `comment=${comment}`}`)
    if (!res.ok) {
      throw new Error(String(res.status))
    }
    const source = await res.json()
    const stored = comment === null ? source[field] : source.body
    ui.edits.set(key, { status: 'ready', stored, version: source.version, focus: true })
    ui.drafts.set(key, stored)
  } catch {
    ui.edits.delete(key)
    toast(`${projectOf(desk)}: could not read #${number}'s ${fieldName(field)}.`)
  }
  draw()
}

/** Closes an edit's box and forgets its text. */
function cancelEdit(key) {
  ui.edits.delete(key)
  ui.drafts.delete(key)
  ui.previewing.delete(key)
  ui.rendered.delete(key)
  draw()
}

/**
 * Saves an edit's text as the item's new title, description, or comment, and on success closes its box and calls
 * `reload` to read the item again, with the new title shown meanwhile. A title left as it was closes its box. A save
 * the server refuses keeps the text, and the toast gives the reason after the project's name.
 */
async function saveEdit(desk, number, field, reload) {
  const key = editKey(desk, number, field)
  const edit = ui.edits.get(key)
  const text = ui.drafts.get(key) ?? ''
  if (edit?.status !== 'ready') {
    return
  }
  if (text === edit.stored) {
    if (field === 'title') {
      cancelEdit(key)
    }
    return
  }
  edit.status = 'saving'
  draw()
  const comment = commentIdOf(field)
  const sent = comment !== null
    ? await post('/api/comment-edit', { desk, number, id: comment, body: text, version: edit.version }, projectOf(desk))
    : await post('/api/edit', { desk, number,
      ...(field === 'title' ? { title: text } : { body: text, version: edit.version }) }, projectOf(desk))
  if (!sent) {
    edit.status = 'ready'
    draw()
    return
  }
  cancelEdit(key)
  const title = text.replace(/\s+/g, ' ').trim()
  reload(field === 'title' ? data => ({ ...data, title }) : undefined)
  toast(comment !== null ? `Saved your comment on #${number}.` : `Saved #${number}'s ${fieldName(field)}.`)
}

/**
 * Puts the cursor at the end of a box Edit just opened, once. Preact hands over a new box before it joins the page,
 * so the focus waits until the draw ends.
 */
function focusOnce(edit) {
  return node => {
    if (node && edit?.focus) {
      edit.focus = false
      queueMicrotask(() => {
        node.focus()
        node.setSelectionRange(node.value.length, node.value.length)
      })
    }
  }
}

/** The muted mark after an edited comment's time, which shows the edit's local time on hover. */
export function EditedMark({ at }) {
  return at ? html`<span class="muted"> · </span><span class="muted edited-mark" title=${`Edited ${new Date(at)
    .toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`}>edited</span>` : null
}

/**
 * A comment's Edit button and, while Edit changes it, its box, as `{ action, editor }` for the comment's header and
 * body; each undefined on a comment commentEditable leaves alone, or while the comment shows as written.
 */
export function commentEdit(desk, number, comment, reload) {
  if (!commentEditable(comment)) {
    return { action: undefined, editor: undefined }
  }
  const field = commentField(comment.id)
  return { action: html`<${EditButton} desk=${desk} number=${number} field=${field} small=${true} />`,
    editor: editing(desk, number, field)
      ? html`<${BodyEdit} desk=${desk} number=${number} field=${field} reload=${reload} />` : undefined }
}

/** The Edit button of an item's title, description, or comment, which reads its source and opens its box. */
export function EditButton({ desk, number, field, small }) {
  const reading = ui.edits.get(editKey(desk, number, field))?.status === 'reading'
  return html`<button type="button" class=${small ? 'small' : undefined} disabled=${reading}
    aria-label=${commentIdOf(field) === null ? `Edit #${number}'s ${fieldName(field)}`
      : `Edit your comment on #${number}`} onClick=${() => void startEdit(desk, number, field)}>
    ${reading ? 'Reading…' : 'Edit'}</button>`
}

/** The header's title while Edit changes it: a field with Save and Cancel, where Enter saves and Esc cancels. */
export function TitleEdit({ desk, number, reload }) {
  const key = editKey(desk, number, 'title')
  const edit = ui.edits.get(key)
  const saving = edit?.status === 'saving'
  // A change to the text redraws only when it enables or disables Save.
  const saveable = canSave(key)
  return html`<div class="title-edit">
    <input type="text" value=${ui.drafts.get(key) ?? ''} disabled=${saving} aria-label=${`Title of #${number}`}
      ref=${focusOnce(edit)}
      onInput=${event => {
        ui.drafts.set(key, event.target.value)
        if (canSave(key) !== saveable) {
          draw()
        }
      }}
      onKeyDown=${event => {
        if (event.key === 'Enter') {
          event.preventDefault()
          void saveEdit(desk, number, 'title', reload)
        } else if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          cancelEdit(key)
        }
      }} />
    <button type="button" class="primary" disabled=${!saveable}
      onClick=${() => void saveEdit(desk, number, 'title', reload)}>${saving ? 'Saving…' : 'Save'}</button>
    <button type="button" disabled=${saving} onClick=${() => cancelEdit(key)}>Cancel</button>
  </div>`
}

/**
 * The description's box while Edit changes it, in place of the description, or a comment's, as `field` names it: a
 * Markdown box holding its source, and Save and Cancel under it. Save waits until the text differs from the stored
 * one, and a description may be empty; Ctrl+Enter saves and Esc cancels. With `agentOn`, as while an agent works or
 * waits to work the issue, a line under a description's Save says the agent won't see the edit.
 */
export function BodyEdit({ desk, number, field = 'body', reload, agentOn }) {
  const key = editKey(desk, number, field)
  const edit = ui.edits.get(key)
  const saving = edit?.status === 'saving'
  const save = () => void saveEdit(desk, number, field, reload)
  const description = field === 'body'
  const saveable = canSave(key)
  return html`<div class=${description ? 'comment edit-box' : 'comment edit-box comment-edit'} onKeyDown=${event => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      cancelEdit(key)
    } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      save()
    }
  }}>
    <${MarkdownBox} desk=${desk} id=${key} placeholder=${description ? 'No description provided.' : 'Leave a comment'}
      disabled=${saving} label=${description ? `Description of #${number}` : `Your comment on #${number}`}
      textRef=${focusOnce(edit)}
      onInput=${() => canSave(key) !== saveable && draw()}>
      <div class="comment-foot">
        <span class="muted">Markdown works here.</span>
        <button type="button" disabled=${saving} onClick=${() => cancelEdit(key)}>Cancel</button>
        <button type="button" class="primary" disabled=${!saveable} onClick=${save}>
          ${saving ? 'Saving…' : 'Save'} <kbd>Ctrl+Enter</kbd></button>
      </div>
      ${agentOn && description ? html`<p class="edit-note muted">The agent read the description when it started and
        won't see this edit. Send to Agent tells it.</p>` : null}
    </${MarkdownBox}>
  </div>`
}

/** Whether the Labels section shows its picker on an item. */
export function editingLabels(desk, number) {
  return ui.edits.has(editKey(desk, number, 'labels'))
}

/** Opens the Labels section's picker on an item, or closes it and forgets the label typed in its box. */
export function toggleLabels(desk, number) {
  const key = editKey(desk, number, 'labels')
  if (ui.edits.delete(key)) {
    ui.drafts.delete(key)
  } else {
    ui.edits.set(key, { status: 'ready' })
  }
  draw()
}

/**
 * Adds or removes a label on an item through /api/edit, as `change` names it in `addLabels` or `removeLabels`, and
 * on success calls `reload` to read the item again, with the labels the store kept shown meanwhile. One write at a
 * time: the picker waits while one runs. A write the server refuses changes nothing, and the toast gives the reason
 * after the project's name. Returns whether the store took it.
 */
export async function writeLabels(desk, number, change, reload) {
  const edit = ui.edits.get(editKey(desk, number, 'labels'))
  if (edit?.status !== 'ready') {
    return false
  }
  edit.status = 'saving'
  draw()
  const sent = await post('/api/edit', { desk, number, ...change }, projectOf(desk))
  edit.status = 'ready'
  if (!sent) {
    draw()
    return false
  }
  reload(data => ({ ...data, labels: sent.labels.map(name => labelOf(desk, name)) }))
  return true
}

/** Adds the label typed in the picker's box, and empties the box once the store has it. */
async function addTyped(desk, number, reload) {
  const key = editKey(desk, number, 'labels')
  const name = (ui.drafts.get(key) ?? '').trim()
  if (name !== '' && await writeLabels(desk, number, { addLabels: [name] }, reload)) {
    ui.drafts.delete(key)
    draw()
  }
}

/** The Labels section's Edit, which opens its picker, and Done, which closes it. */
export function LabelsButton({ desk, number }) {
  const open = editingLabels(desk, number)
  return html`<button type="button" class="small" aria-expanded=${String(open)}
    aria-label=${open ? `Close #${number}'s label picker` : `Edit #${number}'s labels`}
    onClick=${() => toggleLabels(desk, number)}>${open ? 'Done' : 'Edit'}</button>`
}

/**
 * The Labels section's picker on item `number`, with the labels it carries, `picked`, pressed. Pressing a label adds
 * or takes it off at once, and the box adds a label the project lacks. It never offers STATE_LABELS, which the store
 * keeps as the issue's state. Esc closes it.
 */
export function LabelsEdit({ desk, number, picked, reload }) {
  const key = editKey(desk, number, 'labels')
  return html`<${LabelPicker} desk=${desk} picked=${picked} left=${STATE_LABELS} typed=${ui.drafts.get(key) ?? ''}
    disabled=${ui.edits.get(key)?.status === 'saving'}
    onToggle=${name => void writeLabels(desk, number, picked.includes(name) ? { removeLabels: [name] }
      : { addLabels: [name] }, reload)}
    onType=${text => ui.drafts.set(key, text)} onAdd=${() => void addTyped(desk, number, reload)}
    onKeyDown=${event => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        toggleLabels(desk, number)
      }
    }} />`
}
