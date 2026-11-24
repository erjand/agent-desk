import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'

import { childEnv } from './state.ts'

const REFRESH_MS = 60_000
const TIMEOUT_MS = 10_000

type Window = { usedPercent: number; windowDurationMins: number; resetsAt: number }
export type CodexUsage = { billing: 'api' | 'unavailable' } | { billing: 'subscription'; windows: Window[] }

const unavailable: CodexUsage = { billing: 'unavailable' }

function fields(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : undefined
}

/** Reads live windows from the Codex bucket in descending order of duration. */
export function codexWindows(value: unknown, now = Date.now() / 1000): Window[] {
  const result = fields(value)
  const single = fields(result?.rateLimits)
  const buckets = fields(result?.rateLimitsByLimitId)
  const bucket = fields(buckets?.codex) ?? (single?.limitId == null || single.limitId === 'codex' ? single : undefined)
  const windows = [fields(bucket?.primary), fields(bucket?.secondary)].flatMap(window => {
    const { usedPercent, windowDurationMins, resetsAt } = window ?? {}
    if (typeof usedPercent !== 'number' || !Number.isFinite(usedPercent) || usedPercent < 0
      || typeof windowDurationMins !== 'number' || !Number.isSafeInteger(windowDurationMins) || windowDurationMins < 1
      || typeof resetsAt !== 'number' || !Number.isFinite(resetsAt) || resetsAt <= now) {
      return []
    }
    return [{ usedPercent, windowDurationMins, resetsAt }]
  })
  return windows.sort((a, b) => b.windowDurationMins - a.windowDurationMins)
}

/** Reads the CLI's effective account and, on ChatGPT, requests that account's rate limits. */
export function fetchCodexUsage(command = 'codex'): Promise<CodexUsage> {
  return new Promise(resolve => {
    const child = spawn(command, ['app-server', '--listen', 'stdio://'],
      { env: childEnv(), stdio: ['pipe', 'pipe', 'ignore'] })
    const lines = createInterface({ input: child.stdout })
    let finished = false
    const finish = (usage: CodexUsage) => {
      if (finished) {
        return
      }
      finished = true
      resolve(usage)
      clearTimeout(timeout)
      lines.close()
      child.kill()
    }
    const fail = () => finish(unavailable)
    const timeout = setTimeout(fail, TIMEOUT_MS)
    child.on('error', fail)
    child.stdin.on('error', fail)
    lines.on('close', fail)

    const send = (method: string, id?: number, params?: object) =>
      child.stdin.write(`${JSON.stringify({ method, id, params })}\n`)
    let expected = 1
    lines.on('line', line => {
      let reply
      try {
        reply = fields(JSON.parse(line))
      } catch {
        return
      }
      if (reply?.id !== expected || reply.method !== undefined) {
        return
      }
      const result = fields(reply.result)
      if (!result) {
        finish(expected === 3 ? { billing: 'subscription', windows: [] } : unavailable)
      } else if (expected === 1) {
        expected = 2
        send('initialized')
        send('account/read', 2, { refreshToken: false })
      } else if (expected === 2) {
        const type = fields(result.account)?.type
        if (type === 'chatgpt') {
          expected = 3
          send('account/rateLimits/read', 3)
        } else {
          finish(type === 'apiKey' ? { billing: 'api' } : unavailable)
        }
      } else {
        finish({ billing: 'subscription', windows: codexWindows(result) })
      }
    })
    send('initialize', 1, { clientInfo: { name: 'agent-desk', title: 'Agent Desk', version: '1' } })
  })
}

let usage = unavailable as CodexUsage
let readAt = 0
let pending: Promise<void> | null = null

export function readCodexUsage(now = Date.now()): CodexUsage {
  if (now - readAt >= 2 * REFRESH_MS) {
    return unavailable
  }
  if (usage.billing === 'subscription') {
    return { billing: 'subscription', windows: usage.windows.filter(window => window.resetsAt * 1000 > now) }
  }
  return usage
}

/** Starts at most one app-server read per minute, with an early refresh at a known reset. */
export function refreshCodexUsage(now = Date.now()): Promise<void> | null {
  const resetDue = usage.billing === 'subscription' && usage.windows.some(({ resetsAt }) => resetsAt * 1000 <= now)
  if (pending || (now - readAt < REFRESH_MS && !resetDue)) {
    return null
  }
  pending = fetchCodexUsage().then(read => {
    usage = read
    readAt = Date.now()
  }).finally(() => { pending = null })
  return pending
}
