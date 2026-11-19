// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { answerFor, answerText, Body, covers, draftOf, hasAnswer } from './app-decisions.js'
import { answeringOf } from './app-detail.js'
import { ui } from './app-state.js'

const SECTION = {
  anchor: 'issue-body', place: 'the description', heading: '<h2>Decisions Needed</h2>\n', intro: '',
  decisions: [
    { number: 1, question: 'Should the banner pause combat?', head: '<h3>1. Should the banner pause combat?</h3>\n',
      after: '', options: [{ letter: 'A', label: 'Pause combat', pick: false, html: '<strong>A. Pause combat</strong>' },
        { letter: 'B', label: 'Overlay it', pick: true, html: '<strong>B. Overlay it</strong> ✅' }] },
    { number: 2, question: 'Which color?', head: '<h3>2. Which color?</h3>\n', after: '', options: [] },
  ],
}
const LEGACY = { anchor: 'comment-42', place: 'the comment of 2026-11-13T18:05:39Z', heading: '<h2>Decisions Needed</h2>',
  intro: '<ul><li>Should it pause?</li></ul>', decisions: [] }
const you = { login: 'you' }
const draft = (change: object) => ({ picks: {}, other: {}, note: '', ...change })

test('the answer comment links its section, lists each answered decision, and ends with the note', () => {
  assert.equal(answerText(SECTION, draft({ picks: { 1: 'B' }, other: { 2: ' Keep the old color for now. ' },
    note: 'Note text.' })), 'Answers to the Decisions Needed in [the description](#issue-body):\n\n'
    + '- **1B.** Overlay it\n- **2.** Keep the old color for now.\n\nNote text.')
  // A pick with Other text carries both; a decision left alone stays out.
  assert.equal(answerText(SECTION, draft({ picks: { 1: 'A' }, other: { 1: 'but only in boss fights' } })),
    'Answers to the Decisions Needed in [the description](#issue-body):\n\n'
    + '- **1A.** Pause combat: but only in boss fights')
  // A section in the older layout answers with its one text box.
  assert.equal(answerText(LEGACY, draft({ note: 'Overlay it.' })),
    'Answers to the Decisions Needed in [the comment of 2026-11-13T18:05:39Z](#comment-42):\n\nOverlay it.')
  assert.equal(answerText(SECTION, draft({ picks: { 1: undefined }, other: { 2: '  ' } })), '')
})

test('a draft answers once it has a pick, Other text, or a note, and a form without one starts empty', () => {
  assert.equal(hasAnswer(draft({})), false)
  assert.equal(hasAnswer(draft({ picks: { 1: undefined }, other: { 2: ' ' }, note: '\n' })), false)
  assert.equal(hasAnswer(draft({ picks: { 1: 'B' } })), true)
  assert.equal(hasAnswer(draft({ other: { 2: 'blue' } })), true)
  assert.equal(hasAnswer(draft({ note: 'Later.' })), true)
  assert.deepEqual(draftOf('game:1#nothing-yet'), draft({}))
  // The bottom box's text under another key never reads as a form's draft.
  ui.drafts.set('game:1#odd', 'text')
  assert.deepEqual(draftOf('game:1#odd'), draft({}))
  ui.drafts.delete('game:1#odd')
})

test('a section takes its newest answer, which folds the form once it covers every decision', () => {
  const answer = (id: number, anchor: string, picks: string[]) => ({ id, author: you, at: '', answers: { anchor, picks } })
  const comments = [answer(1, 'issue-body', ['1A']), { id: 2, author: you, at: '' }, answer(3, 'comment-9', ['1B']),
    answer(4, 'issue-body', ['1B', '2'])]
  assert.equal(answerFor(comments, 'issue-body')?.id, 4)
  assert.equal(answerFor(comments, 'comment-9')?.id, 3)
  assert.equal(answerFor(comments, 'review-body'), undefined)
  assert.equal(covers(SECTION, answerFor(comments, 'issue-body')), true)
  // An answer to decision 1 alone leaves the form open for decision 2, as after the agent adds a decision.
  assert.equal(covers(SECTION, comments[0]), false)
  assert.equal(covers(SECTION, undefined), false)
  assert.equal(covers(LEGACY, answer(5, 'comment-42', [])), true)
})

type Tree = Node | Tree[] | string | number | null | undefined | boolean
type Node = { type: unknown; props: Record<string, unknown> & { children?: Tree } }

/** Every element in `tree` that `hit` takes, drawing the module's own components into their elements. */
function find(tree: Tree, hit: (node: Node) => boolean): Node[] {
  if (Array.isArray(tree)) {
    return tree.flatMap(one => find(one, hit))
  }
  if (!tree || typeof tree !== 'object') {
    return []
  }
  if (typeof tree.type === 'function' && ['Section', 'AnswerForm'].includes(tree.type.name)) {
    return find((tree.type as (props: object) => Tree)(tree.props), hit)
  }
  return [...hit(tree) ? [tree] : [], ...find(tree.props.children, hit)]
}

function text(tree: Tree): string {
  return Array.isArray(tree) ? tree.map(text).join('') : tree && typeof tree === 'object'
    ? text(tree.props.children) : tree === null || tree === undefined || typeof tree === 'boolean' ? '' : String(tree)
}

type Posted = { key: string; text: string; toAgent: boolean }

/**
 * What a body with `section` draws: its answered line, option buttons, text boxes, and the form's buttons. `press`
 * sends a keydown to the text box with `placeholder`, the `at`th of them, and says whether it prevented the default;
 * the form's posts land in `posted`.
 */
function drawn(section: object, { comments = [] as object[], send = true, busy = false } = {}) {
  const posted: Posted[] = []
  const answering = { desk: 'game', number: 12, comments, send, busy: () => busy,
    post: async (key: string, text: string, toAgent: boolean) => {
      posted.push({ key, text, toAgent })
      return true
    } }
  const tree = Body({ markup: '<p>whole</p>', parts: [{ html: '<p>Before.</p>' }, { section }], answering }) as Tree
  const buttons = find(tree, one => one.type === 'button')
  const boxes = find(tree, one => one.type === 'input' || one.type === 'textarea')
  return {
    posted,
    press: (placeholder: string, keys: Partial<KeyboardEvent>, at = 0) => {
      let prevented = false
      const event = { ctrlKey: false, metaKey: false, ...keys, preventDefault: () => { prevented = true } }
      const box = boxes.filter(one => one.props.placeholder === placeholder)[at]
      ;(box.props.onKeyDown as (event: object) => void)(event)
      return prevented
    },
    answered: find(tree, one => one.props.class === 'answered muted').map(one => text(one.props.children)
      .replace(/\s+/g, ' ').trim()),
    options: buttons.filter(one => one.props.class === 'answer-option').map(one => [text(one.props.children)
      .replace(/\s+/g, ' ').trim(), one.props['aria-pressed']]),
    boxes: boxes.map(one => one.props.placeholder),
    buttons: buttons.filter(one => one.props.class !== 'answer-option' && one.props.class !== 'link')
      .map(one => [text(one.props.children).replace(/\s+/g, ' ').trim(), one.props.disabled]),
    pick: (label: string) => (buttons.find(one => text(one.props.children).includes(label))?.props.onClick as
      () => void)(),
  }
}

test('an unanswered section shows its form open, with a button per option and Send to Agent where the box has it',
  () => {
  const shown = drawn(SECTION)
  assert.deepEqual(shown.answered, [])
  assert.deepEqual(shown.options, [['A Pause combat', 'false'], ['B Overlay it ✅', 'false']])
  assert.deepEqual(shown.boxes, ['Other', 'Other', 'Add a note'])
  // Neither button takes a press until the draft answers something.
  assert.deepEqual(shown.buttons, [['Send to Agent', true], ['Comment Ctrl+Enter', true]])
  assert.deepEqual(drawn(SECTION, { send: false }).buttons, [['Comment Ctrl+Enter', true]])
  // A section in the older layout gets one text box.
  const legacy = drawn(LEGACY)
  assert.deepEqual(legacy.options, [])
  assert.deepEqual(legacy.boxes, ['Your answer'])
})

test('a pick stays in the form\'s draft through a redraw, one per decision, and a second press takes it back', () => {
  const key = 'game:12#issue-body'
  try {
    drawn(SECTION).pick('Pause combat')
    assert.deepEqual(ui.drafts.get(key)?.picks, { 1: 'A' })
    const picked = drawn(SECTION)
    assert.deepEqual(picked.options, [['A Pause combat', 'true'], ['B Overlay it ✅', 'false']])
    assert.deepEqual(picked.buttons, [['Send to Agent', false], ['Comment Ctrl+Enter', false]])
    picked.pick('Overlay it')
    assert.deepEqual(ui.drafts.get(key)?.picks, { 1: 'B' })
    drawn(SECTION).pick('Overlay it')
    assert.equal(hasAnswer(draftOf(key)), false)
  } finally {
    ui.drafts.delete(key)
  }
})

test('Ctrl+Enter or Cmd+Enter in the note or an Other field presses Comment, and only when Comment works', async () => {
  const key = 'game:12#issue-body'
  const settle = () => new Promise(resolve => setImmediate(resolve))
  try {
    // An empty form posts nothing, yet Ctrl+Enter still never reaches the note as a newline.
    const empty = drawn(SECTION)
    assert.equal(empty.press('Add a note', { key: 'Enter', ctrlKey: true }), true)
    assert.equal(empty.press('Other', { key: 'Enter', metaKey: true }, 1), true)
    await settle()
    assert.deepEqual(empty.posted, [])
    ui.drafts.set(key, draft({ picks: { 1: 'B' }, note: 'Ship it.' }))
    const fromNote = drawn(SECTION)
    // Plain Enter keeps its own meaning: a newline in the note, nothing in an Other field.
    assert.equal(fromNote.press('Add a note', { key: 'Enter' }), false)
    assert.equal(fromNote.press('Other', { key: 'Enter' }), false)
    assert.equal(fromNote.press('Add a note', { key: 'Enter', ctrlKey: true }), true)
    await settle()
    assert.deepEqual(fromNote.posted, [{ key, toAgent: false,
      text: 'Answers to the Decisions Needed in [the description](#issue-body):\n\n- **1B.** Overlay it\n\nShip it.' }])
    // Typing into an Other field redraws nothing, so the key reads the draft as it stands.
    ui.drafts.set(key, draft({ other: { 2: 'blue' } }))
    for (const keys of [{ ctrlKey: true }, { metaKey: true }]) {
      const fromOther = drawn(SECTION)
      fromOther.press('Other', { key: 'Enter', ...keys }, 1)
      await settle()
      assert.deepEqual(fromOther.posted.map(one => [one.toAgent, one.text.split('\n').at(-1)]),
        [[false, '- **2.** blue']])
    }
    // A form that is posting takes no second post.
    const posting = drawn(SECTION, { busy: true })
    assert.equal(posting.press('Add a note', { key: 'Enter', ctrlKey: true }), true)
    await settle()
    assert.deepEqual(posting.posted, [])
  } finally {
    ui.drafts.delete(key)
    ui.answering.clear()
  }
})

test('an answered section says who answered what and when, and folds its form behind Answer Again', () => {
  const at = new Date(Date.now() - 5 * 60_000).toISOString()
  const comments = [{ id: 7, author: you, at, answers: { anchor: 'issue-body', picks: ['1B', '2'] } }]
  const folded = drawn(SECTION, { comments })
  assert.deepEqual(folded.answered, ['You answered 1B, 2 · 5m ago'])
  assert.deepEqual(folded.options, [])
  assert.deepEqual(folded.buttons, [['Answer Again', undefined]])
  try {
    folded.pick('Answer Again')
    const again = drawn(SECTION, { comments })
    assert.equal(again.options.length, 2)
    assert.deepEqual(again.buttons.map(([label]) => label), ['Cancel', 'Send to Agent', 'Comment Ctrl+Enter'])
  } finally {
    ui.answering.clear()
  }
  // An answer that leaves decision 2 open keeps the form open under its line.
  const partly = drawn(SECTION, { comments: [{ ...comments[0], author: { login: 'agent' },
    answers: { anchor: 'issue-body', picks: ['1B'] } }] })
  assert.deepEqual(partly.answered, ['agent answered 1B · 5m ago'])
  assert.equal(partly.options.length, 2)
})

test('a conversation\'s forms post through its box\'s rules: Send to Agent where the box has it, Comment always', () => {
  const reload = () => {}
  assert.equal(answeringOf('issue', { number: 4, state: 'open', comments: [] }, 'game', reload).send, true)
  assert.equal(answeringOf('issue', { number: 4, state: 'completed', comments: [] }, 'game', reload).send, false)
  assert.equal(answeringOf('pr', { number: 6, state: 'open', comments: [] }, 'game', reload).send, true)
  assert.equal(answeringOf('pr', { number: 6, state: 'merged', comments: [] }, 'game', reload).send, false)
})

test('a form\'s post goes to its item, to the agent for Send to Agent, and empties its own draft and no other',
  async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const real = globalThis.fetch
  const parser = (globalThis as { DOMParser?: unknown }).DOMParser
  const sent: { path: string; body: Record<string, unknown> }[] = []
  globalThis.fetch = (async (path: string, init: { body: string }) => {
    sent.push({ path, body: JSON.parse(init.body) })
    return { ok: true, json: async () => ({ comment: { id: 9, author: you, at: '', html: '' }, said: 'Sent.' }) }
  }) as unknown as typeof fetch
  Object.assign(globalThis, { DOMParser: class {
    parseFromString = (markup: string) => ({ querySelectorAll: () => [],
      body: { querySelectorAll: () => [], innerHTML: markup } })
  } })
  try {
    ui.drafts.set('game:4', 'The bottom box.')
    ui.drafts.set('game:4#issue-body', draft({ note: 'Yes.' }))
    const issue = answeringOf('issue', { number: 4, state: 'open', comments: [] }, 'game', () => {})
    assert.equal(await issue.post('game:4#issue-body', 'Answers…', true), true)
    assert.deepEqual(sent.at(-1), { path: '/api/comment',
      body: { desk: 'game', number: 4, text: 'Answers…', toAgent: true } })
    assert.equal(ui.drafts.has('game:4#issue-body'), false)
    assert.equal(ui.drafts.get('game:4'), 'The bottom box.')
    ui.drafts.set('game:6', 'The review\'s box.')
    const review = answeringOf('pr', { number: 6, state: 'open', comments: [] }, 'game', () => {})
    assert.equal(await review.post('game:6#review-body', 'Answers…', true), true)
    assert.deepEqual(sent.at(-1), { path: '/api/review',
      body: { desk: 'game', number: 6, action: 'send', text: 'Answers…' } })
    assert.equal(ui.drafts.get('game:6'), 'The review\'s box.')
    assert.equal(await review.post('game:6#review-body', 'Answers…', false), true)
    assert.deepEqual(sent.at(-1)?.path, '/api/comment')
  } finally {
    globalThis.fetch = real
    Object.assign(globalThis, { DOMParser: parser })
    ui.drafts.clear()
    ui.toasts = []
  }
})
