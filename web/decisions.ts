// A body's `## Decisions Needed` section as data, so the page draws it with an answer form under it, and the answer
// comments that form posts, read back to the section they answer. The skills write a section in one of two layouts:
// numbered `### <n>. <question>` decisions, each with lettered options, or the older paragraphs and bullets. Every
// piece of HTML here comes from markdown.ts, through its sanitizer.
import { markdownBlocks, markdownText, type ItemHref, type PageImage, type RenderOptions, type Token }
  from './markdown.ts'

/** A decision's lettered option: its letter, its short label as text, whether the agent picked it, and its HTML. */
type DecisionOption = { letter: string; label: string; pick: boolean; html: string }

/**
 * One numbered decision: its number and question as text, `head`, the HTML of its heading and the context under it,
 * its options, and `after`, the HTML of anything between its options and the next decision.
 */
export type Decision = { number: number; question: string; head: string; options: DecisionOption[]; after: string }

/**
 * A `## Decisions Needed` section: the anchor an answer links it by, the words an answer names its place with, its
 * heading's HTML, `intro`, the HTML between the heading and the first decision, and its decisions. A section in the
 * older layout has no decisions, and `intro` holds all of it.
 */
export type DecisionSection = { anchor: string; place: string; heading: string; intro: string
  decisions: Decision[] }

/** A run of a body's HTML, or a Decisions Needed section in it. */
export type BodyPart = { html: string } | { section: DecisionSection }

/** What an answer comment answers: the section's anchor, and each decision it answers, such as `1B` or `2`. */
export type Answer = { anchor: string; picks: string[] }

const SECTION = /^decisions needed$/i
const DECISION = /^(\d{1,4})\.\s+(.*)$/s
// An option's item starts with its bold letter and label, as `**B. Overlay it**`; the agent's pick follows it with ✅.
const OPTION = /^\*\*([A-Z])\.\s+(.+?)\*\*(\s*✅)?/s

// The line agent-desk signs its comments with, which ends a section so the form sits above it.
const SIGNATURE = /^<sub>Posted by agent-desk\b/

/** Whether a token ends a section at `depth`: a heading at that depth or above, or agent-desk's signature. */
function endsAt(token: Token, depth: number): boolean {
  return (token.type === 'heading' && (token.depth ?? 6) <= depth)
    || ((token.type === 'paragraph' || token.type === 'html') && SIGNATURE.test((token.raw ?? '').trimStart()))
}

/** A list whose every item is a lettered option, read as options; null for any other token. */
function optionsOf(token: Token, html: (tokens: Token[]) => string): DecisionOption[] | null {
  if (token.type !== 'list' || !token.items?.length) {
    return null
  }
  const options = token.items.map(item => {
    const found = OPTION.exec((item.text ?? '').trimStart())
    return found ? { letter: found[1], label: markdownText(found[2]).trim(), pick: found[3] !== undefined,
      html: html(item.tokens ?? []) } : null
  })
  return options.every(option => option !== null) ? options : null
}

/** A section's blocks after its heading, read into numbered decisions; none when it holds no `### <n>.` heading. */
function decisionsOf(tokens: Token[], html: (tokens: Token[]) => string): { intro: string; decisions: Decision[] } {
  const starts = tokens.flatMap((token, at) => token.type === 'heading' && token.depth === 3
    && DECISION.test(token.text ?? '') ? [at] : [])
  if (starts.length === 0) {
    return { intro: html(tokens), decisions: [] }
  }
  const decisions = starts.map((start, index) => {
    const blocks = tokens.slice(start, starts[index + 1] ?? tokens.length)
    const [, number, question] = DECISION.exec(blocks[0].text ?? '') ?? []
    const listAt = blocks.findIndex(token => optionsOf(token, html) !== null)
    const options = listAt < 0 ? [] : optionsOf(blocks[listAt], html) ?? []
    return { number: Number(number), question: markdownText(question).trim(),
      head: html(listAt < 0 ? blocks : blocks.slice(0, listAt)), options,
      after: listAt < 0 ? '' : html(blocks.slice(listAt + 1)) }
  })
  return { intro: html(tokens.slice(0, starts[0])), decisions }
}

/**
 * A body cut at each `## Decisions Needed` section, which runs to the next heading at its level or above, or to
 * agent-desk's signature, with each section as data, or null when it has none. `anchor` and `place` name the body,
 * such as `issue-body` and `the description`; a second section in it takes `-2` on its anchor. The pieces render as
 * `renderMarkdown` renders the whole, with the same options.
 */
export function bodyParts(text: string, anchor: string, place: string, itemHref: ItemHref, pageImage?: PageImage,
  options: RenderOptions = {}): BodyPart[] | null {
  const { tokens, html } = markdownBlocks(text, itemHref, pageImage, options)
  const parts: BodyPart[] = []
  let from = 0
  let at = 0
  while (at < tokens.length) {
    const token = tokens[at]
    if (token.type !== 'heading' || (token.depth ?? 0) > 2 || !SECTION.test((token.text ?? '').trim())) {
      at++
      continue
    }
    const depth = token.depth ?? 2
    let end = at + 1
    while (end < tokens.length && !endsAt(tokens[end], depth)) {
      end++
    }
    if (from < at) {
      parts.push({ html: html(tokens.slice(from, at)) })
    }
    const count = parts.filter(part => 'section' in part).length
    parts.push({ section: { anchor: count === 0 ? anchor : `${anchor}-${count + 1}`, place,
      heading: html([token]), ...decisionsOf(tokens.slice(at + 1, end), html) } })
    from = at = end
  }
  if (parts.length === 0) {
    return null
  }
  if (from < tokens.length) {
    parts.push({ html: html(tokens.slice(from)) })
  }
  return parts
}

const ANSWER = /^(?:@agent\s+)?Answers to the Decisions Needed in \[[^\]\n]*\]\(#([\w-]+)\):/
const PICK = /^- \*\*(\d{1,4}[A-Z]?)\.\*\*/gm

/**
 * The section an answer comment answers and the decisions it answers, from its first line, as the page's answer form
 * writes it; null for any other comment. A comment sent to the agent may start with `@agent`.
 */
export function answerOf(text: string): Answer | null {
  const found = ANSWER.exec(text.trimStart())
  return found ? { anchor: found[1], picks: [...text.matchAll(PICK)].map(pick => pick[1]) } : null
}
