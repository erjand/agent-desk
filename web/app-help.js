// The keyboard shortcuts dialog, which `?` and the header's help button open: the page's keys under headed sections
// that flow into as many columns as the window fits.
import { html } from './vendor/preact-htm.js'
import { REVIEW_KEYS, REVIEW_LABELS } from './app-review.js'
import { draw, KEYS, LABELS, ui } from './app-state.js'

// The prefix that names a review button's key from REVIEW_KEYS in SECTIONS, apart from the card's actions in KEYS.
export const REVIEW = 'review:'

// The dialog's sections in order, each with its rows in order. A row is a key and what it does, the name of a card's
// action in KEYS, or a review button's name in REVIEW_KEYS after REVIEW. A key in KEYS or REVIEW_KEYS shows in the
// dialog only through a row here, and app-help.test.ts fails when one has none.
export const SECTIONS = [
  ['Move Around', [['/', 'Search the List'], ['j / ↓', 'Next Card'], ['k / ↑', 'Previous Card'],
    ['← / →', 'Previous or Next Tab'], ['o / Enter', 'Show the Card, or Open a Failed Run\'s Log'],
    ['1-9', 'All, Then Each Project'], ['-', 'Collapse Every Group'], ['+ / =', 'Expand Every Group']]],
  ['Issues', ['ready', 'start', 'unpick', 'restart', 'designed', 'to-open', 'to-backlog', 'unblock', 'unpause',
    'close']],
  ['Order', ['up', 'down', 'top', 'bottom']],
  ['Runs', ['resume', 'retry', 'dismiss', 'cancel', 'pause', 'stop-command']],
  ['Reviews', ['merge', 'fix', 'undraft', `${REVIEW}close`]],
  ['Panels', [['\\', 'Show or Hide Runners'], ['h', 'Show or Hide History, in Place of Runners'],
    ['`', 'Show or Hide Chat, in Place of Runners (Backtick)'], ['g', 'Show or Hide GitHub, in Place of Runners'],
    ['i', 'New Issue, in Place of the Card'], ['?', 'These Keys']]],
  ['Confirm and Cancel', [['y', 'Confirm, or Press the Action\'s Key Again'],
    ['Esc', 'Cancel, or Close the Card or the New Issue Form'], ['A-Z', 'Letter Keys Work in Either Case']]],
]

// What a key does where its button's label alone doesn't say: Retry reads Continue on an agent's timed-out run, and
// the moves work in Queued's start order and in the five groups' list order.
const HELP_LABELS = { retry: 'Retry, or Continue a Timed-Out Agent Run',
  up: 'Move Up, in Queued, Open, Blocked, Needs Design, Backlog, or Ideas' }

// Each section's title and its rows as a key and what it does.
export function helpSections() {
  return SECTIONS.map(([title, rows]) => [title, rows.map(row => {
    if (Array.isArray(row)) {
      return row
    }
    if (row.startsWith(REVIEW)) {
      const action = row.slice(REVIEW.length)
      return [REVIEW_KEYS[action], REVIEW_LABELS[action]]
    }
    return [KEYS[row], HELP_LABELS[row] ?? LABELS[row]]
  })])
}

export function Help() {
  return html`<div class="help" role="dialog" aria-label="Keyboard shortcuts"
    onClick=${() => { ui.help = false; draw() }}>
    <div class="help-sheet">${helpSections().map(([title, rows]) => html`<section key=${title}>
      <h2>${title}</h2>
      <table>${rows.map(([key, what]) => html`<tr key=${key}><td><kbd>${key}</kbd></td><td>${what}</td></tr>`)}</table>
    </section>`)}</div>
  </div>`
}
