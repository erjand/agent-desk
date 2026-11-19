// Run with: node --test web/
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { renderMarkdown, safeUrl, sanitize, taskStates, toggledTask } from './markdown.ts'

const render = (text: string) => renderMarkdown(text, number => `/item/${number}`)

test('headings, emphasis, strikethrough, and block quotes render as GitHub renders them', () => {
  assert.equal(render('# One\n\n### Three'), '<h1>One</h1>\n<h3>Three</h3>\n')
  assert.equal(render('**bold** _it_ ~~gone~~'), '<p><strong>bold</strong> <em>it</em> <del>gone</del></p>\n')
  assert.equal(render('> said'), '<blockquote>\n<p>said</p>\n</blockquote>\n')
})

test('links keep their addresses and titles, an image on the page keeps its source, and a bare URL becomes a link',
  () => {
  assert.equal(render('[docs](https://example.com/a "Docs")'),
    '<p><a href="https://example.com/a" title="Docs">docs</a></p>\n')
  assert.equal(render('![shot](/api/asset?desk=a&ref=b&path=c.png)'), '<p><a class="picture-link" '
    + 'href="/api/asset?desk=a&amp;ref=b&amp;path=c.png"><img src="/api/asset?desk=a&amp;ref=b&amp;path=c.png" '
    + 'alt="shot"></a></p>\n')
  assert.equal(render('see https://example.com'), '<p>see <a href="https://example.com">https://example.com</a></p>\n')
})

test('fenced and inline code keep their text, with HTML inside escaped', () => {
  assert.equal(render('```ts\nconst a = 1 < 2\n```'),
    '<pre><code class="language-ts">const a = 1 &lt; 2\n</code></pre>\n')
  assert.equal(render('run `just test`'), '<p>run <code>just test</code></p>\n')
})

test('a script tag inside a code fence shows as text and runs nothing', () => {
  assert.equal(render('```\n<script>alert(1)</script>\n```'),
    '<pre><code>&lt;script&gt;alert(1)&lt;/script&gt;\n</code></pre>\n')
})

test('ordered, unordered, and nested lists render with their start', () => {
  assert.equal(render('- a\n- b'), '<ul>\n<li>a</li>\n<li>b</li>\n</ul>\n')
  assert.equal(render('3. c\n4. d'), '<ol start="3">\n<li>c</li>\n<li>d</li>\n</ol>\n')
  assert.equal(render('1. one\n   - inner'), '<ol>\n<li>one<ul>\n<li>inner</li>\n</ul>\n</li>\n</ol>\n')
})

test('a task list carries GitHub\'s classes and disabled checkboxes', () => {
  assert.equal(render('- [x] done\n- [ ] open'), '<ul class="contains-task-list">\n'
    + '<li class="task-list-item"><input type="checkbox" class="task-list-item-checkbox" checked="" disabled="">'
    + ' done</li>\n<li class="task-list-item"><input type="checkbox" class="task-list-item-checkbox" disabled="">'
    + ' open</li>\n</ul>\n')
})

test('an issue\'s task boxes take clicks, each numbered in the order marked reads its task items', () => {
  const text = '- [ ] one\n- [x] two\n  - [ ] inner\n\n```\n- [ ] code\n```\n\n> - [X] quoted\n\n- [ ]\n- not [ ] a task'
  const box = (task: number, checked = false) => '<input type="checkbox" class="task-list-item-checkbox"'
    + `${checked ? ' checked=""' : ''} data-task="${task}">`
  assert.equal(renderMarkdown(text, number => `/item/${number}`, undefined, { tasks: true }),
    `<ul class="contains-task-list">\n<li class="task-list-item">${box(0)} one</li>\n`
    + `<li class="task-list-item">${box(1, true)} two<ul class="contains-task-list">\n`
    + `<li class="task-list-item">${box(2)} inner</li>\n</ul>\n</li>\n</ul>\n`
    + '<pre><code>- [ ] code\n</code></pre>\n<blockquote>\n<ul class="contains-task-list">\n'
    + `<li class="task-list-item">${box(3, true)} quoted</li>\n</ul>\n</blockquote>\n`
    + '<ul>\n<li>[ ]</li>\n<li>not [ ] a task</li>\n</ul>\n')
  assert.deepEqual(taskStates(text), [false, true, false, true])
  // A comment renders the same list with every box disabled and unnumbered.
  const comment = render(text)
  assert.equal(comment.match(/disabled=""/g)?.length, 4)
  assert.doesNotMatch(comment, /data-task/)
})

test('a box the body writes as HTML stays disabled and unnumbered, whatever number it claims', () => {
  const tasks = (text: string) => renderMarkdown(text, number => `/item/${number}`, undefined, { tasks: true })
  assert.equal(tasks('<input type="checkbox" data-task="0">'), '<input type="checkbox" disabled="">')
  assert.equal(tasks('- [ ] real <input type="checkbox" data-task="0">'), '<ul class="contains-task-list">\n'
    + '<li class="task-list-item"><input type="checkbox" class="task-list-item-checkbox" data-task="0"> real '
    + '<input type="checkbox" disabled=""></li>\n</ul>\n')
  assert.equal(sanitize('<input type="checkbox" data-task="0">', undefined, 'key'),
    '<input type="checkbox" disabled="">')
  assert.equal(sanitize('<input type="checkbox" data-task="key:2">', undefined, 'key'),
    '<input type="checkbox" data-task="2">')
  assert.equal(sanitize('<input type="checkbox" data-task="key:2">'), '<input type="checkbox" disabled="">')
  assert.equal(sanitize('<input type="text" data-task="key:2">', undefined, 'key'), '')
})

test('a tick rewrites the marker of the task item it names, and no marker marked doesn\'t read as one', () => {
  const text = '```\n- [ ] fenced\n```\n\n    - [ ] indented\n\n- [ ] see [ ] here\n- [x] two\n  * [ ] inner\n'
    + '- [ ]\n> 1. [X] quoted\n'
  assert.deepEqual(taskStates(text), [false, true, false, true])
  assert.equal(toggledTask(text, 0, true), text.replace('- [ ] see', '- [x] see'))
  assert.equal(toggledTask(text, 1, false), text.replace('- [x] two', '- [ ] two'))
  assert.equal(toggledTask(text, 2, true), text.replace('* [ ] inner', '* [x] inner'))
  assert.equal(toggledTask(text, 3, false), text.replace('[X] quoted', '[ ] quoted'))
  // A box already as asked leaves the text alone, and a number past the last task item names none.
  assert.equal(toggledTask(text, 1, true), text)
  assert.equal(toggledTask(text, 4, true), null)
  assert.equal(toggledTask(text, -1, true), null)
  assert.equal(toggledTask(text, 0.5, true), null)
  assert.equal(toggledTask('- [ ] a\r\n- [ ] b\r\n', 1, true), '- [ ] a\r\n- [x] b\r\n')
  // marked reads no task item where the marker starts the line after its bullet.
  assert.equal(toggledTask('-\n  [ ] later\n', 0, true), null)
})

test('a table keeps its column alignment', () => {
  assert.equal(render('| a | b |\n|:-|-:|\n| x | y |'), '<table>\n<thead>\n<tr>\n<th align="left">a</th>\n'
    + '<th align="right">b</th>\n</tr>\n</thead>\n<tbody><tr>\n<td align="left">x</td>\n<td align="right">y</td>\n'
    + '</tr>\n</tbody></table>\n')
})

test('a details block keeps its summary and renders the Markdown inside it', () => {
  assert.equal(render('<details><summary>More</summary>\n\n**inside**\n\n</details>'),
    '<details><summary>More</summary><p><strong>inside</strong></p>\n</details>')
  assert.equal(sanitize('<details open><summary>S</summary>x</details>'),
    '<details open=""><summary>S</summary>x</details>')
})

test('#123 links to its item, and a commit SHA stays text', () => {
  assert.equal(render('Closes #123.'), '<p>Closes <a class="issue-link" href="/item/123">#123</a>.</p>\n')
  assert.equal(render('(#4, #5)'),
    '<p>(<a class="issue-link" href="/item/4">#4</a>, <a class="issue-link" href="/item/5">#5</a>)</p>\n')
  assert.equal(render('fixed in 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b and 1a2b3c4'),
    '<p>fixed in 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b and 1a2b3c4</p>\n')
  assert.equal(renderMarkdown('#7', number => `https://github.com/o/r/issues/${number}`),
    '<p><a class="issue-link" href="https://github.com/o/r/issues/7">#7</a></p>\n')
})

test('a reference stays text inside code, a link, an image\'s alt, a word, a path, or an entity', () => {
  assert.equal(render('`#1`'), '<p><code>#1</code></p>\n')
  assert.equal(render('[see #2](https://example.com)'), '<p><a href="https://example.com">see #2</a></p>\n')
  assert.equal(render('![shot #3](a.png)'), '<p><img src="a.png" alt="shot #3"></p>\n')
  assert.equal(render('a#4 b/#5 ##6 &#39;'), '<p>a#4 b/#5 ##6 &#39;</p>\n')
  assert.equal(render('<b>x</b> & "q" #8'),
    '<p><b>x</b> &amp; &quot;q&quot; <a class="issue-link" href="/item/8">#8</a></p>\n')
})

test('an image from another host becomes a link with its alt text, so the page fetches nothing from it', () => {
  assert.equal(render('![shot](https://example.com/a.png "Shot")'),
    '<p><a href="https://example.com/a.png">shot</a></p>\n')
  // An imported body's attachment, as GitHub writes one.
  assert.equal(sanitize('<img width="640" alt="Board" src="https://github.com/user-attachments/assets/1-2-3">'),
    '<a href="https://github.com/user-attachments/assets/1-2-3">Board</a>')
  assert.equal(sanitize('<img src="//evil.example/a.png">'), '//evil.example/a.png')
  assert.equal(sanitize('<img src=" HTTPS://evil.example/a.png" alt="x">'),
    '<a href=" HTTPS://evil.example/a.png">x</a>')
  assert.equal(sanitize('<img src="http://evil.example/a.png?a=1&amp;b=&quot;2&quot;">'),
    '<a href="http://evil.example/a.png?a=1&amp;b=&quot;2&quot;">'
    + 'http://evil.example/a.png?a=1&amp;b=&quot;2&quot;</a>')
  // Inside a link, which can't hold another, only the alt text stays.
  assert.equal(render('[![shot](https://example.com/a.png)](https://example.com)'),
    '<p><a href="https://example.com">shot</a></p>\n')
  assert.equal(sanitize('<img alt="none">'), '<img alt="none">')
})

test('an image from another host loads from the page when the page serves a copy of it', () => {
  const copies = (src: string) => src === 'https://example.com/a.png' ? '/api/asset?path=a.png&ref=b' : null
  assert.equal(renderMarkdown('![a](https://example.com/a.png) ![b](https://example.com/b.png)', () => '', copies),
    '<p><a class="picture-link" href="/api/asset?path=a.png&amp;ref=b"><img src="/api/asset?path=a.png&amp;ref=b" '
    + 'alt="a"></a> <a href="https://example.com/b.png">b</a></p>\n')
})

test('a picture the page serves sits inside a link to itself, which opens the viewer', () => {
  // The address `agent-desk shots` prints for a picture it saved.
  const shot = `/api/asset/${'0f'.repeat(32)}`
  assert.equal(render(`![shot](${shot})`),
    `<p><a class="picture-link" href="${shot}"><img src="${shot}" alt="shot"></a></p>\n`)
  assert.equal(sanitize(`<img src="${shot}" width="640">`),
    `<a class="picture-link" href="${shot}"><img src="${shot}" width="640"></a>`)
  // A picture inside an author's own link keeps that link alone.
  assert.equal(render(`[![shot](${shot})](https://example.com)`),
    `<p><a href="https://example.com"><img src="${shot}" alt="shot"></a></p>\n`)
  assert.equal(sanitize(`<a href="/api/asset?ref=a&amp;path=b.png"><img src="${shot}"></a>`),
    `<a href="/api/asset?ref=a&amp;path=b.png"><img src="${shot}"></a>`)
  // A picture from another host stays a link with its alt text.
  assert.equal(render('![shot](https://example.com/a.png)'), '<p><a href="https://example.com/a.png">shot</a></p>\n')
  // An author's link can't take the class, so it opens no viewer.
  assert.equal(sanitize(`<a class="picture-link" href="${shot}">x</a>`), `<a href="${shot}">x</a>`)
  // A picture at a path the page doesn't serve keeps no link.
  assert.equal(render('![shot](a.png)'), '<p><img src="a.png" alt="shot"></p>\n')
})

test('the sanitizer removes script, style, iframe, and form with everything inside them', () => {
  assert.equal(render('a <script>alert(1)</script> b'), '<p>a  b</p>\n')
  assert.equal(sanitize('<style>body{display:none}</style>x'), 'x')
  assert.equal(sanitize('<iframe src="https://example.com"></iframe>x'), 'x')
  assert.equal(sanitize('<form action="/api/act"><button>Merge</button></form>x'), 'x')
  assert.equal(sanitize('<SCRIPT >alert(1)</SCRIPT >x'), 'x')
  assert.equal(sanitize('<svg><script>alert(1)</script></svg>x'), 'x')
  assert.equal(sanitize('<script>never closed'), '')
})

test('the sanitizer removes event-handler attributes, such as onerror on an image', () => {
  assert.equal(render('<img src="/a.png" onerror="alert(1)">'), '<img src="/a.png">')
  assert.equal(sanitize('<img src=x onerror=alert(1)>'), '<img src="x">')
  assert.equal(sanitize('<p ONCLICK="alert(1)" onmouseover=\'x\'>p</p>'), '<p>p</p>')
  assert.equal(sanitize('<img/src=x/onerror=alert(1)>'), '<img src="x/onerror=alert(1)">')
})

test('the sanitizer drops a javascript: link written with mixed case and embedded whitespace', () => {
  assert.equal(render('[x](JaVaScRiPt:alert(1))'), '<p><a>x</a></p>\n')
  // marked percent-encodes a tab inside <...>, which leaves a relative path with no scheme for the browser to run.
  assert.equal(render('[x](<JaVa\tScript:alert(1)>)'), '<p><a href="JaVa%09Script:alert(1)">x</a></p>\n')
  assert.equal(render('<a href="JaVa\tScRiPt:alert(1)">x</a>'), '<p><a>x</a></p>\n')
  assert.equal(sanitize('<a href=" JaVa\nScRiPt:alert(1)">x</a>'), '<a>x</a>')
  assert.equal(sanitize('<a href="java&#x09;script:alert(1)">x</a>'), '<a>x</a>')
  assert.equal(sanitize('<a href="&#106;avascript:alert(1)">x</a>'), '<a>x</a>')
  assert.equal(sanitize('<a href="\u0001javascript:alert(1)">x</a>'), '<a>x</a>')
})

test('href and src keep http, https, and the page\'s own relative paths, and drop every other scheme', () => {
  for (const kept of ['https://example.com', 'HTTP://example.com', '/api/asset?ref=a&path=b', '#top', '?q=1',
    'a/b.png']) {
    assert.ok(safeUrl(kept), kept)
  }
  for (const dropped of ['data:text/html,<script>alert(1)</script>', 'DATA:image/png;base64,AA', 'vbscript:x',
    'javascript:x', 'mailto:a@b.c', '//evil.example', '/\\evil.example', '\\\\evil.example']) {
    assert.ok(!safeUrl(dropped), dropped)
  }
  assert.equal(sanitize('<img src="data:image/svg+xml,<svg onload=alert(1)>" alt="a">'), 'a')
  assert.equal(sanitize('<a href="/api/asset?desk=a&amp;ref=b">x</a>'), '<a href="/api/asset?desk=a&amp;ref=b">x</a>')
})

test('an entity the sanitizer leaves undecoded stays text in the address the browser reads', () => {
  assert.equal(sanitize('<a href="javascript&colon;alert(1)">x</a>'), '<a href="javascript&amp;colon;alert(1)">x</a>')
})

test('the sanitizer keeps only allow-listed tags and attributes', () => {
  assert.equal(sanitize('<p style="color:red" id="merge" class="primary">p</p>'), '<p>p</p>')
  assert.equal(sanitize('<center><font color="red">t</font></center>'), 't')
  assert.equal(sanitize('<object data="x"><embed src="x"></object><link rel="stylesheet" href="x"><meta x>t'), 't')
  assert.equal(sanitize('<base href="https://evil.example/"><textarea><b>t</b></textarea>u'), 'u')
  assert.equal(sanitize('<input type="text" value="x"><input type="checkbox">'), '<input type="checkbox" disabled="">')
  assert.equal(sanitize('<td colspan="2" rowspan="x" align="middle">c</td>'), '<td colspan="2">c</td>')
  assert.equal(sanitize('<code class="language-js evil">c</code>'), '<code class="language-js">c</code>')
  assert.equal(sanitize('<img src="a.png" width="100" height="50%" title="t &quot;q&quot;">'),
    '<img src="a.png" width="100" height="50%" title="t &quot;q&quot;">')
})

test('the sanitizer removes comments and escapes every stray angle bracket', () => {
  assert.equal(sanitize('a<!-- <script>alert(1)</script> -->b<!-->c<!--->d<?php x ?>e<!DOCTYPE html>f'), 'abcdef')
  assert.equal(sanitize('1 < 2 > 0 <3 </ x>'), '1 &lt; 2 &gt; 0 &lt;3 &lt;/ x&gt;')
  assert.equal(sanitize('<scr<script>ipt>alert(1)</script>'), 'ipt&gt;alert(1)')
  assert.equal(sanitize('a & b &amp; c &#39;'), 'a &amp; b &amp; c &#39;')
})

test('the sanitizer closes the tags it keeps, and ignores end tags it never opened', () => {
  assert.equal(sanitize('<blockquote><p><b>x'), '<blockquote><p><b>x</b></p></blockquote>')
  assert.equal(sanitize('</div></td>x<div><span>y</div>z'), 'x<div><span>y</span></div>z')
  assert.equal(sanitize('<a href="https://example.com" title="unfinished'), '')
})

test('markup that changes meaning when parsed twice comes out inert', () => {
  assert.equal(sanitize('<noscript><p title="</noscript><img src=x onerror=alert(1)>">'),
    '<img src="x">"&gt;')
  assert.equal(sanitize('<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>'), '')
  assert.equal(sanitize('<a href="https://example.com" title="a&quot;onmouseover=&quot;alert(1)">x</a>'),
    '<a href="https://example.com" title="a&quot;onmouseover=&quot;alert(1)">x</a>')
})
