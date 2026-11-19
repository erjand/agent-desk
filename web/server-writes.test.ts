// Run with: node --test web/
// Tests of the routes that write the store through the server: the New Issue form and its template, a comment's Edit,
// a review's buttons and line comments, Move to Open, Unpick, and Close on a queued card, Move to Backlog and back, a
// close as a duplicate, Delete Issue, and the header's weekly usage limit.
// First, so the server's modules read the temporary folders it sets.
import './test-env.ts'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'

import { rebuild } from './server.ts'
import { getFrom, JSON_POST, postTo } from './server-fixture.ts'
import { desks, host } from './state.ts'
import type { Desk } from './state.ts'
import { storeFixture } from './store-fixture.ts'
import { storePath } from './store.ts'
import { TEST_HOME } from './test-env.ts'

test('a review\'s buttons and line comments act on an open review in the store, and a bad or cross-origin post '
  + 'does nothing', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-review-'))
  const git = (...args: string[]) => execFileSync('git', ['-C', main, '-c', 'user.name=Test', '-c',
    'user.email=test@localhost', '-c', 'commit.gpgsign=false', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(main, 'lawn.txt'), 'grass\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Sow')
  const base = git('rev-parse', 'HEAD')
  git('switch', '-q', '-c', 'agent/issue-1')
  writeFileSync(join(main, 'lawn.txt'), 'grass\nmore grass\nstripes\n')
  git('commit', '-q', '-am', 'Grow')
  const head = git('rev-parse', 'HEAD')
  git('switch', '-q', 'main')
  storeFixture(['p = "me/yard"', 'store.create(p, "issue", "Mow the lawn")',
    `store.create(p, "review", "Mow it", branch="agent/issue-1", head_sha="${head}")`,
    'store.create(p, "review", "Old try", branch="agent/issue-1")', 'store.set_state(p, 3, "closed")',
    'store.create(p, "review", "By hand", branch="mine/edge")'].join('\n'), storePath())
  const desk = { id: 'yard', repo: 'me/yard', owner: 'me', title: 'Yard', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('yard', desk)
  const ran: string[][] = []
  let answer: string | null = ''
  const real = host.run
  // git reads the checkout, and every other command answers `answer`.
  host.run = async (argv, cwd, stdin) => {
    if (argv[0] === 'git') {
      return real(argv, cwd, stdin)
    }
    ran.push(argv)
    return argv[2]?.includes('board --json') ? null : answer
  }
  const comments = () => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return db.prepare('SELECT body, path, line, head_sha, to_agent FROM comments WHERE project = ? ORDER BY id')
        .all('me/yard').map(row => ({ ...row }))
    } finally {
      db.close()
    }
  }
  // The commands the actions ran, leaving out the board's reads that follow them.
  const actions = () => ran.filter(argv => argv[0] === 'bash' && !argv[2]?.includes('board --json'))
    .map(argv => argv.slice(2).join(' '))
  try {
    // A line comment keeps its file, its line, and the head the page showed, and waits for Send to agent.
    const line = { desk: 'yard', number: 2, path: 'lawn.txt', line: 3, head, text: 'Mow in stripes.' }
    const posted = await postTo('/api/line-comment', line)
    assert.equal(posted.status, 201)
    assert.deepEqual(comments(), [{ body: 'Mow in stripes.', path: 'lawn.txt', line: 3, head_sha: head,
      to_agent: 0 }])
    for (const bad of [{ line: 0 }, { line: 1.5 }, { path: '' }, { path: 'a\nb' }, { text: ' ' }, { number: 1 },
      { head: '--output=x' }]) {
      assert.notEqual((await postTo('/api/line-comment', { ...line, ...bad })).status, 201, JSON.stringify(bad))
    }
    // A file the diff doesn't change, a head that isn't one of the review's commits, and a closed review take none.
    for (const [bad, error] of [[{ path: 'shed.txt' }, '#2 doesn\'t change shed.txt'],
      [{ head: base }, `${base.slice(0, 7)} isn't a commit of #2; reload it`],
      [{ head: 'f'.repeat(40) }, 'fffffff isn\'t a commit of #2; reload it'],
      [{ number: 3 }, '#3 is closed']] as const) {
      assert.deepEqual(await postTo('/api/line-comment', { ...line, ...bad }), { status: 409, data: { error } })
    }
    assert.equal(comments().length, 1)

    // Send to agent posts the box's text and the unsent line comment as one comment to the agent, once, which the
    // store starts with @agent.
    assert.equal((await postTo('/api/review', { desk: 'yard', number: 2, action: 'send', text: '' })).status, 200)
    const sent = comments().at(-1)
    assert.equal(sent?.to_agent, 1)
    assert.equal(sent?.body, '@agent Please address my line comments on the diff.\n\n**Line comments**\n\n'
      + '- `lawn.txt` line 3:\n\n  > Mow in stripes.')
    assert.deepEqual(await postTo('/api/review', { desk: 'yard', number: 2, action: 'send', text: '' }),
      { status: 400, data: { error: 'write a comment or a line comment to send' } })
    assert.equal((await postTo('/api/review', { desk: 'yard', number: 2, action: 'send', text: 'Go on.' })).status,
      200)
    assert.equal(comments().at(-1)?.body, '@agent Go on.')
    // No agent works a branch outside agent/, so Send to agent there sends nothing and says so.
    assert.deepEqual(await postTo('/api/review', { desk: 'yard', number: 4, action: 'send', text: 'Please.' }),
      { status: 409, data: { error: 'no agent works mine/edge, so nothing was sent; only agent/<slug> branches have '
        + 'one' } })
    assert.equal(comments().length, 3)

    // Ready, Merge, and Close run their agent-desk commands on the review.
    for (const action of ['ready', 'merge', 'close']) {
      assert.equal((await postTo('/api/review', { desk: 'yard', number: 2, action })).status, 200, action)
    }
    assert.deepEqual(actions(), [`exec "\${AGENT_DESK_HOME:+$AGENT_DESK_HOME/bin/}agent-desk" "$@" _ review ready 2`,
      `exec "\${AGENT_DESK_HOME:+$AGENT_DESK_HOME/bin/}agent-desk" merges add 2`,
      `exec "\${AGENT_DESK_HOME:+$AGENT_DESK_HOME/bin/}agent-desk" "$@" _ review close 2`])

    // A command that fails reports why, for the page's toast.
    answer = null
    assert.deepEqual(await postTo('/api/review', { desk: 'yard', number: 2, action: 'ready' }),
      { status: 502, data: { error: 'Couldn\'t mark #2 ready' } })
    answer = ''

    // A closed review, an issue, an unknown review or action, and a post from another site do nothing.
    ran.length = 0
    assert.equal((await postTo('/api/review', { desk: 'yard', number: 3, action: 'merge' })).status, 409)
    assert.equal((await postTo('/api/review', { desk: 'yard', number: 1, action: 'merge' })).status, 404)
    assert.equal((await postTo('/api/review', { desk: 'yard', number: 99, action: 'close' })).status, 404)
    assert.equal((await postTo('/api/review', { desk: 'yard', number: 2, action: 'delete' })).status, 404)
    const crossSite = { ...JSON_POST, origin: 'https://attacker.example' }
    assert.deepEqual(await postTo('/api/review', { desk: 'yard', number: 2, action: 'merge' }, crossSite),
      { status: 403, data: { error: 'cross-origin request' } })
    assert.equal((await postTo('/api/line-comment', line, { ...JSON_POST, 'content-type': 'text/plain' })).status,
      403)
    assert.deepEqual(actions(), [])
    assert.equal(comments().length, 3)
    // The board reads each action set off finish before the stand-in goes.
    await new Promise(resolve => setTimeout(resolve, 50))
  } finally {
    host.run = real
    desks.delete('yard')
    rmSync(main, { recursive: true, force: true })
  }
})

test('the New Issue form files an issue as you with its body, labels, Agent-Ready, parent, and blockers, and refuses '
  + 'a bad one with a reason',
  async () => {
    const main = mkdtempSync(join(tmpdir(), 'agent-desk-porch-'))
    storeFixture('store.create("me/porch", "issue", "Sweep the steps")', storePath())
    const desk = { id: 'porch', repo: 'me/porch', owner: 'me', title: 'Porch', main, chores: [], board: null, at: 0,
      refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
      schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
    desks.set('porch', desk)
    // The board's read after a create answers nothing, which leaves the board as it was.
    const real = host.run
    host.run = async () => null
    const stored = () => {
      const db = new DatabaseSync(storePath(), { readOnly: true })
      try {
        return db.prepare('SELECT number, title, body, author FROM items WHERE project = ? ORDER BY number')
          .all('me/porch').map(row => ({ ...row, labels: db.prepare(
            'SELECT name FROM labels WHERE project = ? AND number = ? ORDER BY name').all('me/porch', row.number)
            .map(one => one.name) }))
      } finally {
        db.close()
      }
    }
    try {
      const issue = { desk: 'porch', title: '  Paint\nthe rail ', body: 'Two coats.\n\n- [ ] Sand it',
        labels: ['paint', '-outside', 'paint'] }
      assert.deepEqual(await postTo('/api/issue-create', issue), { status: 201, data: { number: 2 } })
      // A body and labels may be left out.
      assert.deepEqual(await postTo('/api/issue-create', { desk: 'porch', title: 'Oil the hinge' }),
        { status: 201, data: { number: 3 } })
      assert.deepEqual(stored().slice(1), [
        { number: 2, title: 'Paint the rail', body: 'Two coats.\n\n- [ ] Sand it', author: 'you',
          labels: ['-outside', 'paint'] },
        { number: 3, title: 'Oil the hinge', body: '', author: 'you', labels: [] }])

      // Agent-Ready queues the issue, and Parent and Blocked By link it, a repeated blocker once.
      assert.deepEqual(await postTo('/api/issue-create', { desk: 'porch', title: 'Fix the latch', agentReady: true,
        parent: 1, blockedBy: [2, 3, 2] }), { status: 201, data: { number: 4 } })
      assert.deepEqual(await postTo('/api/issue-create', { desk: 'porch', title: 'Wax the latch', agentReady: false,
        parent: null, blockedBy: [] }), { status: 201, data: { number: 5 } })
      const db = new DatabaseSync(storePath(), { readOnly: true })
      try {
        assert.deepEqual(db.prepare('SELECT number, state FROM items WHERE project = ? AND number >= 4 ORDER BY number')
          .all('me/porch').map(row => ({ ...row })), [{ number: 4, state: 'queued' }, { number: 5, state: 'open' }])
        assert.deepEqual(db.prepare('SELECT from_number, to_number, kind FROM links WHERE project = ?'
          + ' ORDER BY kind, from_number').all('me/porch').map(row => ({ ...row })), [
          { from_number: 2, to_number: 4, kind: 'blocks' }, { from_number: 3, to_number: 4, kind: 'blocks' },
          { from_number: 4, to_number: 1, kind: 'parent' }])
      } finally {
        db.close()
      }

      for (const [bad, status, error] of [
        [{ title: ' \n ' }, 400, 'a new issue needs a title'],
        [{ title: 'x'.repeat(257) }, 400, 'a title holds at most 256 characters'],
        [{ body: 'x'.repeat(65_537) }, 400, 'a description holds at most 65536 characters'],
        [{ body: 7 }, 400, 'a description is text'],
        [{ labels: 'paint' }, 400, 'labels come as a list of names'],
        ...[['two\nlines'], ['a,b'], [''], [' padded'], ['x'.repeat(51)], [7]].map(labels => [{ labels }, 400,
          'a label is a name on one line, of at most 50 characters, without commas'] as const),
        [{ desk: 'attic' }, 404, 'no such project'],
        [{ agentReady: 'yes' }, 400, 'Agent-Ready is on or off'],
        ...[0, -2, 1.5, '1', [1], 'one two'].map(parent => [{ parent }, 400, 'a parent is one issue number'] as const),
        ...[1, [0], ['#1'], [1, 'x']].map(blockedBy => [{ blockedBy }, 400, 'Blocked By takes issue numbers'] as const),
        // The store holds no #99, so it files nothing and says why.
        [{ parent: 99 }, 409, 'me/porch has no item #99'],
        [{ blockedBy: [1, 99] }, 409, 'me/porch has no item #99'],
      ] as const) {
        assert.deepEqual(await postTo('/api/issue-create', { ...issue, ...bad }), { status, data: { error } },
          JSON.stringify(bad))
      }
      assert.equal(stored().length, 5)
      // The board reads each create set off finish before the stand-in goes.
      await new Promise(resolve => setTimeout(resolve, 50))
    } finally {
      host.run = real
      desks.delete('porch')
      rmSync(main, { recursive: true, force: true })
    }
  })

test('the New Issue form\'s template is the project\'s task template without its front matter and comments, or the '
  + 'create-issue skill\'s sections', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-porch-'))
  desks.set('porch', { id: 'porch', repo: 'me/porch', main } as Desk)
  const template = async (desk = 'porch') => {
    const got = await getFrom(`/api/issue-template?desk=${desk}`)
    return { status: got.status, data: JSON.parse(got.body.toString()) }
  }
  try {
    const fallback = { status: 200, data: { template: '## Goal\n\n## Done When\n\n## Notes\n' } }
    assert.deepEqual(await template(), fallback)
    mkdirSync(join(main, '.github', 'ISSUE_TEMPLATE'), { recursive: true })
    writeFileSync(join(main, '.github', 'ISSUE_TEMPLATE', 'task.md'), [
      '---', 'name: Task', 'about: A bug or feature', '---', '<!-- markdownlint-disable-file MD041 -->', '',
      '<!--', 'Add a `## Decisions Needed` section when the issue needs an answer.', '-->', '', '## Goal', '',
      '<!-- What should be true. -->', '', '## Done When', '', '  <!-- Checks a reviewer can confirm. -->', '',
      '- [ ]', '', '## Notes', '', '<!-- Repro steps. -->', ''].join('\r\n'))
    assert.deepEqual(await template(), { status: 200,
      data: { template: '## Goal\n\n## Done When\n\n- [ ]\n\n## Notes\n' } })
    // A template of front matter and comments alone leaves nothing, so the skill's sections stand in.
    writeFileSync(join(main, '.github', 'ISSUE_TEMPLATE', 'task.md'), '---\nname: Task\n---\n<!-- Nothing. -->\n')
    assert.deepEqual(await template(), fallback)
    assert.deepEqual(await template('attic'), { status: 404, data: { error: 'no such project' } })
  } finally {
    desks.delete('porch')
    rmSync(main, { recursive: true, force: true })
  }
})

test('a comment\'s Edit reads its Markdown and writes it through the store as you, which refuses another writer\'s '
  + 'comment and one sent to the agent with its reason', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-shed-'))
  const answer = (anchor: string) => `Answers to the Decisions Needed in [the description](#${anchor}):\n\n- **1.** A`
  storeFixture(['import os', 'p = "me/shed"', 'n = store.create(p, "issue", "Fix the shed")',
    'store.comment(p, n, "Use *blue* paint")', 'store.comment(p, n, "Paint it", to_agent=True)',
    `store.comment(p, n, ${JSON.stringify(answer('issue-body'))})`, 'os.environ["AGENT_DESK_RUN"] = "9"',
    'store.comment(p, n, "Painted")'].join('\n'), storePath())
  const ids = (() => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return db.prepare('SELECT id FROM comments WHERE project = ? ORDER BY id').all('me/shed')
        .map(row => Number(row.id))
    } finally {
      db.close()
    }
  })()
  const [mine, sent, answered, agents] = ids
  const desk = { id: 'shed', repo: 'me/shed', owner: 'me', title: 'Shed', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('shed', desk)
  const real = host.run
  host.run = async () => null
  const version = (text: string) => createHash('sha256').update(text).digest('hex').slice(0, 16)
  const source = async (id: unknown) => {
    const read = await getFrom(`/api/source?desk=shed&comment=${id}`)
    return { status: read.status, data: JSON.parse(read.body.toString()) }
  }
  const preview = async () => JSON.parse((await getFrom('/api/issue?desk=shed&number=1')).body.toString()) as
    { comments: { id: number; html: string; edited?: string; toAgent?: true; answers?: { anchor: string } }[] }
  try {
    assert.deepEqual(await source(mine), { status: 200, data: { id: mine, number: 1, body: 'Use *blue* paint',
      version: version('Use *blue* paint') } })
    assert.deepEqual(await source(9999), { status: 404, data: { error: 'no such comment' } })
    assert.deepEqual(await postTo('/api/comment-edit', { desk: 'shed', number: 1, id: mine, body: 'Use *green* paint',
      version: version('Use *blue* paint') }), { status: 200, data: { version: version('Use *green* paint') } })
    const [edited, toAgent] = (await preview()).comments
    assert.equal(edited?.html, '<p>Use <em>green</em> paint</p>\n')
    assert.match(edited?.edited ?? '', /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/)
    assert.equal(toAgent?.toAgent, true)
    assert.equal(toAgent?.edited, undefined)

    // An answer comment you edit ties to the section its new first line names.
    assert.equal((await preview()).comments[2]?.answers?.anchor, 'issue-body')
    assert.equal((await postTo('/api/comment-edit', { desk: 'shed', number: 1, id: answered,
      body: answer(`comment-${mine}`), version: version(answer('issue-body')) })).status, 200)
    assert.equal((await preview()).comments[2]?.answers?.anchor, `comment-${mine}`)

    const edit = { desk: 'shed', number: 1, id: mine, body: 'Use red paint', version: version('Use *green* paint') }
    for (const [bad, status, error] of [
      [{ id: agents, version: version('Painted') }, 409, `Comment ${agents} is by agent, and only its writer edits it`],
      [{ id: sent, version: version('@agent Paint it') }, 409,
        `Comment ${sent} went to the agent, so it stays as the agent read it`],
      [{ body: ' \n ' }, 409, 'A comment needs a body'],
      [{ version: version('Use *blue* paint') }, 409, 'the comment changed since the page read it'],
      [{ id: 9999 }, 404, 'no such comment'],
      [{ number: 2 }, 404, 'no such comment'],
      [{ body: 'x'.repeat(65_537) }, 400, 'a comment holds at most 65536 characters'],
      [{ id: '' }, 400, 'an edit names a comment, its new text, and the version it replaces'],
      [{ version: undefined }, 400, 'an edit names a comment, its new text, and the version it replaces'],
      [{ desk: 'attic' }, 404, 'no such project'],
    ] as const) {
      assert.deepEqual(await postTo('/api/comment-edit', { ...edit, ...bad }), { status, data: { error } },
        JSON.stringify(bad))
    }
    assert.equal((await source(mine)).data.body, 'Use *green* paint')
    // The board reads each edit set off finish before the stand-in goes.
    await new Promise(resolve => setTimeout(resolve, 50))
  } finally {
    host.run = real
    desks.delete('shed')
    rmSync(main, { recursive: true, force: true })
  }
})

test('Move to Open on a queued card cancels the runs that wait for the issue and sets it open, and leaves an issue '
  + 'whose run started in Queued', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-barn-'))
  storeFixture(['p = "me/barn"', 'q = store.create(p, "issue", "Paint the barn", labels=["agent-ready"])',
    'w = store.create(p, "issue", "Fix the door")', 'store.set_state(p, w, "waiting")',
    'r = store.create(p, "issue", "Oil the hinge", labels=["agent-ready"])',
    'store.request_run(p, "agent", str(q), env={"EVENT": "issues"})',
    'store.request_run(p, "agent", str(w), env={"EVENT": "issues"})',
    'store.run_started(store.request_run(p, "agent", str(r)), "agent-desk-agent-me-barn-3-3.service")'].join('\n'),
  storePath())
  const desk = { id: 'barn', repo: 'me/barn', owner: 'me', title: 'Barn', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('barn', desk)
  // The board's read and the dispatcher's command run from this checkout against the test's store.
  const real = host.run
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/barn' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const stored = () => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return { states: db.prepare('SELECT number, state FROM items WHERE project = ? ORDER BY number').all('me/barn')
        .map(row => [row.number, row.state]),
      runs: db.prepare('SELECT target, status FROM runs WHERE project = ? ORDER BY id').all('me/barn')
        .map(row => [row.target, row.status]) }
    } finally {
      db.close()
    }
  }
  const rows = (stage: 'queued' | 'next') => (desk.board?.[stage] ?? []).map(row => [row.key, row.actions])
  try {
    await rebuild(desk)
    // #3's run started before the run moved it to in-progress, so its card still offers the button.
    assert.deepEqual(rows('queued'), [['queued-1', ['open', 'to-open', 'close', 'down']],
      ['waiting-2', ['open', 'to-open', 'close', 'up', 'down', 'top']],
      ['queued-3', ['open', 'to-open', 'close', 'up', 'top']]])
    for (const key of ['queued-1', 'waiting-2', 'queued-3']) {
      assert.equal((await postTo('/api/act', { desk: 'barn', key, action: 'to-open' })).status, 202, key)
      await settled()
    }
    // The dispatcher refused #3's move, so it stays queued with its run.
    assert.deepEqual(stored(), { states: [[1, 'open'], [2, 'open'], [3, 'queued']],
      runs: [['1', 'cancelled'], ['2', 'cancelled'], ['3', 'running']] })
    assert.deepEqual(rows('queued'), [['queued-3', ['open', 'to-open', 'close']]])
    assert.deepEqual(rows('next').map(([key]) => key).sort(), ['next-1', 'next-2'])
  } finally {
    host.run = real
    desks.delete('barn')
    rmSync(main, { recursive: true, force: true })
  }
})

test('Unpick on a picked queued card drops its pick through lib/hold.py and shows Start, and a pick the issue\'s run '
  + 'claimed meanwhile changes nothing', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-crib-'))
  storeFixture(['p = "me/crib"', 'a = store.create(p, "issue", "Patch the roof", labels=["agent-ready"])',
    'b = store.create(p, "issue", "Sweep the loft", labels=["agent-ready"])',
    'store.request_run(p, "agent", str(a), env={"EVENT": "issues"})',
    'store.request_run(p, "agent", str(b), env={"EVENT": "issues"})'].join('\n'), storePath())
  // lib/hold.py's state, in Manual with both issues picked and a pick of another project's issue.
  const held = join(TEST_HOME, 'state', 'agent-desk', 'hold.json')
  mkdirSync(join(TEST_HOME, 'state', 'agent-desk'), { recursive: true })
  const pickedOf = () => JSON.parse(readFileSync(held, 'utf8')).picked
  writeFileSync(held, JSON.stringify({ state: 'manual', since: null, picked: ['me/crib#1', 'me/crib#2', 'me/barn#9'] }))
  const desk = { id: 'crib', repo: 'me/crib', owner: 'me', title: 'Crib', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('crib', desk)
  // The board's read and lib/hold.py run from this checkout against the test's store and state folder.
  const real = host.run
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/crib' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const rows = () => (desk.board?.queued ?? []).map(row => [row.key, row.note, row.actions])
  try {
    await rebuild(desk)
    assert.deepEqual(rows(), [['queued-1', 'picked', ['open', 'unpick', 'to-open', 'close', 'down']],
      ['queued-2', 'picked', ['open', 'unpick', 'to-open', 'close', 'up', 'top']]])
    assert.equal((await postTo('/api/act', { desk: 'crib', key: 'queued-1', action: 'unpick' })).status, 202)
    // The card shows Start before lib/hold.py answers.
    assert.deepEqual(rows()[0], ['queued-1', 'waits for Start', ['open', 'start', 'to-open', 'close', 'down']])
    await settled()
    assert.deepEqual(pickedOf(), ['me/crib#2', 'me/barn#9'])
    assert.deepEqual(rows()[0], ['queued-1', 'waits for Start', ['open', 'start', 'to-open', 'close', 'down']])
    // Unpick is gone from the card, so the server refuses it.
    assert.equal((await postTo('/api/act', { desk: 'crib', key: 'queued-1', action: 'unpick' })).status, 409)

    // #2's run claimed it after the board's read, which dropped its pick, so lib/hold.py refuses and changes nothing.
    writeFileSync(held, JSON.stringify({ state: 'manual', since: null, picked: ['me/barn#9'] }))
    assert.equal((await postTo('/api/act', { desk: 'crib', key: 'queued-2', action: 'unpick' })).status, 202)
    await settled()
    assert.deepEqual(pickedOf(), ['me/barn#9'])
    assert.deepEqual(rows()[1], ['queued-2', 'waits for Start', ['open', 'start', 'to-open', 'close', 'up', 'top']])
  } finally {
    host.run = real
    desks.delete('crib')
    rmSync(held, { force: true })
    rmSync(main, { recursive: true, force: true })
  }
})

test('Move to Backlog labels an Open card backlog and moves it to Backlog at once, Move to Open takes the label off, '
  + 'and a label the store refuses puts the card back', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-silo-'))
  storeFixture(['p = "me/silo"', 'store.create(p, "issue", "Sweep the loft")',
    'store.create(p, "issue", "Stack the hay", labels=["backlog"])'].join('\n'), storePath())
  const desk = { id: 'silo', repo: 'me/silo', owner: 'me', title: 'Silo', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('silo', desk)
  const real = host.run
  let pending = 0
  let refuse = false
  // The board's read and the label edits run from this checkout against the test's store, and a refused edit fails.
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash' || (refuse && argv.includes('edit'))) {
      return null
    }
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/silo' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const labels = (number: number) => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return db.prepare('SELECT name FROM labels WHERE project = ? AND number = ? ORDER BY name').all('me/silo', number)
        .map(row => row.name)
    } finally {
      db.close()
    }
  }
  const rows = (stage: 'next' | 'backlog') => (desk.board?.[stage] ?? []).map(row => [row.key, row.actions])
  try {
    await rebuild(desk)
    assert.deepEqual(rows('next'), [['next-1', ['open', 'ready', 'to-backlog', 'close']]])
    assert.deepEqual(rows('backlog'), [['backlog-2', ['open', 'to-open', 'close']]])
    // The card moves before the command returns, and the read after it finds the label in the store.
    assert.equal((await postTo('/api/act', { desk: 'silo', key: 'next-1', action: 'to-backlog' })).status, 202)
    assert.deepEqual(rows('next'), [])
    assert.equal(rows('backlog')[0]?.[0], 'backlog-1')
    await settled()
    assert.deepEqual(labels(1), ['backlog'])
    assert.deepEqual(rows('backlog').map(([key]) => key).sort(), ['backlog-1', 'backlog-2'])
    assert.equal((await postTo('/api/act', { desk: 'silo', key: 'backlog-2', action: 'to-open' })).status, 202)
    await settled()
    assert.deepEqual(labels(2), [])
    assert.deepEqual(rows('next'), [['next-2', ['open', 'ready', 'to-backlog', 'close']]])
    // A label the store refuses puts the card back in Open.
    refuse = true
    assert.equal((await postTo('/api/act', { desk: 'silo', key: 'next-2', action: 'to-backlog' })).status, 202)
    await settled()
    assert.deepEqual(labels(2), [])
    assert.deepEqual(rows('next').map(([key]) => key), ['next-2'])
    assert.deepEqual(rows('backlog').map(([key]) => key), ['backlog-1'])
  } finally {
    host.run = real
    desks.delete('silo')
    rmSync(main, { recursive: true, force: true })
  }
})

test('the comment box\'s close as a duplicate names another issue of the project, comments, links the original, and '
  + 'cancels the requests that wait for the issue', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-coop-'))
  storeFixture(['p = "me/coop"', 'store.create(p, "issue", "Fix the roost")',
    'q = store.create(p, "issue", "Mend the roost")', 'store.request_run(p, "agent", str(q))',
    'store.create(p, "review", "Fix it", branch="agent/issue-1")'].join('\n'), storePath())
  const desk = { id: 'coop', repo: 'me/coop', owner: 'me', title: 'Coop', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('coop', desk)
  const real = host.run
  const asked: string[][] = []
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    if (argv[4] === 'issue') {
      asked.push(argv.slice(4))
    }
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/coop' } })
    } catch {
      return null
    }
  }
  const stored = () => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return { item: { ...db.prepare('SELECT state, state_reason AS reason FROM items WHERE project = ? AND number = 2')
        .get('me/coop') },
      comments: db.prepare('SELECT body FROM comments WHERE project = ? AND number = 2 ORDER BY id').all('me/coop')
        .map(row => row.body),
      links: db.prepare('SELECT from_number, to_number, kind FROM links WHERE project = ?').all('me/coop')
        .map(row => [row.from_number, row.to_number, row.kind]),
      runs: db.prepare('SELECT target, status FROM runs WHERE project = ?').all('me/coop')
        .map(row => [row.target, row.status]) }
    } finally {
      db.close()
    }
  }
  const close = (fields: Record<string, unknown>) => postTo('/api/act', { desk: 'coop', key: 'next-2',
    action: 'close', reason: 'duplicate', comment: 'Same roost.', ...fields })
  try {
    await rebuild(desk)
    assert.ok(desk.board?.next.find(row => row.key === 'next-2')?.actions.includes('close'))
    // A missing, self, review, or unknown original is refused before the store, which changes nothing.
    for (const [duplicateOf, error] of [[undefined, 'a duplicate names the number of the issue it duplicates'],
      ['two', 'a duplicate names the number of the issue it duplicates'], ['#2', '#2 can\'t be a duplicate of itself'],
      [3, 'me/coop has no issue #3'], [99, 'me/coop has no issue #99']] as const) {
      assert.deepEqual(await close({ duplicateOf }), { status: 400, data: { error } }, String(duplicateOf))
    }
    assert.deepEqual(asked, [])
    assert.deepEqual(stored(), { item: { state: 'open', reason: null }, comments: [], links: [],
      runs: [['2', 'requested']] })
    assert.equal((await close({ duplicateOf: '#1' })).status, 200)
    assert.deepEqual(asked.at(-1), ['issue', 'close', '2', '--duplicate-of', '1', '--comment-file', '-'])
    assert.deepEqual(stored(), { item: { state: 'closed', reason: 'duplicate' },
      comments: ['Duplicate of #1', 'Same roost.'], links: [[2, 1, 'duplicate']], runs: [['2', 'cancelled']] })
  } finally {
    host.run = real
    desks.delete('coop')
    rmSync(main, { recursive: true, force: true })
  }
})

test('Close on a queued or waiting card takes it out of Queued at once, closes the issue, cancels the runs that wait '
  + 'for it, and drops its pick', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-grain-'))
  storeFixture(['p = "me/grain"', 'q = store.create(p, "issue", "Seal the silo", labels=["agent-ready"])',
    'w = store.create(p, "issue", "Seal it again", labels=["agent-ready"])', 'store.set_state(p, w, "waiting")',
    'k = store.create(p, "issue", "Paint the silo", labels=["agent-ready"])',
    'for n in (q, w, k): store.request_run(p, "agent", str(n), env={"EVENT": "issues"})'].join('\n'), storePath())
  // lib/hold.py's state, in Manual with every issue picked and a pick of another project's issue.
  const held = join(TEST_HOME, 'state', 'agent-desk', 'hold.json')
  mkdirSync(join(TEST_HOME, 'state', 'agent-desk'), { recursive: true })
  const pickedOf = () => JSON.parse(readFileSync(held, 'utf8')).picked
  writeFileSync(held, JSON.stringify({ state: 'manual', since: null,
    picked: ['me/grain#1', 'me/grain#2', 'me/grain#3', 'me/barn#9'] }))
  const desk = { id: 'grain', repo: 'me/grain', owner: 'me', title: 'Grain', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('grain', desk)
  const real = host.run
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/grain' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const stored = () => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return { states: db.prepare('SELECT number, state, state_reason AS reason FROM items WHERE project = ? '
        + 'ORDER BY number').all('me/grain').map(row => [row.number, row.state, row.reason]),
      runs: db.prepare('SELECT target, status FROM runs WHERE project = ? ORDER BY id').all('me/grain')
        .map(row => [row.target, row.status]) }
    } finally {
      db.close()
    }
  }
  const rows = () => (desk.board?.queued ?? []).map(row => [row.key, row.actions])
  try {
    await rebuild(desk)
    assert.deepEqual(rows(), [['queued-1', ['open', 'unpick', 'to-open', 'close', 'down']],
      ['waiting-2', ['open', 'unpick', 'to-open', 'close', 'up', 'down', 'top']],
      ['queued-3', ['open', 'unpick', 'to-open', 'close', 'up', 'top']]])
    // The card's Close leaves Queued before the store answers, and the rows left get the moves of their new places.
    assert.equal((await postTo('/api/act', { desk: 'grain', key: 'queued-1', action: 'close' })).status, 202)
    assert.deepEqual(rows(), [['waiting-2', ['open', 'unpick', 'to-open', 'close', 'down']],
      ['queued-3', ['open', 'unpick', 'to-open', 'close', 'up', 'top']]])
    await settled()
    // The waiting card's comment box closes it as a duplicate of #3.
    assert.equal((await postTo('/api/act', { desk: 'grain', key: 'waiting-2', action: 'close', reason: 'duplicate',
      duplicateOf: 3, comment: 'Same silo.' })).status, 200)
    await settled()
    assert.deepEqual(stored(), { states: [[1, 'closed', 'completed'], [2, 'closed', 'duplicate'], [3, 'queued', null]],
      runs: [['1', 'cancelled'], ['2', 'cancelled'], ['3', 'requested']] })
    assert.deepEqual(pickedOf(), ['me/grain#3', 'me/barn#9'])
    assert.deepEqual(rows(), [['queued-3', ['open', 'unpick', 'to-open', 'close']]])
  } finally {
    host.run = real
    desks.delete('grain')
    rmSync(held, { force: true })
    rmSync(main, { recursive: true, force: true })
  }
})

test('Move Up, Move Down, Move to Top, and a drop on a queued card move its issue in the store\'s start order, and a '
  + 'move the store refuses changes nothing', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-loft-'))
  storeFixture(['p = "me/loft"', 'store.create(p, "issue", "Paint the barn", labels=["agent-ready"])',
    'w = store.create(p, "issue", "Fix the door")', 'store.set_state(p, w, "waiting")',
    'store.create(p, "issue", "Oil the hinge", labels=["agent-ready"])',
    'store.create(p, "issue", "Sweep the loft", labels=["agent-ready"])'].join('\n'), storePath())
  const desk = { id: 'loft', repo: 'me/loft', owner: 'me', title: 'Loft', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('loft', desk)
  const real = host.run
  const asked: string[][] = []
  let pending = 0
  host.run = async (argv, _cwd, stdin) => {
    if (argv[0] !== 'bash') {
      return null
    }
    asked.push(argv)
    pending += 1
    try {
      return execFileSync(argv[0], argv.slice(1), { cwd: main, encoding: 'utf8', input: stdin, stdio: 'pipe',
        env: { ...process.env, AGENT_DESK_HOME: join(import.meta.dirname, '..'), GH_REPO: 'me/loft' } })
    } catch {
      return null
    } finally {
      pending -= 1
    }
  }
  const settled = async () => {
    do {
      await new Promise(resolve => setTimeout(resolve, 20))
    } while (pending > 0 || desk.commands > 0 || desk.refreshing)
  }
  const order = () => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return db.prepare('SELECT number FROM items WHERE project = ? AND rank IS NOT NULL ORDER BY rank').all('me/loft')
        .map(row => row.number)
    } finally {
      db.close()
    }
  }
  const keys = () => (desk.board?.queued ?? []).map(row => row.key)
  const press = async (key: string, action: string) => {
    assert.equal((await postTo('/api/act', { desk: 'loft', key, action })).status, 202, `${action} ${key}`)
    await settled()
  }
  try {
    await rebuild(desk)
    assert.deepEqual(keys(), ['queued-1', 'waiting-2', 'queued-3', 'queued-4'])
    // The first card offers no Move Up or Move to Top, and the last no Move Down.
    assert.equal((await postTo('/api/act', { desk: 'loft', key: 'queued-1', action: 'up' })).status, 409)
    assert.equal((await postTo('/api/act', { desk: 'loft', key: 'queued-4', action: 'down' })).status, 409)
    await press('queued-4', 'top')
    assert.deepEqual(order(), [4, 1, 2, 3])
    await press('queued-1', 'down')
    await press('queued-3', 'up')
    assert.deepEqual(order(), [4, 2, 3, 1])
    assert.deepEqual(keys(), ['queued-4', 'waiting-2', 'queued-3', 'queued-1'])
    assert.deepEqual(desk.board?.queued.map(row => row.actions.slice(3)), [['down'], ['up', 'down', 'top'],
      ['up', 'down', 'top'], ['up', 'top']])
    assert.ok(asked.some(argv => argv.slice(-4).join(' ') === 'queue order 4 --top'))
    // A drop places the card at a number from 1, and one past the end puts it last.
    const drop = async (key: string, to: unknown) => (await postTo('/api/place', { desk: 'loft', key, to })).status
    assert.equal(await drop('queued-1', 1), 202)
    await settled()
    assert.deepEqual(order(), [1, 4, 2, 3])
    assert.deepEqual(keys(), ['queued-1', 'queued-4', 'waiting-2', 'queued-3'])
    assert.ok(asked.some(argv => argv.slice(-5).join(' ') === 'queue order 1 --to 1'))
    assert.equal(await drop('queued-1', 9), 202)
    await settled()
    assert.deepEqual(order(), [4, 2, 3, 1])
    // A drop with no place, or of a row outside Queued's start order, sends nothing.
    const sent = asked.length
    assert.deepEqual([await drop('queued-1', 0), await drop('queued-1', 'two'), await drop('next-1', 1),
      await drop('queued-9', 1)], [400, 400, 409, 409])
    assert.equal(asked.length, sent)
    // #3 started after the board's read, so the store refuses its move and the order stays.
    const db = new DatabaseSync(storePath())
    try {
      db.prepare("UPDATE items SET state = 'in-progress', rank = NULL WHERE project = ? AND number = 3").run('me/loft')
    } finally {
      db.close()
    }
    await press('queued-3', 'top')
    assert.deepEqual(order(), [4, 2, 1])
    assert.deepEqual(keys(), ['queued-4', 'waiting-2', 'queued-1'])
    // A drop the store refuses changes nothing in it either.
    const later = new DatabaseSync(storePath())
    try {
      later.prepare("UPDATE items SET state = 'in-progress', rank = NULL WHERE project = ? AND number = 1")
        .run('me/loft')
    } finally {
      later.close()
    }
    assert.equal(await drop('queued-1', 1), 202)
    await settled()
    assert.deepEqual(order(), [4, 2])
    assert.deepEqual(keys(), ['queued-4', 'waiting-2'])
  } finally {
    host.run = real
    desks.delete('loft')
    rmSync(main, { recursive: true, force: true })
  }
})

test('the header\'s usage limit saves through lib/usage.py, as agent-desk usage-limit does, and refuses a bad value '
  + 'before it runs', async () => {
  const folder = join(TEST_HOME, 'state', 'agent-desk')
  mkdirSync(folder, { recursive: true })
  const now = Date.now() / 1000
  // A reading just taken, so the command fetches nothing, with no sign-in for a fetch to use besides.
  writeFileSync(join(folder, 'usage.json'), JSON.stringify({ percent: 81.4, resets_at: null, read_at: now,
    tried_at: now, error: null }))
  const signIn = process.env.CLAUDE_CONFIG_DIR
  process.env.CLAUDE_CONFIG_DIR = mkdtempSync(join(tmpdir(), 'agent-desk-claude-'))
  const limit = () => JSON.parse(readFileSync(join(folder, 'hold.json'), 'utf8')).usageLimit
  try {
    const on = await postTo('/api/usage-limit', { on: true, percent: 80 })
    assert.equal(on.status, 200)
    assert.deepEqual(limit(), { on: true, percent: 80 })
    // The answer is the header's new state, at or over the limit here.
    assert.deepEqual([on.data.on, on.data.limit, on.data.percent, on.data.problem], [true, 80, 81.4, null])
    const off = await postTo('/api/usage-limit', { on: false })
    assert.equal(off.status, 200)
    assert.deepEqual(limit(), { on: false, percent: 80 }, 'off keeps the percentage for on')
    for (const bad of [{ on: true, percent: 101 }, { on: true, percent: -1 }, { on: true, percent: 50.5 },
      { on: true, percent: '80' }, { on: true }, { on: 'yes', percent: 80 }, {}]) {
      const refused = await postTo('/api/usage-limit', bad)
      assert.deepEqual([refused.status, refused.data.error], [400,
        'the weekly usage limit is a whole percentage from 0 to 100'], JSON.stringify(bad))
    }
    assert.deepEqual(limit(), { on: false, percent: 80 }, 'a refused value changes nothing')
    // A post from another page changes nothing either.
    const crossed = await postTo('/api/usage-limit', { on: true, percent: 10 }, { ...JSON_POST,
      origin: 'http://evil.example' })
    assert.notEqual(crossed.status, 200)
    assert.deepEqual(limit(), { on: false, percent: 80 })
  } finally {
    rmSync(process.env.CLAUDE_CONFIG_DIR, { recursive: true, force: true })
    if (signIn === undefined) {
      delete process.env.CLAUDE_CONFIG_DIR
    } else {
      process.env.CLAUDE_CONFIG_DIR = signIn
    }
  }
})

test('an issue\'s preview says whether the store would delete it, and Delete Issue removes it through the store, '
  + 'which refuses an issue with work behind it', async () => {
  const main = mkdtempSync(join(tmpdir(), 'agent-desk-cellar-'))
  const git = (...args: string[]) => execFileSync('git', ['-C', main, '-c', 'user.name=Test', '-c',
    'user.email=test@localhost', '-c', 'commit.gpgsign=false', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  git('commit', '-q', '--allow-empty', '-m', 'Start')
  git('branch', 'agent/issue-4')
  storeFixture(['p = "me/cellar"', 'store.create(p, "issue", "Tidy the cellar")',
    'store.create(p, "issue", "Filed by mistake", labels=["bug"], blocked_by=[1])', 'store.comment(p, 2, "Oops")',
    'store.create(p, "issue", "Sort the shelves", parent=1)', 'store.create(p, "issue", "Oil the saw")'].join('\n'),
  storePath())
  const desk = { id: 'cellar', repo: 'me/cellar', owner: 'me', title: 'Cellar', main, chores: [], board: null, at: 0,
    refreshing: false, starting: new Map(), labels: {}, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  desks.set('cellar', desk)
  // The board's read after a delete answers nothing, which leaves the board as it was.
  const real = host.run
  host.run = async () => null
  const deletable = async (number: number) => JSON.parse((await getFrom(`/api/issue?desk=cellar&number=${number}`))
    .body.toString()).deletable
  const count = (sql: string) => {
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      return Number(Object.values(db.prepare(sql).get('me/cellar') ?? {})[0])
    } finally {
      db.close()
    }
  }
  try {
    // #1 has a sub-issue and #4 a branch, so only #2 and #3 show Delete Issue.
    assert.deepEqual([await deletable(1), await deletable(2), await deletable(3), await deletable(4)],
      [false, true, true, false])
    assert.deepEqual(await postTo('/api/issue-delete', { desk: 'cellar', number: 2 }),
      { status: 200, data: { number: 2 } })
    assert.equal(count('SELECT COUNT(*) FROM items WHERE project = ? AND number = 2'), 0)
    for (const table of ['labels', 'comments']) {
      assert.equal(count(`SELECT COUNT(*) FROM ${table} WHERE project = ? AND number = 2`), 0, table)
    }
    assert.equal(count('SELECT COUNT(*) FROM links WHERE project = ? AND 2 IN (from_number, to_number)'), 0)
    assert.equal(count('SELECT next_number FROM projects WHERE slug = ?'), 5)
    assert.equal((await getFrom('/api/issue?desk=cellar&number=2')).status, 404)
    for (const [sent, status, error] of [
      [{ number: 2 }, 404, '#2 is no issue here'],
      [{ number: 1 }, 409, '#1 has sub-issue #3, so it can\'t be deleted. Close it instead with `agent-desk issue '
        + 'close 1 --reason not-planned`'],
      [{ number: 4 }, 409, '#4 has the branch agent/issue-4, so it can\'t be deleted. Close it instead with '
        + '`agent-desk issue close 4 --reason not-planned`'],
      ...[0, -1, 1.5, '3', null].map(number => [{ number }, 400, 'a delete names one issue number'] as const),
      [{ desk: 'attic', number: 3 }, 404, 'no such project'],
    ] as const) {
      assert.deepEqual(await postTo('/api/issue-delete', { desk: 'cellar', ...sent }), { status, data: { error } },
        JSON.stringify(sent))
    }
    assert.equal(count('SELECT COUNT(*) FROM items WHERE project = ?'), 3)
    // The board reads each delete set off finish before the stand-in goes.
    await new Promise(resolve => setTimeout(resolve, 50))
  } finally {
    host.run = real
    desks.delete('cellar')
    rmSync(main, { recursive: true, force: true })
  }
})
