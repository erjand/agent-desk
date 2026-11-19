// The machine's load and the header's gauges. Like board.ts, it imports no Claude Code module and no Node module, so
// the pane and the web server share it.
import type { SystemLoad } from '../types'

/** The CPU's idle and total time from /proc/stat's first line, in clock ticks since boot. */
export type CpuTimes = { idle: number; total: number }

/**
 * The machine's load from /proc/meminfo and /proc/stat read together: memory in use, counting the page cache the
 * kernel can drop as free, and CPU use since the previous counters, idle counting I/O wait too.
 */
export function loadOf(proc: string, previous: CpuTimes | null): { load: SystemLoad; cpu: CpuTimes } {
  const kib = (key: string) => Number(proc.match(new RegExp(`^${key}:\\s+(\\d+)`, 'm'))?.[1] ?? 0)
  const gib = (value: number) => value / 1_048_576
  const fields = (proc.match(/^cpu\s+(.+)$/m)?.[1] ?? '').trim().split(/\s+/).map(Number)
  const cpu = { idle: (fields[3] ?? 0) + (fields[4] ?? 0), total: fields.reduce((sum, one) => sum + one, 0) }
  const spent = previous ? cpu.total - previous.total : 0
  return {
    load: {
      ramUsed: gib(kib('MemTotal') - kib('MemAvailable')), ramTotal: gib(kib('MemTotal')),
      swapUsed: gib(kib('SwapTotal') - kib('SwapFree')), swapTotal: gib(kib('SwapTotal')),
      cpu: previous && spent > 0 ? 100 * (1 - (cpu.idle - previous.idle) / spent) : null,
    },
    cpu,
  }
}

/**
 * The header's gauges: free RAM and swap, which an OOM guard such as earlyoom watches together, and CPU use. Red always
 * asks the person to act, and a healthy gauge reads faint green. Free swap turns yellow under 10%: nothing needs doing
 * yet, but with swap spent, free RAM is all that stands before the guard stops the biggest process. Free RAM turns
 * yellow under 15% and red under 10%, ahead of where such a guard usually acts.
 */
export function gaugesOf(machine: SystemLoad): { text: string; color: string }[] {
  const red = '#ff3b30'
  // A muted green: dimming a theme color draws it in the theme's gray instead.
  const healthy = '#5f875f'
  const size = (gib: number) => `${gib < 10 ? gib.toFixed(1) : Math.round(gib)}G`
  const freeHue = (free: number, total: number, low: number, spent: number) => (free < total * spent ? red
    : free < total * low ? 'warning' : healthy)
  const ram = machine.ramTotal - machine.ramUsed
  const swap = machine.swapTotal - machine.swapUsed
  return [
    { text: `RAM ${size(ram)} free`, color: freeHue(ram, machine.ramTotal, 0.15, 0.1) },
    ...(machine.swapTotal > 0
      ? [{ text: `swap ${size(swap)} free`, color: swap < machine.swapTotal * 0.1 ? 'warning' : healthy }] : []),
    ...(machine.cpu === null ? [] : [{ text: `CPU ${Math.round(machine.cpu)}%`,
      color: machine.cpu >= 90 ? red : machine.cpu >= 70 ? 'warning' : healthy }]),
  ]
}
