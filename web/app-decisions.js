// A body's `## Decisions Needed` sections, drawn from the data the server cuts out of it, each with a form under it
// that answers the section in a comment: a button per lettered option and an Other field per decision, or one text
// box for a section in the older layout, then a note and the conversation's Comment and Send to Agent. A section an
// answer covers shows who answered and when, and folds its form behind Answer Again.
import { html } from './vendor/preact-htm.js'
import { clean, Markup, scrollToAnchor } from './app-parts.js'
import { age, draw, ui } from './app-state.js'

/** A body's parts with every piece of HTML cleaned, as the page cleans a whole body; undefined stays undefined. */
export function cleanParts(parts) {
  return parts?.map(part => part.section ? { section: cleanSection(part.section) } : { html: clean(part.html) })
}

function cleanSection(section) {
  return { ...section, heading: clean(section.heading), intro: clean(section.intro),
    decisions: section.decisions.map(decision => ({ ...decision, head: clean(decision.head),
      after: clean(decision.after),
      options: decision.options.map(option => ({ ...option, html: clean(option.html) })) })) }
}

/** A form's draft by its key, as it stays in ui.drafts: each decision's pick and Other text by number, and the note. */
export function draftOf(key) {
  const kept = ui.drafts.get(key)
  return typeof kept === 'object' && kept !== null ? kept : { picks: {}, other: {}, note: '' }
}

/** Whether a draft answers anything: a pick, an Other text, or a note. */
export function hasAnswer(draft) {
  return Object.values(draft.picks).some(Boolean) || Object.values(draft.other).some(text => text.trim() !== '')
    || draft.note.trim() !== ''
}

/**
 * The comment a form posts for `section` from `draft`: a first line that links the section by its anchor, then a
 * bullet per decision answered, as `**1B.** <label>` for a pick and `**2.** <text>` for Other text alone, then the
 * note. An empty draft writes nothing.
 */
export function answerText(section, draft) {
  const lines = section.decisions.flatMap(decision => {
    const option = decision.options.find(one => one.letter === draft.picks[decision.number])
    const other = (draft.other[decision.number] ?? '').trim()
    if (!option && !other) {
      return []
    }
    return [`- **${decision.number}${option?.letter ?? ''}.** ${[option?.label, other].filter(Boolean).join(': ')}`]
  })
  const note = draft.note.trim()
  if (lines.length === 0 && !note) {
    return ''
  }
  return [`Answers to the Decisions Needed in [${section.place}](#${section.anchor}):`, lines.join('\n'), note]
    .filter(Boolean).join('\n\n')
}

/** The newest comment that answers the section with anchor `anchor`, as the server read its `answers`; or undefined. */
export function answerFor(comments, anchor) {
  return comments.findLast(comment => comment.answers?.anchor === anchor)
}

/**
 * Whether `answer` covers every decision in `section`, so its form starts folded. A section in the older layout counts
 * as covered by any answer; a numbered decision the agent added after the answer opens the form again.
 */
export function covers(section, answer) {
  if (!answer) {
    return false
  }
  const answered = new Set(answer.answers.picks.map(pick => Number.parseInt(pick, 10)))
  return section.decisions.every(decision => answered.has(decision.number))
}

/**
 * A body as its parts, or as one piece of markup without them: each run of HTML as markup, and each Decisions Needed
 * section drawn with its form. `answering` holds what the forms need from their conversation, as Conversation gives it.
 */
export function Body({ markup, parts, answering }) {
  if (!parts || !answering) {
    return html`<${Markup} markup=${markup} />`
  }
  return html`<div class="markdown-parts">${parts.map((part, index) => part.section
    ? html`<${Section} key=${part.section.anchor} section=${part.section} answering=${answering} />`
    : part.html.trim() ? html`<${Markup} key=${index} markup=${part.html} />` : null)}</div>`
}

/** A Decisions Needed section: its heading, the line saying who answered it, its decisions, and the form under them. */
function Section({ section, answering }) {
  const key = `${answering.desk}:${answering.number}#${section.anchor}`
  const answer = answerFor(answering.comments, section.anchor)
  const draft = draftOf(key)
  const open = !covers(section, answer) || ui.answering.has(key)
  return html`<section class="decisions markdown" data-anchor=${section.anchor}>
    <div dangerouslySetInnerHTML=${{ __html: section.heading }} />
    ${answer ? html`<p class="answered muted"><button type="button" class="link" title="Show the answer"
      onClick=${event => scrollToAnchor(event.currentTarget, `comment-${answer.id}`)}>${answer.author.login === 'you'
        ? 'You' : answer.author.login} answered${answer.answers.picks.length > 0
        ? ` ${answer.answers.picks.join(', ')}` : ''} · ${age(answer.at)} ago</button></p>` : null}
    ${section.intro ? html`<div dangerouslySetInnerHTML=${{ __html: section.intro }} />` : null}
    ${section.decisions.map(decision => html`<div class="decision" key=${decision.number}>
      <div dangerouslySetInnerHTML=${{ __html: decision.head }} />
      ${decision.options.length > 0 ? html`<ul>${decision.options.map(option => html`<li key=${option.letter}
        class=${draft.picks[decision.number] === option.letter ? 'chosen' : undefined}
        dangerouslySetInnerHTML=${{ __html: option.html }} />`)}</ul>` : null}
      ${decision.after ? html`<div dangerouslySetInnerHTML=${{ __html: decision.after }} />` : null}
    </div>`)}
    ${open ? html`<${AnswerForm} section=${section} answering=${answering} draftKey=${key} draft=${draft}
      again=${answer !== undefined && ui.answering.has(key)} />`
      : html`<div class="answer-again"><button type="button" class="small" onClick=${() => {
        ui.answering.add(key)
        draw()
      }}>Answer Again</button></div>`}
  </section>`
}

/**
 * Stores the change `change` makes to a form's draft, redrawing for a pick, or when typing makes the draft empty or
 * not. It reads the stored draft, since the page may not have redrawn since the last key.
 */
function update(key, change, always = false) {
  const draft = draftOf(key)
  const had = hasAnswer(draft)
  const next = change(draft)
  ui.drafts.set(key, next)
  if (always || had !== hasAnswer(next)) {
    draw()
  }
}

/**
 * The form under a section: a row per decision with a button per option, which a second press takes back, and an Other
 * field, or one text box for a section in the older layout; then a note, and the conversation's Send to Agent and
 * Comment, which wait for an answer. Ctrl+Enter or Cmd+Enter in a text box presses Comment.
 */
function AnswerForm({ section, answering, draftKey, draft, again }) {
  const busy = answering.busy(draftKey)
  const ready = hasAnswer(draft) && !busy
  const legacy = section.decisions.length === 0
  const send = async toAgent => {
    if (await answering.post(draftKey, answerText(section, draftOf(draftKey)), toAgent)) {
      ui.answering.delete(draftKey)
      draw()
    }
  }
  // Ctrl+Enter reads the stored draft, which typing may have changed since the last redraw, and posts nothing while
  // Comment can't be pressed.
  const onKeyDown = event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      if (hasAnswer(draftOf(draftKey)) && !answering.busy(draftKey)) {
        void send(false)
      }
    }
  }
  return html`<div class="answer-form">
    ${section.decisions.map(decision => html`<div class="answer-row" key=${decision.number}>
      <span class="answer-number" title=${decision.question}>${decision.number}.</span>
      <span class="answer-options" role="group" aria-label=${`Decision ${decision.number}: ${decision.question}`}>
        ${decision.options.map(option => {
          const picked = draft.picks[decision.number] === option.letter
          return html`<button type="button" key=${option.letter} class="answer-option" aria-pressed=${String(picked)}
            disabled=${busy} title=${option.pick ? 'The agent\'s pick' : undefined}
            onClick=${() => update(draftKey, kept => ({ ...kept, picks: { ...kept.picks,
              [decision.number]: picked ? undefined : option.letter } }), true)}>
            <strong>${option.letter}</strong> ${option.label}${option.pick ? ' ✅' : ''}</button>`
        })}
        <input type="text" class="answer-other" placeholder="Other" disabled=${busy}
          aria-label=${`Other answer to decision ${decision.number}`} value=${draft.other[decision.number] ?? ''}
          onKeyDown=${onKeyDown} onInput=${event => update(draftKey, kept => ({ ...kept,
            other: { ...kept.other, [decision.number]: event.target.value } }))} />
      </span>
    </div>`)}
    <textarea class="answer-note" placeholder=${legacy ? 'Your answer' : 'Add a note'} disabled=${busy}
      aria-label=${legacy ? 'Your answer' : 'A note with your answers'} value=${draft.note} onKeyDown=${onKeyDown}
      onInput=${event => update(draftKey, kept => ({ ...kept, note: event.target.value }))} />
    <div class="comment-foot">
      ${again ? html`<button type="button" class="small" onClick=${() => {
        ui.answering.delete(draftKey)
        draw()
      }}>Cancel</button>` : null}
      ${answering.send ? html`<button type="button" class="answer-send" disabled=${!ready}
        onClick=${() => void send(true)}>Send to Agent</button>` : null}
      <button type="button" class="primary" disabled=${!ready} onClick=${() => void send(false)}>${busy
        ? 'Commenting…' : 'Comment'} <kbd>Ctrl+Enter</kbd></button>
    </div>
  </div>`
}
