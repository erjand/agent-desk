import { expect, test } from 'claude-code/testing'

import { finishedLast, localSessionOf, resumeOf, startedSince, workedHere } from './notes.ts'

test('an issue whose agent finished last waits on the person, and one that stopped resumes', () => {
  const started = 'An agent started on `agent/issue-168` ([run log](x)). To take over, run '
    + '`claude --resume 697cac77-4ef9` in `/repo-issue-168`.'
  const finished = 'The agent finished: https://example.test/pull/194 (still a draft) ([run log](x)).'
  expect(finishedLast([started, finished])).toBe(true)
  expect(finishedLast([started, finished, 'Looks good, one question', started])).toBe(false)
  expect(finishedLast([started, 'The agent stopped without opening a PR.'])).toBe(false)
  expect(finishedLast([started, 'The agent stopped early: it ended its turn while `just bench` still ran, so the run '
    + 'stopped it. Its work is on review #194 (still a draft), and the next run on the issue takes up the same '
    + 'session.'])).toBe(false)
  expect(finishedLast([])).toBe(false)
  expect(resumeOf([started, finished])).toBe('cd /repo-issue-168 && claude --resume 697cac77-4ef9')
  expect(resumeOf(['A question'])).toBeUndefined()
})

test('an issue a local session works shows as at work while its session runs, and resumes once it ends', () => {
  const local = 'An agent started on `agent/issue-197` in a local session (pid 954874 on `desk`). To take over, run '
    + '`claude --resume 52f154a1-692c` in `/repo-issue-197`.'
  const runner = 'An agent started on `agent/issue-197` ([run log](x)). To take over, run '
    + '`claude --resume 697cac77-4ef9` in `/repo-issue-197`.'
  const session = localSessionOf([local])
  expect(session).toEqual({ pid: 954874, host: 'desk' })
  // The session still runs on this machine.
  expect(workedHere(session, 'desk', '/home/me/.local/share/claude/versions/2.1.309 --session-id 52f154a1')).toBe(true)
  expect(workedHere(session, 'desk', 'claude --resume')).toBe(true)
  // The session ended, or another program took its pid: the issue shows as stopped, with its Resume command.
  expect(workedHere(session, 'desk', null)).toBe(false)
  expect(workedHere(session, 'desk', '/usr/bin/python3 tools/build.py')).toBe(false)
  expect(resumeOf([local])).toBe('cd /repo-issue-197 && claude --resume 52f154a1-692c')
  // A note from another machine can't be checked, so the issue shows as it did before.
  expect(workedHere(session, 'laptop', 'claude')).toBe(false)
  // A runner's note, a later outcome, or no note at all names no local session.
  expect(localSessionOf([runner])).toBeUndefined()
  expect(localSessionOf([local, 'The agent stopped without opening a PR.'])).toBeUndefined()
  expect(localSessionOf([local, 'A question about the plan'])).toEqual(session)
  expect(localSessionOf([])).toBeUndefined()
  expect(workedHere(undefined, 'desk', 'claude')).toBe(false)
})

test('an agent that started after a merge counts, and one that started before it doesn\'t', () => {
  const started = (createdAt: string) => [{ body: 'An agent started on `agent/issue-60`.', createdAt }]
  expect(startedSince(started('2026-10-29T12:00:01Z'), '2026-10-29T12:00:00Z')).toBe(true)
  expect(startedSince(started('2026-10-29T11:59:59Z'), '2026-10-29T12:00:00Z')).toBe(false)
  expect(startedSince([{ body: 'The agent finished.', createdAt: '2026-10-29T13:00:00Z' }], '2026-10-29T12:00:00Z'))
    .toBe(false)
})
