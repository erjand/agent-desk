// GitHub-flavored Markdown rendered on this machine, with the output cleaned to an allow-list. Agents write most issue
// and review bodies, and a body that ran script on the page could press its buttons, so every body counts as untrusted.
// marked, vendored from npm, parses the Markdown; the sanitizer below is this project's own code.
import { randomUUID } from 'node:crypto'

import { Marked } from './vendor/marked.esm.js'

/** The address a `#123` reference links to, by the item's number. */
export type ItemHref = (number: number) => string

/**
 * The address on the page's own origin that a picture from `src`, an address on another host, loads from, or null
 * when the page serves no copy of it.
 */
export type PageImage = (src: string) => string | null

/** A token of marked's, as it reads Markdown into blocks and their inline parts. */
export type Token = { type: string; text?: string; raw?: string; escaped?: boolean; tokens?: Token[]; task?: boolean
  checked?: boolean; items?: Token[]; ordered?: boolean; start?: number | ''; box?: string; depth?: number }
type Renderer = { parser: { parse(tokens: Token[]): string }; listitem(item: Token): string }

// A `#123` reference, which GitHub links when no word character, `&`, `/`, or another `#` comes before it, so an
// entity such as `&#39;` and a path such as `a/#1` stay text.
const REFERENCE = /(?<![\w&/#])(#\d+)\b/

/** Text escaped for HTML, leaving entity references the Markdown wrote as they are, as marked escapes text. */
function escapeText(text: string): string {
  return text.replace(/[<>"']|&(?!#\d{1,7};|#[xX][\da-fA-F]{1,6};|\w+;)/g, char =>
    ({ '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char] ?? '&amp;')
}

/** Marks the text inside a link or an image, where a reference would nest a link or land in an `alt` attribute. */
function markInLink(tokens: Token[] | undefined, inLink: WeakSet<Token>): void {
  for (const token of tokens ?? []) {
    inLink.add(token)
    markInLink(token.tokens, inLink)
  }
}

const marked = new Marked({
  gfm: true,
  renderer: {
    // GitHub's classes on a task list, which the page's styles in app-detail.css select.
    list(this: Renderer, token: Token) {
      if (!token.items?.some(item => item.task)) {
        return false
      }
      const tag = token.ordered ? 'ol' : 'ul'
      const start = token.ordered && token.start !== 1 ? ` start="${token.start}"` : ''
      return `<${tag} class="contains-task-list"${start}>\n${token.items.map(item => this.listitem(item)).join('')}`
        + `</${tag}>\n`
    },
    listitem(this: Renderer, item: Token) {
      return item.task ? `<li class="task-list-item">${this.parser.parse(item.tokens ?? [])}</li>\n` : false
    },
    // A box `renderMarkdown` numbered for the page to tick carries its number and stays enabled.
    checkbox({ checked, box }: Token) {
      return `<input type="checkbox" class="task-list-item-checkbox"${checked ? ' checked=""' : ''}`
        + `${box === undefined ? ' disabled=""' : ` data-task="${box}"`}> `
    },
  },
})

/** How a body renders: with `tasks`, its task list's boxes take clicks, each numbered from 0 in `data-task`. */
export type RenderOptions = { tasks?: boolean }

/**
 * `text` as HTML, with each `#123` linked to `itemHref(123)` and anything outside the sanitizer's allow-list removed.
 * A commit SHA stays text, and a picture from another host shows as a link unless `pageImage` serves a copy. Every
 * checkbox is disabled, except a task item's own box when `options.tasks` is set.
 */
export function renderMarkdown(text: string, itemHref: ItemHref, pageImage?: PageImage, options: RenderOptions = {})
  : string {
  const blocks = markdownBlocks(text, itemHref, pageImage, options)
  return blocks.html(blocks.tokens)
}

/**
 * A body read as marked reads it, with its references linked and its task boxes numbered as `renderMarkdown` does them.
 * `tokens` holds its top-level blocks, and `html` renders any run of its tokens through the sanitizer, so a body cut
 * into pieces renders each piece as the whole would, with its boxes' numbers.
 */
export type Blocks = { tokens: Token[]; html(tokens: Token[]): string }

/** `text` read into blocks that render as `renderMarkdown` renders the whole. */
export function markdownBlocks(text: string, itemHref: ItemHref, pageImage?: PageImage, options: RenderOptions = {})
  : Blocks {
  const tokens = marked.lexer(text)
  const inLink = new WeakSet<Token>()
  // Each task box carries a number behind a key made for this render, which the sanitizer alone knows, so a box the
  // body writes as raw HTML can't pass for one.
  const key = options.tasks ? randomUUID() : null
  let boxes = 0
  marked.walkTokens(tokens, (token: Token) => {
    if (token.type === 'checkbox' && key !== null) {
      token.box = `${key}:${boxes++}`
    } else if (token.type === 'link' || token.type === 'image') {
      markInLink(token.tokens, inLink)
    } else if (token.type === 'text' && !token.tokens && !token.escaped && !inLink.has(token)
      && REFERENCE.test(token.text ?? '')) {
      // The text becomes HTML of its own, which marked writes as it stands and the sanitizer then reads.
      token.type = 'html'
      token.text = (token.text ?? '').split(REFERENCE).map((part, at) => at % 2 === 0 ? escapeText(part)
        : `<a class="issue-link" href="${escapeAttribute(itemHref(Number(part.slice(1))))}">${part}</a>`).join('')
    }
  })
  return { tokens, html: run => sanitize(marked.parser(run), pageImage, key ?? undefined) }
}

/** Whether each task item in `text` is ticked, in the order the page numbers their boxes, as marked reads them. */
export function taskStates(text: string): boolean[] {
  const states: boolean[] = []
  marked.walkTokens(marked.lexer(text), (token: Token) => {
    if (token.type === 'checkbox') {
      states.push(token.checked === true)
    }
  })
  return states
}

// Where a task marker may stand: after only spaces, blockquote marks, and list bullets on its line.
const MARKER = /\[[ xX]\]/g
const BEFORE_MARKER = /^[ \t>]*(?:(?:[-+*]|\d{1,9}[.)])[ \t]*)*$/

/**
 * `text` with task item `task`, counted from 0 as `taskStates` counts them, ticked or not as `checked` says; null when
 * the text has no such item. Each marker that could be the item's is tried in turn, and the one marked reads back as
 * changing that item and no other is the one, so a marker in a code block or one that isn't a task item never counts.
 */
export function toggledTask(text: string, task: number, checked: boolean): string | null {
  const states = taskStates(text)
  if (!Number.isInteger(task) || task < 0 || task >= states.length) {
    return null
  }
  if (states[task] === checked) {
    return text
  }
  const wanted = states.map((state, at) => at === task ? checked : state)
  for (const found of text.matchAll(MARKER)) {
    const at = found.index
    const line = text.lastIndexOf('\n', at - 1) + 1
    if ((found[0] !== '[ ]') === checked || !BEFORE_MARKER.test(text.slice(line, at))) {
      continue
    }
    const changed = `${text.slice(0, at)}${checked ? '[x]' : '[ ]'}${text.slice(at + 3)}`
    const after = taskStates(changed)
    if (after.length === wanted.length && after.every((state, index) => state === wanted[index])) {
      return changed
    }
  }
  return null
}

/** `text` as the plain words it renders to, as a hovercard quotes a description, with a space between blocks. */
export function markdownText(text: string): string {
  return decodeEntities(renderMarkdown(text, () => '')
    .replace(/<\/?(?:p|div|li|ul|ol|dl|dt|dd|h\d|table|tr|td|th|blockquote|pre|details|summary|br|hr)\b[^>]*>/gi, ' ')
    .replace(/<[^>]*>/g, ''))
}

// The tags that survive, each with the attributes it keeps besides the global ones.
const TAGS: Record<string, string[]> = {
  a: ['href'], abbr: [], b: [], blockquote: [], br: [], code: [], dd: [], del: [], details: ['open'], div: [], dl: [],
  dt: [], em: [], h1: [], h2: [], h3: [], h4: [], h5: [], h6: [], hr: [], i: [], kbd: [], li: [], mark: [], p: [],
  pre: [], q: [], s: [], samp: [], small: [], span: [], strike: [], strong: [], sub: [], summary: [], sup: [],
  table: [], tbody: [], tfoot: [], thead: [], tr: [], tt: [], u: [], ul: [], var: [],
  img: ['src', 'alt', 'width', 'height'], input: ['type', 'checked', 'disabled'], ins: [], ol: ['start'],
  td: ['colspan', 'rowspan'], th: ['colspan', 'rowspan'],
}
const GLOBAL_ATTRIBUTES = ['title', 'align', 'class']
const VOID = new Set(['br', 'hr', 'img', 'input', 'wbr', 'area', 'base', 'col', 'embed', 'link', 'meta', 'param',
  'source', 'track', 'frame', 'keygen'])
// Tags removed with everything inside them. The raw-text ones end at their own end tag whatever comes between.
const RAW_TEXT = new Set(['script', 'style', 'iframe', 'textarea', 'title', 'xmp', 'noembed', 'noframes',
  'noscript'])
const DROPPED = new Set([...RAW_TEXT, 'form', 'svg', 'math', 'template', 'object', 'select', 'plaintext'])
// The class on the link `image` wraps around a picture the page serves. The sanitizer drops it from an author's own
// link, so only those links open the page's picture viewer.
const PICTURE_LINK = 'picture-link'
// The classes that survive: GitHub's on task lists and references, and a fenced block's language on <code>.
const CLASSES = new Set(['task-list-item', 'contains-task-list', 'task-list-item-checkbox', 'issue-link'])
// The entities an attribute's value is read with; the value is written back with every `&` escaped, so the browser
// reads exactly the value the checks below saw, and an entity left out shows as its own text.
const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }
// The whitespace and control characters a browser drops from a URL, or that no URL holds.
const CONTROLS = /[\u0000- \u007f-\u009f]/g

function escapeAttribute(value: string): string {
  return value.replace(/[&"<>]/g, char => ({ '&': '&amp;', '"': '&quot;', '<': '&lt;', '>': '&gt;' })[char] ?? char)
}

function decodeEntities(value: string): string {
  return value.replace(/&(?:#(\d{1,7})|#[xX]([\da-fA-F]{1,6}));?|&(\w+);/g, (whole, decimal, hex, name) => {
    if (name !== undefined) {
      return ENTITIES[name] ?? whole
    }
    const code = decimal !== undefined ? Number(decimal) : parseInt(hex, 16)
    return code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff) ? '�' : String.fromCodePoint(code)
  })
}

/**
 * Whether an `href` or `src` stays: `http` and `https`, and a path on the page's own origin. Whitespace and control
 * characters are dropped before the check, as a browser drops tabs and newlines, so `java\tscript:` reads as a scheme.
 */
export function safeUrl(value: string): boolean {
  const plain = value.replace(CONTROLS, '')
  if (/^https?:/i.test(plain)) {
    return true
  }
  return onPage(plain)
}

/** Whether a URL, with whitespace and control characters dropped, is a path on the page's own origin. */
function onPage(plain: string): boolean {
  // Any scheme, and `//host` or `/\host`, which leave for another origin.
  return !/^[a-z][a-z\d+.-]*:/i.test(plain) && !/^[\\/]{2}/.test(plain)
}

/**
 * An image as it stays: kept when the page serves its source, with `pageImage`'s address for a picture the page keeps
 * a copy of, and otherwise a link to its source with its alt text, so the browser fetches nothing from another host.
 * Inside another link, or with a source no link may take, only the alt text stays. A kept picture from the server's
 * picture route, outside any link, sits inside a link to itself with the class PICTURE_LINK, which the page opens in
 * its picture viewer.
 */
function image(attributes: Map<string, string>, inLink: boolean, pageImage?: PageImage): string | null {
  const src = attributes.get('src')
  if (src === undefined) {
    return startTag('img', attributes)
  }
  const page = onPage(src.replace(CONTROLS, '')) ? src : pageImage?.(src) ?? null
  if (page !== null) {
    const kept = startTag('img', new Map([...attributes, ['src', page]]))
    return inLink || !/^\/api\/asset[?/]/.test(page) ? kept
      : `<a class="${PICTURE_LINK}" href="${escapeAttribute(page)}">${kept}</a>`
  }
  const text = escapeAttribute(attributes.get('alt') || src)
  return inLink || !safeUrl(src) ? text : `<a href="${escapeAttribute(src)}">${text}</a>`
}

/** An attribute's value as it stays on `tag`, or null to drop the attribute. */
function keptValue(tag: string, name: string, value: string): string | null {
  switch (name) {
    case 'href':
    case 'src':
      return safeUrl(value) ? value : null
    case 'class': {
      const kept = value.split(/\s+/)
        .filter(one => CLASSES.has(one) || (tag === 'code' && /^language-[\w+-]+$/.test(one)))
      return kept.length > 0 ? kept.join(' ') : null
    }
    case 'align':
      return /^(left|right|center|justify)$/i.test(value) ? value.toLowerCase() : null
    case 'width':
    case 'height':
      return /^\d+%?$/.test(value) ? value : null
    case 'start':
    case 'colspan':
    case 'rowspan':
      return /^\d+$/.test(value) ? value : null
    case 'open':
    case 'checked':
    case 'disabled':
      return ''
    case 'type':
      return value.toLowerCase() === 'checkbox' ? 'checkbox' : null
    default:
      return value
  }
}

/** A start tag's attributes, read as a browser reads them: the first of a repeated name wins. */
function readAttributes(html: string, from: number): { attributes: Map<string, string>; end: number; closed: boolean }
  | null {
  const attributes = new Map<string, string>()
  let at = from
  for (;;) {
    while (at < html.length && /[\t\n\f\r /]/.test(html[at])) {
      at++
    }
    if (at >= html.length) {
      return null
    }
    if (html[at] === '>') {
      return { attributes, end: at + 1, closed: html[at - 1] === '/' }
    }
    const name = /^[^\t\n\f\r />][^\t\n\f\r />=]*/.exec(html.slice(at))?.[0] ?? html[at]
    at += name.length
    let value = ''
    const equals = /^[\t\n\f\r ]*=[\t\n\f\r ]*/.exec(html.slice(at))
    if (equals) {
      at += equals[0].length
      const quote = html[at]
      if (quote === '"' || quote === "'") {
        const close = html.indexOf(quote, at + 1)
        if (close < 0) {
          return null
        }
        value = html.slice(at + 1, close)
        at = close + 1
      } else {
        value = /^[^\t\n\f\r >]*/.exec(html.slice(at))?.[0] ?? ''
        at += value.length
      }
    }
    const lower = name.toLowerCase()
    if (!attributes.has(lower)) {
      attributes.set(lower, decodeEntities(value))
    }
  }
}

/**
 * A start tag written back with only the attributes it keeps, or null when the tag goes. A checkbox whose `data-task`
 * starts with this render's `key` keeps its number and takes clicks; every other checkbox is disabled.
 */
function startTag(tag: string, attributes: Map<string, string>, key?: string): string | null {
  const allowed = TAGS[tag]
  if (tag === 'input' && attributes.get('type')?.toLowerCase() !== 'checkbox') {
    return null
  }
  let out = `<${tag}`
  for (const [name, value] of attributes) {
    if (!allowed.includes(name) && !GLOBAL_ATTRIBUTES.includes(name)) {
      continue
    }
    const kept = keptValue(tag, name, value)
    if (kept !== null) {
      out += ` ${name}="${escapeAttribute(kept)}"`
    }
  }
  // A checkbox takes no click, as GitHub draws one in a body it shows, unless it is the renderer's own task box.
  const box = key === undefined ? null : new RegExp(`^${key}:(\\d+)$`).exec(attributes.get('data-task') ?? '')
  if (tag === 'input' && box !== null) {
    out += ` data-task="${box[1]}"`
  } else if (tag === 'input' && !attributes.has('disabled')) {
    out += ' disabled=""'
  }
  return `${out}>`
}

/**
 * `html` cut to the allow-list and written back: each tag in TAGS with only its allowed attributes, quoted and
 * escaped, the tags in DROPPED gone with everything inside them, and comments gone. Any other tag goes, and its
 * contents stay as text. An image from another host becomes a link, as `image` writes it. Every `<` outside a kept
 * tag is escaped and every open tag is closed, so the browser reads the structure written here. Every checkbox is
 * disabled, except one `renderMarkdown` numbered behind `key`.
 */
export function sanitize(html: string, pageImage?: PageImage, key?: string): string {
  let out = ''
  const open: string[] = []
  // The dropped element being skipped, and how deep the same tag nests inside it.
  let skipping: { tag: string; depth: number } | null = null
  let at = 0
  const text = (part: string) => {
    if (skipping === null) {
      out += part.replace(/[<>]|&(?!#\d{1,7};|#[xX][\da-fA-F]{1,6};|\w+;)/g,
        char => ({ '<': '&lt;', '>': '&gt;' })[char] ?? '&amp;')
    }
  }
  while (at < html.length) {
    const lt = html.indexOf('<', at)
    if (lt < 0) {
      text(html.slice(at))
      break
    }
    text(html.slice(at, lt))
    at = lt
    if (html.startsWith('<!--', at)) {
      // A comment ends at --> or --!>, at once as <!--> or <!--->, or at the end of the input.
      const short = /^<!---?>/.exec(html.slice(at, at + 6))
      const close = short ? null : /--!?>/.exec(html.slice(at + 4))
      at = short ? at + short[0].length : close ? at + 4 + close.index + close[0].length : html.length
      continue
    }
    if (html.startsWith('<!', at) || html.startsWith('<?', at)) {
      const close = html.indexOf('>', at)
      at = close < 0 ? html.length : close + 1
      continue
    }
    const end = /^<\/([a-zA-Z][^\t\n\f\r />]*)[^>]*(?:>|$)/.exec(html.slice(at))
    if (end) {
      at += end[0].length
      const tag = end[1].toLowerCase()
      if (skipping !== null) {
        if (tag === skipping.tag && --skipping.depth === 0) {
          skipping = null
        }
      } else if (open.includes(tag)) {
        while (open.length > 0) {
          const last = open.pop()
          out += `</${last}>`
          if (last === tag) {
            break
          }
        }
      }
      continue
    }
    const start = /^<([a-zA-Z][^\t\n\f\r />]*)/.exec(html.slice(at))
    if (!start) {
      text('<')
      at++
      continue
    }
    const tag = start[1].toLowerCase()
    const read = readAttributes(html, at + start[0].length)
    if (read === null) {
      // A tag the input never finishes is no tag, as in a browser.
      break
    }
    at = read.end
    if (skipping !== null) {
      if (tag === skipping.tag && !read.closed) {
        skipping.depth++
      }
      continue
    }
    if (tag === 'plaintext') {
      break
    }
    if (RAW_TEXT.has(tag)) {
      const close = html.slice(at).search(new RegExp(`</${tag}[\\t\\n\\f\\r />]`, 'i'))
      if (close < 0) {
        break
      }
      const closeEnd = html.indexOf('>', at + close)
      at = closeEnd < 0 ? html.length : closeEnd + 1
      continue
    }
    if (DROPPED.has(tag)) {
      if (!read.closed) {
        skipping = { tag, depth: 1 }
      }
      continue
    }
    if (!(tag in TAGS)) {
      continue
    }
    const kept = tag === 'img' ? image(read.attributes, open.includes('a'), pageImage)
      : startTag(tag, read.attributes, key)
    if (kept !== null) {
      out += kept
      if (!VOID.has(tag)) {
        open.push(tag)
      }
    }
  }
  return out + open.reverse().map(tag => `</${tag}>`).join('')
}
