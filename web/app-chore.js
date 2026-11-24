// A chore's page in the detail panel, which a Chores row or Queued's card for a chore opens: what its skill says it
// does, the skill's Markdown and the file it lives in, and the project's profile for the chore, as the server reads
// them, or the warning `agent-desk chores status` gives for a skill Claude Code can't find.
import { html } from './vendor/preact-htm.js'
import { clean, Markup } from './app-parts.js'
import { draw, PREVIEW_MS } from './app-state.js'

// Each chore's text, by `<desk>:<name>`: { status, at, data }.
const texts = new Map()

/**
 * The card a chore's page shows: a Chores row's, which the list doesn't draw, or with `fields` such as its stage and
 * note, Queued's for a chore starting or waiting for a runner. Both have one id, so the page stays on a chore as it
 * moves between them.
 */
export function choreCard(desk, chore, fields = {}) {
  return { id: choreId(desk.id, chore.name), desk: desk.id, title: chore.name, key: '', actions: [], chore, ...fields }
}

export function choreId(desk, name) {
  return `${desk}:chore-${name}`
}

/** A chore's text, read when first shown and again once PREVIEW_MS old, with its HTML cleaned. */
function textOf(desk, name) {
  const id = `${desk}:${name}`
  const kept = texts.get(id)
  if (kept === undefined || (kept.status !== 'loading' && Date.now() - kept.at > PREVIEW_MS)) {
    texts.set(id, { status: 'loading', at: Date.now(), data: kept?.data })
    void (async () => {
      try {
        const res = await fetch(`/api/chore-text?desk=${encodeURIComponent(desk)}&name=${encodeURIComponent(name)}`)
        if (!res.ok) {
          throw new Error(String(res.status))
        }
        const data = await res.json()
        texts.set(id, { status: 'ready', at: Date.now(), data: { ...data, description: clean(data.description),
          html: clean(data.html), profile: data.profile === null ? null : clean(data.profile) } })
      } catch {
        texts.set(id, { status: 'error', at: Date.now(), data: kept?.data })
      }
      draw()
    })()
  }
  return texts.get(id)
}

/** A rendered file in a box, with the line under it that names where it lives. */
function FileBox({ markup, path }) {
  return html`<div class="comment"><div class="comment-body"><${Markup} markup=${markup}
    empty="The file holds nothing after its front matter." /></div></div>
    <p class="muted chore-path">From <code>${path}</code></p>`
}

/** Chore `name`'s skill in project `desk`, and the project's profile for it under its own heading. */
export function ChoreText({ desk, name }) {
  const kept = textOf(desk, name)
  const text = kept.data
  if (!text) {
    return html`<p class="muted">${kept.status === 'error' ? `Could not read the ${name} skill.` : 'Reading…'}</p>`
  }
  return html`<section class="chore-text">
    ${text.warning ? html`<p class="chore-warning">${text.warning}</p>` : html`
      ${text.description ? html`<div class="chore-lead"><${Markup} markup=${text.description} /></div>` : null}
      <${FileBox} markup=${text.html} path=${text.path} />`}
    ${text.profile === null ? null : html`<h2 class="chore-heading">This project's profile</h2>
      <${FileBox} markup=${text.profile} path=${text.profilePath} />`}
  </section>`
}
