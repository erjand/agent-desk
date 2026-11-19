---
name: close-parent
description: Check a parent issue whose sub-issues have all closed against its Done When, then close it with a summary or ask what is left, without changing code. Use when given a parent issue's number, such as `/agent-desk:close-parent 12`, including the headless check runs agent-desk starts when a parent issue is labelled `agent-ready`.
---

# /agent-desk:close-parent

Check parent issue `#<n>` from the arguments. A parent issue groups sub-issues that hold its work, and every one of
them has closed. The user labelled the parent `agent-ready` to close it out. Confirm that the closed sub-issues and
`main` meet its Done When, then close it with a summary, or say what is left and stop.

`lib/close-parent.sh` starts you in a detached worktree of `main`, made for this run and removed when it ends. Read the
code there to confirm a Done When box.

Read and write issues and reviews with `agent-desk issue` and `agent-desk review`, which keep them in the desk's local
store with the verbs and flags `gh issue` and `gh pr` take. Never call `gh` for them.

## Step 1: Read

1. Read the parent with its comments: `agent-desk issue view <n> --comments`. Note each Done When box, and any work
   the description or comments name that no sub-issue covers, such as work in another repository.
2. List its sub-issues: `agent-desk issue view <n> --json subIssues`.
3. For each sub-issue, read it with its comments, `agent-desk issue view <sub> --comments`, and its close reason
   and original, `agent-desk issue view <sub> --json stateReason,duplicateOf`. A `not-planned` reason means it closed
   as not planned. A `duplicate` reason means its work moved to the original that `duplicateOf` names, which may sit
   under another parent or none. Read the original with `agent-desk issue view <original> --comments` and
   `agent-desk issue view <original> --json state,stateReason,duplicateOf`, following each further duplicate to the
   issue that holds the work, and count the sub-issue as that issue counts: completed once it closed as completed,
   and unmet while it is open or closed as not planned. Any other reason means it closed as completed.
4. Find each sub-issue's closing review, the review whose `fixes` link names it, or for a duplicate the one that names
   its original: `agent-desk review list --state all --limit 1000 --json number,state,closingIssuesReferences`,
   keeping the reviews whose `closingIssuesReferences` hold the issue's number. Read each one with
   `agent-desk review view <review>`. A review in state `MERGED` landed; one `CLOSED` didn't, and its comments say
   why.
5. Read the project's AGENTS.md, or the Agent desk section in its CLAUDE.md, for where its work lives.

## Step 2: Check each box

Check each Done When box against the sub-issues, their reviews, and the code on `main` in your worktree, with
read-only commands such as `git log`, `git grep`, and reading files. A box is met when you can point to the review,
commit, or file that meets it. A box you can't confirm from here, such as work a description names in another
repository, counts as unmet.

A parent with no Done When is met when every sub-issue counts as completed, a duplicate by its original as Step 1
reads it. A sub-issue closed as not planned leaves the parent unmet, whatever its Done When says, until the user
accepts it. So does a duplicate whose original is still open or closed as not planned.

## Step 3: Close or ask

Write the comment to a file outside the worktree, such as under `/tmp`. Never write `@agent` in it.

When every box is met, close the parent as completed with one comment:

```bash
agent-desk issue close <n> --reason completed --comment-file <file>
```

The comment lists each sub-issue with its review and outcome, such as `#12, review #15: merged`, `#13: closed as
not planned, because the setting moved to #14`, or `#16: duplicate of #12, review #15: merged`, then each Done When
box with how it was met, naming the review, commit, or `path:line`.

When a box is unmet or can't be confirmed, a sub-issue closed as not planned, or a duplicate's original is open or
closed as not planned, comment on the parent with `agent-desk issue comment <n> --body-file <file>`, starting with
the heading `## Decisions Needed`. Give each unmet box, not-planned sub-issue, and such duplicate a numbered decision
in the format `/agent-desk:work-issue` Step 3 gives, so the user can answer with a short reply such as `1B`: an
`### <n>. <question>` heading naming it, a sentence on what is left, and the lettered options, with ` ✅ *my pick*`
after the label of the one you'd pick and why in its text:

```markdown
## Decisions Needed

### 1. What should happen to the unmet box "The pane shows the runner's name"?

Review #15 shows the name on the page only.

- **A. File a sub-issue** ✅ *my pick*: the pane matches the page once it lands, and the parent waits for it.
- **B. Accept it as done**: the parent closes now, and the pane keeps showing no name.
- **C. Close the parent as not planned**: the parent closes now, and nothing records the gap.
```

Below the decisions, under `## Met`, list the boxes that are met and how, so the list reads apart from the last
decision. Then move the parent to `needs-design`:

```bash
agent-desk issue edit <n> --status needs-design
```

## Limits

The run checks and reports; the work lives in the sub-issues:

- Never commit, push, create a branch, or open a review. Leave the files in the worktree as you found them.
- Never file an issue, and never change any issue but the parent. When work is left, offer the sub-issue as an option
  and let the user file it.
- Post one comment, end with the parent closed or in `needs-design`, and stop.
