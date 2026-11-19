// An issue's Delete Issue button, at the foot of its page's sidebar, and the dialog that confirms it. The issue's
// preview says whether the store would delete it, so the button shows only on an issue with no work behind it. Since
// nothing brings a deleted issue back, the dialog's Delete waits until the person types the issue's number, `#<n>`.
// A delete closes the page, and the board drops the issue's card once it reads the store again.
import { html, useEffect, useRef } from './vendor/preact-htm.js'
import { Icon } from './app-parts.js'
import { closeDetail, draw, toast, ui } from './app-state.js'

/** Whether `typed` confirms the delete of issue `number`: its number with the `#` in front, as `#12`. */
export function confirmsDelete(typed, number) {
  return typed.trim() === `#${number}`
}

/** Opens the confirm dialog for issue `number` of project `desk`, with an empty box. */
export function askDelete(desk, number) {
  ui.deleting = { desk, number, typed: '', status: 'typing', error: null }
  draw()
}

/** Closes the confirm dialog, unless its delete is under way. */
export function cancelDelete() {
  if (ui.deleting?.status !== 'deleting') {
    ui.deleting = null
    draw()
  }
}

/**
 * Deletes the issue the dialog names once its box confirms it. A delete the server takes closes the dialog and the
 * page and says so in a toast; one it refuses keeps the dialog open with the server's reason.
 */
export async function deleteIssue() {
  const asked = ui.deleting
  if (!asked || asked.status === 'deleting' || !confirmsDelete(asked.typed, asked.number)) {
    return
  }
  ui.deleting = { ...asked, status: 'deleting', error: null }
  draw()
  const res = await fetch('/api/issue-delete', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ desk: asked.desk, number: asked.number }) }).catch(() => null)
  const sent = await res?.json().catch(() => ({}))
  if (!res?.ok) {
    ui.deleting = { ...asked, status: 'typing',
      error: res ? sent?.error ?? `the server refused (${res.status})` : 'the agent-desk server is not answering' }
    draw()
    return
  }
  ui.deleting = null
  closeDetail()
  toast(`Deleted #${asked.number}.`)
}

/** The sidebar's Delete Issue button for issue `number`, which opens the confirm dialog. */
export function DeleteButton({ desk, number }) {
  return html`<div class="side-section side-delete">
    <button type="button" class="link delete-issue" onClick=${() => askDelete(desk, number)}>
      <${Icon} name="trash" tone="" /> Delete Issue</button>
  </div>`
}

/**
 * The confirm dialog's content for `asked`, as ui.deleting holds it: what a delete removes, the box for the issue's
 * number, the server's reason when it refused, and Cancel and Delete, which stays off until the box confirms.
 */
export function DeleteForm({ asked, title }) {
  const ready = confirmsDelete(asked.typed, asked.number) && asked.status !== 'deleting'
  return html`<form method="dialog" class="delete-form" onSubmit=${event => {
    event.preventDefault()
    void deleteIssue()
  }}>
    <h2>Delete issue #${asked.number}?</h2>
    <p><strong>${title}</strong> goes for good, with its comments, labels, and links. Its number is never used again.
      Close it instead to keep its history.</p>
    <label for="delete-confirm">Type <code>#${asked.number}</code> to confirm</label>
    <input id="delete-confirm" type="text" autocomplete="off" spellcheck="false" value=${asked.typed}
      disabled=${asked.status === 'deleting'} onInput=${event => {
        ui.deleting = { ...asked, typed: event.currentTarget.value, error: null }
        draw()
      }} />
    ${asked.error ? html`<p class="delete-error" role="alert">${asked.error}.</p>` : null}
    <div class="delete-buttons">
      <button type="button" onClick=${cancelDelete} disabled=${asked.status === 'deleting'}>Cancel</button>
      <button type="submit" class="danger" disabled=${!ready}>${asked.status === 'deleting' ? 'Deleting…'
        : 'Delete Issue'}</button>
    </div>
  </form>`
}

/**
 * The confirm dialog over the page while ui.deleting names issue `number` of project `desk`, with its box focused.
 * Escape or a click outside it closes it, and keys stay inside it, so the page's own keys wait until it closes.
 */
export function DeleteDialog({ desk, number, title }) {
  const asked = ui.deleting
  if (asked?.desk !== desk || asked.number !== number) {
    return null
  }
  return html`<${Modal}><${DeleteForm} asked=${asked} title=${title} /></${Modal}>`
}

function Modal({ children }) {
  const dialog = useRef(null)
  useEffect(() => {
    dialog.current.showModal()
    dialog.current.querySelector('input')?.focus()
  }, [])
  return html`<dialog ref=${dialog} class="delete-dialog" aria-label="Delete issue"
    onCancel=${event => {
      event.preventDefault()
      cancelDelete()
    }}
    onKeyDown=${event => event.stopPropagation()}
    onClick=${event => event.target === dialog.current && cancelDelete()}>${children}</dialog>`
}
