// An issue's Delete Issue on the page: whether the store would delete the issue, which its preview carries so the
// button shows only then, and the delete that POST /api/issue-delete sends, both through `agent-desk issue delete`
// in lib/store_cli.py, which holds the rules for which issues it refuses.
import { runStore } from './review.ts'
import type { Refusal } from './review.ts'
import type { Desk } from './state.ts'
import { storedItem } from './store.ts'

/**
 * Whether the store would delete issue `number`, as `issue delete` without `--yes` says, which reads the store and
 * the project's checkout and changes nothing.
 */
export async function deletable(desk: Desk, number: number): Promise<boolean> {
  return 'out' in await runStore(desk, ['issue', 'delete', String(number), '--repo', desk.repo], '')
}

/**
 * Deletes the issue `sent.number` names through the store as `you`, and returns its number. Why not when it names no
 * issue, or when the store refuses, in the store's words, as it does an issue with work behind it.
 */
export async function deleteIssue(desk: Desk, sent: { number?: unknown }): Promise<{ number: number } | Refusal> {
  const number = sent.number
  if (typeof number !== 'number' || !Number.isInteger(number) || number < 1) {
    return { status: 400, error: 'a delete names one issue number' }
  }
  if (storedItem(desk.repo, number)?.kind !== 'issue') {
    return { status: 404, error: `#${number} is no issue here` }
  }
  const done = await runStore(desk, ['issue', 'delete', String(number), '--repo', desk.repo, '--yes'], '')
  return 'error' in done ? { status: 409, error: done.error } : { number }
}
