// What uses the machine's memory, swap, and CPU, for the web page's Resources view: the memory free, the share the
// dispatcher keeps for the person, and what running runs claimed, each agent-desk unit with what it works on and its
// claim, the runs the dispatcher holds for memory, the apps using the most memory outside agent-desk's units, and /tmp,
// a tmpfs that sits in RAM and swap. It reads systemd, ps, df, /proc, lib/memory.py, and the store's runs, as the
// resource-report skill's first two steps do, and changes nothing.
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import type { HeldRun } from './store.ts'

// lib/memory.py, which sets the memory each run needs and the share kept free for the person, and keeps the claims
// of running runs.
const MEMORY = join(import.meta.dirname, '..', 'lib', 'memory.py')
// How many processes outside agent-desk's units the view groups into apps.
export const TOP_PROCESSES = 15
// The value systemd shows for a figure it doesn't track, such as a unit's swap without swap accounting.
const UNSET = '18446744073709551615'
const KIB = 1024
const GIB = 1024 ** 3

/** A command's output, or null when it failed, as the server's host runner gives it. */
export type Run = (argv: string[]) => Promise<string | null>

/** How much a unit or an app uses: memory and swap in bytes, CPU time in seconds; null where systemd tracks none. */
export type Usage = { memory: number | null; swap: number | null; cpu: number | null }

/**
 * A running run's memory claim, as lib/memory.py's `claims` command reads it, in bytes: what it claimed at start, what
 * it counts for now, the run's peak so far when it counts for less, what the run's programs hold, and what it holds
 * back beyond them, which the dispatcher's memory gate counts against the memory free. `settle` is the minutes after
 * which it may shrink, or null when it has none.
 */
type Claim = { claimed: number; counted: number; peak: number | null; settle: number | null; used: number
  held: number }

/**
 * An active agent-desk unit: its kind, such as `agent`, `fix`, `chore`, `check`, or `dispatch` for the desk's own
 * services, and for a run, its project as the page's desk id and owner/repo, and what it works on: `label`, such as
 * `#12`, `PR #30`, or a chore's skill, and `number` with `pr` when it names an issue or a PR, and its memory claim,
 * or null when it holds none.
 */
type UnitUse = Usage & { unit: string; description: string; kind: string; desk: string | null
  repo: string | null; label: string; number: number | null; pr: boolean; claim: Claim | null }

/** A request the dispatcher holds for memory, with what it works on and why it waits, such as how much it needs. */
type MemoryWait = { desk: string | null; repo: string; kind: string; label: string; number: number | null
  pr: boolean; reason: string }

/** An app outside agent-desk's units, by the name its processes run as, with how many it has and what they use. */
type AppUse = { name: string; processes: number; memory: number; swap: number; cpu: number }

/**
 * What the Resources view shows, in bytes and seconds. `memory.claimed` is what running runs claimed and haven't taken
 * up, or null when lib/memory.py couldn't be read.
 */
export type Resources = {
  at: string
  memory: { total: number; free: number; reserve: number | null; claimed: number | null; swapTotal: number
    swapFree: number }
  units: UnitUse[]
  waiting: MemoryWait[]
  apps: AppUse[]
  tmp: number | null
}

let proc = '/proc'
// The GB lib/memory.py keeps free for the person, which follows only the machine's total memory.
let reserveGb: number | null = null

/** Points the /proc reads at another folder, as the tests do, and forgets the reserve read from the last one. */
export function useProc(other: string): void {
  proc = other
  reserveGb = null
}

/** The fields of /proc/meminfo the view reads, in bytes. */
function memoryOf(meminfo: string): Resources['memory'] {
  const field = (name: string) => Number(meminfo.match(new RegExp(`^${name}:\\s+(\\d+)`, 'm'))?.[1] ?? 0) * KIB
  return { total: field('MemTotal'), free: field('MemAvailable'), reserve: null, claimed: null,
    swapTotal: field('SwapTotal'), swapFree: field('SwapFree') }
}

/**
 * The live claims lib/memory.py's `claims` command prints, in bytes, by the unit that holds each, and what they hold
 * back in all, which the memory gate counts, or null when the command failed. A claim whose unit can't be read counts
 * in the total alone.
 */
export function claimsOf(printed: string | null): { byUnit: Map<string, Claim>; held: number } | null {
  let read: unknown
  try {
    read = JSON.parse(printed ?? '')
  } catch {
    return null
  }
  if (!Array.isArray(read)) {
    return null
  }
  const byUnit = new Map<string, Claim>()
  let held = 0
  for (const one of read as Record<string, unknown>[]) {
    const gb = (key: string) => typeof one[key] === 'number' ? one[key] * GIB : null
    const claim = { claimed: gb('claimed') ?? 0, counted: gb('counted') ?? 0, peak: gb('peak'),
      settle: typeof one.settle === 'number' ? one.settle : null, used: gb('used') ?? 0, held: gb('held') ?? 0 }
    held += claim.held
    if (typeof one.unit === 'string' && !byUnit.has(one.unit)) {
      byUnit.set(one.unit, claim)
    }
  }
  return { byUnit, held }
}

/** The units `systemctl --user list-units --plain --no-legend` lists, by name. */
function unitNames(listed: string): string[] {
  return listed.split('\n').map(line => line.trim().split(/\s+/)[0] ?? '').filter(name => name.endsWith('.service'))
}

/** Each unit's Description and usage from `systemctl show -p Id,Description,MemoryCurrent,...`, by unit name. */
export function unitUsage(shown: string): Map<string, Usage & { description: string }> {
  const figure = (value: string | undefined) => value === undefined || value === UNSET || !/^\d+$/.test(value) ? null
    : Number(value)
  return new Map(shown.split(/\n\s*\n/).flatMap(block => {
    const fields = new Map(block.split('\n').flatMap(line => {
      const at = line.indexOf('=')
      return at > 0 ? [[line.slice(0, at), line.slice(at + 1)] as const] : []
    }))
    const id = fields.get('Id')
    if (!id) {
      return []
    }
    const cpu = figure(fields.get('CPUUsageNSec'))
    return [[id, { description: fields.get('Description') ?? '', memory: figure(fields.get('MemoryCurrent')),
      swap: figure(fields.get('MemorySwapCurrent')), cpu: cpu === null ? null : cpu / 1e9 }]]
  }))
}

/** A unit name's project part, as lib/dispatch.py and lib/checks.py clean it. */
function cleaned(project: string): string {
  return project.replace(/[^A-Za-z0-9_.-]+/g, '-')
}

/**
 * How a run's kind and target read: an issue as `#12`, a PR as `PR #30`, as a fix's target and a branch check's review
 * are, and a chore by its skill.
 */
function targetOf(kind: string, target: string | null): Pick<UnitUse, 'label' | 'number' | 'pr'> {
  const found = (target ?? '').match(/^(pr-|idea-)?(\d+)$/)
  if (!found) {
    return { label: target ?? '', number: null, pr: false }
  }
  const number = Number(found[2])
  const pr = found[1] === 'pr-' || kind === 'fix' || kind === 'check'
  return { label: `${pr ? 'PR ' : ''}#${number}`, number, pr }
}

/**
 * The active agent-desk units with what each works on: a run by the unit the store names for it, a branch check by
 * its id at the end of its unit's name, and any other unit, such as the dispatcher, by the word after `agent-desk-`.
 */
function unitsOf(names: string[], usage: Map<string, Usage & { description: string }>, held: HeldRun[],
  deskOf: (repo: string) => string | null, claims: Map<string, Claim> = new Map()): UnitUse[] {
  return names.map(unit => {
    const use = { ...usage.get(unit) ?? { description: '', memory: null, swap: null, cpu: null },
      claim: claims.get(unit) ?? null }
    const run = held.find(one => one.kind !== 'check' && one.unit === unit)
      ?? held.find(one => one.kind === 'check' && one.status === 'running'
        && unit === `agent-desk-check-${cleaned(one.project)}-${one.id}.service`)
    if (run) {
      return { unit, ...use, kind: run.kind, desk: deskOf(run.project), repo: run.project,
        ...targetOf(run.kind, run.target) }
    }
    const kind = unit.match(/^agent-desk-([^-.]+)/)?.[1] ?? unit
    return { unit, ...use, kind, desk: null, repo: null, label: '', number: null, pr: false }
  }).sort((a, b) => (b.memory ?? 0) - (a.memory ?? 0))
}

/** The runs and branch checks the dispatcher holds for memory, oldest first, with why each waits. */
function memoryWaits(held: HeldRun[], deskOf: (repo: string) => string | null): MemoryWait[] {
  return held.filter(one => one.gate === 'memory' && (one.status === 'requested' || one.status === 'queued'))
    .map(one => ({ desk: deskOf(one.project), repo: one.project, kind: one.kind, ...targetOf(one.kind, one.target),
      reason: one.reason ?? '' }))
}

/** A process as `ps -o pid=,rss=,cputimes=,cgroup:512=,comm=` lists it, with its resident memory in bytes. */
export type Process = { pid: number; memory: number; cpu: number; cgroup: string; comm: string }

/** The processes ps lists, leaving out kernel threads, which hold no memory of their own. */
export function processesOf(listed: string): Process[] {
  return listed.split('\n').flatMap(line => {
    const found = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.*)$/)
    return found && Number(found[2]) > 0 ? [{ pid: Number(found[1]), memory: Number(found[2]) * KIB,
      cpu: Number(found[3]), cgroup: found[4] ?? '', comm: (found[5] ?? '').trim() }] : []
  })
}

/** Whether a process runs in an agent-desk unit, by its cgroup's path. */
export function inDeskUnit(cgroup: string): boolean {
  return /\/agent-desk-[^/]*\.service(\/|$)/.test(cgroup)
}

/** A process's swapped-out memory in bytes, from its /proc/<pid>/status. */
function swapOf(status: string): number {
  return Number(status.match(/^VmSwap:\s+(\d+)/m)?.[1] ?? 0) * KIB
}

/**
 * The app a process belongs to, by the file name of the program its command line starts with, so a browser's tabs
 * group with the browser: `comm` when the command line is empty. A process that rewrote its command line as one string,
 * as Firefox's tabs do, reads to its first space, and one rewritten as `sshd: me` reads `sshd`.
 */
export function appName(cmdline: string, comm: string): string {
  const parts = cmdline.split('\0').filter(Boolean)
  const program = (parts.length === 1 ? parts[0]?.split(' ')[0] : parts[0]) ?? ''
  const name = program.split(/[/\\]/).at(-1)?.split(': ')[0]?.replace(/:$/, '').trim() ?? ''
  return name || comm
}

/** Processes grouped by app, using the most memory and swap first. */
export function appsOf(processes: (Process & { swap: number; name: string })[]): AppUse[] {
  const apps = new Map<string, AppUse>()
  for (const one of processes) {
    const app = apps.get(one.name) ?? { name: one.name, processes: 0, memory: 0, swap: 0, cpu: 0 }
    app.processes += 1
    app.memory += one.memory
    app.swap += one.swap
    app.cpu += one.cpu
    apps.set(one.name, app)
  }
  return [...apps.values()].sort((a, b) => b.memory + b.swap - (a.memory + a.swap))
}

/** The TOP_PROCESSES processes outside agent-desk's units that use the most memory, counting what they swapped out. */
export function topOf<T extends Process & { swap: number }>(processes: T[]): T[] {
  return processes.filter(one => !inDeskUnit(one.cgroup)).sort((a, b) => b.memory + b.swap - (a.memory + a.swap))
    .slice(0, TOP_PROCESSES)
}

async function readProc(path: string): Promise<string> {
  return readFile(join(proc, path), 'utf8').catch(() => '')
}

/**
 * The TOP_PROCESSES processes outside agent-desk's units that use the most memory, counting what they swapped out,
 * grouped by app.
 */
async function topApps(listed: string): Promise<AppUse[]> {
  const outside = processesOf(listed).filter(one => !inDeskUnit(one.cgroup))
  const swapped = await Promise.all(outside.map(async one => ({ ...one,
    swap: swapOf(await readProc(join(String(one.pid), 'status'))) })))
  const top = topOf(swapped)
  return appsOf(await Promise.all(top.map(async one => ({ ...one,
    name: appName(await readProc(join(String(one.pid), 'cmdline')), one.comm) }))))
}

/** The bytes a filesystem uses, from `df -B1 --output=used`, or null when df failed. */
function usedOf(df: string | null): number | null {
  const used = df?.trim().split('\n')[1]?.trim()
  return used && /^\d+$/.test(used) ? Number(used) : null
}

/**
 * Reads what the view shows, with `run` for commands and `held` for the store's runs and checks that wait or work.
 * `deskOf` names the page's desk for a project, or null for one the server doesn't serve.
 */
export async function readResources(run: Run, held: HeldRun[], deskOf: (repo: string) => string | null):
  Promise<Resources> {
  const [meminfo, listed, ps, df, reserve, printed] = await Promise.all([
    readProc('meminfo'),
    run(['systemctl', '--user', 'list-units', 'agent-desk-*.service', '--state=active', '--plain', '--no-legend']),
    run(['ps', '-e', '-o', 'pid=,rss=,cputimes=,cgroup:512=,comm=']),
    run(['df', '-B1', '--output=used', '/tmp']),
    reserveGb === null ? run(['python3', '-B', MEMORY, 'default', 'reserveGB']) : Promise.resolve(String(reserveGb)),
    run(['python3', '-B', MEMORY, 'claims']),
  ])
  const claims = claimsOf(printed)
  if (reserve !== null && /^\d+(\.\d+)?$/.test(reserve.trim())) {
    reserveGb = Number(reserve.trim())
  }
  const names = unitNames(listed ?? '')
  const shown = names.length === 0 ? '' : await run(['systemctl', '--user', 'show',
    '-p', 'Id,Description,MemoryCurrent,MemorySwapCurrent,CPUUsageNSec', ...names])
  return {
    at: new Date().toISOString(),
    memory: { ...memoryOf(meminfo), reserve: reserveGb === null ? null : reserveGb * GIB,
      claimed: claims?.held ?? null },
    units: unitsOf(names, unitUsage(shown ?? ''), held, deskOf, claims?.byUnit),
    waiting: memoryWaits(held, deskOf),
    apps: await topApps(ps ?? ''),
    tmp: usedOf(df),
  }
}
