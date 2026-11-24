// The header shows billing for the selected issue runners. The popover sets the runner and the machine's Claude
// account, weekly limit, API key, and monthly budget.
import { html } from './vendor/preact-htm.js'
import { draw, post, shownDesks, ui } from './app-state.js'

// The percentage and the dollars the popover offers before a limit or a budget was ever set.
const DEFAULT_LIMIT = 80
const DEFAULT_BUDGET = 200

// What the popover says at or over the limit and the budget.
const HELD = 'New agent, chore, and fix runs wait until usage falls under the limit.'
const BUDGET_HELD = 'New agent, chore, and fix runs and the Chat\'s turns wait until the month turns or the budget '
  + 'rises.'

// The popover's state when it opens or closes: the percentage and the dollars typed and not yet saved, or null, why it
// refused them, the switch or the clear waiting for its confirm, whether the key field shows, and why the server
// refused the key.
const CLOSED = { open: false, draft: null, error: null, budgetDraft: null, budgetError: null, confirm: null,
  keyOpen: false, keyError: null }

// What each confirm asks, and its button.
const CONFIRMS = {
  api: ['Bill the API key from the next run on? Runs at work keep the account they started on.', 'Switch to API'],
  subscription: ['Bill the Claude subscription from the next run on? Runs at work keep the account they started on.',
    'Switch to Subscription'],
  clear: ['Remove the API key? On API, the desk moves back to the subscription.', 'Clear Key'],
}

const two = number => String(number).padStart(2, '0')

/** A time in the coming week, in local time, such as `Wed 04:00`, as lib/usage.py writes it; null for no time. */
export function weekAt(at) {
  const date = at instanceof Date ? at : new Date(at)
  if (Number.isNaN(date.getTime())) {
    return null
  }
  return `${date.toLocaleDateString('en-US', { weekday: 'short' })} ${two(date.getHours())}:${two(date.getMinutes())}`
}

/** A reading as a whole percentage, rounded down as lib/usage.py shows it, so one under the limit never reads at it. */
const shown = percent => `${Math.floor(percent)}%`

/** Dollars as the popover writes them, such as `$63.20`, as lib/budget.py prints them. */
export const dollars = amount => `$${amount.toLocaleString('en-US', { minimumFractionDigits: 2,
  maximumFractionDigits: 2 })}`

/**
 * Dollars as the header writes them: whole dollars, the spend rounded down so one under the budget never reads at it,
 * and a budget with cents keeps them, such as `$63`, `$200`, or `$49.50`.
 */
export function headerDollars(amount, down = false) {
  return Number.isInteger(amount) ? `$${amount.toLocaleString('en-US')}`
    : down ? `$${Math.floor(amount).toLocaleString('en-US')}` : dollars(amount)
}

/** The account the desk's runs bill: the server's, or the subscription before the server says. */
const inUse = account => account?.account ?? 'subscription'

/** Whether the limit holds new runs: it is on and the reading counts and is at or over it. */
function overLimit(usage) {
  return Boolean(usage?.on) && usage.problem === null && usage.percent !== null && usage.percent >= usage.limit
}

/**
 * The API key's reading for the header, its tone, and its description: the month's spend and the budget while it is on,
 * such as `$63 / $200`, or the spend alone while it is off. The reading takes the Hold amber while
 * the budget holds new runs.
 */
export function apiView(account) {
  const budget = account.budget?.on ? account.budget : null
  const text = `${headerDollars(account.spent, true)}${budget ? ` / ${headerDollars(budget.dollars)}` : ''}`
  const spend = `The API key's spend is ${dollars(account.spent)} this month`
  if (!budget) {
    return { text, tone: 'under', tip: `${spend}; the API budget is off` }
  }
  if (account.held) {
    return { text, tone: 'over', tip: `${spend}, at the ${dollars(budget.dollars)} budget, so new agent, chore, and `
      + 'fix runs and the Chat\'s turns wait' }
  }
  return { text, tone: 'under', tip: `${spend}, under the ${dollars(budget.dollars)} budget` }
}

/** The popover's line about the last reading: its percentage, when it was read, and when its window resets. */
export function readingLine(usage) {
  if (usage.problem !== null || usage.percent === null || usage.readAt === null) {
    return 'Weekly usage unavailable.'
  }
  const resets = usage.resetsAt ? weekAt(usage.resetsAt) : null
  return `Last read ${shown(usage.percent)} at ${weekAt(usage.readAt * 1000)}${resets ? `; the week resets ${resets}`
    : ''}.`
}

/** The popover's line about the API key's spend this month: its dollars and when the server read them. */
export function spendLine(account) {
  const at = account.readAt ? weekAt(account.readAt * 1000) : null
  return `Last read ${dollars(account.spent)}${at ? ` at ${at}` : ''}.`
}

/** The key's row's words: `No key set`, or the key's last four characters. */
export function keyLine(account) {
  return account.key.set ? `Key ending in ${account.key.ending}` : 'No key set'
}

/** A typed percentage as a whole number from 0 to 100, or null for anything else. */
export function percentOf(text) {
  const trimmed = String(text ?? '').trim()
  return /^\d{1,3}$/.test(trimmed) && Number(trimmed) <= 100 ? Number(trimmed) : null
}

/**
 * A typed budget as a positive number of dollars, such as `200`, `49.50`, or `$200`, with at most two decimals, as
 * `agent-desk account budget` takes one, or null for anything else.
 */
export function amountOf(text) {
  const found = /^\$?(\d{1,9}(?:\.\d{1,2})?)$/.exec(String(text ?? '').trim())
  const amount = found ? Number(found[1]) : 0
  return amount > 0 ? amount : null
}

/**
 * Saves the limit on at the typed percentage, or off, unless the percentage is refused or nothing changed. The header
 * shows the new setting at once, and the server's next state confirms it.
 */
export function saveUsage(on, text) {
  const usage = ui.server?.usage
  if (!usage) {
    return
  }
  const percent = percentOf(text)
  if (on && percent === null) {
    ui.usage.error = 'Enter a whole percentage from 0 to 100.'
    draw()
    return
  }
  ui.usage.error = null
  ui.usage.draft = null
  if (on !== usage.on || (on && percent !== usage.limit)) {
    ui.server.usage = { ...usage, on, limit: on ? percent : usage.limit }
    void post('/api/usage-limit', on ? { on, percent } : { on })
  }
  draw()
}

/**
 * Saves the API budget on at the typed dollars, or off, unless the amount is refused or nothing changed. The header
 * shows the new budget at once, and the server's answer confirms it.
 */
export function saveBudget(on, text) {
  const account = ui.server?.account
  if (!account) {
    return
  }
  const amount = amountOf(text)
  if (on && amount === null) {
    ui.usage.budgetError = 'Enter a positive number of dollars, such as 200 or 49.50.'
    draw()
    return
  }
  ui.usage.budgetError = null
  ui.usage.budgetDraft = null
  const budget = account.budget
  if (on !== Boolean(budget?.on) || (on && amount !== budget?.dollars)) {
    ui.server.account = { ...account, budget: on ? { on, dollars: amount } : budget && { ...budget, on: false } }
    void post('/api/api-budget', on ? { on, dollars: amount } : { on }).then(sent => {
      if (sent && ui.server) {
        ui.server.account = sent
        draw()
      }
    })
  }
  draw()
}

/**
 * Keeps the keyboard in the popover once the button it was on goes, as a confirm's or the key field's do, by moving it
 * to the account in use, so Escape still closes the popover.
 */
function keepFocus() {
  setTimeout(() => {
    const at = document.activeElement
    if (ui.usage.open && (!at || at === document.body)) {
      document.querySelector('#usage-limit .account-choice button[aria-pressed="true"]')?.focus()
    }
  })
}

/** Asks the server to do what a confirm named, and shows the account it answers with. */
async function confirmed(asked) {
  ui.usage.confirm = null
  draw()
  keepFocus()
  const sent = asked === 'clear' ? await post('/api/account-key', { clear: true })
    : await post('/api/account', { account: asked })
  if (sent && ui.server) {
    ui.server.account = sent
    draw()
  }
}

/** Asks to confirm a switch to `account`, or the key's clear, in the popover; asking the same again takes it back. */
export function askConfirm(asked) {
  ui.usage.confirm = ui.usage.confirm === asked ? null : asked
  draw()
  keepFocus()
}

/** Sends the key on Enter in its field. */
function onKeyEnter(event) {
  if (event.key === 'Enter') {
    event.preventDefault()
    void sendKey(event.currentTarget)
  }
}

/** Shows the key field, or hides it and empties it. */
function showKey(open) {
  const field = document.getElementById('account-key')
  if (!open && field) {
    field.value = ''
  }
  ui.usage.keyOpen = open
  ui.usage.keyError = null
  draw()
  if (open) {
    setTimeout(() => document.getElementById('account-key')?.focus())
  } else {
    keepFocus()
  }
}

/**
 * Sends the key the field holds to the server once, over a POST, and empties the field whether the server took it or
 * not, so the browser keeps no copy. The page keeps only what the server answers: whether a key is set and its last
 * four characters.
 */
export async function sendKey(field = document.getElementById('account-key')) {
  const key = String(field?.value ?? '').trim()
  if (field) {
    field.value = ''
  }
  if (!key) {
    ui.usage.keyError = 'Paste an API key first.'
    draw()
    return
  }
  ui.usage.keyError = null
  ui.usage.keyOpen = false
  draw()
  keepFocus()
  const sent = await post('/api/account-key', { key })
  if (sent && ui.server) {
    ui.server.account = sent
    draw()
  }
}

/**
 * Closes the popover, and with `focus`, gives the keyboard back to its button. A press outside it saves a valid
 * percentage or budget typed and not yet saved, as leaving the box would; Escape drops them, and the fields save on a
 * change only while the popover is open, so the change the browser fires as Escape moves the keyboard saves nothing. Either way the key
 * field empties, and a switch or a clear waiting for its confirm drops.
 */
export function closeUsage(focus = false) {
  if (!ui.usage.open) {
    return
  }
  const { draft, budgetDraft } = ui.usage
  if (!focus && draft !== null && ui.server?.usage?.on && percentOf(draft) !== null) {
    saveUsage(true, draft)
  }
  if (!focus && budgetDraft !== null && ui.server?.account?.budget?.on && amountOf(budgetDraft) !== null) {
    saveBudget(true, budgetDraft)
  }
  const field = document.getElementById('account-key')
  if (field) {
    field.value = ''
  }
  ui.usage = { ...CLOSED }
  draw()
  if (focus) {
    document.querySelector('.usage-button')?.focus()
  }
}

/** Closes the popover on a press outside it. */
export function onUsageOutside(event) {
  if (ui.usage.open && !event.target?.closest?.('.usage')) {
    closeUsage()
  }
}

function toggle() {
  if (ui.usage.open) {
    closeUsage()
    return
  }
  ui.usage = { ...CLOSED, open: true }
  draw()
  setTimeout(() => (document.getElementById('issue-runner') ?? document.getElementById('usage-on'))?.focus())
}

function onKeyDown(event) {
  if (event.key === 'Escape' && ui.usage.open) {
    event.stopPropagation()
    event.preventDefault()
    closeUsage(true)
  }
}

function onFocusOut(event) {
  if (ui.usage.open && event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) {
    closeUsage()
  }
}

/** A switch: a checkbox drawn as a track and knob, with its label. */
function Switch({ id, checked, label, onChange }) {
  return html`<label class="usage-switch"><input id=${id} type="checkbox" role="switch" checked=${checked}
    onChange=${onChange} /><span>${label}</span></label>`
}

/** A confirm waiting in the popover: what it asks, its button, and Cancel. */
function Confirm({ asked }) {
  const [question, label] = CONFIRMS[asked]
  return html`<div class="account-confirm" role="group" aria-label=${question}>
    <p>${question}</p>
    <div class="account-buttons">
      <button type="button" class="primary" onClick=${() => void confirmed(asked)}>${label}</button>
      <button type="button" onClick=${() => askConfirm(asked)}>Cancel</button>
    </div>
  </div>`
}

/**
 * The account's part of the popover: the Subscription or API choice, which asks to confirm a switch, and the key's
 * row, with Set Key's password field and Clear Key, which asks to confirm too. API waits for a key.
 */
export function AccountPart({ account }) {
  const used = inUse(account)
  const confirm = ui.usage.confirm
  const choice = (name, label) => html`<button type="button" aria-pressed=${String(used === name)}
    disabled=${name === 'api' && !account.key.set && used !== 'api'}
    aria-describedby=${name === 'api' && !account.key.set ? 'account-no-key' : undefined}
    onClick=${() => used !== name && askConfirm(name)}>${label}</button>`
  return html`<div class="account-part">
    <div class="account-choice" role="group" aria-label="The account Claude runs and Chat bill">
      <span>Bill</span>${choice('subscription', 'Subscription')}${choice('api', 'API')}
    </div>
    ${account.key.set ? null : html`<p id="account-no-key" class="muted">Set a key first to bill the API.</p>`}
    ${confirm === 'api' || confirm === 'subscription' ? html`<${Confirm} asked=${confirm} />` : null}
    <div class="account-key">
      <span class=${account.key.set ? '' : 'muted'}>${keyLine(account)}</span>
      <div class="account-buttons">
        <button type="button" aria-expanded=${String(ui.usage.keyOpen)} onClick=${() => showKey(!ui.usage.keyOpen)}>
          Set Key</button>
        ${account.key.set ? html`<button type="button" onClick=${() => askConfirm('clear')}>Clear Key</button>` : null}
      </div>
    </div>
    ${confirm === 'clear' ? html`<${Confirm} asked="clear" />` : null}
    ${ui.usage.keyOpen ? html`<div class="account-key-field">
      <label for="account-key">Anthropic API key</label>
      <input id="account-key" type="password" autocomplete="off" autocapitalize="off" spellcheck="false"
        data-1p-ignore="true" data-lpignore="true" data-bwignore="true" data-form-type="other"
        aria-invalid=${String(ui.usage.keyError !== null)}
        aria-describedby=${ui.usage.keyError === null ? 'account-key-note' : 'account-key-error account-key-note'}
        onKeyDown=${onKeyEnter}
        onInput=${() => { if (ui.usage.keyError !== null) { ui.usage.keyError = null; draw() } }} />
      <div class="account-buttons">
        <button type="button" class="primary" onClick=${() => void sendKey()}>Save</button>
        <button type="button" onClick=${() => showKey(false)}>Cancel</button>
      </div>
      ${ui.usage.keyError === null ? null
        : html`<p id="account-key-error" class="usage-error" role="alert">${ui.usage.keyError}</p>`}
      <p id="account-key-note" class="muted">The server keeps it in a file only you can read, and the page never shows
        more than its last four characters.</p>
    </div>` : null}
  </div>`
}

/** The budget's part of the popover: its switch, its dollars, which wait while it is off, and the month's spend. */
export function BudgetPart({ account }) {
  const budget = account.budget
  const draft = ui.usage.budgetDraft ?? String(budget?.dollars ?? DEFAULT_BUDGET)
  const error = ui.usage.budgetError
  const on = Boolean(budget?.on)
  return html`<div class="account-part">
    <${Switch} id="budget-on" checked=${on} label="Hold new runs at a monthly API budget"
      onChange=${event => saveBudget(event.currentTarget.checked, draft)} />
    <form class="usage-percent" onSubmit=${event => { event.preventDefault(); saveBudget(true, draft) }}>
      <label for="budget-dollars">Budget</label>
      <span aria-hidden="true">$</span>
      <input id="budget-dollars" type="text" inputmode="decimal" value=${draft} disabled=${!on}
        aria-invalid=${String(error !== null)} aria-describedby=${error === null ? undefined : 'budget-error'}
        onInput=${event => { ui.usage.budgetDraft = event.currentTarget.value; ui.usage.budgetError = null; draw() }}
        onChange=${event => ui.usage.open && saveBudget(true, event.currentTarget.value)} />
      <span>a month</span>
    </form>
    ${error === null ? null : html`<p id="budget-error" class="usage-error" role="alert">${error}</p>`}
    <p class="muted">${spendLine(account)}</p>
    ${account.held ? html`<p class="usage-held">${BUDGET_HELD}</p>` : null}
  </div>`
}

/** The weekly limit's part of the popover: its switch, its percentage, which waits while it is off, and the reading. */
function LimitPart({ usage, used, showReading }) {
  const draft = ui.usage.draft ?? String(usage.limit ?? DEFAULT_LIMIT)
  const error = ui.usage.error
  return html`<div class="account-part">
    <${Switch} id="usage-on" checked=${usage.on} label="Hold new runs at a weekly usage limit"
      onChange=${event => saveUsage(event.currentTarget.checked, draft)} />
    <form class="usage-percent" onSubmit=${event => { event.preventDefault(); saveUsage(true, draft) }}>
      <label for="usage-percent">Limit</label>
      <input id="usage-percent" type="number" min="0" max="100" step="1" inputmode="numeric" value=${draft}
        disabled=${!usage.on} aria-invalid=${String(error !== null)}
        aria-describedby=${error === null ? undefined : 'usage-error'}
        onInput=${event => { ui.usage.draft = event.currentTarget.value; ui.usage.error = null; draw() }}
        onChange=${event => ui.usage.open && saveUsage(true, event.currentTarget.value)} />
      <span aria-hidden="true">%</span>
    </form>
    ${error === null ? null : html`<p id="usage-error" class="usage-error" role="alert">${error}</p>`}
    ${showReading && used ? html`<p class="muted">${readingLine(usage)}${usage.on && usage.problem !== null
      ? ` New runs start while usage is unavailable (${usage.problem}).` : ''}</p>` : null}
    ${usage.on && !used ? html`<p class="muted">The desk bills the API key, so the limit holds no runs.</p>` : null}
    ${used && overLimit(usage) ? html`<p class="usage-held">${HELD}</p>` : null}
  </div>`
}

const RUNNER_NAMES = { claude: 'Claude Code', codex: 'Codex', local: 'Local' }

async function saveIssueRunner(project, choice) {
  if (ui.issueRunnerSaving) {
    return
  }
  ui.issueRunnerSaving = true
  draw()
  const target = project ? { desk: project.id } : { all: true }
  await post('/api/issue-runner', { ...target, runner: choice }, project?.title)
  ui.issueRunnerSaving = false
  draw()
}

function IssueRunnerPart({ projects }) {
  const project = ui.tab === 'all' ? null : projects[0]
  const override = projects[0].issueRunner.override
  const mixed = projects.some(desk => desk.issueRunner.override !== override)
  const current = mixed ? 'mixed' : override ?? 'default'
  const fallback = RUNNER_NAMES[project?.issueRunner.config] ?? 'invalid setting'
  return html`<div class="issue-runner">
    <label>${project?.title ?? 'Runner for all projects'}
      <select id="issue-runner" aria-label=${`Agent runner for ${project?.title ?? 'all projects'}`}
        value=${current} aria-disabled=${ui.issueRunnerSaving ? 'true' : undefined}
        onChange=${event => {
          const choice = event.currentTarget.value
          event.currentTarget.value = current
          if (choice !== current && (choice === 'default' || Object.hasOwn(RUNNER_NAMES, choice))) {
            return saveIssueRunner(project, choice)
          }
        }}>
        ${mixed ? html`<option value="mixed" disabled>Mixed settings</option>` : null}
        <option value="default">${project ? `Use project default (${fallback})` : 'Use project default'}</option>
        <option value="claude">Claude Code</option>
        <option value="codex">Codex</option>
        <option value="local">Local</option>
      </select>
    </label>
  </div>`
}

function windowLabel(minutes) {
  if (minutes === 7 * 24 * 60) {
    return 'Weekly'
  }
  if (minutes === 5 * 60) {
    return '5-hour'
  }
  if (minutes % 1440 === 0) {
    const days = minutes / 1440
    return `${days} ${days === 1 ? 'day' : 'days'}`
  }
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return [hours && `${hours} ${hours === 1 ? 'hr' : 'hrs'}`, rest && `${rest} min`].filter(Boolean).join(' ')
}

function quotaView(label, usedPercent, reset, tone = 'under') {
  const text = `${label} ${shown(Math.max(0, 100 - usedPercent))} left`
  return { text, tone, detail: `${text}${reset ? `, resets ${reset}` : ''}.` }
}

function subscriptionViews(windows) {
  return windows.length ? windows
    : [{ text: 'Quota unavailable', tone: 'unread', detail: 'Subscription quota unavailable.' }]
}

function billingReadings(projects, usage, account, codex) {
  const runners = new Set(projects.map(desk => desk.issueRunner?.effective))
  const readings = []
  if (runners.has('claude')) {
    if (inUse(account) === 'api') {
      const { text, tone, tip } = apiView(account)
      readings.push({ name: 'Claude', entries: [{ text: `API ${text}`, tone, detail: tip }] })
    } else {
      const windows = []
      if (account?.account === 'subscription') {
        if (usage.problem === null && Number.isFinite(usage.percent) && usage.percent >= 0) {
          windows.push(quotaView('Weekly', usage.percent, usage.resetsAt && weekAt(usage.resetsAt),
            overLimit(usage) ? 'over' : 'under'))
        }
        if (usage.fiveHour) {
          const { percent, resetsAt } = usage.fiveHour
          windows.push(quotaView('5-hour', percent, resetsAt && weekAt(resetsAt)))
        }
      }
      readings.push({ name: 'Claude', entries: subscriptionViews(windows) })
    }
  }
  if (runners.has('codex')) {
    const windows = codex?.billing === 'subscription' ? codex.windows.map(window =>
      quotaView(windowLabel(window.windowDurationMins), window.usedPercent, weekAt(window.resetsAt * 1000))) : []
    readings.push({ name: 'Codex', entries: codex?.billing === 'api'
      ? [{ text: 'API key', tone: 'under', detail: 'Bills the API key from the Codex CLI sign-in.' }]
      : subscriptionViews(windows) })
  }
  if (runners.has('local')) {
    readings.push({ name: 'Local', entries: [{ text: 'Model provider', tone: 'idle',
      detail: 'Uses the provider and model in .agent-desk.json.' }] })
  }
  if (projects.length && runners.has(null)) {
    readings.push({ name: 'Runner', entries: [{ text: 'Unavailable', tone: 'unread', detail: 'The runner setting is invalid.' }] })
  }
  return readings
}

export function UsagePopover({ usage, account, readings = billingReadings(shownDesks(), usage, account,
  ui.server?.codexUsage) }) {
  const used = inUse(account) === 'subscription'
  const projects = shownDesks().filter(desk => desk.issueRunner)
  return html`<div id="usage-limit" class="usage-pop" role="dialog" aria-label="Agent runner and billing">
    ${projects.length ? html`<h3>Agent runner</h3><${IssueRunnerPart} projects=${projects} />` : null}
    ${readings.length ? html`<div class="account-part">${readings.map(({ name, entries }) =>
      html`<div key=${name} class="billing-provider"><strong>${name}</strong>
        <div>${entries.map(({ detail }) => html`<p>${detail}</p>`)}</div></div>`)}</div>` : null}
    ${account ? html`<h3>Claude billing</h3><${AccountPart} account=${account} />` : null}
    <h3>Claude subscription limit</h3>
    <${LimitPart} usage=${usage} used=${used}
      showReading=${account && projects.some(desk => desk.issueRunner.effective === 'claude')} />
    ${account ? html`<h3>Claude API budget</h3><${BudgetPart} account=${account} />` : null}
  </div>`
}

export function UsageLimit() {
  const usage = ui.server?.usage
  if (!usage) {
    return null
  }
  const account = ui.server.account ?? null
  const readings = billingReadings(shownDesks(), usage, account, ui.server.codexUsage)
  const tone = readings.some(({ entries }) => entries.some(entry => entry.tone === 'over')) ? 'over' : 'under'
  const description = readings.map(({ name, entries }) => `${name}. ${entries.map(entry => entry.detail).join(' ')}`).join(' ')
  const open = ui.usage.open
  return html`<span class="usage" onKeyDown=${onKeyDown} onFocusOut=${onFocusOut}>
    <button type="button" class=${`usage-button ${tone}`}
      aria-description=${description || undefined} aria-haspopup="dialog" aria-expanded=${String(open)}
      aria-controls=${open ? 'usage-limit' : undefined} onClick=${toggle}>
      ${readings.length ? readings.map(({ name, entries }) => html`<span key=${name} class="balance">
        <strong class="account-name">${name}</strong>
        ${entries.map(({ text, tone }, index) => html`${index ? html`<span aria-hidden="true">·</span>` : null}
          <span class=${`reading ${tone}`}>${text}</span>`)}
      </span>`) : 'Billing'}
    </button>
    ${open ? html`<${UsagePopover} usage=${usage} account=${account} readings=${readings} />` : null}
  </span>`
}
