// What the web page's History column lists: the reviews that merged across a project in a span of time, each with
// what the runs behind it cost. The store gives both, its merged reviews and the results lib/run_log.py records on
// each run; server.ts reads them through store.ts and hands them here. The page sorts and pages the rows itself.
import { gpuUse } from '../inbox/hooks/units.ts'
import { itemLink } from './review.ts'
import type { MergedReview, ReviewCost } from './store.ts'

/** One merged PR. */
export type HistoryRow = {
  /** When it merged, in ISO 8601. */
  at: string
  desk: string
  /** The issue its agent/issue-<n> branch worked, or the PR's own number when no issue stands behind it. */
  number: number
  pr: number
  title: string
  /**
   * What Claude Code reported the tokens of the runs that opened or updated the PR would cost at API list prices, in
   * dollars, from the result event that ends each run's stream; a subscription doesn't bill it, and a run cut off
   * before that event adds nothing. Absent when the store records no run for the review.
   */
  cost?: number
  /** How many recorded runs the cost adds up. */
  runs: number
  /**
   * The minutes those runs' `agent-desk gpu` calls held a GPU slot and queued for one, and both as the GPU column
   * reads them, such as `Held GPU for 12 mins, waited 9 mins for it`; absent when none of them used the GPU.
   */
  gpuHeld?: number
  gpuQueued?: number
  gpu?: string
  /** The PR's link on the page, `#<desk>:<n>`, which shows it in the detail view. */
  url: string
  /** The page's link to the issue its agent/issue-<n> branch worked; absent when no issue stands behind it. */
  issueUrl?: string
}

/**
 * The reviews that merged in [since, until), each under the issue its agent/issue-<n> branch worked, when it has one,
 * with what the runs behind it cost by `costs`.
 */
export function mergedRows(reviews: MergedReview[], desk: string, since: number, until: number,
  costs: Map<number, ReviewCost>): HistoryRow[] {
  return reviews.filter(review => Date.parse(review.mergedAt) >= since && Date.parse(review.mergedAt) < until)
    .map(review => {
      const issue = Number(review.branch.match(/^agent\/issue-(\d+)$/)?.[1] ?? Number.NaN)
      const spent = costs.get(review.number)
      const url = itemLink({ id: desk }, review.number)
      const issueUrl = Number.isNaN(issue) ? undefined : itemLink({ id: desk }, issue)
      return { at: review.mergedAt, desk, number: Number.isNaN(issue) ? review.number : issue, pr: review.number,
        title: review.title, ...(spent ? { cost: spent.cost } : {}), runs: spent?.runs ?? 0, url,
        ...(typeof spent?.gpuHeld === 'number' ? { gpuHeld: spent.gpuHeld, gpuQueued: spent.gpuQueued ?? 0,
          gpu: gpuUse(spent.gpuHeld, spent.gpuQueued) } : {}),
        ...(issueUrl && issue !== review.number ? { issueUrl } : {}) }
    })
}
