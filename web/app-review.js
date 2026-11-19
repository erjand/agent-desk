// A review's diff and the work on it, as GitHub's PR page shows them: its files with the comments on their lines, a
// box that adds one, the comments a later commit left outdated above the diff, and the buttons that send the comments
// to the agent or close the review. `reload` reads the review's preview again, after changing what it shows meanwhile
// when given a change.
import { html } from './vendor/preact-htm.js'
import { commentEdit, EditedMark } from './app-edit.js'
import { Avatar, clean, Markup, Reaction } from './app-parts.js'
import { age, draw, post, remember, toast, ui } from './app-state.js'

// What each of a review's buttons reads, and reads while its request runs.
export const REVIEW_LABELS = { send: 'Send to Agent', close: 'Close Without Merging' }
const BUSY_LABELS = { send: 'Sending…', close: 'Closing…' }
// The buttons that ask to confirm before they act.
const REVIEW_CONFIRMS = new Set(['close'])
// The keys that press the buttons in a review's header, apart from the card's actions in KEYS.
export const REVIEW_KEYS = { close: 'l' }
// A file with more diff rows than this starts collapsed, as GitHub folds a large diff.
const OPEN_DIFF_ROWS = 400
const SIGNS = { add: '+', del: '−', same: ' ' }
// Each file status's word, for the title of its mark in the file tree.
const STATUS_TITLES = { added: 'Added', deleted: 'Deleted', renamed: 'Renamed', modified: 'Modified' }
// The glyph inside each status mark's square.
const STATUS_GLYPHS = {
  added: html`<path d="M8 5.5v5M5.5 8h5" />`,
  deleted: html`<path d="M5.5 8h5" />`,
  renamed: html`<path d="M5 8h6M8.5 5.5 11 8l-2.5 2.5" />`,
  modified: html`<circle cx="8" cy="8" r="1.5" fill="currentColor" stroke="none" />`,
}
// How far below the pinned files bar a file's box stops when the tree scrolls to it, in pixels.
const FILE_GAP = 8
// The custom properties the panel's scroll handler measures the heights of what pins at its top into, as PINNED in
// app-detail.js lists.
const PINNED_HEIGHTS = ['--bar-height', '--tabs-height', '--files-bar-height']

/** The key of the comment box on line `line` of file `path` in a review, which its draft and state go by. */
export function lineKey(desk, number, path, line) {
  return `${desk}:${number}:${path}:${line}`
}

/**
 * A review's line comments by where the page shows them: under a row of the diff, by `<path>:<line>`, or above the
 * diff, for one a later commit left outdated or one on a line the diff doesn't show.
 */
export function placeComments(files, comments) {
  const rows = new Set(files.flatMap(file => (file.lines ?? [])
    .flatMap(row => (row.kind === 'add' || row.kind === 'same' ? [`${file.path}:${row.new}`] : []))))
  const onLines = new Map()
  const above = []
  for (const comment of comments) {
    const where = `${comment.path}:${comment.shown}`
    if (comment.shown !== null && rows.has(where)) {
      onLines.set(where, [...(onLines.get(where) ?? []), comment])
    } else {
      above.push(comment)
    }
  }
  return { onLines, above }
}

/** How many of a review's line comments wait for Send to agent. */
export function unsentCount(pr) {
  return (pr.lineComments ?? []).filter(comment => !comment.sent).length
}

/** The buttons a review's state offers: none once it is merged or closed. */
export function reviewButtons(state) {
  return state === 'open' || state === 'draft' ? ['send', 'close'] : []
}

/**
 * The review's buttons its panel's header shows after the card's actions. Send to Agent stays in the comment box,
 * whose text it sends.
 */
export function headerReviewButtons(state) {
  return reviewButtons(state).filter(action => action !== 'send')
}

function counted(count, noun) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

/**
 * Presses one of a review's buttons: asks the server, says how it went, and reads the review again. Send to agent sends
 * `text` with the unsent line comments, and empties the draft it came from once sent: the conversation's box, or the
 * form `draft` names. Returns whether it worked.
 */
export async function reviewAct(desk, pr, action, reload, text = '', draft = `${desk}:${pr.number}`) {
  const key = `${desk}:${pr.number}:${action}`
  if (ui.reviewBusy.has(key)) {
    return false
  }
  ui.reviewBusy.add(key)
  ui.reviewConfirm = null
  draw()
  const sent = await post('/api/review', { desk, number: pr.number, action, text })
  ui.reviewBusy.delete(key)
  if (sent) {
    if (action === 'send') {
      ui.drafts.delete(draft)
      ui.previewing.delete(draft)
    }
    toast(sent.said ?? 'Done.')
    reload()
  }
  draw()
  return sent !== null
}

/**
 * Presses one of a review's buttons: Close Without Merging waits for its confirm, which takes the place of any card
 * action's, and Send to Agent acts at once.
 */
export function pressReview(desk, pr, action, reload, text = '') {
  const key = `${desk}:${pr.number}:${action}`
  if (ui.reviewBusy.has(key)) {
    return
  }
  if (REVIEW_CONFIRMS.has(action)) {
    ui.reviewConfirm = key
    ui.confirming = null
    draw()
  } else {
    void reviewAct(desk, pr, action, reload, text)
  }
}

/**
 * One of a review's buttons; Close asks to confirm first, and each is disabled while its request runs. Given `hotkey`,
 * it shows the key that presses it, as the header's buttons do.
 */
export function ReviewButton({ desk, pr, action, reload, text, small, hotkey }) {
  const key = `${desk}:${pr.number}:${action}`
  const busy = ui.reviewBusy.has(key)
  const kind = action === 'close' ? 'danger' : undefined
  const cls = [kind, small ? 'small' : undefined].filter(Boolean).join(' ') || undefined
  const hint = name => (hotkey ? html` <kbd>${name}</kbd>` : null)
  if (ui.reviewConfirm === key) {
    return html`<span class="confirm">
      <button type="button" class=${cls} onClick=${() => void reviewAct(desk, pr, action, reload, text?.() ?? '')}>
        Confirm ${REVIEW_LABELS[action]}${hint(hotkey)}</button>
      <button type="button" class=${small ? 'small' : undefined} onClick=${() => {
        ui.reviewConfirm = null
        draw()
      }}>Cancel${hint('Esc')}</button></span>`
  }
  return html`<button type="button" class=${cls} disabled=${busy}
    onClick=${() => pressReview(desk, pr, action, reload, text?.() ?? '')}>
    ${busy ? BUSY_LABELS[action] : REVIEW_LABELS[action]}${hint(hotkey)}</button>`
}

/** Five blocks that split a file's or PR's changed lines between added and removed, as GitHub's diffstat does. */
export function Diffstat({ additions, deletions }) {
  const total = additions + deletions
  const added = total === 0 ? 0 : Math.round(5 * additions / total)
  const removed = total === 0 ? 0 : Math.min(5 - added, Math.round(5 * deletions / total))
  return html`<span class="diffstat">${[...Array(5)].map((_, index) => html`<span key=${index}
    class=${index < added ? 'added' : index < added + removed ? 'removed' : ''} />`)}</span>`
}

export function Lines({ additions, deletions }) {
  return html`<span class="lines"><span class="success">+${additions}</span> <span class="error">−${deletions}</span>
    </span>`
}

/** Orders names as a file browser does: case aside, and numbers by their value. */
function byName(one, other) {
  return one.localeCompare(other, 'en', { sensitivity: 'base', numeric: true }) || (one < other ? -1 : 1)
}

/**
 * A review's files as the rows of a folder tree, keeping those whose path holds `filter`, case aside, and the folders
 * that hold them. Each level lists its folders first and then its files, each by name. A folder that holds one folder
 * and nothing else joins it in one row, named by both, such as `inbox/hooks`. A folder's row is
 * `{ kind: 'folder', name, path, rows }` and a file's `{ kind: 'file', name, file }`.
 */
export function fileTree(files, filter = '') {
  const needle = filter.trim().toLowerCase()
  const top = { folders: new Map(), files: [] }
  for (const file of files) {
    if (needle && !file.path.toLowerCase().includes(needle)) {
      continue
    }
    const parts = file.path.split('/')
    let folder = top
    for (const part of parts.slice(0, -1)) {
      if (!folder.folders.has(part)) {
        folder.folders.set(part, { folders: new Map(), files: [] })
      }
      folder = folder.folders.get(part)
    }
    folder.files.push({ name: parts.at(-1), file })
  }
  const rowsOf = (folder, above) => [
    ...[...folder.folders].sort(([one], [other]) => byName(one, other)).map(([name, inner]) => {
      let joined = name
      let held = inner
      while (held.files.length === 0 && held.folders.size === 1) {
        const [[next, deeper]] = held.folders
        joined = `${joined}/${next}`
        held = deeper
      }
      const path = `${above}${joined}`
      return { kind: 'folder', name: joined, path, rows: rowsOf(held, `${path}/`) }
    }),
    ...folder.files.sort((one, other) => byName(one.name, other.name))
      .map(({ name, file }) => ({ kind: 'file', name, file })),
  ]
  return rowsOf(top, '')
}

/**
 * The file whose diff is in view: the last of `boxes`, each `{ id, top }` in the order the page shows them, whose top
 * has scrolled to `line` or above it, or the first when none has. Null without boxes.
 */
export function fileAt(boxes, line) {
  return boxes.findLast(box => box.top <= line)?.id ?? boxes[0]?.id ?? null
}

/**
 * The height of what pins at the top of a panel on the Files Changed tab, which a file's box scrolls to just below:
 * the compact bar, the tabs, and the files bar, as `style`, the panel's, holds them.
 */
export function pinnedHeight(style) {
  return PINNED_HEIGHTS.reduce((sum, name) => sum + (Number.parseFloat(style.getPropertyValue(name)) || 0), 0)
}

/**
 * Where the tree measures the files against, in pixels down the window: `top`, just below the files bar, until the
 * last screen of scroll, `bottom - top` or all of it when shorter, and from there down to `bottom`, the scroller's
 * bottom edge, as `left`, the scroll still left below, runs out. The last files, which can't scroll up to the files
 * bar, each reach the line in turn, and the last of them reaches it at the bottom.
 */
export function readingLine(top, bottom, left, range) {
  const slide = Math.min(bottom - top, range)
  if (slide <= 0 || left >= slide) {
    return top
  }
  return top + (bottom - top) * (1 - Math.max(left, 0) / slide)
}

/** What scrolls the panel's card: the panel, or the page on a narrow window, where the panel grows to its card. */
export function scrollerOf(panel) {
  return getComputedStyle(panel).overflowY === 'visible' ? document.scrollingElement : panel
}

/**
 * Marks the file whose diff is in view in the tree: the one whose box reaches the reading line, just below the files
 * bar, which pins below the tabs, or lower over the last screen of scroll, as readingLine says. A file picked in the
 * tree stays marked until `scroller`, the panel or the page, scrolls from where the pick left it. A panel without the
 * tree marks nothing. The page runs it after every draw and every scroll.
 */
export function markFileInView(panel, scroller = scrollerOf(panel)) {
  const bar = panel.querySelector('.files-bar')
  if (!bar || !panel.querySelector('.file-tree')) {
    return
  }
  const boxes = [...panel.querySelectorAll('.file[data-file]')]
    .map(box => ({ id: box.dataset.file, top: box.getBoundingClientRect().top }))
  const jump = ui.fileJump
  let id
  if (jump && Math.abs(scroller.scrollTop - jump.scrollTop) < 1 && boxes.some(box => box.id === jump.id)) {
    id = jump.id
  } else {
    ui.fileJump = null
    const bottom = scroller === panel ? panel.getBoundingClientRect().top + panel.clientHeight : scroller.clientHeight
    const range = scroller.scrollHeight - scroller.clientHeight
    const line = readingLine(bar.getBoundingClientRect().bottom + 2 * FILE_GAP, bottom, range - scroller.scrollTop,
      range)
    id = fileAt(boxes, line)
  }
  if (id !== ui.fileInView) {
    ui.fileInView = id
    draw()
  }
}

/**
 * How far to scroll a list whose visible rows span `view`, `{ top, bottom }`, to show `row`: none while it shows
 * whole, and just enough to bring it to the nearer edge otherwise, its top first when it is taller than the view.
 */
export function scrollToShow(view, row) {
  if (row.top < view.top || row.bottom - row.top > view.bottom - view.top) {
    return row.top - view.top
  }
  return row.bottom > view.bottom ? row.bottom - view.bottom : 0
}

// The tree row the tree last scrolled to show, by its `data-mark`, so the tree scrolls only when the mark moves.
let followed = null

/**
 * Scrolls the tree just enough to show its marked row each time the mark moves to another row, and leaves the tree's
 * scroll alone otherwise. The tree shows its rows inside its own box and the panel's visible part, from `top`, the
 * pinned files bar's bottom edge, which a tree on short diffs scrolls under, to `bottom`, the panel's visible bottom
 * edge, and below the filter box while that is in view. The tree scrolls itself, never the panel or the page, so on a
 * narrow panel, where the tree sits above the diffs, the page stays where it is. A hidden tree waits until it shows.
 */
export function followMark(panel, top = panel.querySelector('.files-bar')?.getBoundingClientRect().bottom ?? 0,
  bottom = Math.min(panel.getBoundingClientRect().top + panel.clientHeight, innerHeight)) {
  const tree = panel.querySelector('.file-tree')
  const row = tree?.querySelector('.tree-row.current')
  if (!tree || !row || tree.clientHeight === 0) {
    if (tree && !row) {
      followed = null
    }
    return
  }
  if (row.dataset.mark === followed) {
    return
  }
  followed = row.dataset.mark
  const start = tree.getBoundingClientRect().top + tree.clientTop
  const end = start + tree.clientHeight
  // The rows scroll under the filter box's place at the tree's top.
  const below = Math.max(tree.querySelector('.tree-filter')?.getBoundingClientRect().bottom ?? start, start)
  const shown = row.getBoundingClientRect()
  const visible = { top: Math.max(below, top), bottom: Math.min(end, bottom) }
  // A tree scrolled out of the panel, as above a narrow panel's diffs, keeps to its own box.
  const view = visible.bottom - visible.top >= shown.bottom - shown.top ? visible : { top: below, bottom: end }
  tree.scrollTop += scrollToShow(view, shown)
}

/** Whether a file's diff shows: as the person last left it, or open unless it is large. */
function fileOpen(id, file) {
  return ui.files.get(id) ?? (file.lines !== undefined && file.lines.length <= OPEN_DIFF_ROWS)
}

/** Opens or folds the diffs of files `ids`, which moves the diffs, so a file picked in the tree no longer holds the mark. */
function setFiles(ids, open) {
  for (const id of ids) {
    ui.files.set(id, open)
  }
  ui.fileJump = null
  draw()
}

/**
 * Opens a file's diff from the tree, scrolls its box to the top of the panel, just below the files bar, which pins
 * below the compact bar and the tabs, and marks it in the tree until the next scroll, even when the box can't scroll
 * up that far.
 */
function showFile(id, event) {
  const panel = event.currentTarget.closest('.detail')
  setFiles([id], true)
  const box = panel?.querySelector(`.file[data-file="${CSS.escape(id)}"]`)
  if (box) {
    box.style.scrollMarginTop = `${pinnedHeight(panel.style) + FILE_GAP}px`
    box.scrollIntoView({ block: 'start' })
    ui.fileJump = { id, scrollTop: scrollerOf(panel).scrollTop }
  }
  ui.fileInView = id
  draw()
}

/**
 * Shows or hides the file tree. A panel past the narrow breakpoint, which the files bar marks with `--narrow`, keeps
 * its own choice for this visit, and a wide one remembers its choice across reloads.
 */
function toggleTree(event) {
  if (getComputedStyle(event.currentTarget).getPropertyValue('--narrow').trim() === '1') {
    ui.fileTreeNarrow = !ui.fileTreeNarrow
  } else {
    ui.fileTree = !ui.fileTree
    remember('file-tree-shown', ui.fileTree)
  }
  draw()
}

/** The mark of a file's status in the tree. */
function StatusMark({ status }) {
  return html`<svg class=${`file-status ${status}`} viewBox="0 0 16 16" width="16" height="16" fill="none"
    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" role="img"
    aria-label=${STATUS_TITLES[status]}><title>${STATUS_TITLES[status]}</title>
    <rect x="2.5" y="2.5" width="11" height="11" rx="2.5" />${STATUS_GLYPHS[status]}</svg>`
}

/** One level of the file tree: its folders, each folding open and closed by its caret, then its files. */
export function TreeRows({ desk, pr, rows, depth }) {
  return html`<ul class="tree-rows">${rows.map(row => {
    const indent = { '--depth': depth }
    if (row.kind === 'folder') {
      const key = `${desk}:${pr.number}:${row.path}`
      const open = !ui.treeFolded.has(key)
      // A folded folder stands in for the file in view inside it.
      const current = !open && ui.fileInView?.startsWith(`${key}/`)
      return html`<li key=${`folder:${row.path}`}>
        <button type="button" class=${current ? 'tree-row folder current' : 'tree-row folder'} style=${indent}
          title=${row.path} aria-expanded=${String(open)} aria-current=${current ? 'true' : undefined}
          data-mark=${current ? `${key}/` : undefined} onClick=${() => {
          if (open) {
            ui.treeFolded.add(key)
          } else {
            ui.treeFolded.delete(key)
          }
          draw()
        }}><span class="caret">${open ? '▾' : '▸'}</span><span class="tree-name">${row.name}</span></button>
        ${open ? html`<${TreeRows} desk=${desk} pr=${pr} rows=${row.rows} depth=${depth + 1} />` : null}
      </li>`
    }
    const { file } = row
    const id = `${desk}:${pr.number}:${file.path}`
    const status = file.status ?? 'modified'
    const current = ui.fileInView === id
    return html`<li key=${`file:${file.path}`}>
      <button type="button" class=${current ? 'tree-row current' : 'tree-row'} style=${indent}
        aria-current=${current ? 'true' : undefined} data-mark=${current ? id : undefined}
        title=${file.from ? `${file.path}\nRenamed from ${file.from}` : file.path}
        onClick=${event => showFile(id, event)}><${StatusMark} status=${status} />
        <span class="tree-name">${row.name}</span></button>
    </li>`
  })}</ul>`
}

/** The review's files in a folder tree, under a box that filters them by path, which leaves their diffs alone. */
function FileTree({ desk, pr }) {
  const key = `${desk}:${pr.number}`
  const filter = ui.treeFilter.get(key) ?? ''
  const rows = fileTree(pr.files, filter)
  return html`<div class="file-tree" role="navigation" aria-label="Changed files">
    <input type="search" class="tree-filter" placeholder="Filter files…" aria-label="Filter files" value=${filter}
      onInput=${event => {
        ui.treeFilter.set(key, event.target.value)
        draw()
      }} />
    ${rows.length > 0 ? html`<${TreeRows} desk=${desk} pr=${pr} rows=${rows} depth=${0} />`
      : html`<p class="muted tree-empty">No file's path holds “${filter.trim()}”.</p>`}
  </div>`
}

/** Opens the comment box on a line, keeping any text it held, and puts the cursor in it. */
function openLine(key) {
  ui.lineOpen.add(key)
  ui.lineFocus = key
  draw()
}

function closeLine(key) {
  ui.lineOpen.delete(key)
  draw()
}

/** Saves a line's comment box as a line comment on the head the diff shows, and adds it under its line. */
async function addLineComment(desk, pr, path, line, reload) {
  const key = lineKey(desk, pr.number, path, line)
  const text = ui.drafts.get(key) ?? ''
  if (!text.trim() || ui.posting.has(key)) {
    return
  }
  ui.posting.add(key)
  draw()
  const sent = await post('/api/line-comment', { desk, number: pr.number, path, line, head: pr.tip, text })
  ui.posting.delete(key)
  if (sent) {
    ui.drafts.delete(key)
    ui.lineOpen.delete(key)
    const comment = { ...sent.comment, html: clean(sent.comment.html) }
    reload(data => ({ ...data, lineComments: [...data.lineComments, comment] }))
  }
  draw()
}

/**
 * One line comment on review `pr`: its writer, when, whether its writer edited it, whether it waits for Send to agent,
 * its text, and its 👀 once the agent took it. An unsent one you wrote has Edit, which swaps its text for a box.
 */
function LineComment({ desk, pr, comment, reload }) {
  const { action, editor } = commentEdit(desk, pr.number, comment, reload)
  return html`<div class="line-comment">
    <${Avatar} person=${comment.author} size=${24} />
    <div class="line-comment-main">
      <div class="line-comment-head"><strong>${comment.author.login}</strong>
        <span class="muted"> ${age(comment.at)} ago</span><${EditedMark} at=${comment.edited} />
        ${comment.sent ? null : html` <span class="badge unsent" title="Send to Agent sends it">Not sent</span>`}
        ${editor ? null : action}
      </div>
      ${editor ?? html`<${Markup} markup=${comment.html} />`}
      <${Reaction} run=${comment.run} />
    </div>
  </div>`
}

/** The box that writes a comment on one line of the diff; Ctrl+Enter adds it and Escape puts it away. */
function LineForm({ desk, pr, path, line, reload }) {
  const key = lineKey(desk, pr.number, path, line)
  const posting = ui.posting.has(key)
  return html`<div class="line-form">
    <textarea placeholder=${`Comment on line ${line}`} aria-label=${`Comment on ${path} line ${line}`}
      value=${ui.drafts.get(key) ?? ''} disabled=${posting} ref=${node => {
        if (node && ui.lineFocus === key) {
          ui.lineFocus = null
          node.focus()
        }
      }}
      onInput=${event => ui.drafts.set(key, event.target.value)}
      onKeyDown=${event => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          void addLineComment(desk, pr, path, line, reload)
        } else if (event.key === 'Escape') {
          closeLine(key)
        }
      }} />
    <div class="comment-foot">
      <span class="muted">Send to Agent sends it with the rest.</span>
      <button type="button" class="small" disabled=${posting} onClick=${() => closeLine(key)}>Cancel</button>
      <button type="button" class="small primary" disabled=${posting}
        onClick=${() => void addLineComment(desk, pr, path, line, reload)}>${posting ? 'Saving…' : 'Add Comment'}
        <kbd>Ctrl+Enter</kbd></button>
    </div>
  </div>`
}

/** A line's comments and its comment box, under its row of the diff. */
function LineThread({ desk, pr, path, line, comments, reload }) {
  const key = lineKey(desk, pr.number, path, line)
  return html`<div class="line-thread">
    ${comments.map(comment => html`<${LineComment} key=${comment.id || comment.at} desk=${desk} pr=${pr}
      comment=${comment} reload=${reload} />`)}
    ${ui.lineOpen.has(key) ? html`<${LineForm} desk=${desk} pr=${pr} path=${path} line=${line} reload=${reload} />`
      : html`<button type="button" class="small reply" onClick=${() => openLine(key)}>Reply…</button>`}
  </div>`
}

/**
 * A file's diff, as GitHub draws it: each line with its old and new numbers, added lines green and removed red. A
 * press on a line of the new file, by its number or the + beside it, opens a comment box under it, where its comments
 * show too.
 */
function Diff({ desk, pr, file, onLines, reload, commentable }) {
  return html`<div class="diff-scroll"><table class="diff"><tbody>${file.lines.flatMap((line, index) => {
    if (line.kind === 'hunk' || line.kind === 'note') {
      return [html`<tr class=${line.kind === 'hunk' ? 'hunk' : 'note-row'} key=${index}><td class="ln" colspan="2"></td>
        <td class=${line.kind === 'hunk' ? 'code' : 'code muted'}>${line.text}</td></tr>`]
    }
    const onNew = commentable && line.kind !== 'del'
    const key = onNew ? lineKey(desk, pr.number, file.path, line.new) : ''
    const comments = onNew ? onLines.get(`${file.path}:${line.new}`) ?? [] : []
    const open = onNew && ui.lineOpen.has(key)
    const row = html`<tr class=${line.kind} key=${index}><td class="ln">${line.old ?? ''}</td>
      ${onNew ? html`<td class="ln commentable" title=${`Comment on line ${line.new}`}
        onClick=${() => openLine(key)}>${line.new}</td>` : html`<td class="ln">${line.new ?? ''}</td>`}
      <td class="code">${onNew ? html`<button type="button" class="add-line-comment" tabindex="-1"
        aria-label=${`Comment on line ${line.new}`} onClick=${() => openLine(key)}>+</button>` : null}<span
        class="sign">${SIGNS[line.kind]}</span>${line.text}</td></tr>`
    return comments.length > 0 || open ? [row, html`<tr class="thread-row" key=${`${index}-thread`}><td colspan="3">
      <${LineThread} desk=${desk} pr=${pr} path=${file.path} line=${line.new} comments=${comments}
        reload=${reload} /></td></tr>`] : [row]
  })}</tbody></table></div>`
}

/**
 * Line comments the diff can't place, above it: outdated ones, with the lines of the file they were made on, and
 * ones on lines the diff doesn't show.
 */
function AsideComments({ desk, pr, comments, reload }) {
  const outdated = comments.filter(comment => comment.outdated)
  const hidden = comments.filter(comment => !comment.outdated)
  const section = (title, list, note) => (list.length === 0 ? null : html`<div class="box aside-comments">
    <div class="box-head static"><strong>${title}</strong> <span class="counter">${list.length}</span>
      <span class="muted">${note}</span></div>
    ${list.map(comment => html`<div class="aside-comment" key=${comment.id}>
      <div class="aside-where"><code>${comment.path}</code> <span class="muted">line ${comment.line}</span>
        ${comment.outdated ? html` <span class="badge outdated">Outdated</span>` : null}</div>
      ${comment.code?.length ? html`<div class="diff-scroll"><table class="diff"><tbody>${comment.code.map(row =>
        html`<tr key=${row.line} class=${row.line === comment.line ? 'same commented' : 'same'}>
          <td class="ln">${row.line}</td><td class="code">${row.text}</td></tr>`)}</tbody></table></div>`
        : comment.outdated ? html`<p class="muted aside-gone">The code it was made on is no longer in git.</p>` : null}
      <${LineComment} desk=${desk} pr=${pr} comment=${comment} reload=${reload} />
    </div>`)}
  </div>`)
  return html`${section('Outdated', outdated, 'A commit since changed the line each was made on.')}
    ${section('Outside the diff', hidden, 'Each is on a line the diff leaves out.')}`
}

/**
 * A review's files, each in a box whose heading folds its diff away, with buttons that fold or open them all, and its
 * line comments under their lines; or why it has no diff to show. A folder tree of the files sits left of the diffs,
 * or above them in a narrow panel, and a button in the files bar shows or hides it. While the review is open, a bar
 * sends the line comments that wait to the agent.
 */
export function Files({ pr, desk, reload }) {
  const { onLines, above } = placeComments(pr.files, pr.lineComments ?? [])
  const waiting = unsentCount(pr)
  const sendable = reviewButtons(pr.state).includes('send')
  const aside = html`<${AsideComments} desk=${desk} pr=${pr} comments=${above} reload=${reload} />`
  if (pr.noDiff) {
    return html`<div class="box"><div class="empty">${pr.noDiff}</div></div>${aside}`
  }
  const ids = pr.files.map(file => `${desk}:${pr.number}:${file.path}`)
  const treeClass = ['files-body', ui.fileTree ? 'tree-wide' : '', ui.fileTreeNarrow ? 'tree-narrow' : '']
    .filter(Boolean).join(' ')
  return html`${pr.diffNote ? html`<div class="box"><div class="empty">${pr.diffNote}</div></div>` : null}
    <div class="files-bar">
      <button type="button" class="small tree-toggle" onClick=${toggleTree}>
        <span class="when-wide">${ui.fileTree ? 'Hide' : 'Show'} File Tree</span>
        <span class="when-narrow">${ui.fileTreeNarrow ? 'Hide' : 'Show'} File Tree</span></button>
      <span class="muted">${counted(pr.files.length, 'file')}</span>
      ${sendable && waiting > 0 ? html`<span class="attention-text">${counted(waiting, 'line comment')} not sent</span>
        <${ReviewButton} desk=${desk} pr=${pr} action="send" reload=${reload} small=${true} />` : null}
      <button type="button" class="small" onClick=${() => setFiles(ids, true)}>Expand All</button>
      <button type="button" class="small" onClick=${() => setFiles(ids, false)}>Collapse All</button>
    </div>
    <div class=${treeClass}>
      <${FileTree} desk=${desk} pr=${pr} />
      <div class="files-diffs">
        ${aside}
        ${pr.files.map((file, index) => {
          const id = ids[index]
          const open = fileOpen(id, file)
          const threads = (pr.lineComments ?? []).filter(comment => comment.path === file.path).length
          return html`<div class="box file" key=${file.path} data-file=${id}>
            <button type="button" class="box-head file-head" aria-expanded=${String(open)}
              onClick=${() => setFiles([id], !open)}>
              <span class="caret">${open ? '▾' : '▸'}</span>
              <code class="path">${file.from ? `${file.from} → ` : ''}${file.path}</code>
              ${threads > 0 ? html`<span class="muted" title=${counted(threads, 'line comment')}>💬 ${threads}</span>`
                : null}
              <${Lines} additions=${file.additions} deletions=${file.deletions} />
              <${Diffstat} additions=${file.additions} deletions=${file.deletions} />
            </button>
            ${!open ? null
              : file.omitted ? html`<div class="empty">This file's lines are left out of a large diff.</div>`
              : !file.lines ? html`<div class="empty">This file is binary, so it has no diff to show.</div>`
              : file.lines.length === 0 ? html`<div class="empty">No lines changed.</div>`
              : html`<${Diff} desk=${desk} pr=${pr} file=${file} onLines=${onLines} reload=${reload}
                commentable=${pr.tip !== null && pr.tip !== undefined} />`}
          </div>`
        })}
      </div>
    </div>`
}
