/** What a row's buttons can do. */
export type RowAction = 'open' | 'merge' | 'ready' | 'resume' | 'log' | 'retry' | 'dismiss' | 'fix' | 'close'
  | 'start' | 'restart' | 'designed' | 'to-open' | 'to-backlog' | 'unblock' | 'undraft' | 'cancel' | 'up' | 'down'
  | 'top' | 'unpick' | 'pause' | 'unpause'

/** One line of the inbox: an issue, a PR, or a run. */
export type Row = {
  key: string
  text: string
  number?: number
  /** The web page's address for the row's issue or review, which Open and pressing the row open. */
  url?: string
  resume?: string
  /** The command that prints a failed dispatcher run's log, `journalctl --user -u <unit>`, which Log copies. */
  log?: string
  /** How long ago it happened, or for how long it has run, drawn right-justified in the buttons' column. */
  time?: string
  /** Drawn dimmed, as for an issue parked with on-hold or behind an open blocker. */
  dim?: boolean
  /** The issue an agent PR's branch works, which the row shows in place of the PR's own number. */
  issue?: number
  /** Whether number is a PR's, which a row without an issue shows as PR <n>. */
  pr?: boolean
  /** The failed run a row stands for, which Dismiss hides. */
  run?: number
  /** Whether Retry carries on the failed run's own Claude Code session, as on an agent's timed-out run on an issue. */
  continues?: boolean
  /**
   * A stopped issue's newest failed or cancelled agent run, which Continue reruns; without one, Continue starts a fresh
   * run.
   */
  rerun?: number
  /** The waiting run request a Queued row stands for, which Cancel ends. */
  request?: number
  /** In Manual or on Hold, the issue whose fresh start Start picks, since its request may not exist yet. */
  pick?: number
  /** In Manual or on Hold, the requests the dispatcher holds for Start that Start picks, by run id. */
  starts?: number[]
  /**
   * In Manual or on Hold, the note a picked queued or waiting issue's row reads once Unpick drops its pick, empty for
   * none, which the row shows at once.
   */
  unpicked?: string
  actions: RowAction[]
  // The parts `text` joins, which the web page draws apart; the pane draws `text`.
  /** What the row is about, without its state or note: a title, or what a runner works on. */
  title?: string
  /**
   * Where a Needs You row stands: review, conflicts, draft, stopped, failed, or merged. A runner row's: working,
   * elsewhere, busy, idle, or offline. A Merging row's: stopped, for a PR the merge queue stopped on uncommitted
   * changes to land.
   */
  state?: string
  /** What `text` adds in parentheses, such as `waiting on #285`, `back to its PR`, or `serial`. */
  note?: string
  /** Whether a draft row's head conflicts with its base, which Fix sends its agent back to resolve. */
  conflicts?: boolean
  /** A busy row's kind of run, such as `agent` or `check`, or `local` for a session on this machine. */
  runner?: string
  /** When it began, in ISO 8601: a PR's opening, a run's start, an issue's last change, or a merge. */
  since?: string
  /** The labels on the row's open issue, by name. */
  labels?: string[]
  /** How many sub-issues the row's open issue has, and how many of them are closed, when it has any. */
  subs?: { total: number; done: number }
  /**
   * Marks a row about the project rather than an item, such as main ahead of origin. The web page shows it among its
   * header's notices, in this tone, and opens nothing; the pane lists it in its section.
   */
  notice?: 'muted' | 'warning'
}

/** One chore in agent-desk chores status: its name, days between runs, last run, and when it runs next. */
export type Chore = {
  name: string
  every: string
  last: string
  next: string
}

/** How the person laid out the pane: the sections they collapsed, and where each paged section's window starts. */
export type SectionView = {
  collapsed: string[]
  offsets: Record<string, number>
}

/** The machine's memory in GiB and its CPU use in percent, which the header shows beside the refresh time. */
export type SystemLoad = {
  ramUsed: number
  ramTotal: number
  swapUsed: number
  swapTotal: number
  /** Across every core since the last sample; null for the first, which has nothing to compare with. */
  cpu: number | null
}

/** Everything the pane draws, from one refresh. */
export type Board = {
  /** When the refresh ran, as HH:MM on this machine. */
  at: string
  /** One per busy run: its kind and what it works on, with Open for one on an issue or PR. */
  runners: Row[]
  /**
   * Each kind of the project's dispatcher slots, with how many work now out of how many it has, which the web page's
   * Runners sidebar names. A board read before the field existed has none.
   */
  slots?: { kind: string; busy: number; total: number }[]
  /**
   * One per branch check running now, which the web page's Runners sidebar shows beside the runs and the pane leaves
   * out.
   */
  checks?: Row[]
  needs: Row[]
  /** The PRs in the merge queue, in the order they merge, each with what it waits on. */
  merging: Row[]
  /** Issues stopped with needs-design, waiting on the person's decision. */
  design: Row[]
  /** How many issues need a decision, drawn or not. */
  designTotal: number
  queued: Row[]
  next: Row[]
  /** Issues on hold, blocked by an open issue, or waiting on a stacked PR's base. */
  blocked: Row[]
  /** Issues labelled backlog, planned for later and not queued. */
  backlog: Row[]
  /** Issues labelled idea, not yet planned into work. */
  ideas: Row[]
  merged: Row[]
  chores: string[]
  /** The chores lines' columns, past the header; the web page draws them as a table. */
  choreList?: Chore[]
  /**
   * Each place the desk touches GitHub and how it last went, as notice rows: main's push and the agent branches' for
   * a project that pushes to a host, then the backup's, which every project's board repeats. A board stored before
   * the group existed has none until the next refresh.
   */
  github?: Row[]
  error: string | null
}

declare module 'claude-code' {
  interface PluginState {
    'agent-desk': {
      board: Shaped<Board | null>
      known: string[] | null
      confirm: number | null
      note: string | null
      view: SectionView | null
      refreshing: boolean | null
      load: SystemLoad | null
    }
  }
}
