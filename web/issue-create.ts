// The New Issue form's write and the body it starts from: files an issue in a project's store as `you`, with its
// title, Markdown body, labels, parent, and blockers, as the page's POST /api/issue-create sends them, and reads the
// project's task template for GET /api/issue-template.
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { MAX_COMMENT, MAX_TITLE, runStore } from './review.ts'
import type { Desk } from './state.ts'

// The task template a new issue's body starts from, in the project's main checkout, as the create-issue skill reads it.
const TASK_TEMPLATE = '.github/ISSUE_TEMPLATE/task.md'
// The body a project without a task template starts from: the create-issue skill's sections.
const DEFAULT_TEMPLATE = '## Goal\n\n## Done When\n\n## Notes\n'

// The longest label name an issue or a review may carry, as GitHub caps one.
export const MAX_LABEL = 50
// A label name on one line, without commas, which `--label a,b` would split, and without spaces at either end.
const PLAIN_LABEL = /^(?!\s)[^\p{Cc},]*(?<!\s)$/u

type Refusal = { status: number; error: string }

// Why the page refuses a label name that isn't plain, as plainLabel reads it.
export const LABEL_RULE = `a label is a name on one line, of at most ${MAX_LABEL} characters, without commas`

/** Whether `name` is a label an issue or a review may carry: a plain one-line name of at most MAX_LABEL characters. */
export function plainLabel(name: unknown): name is string {
  return typeof name === 'string' && name !== '' && name.length <= MAX_LABEL && PLAIN_LABEL.test(name)
}

/** Whether `value` is an issue number: a positive integer. */
function issueNumber(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) > 0
}

/**
 * The body a template's Markdown gives a new issue: `text` without its YAML front matter, its HTML comments, or the
 * blank lines they leave, ending in one line break. A template with nothing left gives DEFAULT_TEMPLATE.
 */
function templateBody(text: string): string {
  const body = text.replace(/\r\n?/g, '\n').replace(/^\uFEFF?---\n[\s\S]*?\n---[ \t]*(?:\n|$)/, '')
    .replace(/<!--[\s\S]*?-->/g, '').replace(/^[ \t]+$/gm, '').replace(/\n{3,}/g, '\n\n').trim()
  return body === '' ? DEFAULT_TEMPLATE : `${body}\n`
}

/** The body a new issue in `desk` starts from: its main checkout's TASK_TEMPLATE, or DEFAULT_TEMPLATE without one. */
export async function issueTemplate(desk: Desk): Promise<string> {
  const text = await readFile(join(desk.main, TASK_TEMPLATE), 'utf8').catch(() => null)
  return text === null ? DEFAULT_TEMPLATE : templateBody(text)
}

/**
 * Files a new issue in `desk`'s store through lib/store_cli.py as `you`, with the title, body, and labels the page
 * sent, `agent-ready` too when `agentReady` is true, under the issue `parent` names, and blocked by each issue in
 * `blockedBy`, and returns its number. A title loses its line breaks and the spaces around it. Why not when the title
 * is empty or longer than MAX_TITLE, the body is longer than MAX_COMMENT, a label isn't a plain name, a parent or
 * blocker isn't a positive integer, or the store refuses, as it does a parent or blocker it doesn't hold, in its own
 * words.
 */
export async function createIssue(desk: Desk, sent: { title?: unknown; body?: unknown; labels?: unknown;
  agentReady?: unknown; parent?: unknown; blockedBy?: unknown }): Promise<{ number: number } | Refusal> {
  const title = typeof sent.title === 'string' ? sent.title.replace(/\s+/g, ' ').trim() : ''
  if (title === '') {
    return { status: 400, error: 'a new issue needs a title' }
  }
  if (title.length > MAX_TITLE) {
    return { status: 400, error: `a title holds at most ${MAX_TITLE} characters` }
  }
  const body = sent.body ?? ''
  if (typeof body !== 'string') {
    return { status: 400, error: 'a description is text' }
  }
  if (body.length > MAX_COMMENT) {
    return { status: 400, error: `a description holds at most ${MAX_COMMENT} characters` }
  }
  const labels = sent.labels ?? []
  if (!Array.isArray(labels)) {
    return { status: 400, error: 'labels come as a list of names' }
  }
  if (!labels.every(plainLabel)) {
    return { status: 400, error: LABEL_RULE }
  }
  if (sent.agentReady !== undefined && typeof sent.agentReady !== 'boolean') {
    return { status: 400, error: 'Agent-Ready is on or off' }
  }
  const parent = sent.parent ?? null
  if (parent !== null && !issueNumber(parent)) {
    return { status: 400, error: 'a parent is one issue number' }
  }
  const blockedBy = sent.blockedBy ?? []
  if (!Array.isArray(blockedBy) || !blockedBy.every(issueNumber)) {
    return { status: 400, error: 'Blocked By takes issue numbers' }
  }
  // `--title=` and `--label=` keep a value that starts with a dash from reading as an option.
  const flags = [...new Set([...labels as string[], ...sent.agentReady === true ? ['agent-ready'] : []])]
    .map(label => `--label=${label}`)
  const links = [...parent === null ? [] : [`--parent=${parent}`],
    ...[...new Set(blockedBy as number[])].map(blocker => `--blocked-by=${blocker}`)]
  const done = await runStore(desk, ['issue', 'create', '--repo', desk.repo, `--title=${title}`, '--body-file', '-',
    ...flags, ...links], body)
  if ('error' in done) {
    return { status: 409, error: done.error }
  }
  const number = Number(done.out.trim())
  return issueNumber(number) ? { number } : { status: 502, error: 'the store printed no number for the new issue' }
}
