---
name: discuss-idea
description: Answer the newest comment sent to the agent on an idea, an issue labelled `idea`, with research, options, or a sharper scope, in one comment, without changing code. Use when given an idea's issue number, such as `/agent-desk:discuss-idea 12`, including the headless discussion runs the dispatcher starts.
---

# /agent-desk:discuss-idea

Answer the person on idea `#<n>` from the arguments. An idea is an issue labelled `idea` that the person hasn't
planned yet. They sent you a comment from its comment box, or with an `@agent` mention, to think it through with you:
to research it, weigh options, or sharpen its scope. You answer with one comment and stop. The idea stays an idea until
they plan it.

`agent-desk discuss-idea` starts you in a detached worktree of `main`, made for this run and removed when it ends. Read
the code there. A later discussion of the same idea takes up this session, so you keep what you learned.

Read and write the issue with `agent-desk issue`, which keeps it in the desk's local store with the verbs and flags
`gh issue` takes. Never call `gh` for it.

## Step 1: Read

1. Read the issue with its comments: `agent-desk issue view <n> --comments`. The newest comment from the person is the
   one to answer; the ones before it and your earlier answers are the conversation so far.
2. Read the project's AGENTS.md, or the Agent desk section in its CLAUDE.md, and the design docs it names, such as the
   README.
3. Read the code, issues, and reviews the idea and the comment name, and whatever else the question needs. Search the
   code to check each claim you make about it.

## Step 2: Answer

Write one comment that answers the newest comment: what you found, the options with what each costs, and the one you
would pick and why, or a sharper scope with a Goal and a Done When list the person could plan from. Name files as
`path:line`. Keep it short enough to read in a few minutes.

When the answer asks the person to choose, put the choices under `## Decisions Needed` in the format
`/agent-desk:work-issue` Step 3 gives, so they can answer with a short reply such as `1B`:

```markdown
## Decisions Needed

### 1. Should the runner list sort by load?

The list sorts by name today, and a busy runner hides among idle ones.

- **A. Sort by load** ✅ *my pick*: the busiest runner shows first, where the person looks.
- **B. Keep the name order**: each runner keeps its place, and the load needs a glance down the column.
```

Give each decision an `### <n>. <question>` heading with a short question, its context in a sentence or two, and its
options as bullets `- **<letter>. <short label>**: <what follows from it>`, with ` ✅ *my pick*` after the label of
the one you would pick and why in its text. A reply such as `1B` picks option B of decision 1 in your newest
`## Decisions Needed` section.

When the comment asks you to change the idea's description, such as to fold the discussion into it, edit it with
`agent-desk issue edit <n> --body-file <file>`, keeping the parts the comment didn't ask you to change, and say in your
answer what you changed. Leave the description alone otherwise.

Post the answer with `agent-desk issue comment <n> --body-file <file>`, and write the file outside the worktree, such
as under `/tmp`. Never write `@agent` in it.

## Limits

The run is a conversation, not work on the code:

- Never commit, push, create a branch, or open a review. Leave the files in the worktree as you found them.
- Never file an issue, and never change the idea's labels or state. When the idea should become work, say so in your
  answer, and the person plans it.
- Post one comment per run, then end.
