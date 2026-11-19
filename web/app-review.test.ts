// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { KEYS, ui } from './app-state.js'
import { fileAt, fileTree, followMark, headerReviewButtons, lineKey, markFileInView, pinnedHeight, placeComments,
  readingLine, REVIEW_KEYS, reviewButtons, scrollToShow, TreeRows, unsentCount } from './app-review.js'

const FILES = [
  { path: 'gate.txt', additions: 1, deletions: 1, lines: [
    { kind: 'hunk', text: '@@ -1,3 +1,3 @@' },
    { kind: 'same', old: 1, new: 1, text: 'post' },
    { kind: 'del', old: 2, text: 'rusty hinge' },
    { kind: 'add', new: 2, text: 'oiled hinge' },
    { kind: 'same', old: 3, new: 3, text: 'latch' },
  ] },
  { path: 'pic.png', additions: 0, deletions: 0 },
]

const comment = (id: number, path: string, line: number, shown: number | null, outdated = false, sent = false) =>
  ({ id, path, line, shown, outdated, sent, author: { login: 'you' }, at: '', html: '' })

test('a line comment shows under its line of the new file, and one the diff can\'t place shows above the diff', () => {
  const comments = [comment(1, 'gate.txt', 2, 2), comment(2, 'gate.txt', 2, 2), comment(3, 'gate.txt', 9, 3),
    comment(4, 'gate.txt', 5, null, true), comment(5, 'gate.txt', 40, 40), comment(6, 'pic.png', 1, 1),
    comment(7, 'gone.txt', 1, 1)]
  const { onLines, above } = placeComments(FILES, comments)
  assert.deepEqual([...onLines].map(([where, list]) => [where, list.map(one => one.id)]),
    [['gate.txt:2', [1, 2]], ['gate.txt:3', [3]]])
  // Outdated, on a line past the hunks, in a binary file, and in a file the diff no longer changes.
  assert.deepEqual(above.map(one => one.id), [4, 5, 6, 7])
  assert.deepEqual(placeComments([], []), { onLines: new Map(), above: [] })
})

test('a review counts the line comments no Send to agent has sent, and offers the buttons its state allows', () => {
  assert.equal(unsentCount({ lineComments: [comment(1, 'a', 1, 1, false, true), comment(2, 'a', 2, 2),
    comment(3, 'a', 3, null, true)] }), 2)
  assert.equal(unsentCount({}), 0)
  assert.deepEqual(reviewButtons('open'), ['send', 'close'])
  assert.deepEqual(reviewButtons('draft'), ['send', 'close'])
  assert.deepEqual(reviewButtons('merged'), [])
  assert.deepEqual(reviewButtons('closed'), [])
  assert.equal(lineKey('game', 6, 'src/a b.ts', 12), 'game:6:src/a b.ts:12')
})

test('an open or draft review\'s header offers Close Without Merging, and a merged or closed one none', () => {
  assert.deepEqual(headerReviewButtons('open'), ['close'])
  assert.deepEqual(headerReviewButtons('draft'), ['close'])
  assert.deepEqual(headerReviewButtons('merged'), [])
  assert.deepEqual(headerReviewButtons('closed'), [])
})

test('each header button of a review has a key no card action or page key takes', () => {
  // The keys app.js handles itself: search, help, history, stepping, showing a card, and confirming.
  const taken = new Set([...Object.values(KEYS), '/', '?', 'h', 'H', 'j', 'k', 'o', 'y'])
  for (const action of headerReviewButtons('open')) {
    assert.ok(REVIEW_KEYS[action], action)
    assert.ok(!taken.has(REVIEW_KEYS[action]), REVIEW_KEYS[action])
  }
  assert.equal(new Set(Object.values(REVIEW_KEYS)).size, Object.values(REVIEW_KEYS).length)
})

type Row = { kind: string; name: string; path?: string; file?: { path: string }; rows?: Row[] }

/** A tree's rows as names, a folder's as its name and its own rows, for comparing whole trees. */
const shape = (rows: Row[]): unknown[] => rows.map(row => (row.kind === 'folder'
  ? [`${row.name}/`, shape(row.rows ?? [])] : row.name))

const TREE_FILES = ['web/app.js', 'README.md', 'inbox/hooks/board.ts', 'inbox/hooks/board.test.ts', 'lib/run.sh',
  'web/vendor/preact.js', 'Justfile', 'inbox/hooks/units/a.ts', 'docs/deep/er/notes.md', 'lib/item10.sh',
  'lib/item9.sh'].map(path => ({ path, additions: 1, deletions: 0 }))

test('the file tree nests files under their folders, folders first and then by name, joining lone folder chains', () => {
  assert.deepEqual(shape(fileTree(TREE_FILES)), [
    // docs holds only deep, which holds only er, so the three join; inbox holds only hooks, which holds a file too.
    ['docs/deep/er/', ['notes.md']],
    ['inbox/hooks/', [['units/', ['a.ts']], 'board.test.ts', 'board.ts']],
    // Case aside, and numbers by their value.
    ['lib/', ['item9.sh', 'item10.sh', 'run.sh']],
    ['web/', [['vendor/', ['preact.js']], 'app.js']],
    'Justfile', 'README.md',
  ])
  const tree = fileTree(TREE_FILES) as Row[]
  assert.deepEqual(tree.slice(0, 2).map(row => [row.kind, row.name, row.path]),
    [['folder', 'docs/deep/er', 'docs/deep/er'], ['folder', 'inbox/hooks', 'inbox/hooks']])
  assert.equal(tree[1]?.rows?.[0]?.path, 'inbox/hooks/units')
  assert.equal(tree[1]?.rows?.[1]?.file?.path, 'inbox/hooks/board.test.ts')
  assert.deepEqual(shape(fileTree([{ path: 'a/b/c.txt' }])), [['a/b/', ['c.txt']]])
  assert.deepEqual(fileTree([]), [])
})

test('the file tree\'s filter keeps the files whose path holds its text, case aside, and the folders that hold them', () => {
  assert.deepEqual(shape(fileTree(TREE_FILES, ' BOARD ')), [['inbox/hooks/', ['board.test.ts', 'board.ts']]])
  // A folder's name matches too, keeping every file under it, and a chain joins anew once its other files go.
  assert.deepEqual(shape(fileTree(TREE_FILES, 'web/')), [['web/', [['vendor/', ['preact.js']], 'app.js']]])
  assert.deepEqual(shape(fileTree(TREE_FILES, 'units')), [['inbox/hooks/units/', ['a.ts']]])
  assert.deepEqual(fileTree(TREE_FILES, 'nowhere'), [])
  assert.equal(fileTree(TREE_FILES, '  ').length, fileTree(TREE_FILES).length)
})

test('the tree marks the last file whose box reached the line below the files bar, or the first before any has', () => {
  const boxes = [{ id: 'a', top: 300 }, { id: 'b', top: 900 }, { id: 'c', top: 1500 }]
  assert.equal(fileAt(boxes, 100), 'a')
  assert.equal(fileAt(boxes, 300), 'a')
  assert.equal(fileAt(boxes, 901), 'b')
  assert.equal(fileAt([{ id: 'a', top: -4000 }, { id: 'b', top: -20 }, { id: 'c', top: 60 }], 64), 'c')
  assert.equal(fileAt([], 64), null)
})

/** A panel's style holding the heights its scroll handler measured, by custom property. */
const measured = (heights: Record<string, string>) => ({ getPropertyValue: (name: string) => heights[name] ?? '' })

test('a file jumped to from the tree lands below the compact bar, the tabs, and the files bar, as they wrap', () => {
  const heights = (bar: string, tabs: string, files: string) =>
    measured({ '--bar-height': bar, '--tabs-height': tabs, '--files-bar-height': files })
  assert.equal(pinnedHeight(heights('56px', '41px', '44px')), 141)
  assert.equal(pinnedHeight(heights('97px', '82px', '80px')), 259)
  // Before the first measure, as on a card without tabs, a missing height counts as none.
  assert.equal(pinnedHeight(measured({ '--bar-height': '56px' })), 56)
  assert.equal(pinnedHeight(measured({})), 0)
})

/** A page that scrolls `range` pixels in all, `scrollTop` of them so far, in a window 1000 pixels tall. */
const pageAt = (scrollTop: number, range = 6000) => ({ scrollTop, scrollHeight: range + 1000, clientHeight: 1000 })

/**
 * A Files Changed panel whose files bar's bottom edge sits at `bar` pixels down the window, pinned or not, with a box
 * for each file at its top. A panel without `tree` shows no file tree.
 */
function filesPanel(bar: number | null, tops: Record<string, number>, tree = true) {
  const rect = (top: number) => () => ({ top, bottom: top })
  return {
    querySelector: (selector: string) => ({
      '.files-bar': bar === null ? null : { getBoundingClientRect: rect(bar) },
      '.file-tree': tree ? {} : null,
    })[selector] ?? null,
    querySelectorAll: () => Object.entries(tops)
      .map(([file, top]) => ({ dataset: { file }, getBoundingClientRect: rect(top) })),
  }
}

/** Marks the file in view of a panel on a page scrolled as `pageAt` says, and returns it. */
function marked(bar: number | null, tops: Record<string, number>, page = pageAt(2000), tree = true) {
  markFileInView(filesPanel(bar, tops, tree) as never, page as never)
  return ui.fileInView
}

test('the tree marks the file whose box reaches just below the files bar, pinned on a panel or on the window', () => {
  ui.fileInView = null
  ui.fileJump = null
  // Pinned in a panel that sits 80 pixels down the window: a file jumped to stops 8 pixels below the bar.
  assert.equal(marked(221, { a: -900, b: 229, c: 1400 }), 'b')
  assert.equal(marked(221, { a: -900, b: 238, c: 1400 }), 'a')
  // On a narrow window the page scrolls, and the bar pins near the window's top while the panel's top is far above.
  assert.equal(marked(180, { a: -3000, b: -1200, c: 188 }), 'c')
  // At the top of the tab, before anything pins, the first file is in view.
  assert.equal(marked(400, { a: 404, b: 900 }, pageAt(0)), 'a')
  ui.fileInView = 'b'
  assert.equal(marked(221, { a: 229 }, pageAt(2000), false), 'b')
  assert.equal(marked(null, { a: 229 }), 'b')
  ui.fileInView = null
})

test('over the last screen of scroll the reading line slides from below the files bar to the bottom', () => {
  // A window 1000 pixels tall whose files bar ends at 200, with far more to scroll than a screen.
  assert.equal(readingLine(200, 1000, 5000, 6000), 200)
  assert.equal(readingLine(200, 1000, 800, 6000), 200)
  assert.equal(readingLine(200, 1000, 400, 6000), 600)
  assert.equal(readingLine(200, 1000, 0, 6000), 1000)
  // A diff shorter than a screen past its top slides over all of its scroll, so its first file is marked at the top.
  assert.equal(readingLine(200, 1000, 300, 300), 200)
  assert.equal(readingLine(200, 1000, 150, 300), 600)
  assert.equal(readingLine(200, 1000, 0, 300), 1000)
  // Nothing to scroll keeps the line below the files bar.
  assert.equal(readingLine(200, 1000, 0, 0), 200)
})

test('the last files, which can\'t reach the files bar, are each marked in turn, and the last at the bottom', () => {
  ui.fileInView = null
  ui.fileJump = null
  // The files bar ends at 200; at the bottom of the panel, y, z, and the short last file sit in its last screen.
  const tops = (left: number) => ({ x: -400 + left, y: 300 + left, z: 600 + left, last: 900 + left })
  assert.equal(marked(184, tops(800), pageAt(6000 - 800)), 'x')
  // With 500 pixels left the line sits at 500, which y's box, now at 800, hasn't reached.
  assert.equal(marked(184, tops(500), pageAt(6000 - 500)), 'x')
  assert.equal(marked(184, tops(300), pageAt(6000 - 300)), 'y')
  assert.equal(marked(184, tops(150), pageAt(6000 - 150)), 'z')
  assert.equal(marked(184, tops(0), pageAt(6000)), 'last')
  ui.fileInView = null
})

test('a file picked in the tree stays marked until the panel scrolls from where the pick left it', () => {
  ui.fileInView = 'z'
  // A pick of z took the page to its bottom, where the line would mark the last file.
  ui.fileJump = { id: 'z', scrollTop: 6000 }
  const tops = { x: -400, y: 300, z: 600, last: 900 }
  assert.equal(marked(184, tops, pageAt(6000)), 'z')
  // A redraw that leaves the scroll alone keeps it.
  assert.equal(marked(184, tops, pageAt(6000)), 'z')
  // A scroll gives the mark back to the reading line, and forgets the pick.
  assert.equal(marked(184, { x: -380, y: 320, z: 620, last: 920 }, pageAt(5980)), 'last')
  assert.equal(ui.fileJump, null)
  assert.equal(marked(184, tops, pageAt(6000)), 'last')
  // A pick whose file is gone, as on another review, gives way too.
  ui.fileJump = { id: 'gone', scrollTop: 6000 }
  assert.equal(marked(184, tops, pageAt(6000)), 'last')
  assert.equal(ui.fileJump, null)
  ui.fileInView = null
})

test('a list scrolls just enough to show a row, and not at all while it shows whole', () => {
  const view = { top: 100, bottom: 500 }
  assert.equal(scrollToShow(view, { top: 100, bottom: 128 }), 0)
  assert.equal(scrollToShow(view, { top: 472, bottom: 500 }), 0)
  assert.equal(scrollToShow(view, { top: 490, bottom: 518 }), 18)
  assert.equal(scrollToShow(view, { top: 1000, bottom: 1028 }), 528)
  assert.equal(scrollToShow(view, { top: 80, bottom: 108 }), -20)
  // A row taller than the view shows from its top.
  assert.equal(scrollToShow(view, { top: 300, bottom: 800 }), 200)
})

/**
 * A wide file tree 400 pixels tall at 100 pixels down the window, under a filter box 36 pixels tall, scrolled
 * `scrollTop` down rows 28 pixels tall, with `marked` the row the tree marks.
 */
function treePanel(marked: number | null, mark = `row-${marked}`) {
  const tree = {
    scrollTop: 0,
    clientTop: 0,
    clientHeight: 400,
    getBoundingClientRect: () => ({ top: 100, bottom: 500 }),
    querySelector: (selector: string) => (selector === '.tree-filter'
      ? { getBoundingClientRect: () => ({ top: 100 - tree.scrollTop, bottom: 136 - tree.scrollTop }) }
      : marked === null ? null : {
        dataset: { mark },
        getBoundingClientRect: () => ({ top: 136 + 28 * marked - tree.scrollTop,
          bottom: 164 + 28 * marked - tree.scrollTop }),
      }),
  }
  return { tree, panel: { querySelector: () => tree } }
}

test('the tree scrolls just enough to show its marked row when the mark moves, and leaves its scroll alone after', () => {
  const far = treePanel(30)
  followMark(far.panel as never, 0, 1000)
  // Row 30 ends 1004 pixels down the rows, 504 past the tree's bottom.
  assert.equal(far.tree.scrollTop, 504)
  // The person scrolls the tree back to its top, and the mark stays on row 30: the tree stays where they left it.
  far.tree.scrollTop = 0
  followMark(far.panel as never, 0, 1000)
  assert.equal(far.tree.scrollTop, 0)
  // A row in view moves the mark without a scroll.
  const near = treePanel(2)
  followMark(near.panel as never, 0, 1000)
  assert.equal(near.tree.scrollTop, 0)
  // A row above the view, under where the filter box was, scrolls up to just below the box's place.
  const above = treePanel(3)
  above.tree.scrollTop = 200
  followMark(above.panel as never, 0, 1000)
  assert.equal(above.tree.scrollTop, 120)
  // A filter that hides the marked row, then shows it again, shows it anew.
  followMark(treePanel(null).panel as never, 0, 1000)
  const back = treePanel(3)
  back.tree.scrollTop = 200
  followMark(back.panel as never, 0, 1000)
  assert.equal(back.tree.scrollTop, 120)
  // A tree that runs past the panel's bottom, at 400, shows its rows down to there.
  const clipped = treePanel(10)
  followMark(clipped.panel as never, 0, 400)
  assert.equal(clipped.tree.scrollTop, 44)
  // A tree scrolled almost out of the panel, as above a narrow panel's diffs, scrolls within its own box.
  const away = treePanel(14)
  followMark(away.panel as never, 0, 120)
  assert.equal(away.tree.scrollTop, 56)
  // A tree on short diffs scrolls under the pinned files bar, which ends at 250, so rows above that are hidden.
  const under = treePanel(6)
  under.tree.scrollTop = 100
  followMark(under.panel as never, 250, 1000)
  assert.equal(under.tree.scrollTop, 54)
})

test('a folded folder holding the file in view takes the mark, and an open one leaves it to the file', () => {
  type Node = { type: unknown; props: Record<string, unknown> }
  // The tree's rows, drawing the nested levels it holds.
  const rows = (tree: unknown): Node[] => {
    if (Array.isArray(tree)) {
      return tree.flatMap(rows)
    }
    if (!tree || typeof tree !== 'object') {
      return []
    }
    const node = tree as Node
    if (typeof node.type === 'function') {
      return rows(node.type(node.props))
    }
    return [...String(node.props.class ?? '').startsWith('tree-row') ? [node] : [], ...rows(node.props.children)]
  }
  const drawn = () => rows(TreeRows({ desk: 'd', pr: { number: 7 }, rows: fileTree(TREE_FILES), depth: 0 }))
    .filter(row => row.props['aria-current'] === 'true').map(row => [row.props.title, row.props['data-mark']])
  ui.fileInView = 'd:7:lib/run.sh'
  ui.treeFolded = new Set(['d:7:lib'])
  assert.deepEqual(drawn(), [['lib', 'd:7:lib/']])
  ui.treeFolded = new Set()
  assert.deepEqual(drawn(), [['lib/run.sh', 'd:7:lib/run.sh']])
  // A folded folder beside the file leaves the mark to it.
  ui.fileInView = 'd:7:web/app.js'
  ui.treeFolded = new Set(['d:7:web/vendor'])
  assert.deepEqual(drawn(), [['web/app.js', 'd:7:web/app.js']])
  ui.fileInView = null
  ui.treeFolded = new Set()
})
