// The selected card's panel, as GitHub shows an issue or a PR: its conversations and comment boxes, its dependencies,
// a PR's files and checks, and an issue's agent log, with the previews and relations it reads from the server. An
// issue with a review shows both on one page, the issue's conversation on its Issue tab and the review's on its Review
// tab, each with its own comment box. app-review.js draws a PR's files, line comments, and buttons.
import { html } from './vendor/preact-htm.js'
import { Body, cleanParts } from './app-decisions.js'
import { DeleteButton, DeleteDialog } from './app-delete.js'
import { BodyEdit, commentEdit, EditButton, EditedMark, editing, editingLabels, LabelsButton, LabelsEdit, MarkdownBox,
  TitleEdit } from './app-edit.js'
import { Avatar, Chip, clean, Icon, iconOf, Label, labelOf, Reaction } from './app-parts.js'
import { Diffstat, Files, headerReviewButtons, Lines, markFileInView, pressReview, REVIEW_KEYS, reviewAct, ReviewButton,
  reviewButtons, scrollerOf, unsentCount } from './app-review.js'
import { act, actionLabel, age, closeDetail, deskOf, draw, firstTab, FORWARD, itemHref, KEYS, LABELS, pickDetailTab,
  post, press, PREVIEW_MS, remember, select, STAGES, toast, ui } from './app-state.js'

// How often a working agent's log is read again while it shows.
export const LOG_MS = 5_000
// Each issue's and PR's preview, by `<desk>:<issue|pr>-<number>`: { status, at, data, labels }.
const previews = new Map()
// Each issue's parent, sub-issues, blockers, and the issues it blocks, by `<desk>:<number>`: { status, at, data }.
const relations = new Map()
// The ticks on each issue's task boxes still being written, by the preview's id, as the promise the last one ends with.
const ticking = new Map()

function CloseButton() {
  return html`<button type="button" class="close-detail" title="Close (Esc)" aria-label="Close" onClick=${closeDetail}>
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"
      stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>`
}

/**
 * An issue's or PR's preview, read when first shown, again once PREVIEW_MS old, and again once `labels`, the card's
 * labels from the board, differ from those it was read with, as after Move to Open takes idea off. While one of its
 * comments sent to the agent waits for the dispatcher's answer, it also reads again once the store moves past the
 * event it was read at, so the comment's 👀 shows at the next state after the answer. Its HTML comes back cleaned.
 */
export function preview(kind, desk, number, labels = '') {
  const id = `${desk}:${kind}-${number}`
  const kept = previews.get(id)
  if (kept?.status === 'loading' && kept.labels !== labels) {
    kept.again = true
    kept.labels = labels
  }
  const answered = kept?.data?.waiting === true && ui.storeEvent > kept.event
  if (kept === undefined || (kept.status !== 'loading'
    && (Date.now() - kept.at > PREVIEW_MS || kept.labels !== labels || answered))) {
    const event = ui.storeEvent
    previews.set(id, { status: 'loading', at: Date.now(), data: kept?.data, labels, event })
    void (async () => {
      try {
        const res = await fetch(`/api/${kind}?desk=${encodeURIComponent(desk)}&number=${number}`)
        if (!res.ok) {
          throw new Error(String(res.status))
        }
        const data = await res.json()
        data.body = clean(data.body)
        data.bodyParts = cleanParts(data.bodyParts)
        data.comments = data.comments.map(cleanComment)
        if (data.lineComments) {
          data.lineComments = data.lineComments.map(comment => ({ ...comment, html: clean(comment.html) }))
        }
        // A change asked for while this read ran reads again at the next draw.
        const read = previews.get(id)
        previews.set(id, { status: 'ready', at: read?.again ? 0 : Date.now(), data, labels: read?.labels ?? labels,
          event })
      } catch {
        previews.set(id, { status: 'error', at: Date.now(), data: kept?.data,
          labels: previews.get(id)?.labels ?? labels, event })
      }
      draw()
    })()
  }
  return previews.get(id)
}

/** A comment from the server with its HTML cleaned, and its Decisions Needed sections' HTML. */
function cleanComment(comment) {
  return { ...comment, html: clean(comment.html), parts: cleanParts(comment.parts) }
}

/**
 * Reads an issue's or PR's preview again at the next draw, after a press changed it, showing `change` applied to what
 * it shows meanwhile; a read under way when the press lands reads again once it ends.
 */
function reread(kind, desk, number, change) {
  const kept = previews.get(`${desk}:${kind}-${number}`)
  if (kept) {
    if (change && kept.data) {
      kept.data = change(kept.data)
    }
    kept.again = kept.status === 'loading'
    kept.at = 0
  }
  draw()
}

/**
 * Presses the button of the selected card's review that `key` names, as Close Without Merging's `l`, or confirms the
 * one waiting for its confirm on `y`, Enter, or its own key. Returns whether the key pressed one.
 */
export function reviewKey(card, key) {
  const number = card ? reviewNumberOf(card) : undefined
  const pr = number !== undefined ? previews.get(`${card.desk}:pr-${number}`)?.data : undefined
  if (!pr) {
    return false
  }
  const reload = change => reread('pr', card.desk, pr.number, change)
  const buttons = headerReviewButtons(pr.state)
  const waiting = buttons.find(action => ui.reviewConfirm === `${card.desk}:${pr.number}:${action}`)
  if (waiting && (key === 'y' || key === 'Enter' || key === REVIEW_KEYS[waiting])) {
    void reviewAct(card.desk, pr, waiting, reload)
    return true
  }
  const action = buttons.find(one => REVIEW_KEYS[one] === key)
  if (action) {
    pressReview(card.desk, pr, action, reload)
    return true
  }
  return false
}

/** The issue a card is about, or undefined for a review without one, such as a chore's, or a run without an issue. */
function issueOf(card) {
  return card.number !== undefined && card.number !== card.pr ? card.number : undefined
}

/**
 * What the title's Edit on a card's page edits, as { kind, number }: the issue, whose title the header shows, even
 * beside its review; a review with no issue, such as a chore's, edits its own; and a card with neither has no Edit.
 */
export function titleTarget(card) {
  const issue = issueOf(card)
  return issue !== undefined ? { kind: 'issue', number: issue }
    : card.pr !== undefined ? { kind: 'pr', number: card.pr } : undefined
}

/**
 * The review a card's page shows: a review's own, or for an issue the one picked in its Development section, or else
 * its open review, from its card or, before the board names it, from the issue's preview; undefined for none.
 */
export function shownReview(card, issue) {
  if (issueOf(card) === undefined) {
    return card.pr
  }
  const picked = ui.reviewPicks.get(card.id)
  if (picked !== undefined && (picked === card.pr || issue?.reviews?.some(one => one.number === picked))) {
    return picked
  }
  return card.pr ?? issue?.reviews?.findLast(one => one.state === 'open' || one.state === 'draft')?.number
}

/** The review the card's page shows, from the issue's preview read so far. */
function reviewNumberOf(card) {
  const issue = issueOf(card)
  return shownReview(card, issue === undefined ? undefined : previews.get(`${card.desk}:issue-${issue}`)?.data)
}

/**
 * Shows review `number` on an issue's page at its Review tab, or goes back to the one it shows by default when `number`
 * is the review it already shows. With `number` undefined, as when the person picks the issue, it goes back to the
 * default review and shows the Issue tab.
 */
function pickReview(card, number, shown) {
  if (number === undefined || number === shown) {
    ui.reviewPicks.delete(card.id)
  } else {
    ui.reviewPicks.set(card.id, number)
  }
  pickDetailTab(number === undefined ? 'issue' : 'review')
}

/**
 * The tabs of a card's page, in order, each as [id, title, count]. A count shows once its preview, `issueData` or `pr`,
 * is read. Dependencies counts open sub-issues and blockers, and shows when `related` has any.
 */
export function tabsOf({ issue, issueData, review, pr, related }) {
  return [
    ...(issue !== undefined ? [['issue', 'Issue', issueData?.commentCount]] : []),
    ...(review !== undefined ? [['review', 'Review', pr?.comments.length]] : []),
    ...(related ? [['deps', 'Dependencies', [...related.subs, ...related.blockedBy]
      .filter(one => one.state === 'open').length]] : []),
    ...(pr ? [['files', 'Files Changed', pr.files.length], ['checks', 'Checks', pr.checks.length]] : []),
    ...(issue !== undefined ? [['log', 'Agent Log', undefined]] : []),
  ]
}

/**
 * What tab `tab`'s comment box posts to: `issue` on the Issue tab, and on the Review tab `pr` while the review can
 * still change, or whatever its state when it has no issue, as a chore's review. A merged or closed review of an issue
 * has no box, null, since comments on it go to the issue.
 */
export function boxOf(tab, review, hasIssue) {
  if (tab === 'issue') {
    return 'issue'
  }
  return !hasIssue || reviewButtons(review.state).length > 0 ? 'pr' : null
}

/** An issue's relations, read when first shown and again once PREVIEW_MS old. */
function relationsOf(desk, number) {
  const id = `${desk}:${number}`
  const kept = relations.get(id)
  if (kept === undefined || (kept.status !== 'loading' && Date.now() - kept.at > PREVIEW_MS)) {
    relations.set(id, { status: 'loading', at: Date.now(), data: kept?.data })
    void (async () => {
      try {
        const res = await fetch(`/api/relations?desk=${encodeURIComponent(desk)}&number=${number}`)
        if (!res.ok) {
          throw new Error(String(res.status))
        }
        relations.set(id, { status: 'ready', at: Date.now(), data: await res.json() })
      } catch {
        relations.set(id, { status: 'error', at: Date.now(), data: kept?.data })
      }
      draw()
    })()
  }
  return relations.get(id)
}

/** The selected card's buttons, each with its key; one waiting for its confirm shows Confirm and Cancel. */
function Actions({ card }) {
  return card.actions.filter(action => LABELS[action]).map(action => {
    const main = FORWARD.has(action)
    const kind = main ? 'primary' : ['dismiss', 'close', 'cancel'].includes(action) ? 'danger' : undefined
    if (ui.confirming?.id === card.id && ui.confirming.action === action) {
      return html`<span class="confirm" key=${action}>
        <button type="button" class=${kind} onClick=${() => void act(card, action)}>
          Confirm ${actionLabel(card, action)} <kbd>${KEYS[action]}</kbd></button>
        <button type="button" onClick=${() => { ui.confirming = null; draw() }}>Cancel <kbd>Esc</kbd></button>
      </span>`
    }
    return html`<button type="button" key=${action} class=${kind} onClick=${() => press(card, action)}>
      ${actionLabel(card, action)} <kbd>${KEYS[action]}</kbd></button>`
  })
}

/**
 * Writes a tick on a task box in an issue's description, which shows the tick at once. Ticks on one issue go to the
 * server one at a time, each with the version of the description the last one left, and the description is read again
 * after each. A tick the server refuses, as on a description changed since the page read it, puts the box back, and
 * the server's reason shows.
 */
function tick(event, desk, number) {
  const box = event.target
  if (!(box instanceof HTMLInputElement) || box.dataset.task === undefined) {
    return
  }
  const id = `${desk}:issue-${number}`
  const checked = box.checked
  const task = Number(box.dataset.task)
  const done = (ticking.get(id) ?? Promise.resolve()).then(async () => {
    const sent = await post('/api/task', { desk, number, task, checked, version: previews.get(id)?.data?.version })
    if (!sent) {
      box.checked = !checked
    }
    reread('issue', desk, number, sent ? data => ({ ...data, version: sent.version }) : undefined)
  })
  ticking.set(id, done)
  void done.finally(() => ticking.get(id) === done && ticking.delete(id))
}

/**
 * One comment in a conversation, as GitHub draws it, with a 👀 under its text once the dispatcher asked for run `run`.
 * Given `onTick`, its task boxes take clicks. `anchor` names it for a link, and `parts` and `answering` draw its
 * Decisions Needed sections with their forms, as Body does. `editor`, while Edit changes the comment, takes the box's
 * place.
 */
function Comment({ author, at, edited, verb = 'commented', markup, parts, answering, anchor, opener, onTick, run,
  action, editor }) {
  return html`<div class="timeline-item" data-anchor=${anchor}>
    <${Avatar} person=${author} />
    ${editor ?? html`<div class=${opener ? 'comment opener' : 'comment'}>
      <div class="comment-head"><strong>${author.login}</strong> <span class="muted">${verb} ${age(at)} ago</span>
        <${EditedMark} at=${edited} />
        ${action ? html`<span class="head-actions">${action}</span>` : null}</div>
      <div class="comment-body" onChange=${onTick}><${Body} markup=${markup} parts=${parts} answering=${answering} />
        <${Reaction} run=${run} /></div>
    </div>`}
  </div>`
}

/**
 * What a comment box's Send to Agent sends, or null when the box has none: a review that can still change sends the
 * box's text with its unsent line comments, an idea sends the box's text for the agent to discuss, and any other open
 * issue sends it to the agent that works the issue. A closed issue, or a review that can't change, has no Send to Agent.
 */
export function sendOf(kind, item) {
  if (kind === 'pr') {
    return reviewButtons(item.state).includes('send') ? 'review' : null
  }
  return kind !== 'issue' ? null : item.idea === true ? 'idea' : item.state === 'open' ? 'issue' : null
}

/** The line under a comment box, which says what its Send to Agent sends, as sendOf names it. */
function footOf(send, waiting = 0) {
  return send === 'review' ? html`Markdown works here. Send to Agent sends this text${waiting > 0
    ? ` and ${waiting} unsent line comment${waiting === 1 ? '' : 's'}` : ''} to the agent, adding
      ${' '}<code>@agent</code> to the comment.`
    : send === 'idea' ? html`Markdown works here. Send to Agent adds <code>@agent</code> to the comment and asks the
      agent to discuss this idea; it changes no code.`
    : send === 'issue' ? html`Markdown works here. Send to Agent sends this text to the agent, adding
      ${' '}<code>@agent</code> to the comment, and asks for a run of this issue.`
    : 'Markdown works here.'
}

/**
 * Posts a comment box's text, and adds the comment to its conversation once the store has it. With `toAgent`, as an
 * issue's Send to Agent posts it, the comment goes to the agent. A Decisions Needed form posts `text` under its own
 * draft's key `id`. Returns whether the comment posted.
 */
async function postComment(desk, kind, number, toAgent = false, id = `${desk}:${number}`,
  text = ui.drafts.get(id) ?? '') {
  if (!text.trim() || ui.posting.has(id)) {
    return false
  }
  ui.posting.add(id)
  draw()
  const sent = await post('/api/comment', { desk, number, text, ...(toAgent ? { toAgent } : {}) })
  ui.posting.delete(id)
  if (sent) {
    ui.drafts.delete(id)
    ui.previewing.delete(id)
    const kept = previews.get(`${desk}:${kind}-${number}`)
    if (kept?.data) {
      kept.data = { ...kept.data, comments: [...kept.data.comments, cleanComment(sent.comment)],
        ...(kept.data.commentCount === undefined ? {} : { commentCount: kept.data.commentCount + 1 }) }
    }
    toast(toAgent ? `Sent your comment on #${number} to the agent.` : `Commented on #${number}.`)
  }
  draw()
  return sent !== null
}

/**
 * Closes an issue from its comment box, posting the box's text as a closing comment, and empties the box once done. A
 * close as a duplicate sends the original's number, and waits while the field names no issue it may duplicate.
 */
async function closeFromBox(card, number) {
  const id = `${card.desk}:${number}`
  const reason = ui.closeReasons.get(id) ?? 'completed'
  const original = reason === 'duplicate' ? duplicateCheck(card.desk, number, ui.duplicateOf.get(id) ?? '').original
    : undefined
  if (ui.posting.has(id) || (reason === 'duplicate' && original === undefined)) {
    return
  }
  ui.posting.add(id)
  draw()
  const sent = await post('/api/act', { desk: card.desk, key: card.key, action: 'close', reason,
    ...original !== undefined ? { duplicateOf: original } : {}, comment: ui.drafts.get(id) ?? '' })
  ui.posting.delete(id)
  if (sent) {
    ui.drafts.delete(id)
    ui.previewing.delete(id)
    ui.closeReasons.delete(id)
    ui.duplicateOf.delete(id)
  }
  draw()
}

/**
 * What the comment box's field for a duplicate's original names, as `text` holds it beside issue `number`: the
 * original's number once it is another issue of the project, which its preview confirms, and a note for beside the
 * field, its title or what is wrong, with `error` when the field names no issue it may duplicate.
 */
function duplicateCheck(desk, number, text) {
  const digits = text.trim().replace(/^#/, '')
  if (!/^\d{1,9}$/.test(digits)) {
    return { note: digits ? 'Give the original issue\'s number' : '', error: digits !== '' }
  }
  const original = Number(digits)
  if (original === number) {
    return { note: 'An issue can\'t duplicate itself', error: true }
  }
  const kept = preview('issue', desk, original)
  return kept.data ? { original, note: kept.data.title }
    : kept.status === 'error' ? { note: `No issue #${original}`, error: true }
    : { note: `Looking for #${original}…` }
}

/**
 * The box under a conversation that comments on it, as GitHub's. Its text stays put while the page redraws and when
 * another card is selected. An issue the board can close, including a queued or waiting one in Queued, also gets a
 * close button with a choice of reason, and a duplicate's original named in a field beside it. A review that can still
 * change, and an open issue, get Send to Agent, as sendOf says. `pr` is the review a review's box comments on, and
 * `issue` the issue an issue's box does.
 */
export function CommentForm({ desk, kind, number, closable, pr, issue, reload }) {
  const id = `${desk}:${number}`
  const posting = ui.posting.has(id)
  const me = ui.server?.me
  const send = sendOf(kind, pr ?? issue)
  // A review sends the box's text and its unsent line comments to the agent in one comment.
  const sendable = send === 'review'
  const waiting = sendable ? unsentCount(pr) : 0
  const hadText = (ui.drafts.get(id) ?? '').trim() !== ''
  // A close as a duplicate names the original in a field beside the reason, and Close waits until it names one.
  const duplicate = closable && ui.closeReasons.get(id) === 'duplicate'
    ? duplicateCheck(desk, number, ui.duplicateOf.get(id) ?? '') : null
  return html`<div class="timeline-item comment-form">
    <${Avatar} person=${me ?? { login: 'you' }} />
    <div class="comment">
      <${MarkdownBox} desk=${desk} id=${id} placeholder="Leave a comment" label=${`Comment on #${number}`}
        disabled=${posting}
        onInput=${() => {
          // The close button names the comment once there is one, so it redraws when the box empties or fills.
          if (closable && hadText !== ((ui.drafts.get(id) ?? '').trim() !== '')) {
            draw()
          }
        }}
        onKeyDown=${event => {
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault()
            void postComment(desk, kind, number)
          }
        }}>
        <div class="comment-foot">
          <span class="muted">${footOf(send, waiting)}</span>
          ${closable ? html`<span class="close-issue">
            <select aria-label=${`Close #${number} as`} disabled=${posting}
              value=${ui.closeReasons.get(id) ?? 'completed'}
              onChange=${event => { ui.closeReasons.set(id, event.target.value); draw() }}>
              <option value="completed">Completed</option>
              <option value="not planned">Not planned</option>
              <option value="duplicate">Duplicate</option>
            </select>
            ${duplicate ? html`<input type="text" class="duplicate-of" inputmode="numeric" placeholder="#"
              aria-label=${`Original issue #${number} duplicates`} disabled=${posting}
              value=${ui.duplicateOf.get(id) ?? ''} onInput=${event => { ui.duplicateOf.set(id, event.target.value)
              draw() }} />
              ${duplicate.note ? html`<span class=${duplicate.error ? 'duplicate-note error' : 'duplicate-note muted'}
                title=${duplicate.note}>${duplicate.note}</span>` : null}` : null}
            <button type="button" disabled=${posting || (duplicate && duplicate.original === undefined)}
              onClick=${() => void closeFromBox(closable, number)}>
              ${(ui.drafts.get(id) ?? '').trim() ? 'Close with Comment' : 'Close Issue'}</button></span>` : null}
          ${sendable ? html`<${ReviewButton} desk=${desk} pr=${pr} action="send" reload=${reload}
            text=${() => ui.drafts.get(id) ?? ''} />` : null}
          ${send === 'idea' || send === 'issue' ? html`<button type="button" disabled=${posting}
            onClick=${() => void postComment(desk, kind, number, true)}>Send to Agent</button>` : null}
          <button type="button" class="primary" disabled=${posting}
            onClick=${() => void postComment(desk, kind, number)}>${posting ? 'Commenting…' : 'Comment'}
            <kbd>Ctrl+Enter</kbd></button>
        </div>
      </${MarkdownBox}>
    </div>
  </div>`
}

/**
 * One tab's conversation: the description of `item`, an issue or a review as `kind` says, then its comments and the box
 * that comments on it, as boxOf says, which `hasIssue` decides for a review. `agentOn` adds a note to the description's
 * editor, since an agent that works or waits to work the issue read it already. `closable`, the card, lets an issue's
 * box close it. A merged or closed review of an issue ends in a line that points to the Issue tab in place of a box.
 */
function Conversation({ kind, item, desk, closable, hasIssue, agentOn }) {
  const box = boxOf(kind === 'issue' ? 'issue' : 'review', item, hasIssue)
  const reload = change => reread(kind, desk, item.number, change)
  const unsent = box === 'pr' && sendOf('pr', item) ? unsentCount(item) : 0
  const answering = box ? answeringOf(kind, item, desk, reload) : undefined
  return html`<div class="timeline">
    <${Comment} author=${item.author} at=${item.at} verb="opened this" markup=${item.body} parts=${item.bodyParts}
      answering=${answering} anchor=${kind === 'issue' ? 'issue-body' : 'review-body'} opener=${true}
      onTick=${kind === 'issue' ? event => tick(event, desk, item.number) : undefined}
      action=${html`<${EditButton} desk=${desk} number=${item.number} field="body" small=${true} />`}
      editor=${editing(desk, item.number, 'body') ? html`<${BodyEdit} desk=${desk} number=${item.number}
        reload=${reload} agentOn=${agentOn} />` : undefined} />
    ${item.comments.map((one, index) => html`<${Comment} key=${index} author=${one.author} at=${one.at}
      edited=${one.edited} markup=${one.html} parts=${one.parts} answering=${answering} anchor=${`comment-${one.id}`}
      run=${one.run} ...${commentEdit(desk, item.number, one, reload)} />`)}
    ${unsent > 0 ? html`<div class="timeline-note muted"><${Icon} name="waiting" tone="attention" />
      <span>${unsent} line comment${unsent === 1 ? '' : 's'} on <button type="button" class="link"
        onClick=${() => pickDetailTab('files')}>Files Changed</button> wait${unsent === 1 ? 's' : ''} unsent; Send
        to Agent sends ${unsent === 1 ? 'it' : 'them'} with your comment.</span></div>` : null}
    ${box ? html`<${CommentForm} desk=${desk} kind=${kind} number=${item.number}
      closable=${kind === 'issue' ? closable : undefined} pr=${kind === 'pr' ? item : undefined}
      issue=${kind === 'issue' ? item : undefined} reload=${reload} />`
      : html`<div class="timeline-note muted"><${Icon} name=${item.state === 'merged' ? 'merged' : 'pr'}
        tone="muted" /><span>This review is ${item.state}. Comment on its issue in the <button type="button"
        class="link" onClick=${() => pickDetailTab('issue')}>Issue</button> tab.</span></div>`}
  </div>`
}

/**
 * What a conversation's Decisions Needed forms post through, as the box at its bottom posts: Comment always, and Send
 * to Agent when the box has it, which for a review sends its unsent line comments too.
 */
export function answeringOf(kind, item, desk, reload) {
  const send = sendOf(kind, item)
  return {
    desk, number: item.number, comments: item.comments, send: send !== null,
    busy: key => ui.posting.has(key) || ui.reviewBusy.has(`${desk}:${item.number}:send`),
    post: (key, text, toAgent) => toAgent && send === 'review' ? reviewAct(desk, item, 'send', reload, text, key)
      : postComment(desk, kind, item.number, toAgent, key, text),
  }
}

/** What a preview shows while it is read, or when it couldn't be. */
function Waiting({ kept, what }) {
  return kept?.status === 'error' ? html`<p class="error">Could not read the ${what}.</p>`
    : html`<p class="muted">Reading the ${what}…</p>`
}

function checkTone(check) {
  return ['success', 'neutral', 'skipped'].includes(check.state) ? 'open'
    : ['failure', 'error', 'timed_out', 'cancelled', 'action_required', 'startup_failure'].includes(check.state)
      ? 'danger' : 'attention'
}

/** A PR's checks, each with how it stands and a link to its run. */
function Checks({ pr }) {
  return html`<div class="box">${pr.checks.length === 0 ? html`<div class="empty">No checks ran.</div>`
    : pr.checks.map(check => html`<div class="box-row" key=${check.name}>
      <${Icon} name=${checkTone(check) === 'open' ? 'closed' : checkTone(check) === 'danger' ? 'failed' : 'waiting'}
        tone=${checkTone(check)} /><strong>${check.name}</strong>
      <span class="muted">${check.state.replace(/_/g, ' ')}</span>
      ${check.url ? html`<a class="details" href=${check.url} target="_blank" rel="noopener">Details</a>` : null}
    </div>`)}</div>`
}

/**
 * The newest agent session's log for an issue: read again every LOG_MS while its agent works, and once a
 * PREVIEW_MS otherwise.
 */
function logOf(desk, number, live) {
  const id = `${desk}:${number}`
  const kept = ui.logs.get(id)
  const stale = Date.now() - (kept?.at ?? 0) > (live ? LOG_MS : PREVIEW_MS)
  if (kept === undefined || (kept.status !== 'loading' && stale)) {
    ui.logs.set(id, { status: 'loading', at: Date.now(), data: kept?.data })
    void (async () => {
      try {
        const res = await fetch(`/api/log?desk=${encodeURIComponent(desk)}&number=${number}`)
        ui.logs.set(id, res.ok ? { status: 'ready', at: Date.now(), data: await res.json() }
          : { status: 'none', at: Date.now() })
      } catch {
        ui.logs.set(id, { status: 'error', at: Date.now(), data: kept?.data })
      }
      draw()
    })()
  }
  return ui.logs.get(id)
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    toast(`Copied: ${text}`)
  } catch {
    toast(`Run in a terminal: ${text}`)
  }
}

const STAMP = /^(\d\d:\d\d:\d\d) /

/**
 * The agent's log lines joined by newlines, with the `HH:MM:SS` time that leads a line in its own `log-time` span, so
 * the times stand out while the log's text copies as before. Each line whose index `beats` lists, a beat logged while
 * the agent waited on a quiet tool call, goes in a muted `log-beat` span, so the agent's own lines stand out.
 */
export function logLines(lines, beats = []) {
  const muted = new Set(beats)
  return lines.flatMap((line, at) => {
    const stamp = STAMP.exec(line)
    const parts = stamp ? [html`<span class="log-time">${stamp[1]}</span>`, line.slice(stamp[1].length)] : [line]
    const shown = muted.has(at) ? [html`<span class="log-beat">${parts}</span>`] : parts
    return at ? ['\n', ...shown] : shown
  })
}

/**
 * The agent's log for an issue, as its Actions run would show it, from the newest Claude Code session in the issue's
 * worktree on this machine. It follows the newest line while the agent works, until the person scrolls up.
 */
function AgentLog({ desk, number, live }) {
  const kept = logOf(desk, number, live)
  const log = kept.data
  if (!log) {
    return kept.status === 'none' ? html`<p class="muted">No agent session has run for #${number} on this machine.</p>`
      : kept.status === 'error' ? html`<p class="error">Could not read the agent's log.</p>`
      : html`<p class="muted">Reading the agent's log…</p>`
  }
  const resume = `cd ${log.worktree} && claude --resume ${log.session}`
  return html`<div class="box agent-log-box">
    <div class="box-head static">
      ${live ? html`<span class="live">Live</span>` : null}
      <span>Session <code>${log.session.slice(0, 8)}</code></span>
      <span class="muted">updated ${age(log.at)} ago${log.sessions > 1 ? ` · newest of ${log.sessions} sessions` : ''}
        </span>
      <span class="head-actions">
        <button type="button" class="small" title=${resume} onClick=${() => void copy(resume)}>Copy Take Over Command
          </button>
      </span>
    </div>
    <pre class="agent-log" onScroll=${event => {
      const pre = event.currentTarget
      ui.logStick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24
    }}>${logLines(log.lines, log.beats)}</pre>
  </div>`
}

const PR_STATES = { open: ['Open', 'pr', 'open'], draft: ['Draft', 'pr', 'muted'], merged: ['Merged', 'merged', 'done'],
  closed: ['Closed', 'pr', 'danger'] }
const RUN_STATES = { failed: ['Failed', 'failed', 'danger'], 'timed out': ['Timed Out', 'stopped', 'attention'] }
const ISSUE_STATES = { open: ['Open', 'issue', 'open'], completed: ['Closed', 'closed', 'done'],
  not_planned: ['Closed', 'blocked', 'muted'], duplicate: ['Closed', 'blocked', 'muted'] }

/** A PR's or issue's state as GitHub's round label shows it. */
function StateLabel({ state, states }) {
  const [title, icon, tone] = states[state] ?? states.open
  return html`<span class=${`state ${tone}`}><${Icon} name=${icon} tone="" />${title}</span>`
}

/** A section of the sidebar under its title, with `action`, such as the Labels section's Edit, beside the title. */
function SideSection({ title, action, children }) {
  return html`<div class="side-section">${action ? html`<div class="side-head"><h4>${title}</h4>${action}</div>`
    : html`<h4>${title}</h4>`}${children}</div>`
}

/**
 * The Status section's lines under the stage: the card's note, and how long a Needs You card has waited on you. A
 * failing card's note names what went wrong, so it shows in the danger tone after the failed icon; any other note
 * stays muted. The wait sits on its own line, apart from the note, with the time the card entered Needs You on hover.
 */
export function StatusNote({ card }) {
  const failing = card.state === 'failing'
  const waits = card.stage === 'needs' && card.since
  return [
    card.note ? html`<div key="note" class=${failing ? 'status-line failure' : 'status-line muted'}>${failing
      ? html`<${Icon} name="failed" tone="danger" />` : null}<span>${card.note}</span></div>` : null,
    waits ? html`<div key="wait" class="status-line status-wait muted" title=${`In Needs You since ${
      stampOf(card.since)}`}><${Icon} name="waiting" tone="muted" /><span>Waiting on you for ${age(card.since)}</span>
      </div>` : null,
  ]
}

/** An ISO stamp as the local date and time, such as Nov 12, 9:41 PM. */
function stampOf(stamp) {
  return new Date(stamp).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/**
 * The selected card as GitHub shows an issue or a PR, with a sidebar of where the card stands. An issue with a review
 * shows the review on its page, under the issue's title and number. Until the person picks a tab, the page shows the
 * card's first tab, as firstTab says. `visible` holds the ids of the cards the list shows, which a dependency's title
 * selects.
 */
export function Detail({ card, visible }) {
  const issue = issueOf(card)
  const issueKept = issue !== undefined ? preview('issue', card.desk, issue, (card.labels ?? []).join(',')) : null
  const issueData = issueKept?.data
  const reviewNumber = shownReview(card, issueData)
  const prKept = reviewNumber !== undefined ? preview('pr', card.desk, reviewNumber) : null
  const pr = prKept?.data
  const item = pr ?? issueData
  const number = issue ?? card.pr ?? card.number
  const title = issueData?.title ?? (issue === undefined ? pr?.title : undefined) ?? card.title
  const target = titleTarget(card)
  const titleEditing = target !== undefined && editing(card.desk, target.number, 'title')
  // An agent that works or waits to work the issue read its description when it started.
  const agentOn = card.stage === 'working' || card.stage === 'queued'
  const stage = STAGES.find(([one]) => one === card.stage)
  const related = issue !== undefined ? relationsOf(card.desk, issue).data : undefined
  const hasRelated = related && (related.parent || related.subs.length + related.blockedBy.length
    + related.blocking.length > 0)
  const tabs = tabsOf({ issue, issueData, review: reviewNumber, pr, related: hasRelated ? related : undefined })
  const wanted = ui.detailTab ?? firstTab(card, reviewNumber !== undefined)
  const shown = tabs.some(([id]) => id === wanted) ? wanted : tabs[0]?.[0]
  // The Files Changed tab gives the sidebar's room to the diffs and the folder tree beside them.
  const shownFiles = shown === 'files' && pr
  // The state label and the line beside it, which both the header and the bar that stays at the top show.
  const state = pr ? html`<${StateLabel} state=${pr.state} states=${PR_STATES} />`
    : item ? html`<${StateLabel} state=${item.state} states=${ISSUE_STATES} />`
    : card.run !== undefined ? html`<${StateLabel} state=${card.state} states=${RUN_STATES} />` : null
  const merging = pr && `${pr.state === 'merged' ? 'merged' : 'wants to merge'} ${pr.commits} commit${
    pr.commits === 1 ? '' : 's'} into`
  // On an issue's page the review's number follows its state, since the title carries the issue's.
  const meta = pr ? html`<span>${issue !== undefined ? html`<span class="muted">#${pr.number}</span> ` : null}
      <strong>${pr.author.login}</strong> ${merging} <code class="branch">${pr.base}</code>
      ${' from '}<code class="branch">${pr.head}</code></span>`
    : item ? html`<span><strong>${item.author.login}</strong> opened this issue ${age(item.at)} ago ·
      ${` ${item.commentCount} comment${item.commentCount === 1 ? '' : 's'}`}</span>`
    : card.run !== undefined ? html`<span>An agent-desk run ${card.state === 'timed out'
      ? 'ran past its time limit' : 'failed'}${card.note ? ` in ${card.note}` : ''}.</span>`
    : html`<span class="muted">${card.chore ? 'A chore.' : card.request !== undefined ? 'A run that waits to start.'
      : 'Reading…'}</span>`
  // An issue closed as a duplicate names its original after the line, on the issue's page and beside its review.
  const original = issueData?.state === 'duplicate' ? issueData.duplicateOf : undefined
  const duplicate = original ? html`<span class="duplicate-line">Closed as a duplicate of <a
    href=${itemHref(card.desk, original.number)} title=${original.title}>#${original.number}</a></span>` : null
  // An open or draft review's Close Without Merging, after the card's actions in both headers.
  const reviewActions = pr ? headerReviewButtons(pr.state).map(action => html`<${ReviewButton} key=${action}
    desk=${card.desk} pr=${pr} action=${action} reload=${change => reread('pr', card.desk, pr.number, change)}
    hotkey=${REVIEW_KEYS[action]} />`) : null
  // The issue's labels, or a review's without an issue; a review without labels of its own shows its card's.
  const labels = [issueData, pr].find(one => one?.labels.length)?.labels
    ?? (card.labels ?? []).map(name => labelOf(card.desk, name))
  // The item the Labels section's Edit changes, the one the title's Edit does, once its preview is read.
  const labelled = target?.kind === 'issue' ? issueData : target?.kind === 'pr' ? pr : undefined
  return html`<article>
    <div class="sticky-holder">
      <div class=${ui.stuck ? 'gh-sticky shown' : 'gh-sticky'} aria-hidden=${String(!ui.stuck)}>
        ${state}
        <div class="sticky-text">
          <div class="sticky-title"><strong>${title}</strong>
            ${number === undefined ? null : html` <span class="muted">#${number}</span>`}</div>
          <div class="sticky-meta">${meta}${duplicate}</div>
        </div>
        <div class="actions"><${Actions} card=${card} />${reviewActions}<${CloseButton} /></div>
      </div>
    </div>
    <div class="gh-header">
      <div class="gh-title">
        ${titleEditing ? html`<${TitleEdit} desk=${card.desk} number=${target.number}
          reload=${change => reread(target.kind, card.desk, target.number, change)} />`
          : html`<h1>${title} ${number === undefined ? null : html`<a class="muted" href=${itemHref(card.desk, number)}
            target="_blank" rel="noopener">#${number}</a>`}
          </h1>`}
        <div class="actions">${target && !titleEditing ? html`<${EditButton} desk=${card.desk} number=${target.number}
          field="title" />` : null}<${Actions} card=${card} />${reviewActions}<${CloseButton} /></div>
      </div>
      <div class="gh-meta">${state}${meta}${duplicate}</div>
    </div>
    <div class=${shownFiles ? 'gh-layout no-side' : 'gh-layout'}>
      <div class="gh-main">
        ${card.chore ? html`<p>${card.chore.name} runs every ${card.chore.every}. Its last run: ${card.chore.last}.</p>`
          : null}
        ${card.state === 'timed out' ? html`<p>The dispatcher stopped the run at its unit's time limit. Its
          worktree and any commits it made stay on this machine, and ${card.continues
            ? 'Continue takes up the same agent session in them' : 'Retry starts a new agent from them'}.</p>` : null}
        ${card.run !== undefined && card.log ? html`<p>Read the run's log in a terminal with
          <code>${card.log}</code>${' '}<button type="button" class="small"
          onClick=${() => void copy(card.log)}>Copy</button>${issue !== undefined
          ? html`, or its agent's session in the <button type="button" class="link"
            onClick=${() => pickDetailTab('log')}>Agent Log</button> tab` : null}.</p>` : null}
        ${tabs.length > 0 ? html`<nav class=${ui.pinned ? 'tabnav pinned' : 'tabnav'}>${tabs.map(([id, title, count]) =>
          html`<button type="button" key=${id} aria-current=${String(id === shown)}
            onClick=${() => pickDetailTab(id)}>${title}
          ${count === undefined ? null : html`<span class="counter">${count}</span>`}</button>`)}
          ${pr ? html`<span class="tabnav-extra"><${Lines} additions=${pr.additions} deletions=${pr.deletions} />
            <${Diffstat} additions=${pr.additions} deletions=${pr.deletions} /></span>` : null}
        </nav>` : null}
        ${shown === 'log' ? html`<${AgentLog} desk=${card.desk} number=${issue} live=${card.stage === 'working'} />`
          : shown === 'deps' && hasRelated ? html`<${Dependencies} desk=${card.desk} number=${issue}
            related=${related} visible=${visible} />`
          : shown === 'files' && pr ? html`<${Files} pr=${pr} desk=${card.desk}
            reload=${change => reread('pr', card.desk, pr.number, change)} />`
          : shown === 'checks' && pr ? html`<${Checks} pr=${pr} />`
          : shown === 'issue' ? issueData ? html`<${Conversation} kind="issue" item=${issueData} desk=${card.desk}
            closable=${card.actions.includes('close') ? card : undefined} agentOn=${agentOn} />`
            : html`<${Waiting} kept=${issueKept} what="issue" />`
          : shown === 'review' ? pr ? html`<${Conversation} kind="pr" item=${pr} desk=${card.desk}
            hasIssue=${issue !== undefined} agentOn=${agentOn} />`
            : html`<${Waiting} kept=${prKept} what="review" />` : null}
      </div>
      ${shownFiles ? null : html`<aside class="gh-side">
        <${SideSection} title="Project"><${Chip} desk=${card.desk} /></${SideSection}>
        <${SideSection} title="Status">
          <div><span style=${{ color: `var(--${stage?.[2]})` }}>${stage?.[1]}</span>
            ${card.state ? html` <span class=${`badge ${card.state}`}>${card.state}</span>` : null}</div>
          <${StatusNote} card=${card} />
        </${SideSection}>
        ${card.runner ? html`<${SideSection} title="Runner"><div><span class="runner">${card.runner}</span>
          <span class="muted"> for ${card.time ?? ''}</span></div>${issue !== undefined ? html`<button type="button"
          class="link" onClick=${() => pickDetailTab('log')}>Agent Log</button>` : null}</${SideSection}>` : null}
        ${hasRelated ? html`<${SideSection} title="Dependencies"><${DepsSummary} related=${related} />
          </${SideSection}>` : null}
        <${SideSection} title="Labels" action=${labelled ? html`<${LabelsButton} desk=${card.desk}
          number=${target.number} />` : null}>${labels.length > 0 ? html`<div class="labels">${labels.map(label =>
          html`<${Label} key=${label.name} label=${label} />`)}</div>` : html`<span class="muted">None Yet</span>`}
          ${labelled && editingLabels(card.desk, target.number) ? html`<${LabelsEdit} desk=${card.desk}
            number=${target.number} picked=${labelled.labels.map(label => label.name)}
            reload=${change => reread(target.kind, card.desk, target.number, change)} />` : null}
        </${SideSection}>
        <${SideSection} title="Development">
          ${issue !== undefined ? html`<${IssueReviews} card=${card} issue=${issue} reviews=${issueData?.reviews}
            shown=${reviewNumber} />` : null}
          ${issue === undefined && card.pr !== undefined ? html`<a href=${itemHref(card.desk, card.pr)} target="_blank"
            rel="noopener"><${Icon} name=${pr?.state === 'merged' ? 'merged' : 'pr'}
              tone=${pr?.state === 'merged' ? 'done' : 'open'} />
            PR #${card.pr}</a>` : null}
          ${pr?.issues.filter(one => one.number !== issue).map(one => html`<a key=${one.number} href=${one.url}
            target="_blank" rel="noopener"><${Icon} name="issue" tone="open" /> #${one.number} ${one.title}</a>`)}
        </${SideSection}>
        ${issueData?.deletable ? html`<${DeleteButton} desk=${card.desk} number=${issue} />` : null}
      </aside>`}
    </div>
    ${issue !== undefined ? html`<${DeleteDialog} desk=${card.desk} number=${issue} title=${title} />` : null}
  </article>`
}

/**
 * The Development section of an issue's page: the issue, then its reviews, newest first, each with its state. Pressing
 * a review shows its conversation on the Review tab and its files on the page; pressing the review shown goes back to
 * the open review, and pressing the issue goes back to it and shows the Issue tab.
 */
function IssueReviews({ card, issue, reviews = [], shown }) {
  const listed = card.pr === undefined || reviews.some(one => one.number === card.pr) ? reviews
    : [...reviews, { number: card.pr, title: card.title, state: 'open' }]
  return html`<div class="dev-items">
    <button type="button" class="dev-item" aria-current=${String(shown === undefined)}
      onClick=${() => pickReview(card, undefined, shown)}><${Icon} name="issue" tone="open" /> Issue #${issue}</button>
    ${[...listed].reverse().map(one => {
      const [title, icon, tone] = PR_STATES[one.state] ?? PR_STATES.open
      return html`<button type="button" key=${one.number} class="dev-item" aria-current=${String(one.number === shown)}
        title=${one.title} onClick=${() => pickReview(card, one.number, shown)}><${Icon} name=${icon} tone=${tone} />
        <span>Review #${one.number}</span><span class=${`dev-state ${tone}`}>${title}</span></button>`
    })}
  </div>`
}

/** The card for an issue in a project, wherever the list keeps it, or undefined when the list has none. */
function cardOfIssue(desk, number) {
  return deskOf(desk)?.cards.find(card => card.id === `${desk}:${number}`)
}

/** The sidebar's summary of an issue's dependencies: its parent, how many sub-issues are done, and what blocks it. */
function DepsSummary({ related }) {
  const done = related.subs.filter(one => one.state !== 'open').length
  const open = related.blockedBy.filter(one => one.state === 'open')
  const blocks = related.blocking.filter(one => one.state === 'open').length
  const tab = () => pickDetailTab('deps')
  return html`<div class="deps-summary">
    ${related.parent ? html`<div>Parent <button type="button" class="link" onClick=${tab}>${related.parent.ref}</button>
      </div>` : null}
    ${related.subs.length > 0 ? html`<div><button type="button" class="link" onClick=${tab}>${
      `${done} of ${related.subs.length} sub-issues done`}</button>
      <${Progress} done=${done} total=${related.subs.length} /></div>` : null}
    ${open.length > 0 ? html`<div class="attention-text">Blocked by ${open.map(one => one.ref).join(', ')}</div>`
      : related.blockedBy.length > 0 ? html`<div class="muted">Its blockers are closed</div>` : null}
    ${blocks > 0 ? html`<div class="muted">Blocks ${blocks} open issue${blocks === 1 ? '' : 's'}</div>` : null}
  </div>`
}

function Progress({ done, total }) {
  return html`<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax=${total} aria-valuenow=${done}>
    <span style=${{ width: `${total ? (100 * done) / total : 0}%` }}></span></div>`
}

/**
 * An issue's dependency tree: its parent, its sub-issues, the issues blocking it, and the issues it blocks. Each row
 * shows its card's group when the list has one, and opens into its own sub-issues and open blockers.
 */
function Dependencies({ desk, number, related, visible }) {
  const hide = ui.depsHideClosed
  const kept = list => (hide ? list.filter(one => one.state === 'open') : list)
  const done = related.subs.filter(one => one.state !== 'open').length
  const section = (title, list, extra) => (list.length > 0 ? html`<section class="deps-section">
    <h3>${title}${extra}</h3>
    ${kept(list).length > 0 ? html`<ul class="deps-tree">${kept(list).map(one => html`<${DepRow} key=${one.ref}
      desk=${desk} item=${one} path=${[number]} visible=${visible} />`)}</ul>` : html`<p class="muted">All closed.</p>`}
  </section>` : null)
  return html`<div class="deps">
    <label class="deps-hide"><input type="checkbox" checked=${hide} onChange=${event => {
      ui.depsHideClosed = event.target.checked
      remember('deps-hide-closed', ui.depsHideClosed)
      draw()
    }} /> Hide closed issues</label>
    ${related.parent ? html`<section class="deps-section"><h3>Parent</h3><ul class="deps-tree">
      <${DepRow} desk=${desk} item=${related.parent} path=${[number]} visible=${visible} /></ul></section>` : null}
    ${section('Sub-issues', related.subs, html` <span class="muted">${done} of ${related.subs.length} done</span>
      <${Progress} done=${done} total=${related.subs.length} />`)}
    ${section('Blocked by', related.blockedBy, null)}
    ${section('Blocking', related.blocking, null)}
  </div>`
}

/**
 * One issue in the dependency tree: its state, number, and title, its card's group, how many of its sub-issues are
 * done, and its open blockers. `path` holds the numbers of the rows above it, so an issue never opens inside itself.
 */
function DepRow({ desk, item, path, blocker, visible }) {
  const card = item.local ? cardOfIssue(desk, item.number) : undefined
  const [icon, tone] = card ? iconOf(card) : item.state === 'open' ? ['issue', 'open']
    : item.state === 'completed' ? ['closed', 'done'] : ['blocked', 'muted']
  const id = `${desk}:${[...path, item.number].join('/')}`
  const opens = item.local && !path.includes(item.number) && (item.subs.total > 0 || item.blockers.length > 0)
  const open = opens && ui.depsOpen.has(id)
  const stage = card && STAGES.find(([one]) => one === card.stage)
  const shown = card && visible.has(card.id)
  const go = event => {
    if (shown) {
      event.preventDefault()
      select(card.id)
      ui.detailTab = 'deps'
      draw()
    }
  }
  const toggle = () => {
    if (open) {
      ui.depsOpen.delete(id)
    } else {
      ui.depsOpen.add(id)
    }
    draw()
  }
  const children = open ? relationsOf(desk, item.number) : null
  const hide = ui.depsHideClosed
  const kids = children?.data ? [
    ...children.data.subs.filter(one => !hide || one.state === 'open').map(one => [one, false]),
    ...children.data.blockedBy.filter(one => one.state === 'open').map(one => [one, true]),
  ] : []
  return html`<li class=${item.state === 'open' ? 'dep' : 'dep closed'}>
    <div class="dep-row">
      ${opens ? html`<button type="button" class="dep-toggle" aria-expanded=${String(open)}
        aria-label=${`${open ? 'Close' : 'Open'} ${item.ref}`} onClick=${toggle}>${open ? '▾' : '▸'}</button>`
        : html`<span class="dep-toggle"></span>`}
      <${Icon} name=${icon} tone=${tone} />
      ${blocker ? html`<span class="badge blocker">blocks it</span>` : null}
      <a class="dep-title" href=${item.url} target="_blank" rel="noopener" onClick=${go}
        title=${shown ? 'Show its card' : 'Show it'}><span class="muted">${item.ref}</span> ${item.title}</a>
      ${stage ? html`<span class="dep-stage" style=${{ color: `var(--${stage[2]})` }}>${stage[1]}</span>` : null}
      ${card?.state ? html`<span class=${`badge ${card.state}`}>${card.state}</span>` : null}
      ${item.subs.total > 0 ? html`<span class="muted">${item.subs.done}/${item.subs.total}</span>` : null}
      ${item.state === 'open' && item.blockers.length > 0 ? html`<span class="attention-text">${`blocked by ${
        item.blockers.length > 3 ? `${item.blockers.slice(0, 3).join(', ')} and ${item.blockers.length - 3} more`
          : item.blockers.join(', ')}`}</span>` : null}
    </div>
    ${open ? children?.data ? kids.length > 0 ? html`<ul class="deps-tree">${kids.map(([one, isBlocker]) => html`
      <${DepRow} key=${`${isBlocker ? 'b' : 's'}${one.ref}`} desk=${desk} item=${one}
        path=${[...path, item.number]} blocker=${isBlocker} visible=${visible} />`)}</ul>`
      : html`<p class="muted dep-note">All closed.</p>`
      : html`<p class=${children?.status === 'error' ? 'error dep-note' : 'muted dep-note'}>${
        children?.status === 'error' ? 'Could not read it.' : 'Reading…'}</p>` : null}
  </li>`
}

/**
 * Pins the selected card's compact bar to the top of the panel once its header scrolls out of view, as GitHub does on
 * an issue or PR page. The bar shows by the time the tabs reach it and pin under it, even while the header's last
 * line is in view, so the tabs never pin under an empty strip. A narrow window scrolls the page instead of the panel,
 * and the page's scroll calls it too. It also marks the file whose diff is in view in the Files Changed tab's tree.
 */
export function onDetailScroll(panel) {
  const scroller = scrollerOf(panel)
  const top = scroller === panel ? panel.getBoundingClientRect().top : 0
  const header = panel.querySelector('.gh-header')
  const tabs = panel.querySelector('.gh-main > .tabnav')
  const pinned = tabs !== null && tabs.getBoundingClientRect().top - top <= barHeight(panel) + 0.5
  const stuck = pinned || header !== null && header.getBoundingClientRect().bottom - top < 12
  if (stuck !== ui.stuck || pinned !== ui.pinned) {
    ui.stuck = stuck
    ui.pinned = pinned
    draw()
  }
  markFileInView(panel, scroller)
}

// What pins at the top of the selected card's panel, each with the custom property its height goes into: the compact
// bar, the tabs that pin below it, and the Files Changed tab's files bar that pins below the tabs.
const PINNED = [['.gh-sticky', '--bar-height'], ['.gh-main > .tabnav', '--tabs-height'],
  ['.files-bar', '--files-bar-height']]

/**
 * Measures what pins at the top of the panel into its custom properties and returns the compact bar's height, the
 * offset its tabs pin at. The bar keeps its layout while hidden, and each pinned part grows as its contents wrap.
 */
export function barHeight(panel) {
  const [bar] = PINNED.map(([selector, property]) => {
    const height = panel.querySelector(selector)?.offsetHeight ?? 0
    if (panel.style.getPropertyValue(property) !== `${height}px`) {
      panel.style.setProperty(property, `${height}px`)
    }
    return height
  })
  return bar
}

/**
 * Scrolls the panel so a tab picked while the tabs are pinned shows from its top, with the tabs just where they pin.
 * The tabs' place in the flow comes from laying them out unpinned for a moment; a tab picked before they pin leaves
 * the panel where it is.
 */
export function showTabTop(panel) {
  const tabs = panel.querySelector('.gh-main > .tabnav')
  if (!tabs) {
    return
  }
  const pinnedAt = tabs.getBoundingClientRect().top
  tabs.style.position = 'static'
  const flowAt = tabs.getBoundingClientRect().top
  tabs.style.position = ''
  if (flowAt < pinnedAt) {
    scrollerOf(panel).scrollTop -= pinnedAt - flowAt
  }
}
