// Run with: node --test web/
// First, so the modules read the temporary state folder, where the store and the check logs live.
import { TEST_HOME } from './test-env.ts'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { dirname, join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'

import { agentRequest, bodyVersion, capDiff, checkLogPath, diffLines, hovercardOf, issuePreviewOf, markdownOf,
  movedLine, NOTHING_TO_SEND, pictureOf, pictureType, postComment, postLineComment, previewOf, relationsOf, sendToAgent,
  statusesOf, unsentOf, withAssets } from './review.ts'
import { OWN_SOCKET } from './own-socket.ts'
import { handle } from './server.ts'
import { desks } from './state.ts'
import type { Desk } from './state.ts'
import { storeFixture } from './store-fixture.ts'
import { storePath } from './store.ts'

const MAIN = join(TEST_HOME, 'game')
// Where git would write a diff that read review #11's merge commit as its --output option.
const OPTION_OUTPUT = join(TEST_HOME, 'written-by-git')
const PICTURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 1, 2, 3])

function git(...args: string[]): string {
  return execFileSync('git', ['-C', MAIN, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c',
    'commit.gpgsign=false', ...args], { encoding: 'utf8' }).trim()
}

/**
 * A project's main checkout and its store. Review #6 changes a.txt, moves old.txt to new.txt, deletes gone.txt, and
 * adds a picture on agent/issue-4; review #7 merged agent/issue-9, whose branch is gone; review #8's branch never
 * reached this machine. Review #9 was rebase-merged as two commits after main moved on; review #10 adds a file too
 * large for the page to read, beside a change to a.txt, a move of old.txt, and gone.txt deleted; review #11's merge
 * commit, as the store holds it, reads as one of git's options.
 * Issues #2 to #4 are #1's sub-issues, #4 waits on #2 and #3, and #5 waits on #4.
 */
function project(): { desk: Desk; head: string; merge: string } {
  mkdirSync(MAIN, { recursive: true })
  git('init', '-q', '-b', 'main')
  writeFileSync(join(MAIN, 'a.txt'), 'one\ntwo\nthree\n')
  writeFileSync(join(MAIN, 'old.txt'), 'a line that moves\nwith another\nand a third\n')
  writeFileSync(join(MAIN, 'gone.txt'), 'bye\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Start')
  git('switch', '-q', '-c', 'agent/issue-4')
  writeFileSync(join(MAIN, 'a.txt'), 'one\nTWO\nthree\nfour')
  git('mv', 'old.txt', 'new.txt')
  git('rm', '-q', 'gone.txt')
  writeFileSync(join(MAIN, 'pic.png'), PICTURE)
  git('add', '.')
  git('commit', '-q', '-m', 'Paint')
  const head = git('rev-parse', 'HEAD')
  git('switch', '-q', '-c', 'agent/issue-9', 'main')
  writeFileSync(join(MAIN, 'b.txt'), 'gate\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Gate')
  git('switch', '-q', 'main')
  git('merge', '-q', '--no-ff', '-m', 'Merge the gate', 'agent/issue-9')
  git('branch', '-q', '-D', 'agent/issue-9')
  const merge = git('rev-parse', 'HEAD')
  // A branch of two commits, rebased onto a main that moved since, and landed there as GitHub's rebase-merge does.
  git('switch', '-q', '-c', 'agent/issue-10')
  writeFileSync(join(MAIN, 'rail.txt'), 'one\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Lay one rail')
  writeFileSync(join(MAIN, 'rail.txt'), 'one\ntwo\n')
  git('commit', '-q', '-am', 'Lay another')
  git('switch', '-q', 'main')
  writeFileSync(join(MAIN, 'elsewhere.txt'), 'main moved\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Elsewhere')
  git('rebase', '-q', 'main', 'agent/issue-10')
  git('switch', '-q', 'main')
  git('merge', '-q', '--ff-only', 'agent/issue-10')
  git('branch', '-q', '-D', 'agent/issue-10')
  const rebased = git('rev-parse', 'HEAD')
  git('switch', '-q', '-c', 'agent/issue-11')
  writeFileSync(join(MAIN, 'huge.txt'), 'x\n'.repeat(200_001))
  writeFileSync(join(MAIN, 'small.txt'), 'small\n')
  writeFileSync(join(MAIN, 'a.txt'), 'one\ntwo\nTHREE\n')
  git('mv', 'old.txt', 'kept.txt')
  git('rm', '-q', 'gone.txt')
  git('add', '.')
  git('commit', '-q', '-m', 'Huge')
  git('switch', '-q', 'main')
  // The pictures `agent-desk shots` pushed, as the last fetch left origin's pr-assets branch.
  git('switch', '-q', '--orphan', 'pr-assets')
  mkdirSync(join(MAIN, '6'))
  writeFileSync(join(MAIN, '6', 'shot.png'), PICTURE)
  git('add', '6/shot.png')
  git('commit', '-q', '-m', 'Pictures')
  git('update-ref', 'refs/remotes/origin/pr-assets', 'HEAD')
  git('switch', '-q', '-f', 'main')
  git('branch', '-q', '-D', 'pr-assets')

  mkdirSync(dirname(storePath()), { recursive: true })
  storeFixture([
    'import os',
    'p = "me/game"',
    'store.create(p, "issue", "Fence work")',
    'store.create(p, "issue", "Buy paint", parent=1)',
    'store.create(p, "issue", "Sand the fence", parent=1)',
    'store.create(p, "issue", "Paint the fence", "Needs #2 first, and see [the notes](#notes).\\n\\n**Bold** words.",'
      + ' labels=["bug", "odd"], parent=1, blocked_by=[2, 3])',
    'store.create(p, "issue", "Hang the gate", "x" * 300, blocked_by=[4])',
    'store.set_state(p, 2, "closed", "not-planned")',
    'store.comment(p, 4, "Looks right to me.")',
    `store.create(p, "review", "Paint it", "Fixes #4.", branch="agent/issue-4", head_sha="${head}", fixes=[4])`,
    'store.create(p, "review", "Hang it", branch="agent/issue-9")',
    `store.merge(p, 7, "${merge}")`,
    'store.create(p, "review", "Lost work", branch="agent/gone", draft=True)',
    'store.create(p, "review", "Lay the rails", branch="agent/issue-10")',
    `store.merge(p, 9, "${rebased}")`,
    'db.execute("UPDATE reviews SET merged_commits = 2 WHERE project = ? AND number = 9", (p,))',
    'db.commit()',
    'store.create(p, "review", "Huge", branch="agent/issue-11")',
    'store.create(p, "review", "Odd merge", branch="agent/odd")',
    `store.merge(p, 11, "--output=${OPTION_OUTPUT}")`,
    'store.comment(p, 6, "A typo here.", path="a.txt", line=2)',
    `check = store.check_queue(p, "agent/issue-4", "${head}")`,
    `store.check_started(check, ${JSON.stringify(checkLogPath('me/game', 1))})`,
    'store.check_ended(check, "failed", rerun=True)',
    'os.environ["AGENT_DESK_RUN"] = "1"',
    'store.comment(p, 4, "Done in #6.")',
    'store.comment(p, 6, "Ready for review.")',
  ].join('\n'), storePath())
  const desk = { id: 'game', repo: 'me/game', owner: 'me', title: 'Game', main: MAIN, chores: [], board: null, at: 0,
    refreshing: false, fixing: new Map(), starting: new Map(), labels: { bug: 'd73a4a' }, models: {}, chorePrs: {},
    schedulePr: null, modelsFor: '', known: null, refreshes: 0, commands: 0 } as Desk
  return { desk, head, merge }
}

const { desk } = project()

test('an issue\'s preview reads from the store, renders each body here, and links each #123 to the page', () => {
  const issue = issuePreviewOf(desk, 4)
  assert.ok(issue)
  assert.equal(issue.title, 'Paint the fence')
  assert.equal(issue.url, '#game:4')
  assert.equal(issue.state, 'open')
  assert.deepEqual(issue.author, { login: 'you' })
  assert.deepEqual(issue.labels, [{ name: 'bug', color: 'd73a4a' }, { name: 'odd', color: 'ededed' }])
  assert.equal(issue.body, '<p>Needs <a class="issue-link" href="#game:2">#2</a> first, and see '
    + '<a href="#notes">the notes</a>.</p>\n<p><strong>Bold</strong> words.</p>\n')
  assert.deepEqual(issue.comments.map(one => [one.author.login, one.html]), [
    ['you', '<p>Looks right to me.</p>\n'],
    ['agent', '<p>Done in <a class="issue-link" href="#game:6">#6</a>.</p>\n'],
  ])
  assert.equal(issue.commentCount, 2)
  assert.equal(issuePreviewOf(desk, 2)?.state, 'not_planned')
  // A review, or a number nothing has, is no issue.
  assert.equal(issuePreviewOf(desk, 6), null)
  assert.equal(issuePreviewOf(desk, 99), null)
})

test('a hovercard names an item\'s kind and state, and quotes its description as plain text cut short', () => {
  assert.deepEqual(hovercardOf(desk, 4), { kind: 'issue', number: 4, title: 'Paint the fence', state: 'open',
    at: hovercardOf(desk, 4)?.at, labels: [{ name: 'bug', color: 'd73a4a' }, { name: 'odd', color: 'ededed' }],
    excerpt: 'Needs #2 first, and see the notes. Bold words.' })
  assert.equal(hovercardOf(desk, 2)?.state, 'not_planned')
  assert.equal(hovercardOf(desk, 5)?.excerpt.length, 240)
  assert.equal(hovercardOf(desk, 1)?.excerpt, '')
  assert.deepEqual([6, 7, 8].map(number => [hovercardOf(desk, number)?.kind, hovercardOf(desk, number)?.state]),
    [['pr', 'open'], ['pr', 'merged'], ['pr', 'draft']])
  assert.equal(hovercardOf(desk, 99), null)
})

test('an issue\'s relations come from the store\'s links, with each one\'s sub-issues and open blockers', () => {
  const relations = relationsOf(desk, 4)
  assert.ok(relations)
  assert.deepEqual(relations.parent, { number: 1, ref: '#1', title: 'Fence work', url: '#game:1', local: true,
    state: 'open', subs: { total: 3, done: 1 }, blockers: [] })
  assert.deepEqual(relations.subs, [])
  assert.deepEqual(relations.blockedBy.map(one => [one.ref, one.state]), [['#2', 'not_planned'], ['#3', 'open']])
  assert.deepEqual(relations.blocking.map(one => [one.ref, one.blockers]), [['#5', ['#4']]])
  // Only #3 still blocks #4.
  assert.deepEqual(relationsOf(desk, 1)?.subs.map(one => [one.ref, one.blockers]),
    [['#2', []], ['#3', []], ['#4', ['#3']]])
  assert.equal(relationsOf(desk, 6), null)
})

test('a review\'s files come from git diff main...branch, with their status, its binary file, and its checks',
  async () => {
  const pr = await previewOf(desk, 6)
  assert.ok(pr)
  assert.equal(pr.state, 'open')
  assert.equal(pr.url, '#game:6')
  assert.deepEqual([pr.head, pr.base, pr.commits, pr.additions, pr.deletions], ['agent/issue-4', 'main', 1, 2, 2])
  assert.equal(pr.noDiff, undefined)
  assert.deepEqual(pr.files.map(file => [file.path, file.from, file.status, file.additions, file.deletions,
    file.lines?.length]), [
    ['a.txt', undefined, 'modified', 2, 1, 7],
    ['gone.txt', undefined, 'deleted', 0, 1, 2],
    ['new.txt', 'old.txt', 'renamed', 0, 0, 0],
    ['pic.png', undefined, 'added', 0, 0, undefined],
  ])
  assert.deepEqual(pr.files[0]?.lines?.slice(1), [
    { kind: 'same', old: 1, new: 1, text: 'one' },
    { kind: 'del', old: 2, text: 'two' },
    { kind: 'add', new: 2, text: 'TWO' },
    { kind: 'same', old: 3, new: 3, text: 'three' },
    { kind: 'add', new: 4, text: 'four' },
    { kind: 'note', text: 'No newline at end of file' },
  ])
  // The failed first attempt stands while its retry waits, linked to its log on the page.
  assert.deepEqual(pr.checks, [{ name: 'test', state: 'failure', url: '/api/check-log?desk=game&check=1' }])
  assert.deepEqual(pr.issues, [{ number: 4, title: 'Paint the fence', url: '#game:4' }])
  assert.equal(pr.body, '<p>Fixes <a class="issue-link" href="#game:4">#4</a>.</p>\n')
  // The line comment belongs to the diff, so the conversation leaves it out.
  assert.deepEqual(pr.comments.map(one => [one.author.login, one.html]), [['agent', '<p>Ready for review.</p>\n']])
  assert.equal(await previewOf(desk, 4), null)
})

test('a merged review shows its merge\'s changes, and one whose branch is gone says its diff is unavailable',
  async () => {
  const merged = await previewOf(desk, 7)
  assert.equal(merged?.state, 'merged')
  assert.deepEqual(merged?.files.map(file => [file.path, file.additions]), [['b.txt', 1]])
  assert.equal(merged?.commits, 1)

  const gone = await previewOf(desk, 8)
  assert.ok(gone)
  assert.equal(gone.state, 'draft')
  assert.deepEqual(gone.files, [])
  assert.equal(gone.noDiff, 'The branch agent/gone is gone, so its diff is unavailable.')
  assert.deepEqual([gone.title, gone.checks, gone.comments], ['Lost work', [], []])
})

test('a rebase-merged review shows every commit it wrote, not the last alone', async () => {
  const merged = await previewOf(desk, 9)
  assert.equal(merged?.state, 'merged')
  assert.equal(merged?.commits, 2)
  assert.deepEqual(merged?.files.map(file => [file.path, file.additions, file.deletions]), [['rail.txt', 2, 0]])
})

test('a merge commit that reads as an option never reaches git as one', async () => {
  const odd = await previewOf(desk, 11)
  assert.equal(odd?.noDiff, 'The branch agent/odd is gone, so its diff is unavailable.')
  assert.ok(!existsSync(OPTION_OUTPUT))
})

test('a diff too large to read lists its files with their counts and status, and says it is too large', async () => {
  const huge = await previewOf(desk, 10)
  assert.ok(huge)
  assert.equal(huge.noDiff, undefined)
  assert.equal(huge.diffNote, 'This diff is too large to read, so its 5 files show without their lines.')
  assert.deepEqual(huge.files.map(file => [file.path, file.from, file.status, file.additions, file.omitted,
    file.lines]), [
    ['a.txt', undefined, 'modified', 1, true, undefined],
    ['gone.txt', undefined, 'deleted', 0, true, undefined],
    ['huge.txt', undefined, 'added', 200_001, true, undefined],
    ['kept.txt', 'old.txt', 'renamed', 0, undefined, []],
    ['small.txt', undefined, 'added', 1, true, undefined],
  ])
  assert.equal(huge.additions, 200_003)
})

test('each file\'s status reads from git\'s --name-status -z by its new path, with a copy as added', () => {
  const statuses = statusesOf(['M', 'a b.txt', 'A', 'new.txt', 'D', 'gone.txt', 'R087', 'old/x.ts', 'new/x.ts',
    'C100', 'src.ts', 'copy.ts', 'T', 'link', ''].join('\0'))
  assert.deepEqual([...statuses], [['a b.txt', 'modified'], ['new.txt', 'added'], ['gone.txt', 'deleted'],
    ['new/x.ts', 'renamed'], ['copy.ts', 'added'], ['link', 'modified']])
  assert.deepEqual(statusesOf(''), new Map())
})

test('a large diff keeps the rows of its smallest files and leaves out those of the largest', () => {
  const file = (path: string, rows: number) => ({ path, status: 'added' as const, additions: rows, deletions: 0,
    lines: Array.from({ length: rows }, (_, at) => ({ kind: 'add' as const, new: at + 1, text: 'x' })) })
  const capped = capDiff([file('a', 15_000), file('b', 4_000), file('c', 1_500), file('moved', 0),
    { path: 'pic.png', status: 'added' as const, additions: 0, deletions: 0 }])
  assert.equal(capped.omitted, 1)
  assert.deepEqual(capped.files.map(one => [one.path, one.lines?.length, one.omitted]),
    [['a', undefined, true], ['b', 4_000, undefined], ['c', 1_500, undefined], ['moved', 0, undefined],
      ['pic.png', undefined, undefined]])
  // Past 300 files with rows, the largest go without theirs however few rows each holds.
  const many = capDiff(Array.from({ length: 302 }, (_, at) => file(`f${at}`, at === 0 ? 3 : 2)))
  assert.equal(many.omitted, 2)
  assert.equal(many.files[0]?.omitted, true)
  assert.equal(many.files.filter(one => one.lines).length, 300)
  assert.deepEqual(capDiff([file('a', 3)]).omitted, 0)
})

test('a comment from the page goes through the store as you, and one that mentions @agent goes to the agent',
  async () => {
  const posted = await postComment(desk, 4, 'Use the blue from #2.')
  assert.equal(posted?.author.login, 'you')
  assert.equal(posted?.html, '<p>Use the blue from <a class="issue-link" href="#game:2">#2</a>.</p>\n')
  assert.ok(await postComment(desk, 6, '@agent please fix the typo'))
  assert.equal(await postComment(desk, 99, 'Nobody reads this.'), null)

  const db = new DatabaseSync(storePath(), { readOnly: true })
  try {
    assert.deepEqual(db.prepare('SELECT number, author, body, to_agent FROM comments ORDER BY id DESC LIMIT 2').all()
      .map(row => ({ ...row })), [
      { number: 6, author: 'you', body: '@agent please fix the typo', to_agent: 1 },
      { number: 4, author: 'you', body: 'Use the blue from #2.', to_agent: 0 },
    ])
  } finally {
    db.close()
  }
  assert.equal(issuePreviewOf(desk, 4)?.comments.at(-1)?.author.login, 'you')
})

test('a comment\'s preview renders on this machine, with the repository\'s pictures through the asset route', () => {
  assert.equal(markdownOf(desk, 'See #5 and ![shot](https://github.com/me/game/blob/pr-assets/6/shot.png?raw=true)'),
    '<p>See <a class="issue-link" href="#game:5">#5</a> and '
    + '<a class="picture-link" href="/api/asset?desk=game&amp;ref=pr-assets&amp;path=6%2Fshot.png">'
    + '<img src="/api/asset?desk=game&amp;ref=pr-assets&amp;path=6%2Fshot.png" alt="shot"></a></p>\n')
})

test('the repository\'s own pictures go through the asset route, and others stay as they are', () => {
  const html = '<a href="https://github.com/org/game/blob/pr-assets/227/after-1.jpg?raw=true">'
    + '<img src="https://github.com/org/game/blob/pr-assets/227/after-1.jpg?raw=true"></a>'
    + '<img src="https://github.com/org/other/blob/main/a.png?raw=true">'
  assert.equal(withAssets(html, 'game', 'org/game'),
    '<a href="/api/asset?desk=game&amp;ref=pr-assets&amp;path=227%2Fafter-1.jpg">'
    + '<img src="/api/asset?desk=game&amp;ref=pr-assets&amp;path=227%2Fafter-1.jpg"></a>'
    + '<img src="https://github.com/org/other/blob/main/a.png?raw=true">')
})

test('a picture comes from origin\'s copy of its branch in the main checkout', async () => {
  assert.deepEqual(await pictureOf(desk, 'pr-assets', '6/shot.png'), PICTURE)
  assert.equal(await pictureOf(desk, 'pr-assets', '6/missing.png'), null)
})

test('the asset route serves only pictures named plainly', () => {
  assert.equal(pictureType('pr-assets', '227/after-1.jpg'), 'image/jpeg')
  assert.equal(pictureType('pr-assets', '227/shot.PNG'), 'image/png')
  assert.equal(pictureType('pr-assets', '../secrets.png'), null)
  assert.equal(pictureType('pr-assets', '227/notes.md'), null)
  assert.equal(pictureType('main; rm', 'a.png'), null)
  assert.equal(pictureType('pr-assets', '/etc/a.png'), null)
})

test('a patch reads as rows numbered in the old and new file', () => {
  const patch = '@@ -3,3 +3,3 @@ class Snow\n keep\n-old line\n+new line\n+added\n\\ No newline at end of file'
  assert.deepEqual(diffLines(patch), [
    { kind: 'hunk', text: '@@ -3,3 +3,3 @@ class Snow' },
    { kind: 'same', old: 3, new: 3, text: 'keep' },
    { kind: 'del', old: 4, text: 'old line' },
    { kind: 'add', new: 4, text: 'new line' },
    { kind: 'add', new: 5, text: 'added' },
    { kind: 'note', text: 'No newline at end of file' },
  ])
})

/** What the server answers a GET from the page. */
async function getFrom(path: string): Promise<{ status: number; type: string; text: string }> {
  const req = { method: 'GET', url: path, headers: { host: '127.0.0.1:7480' }, socket: OWN_SOCKET }
  let status = 0
  let type = ''
  let text = ''
  const res = { headersSent: false, setHeader: () => res,
    writeHead: (code: number, headers?: Record<string, string>) => {
      status = code
      type = headers?.['Content-Type'] ?? ''
      return res
    },
    end: (sent: string | Buffer) => { text = String(sent) } }
  await handle(req as unknown as IncomingMessage, res as unknown as ServerResponse)
  return { status, type, text }
}

test('a check\'s Details link serves its log, and only a check of the project named', async () => {
  desks.set('game', desk)
  mkdirSync(dirname(checkLogPath('me/game', 1)), { recursive: true })
  writeFileSync(checkLogPath('me/game', 1), 'FAILED tests/test_paint.py\n')
  try {
    assert.deepEqual(await getFrom('/api/check-log?desk=game&check=1'),
      { status: 200, type: 'text/plain; charset=utf-8', text: 'FAILED tests/test_paint.py\n' })
    // The retry has no log yet, and no project has check 9.
    assert.equal((await getFrom('/api/check-log?desk=game&check=2')).status, 404)
    assert.equal((await getFrom('/api/check-log?desk=game&check=9')).status, 404)
    assert.equal((await getFrom('/api/check-log?desk=game&check=../1')).status, 404)
    assert.equal((await getFrom('/api/check-log?desk=other&check=1')).status, 404)
  } finally {
    desks.delete('game')
  }
})

test('a line keeps its place, or moves past the lines a later commit added or removed above it', () => {
  // Line 1 changed, two lines came in after line 3, and lines 8 and 9 went.
  const patch = '@@ -1 +1 @@\n-a\n+A\n@@ -3,0 +4,2 @@\n+new\n+new\n@@ -8,2 +9,0 @@\n-gone\n-gone'
  assert.equal(movedLine(patch, 1), null)
  assert.equal(movedLine(patch, 2), 2)
  assert.equal(movedLine(patch, 3), 3)
  assert.equal(movedLine(patch, 4), 6)
  assert.equal(movedLine(patch, 8), null)
  assert.equal(movedLine(patch, 9), null)
  assert.equal(movedLine(patch, 10), 10)
  assert.equal(movedLine('', 5), 5)
})

test('a line comment shows on its line, moved by a later commit, or outdated above the diff with the code it was '
  + 'made on', async () => {
  git('switch', '-q', '-c', 'agent/issue-12', 'main')
  writeFileSync(join(MAIN, 'posts.txt'), 'one\ntwo\nthree\nfour\nfive\nsix\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Set the posts')
  const first = git('rev-parse', 'HEAD')
  writeFileSync(join(MAIN, 'posts.txt'), 'zero\none\ntwo\nthree\nfour\nFIVE\nsix\n')
  git('commit', '-q', '-am', 'Fix a post')
  const second = git('rev-parse', 'HEAD')
  git('switch', '-q', 'main')
  storeFixture(['p = "me/game"', 'store.create(p, "review", "Fence posts", branch="agent/issue-12")',
    `store.comment(p, 12, "Moves down one.", path="posts.txt", line=2, head_sha="${first}")`,
    `store.comment(p, 12, "Changed since.", path="posts.txt", line=5, head_sha="${first}")`,
    `store.comment(p, 12, "Made on the newest.", path="posts.txt", line=6, head_sha="${second}")`,
    'store.comment(p, 12, "Made on a head git lost.", path="posts.txt", line=1, head_sha="0123abcd")'].join('\n'),
  storePath())
  const shown = await previewOf(desk, 12)
  assert.equal(shown?.tip, second)
  assert.deepEqual(shown?.lineComments.map(one => [one.line, one.shown, one.outdated, one.code]), [
    [2, 3, false, undefined],
    [5, null, true, [{ line: 2, text: 'two' }, { line: 3, text: 'three' }, { line: 4, text: 'four' },
      { line: 5, text: 'five' }]],
    [6, 6, false, undefined],
    [1, null, true, []],
  ])
  // Line comments stay out of the conversation, whose comments are the review's own.
  assert.deepEqual(shown?.comments, [])
})

test('the page\'s line comments wait for Send to agent, which sends them once in one comment to the agent',
  async () => {
  git('switch', '-q', '-c', 'agent/issue-13', 'main')
  writeFileSync(join(MAIN, 'hinge.txt'), 'one\ntwo\nthree\nfour\n')
  writeFileSync(join(MAIN, 'gate.txt'), 'gate\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Hang the gate')
  git('switch', '-q', 'main')
  storeFixture('store.create("me/game", "review", "Gate hinges", branch="agent/issue-13")', storePath())
  const before = await previewOf(desk, 13)
  const posted = await postLineComment(desk, 13, 'hinge.txt', 4, before?.tip ?? null, 'Oil this one.\nTwice.')
  assert.equal('error' in posted ? posted.error : posted.author.login, 'you')
  assert.ok(!('error' in await postLineComment(desk, 13, 'gate.txt', 1, null, 'Paint it.')))
  // An issue's comments take no line, a head must name a commit, and the file must be one the diff changes.
  assert.deepEqual(await postLineComment(desk, 4, 'a.txt', 1, null, 'Here.'), { status: 404, error: 'no such review' })
  assert.deepEqual(await postLineComment(desk, 13, 'hinge.txt', 1, '--output=x', 'Here.'),
    { status: 400, error: 'a head names a commit' })
  assert.deepEqual(await postLineComment(desk, 13, 'a.txt', 1, null, 'Here.'),
    { status: 409, error: '#13 doesn\'t change a.txt' })
  // A merged review takes none either.
  assert.deepEqual(await postLineComment(desk, 7, 'b.txt', 1, null, 'Here.'), { status: 409, error: '#7 is merged' })
  assert.deepEqual(unsentOf(desk, 13).map(one => [one.path, one.line, one.author]),
    [['hinge.txt', 4, 'you'], ['gate.txt', 1, 'you']])
  assert.equal(agentRequest('', unsentOf(desk, 13)), 'Please address my line comments on the diff.\n\n'
    + '**Line comments**\n\n- `hinge.txt` line 4:\n\n  > Oil this one.\n  > Twice.\n\n- `gate.txt` line 1:\n\n'
    + '  > Paint it.')
  assert.equal(agentRequest('  Just this.  ', []), 'Just this.')

  assert.deepEqual(await sendToAgent(desk, 13, 'Thanks.'), { sent: 2 })
  assert.deepEqual(unsentOf(desk, 13), [])
  assert.deepEqual((await previewOf(desk, 13))?.lineComments.map(one => one.sent), [true, true])
  assert.equal(await sendToAgent(desk, 13, ''), NOTHING_TO_SEND)
  assert.equal(await sendToAgent(desk, 4, 'An issue.'), 'no such review')
  const db = new DatabaseSync(storePath(), { readOnly: true })
  try {
    const last = db.prepare('SELECT body, to_agent, author FROM comments ORDER BY id DESC LIMIT 1').get()
    assert.equal(last?.to_agent, 1)
    assert.equal(last?.author, 'you')
    assert.match(String(last?.body), /^@agent Thanks\.\n\n\*\*Line comments\*\*\n\n- `hinge\.txt` line 4:/)
  } finally {
    db.close()
  }
})

test('a line comment counts as sent only once a Send to agent carried it, not after an @agent note or an agent\'s '
  + 'comment', async () => {
  git('switch', '-q', '-c', 'agent/issue-14', 'main')
  writeFileSync(join(MAIN, 'latch.txt'), 'latch\n')
  git('add', '.')
  git('commit', '-q', '-m', 'Fit the latch')
  git('switch', '-q', 'main')
  storeFixture('store.create("me/game", "review", "Latch", branch="agent/issue-14")', storePath())
  const tip = (await previewOf(desk, 14))?.tip ?? null
  assert.ok(!('error' in await postLineComment(desk, 14, 'latch.txt', 1, tip, 'Oil the latch.')))
  assert.ok(await postComment(desk, 14, '@agent and check the hinge'))
  storeFixture(['import os', 'os.environ["AGENT_DESK_RUN"] = "1"',
    'store.comment("me/game", 14, "Done.", to_agent=True)'].join('\n'), storePath())
  assert.deepEqual(unsentOf(desk, 14).map(one => one.body), ['Oil the latch.'])
  assert.deepEqual((await previewOf(desk, 14))?.lineComments.map(one => one.sent), [false])
  assert.deepEqual(await sendToAgent(desk, 14, ''), { sent: 1 })
  assert.deepEqual((await previewOf(desk, 14))?.lineComments.map(one => one.sent), [true])
})

test('Send to agent names the head an older line comment was made on, and its line now when a commit moved it',
  async () => {
  // Review #12's comments, from the test above: one moved, one changed since, one on the newest head, one on a head
  // git lost.
  const first = git('rev-parse', 'agent/issue-12~1').slice(0, 7)
  assert.deepEqual(await sendToAgent(desk, 12, ''), { sent: 4 })
  const db = new DatabaseSync(storePath(), { readOnly: true })
  try {
    const last = db.prepare('SELECT body FROM comments WHERE number = 12 ORDER BY id DESC LIMIT 1').get()
    assert.equal(last?.body, '@agent Please address my line comments on the diff.\n\n**Line comments**\n\n'
      + `- \`posts.txt\` line 2 at ${first}, now line 3:\n\n  > Moves down one.\n\n`
      + `- \`posts.txt\` line 5 at ${first}, a line a commit since changed:\n\n  > Changed since.\n\n`
      + '- `posts.txt` line 6:\n\n  > Made on the newest.\n\n'
      + '- `posts.txt` line 1 at 0123abc, a commit git here no longer has:\n\n  > Made on a head git lost.')
  } finally {
    db.close()
  }
})

test('Send to agent on a review whose branch no agent works sends nothing and says so', async () => {
  storeFixture(['store.create("me/game", "review", "By hand", branch="mine/fence")',
    'store.comment("me/game", 15, "Here.", path="a.txt", line=1)'].join('\n'), storePath())
  assert.equal(await sendToAgent(desk, 15, 'Please.'),
    'no agent works mine/fence, so nothing was sent; only agent/<slug> branches have one')
  assert.equal(unsentOf(desk, 15).length, 1)
})

/** A new issue in the store with `body`, by its number. */
function newIssue(body: string): number {
  storeFixture(`store.create("me/game", "issue", "Tasks", ${JSON.stringify(body)})`, storePath())
  const db = new DatabaseSync(storePath(), { readOnly: true })
  try {
    return Number((db.prepare('SELECT MAX(number) AS number FROM items WHERE project = ?').get('me/game') as
      { number: number }).number)
  } finally {
    db.close()
  }
}

/** What the server answers a POST from the page. */
async function postTo(path: string, data: unknown): Promise<{ status: number; data: Record<string, unknown> }> {
  const req = Object.assign([Buffer.from(JSON.stringify(data))], { method: 'POST', url: path, socket: OWN_SOCKET,
    headers: { host: '127.0.0.1:7480', origin: 'http://127.0.0.1:7480', 'content-type': 'application/json' } })
  let status = 0
  let text = ''
  const res = { headersSent: false, setHeader: () => res, writeHead: (code: number) => {
    status = code
    return res
  }, end: (sent: string) => { text = sent } }
  await handle(req as unknown as IncomingMessage, res as unknown as ServerResponse)
  return { status, data: text ? JSON.parse(text) as Record<string, unknown> : {} }
}

test('an issue\'s description numbers its task boxes for the page, and its comments\' boxes stay disabled', () => {
  const number = newIssue('- [ ] one\n- [x] two')
  storeFixture(`store.comment("me/game", ${number}, "- [ ] in a comment")`, storePath())
  const issue = issuePreviewOf(desk, number)
  assert.ok(issue)
  assert.match(issue.body, /<input type="checkbox" class="task-list-item-checkbox" data-task="0">/)
  assert.match(issue.body, /checked="" data-task="1">/)
  assert.doesNotMatch(issue.body, /disabled/)
  assert.equal(issue.version, bodyVersion('- [ ] one\n- [x] two'))
  assert.match(issue.comments[0]?.html ?? '', /class="task-list-item-checkbox" disabled="">/)
  assert.doesNotMatch(issue.comments[0]?.html ?? '', /data-task/)
})

test('a preview cuts its description and comments at their Decisions Needed sections, and ties an answer back to one',
  async () => {
  const number = newIssue('- [ ] first\n\n## Decisions Needed\n\n### 1. Which?\n\n'
    + '- **A. One**: the first\n- **B. Two** ✅ *my pick*: the second\n\n## Done When\n\n- [ ] last')
  storeFixture(`store.comment("me/game", ${number}, "## Decisions Needed\\n\\n- Older layout?")`, storePath())
  const posted = await postComment(desk, number,
    'Answers to the Decisions Needed in [the description](#issue-body):\n\n- **1B.** Two')
  assert.deepEqual(posted?.answers, { anchor: 'issue-body', picks: ['1B'] })
  const issue = issuePreviewOf(desk, number)
  assert.ok(issue?.bodyParts)
  const [before, section, after] = issue.bodyParts
  assert.ok('html' in before && 'section' in section && 'html' in after)
  assert.match(before.html, /data-task="0"/)
  assert.match(after.html, /data-task="1"/)
  assert.equal(section.section.anchor, 'issue-body')
  assert.deepEqual(section.section.decisions[0]?.options.map(one => [one.letter, one.label, one.pick]),
    [['A', 'One', false], ['B', 'Two', true]])
  const [question, answer] = issue.comments
  assert.ok(question && answer)
  const older = question.parts?.find(part => 'section' in part)
  assert.ok(older && 'section' in older)
  assert.equal(older.section.anchor, `comment-${question.id}`)
  assert.deepEqual(older.section.decisions, [])
  assert.equal(question.answers, undefined)
  assert.equal(answer.id, posted && 'id' in posted ? posted.id : -1)
  assert.deepEqual(answer.answers, { anchor: 'issue-body', picks: ['1B'] })
  assert.equal(answer.parts, undefined)
  // A description without the section carries no parts.
  assert.equal(issuePreviewOf(desk, newIssue('Plain.'))?.bodyParts, undefined)
})

test('an issue closed as a duplicate shows as one with its original, and one closed for another reason with none',
  () => {
  const copy = newIssue('Same as the fence.')
  const done = newIssue('Done.')
  storeFixture([`store.link("me/game", ${copy}, 4, "duplicate")`,
    `store.set_state("me/game", ${copy}, "closed", reason="duplicate")`,
    `store.link("me/game", ${done}, 4, "duplicate")`,
    `store.set_state("me/game", ${done}, "closed", reason="completed")`].join('\n'), storePath())
  const issue = issuePreviewOf(desk, copy)
  assert.equal(issue?.state, 'duplicate')
  assert.deepEqual(issue?.duplicateOf, { number: 4, title: 'Paint the fence', url: '#game:4' })
  assert.equal(hovercardOf(desk, copy)?.state, 'duplicate')
  assert.equal(issuePreviewOf(desk, done)?.state, 'completed')
  assert.equal(issuePreviewOf(desk, done)?.duplicateOf, undefined)
})

test('an issue\'s preview lists the reviews that fix it, oldest first, each with its state', () => {
  assert.deepEqual(issuePreviewOf(desk, 4)?.reviews.map(one => [one.number, one.title, one.state]),
    [[6, 'Paint it', 'open']])
  assert.deepEqual(issuePreviewOf(desk, 1)?.reviews, [])
  const number = newIssue('Mend the fence.')
  storeFixture([
    'p = "me/game"',
    `store.create(p, "review", "Mend it badly", branch="agent/issue-${number}", fixes=[${number}])`,
    `store.set_state(p, ${number + 1}, "closed")`,
    `store.create(p, "review", "Mend it", branch="agent/issue-${number}", fixes=[${number}])`,
    `store.merge(p, ${number + 2}, "abc1234")`,
    `store.create(p, "review", "Mend it again", branch="agent/issue-${number}", draft=True, fixes=[${number}])`,
  ].join('\n'), storePath())
  const reviews = issuePreviewOf(desk, number)?.reviews ?? []
  assert.deepEqual(reviews.map(one => [one.number, one.title, one.state]), [[number + 1, 'Mend it badly', 'closed'],
    [number + 2, 'Mend it', 'merged'], [number + 3, 'Mend it again', 'draft']])
  assert.match(reviews[0]?.at ?? '', /^\d{4}-\d\d-\d\dT/)
})

test('an issue\'s comment box sends its text to the agent through /api/comment, and the server refuses that on a '
  + 'closed issue or a review', async () => {
  desks.set('game', desk)
  try {
    const idea = newIssue('Maybe a map.')
    const worked = newIssue('A queued idea.')
    const plain = newIssue('Fix the bridge.')
    const closed = newIssue('An old bug.')
    storeFixture([`store.edit("me/game", ${idea}, add_labels=["idea"])`,
      `store.edit("me/game", ${worked}, add_labels=["idea"])`,
      `store.set_state("me/game", ${worked}, "queued")`,
      `store.set_state("me/game", ${closed}, "closed", reason="completed")`].join('\n'), storePath())
    assert.equal(issuePreviewOf(desk, idea)?.idea, true)
    // An idea an agent works, a plain issue, and a review are no ideas.
    assert.equal(issuePreviewOf(desk, worked)?.idea, false)
    assert.equal(issuePreviewOf(desk, 4)?.idea, false)

    const sent = await postTo('/api/comment', { desk: 'game', number: idea, text: 'What would this take?',
      toAgent: true })
    assert.equal(sent.status, 201)
    // The page shows the comment as the store kept it, with the mention Send to Agent adds.
    assert.equal((sent.data as { comment: { html: string } }).comment.html, '<p>@agent What would this take?</p>\n')
    const asked = await postTo('/api/comment', { desk: 'game', number: idea, text: 'Ask @agent about roads.',
      toAgent: true })
    assert.equal((asked.data as { comment: { html: string } }).comment.html, '<p>Ask @agent about roads.</p>\n')
    // An open issue that is no idea takes Send to Agent too, whether in the backlog or queued.
    for (const number of [plain, worked]) {
      const reply = await postTo('/api/comment', { desk: 'game', number, text: 'Yes, keep it.', toAgent: true })
      assert.equal(reply.status, 201)
      assert.equal((reply.data as { comment: { html: string } }).comment.html, '<p>@agent Yes, keep it.</p>\n')
    }
    // Comment, without toAgent, sends a typed mention to the agent and leaves any other comment plain.
    assert.equal((await postTo('/api/comment', { desk: 'game', number: plain, text: '@agent and the rail.' })).status,
      201)
    assert.equal((await postTo('/api/comment', { desk: 'game', number: plain, text: 'Just a note.' })).status, 201)
    assert.deepEqual(await postTo('/api/comment', { desk: 'game', number: closed, text: 'Think it over.',
      toAgent: true }),
    { status: 409, data: { error: `#${closed} is closed, so Send to Agent from its comment box starts nothing` } })
    for (const number of [6, 7, 999]) {
      assert.deepEqual(await postTo('/api/comment', { desk: 'game', number, text: 'Think it over.', toAgent: true }),
        { status: 409,
          data: { error: `#${number} isn't an issue, so Send to Agent from its comment box starts nothing` } })
    }
    const db = new DatabaseSync(storePath(), { readOnly: true })
    try {
      const sentTo = (number: number) => db.prepare(
        'SELECT number, author, body, to_agent FROM comments WHERE number = ? ORDER BY id').all(number)
        .map(row => ({ ...row }))
      assert.deepEqual(sentTo(idea),
        [{ number: idea, author: 'you', body: '@agent What would this take?', to_agent: 1 },
          { number: idea, author: 'you', body: 'Ask @agent about roads.', to_agent: 1 }])
      assert.deepEqual(sentTo(plain),
        [{ number: plain, author: 'you', body: '@agent Yes, keep it.', to_agent: 1 },
          { number: plain, author: 'you', body: '@agent and the rail.', to_agent: 1 },
          { number: plain, author: 'you', body: 'Just a note.', to_agent: 0 }])
      assert.equal(db.prepare('SELECT COUNT(*) AS n FROM comments WHERE body LIKE ?').get('%Think it over.%')?.n, 0)
    } finally {
      db.close()
    }
  } finally {
    desks.delete('game')
  }
})

test('a comment the dispatcher answered with a run, and each line comment it quoted, name the run for the page\'s 👀',
  async () => {
  const eyes = { ...desk, repo: 'me/eyes' }
  const newest = 'db.execute("SELECT MAX(id) FROM events").fetchone()[0]'
  storeFixture(['p = "me/eyes"', 'store.create(p, "issue", "Paint the fence")',
    'store.create(p, "review", "Fence", branch="agent/issue-1")',
    `store.comment_answered(None, None, ${newest}, "start")`,
    `c = store.comment(p, 1, "@agent paint it", to_agent=True); store.comment_answered(p, 1, ${newest}, "run", c, 157)`,
    'store.comment(p, 1, "Looks right.")',
    `c = store.comment(p, 1, "@agent again", to_agent=True); store.comment_answered(p, 1, ${newest}, "ignored", c)`,
    'line = store.comment(p, 2, "Oil it.", path="gate.txt", line=1, head_sha="abc")',
    'store.comment(p, 2, "Paint it.", path="gate.txt", line=2, head_sha="abc")',
    `c = store.comment(p, 2, "Fix.", to_agent=True, quotes=[line]); store.comment_answered(p, 2, ${newest}, "run", c, 158)`,
  ].join('\n'), storePath())
  const issue = issuePreviewOf(eyes, 1)
  assert.deepEqual(issue?.comments.map(one => one.run), [157, undefined, undefined])
  assert.equal(issue?.waiting, false)
  const before = await previewOf(eyes, 2)
  assert.deepEqual(before?.comments.map(one => one.run), [158])
  assert.deepEqual(before?.lineComments.map(one => [one.html, one.sent, one.run]),
    [['<p>Oil it.</p>\n', true, 158], ['<p>Paint it.</p>\n', false, undefined]])
  assert.equal(before?.waiting, false)

  // A comment sent since waits for its answer, and names no run until the dispatcher asks for one.
  assert.ok(await postComment(eyes, 2, '@agent and the latch'))
  const asked = await previewOf(eyes, 2)
  assert.deepEqual(asked?.comments.map(one => one.run), [158, undefined])
  assert.equal(asked?.waiting, true)
  storeFixture(`store.comment_answered("me/eyes", 2, ${newest}, "run",`
    + ' db.execute("SELECT MAX(id) FROM comments").fetchone()[0], 159)', storePath())
  const answered = await previewOf(eyes, 2)
  assert.deepEqual(answered?.comments.map(one => one.run), [158, 159])
  assert.equal(answered?.waiting, false)
})
