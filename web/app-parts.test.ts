// Run with: node --test web/
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { authorName, Avatar, avatarOf, Chip, chipWidth, hueOf, iconOf, Label, labelNamed, labelOf, LabelPicker,
  offeredLabels, openPicture, PostingFooter, Reaction, STATE_LABELS, tagsOf, viewedPicture } from './app-parts.js'
import { ui } from './app-state.js'

afterEach(() => {
  ui.server = null
})

test("a chore's card waits for attention whatever its stage", () => {
  assert.deepEqual(iconOf({ chore: true, stage: 'needs', state: 'failed' }), ['waiting', 'attention'])
  assert.deepEqual(iconOf({ chore: true, stage: 'working' }), ['waiting', 'attention'])
})

test("a card that needs the person shows its PR's or run's state", () => {
  assert.deepEqual(iconOf({ stage: 'needs', state: 'review' }), ['pr', 'open'])
  assert.deepEqual(iconOf({ stage: 'needs', state: 'conflicts' }), ['pr', 'danger'])
  assert.deepEqual(iconOf({ stage: 'needs', state: 'failing' }), ['pr', 'danger'])
  assert.deepEqual(iconOf({ stage: 'needs', state: 'draft' }), ['pr', 'muted'])
  assert.deepEqual(iconOf({ stage: 'needs', state: 'stopped' }), ['stopped', 'attention'])
  assert.deepEqual(iconOf({ stage: 'needs', state: 'timed out' }), ['stopped', 'attention'])
  assert.deepEqual(iconOf({ stage: 'needs', state: 'failed' }), ['failed', 'danger'])
  assert.deepEqual(iconOf({ stage: 'needs', state: 'merged' }), ['closed', 'done'])
})

test('a card that needs the person in a state the page does not know shows an open issue', () => {
  assert.deepEqual(iconOf({ stage: 'needs', state: 'something new' }), ['issue', 'open'])
  assert.deepEqual(iconOf({ stage: 'needs' }), ['issue', 'open'])
})

test("every other card shows its stage, and an unknown stage an open issue", () => {
  assert.deepEqual(iconOf({ stage: 'working' }), ['working', 'runners'])
  assert.deepEqual(iconOf({ stage: 'merging' }), ['pr', 'merging'])
  assert.deepEqual(iconOf({ stage: 'merging', state: 'stopped' }), ['stopped', 'attention'])
  assert.deepEqual(iconOf({ stage: 'queued' }), ['waiting', 'attention'])
  assert.deepEqual(iconOf({ stage: 'design' }), ['issue', 'design'])
  assert.deepEqual(iconOf({ stage: 'blocked' }), ['blocked', 'muted'])
  assert.deepEqual(iconOf({ stage: 'open' }), ['issue', 'open'])
  assert.deepEqual(iconOf({ stage: 'backlog' }), ['backlog', 'backlog'])
  assert.deepEqual(iconOf({ stage: 'idea' }), ['idea', 'ideas'])
  assert.deepEqual(iconOf({ stage: 'merged' }), ['merged', 'done'])
  assert.deepEqual(iconOf({ stage: 'elsewhere' }), ['issue', 'open'])
})

test("a label takes its project's color, or GitHub's gray when the project doesn't list it", () => {
  ui.server = { desks: [{ id: 'game', labels: { bug: 'd73a4a' } }, { id: 'tools' }] }
  assert.deepEqual(labelOf('game', 'bug'), { name: 'bug', color: 'd73a4a' })
  assert.deepEqual(labelOf('game', 'idea'), { name: 'idea', color: 'ededed' })
  assert.deepEqual(labelOf('tools', 'bug'), { name: 'bug', color: 'ededed' })
  assert.deepEqual(labelOf('gone', 'bug'), { name: 'bug', color: 'ededed' })
})

test('a label hands its color on as red, green, and blue and as hue, saturation, and lightness', () => {
  const color = (hex: string) => {
    const { style } = Label({ label: { name: 'x', color: hex } }).props
    return [style['--label-r'], style['--label-g'], style['--label-b'],
      style['--label-h'], style['--label-s'], style['--label-l']]
  }
  // Red, green, and blue each lead one of these, which takes a different branch of the hue.
  assert.deepEqual(color('d73a4a'), [215, 58, 74, 354, 66, 54])
  assert.deepEqual(color('0e8a16'), [14, 138, 22, 124, 82, 30])
  assert.deepEqual(color('0075ca'), [0, 117, 202, 205, 100, 40])
  // A gray has no hue or saturation, only lightness.
  assert.deepEqual(color('ededed'), [237, 237, 237, 0, 0, 93])
  assert.deepEqual(color('000000'), [0, 0, 0, 0, 0, 0])
  assert.deepEqual(color('ffffff'), [255, 255, 255, 0, 0, 100])
})

test('a label shows its name', () => {
  assert.equal(Label({ label: { name: 'agent-ready', color: '0e8a16' } }).props.children, 'agent-ready')
})

test('a note names a label when the project has one by that name, with dashes for spaces', () => {
  ui.server = { desks: [{ id: 'game', labels: { 'on-hold': 'fbca04', serial: 'ededed' } }] }
  assert.equal(labelNamed('game', 'on hold'), 'on-hold')
  assert.equal(labelNamed('game', 'serial'), 'serial')
  assert.equal(labelNamed('game', 'checks failed'), null)
  assert.equal(labelNamed('game', undefined), null)
})

test("a card's tags leave off the flow labels, show a note that names a label as that label, and the rest as text", () => {
  ui.server = { desks: [{ id: 'game', labels: { bug: 'd73a4a', 'on-hold': 'fbca04' } }] }
  const names = (tags: { props: { label?: { name: string } }; key: string }[]) =>
    tags.filter(Boolean).map(tag => tag.props.label?.name ?? `note:${tag.key}`)
  assert.deepEqual(names(tagsOf('game', ['bug', 'in-progress', 'agent-ready'], 'on hold')), ['bug', 'on-hold'])
  assert.deepEqual(names(tagsOf('game', ['bug'], 'checks failed')), ['bug', 'note:note'])
  assert.deepEqual(names(tagsOf('game', ['bug'], 'checks failed', { noteApart: true })), ['bug'])
  assert.deepEqual(names(tagsOf('game', ['on-hold'], 'on hold')), ['on-hold'])
  assert.deepEqual(names(tagsOf('game', ['bug', 'backlog', 'idea'])), ['bug'])
})

test("each of six projects takes its own hue by its place on the page, and a seventh repeats the first's", () => {
  const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g']
  ui.server = { desks: ids.map(id => ({ id, title: id.toUpperCase() })) }
  assert.deepEqual(ids.map(hueOf), [0, 1, 2, 3, 4, 5, 0])
  assert.equal(hueOf('gone'), -1)
})

test("a project's chip takes its hue, and one the page doesn't list a plain chip", () => {
  ui.server = { desks: ['a', 'b', 'c', 'd', 'e'].map(id => ({ id, title: id.toUpperCase() })) }
  assert.equal(Chip({ desk: 'e' }).props.class, 'chip project-4')
  assert.equal(Chip({ desk: 'e' }).props.children, 'E')
  assert.equal(Chip({ desk: 'gone' }).props.class, 'chip')
})

test("the project column fits the longest project's title and the chip's border", () => {
  ui.server = { desks: [{ id: 'game', title: 'Game' }, { id: 'tools', title: 'Toolchain' }] }
  assert.equal(chipWidth(), 12)
  ui.server = { desks: [] }
  assert.equal(chipWidth(), 3)
})

/** A click on a body, its target inside a link of `linkClass` around a picture, or outside any link. */
function clickOn(linkClass: string | null, buttons: Record<string, unknown> = {}) {
  const picture = { getAttribute: (name: string) => ({ src: '/api/asset/0f', alt: 'Board' })[name] ?? null }
  const link = { querySelector: (selector: string) => selector === 'img' ? picture : null }
  const target = {
    closest: (selector: string) => linkClass !== null && selector === `a.${linkClass}` ? link : null,
  }
  let prevented = false
  const event = {
    target, button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, defaultPrevented: false,
    preventDefault: () => { prevented = true }, ...buttons,
  }
  return { event, link, prevented: () => prevented }
}

test('a plain click on a wrapped picture opens it in the viewer in place of following its link', () => {
  const { event, link, prevented } = clickOn('picture-link')
  const shown: unknown[] = []
  openPicture(event, (picture: unknown) => shown.push(picture))
  assert.deepEqual(shown, [{ link, src: '/api/asset/0f', alt: 'Board' }])
  assert.ok(prevented())
})

test('a click with a modifier key or another button leaves a wrapped picture to the browser', () => {
  for (const buttons of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 },
    { defaultPrevented: true }]) {
    const { event, prevented } = clickOn('picture-link', buttons)
    const shown: unknown[] = []
    openPicture(event, (picture: unknown) => shown.push(picture))
    assert.deepEqual(shown, [], JSON.stringify(buttons))
    assert.ok(!prevented(), JSON.stringify(buttons))
  }
})

test("a click on a picture inside an author's link, or outside any picture, opens no viewer", () => {
  assert.equal(viewedPicture(clickOn('other').event), null)
  assert.equal(viewedPicture(clickOn(null).event), null)
  assert.equal(viewedPicture({ ...clickOn(null).event, target: null }), null)
})

test('a comment the agent picked up shows 👀 naming its run, and any other comment shows no reaction', () => {
  const eyes = Reaction({ run: 157 }).props.children
  assert.equal(eyes.props.children, '👀')
  assert.equal(eyes.props.title, 'The agent picked this up in run 157.')
  assert.equal(eyes.props['aria-label'], 'The agent picked this up in run 157.')
  assert.equal(Reaction({ run: undefined }), null)
})

test("an avatar shows the first letter or digit of its author's login, uppercased", () => {
  assert.deepEqual(['you', 'agent', 'desk', 'ghost', '42-bot', '_x', '--'].map(login => avatarOf(login).initial),
    ['Y', 'A', 'D', 'G', '4', 'X', '?'])
})

test('a login takes the same avatar color every time, and you, agent, and desk each take their own', () => {
  assert.equal(avatarOf('erjand').hue, avatarOf('erjand').hue)
  const hues = ['you', 'agent', 'desk'].map(login => avatarOf(login).hue)
  assert.equal(new Set(hues).size, 3)
  for (const hue of hues) {
    assert.ok(Number.isInteger(hue) && hue >= 0 && hue < 8, String(hue))
  }
})

test("an avatar draws its initial at its size in its login's color, hidden from screen readers", () => {
  const avatar = Avatar({ person: { login: 'agent' }, size: 24 })
  assert.equal(avatar.props.class, `avatar avatar-${avatarOf('agent').hue}`)
  assert.equal(avatar.props['aria-hidden'], 'true')
  assert.deepEqual(avatar.props.style, { width: '24px', height: '24px', fontSize: '11px' })
  assert.equal(avatar.props.children, 'A')
})

test('a label picker offers the project\'s labels, picked and typed ones too, and never Agent-Ready', () => {
  const labels = { bug: 'd73a4a', 'agent-ready': '0e8a16', 'needs-design': 'fbca04' }
  assert.deepEqual(offeredLabels(labels, []), ['bug', 'needs-design'])
  assert.deepEqual(offeredLabels(labels, ['web', 'agent-ready', 'bug']), ['bug', 'needs-design', 'web'])
  assert.deepEqual(offeredLabels(undefined, ['in-progress']), [])
  // The detail view's picker leaves out every label the store keeps as an issue's state.
  assert.deepEqual(offeredLabels({ ...labels, waiting: 'ededed', 'on-hold': 'ededed' },
    ['in-progress', 'web'], STATE_LABELS), ['bug', 'web'])
})

type Node = { type: unknown; props: Record<string, unknown> & { children?: unknown } }

/** The elements of a drawn tree that `hit` picks. */
function picked(node: unknown, hit: (one: Node) => boolean): Node[] {
  if (Array.isArray(node)) {
    return node.flatMap(one => picked(one, hit))
  }
  if (node === null || typeof node !== 'object') {
    return []
  }
  const one = node as Node
  return [...hit(one) ? [one] : [], ...picked(one.props.children, hit)]
}

test('recorded providers use their bundled marks and the footer names the model and version', () => {
  for (const [provider, model] of [['anthropic', 'claude-opus-4-6'], ['openai', 'gpt-6.2-sol']]) {
    const person = { login: 'agent', posting: { provider, model, version: '0.2.4' } }
    assert.equal(authorName(person), model)
    const image = picked(Avatar({ person, size: 24 }), node => node.type === 'img')[0]
    assert.equal(image.props.src, `/${provider}.svg`)
    const footer = picked(PostingFooter({ person }), node => node.type === 'small')[0]
    assert.equal(footer.props.children, `Posted by ${model} with agent-desk v0.2.4`)
  }
  assert.equal(PostingFooter({ person: { login: 'agent' } }), null)
  const local = { login: 'agent', posting: { model: 'qwen3', version: '0.2.4' } }
  assert.equal(Avatar({ person: local }).props.children, 'Q')
})

test('a label picker draws each offered label in its color, pressed when picked, and presses call back by name', () => {
  ui.server = { desks: [{ id: 'game', labels: { bug: 'd73a4a', web: '1d76db', 'agent-ready': '0e8a16' } }] }
  const toggled: string[] = []
  const tree = LabelPicker({ desk: 'game', picked: ['web', 'gone'], typed: '', disabled: false,
    onToggle: (name: string) => toggled.push(name), onType: () => {}, onAdd: () => {} })
  const presses = picked(tree, one => one.props.class === 'label-pick')
  assert.deepEqual(presses.map(one => [(picked(one.props.children, two => two.type === Label)[0]?.props.label as
    { name: string }).name, one.props['aria-pressed']]), [['bug', 'false'], ['gone', 'true'], ['web', 'true']])
  assert.deepEqual(picked(presses[0]?.props.children, two => two.type === Label)[0]?.props.label,
    { name: 'bug', color: 'd73a4a' })
  ;(presses[2]?.props.onClick as () => void)()
  assert.deepEqual(toggled, ['web'])
  // A project without labels says so, and the box still adds one.
  ui.server = { desks: [{ id: 'game', labels: {} }] }
  const empty = LabelPicker({ desk: 'game', picked: [], typed: 'new', disabled: true, onToggle: () => {},
    onType: () => {}, onAdd: () => {} })
  assert.equal(picked(empty, one => one.props.class === 'label-pick').length, 0)
  assert.equal(picked(empty, one => one.props.class === 'muted').length, 1)
  const [box] = picked(empty, one => one.type === 'input')
  assert.deepEqual([box?.props.value, box?.props.disabled], ['new', true])
})
