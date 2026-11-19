import { expect, test } from 'claude-code/testing'

import { gaugesOf, loadOf } from './load.ts'

test('the machine\'s load reads memory in use and CPU use since the last sample', () => {
  const proc = (busy: number, idle: number) => ['MemTotal:       62914560 kB', 'MemAvailable:   15728640 kB',
    'SwapTotal:      31457280 kB', 'SwapFree:        3145728 kB', `cpu  ${busy} 0 0 ${idle} 0 0 0 0 0 0`,
    `cpu0 ${busy} 0 0 ${idle} 0 0 0 0 0 0`].join('\n')
  const first = loadOf(proc(1000, 3000), null)
  expect(first.load.ramUsed).toBe(45)
  expect(first.load.ramTotal).toBe(60)
  expect(first.load.swapUsed).toBe(27)
  expect(first.load.cpu).toBeNull()
  // 300 busy ticks of 400 since the last sample.
  expect(loadOf(proc(1300, 3100), first.cpu).load.cpu).toBe(75)
})

test('the gauges show free RAM, red when it needs action, free swap, yellow once spent, CPU use, and green when healthy', () => {
  const machine = { ramUsed: 44, ramTotal: 60, swapUsed: 28.4, swapTotal: 30, cpu: 34 }
  expect(gaugesOf(machine)).toEqual([{ text: 'RAM 16G free', color: '#5f875f' },
    { text: 'swap 1.6G free', color: 'warning' }, { text: 'CPU 34%', color: '#5f875f' }])
  expect(gaugesOf({ ...machine, swapUsed: 20 })[1]).toEqual({ text: 'swap 10G free', color: '#5f875f' })
  expect(gaugesOf({ ...machine, ramUsed: 52, cpu: null })[0]).toEqual({ text: 'RAM 8.0G free', color: 'warning' })
  expect(gaugesOf({ ...machine, ramUsed: 56, swapTotal: 0 })).toEqual([{ text: 'RAM 4.0G free', color: '#ff3b30' },
    { text: 'CPU 34%', color: '#5f875f' }])
})
