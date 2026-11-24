// A chore's text for the panel a Chores row opens: what its skill says it does, the skill's Markdown, and the
// project's profile for it. lib/chore_skills.py finds the skill as `agent-desk chores status` does, so the page and the
// warnings agree on which file a chore runs.
import { readFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join } from 'node:path'

import { markdownOf } from './review.ts'
import { HERE, host } from './state.ts'
import type { Desk } from './state.ts'

const CHORE_SKILLS = join(HERE, '..', 'lib', 'chore_skills.py')
// A skill's name, bare or as `<plugin>:<name>`, which names no folder above its own.
const SKILL_NAME = /^(?:[\w.-]+:)?(?!\.+$)[\w.-]+$/

/**
 * A chore's skill and profile, as HTML: `description` from the skill's front matter, `html` the rest of the skill, and
 * `path` where it lives; or `warning` in their place when Claude Code finds no skill by that name. `profile` and
 * `profilePath` hold the checkout's `.agent-skills/<name>/PROFILE.md` when it has one.
 */
export type ChoreText = { name: string; description: string; html: string; path: string; warning: string | null;
  profile: string | null; profilePath: string | null }

/** A skill file's `description` from its YAML front matter, and the text after the front matter. */
export function splitSkill(text: string): { description: string; body: string } {
  const normal = text.replace(/\r\n?/g, '\n').replace(/^﻿/, '')
  const front = /^---\n([\s\S]*?)\n---[ \t]*(?:\n|$)/.exec(normal)
  if (!front) {
    return { description: '', body: normal }
  }
  return { description: descriptionOf(front[1] ?? ''), body: normal.slice(front[0].length) }
}

/**
 * The `description` value of front matter, on its own line or folded over the indented lines after it, as `>` and `|`
 * blocks and plain multi-line values give it, with its quotes taken off.
 */
function descriptionOf(yaml: string): string {
  const lines = yaml.split('\n')
  const at = lines.findIndex(line => line.startsWith('description:'))
  if (at < 0) {
    return ''
  }
  const first = (lines[at] ?? '').slice('description:'.length).trim()
  const more: string[] = []
  for (const line of lines.slice(at + 1)) {
    if (line.trim() !== '' && !/^\s/.test(line)) {
      break
    }
    more.push(line.trim())
  }
  const text = [/^[>|][+-]?$/.test(first) ? '' : first, ...more].filter(Boolean).join(' ')
  if (text.length >= 2 && text.startsWith('"') && text.endsWith('"')) {
    try {
      return JSON.parse(text) as string
    } catch {
      return text.slice(1, -1)
    }
  }
  return text.length >= 2 && text.startsWith("'") && text.endsWith("'") ? text.slice(1, -1).replaceAll("''", "'")
    : text
}

/** A path with the user's home folder written as `~`. */
export function shortPath(path: string, home = homedir()): string {
  return path === home || path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path
}

/**
 * The text of chore `name` in a project, or null when the project's `chores.every` doesn't list it or it isn't a
 * skill's name, so the server reads no other skill and no path a request names.
 */
export async function choreTextOf(desk: Desk, name: string): Promise<ChoreText | null> {
  if (!desk.chores.includes(name) || !SKILL_NAME.test(name)) {
    return null
  }
  const out = await host.run(['python3', '-B', CHORE_SKILLS, '--file', desk.main, name], desk.main)
  let found: { path?: string; warning?: string } = {}
  try {
    found = JSON.parse(out ?? '') as typeof found
  } catch {
    found = { warning: `agent-desk couldn't look up the ${name} skill.` }
  }
  const skill = found.path ? await readFile(found.path, 'utf8').catch(() => null) : null
  const { description, body } = splitSkill(skill ?? '')
  const bare = name.slice(name.lastIndexOf(':') + 1)
  const profilePath = join('.agent-skills', bare, 'PROFILE.md')
  const profile = await readFile(join(desk.main, profilePath), 'utf8').catch(() => null)
  return { name, description: description ? markdownOf(desk, description) : '',
    html: skill === null ? '' : markdownOf(desk, body), path: found.path ? shortPath(found.path) : '',
    warning: skill !== null ? null : found.warning ?? `agent-desk couldn't read ${found.path ?? 'the skill'}.`,
    profile: profile === null ? null : markdownOf(desk, profile), profilePath: profile === null ? null : profilePath }
}
