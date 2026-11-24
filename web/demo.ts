// A demo desk for screenshots: three made-up projects with cards in every section, in a folder of their own, so a
// server started on it shows no real project's issues. `just demo` builds one in a new temporary folder and serves it;
// by hand:
//   node web/demo.ts <folder>   builds the demo in <folder>, which must be empty or absent, and prints its folders
// Then start the server with XDG_CONFIG_HOME=<folder>/config, XDG_STATE_HOME=<folder>/state, and
// AGENT_DESK_TRANSCRIPTS=<folder>/transcripts, where Orchard #2's agent session keeps its transcript.
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, utimesSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { storeFixture } from './store-fixture.ts'
import { codexLogFolder } from './agent-log.ts'

/** One made-up project: its owner/repo, the title its config gives, and its chores. */
export type DemoProject = { repo: string; title: string; chores: string[] }

export const DEMO_PROJECTS: DemoProject[] = [
  { repo: 'demo/orchard', title: 'Orchard', chores: ['audit', 'refactor'] },
  { repo: 'demo/lighthouse', title: 'Lighthouse', chores: ['add-tests'] },
  { repo: 'demo/kiln', title: 'Kiln', chores: [] },
]

// Each demo chore's skill, which the demo writes in its project's `.claude/skills`, where Claude Code finds it ahead of
// the user's own, so a chore's page shows made-up text. Orchard's audit also has the profile its skill reads.
const DEMO_SKILLS: Record<string, string> = {
  audit: `---
name: audit
description: Audit one area of the project for real problems, such as bugs, stale code, or missing tests, and file each
  as an issue labelled \`audit\`.
---

# Audit

Read \`.agent-skills/audit/PROFILE.md\` first: it names what counts as harm here and which paths matter most.

## Steps

1. Pick the area the last audit covered least, from the profile's **Source** list.
2. Read it end to end, with its tests, and note each problem that loses work or misleads a user.
3. Check each note against the code once more, then file it with \`/create-issue\`.

Skip style nits and anything an open issue already covers.
`,
  refactor: `---
name: refactor
description: Find one behavior-preserving refactor in the project, make it, and open a review.
---

# Refactor

1. Look for a long function, a repeated block, or a name that misleads.
2. Change one file, or a few that move together, and keep every test passing.
3. Open a review that says what moved and why the behavior stays the same.

\`\`\`bash
just test
\`\`\`
`,
  'add-tests': `---
name: add-tests
description: Find a source file with no tests that deserves them, write the tests, and open a review.
---

# Add Tests

List the source files without a test beside them, rank them by how much a bug there would cost, and test the first.
`,
}

// The chores table `agent-desk chores status` saves for each project with chores, which the Chores section draws.
const DEMO_CHORE_TABLES: Record<string, string> = {
  'demo/orchard': `Chore          Every  Last run                     Next
audit          7d     done Mon Nov 16 09:12            Mon Nov 23 09:12
refactor       7d     failure Wed Nov 18 14:40         now
`,
  'demo/lighthouse': `Chore          Every  Last run                     Next
add-tests      7d     running for 12 min           after this run
`,
}

// The profiles the demo's projects keep for their chores, by project and chore.
const DEMO_PROFILES: Record<string, Record<string, string>> = {
  'demo/orchard': { audit: `# Audit Profile

## Harm

A finding must lose a grower's harvest log, show a wrong picking date, or slow the season planner. A lost log
outranks a wrong date, which outranks a slow page.

## Source

The planner in \`src/\`, its API in \`api/\`, and the import scripts in \`tools/\`.
` },
}

// What the demo's store holds, written by lib/store.py: `checkouts[repo]` names each project's checkout. Each project
// carries cards in the board's sections: a run working, a review waiting on you, queued and blocked issues, one on
// hold, a decision, the backlog, an idea, a merged review, an agent that timed out and stopped, and a failed run on an
// issue with a review. Orchard's start order is out of its sorted order, and its review sent back to its agent waits in
// Queued ahead of the queued issues.
const SEED = `
o, l, k = "demo/orchard", "demo/lighthouse", "demo/kiln"
for repo in (o, l, k):
    store.project(repo, checkouts[repo])

store.create(o, "issue", "Prune the apple rows before the first frost",
             "## Goal\\n\\nEach apple row gets pruned once a season.\\n\\n## Done When\\n\\n- [ ] Rows 1 to 6 pruned",
             labels=["enhancement"])
store.create(o, "issue", "Count the pear harvest by tree", labels=["enhancement", "gpu", "in-progress"])
store.comment(o, 2, "Started on the tally sheet.")
import os
claude = store.request_run(o, "chore", "audit")
store.run_started(claude, f"agent-desk-chore-demo-orchard-audit-{claude}.service")
store.run_posting_identity(claude, "claude-opus-4-6", "anthropic", "0.2.4")
os.environ["AGENT_DESK_RUN"] = str(claude)
cherries = store.create(o, "issue", "Net the cherry trees against birds", "## Decisions Needed\\n\\n### 1. Which netting?\\n\\n"
             "- **A. Fine mesh**: keeps out every bird.\\n- **B. Wide mesh** ✅ *my pick*: cheaper.",
             labels=["needs-design"])
os.environ.pop("AGENT_DESK_RUN")
store.run_ended(claude, "succeeded", 0)
codex = store.request_run(o, "agent", str(cherries))
store.run_started(codex, f"agent-desk-agent-demo-orchard-{cherries}-{codex}.service", account="codex")
store.run_posting_identity(codex, "gpt-6.2-sol", "openai", "0.2.4")
os.environ["AGENT_DESK_RUN"] = str(codex)
store.comment(o, cherries, "Wide mesh is available in rolls long enough to cover each row in one piece.\\n\\n"
              "<sub>Posted by agent-desk</sub>\\n<!-- agent-desk -->")
os.environ.pop("AGENT_DESK_RUN")
store.run_ended(codex, "succeeded", 0)
store.create(o, "issue", "Fix the gate latch that swings open", labels=["bug", "agent-ready"])
store.create(o, "issue", "Replace the irrigation timer", labels=["agent-ready"], blocked_by=[4])
store.create(o, "issue", "Plant a row of plums", labels=["idea"])
store.create(o, "review", "Count the pear harvest by tree", branch="agent/issue-2", head_sha="a1" * 20, draft=True,
             fixes=[2])
store.create(o, "issue", "Paint the barn doors", labels=["enhancement"])
store.create(o, "review", "Paint the barn doors", branch="agent/issue-8", head_sha="b2" * 20, fixes=[8])
# Its check passed merged with an older main, and two merges landed on main since.
store.branch_moved(o, "agent/issue-8", "b2" * 20, None)
store.merge_tried(o, "agent/issue-8", "b2" * 20, "main", mains[o][-1], "MERGEABLE")
check = store.check_queue(o, "agent/issue-8", "b2" * 20)
store.check_merges(check, "main", mains[o][0])
store.check_ended(check, "passed")
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
# Orchard's start order, as set by hand, holds a blocked issue ahead of its blocker and a serial pair's second issue
# ahead of its first, so Queued's Sort has an order to change.
store.create(o, "issue", "Sharpen the pruning saws", labels=["agent-ready", "serial:shed"])
store.create(o, "issue", "Oil the shed's tool rack", labels=["agent-ready", "serial:shed"])
store.move(o, 5, "top")
store.move(o, 13, "top")
# A ready review sent back to its agent, whose follow-up leads Queued ahead of the queued issues.
staked = store.create(o, "issue", "Stake the young walnut saplings", labels=["enhancement", "in-progress"])
review = store.create(o, "review", "Stake the young walnut saplings", branch=f"agent/issue-{staked}",
                      head_sha="b8" * 20, fixes=[staked])
store.comment(o, review, "Use two stakes per sapling on the windward rows.", to_agent=True)
store.request_run(o, "agent", str(staked), False, {"EVENT": "issue_comment", "REPLY_PR": str(review)})
run = store.request_run(o, "agent", "2")
store.run_started(run, "agent-desk-agent-demo-orchard-2-1.service")
call = store.gpu_asked(run, checkouts[o], False, 3000, held_by="demo-lighthouse")
store.gpu_got(call, 3240)
store.gpu_released(call, 3960)
# More open issues and three in the backlog, so Open and Backlog show their moves between cards and a card dragged
# past another.
store.create(o, "issue", "Rake the windfall apples under row 3", labels=["enhancement"])
store.create(o, "issue", "Build a shed for the cider press", labels=["enhancement", "backlog"])
store.create(o, "issue", "Graft a russet branch onto the old tree", labels=["backlog"])
store.create(o, "issue", "Mulch around the young pear trees", labels=["enhancement"])
store.create(o, "issue", "Trial a hedge of quince along the lane", labels=["backlog"])

store.create(l, "issue", "Swap the lamp for an LED", labels=["enhancement", "agent-ready"])
store.create(l, "issue", "Log each ship that passes at night", labels=["enhancement", "agent-ready"])
store.create(l, "issue", "Paint the tower's red stripe", labels=["needs-design"])
store.create(l, "issue", "Oil the stair rail", labels=["bug"])
store.create(l, "review", "Swap the lamp for an LED", branch="agent/issue-1", head_sha="e5" * 20, fixes=[1])
# Its check passed, and its re-check after main moved failed.
check = store.check_queue(l, "agent/issue-1", "e5" * 20)
store.check_merges(check, "main", "9b" * 20)
store.check_ended(check, "passed")
check = store.check_queue(l, "agent/issue-1", "e5" * 20, "main", "01840f3c" * 5, recheck=True)
store.check_ended(check, "failed")
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
# A follow-up run on the review's issue failed, so its card names the review.
run = store.request_run(k, "agent", "3")
store.run_started(run, "agent-desk-agent-demo-kiln-3-4.service")
store.run_ended(run, "failed", exit_code=1)
store.create(k, "issue", "Slow the ramp for thick pots", labels=["enhancement", "in-progress"])
store.comment(k, 5, "An agent started on \`agent/issue-5\`.")
store.create(k, "review", "Slow the ramp for thick pots", branch="agent/issue-5", head_sha="a7" * 20, draft=True,
             fixes=[5])
store.comment(k, 5, "The agent finished: https://example.test/6 (draft).")
check = store.check_queue(k, "agent/issue-5", "a7" * 20)
store.check_ended(check, "failed", name="merge")
store.create(k, "issue", "Restack the kiln shelves for the long firing", labels=["enhancement", "on-hold"])
# An agent whose test run sits in a deadlock, so the watch in lib/run_stall.py flagged its run as stalled.
logged = store.create(k, "issue", "Log the kiln's temperature every minute", labels=["enhancement", "in-progress"])
store.comment(k, logged, f"An agent started on \`agent/issue-{logged}\`.")
run = store.request_run(k, "agent", str(logged))
store.run_started(run, f"agent-desk-agent-demo-kiln-{logged}-{run}.service")
stalls = Path(sys.argv[1]).parent / "stalls"
stalls.mkdir(exist_ok=True)
(stalls / f"{run}.json").write_text(json.dumps({"since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 840)),
                                                "tool": "Bash", "summary": "pytest tests/test_logger.py -x"}))
# Enough queued issues that Kiln's Queued runs past the bottom of the window, so a dragged card scrolls to its place.
for title in ("Label the glaze buckets by cone", "Sweep the clay dust from the wedging table",
              "Check the thermocouple against a witness cone", "Reorder the stilts and kiln wash",
              "Log each firing's peak temperature", "Patch the crack in the kiln lid",
              "Sort the reclaim clay by body", "Mount a fan over the glaze station"):
    store.create(k, "issue", title, labels=["enhancement", "agent-ready"])
`

// The session of the agent working Orchard #2, whose Agent Log tab shows its transcript with the lines a call's result
// logs, and the beats and the crash lib/agent_run.py logged while the agent waited on a long test run and a bench.
export const DEMO_SESSION = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f'
const CODEX_SESSION = '2a65f1c0-7f54-4b5f-ac09-c27361699f89'

/**
 * Writes the demo session's transcript under `transcripts`, in the folder Claude Code names after the worktree's path,
 * and its beats file under `state`, timed so the session's last line comes two minutes before `now`.
 */
function writeSession(transcripts: string, state: string, worktree: string, now: number): void {
  const at = (minutes: number) => new Date(now - (60 - minutes) * 60_000).toISOString()
  const said = (minutes: number, text: string) => ({ type: 'assistant', timestamp: at(minutes),
    message: { content: [{ type: 'text', text }] } })
  const ran = (minutes: number, id: string, name: string, input: Record<string, string>) => ({ type: 'assistant',
    timestamp: at(minutes), message: { content: [{ type: 'tool_use', id, name, input }] } })
  const got = (minutes: number, id: string, content = 'ok', isError = false) => ({ type: 'user',
    timestamp: at(minutes), message: { content: [{ type: 'tool_result', tool_use_id: id, content,
      is_error: isError }] } })
  const events = [
    said(0, 'I\'ll read the issue and its comments first.'),
    ran(0.2, 't1', 'Bash', { command: 'agent-desk issue view 2 --comments', description: 'Read the issue' }),
    got(0.21, 't1'),
    ran(1, 't2', 'Read', { file_path: '/srv/orchard-issue-2/tally/pears.py' }),
    got(1.01, 't2'),
    ran(4, 't3', 'Edit', { file_path: '/srv/orchard-issue-2/tally/pears.py' }),
    got(4.01, 't3'),
    ran(4.5, 't4', 'Bash', { command: 'just lint', description: 'Lint the tally' }),
    got(4.8, 't4', 'Exit code 1\nerror: line too long (124 > 120) at tally/pears.py:44', true),
    ran(5, 't5', 'Edit', { file_path: '/srv/orchard-issue-2/tally/pears.py' }),
    got(5.01, 't5'),
    ran(6, 't6', 'Bash', { command: 'just test', description: 'Run every suite' }),
    got(22, 't6'),
    said(22, 'The tests pass. Committing the tally by tree.'),
    ran(22.5, 't7', 'Bash',
      { command: 'git commit -F - <<\'EOF\'\nCount the pear harvest by tree\nEOF\ngit log -1 --stat' }),
    got(22.51, 't7'),
    ran(23, 't8', 'Bash', { command: 'just bench harvest', description: 'Time the harvest tally' }),
    got(57.5, 't8'),
    said(58, 'The bench holds under its budget, so I\'ll open the review.'),
  ]
  const transcript = join(transcripts, worktree.replace(/[^a-zA-Z0-9]/g, '-'))
  mkdirSync(transcript, { recursive: true })
  writeFileSync(join(transcript, `${DEMO_SESSION}.jsonl`), events.map(event => `${JSON.stringify(event)}\n`).join(''))
  const line = (type: string) => (minutes: number, text: string) => JSON.stringify({ type, timestamp: at(minutes),
    text })
  const beat = line('beat')
  const beats = [
    beat(11, 'Still in Bash for 5 min: Run every suite · just test · busy: pytest (96% CPU), node --test (12%)'),
    beat(16, 'Still in Bash for 10 min: Run every suite · just test · busy: pytest (98% CPU), node --test (4%)'),
    beat(21, 'Still in Bash for 15 min: Run every suite · just test · busy: pytest (64% CPU)'),
    ...[28, 33, 38, 43, 48, 53].map(minutes => beat(minutes, `Still in Bash for ${minutes - 23} min: Time the harvest `
      + 'tally · just bench … · background: python -m tally.sample … · busy: python -m tally.bench (99% CPU), '
      + 'python -m tally.sample (31%), claude -p ×2 (6%)')),
    line('crash')(31.5, 'A process under the run crashed: tally-native (SIGSEGV)'),
  ]
  mkdirSync(join(state, 'agent-desk', 'beats'), { recursive: true })
  writeFileSync(join(state, 'agent-desk', 'beats', DEMO_SESSION), beats.map(line => `${line}\n`).join(''))
}

function writeCodexSession(state: string, worktree: string, now: number): void {
  const folder = codexLogFolder(join(state, 'agent-desk'), worktree)
  const path = join(folder, `${CODEX_SESSION}.jsonl`)
  const events = [
    { type: 'system', subtype: 'init', model: 'Codex', session_id: CODEX_SESSION },
    { type: 'assistant', message: { content: [{ type: 'text', text: 'I’ll check the paint samples before opening the review.' }] } },
    { type: 'assistant', message: { content: [{ type: 'tool_use', id: 't1', name: 'Shell',
      input: { command: 'just test paint' } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't1', content: 'All paint tests passed.' }] } },
    { type: 'assistant', message: { content: [{ type: 'text', text: 'The paint tests pass. I’ll open the draft review.' }] } },
  ]
  mkdirSync(folder, { recursive: true })
  writeFileSync(path, events.map((event, index) => JSON.stringify({
    ...event, timestamp: new Date(now - (5 - index) * 60_000).toISOString(),
  })).join('\n') + '\n')
  utimesSync(path, now / 1000, now / 1000)
}

/**
 * Builds the demo in `root`, an empty or absent folder: a projects file under `root/config`, a checkout for each
 * project under `root/projects` with its `.agent-desk.json` and its chores' skills and profiles, a store under
 * `root/state` holding the made-up cards, each project's chores table, a hold.json that lowers Orchard's agent and
 * gpu slots, puts Lighthouse in Manual, and sets a weekly usage limit and an API budget, subscription readings and the
 * Chat's spend on the API key, and a backup.json whose push failed, and Orchard #2's agent session under
 * `root/transcripts`. Returns the folders to start the server with.
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
  const mains: Record<string, string[]> = {}
  for (const { repo, title, chores } of DEMO_PROJECTS) {
    const checkout = join(at, 'projects', repo.split('/')[1] ?? repo)
    mkdirSync(checkout, { recursive: true })
    // agent-desk board finds its project from the checkout git names, which the projects file lists.
    execFileSync('git', ['init', '--quiet', '--initial-branch', 'main', checkout])
    writeFileSync(join(checkout, '.agent-desk.json'), `${JSON.stringify({ title, host: 'github', test: '',
      chores: { every: Object.fromEntries(chores.map(chore => [chore, 7])) } }, null, 2)}\n`)
    for (const chore of chores) {
      mkdirSync(join(checkout, '.claude', 'skills', chore), { recursive: true })
      writeFileSync(join(checkout, '.claude', 'skills', chore, 'SKILL.md'), DEMO_SKILLS[chore] ?? '')
    }
    for (const [chore, text] of Object.entries(DEMO_PROFILES[repo] ?? {})) {
      mkdirSync(join(checkout, '.agent-skills', chore), { recursive: true })
      writeFileSync(join(checkout, '.agent-skills', chore, 'PROFILE.md'), text)
    }
    checkouts[repo] = checkout
    // Orchard's main takes three commits, so a review's check of the first reads two merges behind.
    mains[repo] = repo === 'demo/orchard' ? ['start', 'Merge one', 'Merge two'].map(message => {
      execFileSync('git', ['-C', checkout, '-c', 'user.name=demo', '-c', 'user.email=demo@localhost', '-c',
        'commit.gpgsign=false', 'commit', '--quiet', '--allow-empty', '-m', message])
      return execFileSync('git', ['-C', checkout, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
    }) : []
  }
  mkdirSync(join(config, 'agent-desk'), { recursive: true })
  writeFileSync(join(config, 'agent-desk', 'projects'),
    DEMO_PROJECTS.map(({ repo }) => `${repo} ${checkouts[repo]}\n`).join(''))
  mkdirSync(join(state, 'agent-desk'), { recursive: true, mode: 0o700 })
  for (const [repo, table] of Object.entries(DEMO_CHORE_TABLES)) {
    const folder = join(state, 'agent-desk', 'chores', repo.replace('/', '__'))
    mkdirSync(folder, { recursive: true })
    writeFileSync(join(folder, 'status.txt'), table)
  }
  storeFixture(`import json, time\nfrom pathlib import Path\nnow = ${now / 1000}\n`
    + `checkouts = ${JSON.stringify(checkouts)}\nmains = ${JSON.stringify(mains)}\n${SEED}`,
    join(state, 'agent-desk', 'desk.sqlite'))
  writeSession(transcripts, state, `${checkouts['demo/orchard']}-issue-2`, now)
  writeCodexSession(state, `${checkouts['demo/orchard']}-issue-8`, now)
  // Orchard runs two agent slots of the three its config gives, and one gpu slot, which #2 works, as `agent-desk slots
  // demo/orchard agent=2 gpu=1` sets, so the Runners sidebar shows both overrides and their Resets. Lighthouse runs in
  // Manual, so its queued issues wait for Start.
  // The header's account control bills the subscription, with a weekly usage limit at 80% and an API budget of $200,
  // and no API key, so Set Key saves a made-up one in the demo's own config folder.
  writeFileSync(join(state, 'agent-desk', 'hold.json'), `${JSON.stringify({ state: 'running', since: null,
    projects: { 'demo/lighthouse': 'manual' }, slots: { 'demo/orchard': { agent: 2, gpu: 1 } },
    issueRunners: { 'demo/orchard': 'codex' },
    usageLimit: { on: true, percent: 80 }, apiBudget: { on: true, dollars: 200 } })}\n`)
  // The subscription used 41% of its week and 36% of its five hours. The API key spent $63.20 on two Chat turns, as
  // lib/usage.py and lib/budget.py record them.
  const seconds = Math.floor(now / 1000)
  writeFileSync(join(state, 'agent-desk', 'usage.json'), `${JSON.stringify({ percent: 41.3,
    resets_at: new Date(now + 3 * 86_400_000).toISOString(), read_at: seconds, tried_at: seconds, error: null,
    five_hour: { percent: 36, resets_at: new Date(now + 7_200_000).toISOString(), read_at: seconds } })}\n`)
  const bin = join(at, 'bin')
  mkdirSync(bin)
  const limits = { rateLimits: { limitId: 'codex', primary: {
    usedPercent: 24, windowDurationMins: 300, resetsAt: seconds + 3600 }, secondary: {
    usedPercent: 73, windowDurationMins: 10080, resetsAt: seconds + 4 * 86400 } } }
  writeFileSync(join(bin, 'codex'), `#!/usr/bin/env node
const { createInterface } = require('node:readline')
if (process.argv[2] !== 'app-server') process.exit(1)
const replies = { initialize: {}, 'account/read': { account: { type: 'chatgpt' } },
  'account/rateLimits/read': ${JSON.stringify(limits)} }
createInterface({ input: process.stdin }).on('line', line => {
  const { id, method } = JSON.parse(line)
  if (id !== undefined) console.log(JSON.stringify({ id, result: replies[method] }))
})
`, { mode: 0o755 })
  const turns = [{ at: seconds - 60, session: 'demo-chat', account: 'api', reported: 40, cost: 40 },
    { at: seconds - 30, session: 'demo-chat', account: 'api', reported: 63.2, cost: 23.2 }]
  writeFileSync(join(state, 'agent-desk', 'chat-spend.jsonl'), turns.map(turn => `${JSON.stringify(turn)}\n`).join(''))
  // The store's last backup committed but couldn't push, so the GitHub sidebar shows a failing row and the header's
  // GitHub button counts it.
  writeFileSync(join(state, 'agent-desk', 'backup.json'), `${JSON.stringify({ at: seconds,
    remote: 'git@github.com:demo/desk-backup.git', waiting: true, committedAt: seconds - 600,
    pushedAt: seconds - 5400, error: 'the push to git@github.com:demo/desk-backup.git failed: Could not resolve '
      + "hostname github.com: Temporary failure in name resolution" })}\n`)
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
