// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { answerOf, bodyParts } from './decisions.ts'
import type { BodyPart, DecisionSection } from './decisions.ts'
import { renderMarkdown } from './markdown.ts'

const href = (number: number) => `#game:${number}`
const parts = (text: string, tasks = false) => bodyParts(text, 'issue-body', 'the description', href, undefined,
  { tasks })

/** The one section among `found`, failing when there is none. */
function sectionOf(found: BodyPart[] | null, index = 0): DecisionSection {
  const sections = (found ?? []).flatMap(part => 'section' in part ? [part.section] : [])
  assert.ok(sections[index], 'the body has the section')
  return sections[index]
}

const NUMBERED = `Some context.

## Decisions Needed

### 1. Should the banner pause combat?

The banner shows for two seconds after a unit levels up, mid-turn.

- **A. Pause combat** while it shows: the player reads it, and every fight runs two seconds longer.
- **B. Overlay it** ✅ *my pick*: combat stays fast, and the banner fades over the board.

### 2. Which color should it use, as in #4?

See \`## Suggested Design\`.

## Notes

- Keep it short.`

test('a numbered section reads into decisions with their lettered options and the agent\'s pick', () => {
  const found = parts(NUMBERED)
  assert.equal(found?.length, 3)
  assert.deepEqual(found?.[0], { html: '<p>Some context.</p>\n' })
  assert.deepEqual(found?.[2], { html: '<h2>Notes</h2>\n<ul>\n<li>Keep it short.</li>\n</ul>\n' })
  const section = sectionOf(found)
  assert.equal(section.anchor, 'issue-body')
  assert.equal(section.place, 'the description')
  assert.equal(section.heading, '<h2>Decisions Needed</h2>\n')
  assert.equal(section.intro, '')
  assert.deepEqual(section.decisions.map(one => [one.number, one.question]),
    [[1, 'Should the banner pause combat?'], [2, 'Which color should it use, as in #4?']])
  const [first, second] = section.decisions
  assert.equal(first.head, '<h3>1. Should the banner pause combat?</h3>\n'
    + '<p>The banner shows for two seconds after a unit levels up, mid-turn.</p>\n')
  assert.deepEqual(first.options.map(one => [one.letter, one.label, one.pick]),
    [['A', 'Pause combat', false], ['B', 'Overlay it', true]])
  assert.equal(first.options[1].html,
    '<strong>B. Overlay it</strong> ✅ <em>my pick</em>: combat stays fast, and the banner fades over the board.')
  assert.equal(first.after, '')
  // A decision without options keeps everything under its heading in `head`, its reference linked.
  assert.deepEqual(second.options, [])
  assert.match(second.head, /<a class="issue-link" href="#game:4">#4<\/a>/)
})

test('a section in the older layout keeps its text whole, with no decisions', () => {
  const section = sectionOf(parts('## Decisions Needed\n\n- Should it pause? I\'d overlay it.\n- Which color?'))
  assert.deepEqual(section.decisions, [])
  assert.equal(section.intro, '<ul>\n<li>Should it pause? I&#39;d overlay it.</li>\n<li>Which color?</li>\n</ul>\n')
})

test('a body without the section has no parts, and one inside a code block or under another heading level isn\'t one',
  () => {
  assert.equal(parts('Just text.\n\n## Notes\n\nMore.'), null)
  assert.equal(parts('```\n## Decisions Needed\n```'), null)
  assert.equal(parts('### Decisions Needed\n\nA third-level heading.'), null)
})

test('a list that isn\'t all lettered options stays in the decision\'s text, and text after the options follows them',
  () => {
  const section = sectionOf(parts('## Decisions Needed\n\n### 3. Which?\n\n- a plain bullet\n\nThe options:\n\n'
    + '- **A. One**: first\n- **B. Two**: second\n\nTrailing words.'))
  const [decision] = section.decisions
  assert.equal(decision.number, 3)
  assert.match(decision.head, /a plain bullet/)
  assert.deepEqual(decision.options.map(one => one.letter), ['A', 'B'])
  assert.equal(decision.after, '<p>Trailing words.</p>\n')
})

test('a section ends at agent-desk\'s signature, so the form sits above it', () => {
  const found = parts('## Decisions Needed\n\n- Migrate old saves?\n\n<sub>Posted by agent-desk on runner-1</sub>\n'
    + '<!-- agent-desk -->')
  assert.equal(found?.length, 2)
  assert.doesNotMatch(sectionOf(found).intro, /Posted by/)
  assert.match(JSON.stringify(found?.[1]), /<sub>Posted by agent-desk on runner-1<\/sub>/)
})

test('a second section in one body takes its own anchor', () => {
  const found = parts('## Decisions Needed\n\n- First?\n\n## Decisions Needed\n\n- Second?')
  assert.equal(sectionOf(found, 0).anchor, 'issue-body')
  assert.equal(sectionOf(found, 1).anchor, 'issue-body-2')
})

test('the pieces number task boxes as the whole body does, and keep the sanitizer\'s guarantees', () => {
  const text = '- [ ] before\n\n## Decisions Needed\n\n### 1. Which?\n\n- **A. Safe** <script>alert(1)</script>'
    + ' <img src=x onerror=alert(1)>: fine\n\nThen:\n\n- [x] after the options\n\n## Done\n\n- [ ] last'
  const found = parts(text, true)
  const whole = renderMarkdown(text, href, undefined, { tasks: true })
  const html = JSON.stringify(found)
  assert.match(html, /data-task=\\"0\\"/)
  assert.match(html, /data-task=\\"2\\"/)
  assert.equal((whole.match(/data-task/g) ?? []).length, (html.match(/data-task/g) ?? []).length)
  assert.doesNotMatch(html, /<script|onerror/)
  assert.equal(sectionOf(found).decisions[0]?.options[0]?.label, 'Safe')
})

test('an answer comment names the section it answers by its anchor, and each decision it answers', () => {
  assert.deepEqual(answerOf('Answers to the Decisions Needed in [the description](#issue-body):\n\n'
    + '- **1B.** Overlay it\n- **2.** Keep the old color for now.\n\nThanks.'),
  { anchor: 'issue-body', picks: ['1B', '2'] })
  // The store puts @agent in front of a comment sent to the agent.
  assert.deepEqual(answerOf('@agent Answers to the Decisions Needed in [the comment of 2026-11-13T18:05:39Z]'
    + '(#comment-42):\n\nGo with the overlay.'), { anchor: 'comment-42', picks: [] })
  assert.equal(answerOf('I read the Answers to the Decisions Needed in [the description](#issue-body):'), null)
  assert.equal(answerOf('Answers to the Decisions Needed in [elsewhere](https://example.com/#x):'), null)
})
