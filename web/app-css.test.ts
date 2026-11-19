// Run with: node --test web/
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// Each rule's selector and its z-index, from the page's style sheets.
function zIndexes(): Map<string, number> {
  const found = new Map<string, number>()
  for (const file of ['app.css', 'app-header.css', 'app-detail.css', 'app-chat.css']) {
    const css = readFileSync(new URL(file, import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const z = /(?:^|;)\s*z-index:\s*(-?\d+)/.exec(body)
      if (z) {
        found.set(selector.trim(), Number(z[1]))
      }
    }
  }
  return found
}

test('the keyboard help paints over the sticky bars and under hovercards and tips', () => {
  const z = zIndexes()
  const help = z.get('.help')
  assert.ok(help !== undefined, '.help sets a z-index')
  for (const sticky of ['.search', '.box > .box-head, .box > .box-heading', '.gh-main > .tabnav', '.files-bar',
    '.sticky-holder']) {
    assert.ok(z.has(sticky), `${sticky} sets a z-index`)
    assert.ok(help > z.get(sticky)!, `.help is above ${sticky}`)
  }
  assert.ok(help < z.get('.hovercard')!, '.help is below .hovercard')
  assert.ok(help < z.get('button[data-tip]:hover, button[data-tip]:focus-visible')!, '.help is below tips')
})

// The body of the first rule in `file` whose selector is exactly `selector`.
function rule(selector: string, file = 'app.css'): string {
  const css = readFileSync(new URL(file, import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  for (const [, found, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (found.trim() === selector) {
      return body
    }
  }
  throw new Error(`no ${selector} rule in ${file}`)
}

test("a list group's heading sticks below the search box, over its rows and under the search box", () => {
  const head = rule('.box > .box-head, .box > .box-heading')
  assert.match(head, /(?:^|;)\s*position:\s*sticky\s*;/, 'the heading sticks')
  const search = rule('.search')
  const searchTop = Number(/(?:^|;)\s*top:\s*(-?\d+)px\s*;/.exec(search)![1])
  // The search box stands 57px tall: 12px of padding over and under a 32px field, and a 1px border under it.
  const top = Number(/(?:^|;)\s*top:\s*(-?\d+)px\s*;/.exec(head)![1])
  assert.equal(top, searchTop + 57, 'the heading sticks where the search box ends')
  const z = zIndexes()
  const heading = z.get('.box > .box-head, .box > .box-heading')!
  assert.ok(heading >= 1, 'the heading paints over the rows')
  assert.ok(heading < z.get('.search')!, 'the heading paints under the search box')
  // A box that hides or scrolls its overflow becomes the heading's scroller, where it never sticks.
  assert.match(rule('.box'), /(?:^|;)\s*overflow:\s*clip\s*;/, '.box clips its corners without scrolling')
})

test("a card the keys reveal stops below the search box and its group's stuck heading", () => {
  const padding = /(?:^|;)\s*scroll-padding-top:\s*(\d+)px\s*;/.exec(rule('.list'))
  assert.ok(padding, '.list sets scroll-padding-top in pixels')
  // The search box shows 57px of itself and a heading with its counter stands 38px tall.
  assert.ok(Number(padding[1]) >= 57 + 38, `${padding[1]}px clears the search box and a heading`)
})

test('the keyboard help scrolls inside its backdrop when the window is too short for its sheet', () => {
  const help = rule('.help')
  assert.match(help, /(?:^|;)\s*overflow:\s*auto\s*;/, '.help scrolls its overflow')
  assert.match(help, /(?:^|;)\s*overscroll-behavior:\s*contain\s*;/, '.help keeps its scrolling from the page')
  assert.match(help, /(?:^|;)\s*place-items:\s*safe center\s*;/, '.help centres the sheet only while it fits')
})

test("the pressed mode button takes its mode's color, the one its project tabs name it in", () => {
  for (const mode of ['running', 'manual', 'held']) {
    const body = rule(`.modes button[data-mode="${mode}"][aria-pressed="true"]`, 'app-header.css')
    assert.match(body, new RegExp(`(?:^|;)\\s*color:\\s*var\\(--mode-${mode}\\)\\s*;`), `${mode} text`)
    assert.match(body, new RegExp(`(?:^|;)\\s*border-color:\\s*var\\(--mode-${mode}\\)\\s*;`), `${mode} border`)
    assert.match(rule(`.tab-mode.${mode}`, 'app-header.css'), new RegExp(`color:\\s*var\\(--mode-${mode}\\)`), `${mode} tab`)
  }
  assert.match(rule('.bar button[aria-pressed="true"]', 'app-header.css'), /color:\s*var\(--accent\)/, 'other pressed buttons')
})

test('the Refresh icon turns once a second while it refreshes, and stays still under reduced motion', () => {
  assert.match(rule('.refresh.spinning .icon', 'app-header.css'), /animation:\s*spin 1s linear infinite\s*;/)
  const css = readFileSync(new URL('app-header.css', import.meta.url), 'utf8')
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s*\.refresh\.spinning \.icon \{ animation: none; \}/)
})

test("the file tree's rows are tall enough for the descenders its names' ellipsis would clip", () => {
  const row = rule('.tree-row', 'app-detail.css')
  const font = /(?:^|;)\s*font:\s*\d+\s+(\d+)px\/(\d+)px\s/.exec(row)
  assert.ok(font, '.tree-row sets its font size and a line height in pixels')
  assert.ok(Number(font[2]) >= Number(font[1]) * 1.4, `a ${font[2]}px line holds ${font[1]}px text's descenders`)
  assert.ok(Number(font[2]) <= 28, 'the line fits in the 28px row')
  assert.match(rule('.tree-name', 'app-detail.css'), /overflow:\s*hidden/, '.tree-name clips its overflow for the ellipsis')
})

// The value a token takes in `:root` in the light theme, or in the dark theme's `:root`.
function token(name: string, dark: boolean): string {
  const css = readFileSync(new URL('app.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  const darkStart = css.indexOf('@media (prefers-color-scheme: dark) {')
  const block = dark ? css.slice(darkStart) : css.slice(0, darkStart)
  const root = /:root\s*\{([^{}]*)\}/.exec(block)
  assert.ok(root, `a ${dark ? 'dark' : 'light'} :root block`)
  const value = new RegExp(`(?:^|;)\\s*${name}:\\s*([^;]+);`).exec(root[1])
  assert.ok(value, `${name} is defined in the ${dark ? 'dark' : 'light'} theme`)
  return value[1].trim()
}

const lightness = (value: string): number => Number(/^oklch\(([\d.]+)/.exec(value)![1])

test('the header, the tab band, and the list search sit on three surfaces, the header deepest', () => {
  assert.match(rule('header', 'app-header.css'), /(?:^|;)\s*background:\s*var\(--chrome\)\s*;/, 'header')
  assert.match(rule('header > nav', 'app-header.css'), /(?:^|;)\s*background:\s*var\(--panel\)\s*;/, 'header > nav')
  assert.match(rule('.search'), /(?:^|;)\s*background:\s*var\(--bg\)\s*;/, '.search')
  assert.match(rule('header > nav button[aria-current="true"]', 'app-header.css'), /color-mix\(in srgb, var\(--tab-hue\) \d+%, var\(--panel\)\)/)
  assert.match(rule('.history .search'), /(?:^|;)\s*background:\s*var\(--panel\)\s*;/, "History's search keeps --panel")
  const [chrome, panel, bg] = ['--chrome', '--panel', '--bg'].map(name => lightness(token(name, false)))
  assert.ok(chrome < panel && panel < bg, 'in light, --chrome < --panel < --bg')
  const [darkChrome, darkPanel, darkBg] = ['--chrome', '--panel', '--bg'].map(name => lightness(token(name, true)))
  assert.ok(darkChrome < darkBg && darkBg < darkPanel, 'in dark, --chrome < --bg < --panel')
  const css = readFileSync(new URL('app-header.css', import.meta.url), 'utf8')
  assert.match(css, /@media \(prefers-color-scheme: dark\) \{\s*header > nav button\[aria-current="true"\] \{ background: color-mix\(in srgb, var\(--tab-hue\) \d+%, var\(--panel\)\); \}/)
})

test("in light, the header's notices, disconnected status, gauges, and usage limit take deeper warning, error, and "
  + 'green tones', () => {
  const css = readFileSync(new URL('app-header.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  const block = /@media \(prefers-color-scheme: light\) \{\s*header \.notice, header \.code-notice, header \.offline, header \.gauges, header \.usage \{([^{}]*)\}/.exec(css)
  assert.ok(block, 'a light-only rule for the header text on --chrome')
  for (const name of ['--warning', '--error', '--gauge-ok']) {
    const value = new RegExp(`(?:^|;)\\s*${name}:\\s*([^;]+);`).exec(block[1])
    assert.ok(value, `the header rule sets ${name}`)
    assert.ok(lightness(value[1].trim()) < lightness(token(name, false)), `the header's ${name} is deeper than the page's`)
  }
  assert.doesNotMatch(block[0], /hold-state/, "the held pill's fill keeps the page's warning")
})

// A selector's specificity as [ids, classes, types], counting attributes and pseudo-classes as classes.
function specificity(selector: string): [number, number, number] {
  const plain = selector.replace(/::[\w-]+/g, ' x').replace(/\[[^\]]*\]/g, '.a')
  return [
    (plain.match(/#[\w-]+/g) ?? []).length,
    (plain.match(/[.:][\w-]+/g) ?? []).length,
    (plain.match(/(?:^|[\s>+~])[a-z][\w-]*/gi) ?? []).length,
  ]
}

test('the picture viewer centres itself above any rule that sets the margin of whatever child it sits beside', () => {
  const viewer = rule('.picture-viewer:modal', 'app-detail.css')
  assert.match(viewer, /(?:^|;)\s*margin:\s*auto\s*;/, 'the viewer restates the margin that centres a modal dialog')
  const own = specificity('.picture-viewer:modal')
  for (const file of ['app.css', 'app-header.css', 'app-detail.css', 'app-chat.css']) {
    const css = readFileSync(new URL(file, import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    for (const [, selectors, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/(?:^|;)\s*margin(?:-top|-bottom|-block[\w-]*)?\s*:/.test(body)) {
        continue
      }
      // A selector whose subject is any element, such as `.markdown-parts > * + *`, can match the viewer's dialog.
      for (const selector of selectors.split(',').map(part => part.trim()).filter(part => /(?:^|[\s>+~])\*$/.test(part))) {
        const other = specificity(selector)
        const index = own.findIndex((count, at) => count !== other[at])
        assert.ok(index !== -1 && own[index] > other[index], `.picture-viewer:modal outranks ${selector} in ${file}`)
      }
    }
  }
})

test("a diff line's thread fits the width the diff shows, up to 820px, and stays in view while the diff scrolls", () => {
  assert.match(rule('.diff-scroll', 'app-detail.css'), /(?:^|;)\s*container-type:\s*inline-size\s*;/,
    '.diff-scroll is the container the thread measures')
  const thread = rule('.line-thread', 'app-detail.css')
  const margin = /(?:^|;)\s*margin:\s*\d+px\s+(\d+)px\s+\d+px\s+(\d+)px\s*;/.exec(thread)
  assert.ok(margin, '.line-thread sets its four margins in pixels')
  const sides = Number(margin[1]) + Number(margin[2])
  assert.match(thread, new RegExp(`(?:^|;)\\s*max-width:\\s*min\\(820px,\\s*100cqi - ${sides}px\\)\\s*;`),
    `.line-thread caps at 820px and at the diff's width less its ${sides}px of margins`)
  assert.match(thread, /(?:^|;)\s*position:\s*sticky\s*;/, '.line-thread sticks')
  assert.match(thread, /(?:^|;)\s*left:\s*0\s*;/, "to the diff's left edge")
})

test("a card's note in the warning tone takes the warning color and border", () => {
  const css = readFileSync(new URL('app.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  const rule = /(?:^|\})\s*\.note\.warning \{([^{}]*)\}/.exec(css)
  assert.ok(rule, 'a rule for .note.warning')
  assert.match(rule[1], /color: var\(--warning\)/)
  assert.match(rule[1], /border-color: currentColor/)
})

test("the Chat's rows keep their height once the log overflows, a tool call's clipped line included", () => {
  assert.match(rule('.chat-log > *', 'app-chat.css'), /(?:^|;)\s*flex:\s*none\s*;/)
  assert.match(rule('.chat-tool', 'app-chat.css'), /overflow:\s*hidden/)
})

test("the header's styles load right after the page's, so the cascade keeps the order the two files split", () => {
  const page = readFileSync(new URL('index.html', import.meta.url), 'utf8')
  const sheets = [...page.matchAll(/<link rel="stylesheet" href="([^"]+)">/g)].map(found => found[1])
  assert.deepEqual(sheets.slice(0, 2), ['app.css', 'app-header.css'])
})
