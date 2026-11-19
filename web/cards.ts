// The web page's cards: one per piece of work, an issue with its PR, runs, and runner, built from the board's rows.
// The board lists an issue once per section it shows in, and an agent's PR apart from its issue; a card joins them
// and takes the stage of the section that asks the most of the person.
import type { Board, Row, RowAction } from '../inbox/types'

/** Where a card's work stands, from the section that showed it. */
type Stage = 'needs' | 'merging' | 'design' | 'working' | 'queued' | 'blocked' | 'open' | 'backlog' | 'idea'
  | 'merged'

export type Card = {
  /**
   * `<project>:<issue>`, or `<project>:pr-<n>` for a PR without an agent issue, or `<project>:run-<n>` for a failed
   * run.
   */
  id: string
  desk: string
  stage: Stage
  /** The issue the card is about, or its PR's number when it has no issue. */
  number?: number
  /** The card's PR, when it has one the board knows. */
  pr?: number
  title: string
  /**
   * The badge: review, conflicts, failing, draft, stopped, failed, or merged for Needs You; a runner's name while
   * working.
   */
  state?: string
  note?: string
  /** What the card opens on GitHub: its PR, its issue, or a failed run's log. */
  url?: string
  /** When it began, in ISO 8601, which orders Needs You and Recently Merged. */
  since?: string
  /** How long its runner has worked, or when it merged. */
  time?: string
  runner?: string
  /** The row the card's buttons act on, and its actions. */
  key: string
  actions: RowAction[]
  resume?: string
  /** Whether Retry carries on the failed run's own session, which names it Continue. */
  continues?: boolean
  /** The command that prints a failed dispatcher run's log, `journalctl --user -u <unit>`. */
  log?: string
  run?: number
  /** The waiting run request a Queued card stands for, as a chore's card without an issue or PR does. */
  request?: number
  /** Its issue's labels, by name. */
  labels?: string[]
  /** How many sub-issues its issue has, and how many are closed. */
  subs?: { total: number; done: number }
}

// The stages by what they ask of the person, first the most: a card in two sections takes the earlier one.
const ORDER: Stage[] = ['needs', 'merging', 'working', 'queued', 'design', 'blocked', 'open', 'backlog', 'idea',
  'merged']

/**
 * A row's card id: its issue's number, its PR's without one, or its run's without either. A failed run keeps a card of
 * its own, so a run that failed after its agent opened a PR leaves the PR's card, and its Merge button, in place.
 */
function cardId(desk: string, row: Row): string {
  if (row.run !== undefined) {
    return `${desk}:run-${row.run}`
  }
  const issue = row.issue ?? (row.pr ? undefined : row.number)
  return issue !== undefined ? `${desk}:${issue}` : row.number !== undefined ? `${desk}:pr-${row.number}`
    : `${desk}:run-${row.run ?? row.key}`
}

function cardOf(desk: string, stage: Stage, row: Row): Card {
  const issue = row.issue ?? (row.pr ? undefined : row.number)
  return {
    id: cardId(desk, row), desk, stage, number: issue ?? row.number, ...(row.pr ? { pr: row.number } : {}),
    title: row.title ?? row.text, key: row.key, actions: row.actions,
    ...Object.fromEntries(Object.entries({ state: row.state, note: row.note, url: row.url, since: row.since,
      time: row.time, resume: row.resume, log: row.log, run: row.run, continues: row.continues, request: row.request,
      labels: row.labels, subs: row.subs })
      .filter(([, value]) => value !== undefined)),
  }
}

/**
 * A project's cards, each piece of work once, in each stage's order: Needs You's longest waiting first, and the rest
 * as the board lists them. A working card names its runner, and takes a PR or a note another section knows.
 */
export function cardsOf(desk: string, board: Board): Card[] {
  const working = board.runners.filter(row => row.number !== undefined && !row.dim && row.state === 'working')
  const sections: [Stage, Row[]][] = [
    ['needs', [...board.needs].sort((a, b) => stamp(a) - stamp(b))], ['merging', board.merging ?? []],
    ['working', working], ['queued', board.queued],
    ['design', board.design], ['blocked', board.blocked], ['open', board.next], ['backlog', board.backlog],
    ['idea', board.ideas],
    ['merged', board.merged],
  ]
  const cards = new Map<string, Card>()
  for (const stage of ORDER) {
    // A notice is about the project, not a piece of work, so the page's header shows it and no card does.
    for (const row of (sections.find(([one]) => one === stage)?.[1] ?? []).filter(one => !one.notice)) {
      const card = cardOf(desk, stage, row)
      const held = cards.get(card.id)
      if (held === undefined) {
        cards.set(card.id, stage === 'working' ? { ...card, state: undefined, runner: row.runner } : card)
      } else if (held.pr === undefined && card.pr !== undefined) {
        held.pr = card.pr
      }
    }
  }
  return [...cards.values()]
}

function stamp(row: Row): number {
  return row.since ? Date.parse(row.since) : Number.POSITIVE_INFINITY
}
