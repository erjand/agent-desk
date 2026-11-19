// The store and host the board's tests build boards from, shared by board.test.ts and queued.test.ts.
import type { Host, StoreBoard, StoreIssue, StoreReview } from './board.ts'
import { DESK } from './run.ts'

export const NOW = Date.parse('2026-10-29T12:00:00Z')
export const BOARD_READ = `exec "${DESK}" board --json`

/** What `agent-desk board --json` prints for a project holding `parts`, and nothing else. */
export function storeOf(parts: Partial<StoreBoard>): StoreBoard {
  return { repo: 'org/game', issues: [], reviews: [], merged: [], runs: [], ...parts }
}

/** An open issue in the store, titled `Issue <n>`, in workflow state `status`. */
export function issue(number: number, status = 'open', extra: Partial<StoreIssue> = {}): StoreIssue {
  return { number, title: `Issue ${number}`, status, labels: [], createdAt: '2026-10-29T09:00:00Z',
    updatedAt: '2026-10-29T10:00:00Z', blockedBy: [], ...(status === 'in-progress' ? { comments: [], landed: null } : {}),
    ...extra }
}

/** An open review in the store, titled `PR <n>`, from the branch of the issue numbered one below it. */
export function review(number: number, extra: Partial<StoreReview> = {}): StoreReview {
  return { number, title: `PR ${number}`, branch: `agent/issue-${number - 1}`, base: 'main', head: `h${number}`,
    draft: false, createdAt: '2026-10-29T10:00:00Z', checks: [], mergeable: 'MERGEABLE', ...extra }
}

/**
 * A host whose store holds `store`, read anew for each board, and whose other commands print what `other` gives, or
 * nothing. `asked` collects each command it ran.
 */
export function storeHost(store: StoreBoard | (() => StoreBoard), other: (argv: string[]) => string | null = () => '',
  now = NOW): Host & { asked: string[][] } {
  const asked: string[][] = []
  return {
    asked,
    run: async argv => {
      asked.push(argv)
      if (argv[0] === 'bash' && argv[2] === BOARD_READ) {
        return JSON.stringify(typeof store === 'function' ? store() : store)
      }
      return other(argv)
    },
    now: async () => now,
    get: async () => undefined,
    set: async () => undefined,
  }
}
