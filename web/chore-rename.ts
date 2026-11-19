// Renames a chore in a project's .agent-desk.json through the schedule review the web page's controls use, for
// `agent-desk chores rename`, which lib/chore_rename.py runs:
//   node web/chore-rename.ts <owner/repo> <main checkout> <old> <new>
// It prints the review's number, or says why it failed on stderr and exits 1.
import { renameChore } from './chores.ts'

export async function main(argv: string[]): Promise<number> {
  const [repo = '', main = '', from = '', to = ''] = argv
  if (argv.length !== 4 || !repo.includes('/')) {
    process.stderr.write('usage: node web/chore-rename.ts <owner/repo> <main checkout> <old> <new>\n')
    return 2
  }
  try {
    const { pr } = await renameChore({ id: repo.split('/')[1] ?? repo, repo, main }, from, to)
    process.stdout.write(`${pr}\n`)
    return 0
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    return 1
  }
}

if (import.meta.main) {
  process.exitCode = await main(process.argv.slice(2))
}
