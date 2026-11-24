// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { closeNewIssue, createNewIssue, defaultProject, issueNumbers, NEW_ISSUE, NewIssue, openNewIssue, pickProject,
  showCreated, untouched } from './app-new-issue.js'
import { emptyDraft, ui } from './app-state.js'

const DESKS = [{ id: 'yard', title: 'Yard', labels: { bug: 'd73a4a', 'agent-ready': '0e8a16' }, cards: [] },
  { id: 'shed', title: 'Shed', labels: {}, cards: [] }]
const realFetch = globalThis.fetch

afterEach(() => {
  ui.server = null
  ui.tab = 'all'
  ui.newIssue = emptyDraft()
  ui.created = null
  ui.selected = null
  ui.detailTab = null
  ui.drafts.clear()
  ui.rendered.clear()
  ui.previewing.clear()
  ui.toasts = []
  globalThis.fetch = realFetch
})

/**
 * Answers the page's posts with `status` and `data`, and keeps each post's path and body. A template read gets the
 * project's entry in `templates`, or a 404 without one.
 */
function answer(status: number, data: unknown, templates: Record<string, string> = {}):
  { path: string; sent: Record<string, unknown> }[] {
  const posts: { path: string; sent: Record<string, unknown> }[] = []
  globalThis.fetch = (async (path: string, init?: { body: string }) => {
    if (init === undefined) {
      const desk = new URL(path, 'http://localhost').searchParams.get('desk') ?? ''
      return desk in templates ? new Response(JSON.stringify({ template: templates[desk] }))
        : new Response('{"error":"no such project"}', { status: 404 })
    }
    posts.push({ path, sent: JSON.parse(init.body) })
    return new Response(JSON.stringify(data), { status })
  }) as typeof fetch
  return posts
}

// A drawn element, with its function components drawn in place.
type Drawn = { type?: unknown; props?: Record<string, unknown> } | string | number | null | undefined | boolean
  | Drawn[]
function drawn(node: Drawn): Drawn {
  if (Array.isArray(node)) {
    return node.map(drawn)
  }
  if (node === null || typeof node !== 'object') {
    return node
  }
  if (typeof node.type === 'function') {
    return drawn((node.type as (props: unknown) => Drawn)(node.props))
  }
  return { ...node, props: { ...node.props, children: drawn(node.props?.children as Drawn) } }
}

/** The text of a drawn element and everything under it. */
function textOf(node: Drawn): string {
  if (Array.isArray(node)) {
    return node.map(textOf).join('')
  }
  if (node === null || node === undefined || typeof node === 'boolean') {
    return ''
  }
  return typeof node === 'object' ? textOf(node.props?.children as Drawn) : String(node)
}

/** The drawn buttons whose text includes `text`. */
function buttons(node: Drawn, text: string): { props: Record<string, unknown> }[] {
  if (Array.isArray(node)) {
    return node.flatMap(one => buttons(one, text))
  }
  if (node === null || typeof node !== 'object') {
    return []
  }
  const own = node.type === 'button' && textOf(node).includes(text) ? [node as { props: Record<string, unknown> }]
    : []
  return [...own, ...buttons(node.props?.children as Drawn, text)]
}

test('the project picker starts on the open project\'s tab, or on All on the selected card\'s project', () => {
  // A project's tab picks its project, whatever card is selected.
  assert.equal(defaultProject('shed', DESKS, 'yard'), 'shed')
  // All picks the selected card's project, and with no card selected the first project in the page's order.
  assert.equal(defaultProject('all', DESKS, 'shed'), 'shed')
  assert.equal(defaultProject('all', DESKS, undefined), 'yard')
  // A project the page no longer shows counts as none, and no project leaves the picker empty.
  assert.equal(defaultProject('gone', DESKS, 'gone'), 'yard')
  assert.equal(defaultProject('all', [], undefined), null)
})

test('the form keeps its draft while closed, and a blank draft starts on the project open now', () => {
  ui.server = { desks: DESKS }
  ui.tab = 'shed'
  openNewIssue('yard')
  assert.equal(ui.newIssue.open, true)
  assert.equal(ui.newIssue.desk, 'shed')
  ui.newIssue.title = 'Fix the gate'
  closeNewIssue()
  assert.equal(ui.newIssue.open, false)
  ui.tab = 'all'
  openNewIssue('yard')
  assert.deepEqual([ui.newIssue.desk, ui.newIssue.title], ['shed', 'Fix the gate'])
  // An emptied draft starts again on the project the page shows now.
  ui.newIssue.title = ' '
  openNewIssue('yard')
  assert.equal(ui.newIssue.desk, 'yard')
})

test('Create waits for a title', () => {
  ui.server = { desks: DESKS }
  openNewIssue(undefined)
  const create = () => buttons(drawn(NewIssue() as Drawn), 'Create')[0]?.props.disabled
  assert.equal(create(), true)
  ui.newIssue.title = '   '
  assert.equal(create(), true)
  ui.newIssue.title = 'Fix the gate'
  assert.equal(create(), false)
})

test('a create posts the draft, clears the form, and selects the new card on its Issue tab once the board has it',
  async () => {
    ui.server = { desks: DESKS }
    openNewIssue('yard')
    ui.newIssue.title = 'Fix the gate'
    ui.newIssue.labels = ['bug']
    ui.newIssue.agentReady = true
    ui.newIssue.parent = ' #3 '
    ui.newIssue.blockedBy = '#4, 5 #4'
    ui.drafts.set(NEW_ISSUE, 'It squeaks.')
    const posts = answer(201, { number: 7 })
    await createNewIssue()
    assert.deepEqual(posts, [{ path: '/api/issue-create', sent: { desk: 'yard', title: 'Fix the gate',
      body: 'It squeaks.', labels: ['bug'], agentReady: true, parent: 3, blockedBy: [4, 5, 4] } }])
    assert.deepEqual(ui.newIssue, emptyDraft())
    assert.equal(ui.drafts.has(NEW_ISSUE), false)
    // The board that lists the new card comes after the create's answer.
    assert.deepEqual(ui.created, { desk: 'yard', number: 7 })
    const card = { id: 'yard:7', desk: 'yard', stage: 'open', number: 7, title: 'Fix the gate', actions: [] }
    ui.server = { desks: [{ ...DESKS[0], cards: [card] }, DESKS[1]] }
    ui.detailTab = 'log'
    assert.equal(showCreated(), true)
    assert.deepEqual([ui.selected, ui.detailTab, ui.created], ['yard:7', 'issue', null])
  })

test('a refused create keeps the draft and toasts the server\'s reason after the project\'s name', async () => {
  ui.server = { desks: DESKS }
  openNewIssue('yard')
  ui.newIssue.title = 'x'.repeat(300)
  ui.drafts.set(NEW_ISSUE, 'Long.')
  answer(400, { error: 'a title holds at most 256 characters' })
  await createNewIssue()
  assert.equal(ui.newIssue.title, 'x'.repeat(300))
  assert.equal(ui.newIssue.open, true)
  assert.equal(ui.newIssue.creating, false)
  assert.equal(ui.drafts.get(NEW_ISSUE), 'Long.')
  assert.equal(ui.created, null)
  assert.equal(ui.toasts.at(-1)?.text, 'Yard: a title holds at most 256 characters.')
})

test('Parent and Blocked By read numbers with or without #, split by commas or spaces, and pass on any other word', () => {
  assert.deepEqual(issueNumbers('#12, #15'), [12, 15])
  assert.deepEqual(issueNumbers(' 12 15,16 '), [12, 15, 16])
  assert.deepEqual(issueNumbers(''), [])
  assert.deepEqual(issueNumbers('#12 twelve 0'), [12, 'twelve', 0])
})

test('a create sends an empty Parent as null and a Parent of several words as typed, for the server to refuse',
  async () => {
    ui.server = { desks: DESKS }
    for (const [parent, sent] of [['', null], ['#3 #4', '#3 #4'], ['three', 'three']] as const) {
      openNewIssue('yard')
      ui.newIssue.title = 'Fix the gate'
      ui.newIssue.parent = parent
      const posts = answer(400, { error: 'a parent is one issue number' })
      await createNewIssue()
      assert.equal(posts[0]?.sent.parent, sent)
    }
    assert.equal(ui.newIssue.parent, 'three')
    assert.equal(ui.toasts.at(-1)?.text, 'Yard: a parent is one issue number.')
  })

test('Agent-Ready starts unchecked each time the form opens, and its box says it starts an agent', () => {
  ui.server = { desks: DESKS }
  openNewIssue('yard')
  ui.newIssue.title = 'Fix the gate'
  ui.newIssue.agentReady = true
  closeNewIssue()
  openNewIssue('yard')
  assert.equal(ui.newIssue.agentReady, false)
  assert.match(textOf(drawn(NewIssue() as Drawn)), /Agent-Ready: start an agent on it now/)
})

test('Parent and Blocked By open empty, with no sample number that reads as a real one', () => {
  ui.server = { desks: DESKS }
  openNewIssue('yard')
  const fields: Record<string, unknown>[] = []
  const walk = (node: Drawn): void => {
    if (Array.isArray(node)) {
      node.forEach(walk)
    } else if (node !== null && typeof node === 'object') {
      if (node.type === 'input' && node.props?.class === 'number-field') {
        fields.push(node.props)
      }
      walk(node.props?.children as Drawn)
    }
  }
  walk(drawn(NewIssue() as Drawn))
  assert.deepEqual(fields.map(one => [one.value, one.placeholder]), [['', undefined], ['', undefined]])
})

test('the body starts from the project\'s template, and a project change swaps it only while it is untouched',
  async () => {
    ui.server = { desks: DESKS }
    answer(201, {}, { yard: '## Goal\n', shed: '## Aim\n' })
    await openNewIssue('yard')
    assert.deepEqual([ui.drafts.get(NEW_ISSUE), ui.newIssue.template], ['## Goal\n', '## Goal\n'])
    // An untouched template swaps for the next project's.
    await pickProject('shed')
    assert.deepEqual([ui.drafts.get(NEW_ISSUE), ui.newIssue.template], ['## Aim\n', '## Aim\n'])
    // A body the person changed stays when the project changes, and when the form opens again.
    ui.drafts.set(NEW_ISSUE, '## Aim\n\nPaint it.')
    await pickProject('yard')
    assert.equal(ui.drafts.get(NEW_ISSUE), '## Aim\n\nPaint it.')
    closeNewIssue()
    await openNewIssue('yard')
    assert.equal(ui.drafts.get(NEW_ISSUE), '## Aim\n\nPaint it.')
    // An emptied body takes the template again when the form opens.
    ui.drafts.set(NEW_ISSUE, '')
    closeNewIssue()
    await openNewIssue('yard')
    assert.equal(ui.drafts.get(NEW_ISSUE), '## Goal\n')
  })

test('a template that arrives after the project changed, or after the person wrote, leaves the body alone',
  async () => {
    ui.server = { desks: DESKS }
    answer(201, {}, { yard: '## Goal\n', shed: '## Aim\n' })
    const late = openNewIssue('yard')
    ui.newIssue.desk = 'shed'
    await late
    assert.equal(ui.drafts.get(NEW_ISSUE) ?? '', '')
    const written = pickProject('yard')
    ui.drafts.set(NEW_ISSUE, 'Paint it.')
    await written
    assert.equal(ui.drafts.get(NEW_ISSUE), 'Paint it.')
    // A read the server refuses leaves the body too.
    ui.drafts.set(NEW_ISSUE, '')
    answer(201, {})
    await pickProject('shed')
    assert.equal(ui.drafts.get(NEW_ISSUE), '')
    assert.equal(untouched(' \n', '## Goal\n'), true)
    assert.equal(untouched('## Goal\n', null), false)
  })
