---
name: work-issue
description: Work one issue end to end, from reading it to a review ready for the user, or return to an issue's open review with new feedback, in a project set up with agent-desk. Use when given an issue number to work, such as `/agent-desk:work-issue 12`, including headless runs the dispatcher starts.
---

# /agent-desk:work-issue

Work issue `#<n>` from the arguments to a review the user can read in a few minutes. The user labels issues
`agent-ready` to hand them over, and often isn't watching: everything they need goes on the issue or the review.

Read and write issues and reviews with `agent-desk issue` and `agent-desk review`, which keep them in the desk's
local store with the verbs and flags `gh issue` and `gh pr` take. Never call `gh` for them.

The project's AGENTS.md has an Agent desk section with its test command, how to show visual changes, and which files
make an issue `serial`; a project whose AGENTS.md is shared beyond it keeps the section in its CLAUDE.md instead. Read
it first; it overrides the defaults below.

## Step 1: Get into the worktree

Work in `../<repo>-issue-<n>` on `agent/issue-<n>`, beside the main checkout. `agent-desk work-issue` creates it and
starts you there in headless runs. In an interactive session, run `agent-desk start <n>` from the main checkout and
work in the directory it prints. It claims the issue the way headless runs do, creates the worktree or continues the
branch, and posts a note naming this session, which the inbox reads to show the issue as worked here instead of as
stopped. If it exits with status 3, the issue waits behind another serial issue: tell the user and stop. Run
`agent-desk` from PATH.

Never edit the main checkout or another agent's worktree.

## Step 2: Read the work

1. Read the issue with its comments: `agent-desk issue view <n> --comments`.
2. Check for an open review of your branch: `agent-desk review list --head agent/issue-<n>`. If one exists, you're
   iterating. Read its description and comments with `agent-desk review view <review>`, which prints each line
   comment with its file and line, and treat everything since your last commit as the new request. A run the user
   started with a comment for the agent carries its request in that comment and the line comments submitted with it.
3. Never add `agent-ready` yourself. Post a comment with `agent-desk issue comment <n> --body-file <file>` or
   `agent-desk review comment <review> --body-file <file>`, and end every comment with these two lines, the second
   hidden where the comment renders. In a run the dispatcher started, put the value of `$AGENT_DESK_UNIT` without
   `.service` in place of `<unit>`. When `$AGENT_DESK_UNIT` is unset, as in an interactive session, drop ` in <unit>`.

   ```markdown
   <sub>Posted by agent-desk in <unit></sub>
   <!-- agent-desk -->
   ```

   You post as the user, so a comment without the hidden line that mentions `@agent` anywhere starts another run at
   once; never write `@agent` in a comment either way.
   `agent-desk serial` has already held a `serial` issue back while another one works, so a run that reaches this
   step may go ahead.
4. The user answers a decision under `## Decisions Needed`, in the issue body, the review's description, or an
   agent's comment, in words or with a short reply such as `1B`, `1: B`, or `1 B, 2 A`. A short reply picks the
   lettered option of the numbered decision in the newest `## Decisions Needed` section on the issue or its review, so
   `1B` picks option B of decision 1. Record each answer in the issue's Notes and remove the decision's whole block,
   from its heading through its options. Keep the numbers of the decisions left, so a later `2A` still means the same
   decision, and remove `## Decisions Needed` once no decision is left. Sections written before this format hold one
   bullet per decision: remove an answered bullet the same way. Keep the two closing lines on any comment you edit.
   Edit an agent's comment in place with `agent-desk issue comment <n> --edit <comment> --body-file <file> --note
   "<what changed>"`, or the same on `review`, by the comment id `view` prints. The store adds the note to the
   comment above its closing lines, so the comment shows what you changed. Keep the notes of earlier edits.

## Step 3: Decide whether you can finish it

Stop and ask when the issue leaves a design choice open that the user would want to make: how something should look,
sound, or feel, a change to a design doc, or two reasonable readings of the goal that lead to different work. Comment
the questions on the issue, starting the comment with the heading `## Decisions Needed` unless the project's own
header rule places another section above it. Number the decisions and letter their options, so the user can answer
with a short reply such as `1B`:

```markdown
## Decisions Needed

### 1. Should the banner pause combat?

The banner shows for two seconds after a unit levels up, mid-turn.

- **A. Pause combat** while it shows: the player reads it, and every fight runs two seconds longer.
- **B. Overlay it** ✅ *my pick*: combat stays fast, and the banner fades over the board.
```

- Give each decision an `### <n>. <question>` heading, numbered from 1, with a short question ending in `?`.
- Put the context the question needs, if any, in one or two sentences under the heading.
- Write each option as a bullet `- **<letter>. <short label>**: <what follows from it>`, lettered A, B, C.
- Mark the option you'd pick with ` ✅ *my pick*` after its label, and say why in its text.
- Give a decision that points at another section, such as a value in a `## Suggested Design` section, its own heading
  too, with one line pointing there.

The comment carries the question, so the issue body needs no copy. Any other comment that needs the user's answer
starts the same way. Then replace `in-progress` with `needs-design`, with
`agent-desk issue edit <n> --add-label needs-design --remove-label in-progress`, and stop. In an interactive session,
also run `agent-desk serial release`, which headless runs do on their own. Don't ask about choices the code,
AGENTS.md, or the design docs already settle.

## Step 4: Do the work

Open the review early. Once you have a first commit, open the draft review as Step 6 describes. Your branch lives in
the repository every worktree shares with the main checkout, so each commit reaches the review and the board without a
push, and the work survives a headless run that ends at its time limit.

Follow AGENTS.md throughout. Keep the change to what the issue asks. File anything else you find, such as a bug, a
cleanup, or a refactor, with the `/agent-desk:create-issue` skill, and name it in the review. Filing needs no
permission: the user triages new issues before any agent works one, so file instead of asking in the review whether to.

When you first edit a file the Agent desk section lists as serial and the issue lacks that file's `serial` or
`serial:<group>` label, add the label at once with `agent-desk issue edit <n> --add-label <label>`, before you go on.
An issue holds its groups' slots while it is in progress, so from then on another issue of the group waits for yours
before its run starts; until the label lands, one can start beside you on the same files.

Before opening the review, count the lines of each source file you changed. File a cleanup issue for every one over
1000 lines, or over the limit the project's conventions set, unless an open issue already covers it. The issue names
the file, its size, and the split you'd make.

## Step 5: Verify

- In a project whose `.agent-desk.json` sets a `test` command, run the tests that cover the code you changed and
  added, and leave the full suite to the branch check: Step 6 waits for it to run that command on your commit merged
  with its base. Run the full suite yourself when you can't tell which tests cover the change, such as a change to a
  shared helper or a build setting. In a project with no `test` command, no branch check runs, so run the project's
  tests as its Agent desk section names them. Either way, add tests for new logic the way AGENTS.md describes.
- For anything seen on screen, capture it the way the Agent desk section describes, at the places and times the
  issue's Done When names. Look at every picture you take.
- Go through the issue's Done When list and check each item you can.
- Wait on a long run, such as a benchmark or a render, with a loop or Monitor that stops on an error line, such as
  `ERROR:`, as well as on the run's end, and stop the run when one appears. A run that fails every frame otherwise
  holds the GPU, and every agent queued behind it, until its timeout.

## Step 6: Open or update the review

1. Commit with a message in the repository's style, ending with the `Co-Authored-By` line your harness specifies.
2. Merge the base into your branch before you open the review and before each wait for the checks in item 8, so the
   check never spends its turn finding a conflict you could have resolved first. The base is your review's base when
   you iterate, and otherwise the branch a `base:<branch>` label names, or `main`. Read only local branches and fetch
   nothing. `git log --oneline HEAD..<base>` lists the base's commits your branch lacks; when it lists none, go on.
   Otherwise run `git merge --no-edit <base>`, resolve any conflict and commit the merge, and run the tests that cover
   the files both your branch and the base changed. Merge and never rebase or force-push, as the conflict fixes do.
3. If you haven't opened the draft review yet, write its description to a file, from
   `.github/pull_request_template.md` when the project has one, and open it:

   ```bash
   agent-desk review create --branch agent/issue-<n> --title "<title>" --body-file <file> --draft --fixes <n>
   ```

   Title the review as the create-issue skill titles an issue, for a reviewer scanning the board: a capitalized
   imperative of about 80 characters or fewer, with no trailing period, that names in plain words the outcome a user
   or developer notices, and leaves the mechanism, the exact scope, and file and function names to the description.
   `--fixes <n>` closes the issue when the review merges into main. When the issue carries a `base:<branch>` label,
   add `--base <branch>`: agent-desk cut your branch from that base and merges the review into it. When iterating,
   update the description with `agent-desk review edit <review> --body-file <file>`, and reply to each comment you
   addressed with `agent-desk review comment <review> --body-file <file>`.
4. Start the description with a `## Summary` of one or two plain sentences that tell the user what the review changes
   for them and why, in words they can follow without reading the code. Keep file paths, function names, and other
   identifiers out of it: the per-file changes and the verification come after it. The project's template, when it
   has one, shapes those later sections.

   ```markdown
   ## Summary

   Leveling up mid-fight no longer freezes combat. The level-up banner now fades in over the board while the fight
   goes on, so battles keep their pace.
   ```

   When the Summary leaves the cause unclear, as with many bug fixes, follow it with a `## Why` section that walks
   through one concrete example before the list of changes. Each time you update the description on a later
   iteration, reread the Summary and rewrite it if the review now changes something else.
5. When the review needs the user's answer, such as a choice you made provisionally and want confirmed before merge, put
   a `## Decisions Needed` section first in the description, with the Summary directly under it, using numbered
   decisions and lettered options in the format Step 3 gives, unless the project's own header rule places another
   section above it. Under each decision's options, add a line naming the option the branch implements, so the user
   can tell whether an answer needs more work. When the branch implements none of them, write `none` and say in a
   sentence what it does instead. Update the line whenever a later commit changes which option the branch holds.

   ```markdown
   ### 1. Should the banner pause combat?

   - **A. Pause combat** while it shows: the player reads it, and every fight runs two seconds longer.
   - **B. Overlay it** ✅ *my pick*: combat stays fast, and the banner fades over the board.

   **On this branch:** B.
   ```

   Leave the heading out when nothing needs an answer, and never write an empty section or `None`.
   Pointers to what to review first and notes that need no answer go elsewhere.
6. For a change you can see, such as a model, lighting, or UI, upload the pictures that show it best with
   `agent-desk shots <review> <picture>...` and put the Markdown it prints in the review's verification notes, each
   picture under a line saying what it shows. Two to four pictures usually cover it. After a fix, upload again and
   replace the pictures. The command keeps the pictures on this machine and needs no network. When it fails, keep
   the pictures in your worktree, name each one's path in the verification notes under the line saying what it
   shows, and say the upload failed.
7. Run each description edit as its own command, apart from comments on other issues or reviews. A permission check
   that blocks one part of a combined command stops all of it. After each edit, read the description back with
   `agent-desk review view <review> --json body` and fix any placeholder or missing picture.
8. Wait for the local checks with `agent-desk review checks <review> --watch`. The desk checks your branch's newest
   commit merged with its review's base once the branch stands still for a minute, and the command returns when that
   check ends: status 0 when it passed, 1 when it failed, with the path of its log. Read the log, fix the failure,
   commit, merge the base as item 2 says, and wait again. When it says the project sets no test command, say so in
   the description. Run the command in the foreground, with the longest timeout your shell tool allows, and run it
   again if it times out before the check ends. Never leave it running in the background, and end your turn only after
   it returns: a headless run ends 30 seconds after your final message and stops whatever you left running.
9. When the checks pass and the description you read back says what you checked, run
   `agent-desk review ready <review>`. If an item in Done When stays unchecked, say why there.

## Finish

End with a two- or three-sentence summary: what changed, how you verified it, and anything the user should look at
first. Headless runs print it at the end of the run log.
