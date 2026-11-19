// The page's state, shared by app.js and the modules beside it: what the page shows and what the person picked, the
// tables of stages and actions, and how a press changes them. draw() draws the page again with the function app.js
// hands to drawWith(), so no module here imports app.js and the modules stay free of import cycles.

// The list's groups, in order: each card stage, its heading, and its heading color, a custom property app.css defines.
export const STAGES = [
  ['merging', 'Merging', 'merging'], ['needs', 'Needs You', 'needs'], ['working', 'Working', 'runners'], ['queued', 'Queued', 'queued'],
  ['open', 'Open', 'open'], ['blocked', 'Blocked', 'blocked'], ['design', 'Needs Design', 'design'],
  ['backlog', 'Backlog', 'backlog'], ['idea', 'Ideas', 'ideas'],
]
export const STAGE_NAMES = Object.fromEntries(STAGES.map(([stage, title]) => [stage, title]))
// The groups that sort by issue number, highest first until the person flips one; the others keep their own order.
export const SORTABLE = new Set(['open', 'blocked', 'design', 'backlog', 'idea'])
export const LABELS = { merge: 'Merge', ready: 'Agent-Ready', start: 'Start', resume: 'Take Over',
  restart: 'Continue', retry: 'Retry', dismiss: 'Dismiss', fix: 'Fix', close: 'Close', designed: 'Designed',
  'to-open': 'Move to Open', 'to-backlog': 'Move to Backlog', unblock: 'Unblock', undraft: 'Ready',
  cancel: 'Cancel Run', up: 'Move Up', down: 'Move Down', top: 'Move to Top', unpick: 'Unpick', pause: 'Pause',
  unpause: 'Resume' }
// The key that presses each action on the selected card. The start order's moves take brackets, `{` being Shift+`[`,
// Unpick, which takes back a Start, takes `z`, as undo does, Pause takes `w` for wait, and Resume after it takes `q`
// for queue.
export const KEYS = { merge: 'm', fix: 'f', ready: 'a', start: 's', resume: 'r', restart: 'e', retry: 't', dismiss: 'd',
  close: 'c', designed: 'g', 'to-open': 'n', 'to-backlog': 'b', unblock: 'u', undraft: 'p', cancel: 'x', up: '[',
  down: ']', top: '{', unpick: 'z', pause: 'w', unpause: 'q' }
// The actions that move an issue or PR forward, drawn in the accent color. Take Over, which copies a command, leaves
// Continue the stopped card's main button.
export const FORWARD = new Set(['merge', 'ready', 'start', 'restart', 'retry', 'fix', 'undraft', 'unpause'])

/** An action's button label on a card: Retry reads Continue on a run whose session it carries on. */
export function actionLabel(card, action) {
  return action === 'retry' && card.continues ? LABELS.restart : LABELS[action]
}
// The actions that ask to confirm before they act.
export const CONFIRMS = new Set(['merge', 'fix', 'dismiss', 'ready', 'retry', 'restart', 'close', 'unblock',
  'undraft', 'to-open', 'pause'])
// How many project hues app.css defines, as --project-0 and on.
export const PROJECT_HUES = 6
// How long a preview stays fresh before showing its card again reads it anew, as the server keeps it.
export const PREVIEW_MS = 60_000
const TOAST_MS = 8_000

function remembered(key, fallback) {
  try {
    const value = localStorage.getItem(key)
    return value === null ? fallback : JSON.parse(value)
  } catch {
    return fallback
  }
}

export function remember(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // A page without storage forgets the tab, the collapsed and flipped groups, and the selected card on reload.
  }
}

/**
 * An empty New Issue draft: whether the form shows in the panel, the project it files in, its title, its picked
 * labels, the label being typed, whether its create is on its way, whether its title takes the cursor at the next
 * draw, whether Agent-Ready is checked, the Parent and Blocked By fields' text, and the template the body last took,
 * or null. Its body goes in drafts under the form's own key.
 */
export function emptyDraft() {
  return { open: false, desk: null, title: '', labels: [], typed: '', creating: false, focus: false, agentReady: false,
    parent: '', blockedBy: '', template: null }
}

// The groups that start collapsed, since they can run to dozens of cards each.
const FOLDED = ['design', 'backlog', 'idea']
// The groups a page that remembered no groups knew, from before Backlog.
const EARLIER_STAGES = ['merging', 'needs', 'working', 'queued', 'open', 'blocked', 'design', 'idea']

/**
 * The groups the page starts collapsed: those the person left collapsed, as `stored` remembers them, or FOLDED when
 * nothing does, and every group in FOLDED new since the page last ran, which `known` names.
 */
export function startCollapsed(stored, known) {
  return new Set([...(stored ?? FOLDED), ...(stored ? FOLDED.filter(stage => !known.includes(stage)) : [])])
}

/** What the page shows: the server's last state, and what the person picked. */
export const ui = {
  server: null,
  // The store event id the server's last state was read after, which moves with every change to the store.
  storeEvent: 0,
  connected: false,
  tab: remembered('inbox-tab', 'all'),
  collapsed: startCollapsed(remembered('inbox-collapsed', null), remembered('inbox-groups', EARLIER_STAGES)),
  // The sortable groups the person flipped to lowest issue number first.
  ascending: new Set(remembered('inbox-ascending', [])),
  selected: remembered('inbox-selected', null),
  // An item a link showed that the list has no card for, as a card of its own with no actions, or null.
  visiting: null,
  // Whether the person closed the panel, which leaves no card selected.
  closed: remembered('inbox-closed', false),
  // The selected card's action that waits for its confirm, as { id, action }.
  confirming: null,
  // The issue whose Delete Issue dialog is open, as { desk, number, typed, status, error }, which app-delete.js reads.
  deleting: null,
  help: false,
  // The search box's text, and whether it changed since the last draw.
  query: '',
  searched: false,
  // The selected card's tab: issue, review, deps, files, checks, or log, or null for the card's first tab, as firstTab
  // says; and whether its agent log follows its newest line.
  detailTab: null,
  // Whether the selected card's header has scrolled out of view, which shows its compact bar; whether its tabs have
  // reached the bar and pinned under it; and whether a tab was just picked, which the next draw shows from its top
  // when the tabs are pinned.
  stuck: false,
  pinned: false,
  tabTop: false,
  logStick: true,
  // Each issue's agent log, by `<desk>:<number>`: { status, at, data }.
  logs: new Map(),
  // Whether each PR file's diff shows, by `<desk>:<pr>:<path>`, once the person folded or opened it.
  files: new Map(),
  // The Files Changed tab's folder tree: whether it shows in a wide panel, and in a narrow one, where it starts
  // hidden; its folded folders, by `<desk>:<pr>:<folder path>`; its filter's text, by `<desk>:<pr>`; the file whose
  // diff is in view, by `<desk>:<pr>:<path>`; and the file last picked in the tree, as { id, scrollTop }, which keeps
  // the mark while the panel or the page stays scrolled to `scrollTop`.
  fileTree: remembered('file-tree-shown', true),
  fileTreeNarrow: false,
  treeFolded: new Set(),
  treeFilter: new Map(),
  fileInView: null,
  fileJump: null,
  // How many times to run each chore, by `<desk>:<chore>`, as its box last read.
  choreTimes: new Map(),
  // The chore whose schedule the person is changing, as { id, desk, name, every, paused, confirm, saving, error }: the
  // days or pause it asks for, whether it waits for its confirm or the server, and why the server refused it.
  schedule: null,
  // Each comment box's unsent text, whether it shows its preview, and its preview's HTML, by `<desk>:<number>`; and
  // the boxes whose comment is on its way to GitHub.
  drafts: new Map(),
  previewing: new Set(),
  rendered: new Map(),
  posting: new Set(),
  // Each title and description that Edit changes, by `<desk>:<number>:edit-title` or `edit-body`, as { status, stored,
  // version, focus }: whether its source is still being read, ready, or saving, the text the store holds, the
  // description's version, and whether its box takes the cursor at the next draw. Its text goes in drafts by the same
  // key, and a description's preview in previewing and rendered.
  edits: new Map(),
  // The answered Decisions Needed sections whose form the person opened again with Answer Again, by the form's draft
  // key, `<desk>:<number>#<anchor>`. A form's draft goes in drafts by the same key.
  answering: new Set(),
  // The reason each issue's comment box closes it with, by `<desk>:<number>`, when the person picked not planned or
  // duplicate, and the text of the field naming the original for a duplicate.
  closeReasons: new Map(),
  duplicateOf: new Map(),
  // The diff lines whose comment box is open, by `<desk>:<review>:<path>:<line>`, and the one whose box takes the
  // cursor at the next draw. A line's unsent text and whether it is saving go in drafts and posting by the same key.
  lineOpen: new Set(),
  lineFocus: null,
  // A review's buttons whose request runs, by `<desk>:<review>:<action>`, and the one that waits for its confirm.
  reviewBusy: new Set(),
  reviewConfirm: null,
  // The earlier review an issue's page shows in place of its open one, by the card's id.
  reviewPicks: new Map(),
  // The New Issue form's draft, as emptyDraft makes it, which stays until a create or a reload; and the issue the
  // last create filed, as { desk, number }, until the page selects its card.
  newIssue: emptyDraft(),
  created: null,
  // The Resources view, which app-resources.js shows in the panel in place of the card: whether it shows, its last
  // reading of /api/resources, and why the last read failed, or null.
  resources: { open: false, data: null, error: null },
  toasts: [],
  // Whether the next draw scrolls the selected card into view, after a key moved the selection.
  reveal: false,
  // The Dependencies tab's open rows, by `<desk>:<numbers from the top row down>`, and whether it hides closed issues.
  depsOpen: new Set(),
  depsHideClosed: remembered('deps-hide-closed', false),
  // The sidebar at the right: null for none, 'runners', 'history', or 'chat'. An older page's choice to show History
  // carries over.
  side: remembered('side-shown', remembered('history-shown', false) === true ? 'history' : null),
  // The History column: its span, its sort, its search, its page, and its last read of the server, by the projects and
  // span it asked for.
  history: {
    query: '',
    range: remembered('history-range', '24h'),
    from: remembered('history-from', ''),
    to: remembered('history-to', ''),
    sort: remembered('history-sort', { key: 'at', dir: 'desc' }),
    page: 0,
    read: null,
  },
  // The Chat column: the conversation the server last sent, its session, and whether a reply is coming; the message
  // being typed; whether a message is on its way to the server; whether Clear Chat waits for its confirm; whether the
  // log stays at its newest line; and whether the box takes the cursor at the next draw.
  chat: { items: [], session: null, running: false, draft: '', sending: false, confirmClear: false, stick: true,
    focus: false },
  // The header's weekly usage limit: whether its popover shows, the percentage typed and not yet saved, or null, and
  // why the popover refused it, or null.
  usage: { open: false, draft: null, error: null },
}
// The page remembers the groups it knows, so a group added later starts collapsed once, as FOLDED says.
remember('inbox-collapsed', [...ui.collapsed])
remember('inbox-groups', STAGES.map(([stage]) => stage))

// What draw() runs: app.js's own draw, once it hands it over.
let redraw = () => undefined

/** Hands draw() the function that draws the page again, which app.js does as it starts. */
export function drawWith(drawPage) {
  redraw = drawPage
}

/** Draws the page again after any change to `ui`. */
export function draw() {
  redraw()
}

/**
 * Posts to the server and returns its answer, or null after a toast says why it failed. Given `project`, a project's
 * name, the toast gives the server's reason after it.
 */
export async function post(path, data, project) {
  try {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data) })
    const sent = await res.json().catch(() => ({}))
    if (!res.ok) {
      const reason = sent.error ?? `the server refused (${res.status})`
      toast(project ? `${project}: ${reason}.` : sent.error ? `The server refused: ${reason}.`
        : `The server refused (${res.status}).`)
      return null
    }
    return sent
  } catch {
    toast('The agent-desk server is not answering.')
    return null
  }
}

export function toast(text) {
  const shown = { id: Math.random(), text }
  ui.toasts = [...ui.toasts, shown]
  draw()
  setTimeout(() => {
    ui.toasts = ui.toasts.filter(one => one !== shown)
    draw()
  }, TOAST_MS)
}

/** The time since an ISO stamp, as the pane writes it: minutes under an hour, hours under two days, then days. */
export function age(since) {
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(since)) / 60_000))
  return minutes < 60 ? `${minutes}m` : minutes < 2_880 ? `${Math.round(minutes / 60)}h`
    : `${Math.round(minutes / 1_440)}d`
}

export function ready(desk) {
  return desk.board !== null && !desk.board.error
}

/**
 * A group's cards by issue number, highest first or, when ascending, lowest first. Each project's cards stay together
 * in the order the projects first appear, as All lists them, and sort within it.
 */
export function byNumber(cards, ascending) {
  const desks = [...new Set(cards.map(card => card.desk))]
  const sign = ascending ? 1 : -1
  return [...cards].sort((a, b) => desks.indexOf(a.desk) - desks.indexOf(b.desk)
    || sign * ((a.number ?? 0) - (b.number ?? 0)))
}

/**
 * The GitHub group's rows for the projects shown, each as `{ desk, row }`: every project's main and agent branch rows,
 * then the backup's row once, with no project, since the backup covers every project and each board repeats it.
 */
export function githubRows(desks) {
  const rows = desks.flatMap(desk => (desk.board?.github ?? []).filter(row => row.key !== 'github-backup')
    .map(row => ({ desk, row })))
  const backup = desks.map(desk => desk.board?.github?.find(row => row.key === 'github-backup')).find(Boolean)
  return backup ? [...rows, { desk: null, row: backup }] : rows
}

export function deskOf(id) {
  return ui.server?.desks.find(desk => desk.id === id)
}

export function shownDesks() {
  const desks = ui.server?.desks ?? []
  return ui.tab === 'all' ? desks : desks.filter(desk => desk.id === ui.tab)
}

/** Shows a project's tab, or All, and keeps it for the next visit. */
export function pickTab(id) {
  ui.tab = id
  remember('inbox-tab', id)
  draw()
}

/**
 * Shows sidebar `side`, `'runners'`, `'history'`, or `'chat'`, in place of the others, or hides it, and remembers the
 * choice. The Chat's box takes the cursor as it opens.
 */
export function toggleSide(side) {
  ui.side = ui.side === side ? null : side
  ui.chat.focus = ui.side === 'chat'
  remember('side-shown', ui.side)
}

/** Whether a board row works in a slot of `kind`: a benchmark slot's rows are the agents' on issues labelled so. */
function worksIn(kind, row) {
  return kind === 'benchmark' ? row.runner === 'agent' && (row.labels ?? []).includes('benchmark') : row.runner === kind
}

/**
 * A project's slots as rows, one per slot, as `{ kind, busy, row, tone }` with its kind's tone: each kind's busy
 * slots first, each with the board's runner or check row of its kind in the order the board lists them, then its free
 * ones up to its total. A kind that counts more busy runs than rows match shows the rest as busy with no row, and one
 * that counts more busy runs than slots, after the config shrinks, shows a row for each run and none free.
 */
export function slotRows(slots, rows) {
  return slots.flatMap(({ kind, busy, total, tone }) => {
    const working = rows.filter(row => worksIn(kind, row)).slice(0, busy)
    return Array.from({ length: Math.max(total, busy) }, (_, at) => ({ kind, busy: at < busy, row: working[at], tone }))
  })
}

/**
 * The Runners sidebar's projects for the tab shown, in the page's project order, each whose board read: its slot
 * counts, each with its tone, `full` when every slot of the kind works, `busy` when some do, and `free` when none do;
 * a row per slot; and its local sessions, which take no slot.
 */
export function runnerProjects() {
  return shownDesks().filter(desk => desk.board && desk.board.error === null).map(desk => {
    const rows = [...desk.board.runners, ...(desk.board.checks ?? [])]
    const tone = one => (one.busy >= one.total ? 'full' : one.busy > 0 ? 'busy' : 'free')
    const slots = (desk.board.slots ?? []).map(one => ({ ...one, tone: tone(one) }))
    return { desk: desk.id, slots, rows: slotRows(slots, rows), locals: rows.filter(row => row.runner === 'local') }
  })
}

/** Closes the panel, which leaves no card selected and the panel blank, until a card is picked again. */
export function closeDetail() {
  ui.closed = true
  ui.confirming = null
  remember('inbox-closed', true)
  draw()
}

/** Shows card `id` in the panel, in place of the New Issue form or the Resources view. */
export function select(id) {
  ui.selected = id
  ui.newIssue.open = false
  ui.resources.open = false
  ui.closed = false
  remember('inbox-closed', false)
  ui.confirming = null
  ui.detailTab = null
  remember('inbox-selected', id)
  draw()
}

/**
 * The tab a card's page opens on: Review for a card in Needs You or Merging, whose review waits on the person, when
 * the page shows a review, as `reviewed` says, and Issue for any other. A review without an issue has no Issue tab, so
 * its page shows its Review tab, the first it has.
 */
export function firstTab(card, reviewed) {
  return reviewed && (card.stage === 'needs' || card.stage === 'merging') ? 'review' : 'issue'
}

// A link to an item on the page, as review.ts's itemLink writes it: `#<project>:<number>`.
const ITEM_LINK = /^#([\w.-]+):(\d+)$/

/** The project and number a link to an item on the page names, when the page shows that project; or null. */
export function pageItemOf(href) {
  const url = new URL(href, location.href)
  const found = url.origin === location.origin && url.pathname === '/' ? ITEM_LINK.exec(url.hash) : null
  return found && deskOf(found[1]) ? { desk: found[1], number: Number(found[2]) } : null
}

/** The page's link to item `number` in a project, which a plain click shows in the detail view. */
export function itemHref(desk, number) {
  return `#${desk}:${number}`
}

/**
 * The item a plain left click on a link to an item on the page names; or null for a click with a modifier, which
 * opens the page in a new tab at that item, or for any other link.
 */
export function clickedItem(event, href) {
  return !event.defaultPrevented && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey
    && !event.altKey ? pageItemOf(href) : null
}

/**
 * The card the list draws for item `number` in a project, by its issue's number or its PR's; or undefined. A merged
 * card, which the History column lists in place of the list, counts as none.
 */
export function listedCard(desk, number) {
  return deskOf(desk)?.cards.find(card => STAGE_NAMES[card.stage] !== undefined
    && (card.number === number || card.pr === number))
}

/**
 * Shows an issue or a review in the detail view: its card when the list has one, or else a card of its own, titled
 * from the item's hovercard, which the list doesn't show.
 */
export async function visit(desk, number) {
  const card = listedCard(desk, number)
  if (card) {
    select(card.id)
    return
  }
  let found
  try {
    const res = await fetch(`/api/hovercard?desk=${encodeURIComponent(desk)}&number=${number}`)
    if (!res.ok) {
      throw new Error(String(res.status))
    }
    found = await res.json()
  } catch {
    toast(`Could not read #${number} in ${desk}.`)
    return
  }
  ui.visiting = { id: `${desk}:visit-${number}`, desk, number, ...(found.kind === 'pr' ? { pr: number } : {}),
    title: found.title, key: '', actions: [], labels: found.labels.map(label => label.name),
    note: 'Not on the board' }
  select(ui.visiting.id)
}

/** Shows the open card's tab `id`, from its top when the tabs are pinned under the bar that stays at the top. */
export function pickDetailTab(id) {
  ui.detailTab = id
  ui.logStick = true
  ui.tabTop = true
  draw()
}

/** Shows a failed or timed-out run's card in the panel, at its Agent Log tab when the run worked an issue. */
export function showRun(card) {
  select(card.id)
  if (card.number !== undefined && card.number !== card.pr) {
    pickDetailTab('log')
  }
}

export async function act(card, action) {
  ui.confirming = null
  draw()
  if (action === 'resume') {
    try {
      await navigator.clipboard.writeText(card.resume)
      toast(`Copied: ${card.resume}`)
    } catch {
      toast(`Run in a terminal: ${card.resume}`)
    }
    return
  }
  await post('/api/act', { desk: card.desk, key: card.key, action })
}

/**
 * Presses an action: one that asks to confirm waits for it, in place of any review button's confirm, and any other acts
 * at once.
 */
export function press(card, action) {
  if (CONFIRMS.has(action)) {
    ui.confirming = { id: card.id, action }
    ui.reviewConfirm = null
    draw()
  } else {
    void act(card, action)
  }
}

/**
 * The key a keydown means to the hotkeys, with a single letter folded to lower case so Shift and Caps Lock change
 * nothing, or null for a Ctrl, Meta, or Alt chord, a key typed in an input, textarea, or select, and Enter on a button,
 * link, or summary, which presses it instead.
 */
export function hotkeyOf(event) {
  if (event.ctrlKey || event.metaKey || event.altKey || event.target?.closest?.('input, textarea, select')
    || (event.key === 'Enter' && event.target?.closest?.('button, a, summary'))) {
    return null
  }
  return /^\p{L}$/u.test(event.key) ? event.key.toLowerCase() : event.key
}
