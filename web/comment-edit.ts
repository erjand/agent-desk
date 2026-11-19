// A comment's Edit on the page: the Markdown source its box holds, which GET /api/source reads, and the write its Save
// posts to POST /api/comment-edit, through the store as `you`, which lets you edit only a comment you wrote and never
// one sent to the agent.
import { bodyVersion, MAX_COMMENT, runStore } from './review.ts'
import type { Refusal } from './review.ts'
import type { Desk } from './state.ts'
import { storedComment, storedItem } from './store.ts'

/** A comment's Markdown as the store keeps it, with the item it is on and its version. */
type CommentSource = { id: number; number: number; body: string; version: string }

/** Comment `id`'s Markdown, which the page's Edit puts in its box; null when the project holds none by that id. */
export function commentSourceOf(desk: Desk, id: number): CommentSource | null {
  const comment = storedComment(desk.repo, id)
  return comment === null ? null
    : { id, number: comment.number, body: comment.body, version: bodyVersion(comment.body) }
}

/**
 * Writes the new text of comment `sent.id` on item `sent.number`, as the page's Edit sends it, through
 * `lib/store_cli.py <issue|review> comment <n> --edit <id>` as `you`, and returns its version after. The text replaces
 * the one the page read at `sent.version`. Why not when the request is malformed, the text is longer than MAX_COMMENT,
 * the item holds no such comment, the comment changed since, or the store refuses, as it does another writer's
 * comment, one sent to the agent, and an empty text, with the store's reason.
 */
export async function editComment(desk: Desk, sent: { number?: unknown; id?: unknown; body?: unknown;
  version?: unknown }): Promise<{ version: string } | Refusal> {
  const id = Number(sent.id)
  const number = Number(sent.number)
  if (!Number.isInteger(id) || id <= 0 || typeof sent.body !== 'string' || typeof sent.version !== 'string') {
    return { status: 400, error: 'an edit names a comment, its new text, and the version it replaces' }
  }
  if (sent.body.length > MAX_COMMENT) {
    return { status: 400, error: `a comment holds at most ${MAX_COMMENT} characters` }
  }
  const comment = storedComment(desk.repo, id)
  const item = comment === null || comment.number !== number ? null : storedItem(desk.repo, number)
  if (comment === null || item === null) {
    return { status: 404, error: 'no such comment' }
  }
  if (bodyVersion(comment.body) !== sent.version) {
    return { status: 409, error: 'the comment changed since the page read it' }
  }
  if (sent.body === comment.body) {
    return { version: sent.version }
  }
  const done = await runStore(desk, [item.kind, 'comment', String(number), '--repo', desk.repo, '--edit', String(id),
    '--body-file', '-'], sent.body)
  return 'error' in done ? { status: 409, error: done.error } : { version: bodyVersion(sent.body) }
}
