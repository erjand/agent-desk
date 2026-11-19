// A demo desk for screenshots: three made-up projects with cards in every section, in a folder of their own, so a
// server started on it shows no real project's issues. `just demo` builds one in a new temporary folder and serves it;
// by hand:
//   node web/demo.ts <folder>   builds the demo in <folder>, which must be empty or absent, and prints its folders
// Then start the server with XDG_CONFIG_HOME=<folder>/config, XDG_STATE_HOME=<folder>/state, and
// AGENT_DESK_TRANSCRIPTS=<folder>/transcripts, where Orchard #2's agent session keeps its transcript.
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { storeFixture } from './store-fixture.ts'

/** One made-up project: its owner/repo, the title its config gives, and its chores. */
export type DemoProject = { repo: string; title: string; chores: string[] }

export const DEMO_PROJECTS: DemoProject[] = [
  { repo: 'demo/orchard', title: 'Orchard', chores: ['audit', 'refactor'] },
  { repo: 'demo/lighthouse', title: 'Lighthouse', chores: ['add-tests'] },
  { repo: 'demo/kiln', title: 'Kiln', chores: [] },
]

// What the demo's store holds, written by lib/store.py: `checkouts[repo]` names each project's checkout. Each project
// carries cards in the board's sections: a run working, a review waiting on you, queued and blocked issues, one on
// hold, a decision, the backlog, an idea, a merged review, and an agent that timed out and stopped.
const SEED = `
o, l, k = "demo/orchard", "demo/lighthouse", "demo/kiln"
for repo in (o, l, k):
    store.project(repo, checkouts[repo])

store.create(o, "issue", "Prune the apple rows before the first frost",
             "## Goal\\n\\nEach apple row gets pruned once a season.\\n\\n## Done When\\n\\n- [ ] Rows 1 to 6 pruned",
             labels=["enhancement"])
store.create(o, "issue", "Count the pear harvest by tree", labels=["enhancement", "in-progress"])
store.comment(o, 2, "Started on the tally sheet.")
store.create(o, "issue", "Net the cherry trees against birds", "## Decisions Needed\\n\\n### 1. Which netting?\\n\\n"
             "- **A. Fine mesh**: keeps out every bird.\\n- **B. Wide mesh** ✅ *my pick*: cheaper.",
             labels=["needs-design"])
store.create(o, "issue", "Fix the gate latch that swings open", labels=["bug", "agent-ready"])
store.create(o, "issue", "Replace the irrigation timer", labels=["agent-ready"], blocked_by=[4])
store.create(o, "issue", "Plant a row of plums", labels=["idea"])
store.create(o, "review", "Count the pear harvest by tree", branch="agent/issue-2", head_sha="a1" * 20, draft=True,
             fixes=[2])
store.create(o, "issue", "Paint the barn doors", labels=["enhancement"])
store.create(o, "review", "Paint the barn doors", branch="agent/issue-8", head_sha="b2" * 20, fixes=[8])
store.create(o, "issue", "Mend the fence by the pond")
store.create(o, "review", "Mend the fence by the pond", branch="agent/issue-10", head_sha="c3" * 20, fixes=[10])
store.merge(o, 11, "d4" * 20)
merged = store.request_run(o, "agent", "10")
store.run_started(merged, "agent-desk-agent-demo-orchard-10-1.service")
store.run_ended(merged, "succeeded", 0)
store.run_result(merged, model="opus", effort="high", minutes=42, cost=3.1, review=11)
for asked, got, released in ((1000, 1000, 1300), (2000, 2540, 2900)):
    call = store.gpu_asked(merged, checkouts[o], False, asked, held_by=None if got == asked else "demo-kiln")
    store.gpu_got(call, got)
    store.gpu_released(call, released)
run = store.request_run(o, "agent", "2")
store.run_started(run, "agent-desk-agent-demo-orchard-2-1.service")
call = store.gpu_asked(run, checkouts[o], False, 3000, held_by="demo-lighthouse")
store.gpu_got(call, 3240)
store.gpu_released(call, 3960)

store.create(l, "issue", "Swap the lamp for an LED", labels=["enhancement", "agent-ready"])
store.create(l, "issue", "Log each ship that passes at night", labels=["enhancement", "agent-ready"])
store.create(l, "issue", "Paint the tower's red stripe", labels=["needs-design"])
store.create(l, "issue", "Oil the stair rail", labels=["bug"])
store.create(l, "review", "Swap the lamp for an LED", branch="agent/issue-1", head_sha="e5" * 20, fixes=[1])
store.create(l, "issue", "Add a fog horn schedule", labels=["idea"])
run = store.request_run(l, "chore", "add-tests")
store.run_started(run, "agent-desk-chore-demo-lighthouse-add-tests-2.service")
store.create(l, "issue", "Wind the gallery clock each dusk", labels=["enhancement", "in-progress"])
run = store.request_run(l, "agent", "7")
store.run_started(run, "agent-desk-agent-demo-lighthouse-7-3.service")
store.comment(l, 7, "An agent started on \`agent/issue-7\`. To take over, run "
              "\`claude --resume 3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f\` in \`/srv/lighthouse-issue-7\`.")
store.run_ended(run, "timed-out", exit_code=124)
store.comment(l, 7, "The agent run was cancelled or timed out.")

store.create(k, "issue", "Hold the firing at cone 6 for ten minutes", labels=["enhancement", "agent-ready"])
store.create(k, "issue", "Glaze test tiles crack in the cool-down", labels=["bug"])
store.create(k, "issue", "Shelve the bisque by size")
store.create(k, "review", "Shelve the bisque by size", branch="agent/issue-3", head_sha="f6" * 20, fixes=[3])
store.create(k, "issue", "Slow the ramp for thick pots", labels=["enhancement", "in-progress"])
store.comment(k, 5, "An agent started on \`agent/issue-5\`.")
store.create(k, "review", "Slow the ramp for thick pots", branch="agent/issue-5", head_sha="a7" * 20, draft=True,
             fixes=[5])
store.comment(k, 5, "The agent finished: https://example.test/6 (draft).")
check = store.check_queue(k, "agent/issue-5", "a7" * 20)
store.check_ended(check, "failed", name="merge")
store.create(k, "issue", "Restack the kiln shelves for the long firing", labels=["enhancement", "on-hold"])
`

// The session of the agent working Orchard #2, whose Agent Log tab shows its transcript with the beats lib/agent_run.py
// logged while the agent waited on a long test run.
export const DEMO_SESSION = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f'

/**
 * Writes the demo session's transcript under `transcripts`, in the folder Claude Code names after the worktree's path,
 * and its beats file under `state`, timed so the session's last line comes two minutes before `now`.
 */
function writeSession(transcripts: string, state: string, worktree: string, now: number): void {
  const at = (minutes: number) => new Date(now - (60 - minutes) * 60_000).toISOString()
  const said = (minutes: number, text: string) => ({ type: 'assistant', timestamp: at(minutes),
    message: { content: [{ type: 'text', text }] } })
  const ran = (minutes: number, name: string, input: Record<string, string>) => ({ type: 'assistant',
    timestamp: at(minutes), message: { content: [{ type: 'tool_use', name, input }] } })
  const events = [
    said(0, 'I\'ll read the issue and its comments first.'),
    ran(0.2, 'Bash', { command: 'agent-desk issue view 2 --comments' }),
    ran(1, 'Read', { file_path: '/srv/orchard-issue-2/tally/pears.py' }),
    ran(4, 'Edit', { file_path: '/srv/orchard-issue-2/tally/pears.py' }),
    ran(6, 'Bash', { command: 'just test' }),
    said(22, 'The tests pass. Committing the tally by tree.'),
    ran(22.5, 'Bash', { command: 'git commit -am "Count the pear harvest by tree"' }),
    ran(23, 'Bash', { command: 'just bench harvest' }),
    said(58, 'The bench holds under its budget, so I\'ll open the review.'),
  ]
  const transcript = join(transcripts, worktree.replace(/[^a-zA-Z0-9]/g, '-'))
  mkdirSync(transcript, { recursive: true })
  writeFileSync(join(transcript, `${DEMO_SESSION}.jsonl`), events.map(event => `${JSON.stringify(event)}\n`).join(''))
  const beat = (minutes: number, text: string) => JSON.stringify({ type: 'beat', timestamp: at(minutes), text })
  const beats = [
    beat(11, 'Still in Bash for 5 min: just test · busy: pytest (96% CPU), node --test (12%)'),
    beat(16, 'Still in Bash for 10 min: just test · busy: pytest (98% CPU), node --test (4%)'),
    beat(21, 'Still in Bash for 15 min: just test · busy: pytest (64% CPU)'),
    ...[28, 33, 38, 43, 48, 53].map(minutes => beat(minutes, `Still in Bash for ${minutes - 23} min: just bench `
      + 'harvest · background: python -m tally.sample … · busy: python -m tally.bench (99% CPU), '
      + 'python -m tally.sample (31%), claude -p ×2 (6%)')),
  ]
  mkdirSync(join(state, 'agent-desk', 'beats'), { recursive: true })
  writeFileSync(join(state, 'agent-desk', 'beats', DEMO_SESSION), beats.map(line => `${line}\n`).join(''))
}

/**
 * Builds the demo in `root`, an empty or absent folder: a projects file under `root/config`, a checkout for each
 * project under `root/projects` with its `.agent-desk.json`, a store under `root/state` holding the made-up cards, and
 * Orchard #2's agent session under `root/transcripts`. Returns the folders to start the server with.
 */
export function buildDemo(root: string, now = Date.now()): { config: string; state: string; transcripts: string } {
  const at = resolve(root)
  if (existsSync(at) && readdirSync(at).length) {
    throw new Error(`${at} isn't empty; the demo builds in an empty or new folder.`)
  }
  const config = join(at, 'config')
  const state = join(at, 'state')
  const transcripts = join(at, 'transcripts')
  const checkouts: Record<string, string> = {}
  for (const { repo, title, chores } of DEMO_PROJECTS) {
    const checkout = join(at, 'projects', repo.split('/')[1] ?? repo)
    mkdirSync(checkout, { recursive: true })
    // agent-desk board finds its project from the checkout git names, which the projects file lists.
    execFileSync('git', ['init', '--quiet', checkout])
    writeFileSync(join(checkout, '.agent-desk.json'), `${JSON.stringify({ title, host: 'github', test: '',
      chores: { every: Object.fromEntries(chores.map(chore => [chore, 7])) } }, null, 2)}\n`)
    checkouts[repo] = checkout
  }
  mkdirSync(join(config, 'agent-desk'), { recursive: true })
  writeFileSync(join(config, 'agent-desk', 'projects'),
    DEMO_PROJECTS.map(({ repo }) => `${repo} ${checkouts[repo]}\n`).join(''))
  mkdirSync(join(state, 'agent-desk'), { recursive: true, mode: 0o700 })
  storeFixture(`checkouts = ${JSON.stringify(checkouts)}\n${SEED}`,
    join(state, 'agent-desk', 'desk.sqlite'))
  writeSession(transcripts, state, `${checkouts['demo/orchard']}-issue-2`, now)
  return { config, state, transcripts }
}

if (import.meta.main) {
  const root = process.argv[2]
  if (!root) {
    console.error('usage: node web/demo.ts <folder>')
    process.exit(2)
  }
  const { config, state, transcripts } = buildDemo(root)
  console.log(`XDG_CONFIG_HOME=${config} XDG_STATE_HOME=${state} AGENT_DESK_TRANSCRIPTS=${transcripts}`)
}
