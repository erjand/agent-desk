// The header's account control: which account the desk's runs bill, the API key's spend this month against its budget,
// and whether a key is set, as lib/account.py reads them, and the switch, the key, and the budget, which go through
// the same script `agent-desk account` runs, so the page and the terminal can't disagree. One account covers every
// project. The key goes to the script on its standard input alone, and nothing here keeps it, logs it, or sends it
// back: a reading carries only whether a key is set and its last four characters.
import { execFile } from 'node:child_process'
import { join } from 'node:path'

import { childEnv, DESK_HOME } from './state.ts'
import type { Loop } from './state.ts'

const ACCOUNT = join(DESK_HOME, 'lib', 'account.py')
// How long the script may take, which reads the store for the month's spend.
const ACCOUNT_MS = 30_000
// The longest key the page may send; an Anthropic key is about a hundred characters.
export const MAX_KEY = 512

/**
 * The account the desk's runs bill, the month's spend on the API key in dollars, the budget as `{ on, dollars }` or
 * null before one was set, whether the budget holds new runs, the key: whether one is set and its last four
 * characters, or null, and when the server read it, in seconds since the epoch.
 */
export type Account = { account: 'subscription' | 'api'; spent: number; budget: { on: boolean; dollars: number } | null
  held: boolean; key: { set: boolean; ending: string | null }; readAt: number }

let reading: Account | null = null

/** The last reading the script gave, or null before the first. */
export function readAccount(): Account | null {
  return reading
}

/**
 * A reading from the script's JSON, rebuilt field by field, so the page's state carries no field the script might add,
 * or null when the JSON isn't one.
 */
export function accountOf(text: string): Omit<Account, 'readAt'> | null {
  let data: Record<string, unknown>
  try {
    data = JSON.parse(text) as Record<string, unknown>
  } catch {
    return null
  }
  const budget = data?.budget as { on?: unknown; dollars?: unknown } | null | undefined
  const key = data?.key as { set?: unknown; ending?: unknown } | undefined
  if ((data?.account !== 'subscription' && data?.account !== 'api') || typeof data.spent !== 'number'
    || typeof data.held !== 'boolean' || typeof key?.set !== 'boolean'
    || (budget !== null && (typeof budget?.on !== 'boolean' || typeof budget.dollars !== 'number'))) {
    return null
  }
  const ending = key.set && typeof key.ending === 'string' ? key.ending.slice(-4) : null
  return { account: data.account, spent: data.spent, held: data.held, key: { set: key.set, ending },
    budget: budget === null ? null : { on: budget.on as boolean, dollars: budget.dollars as number } }
}

/**
 * Runs lib/account.py with `args`, and `stdin` on its standard input, and gives its exit status, output, and error
 * output. The arguments never hold the key.
 */
function runAccount(args: string[], stdin?: string): Promise<{ ok: boolean; out: string; err: string }> {
  return new Promise(resolve => {
    const child = execFile('python3', ['-B', ACCOUNT, ...args], { env: childEnv(), timeout: ACCOUNT_MS },
      (error, out, err) => resolve({ ok: !error, out: String(out), err: String(err) }))
    child.stdin?.on('error', () => {})
    child.stdin?.end(stdin ?? '')
  })
}

/** Reads the account, the month's spend, the budget, and whether a key is set again, and says whether it could. */
export async function refreshAccount(): Promise<boolean> {
  const { ok, out } = await runAccount(['json'])
  const read = ok ? accountOf(out) : null
  if (read !== null) {
    reading = { ...read, readAt: Math.floor(Date.now() / 1000) }
  }
  return read !== null
}

/** The script's reason for a refusal, its first line of error output without the `agent-desk: ` it may lead with. */
function reasonOf(err: string, fallback: string): string {
  const line = err.split('\n').find(one => one.trim() !== '')?.trim().replace(/^agent-desk: /, '').replace(/\.$/, '')
  return line || fallback
}

/** Runs the script, reads the account again, and publishes it. Gives null when the script took it, or its reason. */
async function act(loop: Loop, args: string[], fallback: string, stdin?: string): Promise<string | null> {
  const { ok, err } = await runAccount(args, stdin)
  await refreshAccount()
  loop.publish()
  return ok ? null : reasonOf(err, fallback)
}

/** Bills `account` from the next run on, as `agent-desk account api` or `subscription` does. */
export function switchAccount(loop: Loop, account: 'subscription' | 'api'): Promise<string | null> {
  return act(loop, [account], `couldn't switch to ${account}`)
}

/** Saves `key` as `agent-desk account key` does, from its standard input. */
export function setKey(loop: Loop, key: string): Promise<string | null> {
  return act(loop, ['key'], 'couldn\'t save the API key', key)
}

/** Removes the key, which moves an `api` account back to `subscription`, as `agent-desk account key --clear` does. */
export function clearKey(loop: Loop): Promise<string | null> {
  return act(loop, ['key', '--clear'], 'couldn\'t remove the API key')
}

/** Turns the budget on at `dollars`, or off, keeping its amount, as `agent-desk account budget` does. */
export function changeBudget(loop: Loop, on: boolean, dollars?: number): Promise<string | null> {
  return act(loop, ['budget', on ? Number(dollars).toFixed(2) : 'off'], 'couldn\'t change the API budget')
}

/** Whether `dollars` is a budget `agent-desk account budget` takes: a positive amount with at most two decimals. */
export function budgetAmount(dollars: unknown): dollars is number {
  return typeof dollars === 'number' && Number.isFinite(dollars) && dollars > 0 && dollars < 1e9
    && Math.abs(Math.round(dollars * 100) - dollars * 100) < 1e-6
}
