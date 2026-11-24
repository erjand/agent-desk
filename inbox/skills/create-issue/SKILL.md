---
name: create-issue
description: Create issues that a headless agent can work end to end, from a rough idea, a bug, or a finding, splitting larger work into a set. Use when the user asks to create, file, or write up an issue, and whenever another skill or AGENTS.md says to file one.
---

# /agent-desk:create-issue

Turn the idea in the arguments, or the bug or finding at hand, into issues an agent can finish from the label alone.
The user triages them and adds `agent-ready`, and the agent that works one starts with only the issue, AGENTS.md, and
the code. Everything it needs goes in the issue.

## Step 1: Understand the work

1. Read the code, the design docs, and the AGENTS.md rules the work touches, so the issue names real files and fits
   the project. The project's Agent desk section, in its AGENTS.md or else its CLAUDE.md, names its design docs and
   where ideas go.
2. Search the open issues for the same work, by a key term and by file name:
   `agent-desk issue list --search "<term>"`. When one already covers it, comment the new detail there with
   `agent-desk issue comment <n> --body-file <file>` instead, and stop. Note related issues to mention.
3. Keep brainstorming out of work issues. An idea without a concrete outcome goes where the Agent desk section says
   ideas go: a design doc, or an issue labelled `idea`. When you can't tell which it is, ask the user.

## Step 2: Size and split

One issue is one agent run, ending in a review the user can read in a few minutes.

- Split larger work into a foundation issue that sets the pattern and follow-ups that extend it. File the foundation
  first, then each follow-up with `--blocked-by <foundation>`.
- When the user asks for a group of issues under one goal, file the goal as a parent issue and each piece with
  `--parent <goal>`.
- Label `serial` every issue that edits the files AGENTS.md lists as serial. When AGENTS.md names a `serial:<group>`
  label for those files, such as `serial:audio`, use that label instead: issues of one group work one at a time, while
  other groups keep working. Give an issue each group whose files it edits. That includes a set of issues that extend
  the same builder, since each one rebuilds its shared `.blend` and texture atlas.

## Step 3: Settle the design

An agent stops with `needs-design` when an issue leaves open how something looks, sounds, or feels, or reads two
ways. Settle each such choice before filing:

- Cite the code, design doc, or AGENTS.md rule that already settles it.
- In a session with the user, ask about the choices left, with the option you'd pick, and write the answers in.
- In a headless run, write the open choice under `## Decisions Needed` in the format Step 4 gives, with the option
  you'd pick, so the user settles it before labelling the issue.

Flag judgement calls to the user instead of guessing, such as a scope you narrowed or a requirement you inferred.

## Step 4: Write it

Write the title for a reviewer scanning the board: a capitalized imperative of about 80 characters or fewer, with no
trailing period, that names in plain words the outcome a user or developer notices. Leave the mechanism, the exact
scope, and file and function names to the body. "Scroll Queued with the mousewheel while dragging a card" tells the
reader what changes; "Replace native drag and drop with pointer events in app-drag.js" makes them open the issue.

Write the body from `.github/ISSUE_TEMPLATE/task.md` when the project has one, leaving out its comments, or else
with these sections:

- **Goal.** One or two sentences: what is true when the work is done, and what the user or developer notices.
- **Done When.** Checkboxes a reviewer can confirm from the review: behaviour, tests that pass, and, for anything seen
  on screen, the pictures the Agent desk section describes, uploaded with `agent-desk shots`. When that command
  fails, the agent keeps the pictures in its worktree and names their paths in the review.
- **Notes.** The agent's starting points: file paths and functions, steps and commands that reproduce a bug, the
  design docs and AGENTS.md rules that apply, related issues, and what you ruled out.
- **Decisions Needed.** Everything the user must answer: a design choice left open, two reasonable readings of the
  goal, a choice you made provisionally and want confirmed, or a tuning value to settle before labelling the issue.
  Number the decisions and letter their options, so the user can answer with a short reply such as `1B`. This heading
  comes first in the body, unless the project's own header rule places another section above it.

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

Leave Decisions Needed out when nothing is open, because its absence tells the user the issue needs no answer. Never
write an empty section or `None`. Choices the code or docs already settle, pointers to what to review first, and notes
that need no answer belong elsewhere.

Follow the writing style in AGENTS.md and the user's global instructions.

## Step 5: File it

Write the body to a temporary file and pass it with `--body-file`, since backticks and apostrophes in a `--body`
argument break in the shell. `agent-desk issue create` files the issue in the desk's local store and prints its
number:

```bash
agent-desk issue create --title "<title>" --body-file <file> --label enhancement [--label serial | --label serial:<group>] [--blocked-by <n>] [--parent <n>]
```

Use `bug` for a bug and `enhancement` for anything else, plus any label the caller asks for, such as `audit`. When
the user says the work is for later, add `--label backlog`, which files the issue in the desk's Backlog section
instead of Open. Never add `agent-ready`: the user decides what agents work on, and the label starts a run at once.

The command files the issue, its labels, and its links in one transaction, so one that fails, such as on a
`--blocked-by` number that doesn't exist, files nothing. Fix what it names and run it again.

Report each issue's number and title, which ones are ready to label and which wait on another, and the judgement calls
you flagged.
