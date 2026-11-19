// An issue's or a review's Edit and task boxes on the page: the title and Markdown description its Edit box holds,
// which GET /api/source reads, and the writes its Save, its Labels picker, and a tick post to POST /api/edit and
// POST /api/task, through the store as `you`.
import { LABEL_RULE, plainLabel } from './issue-create.ts'
import { toggledTask } from './markdown.ts'
import { bodyVersion, MAX_COMMENT, MAX_TITLE, writeStore } from './review.ts'
import type { Refusal } from './review.ts'
import type { Desk } from './state.ts'
import { storedItem, storedLabels } from './store.ts'
import type { StoredItem } from './store.ts'

// The labels the store keeps as an issue's workflow state in place of a label, which the Labels picker never sets or
// takes off: agent-ready and in-progress, which hand an issue to an agent and mark its run, and the states the card's
// buttons and runs move it through. A label the store keeps as a state never shows among an item's labels, so the
// picker could never show it pressed.
const STATE_LABELS = new Set(['agent-ready', 'in-progress', 'waiting', 'needs-design', 'on-hold'])


/**
 * Ticks or unticks task item `task`, counted from 0, in issue `number`'s description, through the store as `you`, and
 * returns the description's new version; why not when there is no such issue or item, when the description changed
 * since the page read it at `version`, or when the store refuses the edit.
 */
export async function tickTask(desk: Desk, number: number, task: number, checked: boolean, version: string):
  Promise<{ version: string } | Refusal> {
  const issue = editable(desk, number, ['issue'], version)
  if ('error' in issue) {
    return issue
  }
  const body = toggledTask(issue.body, task, checked)
  if (body === null) {
    return { status: 409, error: `#${number}'s description has no task ${task + 1}` }
  }
  return writeEdit(desk, issue, { body })
}

/** An issue's or a review's title and description as the store keeps them, with the description's version. */
export type Source = { number: number; title: string; body: string; version: string }

/** Item `number`'s title and Markdown description, which the page's Edit puts in its box; null for no such item. */
export function sourceOf(desk: Desk, number: number): Source | null {
  const item = storedItem(desk.repo, number)
  return item === null ? null : { number, title: item.title, body: item.body, version: bodyVersion(item.body) }
}

/**
 * Writes a new title or description, as the page's Edit sends one in `sent`, to issue or review `number` through the
 * store as `you`, and returns the description's version after it. A title loses its line breaks and the spaces around
 * it, and a description replaces the one the page read at `sent.version`. Labels the Labels picker adds or removes,
 * as `sent.addLabels` and `sent.removeLabels`, go through editLabels. Why not when there is no such item, when the
 * title is empty or longer than MAX_TITLE, when the description is longer than MAX_COMMENT or changed since, or when
 * the store refuses the edit.
 */
export async function editItem(desk: Desk, number: number, sent: { title?: unknown; body?: unknown;
  version?: unknown; addLabels?: unknown; removeLabels?: unknown }):
  Promise<{ version: string } | { labels: string[] } | Refusal> {
  const labels = sent.addLabels !== undefined || sent.removeLabels !== undefined
  if (labels && sent.title === undefined && sent.body === undefined) {
    return editLabels(desk, number, sent.addLabels ?? [], sent.removeLabels ?? [])
  }
  if (typeof sent.title === 'string' && sent.body === undefined && !labels) {
    const title = sent.title.replace(/\s+/g, ' ').trim()
    if (title === '') {
      return { status: 400, error: 'a title can\'t be empty' }
    }
    if (title.length > MAX_TITLE) {
      return { status: 400, error: `a title holds at most ${MAX_TITLE} characters` }
    }
    const item = editable(desk, number, ['issue', 'review'])
    return 'error' in item ? item : writeEdit(desk, item, { title })
  }
  if (labels || typeof sent.body !== 'string' || sent.title !== undefined || typeof sent.version !== 'string') {
    return { status: 400, error: MALFORMED }
  }
  if (sent.body.length > MAX_COMMENT) {
    return { status: 400, error: `a description holds at most ${MAX_COMMENT} characters` }
  }
  const item = editable(desk, number, ['issue', 'review'], sent.version)
  return 'error' in item ? item : writeEdit(desk, item, { body: sent.body })
}

// Why an edit that names none of the things Edit and the Labels picker change, or more than one, does nothing.
const MALFORMED = 'an edit names a new title, a new description with the version it replaces, or labels to add or'
  + ' remove'

/**
 * Adds the labels `add` names to issue or review `number` and takes off those `remove` names, through the store as
 * `you`, and returns the item's labels after. A label the item carries already, or one it lacks, writes nothing. A
 * label the store keeps as a workflow state, as STATE_LABELS names them, may change the card's group, so the page
 * refuses it, as it does a name plainLabel refuses or a label named both ways.
 */
async function editLabels(desk: Desk, number: number, add: unknown, remove: unknown):
  Promise<{ labels: string[] } | Refusal> {
  if (!Array.isArray(add) || !Array.isArray(remove) || add.length + remove.length === 0) {
    return { status: 400, error: MALFORMED }
  }
  const names = [...add, ...remove]
  if (!names.every(plainLabel)) {
    return { status: 400, error: LABEL_RULE }
  }
  const state = names.find(name => STATE_LABELS.has(name))
  if (state !== undefined) {
    return { status: 400, error: `the page leaves ${state} to the card's buttons and the agent's runs, since the`
      + ' store keeps it as the issue\'s state' }
  }
  if (add.some(name => remove.includes(name))) {
    return { status: 400, error: 'a label is added or removed, not both' }
  }
  const item = editable(desk, number, ['issue', 'review'])
  if ('error' in item) {
    return item
  }
  const had = new Set(storedLabels(desk.repo, number))
  // `--add-label=` keeps a name that starts with a dash from reading as an option.
  const flags = [...new Set(add as string[])].filter(name => !had.has(name)).map(name => `--add-label=${name}`)
    .concat([...new Set(remove as string[])].filter(name => had.has(name)).map(name => `--remove-label=${name}`))
  if (flags.length > 0
    && await writeStore(desk, [item.kind, 'edit', String(number), '--repo', desk.repo, ...flags], '') === null) {
    return { status: 502, error: 'the store refused the edit' }
  }
  return { labels: storedLabels(desk.repo, number) }
}

/**
 * Item `number` when it is one of `kinds` and, given `version`, its description is still the one the page read at
 * that version; why not otherwise.
 */
function editable(desk: Desk, number: number, kinds: StoredItem['kind'][], version?: string): StoredItem | Refusal {
  const item = Number.isInteger(number) && number > 0 ? storedItem(desk.repo, number) : null
  if (item === null || !kinds.includes(item.kind)) {
    return { status: 404, error: `no such ${kinds.join(' or ')}` }
  }
  if (version !== undefined && bodyVersion(item.body) !== version) {
    return { status: 409, error: `#${number}'s description changed since the page read it` }
  }
  return item
}

/**
 * Writes `change`, a new title or description, to `item` through the store as `you`, and returns the description's
 * version after it; a change that leaves the item as it was writes nothing.
 */
async function writeEdit(desk: Desk, item: StoredItem, change: { title: string } | { body: string }):
  Promise<{ version: string } | Refusal> {
  const body = 'body' in change ? change.body : item.body
  if ('title' in change ? change.title === item.title : body === item.body) {
    return { version: bodyVersion(body) }
  }
  // `--title=` keeps a title that starts with a dash from reading as an option.
  const flags = 'title' in change ? [`--title=${change.title}`] : ['--body-file', '-']
  if (await writeStore(desk, [item.kind, 'edit', String(item.number), '--repo', desk.repo, ...flags], body) === null) {
    return { status: 502, error: 'the store refused the edit' }
  }
  return { version: bodyVersion(body) }
}
