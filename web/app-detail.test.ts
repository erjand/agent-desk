// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { barHeight, boxOf, CommentForm, logLines, onDetailScroll, preview, sendOf, showTabTop, shownReview, StatusNote,
  tabsOf, titleTarget } from './app-detail.js'
import { BodyEdit, canSave, commentEdit, commentEditable, commentField, EditButton, EditedMark, editing,
  editingLabels, editKey, LabelsButton, LabelsEdit, MarkdownBox, toggleLabels, writeLabels } from './app-edit.js'
import { Icon, Label, LabelPicker, STATE_LABELS } from './app-parts.js'
import { askDelete, cancelDelete, confirmsDelete, DeleteButton, DeleteDialog, DeleteForm, deleteIssue }
  from './app-delete.js'
import { ReviewButton } from './app-review.js'
import { ui } from './app-state.js'

type Box = { top: number; bottom?: number }

/**
 * A panel with a compact bar `bar` pixels tall, whose header ends and tabs start at the given offsets from the panel's
 * top, which sits `at` pixels down the window. The tabs stand `tabsTall` pixels, and a Files Changed tab's files bar
 * `filesBar`, which a panel on another tab leaves out. The tabs pin at the bar's height when `sticky` and they would sit above
 * it. A panel on a narrow window, `page`, lets the page scroll instead, as its visible overflow says.
 */
function panel({ at = 80, bar = 56, header = 200, tabs = 260, tabsTall = 41, filesBar = 0, sticky = true,
  page = false }) {
  const props = new Map<string, string>()
  const tabsEl = {
    offsetHeight: tabsTall,
    style: { position: '' },
    getBoundingClientRect: (): Box => {
      const flow = at + tabs
      return { top: sticky && tabsEl.style.position !== 'static' ? Math.max(flow, (page ? 0 : at) + bar) : flow }
    },
  }
  const self = {
    scrollTop: 0,
    overflowY: page ? 'visible' : 'auto',
    style: { getPropertyValue: (name: string) => props.get(name) ?? '',
      setProperty: (name: string, value: string) => props.set(name, value) },
    getBoundingClientRect: (): Box => ({ top: at }),
    querySelector: (selector: string) => ({
      '.gh-sticky': { offsetHeight: bar },
      '.gh-header': { getBoundingClientRect: (): Box => ({ top: at, bottom: at + header }) },
      '.gh-main > .tabnav': tabsEl,
      '.files-bar': filesBar > 0 ? { offsetHeight: filesBar } : null,
    })[selector] ?? null,
    props,
    tabsEl,
  }
  return self
}

const page = { scrollTop: 0 }
Object.assign(globalThis, {
  getComputedStyle: (element: { overflowY: string }) => ({ overflowY: element.overflowY }),
  document: { scrollingElement: page },
})

function scrolled(options: Parameters<typeof panel>[0]) {
  ui.stuck = false
  ui.pinned = false
  onDetailScroll(panel(options))
  return { stuck: ui.stuck, pinned: ui.pinned }
}

test('the panel takes the compact bar\'s height for its tabs to pin at, as its buttons wrap', () => {
  const one = panel({ bar: 56 })
  assert.equal(barHeight(one), 56)
  assert.equal(one.props.get('--bar-height'), '56px')
  const wrapped = panel({ bar: 97 })
  assert.equal(barHeight(wrapped), 97)
  assert.equal(wrapped.props.get('--bar-height'), '97px')
})

test('the panel takes the tabs\' height for the files bar to pin at, and the files bar\'s for the file tree', () => {
  const files = panel({ bar: 56, tabsTall: 41, filesBar: 44 })
  assert.equal(barHeight(files), 56)
  assert.deepEqual(Object.fromEntries(files.props),
    { '--bar-height': '56px', '--tabs-height': '41px', '--files-bar-height': '44px' })
  // On a narrow window the tabs and the files bar wrap, and another tab has no files bar.
  const wrapped = panel({ bar: 97, tabsTall: 82, filesBar: 80 })
  barHeight(wrapped)
  assert.equal(wrapped.props.get('--tabs-height'), '82px')
  assert.equal(wrapped.props.get('--files-bar-height'), '80px')
  const conversation = panel({})
  barHeight(conversation)
  assert.equal(conversation.props.get('--files-bar-height'), '0px')
})

test('the bar shows once the header scrolls away, and the tabs pin once they reach it', () => {
  assert.deepEqual(scrolled({ header: 200, tabs: 260 }), { stuck: false, pinned: false })
  assert.deepEqual(scrolled({ header: 8, tabs: 100 }), { stuck: true, pinned: false })
  assert.deepEqual(scrolled({ header: -60, tabs: -10 }), { stuck: true, pinned: true })
})

test('the bar shows by the time the tabs reach it, with the header\'s last line still in view', () => {
  assert.deepEqual(scrolled({ header: 40, tabs: 56 }), { stuck: true, pinned: true })
  assert.deepEqual(scrolled({ header: 40, tabs: 57 }), { stuck: false, pinned: false })
})

test('on a narrow window the page scrolls, and the bar and tabs measure from the window\'s top', () => {
  assert.deepEqual(scrolled({ page: true, at: -400, header: 600, tabs: 650 }), { stuck: false, pinned: false })
  assert.deepEqual(scrolled({ page: true, at: -400, header: 430, tabs: 456 }), { stuck: true, pinned: true })
})

test('a tab picked with the tabs pinned shows from its top, and one picked before they pin leaves the panel', () => {
  const pinned = panel({ tabs: -900 })
  pinned.scrollTop = 1200
  showTabTop(pinned)
  assert.equal(pinned.scrollTop, 1200 - 956)
  assert.equal(pinned.tabsEl.style.position, '')
  const resting = panel({ tabs: 140 })
  resting.scrollTop = 60
  showTabTop(resting)
  assert.equal(resting.scrollTop, 60)
})

test('on a narrow window a tab picked while the tabs are pinned scrolls the page', () => {
  page.scrollTop = 3000
  const pinned = panel({ page: true, at: -2000, tabs: 1500 })
  showTabTop(pinned)
  assert.equal(page.scrollTop, 3000 - 556)
  assert.equal(pinned.scrollTop, 0)
})

test('an issue\'s preview reads again once its card\'s labels change, as after Move to Open, and not before', async () => {
  const real = globalThis.fetch
  let reads = 0
  let labels = ['idea']
  globalThis.fetch = (async () => {
    reads += 1
    return { ok: true, json: async () => ({ body: '', comments: [], labels: labels.map(name => ({ name })) }) }
  }) as unknown as typeof fetch
  // The preview cleans the issue's HTML, which here has no elements to clean.
  const parser = (globalThis as { DOMParser?: unknown }).DOMParser
  Object.assign(globalThis, { DOMParser: class {
    parseFromString = (markup: string) => ({ querySelectorAll: () => [],
      body: { querySelectorAll: () => [], innerHTML: markup } })
  } })
  const settle = () => new Promise(resolve => setTimeout(resolve, 0))
  try {
    preview('issue', 'game', 70, 'idea')
    await settle()
    preview('issue', 'game', 70, 'idea')
    await settle()
    assert.equal(reads, 1)
    assert.deepEqual(preview('issue', 'game', 70, 'idea')?.data.labels, [{ name: 'idea' }])
    // Move to Open took idea off: the board's next read gives the card no labels, and the panel reads the issue again.
    labels = []
    preview('issue', 'game', 70, '')
    await settle()
    assert.equal(reads, 2)
    assert.deepEqual(preview('issue', 'game', 70, '')?.data.labels, [])
    assert.equal(reads, 2)
  } finally {
    globalThis.fetch = real
    Object.assign(globalThis, { DOMParser: parser })
  }
})

test('a preview with a comment waiting for the dispatcher reads again once the store moves, and one without waits',
  async () => {
  const real = globalThis.fetch
  let reads = 0
  let waiting = true
  globalThis.fetch = (async () => {
    reads += 1
    return { ok: true, json: async () => ({ body: '', comments: [], lineComments: [], labels: [], waiting }) }
  }) as unknown as typeof fetch
  const parser = (globalThis as { DOMParser?: unknown }).DOMParser
  Object.assign(globalThis, { DOMParser: class {
    parseFromString = (markup: string) => ({ querySelectorAll: () => [],
      body: { querySelectorAll: () => [], innerHTML: markup } })
  } })
  const settle = () => new Promise(resolve => setTimeout(resolve, 0))
  try {
    ui.storeEvent = 40
    preview('pr', 'game', 71)
    await settle()
    preview('pr', 'game', 71)
    await settle()
    assert.equal(reads, 1)
    // The dispatcher's answer moves the store, and the next state's draw reads the review again.
    waiting = false
    ui.storeEvent = 41
    preview('pr', 'game', 71)
    await settle()
    assert.equal(reads, 2)
    assert.equal(preview('pr', 'game', 71)?.data.waiting, false)
    // With nothing waiting, a move of the store leaves it until PREVIEW_MS.
    ui.storeEvent = 42
    preview('pr', 'game', 71)
    await settle()
    assert.equal(reads, 2)
  } finally {
    globalThis.fetch = real
    Object.assign(globalThis, { DOMParser: parser })
    ui.storeEvent = 0
  }
})

test('Send to Agent shows in an open issue\'s comment box and a changing review\'s, and in no closed one\'s', () => {
  assert.equal(sendOf('issue', { idea: true, state: 'open' }), 'idea')
  assert.equal(sendOf('issue', { idea: false, state: 'open' }), 'issue')
  assert.equal(sendOf('issue', { idea: false, state: 'completed' }), null)
  assert.equal(sendOf('issue', { idea: false, state: 'not_planned' }), null)
  assert.equal(sendOf('issue', {}), null)
  assert.equal(sendOf('pr', { state: 'draft' }), 'review')
  assert.equal(sendOf('pr', { state: 'open' }), 'review')
  assert.equal(sendOf('pr', { state: 'merged' }), null)
})

type Element = { type: unknown; props: Record<string, unknown> & { class?: string; children?: unknown } }
type Tree = Element | string | number | null | undefined | boolean | Tree[]

/** A drawn tree's text, without its keys' hints. */
function text(node: Tree): string {
  return Array.isArray(node) ? node.map(text).join('')
    : node === null || node === undefined || typeof node === 'boolean' ? ''
    : typeof node === 'object' ? node.type === 'kbd' ? '' : text(node.props.children as Tree) : String(node)
}

/** The elements of a drawn tree that `hit` picks, outside the components it draws. */
function find(node: Tree, hit: (one: Element) => boolean): Element[] {
  return Array.isArray(node) ? node.flatMap(one => find(one, hit))
    : node && typeof node === 'object' ? [...hit(node) ? [node] : [], ...find(node.props.children as Tree, hit)] : []
}

/** A comment box's footer text and its buttons' labels, with a review's Send to Agent as ReviewButton draws it. */
function formOf(props: Record<string, unknown>) {
  const form = CommentForm({ desk: 'game', number: 12, reload: () => {}, ...props }) as Tree
  const [foot] = find(form, one => one.props.class === 'comment-foot')
  const [line] = find(foot.props.children as Tree, one => one.type === 'span' && one.props.class === 'muted')
  const buttons = find(foot.props.children as Tree, one => one.type === 'button' || one.type === ReviewButton)
    .map(one => (one.type === ReviewButton ? `review:${String(one.props.action)}`
      : text(one.props.children as Tree).replace(/\s+/g, ' ').trim()))
  return { foot: text(line.props.children as Tree).replace(/\s+/g, ' ').trim(), buttons }
}

test('each comment box says what its Send to Agent sends, and a closed issue\'s offers none', () => {
  const sends = 'Markdown works here. Send to Agent sends this text to the agent, adding @agent to the comment'
  assert.deepEqual(formOf({ kind: 'issue', issue: { state: 'open', idea: false } }), {
    foot: `${sends}, and asks for a run of this issue.`, buttons: ['Send to Agent', 'Comment'] })
  assert.deepEqual(formOf({ kind: 'issue', issue: { state: 'open', idea: true } }), {
    foot: 'Markdown works here. Send to Agent adds @agent to the comment and asks the agent to discuss this idea; it '
      + 'changes no code.',
    buttons: ['Send to Agent', 'Comment'] })
  assert.deepEqual(formOf({ kind: 'issue', issue: { state: 'completed', idea: false } }),
    { foot: 'Markdown works here.', buttons: ['Comment'] })
  // An issue the board can close also gets the close button, beside Send to Agent.
  assert.deepEqual(formOf({ kind: 'issue', issue: { state: 'open', idea: false }, closable: { desk: 'game' } }).buttons,
    ['Close Issue', 'Send to Agent', 'Comment'])
  assert.deepEqual(formOf({ kind: 'pr', pr: { state: 'draft', lineComments: [] } }),
    { foot: `${sends}.`, buttons: ['review:send', 'Comment'] })
  assert.deepEqual(formOf({ kind: 'pr', pr: { state: 'merged', lineComments: [] } }),
    { foot: 'Markdown works here.', buttons: ['Comment'] })
  for (const props of [{ kind: 'issue', issue: { state: 'open' } }, { kind: 'pr', pr: { state: 'open' } }]) {
    assert.doesNotMatch(formOf(props).foot, /Mention/)
  }
})

test('the comment box closes as Duplicate with a field for the original, and Close waits until it names another '
  + 'issue the project has', async () => {
  const real = globalThis.fetch
  globalThis.fetch = (async (url: string) => (String(url).endsWith('number=25')
    ? { ok: true, json: async () => ({ title: 'Paint the barn', body: '', comments: [] }) }
    : { ok: false, status: 404 })) as unknown as typeof fetch
  const parser = (globalThis as { DOMParser?: unknown }).DOMParser
  Object.assign(globalThis, { DOMParser: class {
    parseFromString = (markup: string) => ({ querySelectorAll: () => [],
      body: { querySelectorAll: () => [], innerHTML: markup } })
  } })
  const settle = () => new Promise(resolve => setTimeout(resolve, 0))
  const box = () => {
    const form = CommentForm({ desk: 'dup', number: 12, kind: 'issue', issue: { state: 'open', idea: false },
      closable: { desk: 'dup', key: 'open-12' }, reload: () => {} }) as Tree
    const [close] = find(form, one => one.props.class === 'close-issue')
    const options = find(close, one => one.type === 'option').map(one => text(one.props.children as Tree))
    const field = find(close, one => one.type === 'input')[0]
    const note = find(close, one => String(one.props.class ?? '').startsWith('duplicate-note'))[0]
    const button = find(close, one => one.type === 'button')[0]
    return { options, field: field?.props['aria-label'],
      note: note ? [note.props.class, text(note.props.children as Tree)] : null,
      disabled: Boolean(button?.props.disabled) }
  }
  try {
    assert.deepEqual(box(), { options: ['Completed', 'Not planned', 'Duplicate'], field: undefined, note: null,
      disabled: false })
    ui.closeReasons.set('dup:12', 'duplicate')
    assert.deepEqual(box(), { options: ['Completed', 'Not planned', 'Duplicate'],
      field: 'Original issue #12 duplicates', note: null, disabled: true })
    for (const [typed, note] of [['seven', 'Give the original issue\'s number'],
      ['#12', 'An issue can\'t duplicate itself']]) {
      ui.duplicateOf.set('dup:12', typed)
      assert.deepEqual([box().note, box().disabled], [['duplicate-note error', note], true], typed)
    }
    // A number of another issue reads its preview; Close waits for it, and stays off for a number the project lacks.
    ui.duplicateOf.set('dup:12', '#25')
    assert.deepEqual([box().note, box().disabled], [['duplicate-note muted', 'Looking for #25…'], true])
    await settle()
    assert.deepEqual([box().note, box().disabled], [['duplicate-note muted', 'Paint the barn'], false])
    ui.duplicateOf.set('dup:12', '99')
    box()
    await settle()
    assert.deepEqual([box().note, box().disabled], [['duplicate-note error', 'No issue #99'], true])
  } finally {
    globalThis.fetch = real
    Object.assign(globalThis, { DOMParser: parser })
    ui.closeReasons.delete('dup:12')
    ui.duplicateOf.delete('dup:12')
  }
})

type Node = { type: unknown; props: { class?: string; title?: string; name?: string; tone?: string;
  children?: unknown } } | string | number | null | undefined | boolean | Node[]

/** The Status section's lines for a card, each as its class, its tooltip, its icon and tone, and its text. */
function statusLines(card: Record<string, unknown>) {
  const text = (node: Node): string => (Array.isArray(node) ? node.map(text).join('')
    : node === null || node === undefined || typeof node === 'boolean' ? ''
    : typeof node === 'object' ? text(node.props.children as Node) : String(node))
  const icon = (node: Node): string | undefined => (Array.isArray(node) ? node.map(icon).find(Boolean)
    : node && typeof node === 'object' ? node.type === Icon ? `${node.props.name} ${node.props.tone}`
      : icon(node.props.children as Node) : undefined)
  return (StatusNote({ card }) as Node[]).filter(Boolean).map(line => {
    const { props } = line as { props: { class: string; title?: string } }
    return { class: props.class, title: props.title, icon: icon(line), text: text(line).replace(/\s+/g, ' ').trim() }
  })
}

test('a failing card\'s note shows as what went wrong, and its wait on its own line after the clock', () => {
  const since = new Date(Date.now() - 16 * 60_000).toISOString()
  const [note, wait, ...rest] = statusLines({ stage: 'needs', state: 'failing',
    note: 'test failed: Content.mgcb is stale', since })
  assert.deepEqual(note, { class: 'status-line failure', title: undefined, icon: 'failed danger',
    text: 'test failed: Content.mgcb is stale' })
  assert.equal(wait.class, 'status-line status-wait muted')
  assert.equal(wait.icon, 'waiting muted')
  assert.equal(wait.text, 'Waiting on you for 16m')
  const at = new Date(since).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  assert.equal(wait.title, `In Needs You since ${at}`)
  assert.deepEqual(rest, [])
})

test('a note that names no failure stays muted with no icon, and a card outside Needs You shows no wait', () => {
  assert.deepEqual(statusLines({ stage: 'queued', note: 'waits for Start', since: new Date().toISOString() }),
    [{ class: 'status-line muted', title: undefined, icon: undefined, text: 'waits for Start' }])
  const [note, wait] = statusLines({ stage: 'needs', state: 'review', note: 'left the merge queue: conflicts',
    since: new Date(Date.now() - 3 * 3_600_000).toISOString() })
  assert.equal(note.class, 'status-line muted')
  assert.equal(note.icon, undefined)
  assert.equal(wait.text, 'Waiting on you for 3h')
  assert.deepEqual(statusLines({ stage: 'needs', state: 'review' }), [])
})

const you = { login: 'you' }
const agent = { login: 'agent' }

test('an issue with a review shows Issue and Review tabs, an issue alone no Review, and a review alone no Issue', () => {
  const issueData = { commentCount: 3 }
  const pr = { comments: [{}, {}], files: [{}], checks: [{}, {}, {}] }
  const ids = (tabs: unknown[][]) => tabs.map(([id, , count]) => [id, count])
  assert.deepEqual(ids(tabsOf({ issue: 12, issueData, review: 20, pr })),
    [['issue', 3], ['review', 2], ['files', 1], ['checks', 3], ['log', undefined]])
  assert.deepEqual(tabsOf({ issue: 12, issueData, review: 20, pr }).slice(0, 2).map(([, title]) => title),
    ['Issue', 'Review'])
  assert.deepEqual(ids(tabsOf({ issue: 12, issueData })), [['issue', 3], ['log', undefined]])
  assert.deepEqual(ids(tabsOf({ review: 30, pr })), [['review', 2], ['files', 1], ['checks', 3]])
  // While the previews are read, the tabs show without their counts, and a review's files and checks wait.
  assert.deepEqual(ids(tabsOf({ issue: 12, review: 20 })), [['issue', undefined], ['review', undefined],
    ['log', undefined]])
  // Dependencies count the open sub-issues and blockers.
  const related = { subs: [{ state: 'open' }, { state: 'completed' }], blockedBy: [{ state: 'open' }], blocking: [] }
  assert.deepEqual(ids(tabsOf({ issue: 12, issueData, related })), [['issue', 3], ['deps', 2], ['log', undefined]])
  assert.equal(tabsOf({}).length, 0)
})

test('the Issue tab\'s box posts to the issue, and the Review tab\'s to a review that can still change', () => {
  for (const state of ['open', 'draft', 'merged', 'closed']) {
    assert.equal(boxOf('issue', { state }, true), 'issue')
  }
  for (const state of ['open', 'draft']) {
    assert.equal(boxOf('review', { state }, true), 'pr')
    assert.equal(boxOf('review', { state }, false), 'pr')
  }
  // A merged or closed review of an issue has no box, and one without an issue, such as a chore's, keeps its own.
  for (const state of ['merged', 'closed']) {
    assert.equal(boxOf('review', { state }, true), null)
    assert.equal(boxOf('review', { state }, false), 'pr')
  }
})

test('an issue\'s page shows its open review, or the one picked in Development, and a review its own', () => {
  const card = { id: 'game:12', desk: 'game', number: 12, pr: 20 }
  const issue = { reviews: [{ number: 15, state: 'closed' }, { number: 18, state: 'merged' },
    { number: 20, state: 'open' }] }
  try {
    assert.equal(shownReview(card, issue), 20)
    assert.equal(shownReview(card, undefined), 20)
    ui.reviewPicks.set(card.id, 18)
    assert.equal(shownReview(card, issue), 18)
    // A pick the issue's reviews don't hold, as before its preview is read, falls back to the open review.
    assert.equal(shownReview(card, undefined), 20)
    ui.reviewPicks.set(card.id, 99)
    assert.equal(shownReview(card, issue), 20)
    // Before the board names the open review, the issue's preview does.
    ui.reviewPicks.clear()
    assert.equal(shownReview({ ...card, pr: undefined }, issue), 20)
    // An issue whose reviews all closed or merged shows none until one is picked.
    const done = { reviews: issue.reviews.slice(0, 2) }
    assert.equal(shownReview({ ...card, pr: undefined }, done), undefined)
    ui.reviewPicks.set(card.id, 15)
    assert.equal(shownReview({ ...card, pr: undefined }, done), 15)
    // A chore's review, with no issue, shows itself.
    assert.equal(shownReview({ id: 'game:pr-30', desk: 'game', number: 30, pr: 30 }, undefined), 30)
  } finally {
    ui.reviewPicks.clear()
  }
})

test('the title\'s Edit edits the issue on an issue\'s page, beside its review too, and a review\'s own without one',
  () => {
  assert.deepEqual(titleTarget({ id: 'game:12', desk: 'game', number: 12 }), { kind: 'issue', number: 12 })
  // The joined page's header shows the issue's title, so its Edit edits the issue's.
  assert.deepEqual(titleTarget({ id: 'game:12', desk: 'game', number: 12, pr: 20 }), { kind: 'issue', number: 12 })
  // A chore's review has no issue and edits its own title.
  assert.deepEqual(titleTarget({ id: 'game:pr-30', desk: 'game', number: 30, pr: 30 }), { kind: 'pr', number: 30 })
  assert.deepEqual(titleTarget({ id: 'game:pr-31', desk: 'game', pr: 31 }), { kind: 'pr', number: 31 })
  // A failed run with neither has no Edit.
  assert.equal(titleTarget({ id: 'game:run-5', desk: 'game', run: 5 }), undefined)
})

/** The description's edit box for issue #12 holding `draft`: its Markdown box's draft key, buttons, and notes. */
function editBoxOf(draft: string, agentOn = false) {
  const key = editKey('game', 12, 'body')
  ui.edits.set(key, { status: 'ready', stored: 'Fix the bridge.', version: 'v1' })
  ui.drafts.set(key, draft)
  try {
    const box = BodyEdit({ desk: 'game', number: 12, reload: () => {}, agentOn }) as Tree
    // The Markdown box draws its tabs and text box once rendered; its children are Edit's own buttons and note.
    const [markdown] = find(box, one => one.type === MarkdownBox)
    const buttons = find(markdown.props.children as Tree, one => one.type === 'button')
      .map(one => ({ label: text(one.props.children as Tree).trim(), disabled: one.props.disabled }))
    const notes = find(markdown.props.children as Tree, one => one.props.class === 'edit-note muted')
      .map(one => text(one.props.children as Tree).replace(/\s+/g, ' ').trim())
    return { id: markdown.props.id, buttons, notes }
  } finally {
    ui.edits.delete(key)
    ui.drafts.delete(key)
  }
}

test('the description\'s Save waits until its text differs from the stored one, and an empty one saves', () => {
  assert.deepEqual(editBoxOf('Fix the bridge.').buttons,
    [{ label: 'Cancel', disabled: false }, { label: 'Save', disabled: true }])
  assert.deepEqual(editBoxOf('Fix the bridge today.').buttons,
    [{ label: 'Cancel', disabled: false }, { label: 'Save', disabled: false }])
  assert.equal(editBoxOf('').buttons[1].disabled, false)
  assert.equal(editBoxOf('').id, editKey('game', 12, 'body'))
})

test('the description\'s edit box says the agent won\'t see the edit while one works or waits to work the issue',
  () => {
  assert.deepEqual(editBoxOf('Fix it.').notes, [])
  assert.deepEqual(editBoxOf('Fix it.', true).notes, ['The agent read the description when it started and won\'t '
    + 'see this edit. Send to Agent tells it.'])
})

test('Edit shows its box once the source is read and while it saves, and Save posts only a changed text', () => {
  const key = editKey('game', 12, 'title')
  try {
    ui.edits.set(key, { status: 'reading' })
    assert.equal(editing('game', 12, 'title'), false)
    assert.equal(canSave(key), false)
    ui.edits.set(key, { status: 'ready', stored: 'Old', version: 'v1' })
    ui.drafts.set(key, 'Old')
    assert.equal(editing('game', 12, 'title'), true)
    assert.equal(canSave(key), false)
    ui.drafts.set(key, 'New')
    assert.equal(canSave(key), true)
    ui.edits.set(key, { status: 'saving', stored: 'Old', version: 'v1' })
    assert.equal(editing('game', 12, 'title'), true)
    assert.equal(canSave(key), false)
  } finally {
    ui.edits.delete(key)
    ui.drafts.delete(key)
  }
})

test('Edit shows on a comment you wrote and kept, and not on one sent to the agent, sent with a line comment, posted '
  + 'a moment ago, or by anyone else', () => {
  const you = { login: 'you' }
  const shown = (comment: Record<string, unknown>) => commentEditable({ id: 5, author: you, ...comment })
  assert.equal(shown({}), true)
  assert.equal(shown({ sent: false }), true)
  assert.equal(shown({ edited: '2026-11-14T20:00:00Z' }), true)
  assert.equal(shown({ toAgent: true }), false)
  assert.equal(shown({ sent: true }), false)
  // A line comment the page shows before the store reads it back has no id yet.
  assert.equal(shown({ id: 0 }), false)
  for (const login of ['agent', 'desk', 'octocat']) {
    assert.equal(shown({ author: { login } }), false, login)
  }
  const none = commentEdit('game', 12, { id: 5, author: { login: 'agent' } }, () => {})
  assert.deepEqual(none, { action: undefined, editor: undefined })
  const { action, editor } = commentEdit('game', 12, { id: 5, author: you }, () => {}) as
    { action: Element; editor: unknown }
  assert.equal(action.type, EditButton)
  assert.deepEqual([action.props.number, action.props.field], [12, commentField(5)])
  assert.equal(editor, undefined)
})

test('a comment\'s Edit opens the description\'s box on its Markdown, whose Save waits for a changed text with more '
  + 'than spaces', () => {
  const key = editKey('game', 12, commentField(5))
  ui.edits.set(key, { status: 'ready', stored: 'Use blue.', version: 'v1' })
  try {
    const { editor } = commentEdit('game', 12, { id: 5, author: { login: 'you' } }, () => {}) as
      { editor: Element }
    assert.equal(editor.type, BodyEdit)
    const boxOf = (draft: string) => {
      ui.drafts.set(key, draft)
      const box = BodyEdit(editor.props) as Tree
      const [markdown] = find(box, one => one.type === MarkdownBox)
      const [save] = find(markdown.props.children as Tree, one => one.type === 'button'
        && one.props.class === 'primary')
      return { id: markdown.props.id, label: markdown.props.label, save: save.props.disabled,
        notes: find(markdown.props.children as Tree, one => one.props.class === 'edit-note muted').length }
    }
    assert.deepEqual(boxOf('Use blue.'), { id: key, label: 'Your comment on #12', save: true, notes: 0 })
    assert.equal(boxOf('Use green.').save, false)
    assert.equal(boxOf('  \n ').save, true)
    // A description may be emptied; a comment may not.
    const body = editKey('game', 12, 'body')
    ui.edits.set(body, { status: 'ready', stored: 'Fix it.', version: 'v1' })
    ui.drafts.set(body, '')
    assert.equal(canSave(body), true)
    ui.edits.delete(body)
    ui.drafts.delete(body)
  } finally {
    ui.edits.delete(key)
    ui.drafts.delete(key)
  }
})

test('an edited comment says edited after its time, with the edit\'s time on hover, and one never edited says nothing',
  () => {
  assert.equal(EditedMark({ at: undefined }), null)
  const drawn = EditedMark({ at: '2026-11-14T20:00:00Z' }) as Tree
  assert.equal(text(drawn), ' · edited')
  const [mark] = find(drawn, one => one.props.class === 'muted edited-mark')
  assert.match(String(mark.props.title), /^Edited .*2026/)
})

test('the agent log draws each leading time in its own span and copies as the joined lines', () => {
  type Part = string | { type: string; props: { class: string; children: string } }
  const lines = ['15:11:10 Read web/app.js', 'continued output', '15:11:12 Waiting until 15:12:00 for checks']
  const parts = logLines(lines) as Part[]
  const shown = parts.map(part => (typeof part === 'string' ? part : `[${part.props.class}:${part.props.children}]`))
  assert.deepEqual(shown, ['[log-time:15:11:10]', ' Read web/app.js', '\n', 'continued output', '\n',
    '[log-time:15:11:12]', ' Waiting until 15:12:00 for checks'])
  const copied = parts.map(part => (typeof part === 'string' ? part : part.props.children)).join('')
  assert.equal(copied, lines.join('\n'))
  assert.deepEqual(logLines(['15:11 short stamp', '15:11:10x no space']),
    ['15:11 short stamp', '\n', '15:11:10x no space'])
})

test('Delete Issue confirms only on the issue\'s number with its #, as the dialog asks', () => {
  assert.equal(confirmsDelete('#12', 12), true)
  assert.equal(confirmsDelete(' #12 ', 12), true)
  assert.equal(confirmsDelete('12', 12), false)
  assert.equal(confirmsDelete('#1', 12), false)
  assert.equal(confirmsDelete('#120', 12), false)
  assert.equal(confirmsDelete('', 12), false)
})

/** The confirm dialog's Delete button for `asked`, and the form's text. */
function deleteFormOf(asked: Record<string, unknown>) {
  const form = DeleteForm({ asked: { desk: 'game', number: 12, typed: '', status: 'typing', error: null, ...asked },
    title: 'Filed by mistake' }) as Tree
  const [submit] = find(form, one => one.type === 'button' && one.props.type === 'submit')
  const [cancel] = find(form, one => one.type === 'button' && one.props.type === 'button')
  return { submit: { text: text(submit.props.children as Tree).trim(), disabled: submit.props.disabled },
    cancel: cancel.props.disabled, text: text(form).replace(/\s+/g, ' ').trim() }
}

test('the dialog\'s Delete stays off until the box holds the issue\'s number, and while the delete runs', () => {
  const empty = deleteFormOf({})
  assert.deepEqual(empty.submit, { text: 'Delete Issue', disabled: true })
  assert.match(empty.text, /^Delete issue #12\?Filed by mistake goes for good, with its comments, labels, and links\./)
  assert.match(empty.text, /Type #12 to confirm/)
  assert.deepEqual(deleteFormOf({ typed: '12' }).submit, { text: 'Delete Issue', disabled: true })
  assert.deepEqual(deleteFormOf({ typed: '#12' }).submit, { text: 'Delete Issue', disabled: false })
  const running = deleteFormOf({ typed: '#12', status: 'deleting' })
  assert.deepEqual([running.submit, running.cancel], [{ text: 'Deleting…', disabled: true }, true])
  assert.match(deleteFormOf({ typed: '#12', error: '#12 has review #14' }).text, /confirm#12 has review #14\.Cancel/)
})

test('the sidebar\'s Delete Issue opens the dialog for its own issue, and Cancel closes it', () => {
  const button = DeleteButton({ desk: 'game', number: 12 }) as Tree
  const [press] = find(button, one => one.type === 'button')
  assert.equal(text(press.props.children as Tree).trim(), 'Delete Issue')
  try {
    assert.equal(DeleteDialog({ desk: 'game', number: 12, title: 'T' }), null)
    ;(press.props.onClick as () => void)()
    assert.deepEqual(ui.deleting, { desk: 'game', number: 12, typed: '', status: 'typing', error: null })
    assert.notEqual(DeleteDialog({ desk: 'game', number: 12, title: 'T' }), null)
    assert.equal(DeleteDialog({ desk: 'game', number: 13, title: 'T' }), null)
    assert.equal(DeleteDialog({ desk: 'other', number: 12, title: 'T' }), null)
    cancelDelete()
    assert.equal(ui.deleting, null)
  } finally {
    ui.deleting = null
  }
})

test('a delete the server takes closes the dialog and the page, and one it refuses shows why in the dialog',
  async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const real = globalThis.fetch
  const sent: unknown[] = []
  let answer = { ok: false, status: 409, body: { error: '#12 has review #14, so it can\'t be deleted' } }
  globalThis.fetch = (async (path: string, init: { body: string }) => {
    sent.push([path, JSON.parse(init.body)])
    return { ok: answer.ok, status: answer.status, json: async () => answer.body }
  }) as unknown as typeof fetch
  try {
    ui.closed = false
    askDelete('game', 12)
    await deleteIssue()
    assert.deepEqual(sent, [], 'nothing posts before the box confirms')
    ui.deleting = { ...ui.deleting, typed: '#12' }
    await deleteIssue()
    assert.deepEqual(sent, [['/api/issue-delete', { desk: 'game', number: 12 }]])
    assert.deepEqual(ui.deleting, { desk: 'game', number: 12, typed: '#12', status: 'typing',
      error: '#12 has review #14, so it can\'t be deleted' })
    assert.equal(ui.closed, false)
    answer = { ok: true, status: 200, body: { number: 12 } }
    await deleteIssue()
    assert.equal(ui.deleting, null)
    assert.equal(ui.closed, true)
    assert.equal(ui.toasts.at(-1)?.text, 'Deleted #12.')
  } finally {
    globalThis.fetch = real
    ui.deleting = null
    ui.closed = false
    ui.toasts = []
    t.mock.timers.reset()
  }
})

test('the Labels section\'s Edit opens a picker of the project\'s labels with the item\'s pressed, leaving out the '
  + 'labels the store keeps as a state', () => {
  ui.server = { desks: [{ id: 'game', labels: { bug: 'd73a4a', web: '1d76db', 'agent-ready': '0e8a16',
    'in-progress': 'ededed', waiting: 'ededed', 'needs-design': 'ededed', 'on-hold': 'ededed', backlog: 'ededed' } }] }
  try {
    const button = () => LabelsButton({ desk: 'game', number: 12 }) as Element
    assert.equal(text(button().props.children as Tree), 'Edit')
    assert.equal(editingLabels('game', 12), false)
    ;(button().props.onClick as () => void)()
    assert.equal(editingLabels('game', 12), true)
    assert.equal(text(button().props.children as Tree), 'Done')
    const edit = LabelsEdit({ desk: 'game', number: 12, picked: ['web', 'agent-ready'], reload: () => {} }) as Element
    assert.equal(edit.type, LabelPicker)
    assert.equal(edit.props.left, STATE_LABELS)
    const picker = LabelPicker(edit.props) as Tree
    const presses = find(picker, one => one.props.class === 'label-pick')
    assert.deepEqual(presses.map(one => [(find(one.props.children as Tree, two => two.type === Label)[0]?.props.label as
      { name: string }).name, one.props['aria-pressed']]), [['backlog', 'false'], ['bug', 'false'], ['web', 'true']])
    // Esc closes the picker, as Done does.
    let stopped = false
    ;(edit.props.onKeyDown as (event: unknown) => void)({ key: 'Escape', preventDefault: () => {},
      stopPropagation: () => { stopped = true } })
    assert.equal(stopped, true)
    assert.equal(editingLabels('game', 12), false)
  } finally {
    ui.edits.delete(editKey('game', 12, 'labels'))
    ui.server = null
  }
})

test('pressing a label in the picker posts it to /api/edit and shows the stored labels; a refusal shows its reason',
  async () => {
  const real = globalThis.fetch
  const posts: unknown[] = []
  let answer: [number, unknown] = [200, { labels: ['bug', 'web'] }]
  globalThis.fetch = (async (_url: string, init: { body: string }) => {
    posts.push(JSON.parse(init.body))
    return new Response(JSON.stringify(answer[1]), { status: answer[0] })
  }) as typeof fetch
  ui.server = { desks: [{ id: 'game', title: 'Game', labels: { bug: 'd73a4a' } }] }
  const shown: unknown[] = []
  const reload = (change: (data: { labels: unknown[] }) => unknown) => shown.push(change({ labels: [] }))
  try {
    // A closed picker writes nothing.
    assert.equal(await writeLabels('game', 12, { addLabels: ['web'] }, reload), false)
    toggleLabels('game', 12)
    const edit = LabelsEdit({ desk: 'game', number: 12, picked: ['bug'], reload }) as Element
    ;(edit.props.onToggle as (name: string) => void)('web')
    // The picker waits while a write runs.
    assert.equal(ui.edits.get(editKey('game', 12, 'labels'))?.status, 'saving')
    assert.equal((LabelsEdit({ desk: 'game', number: 12, picked: ['bug'], reload }) as Element).props.disabled, true)
    await new Promise(resolve => setTimeout(resolve, 10))
    assert.deepEqual(posts, [{ desk: 'game', number: 12, addLabels: ['web'] }])
    assert.deepEqual(shown, [{ labels: [{ name: 'bug', color: 'd73a4a' }, { name: 'web', color: 'ededed' }] }])
    // Pressing a picked label takes it off.
    ;(edit.props.onToggle as (name: string) => void)('bug')
    await new Promise(resolve => setTimeout(resolve, 10))
    assert.deepEqual(posts[1], { desk: 'game', number: 12, removeLabels: ['bug'] })
    // A typed name the server refuses stays in the box, and the toast gives the reason after the project's name.
    answer = [400, { error: 'a label is a name on one line, of at most 50 characters, without commas' }]
    ;(edit.props.onType as (text: string) => void)(' a,b ')
    ;(edit.props.onAdd as () => void)()
    await new Promise(resolve => setTimeout(resolve, 10))
    assert.deepEqual(posts[2], { desk: 'game', number: 12, addLabels: ['a,b'] })
    assert.equal(shown.length, 2)
    assert.equal(ui.drafts.get(editKey('game', 12, 'labels')), ' a,b ')
    assert.equal(ui.toasts.at(-1)?.text,
      'Game: a label is a name on one line, of at most 50 characters, without commas.')
    // A typed name the store takes empties the box.
    answer = [200, { labels: ['bug', 'docs'] }]
    ;(edit.props.onType as (text: string) => void)('docs')
    ;(edit.props.onAdd as () => void)()
    await new Promise(resolve => setTimeout(resolve, 10))
    assert.equal(ui.drafts.has(editKey('game', 12, 'labels')), false)
    assert.equal(ui.edits.get(editKey('game', 12, 'labels'))?.status, 'ready')
  } finally {
    globalThis.fetch = real
    ui.edits.delete(editKey('game', 12, 'labels'))
    ui.drafts.delete(editKey('game', 12, 'labels'))
    ui.toasts = []
    ui.server = null
  }
})
