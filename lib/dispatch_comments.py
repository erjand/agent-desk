"""
The dispatcher's answers to comments sent to the agent, and its cleanups after agent reviews end. `Dispatcher` in
`lib/dispatch.py` inherits `CommentsMixin`, whose methods use the dispatcher's `answered_to`, `cleaned_to`, `desk`,
`desk_lib`, `ended`, `say`, and `systemd`.

A comment with `to_agent` set, as the page's Send to agent and the pane's Fix post, asks for an agent run the way an
@agent comment did in the agent workflow: on an idea, an open issue labelled `idea` in no agent state, as the board's
Ideas column shows it, an `idea-<n>` discussion run, which holds the issue's `agent-<n>` target and an agent slot for at
most lib/dispatch.py's DISCUSS_MINUTES and leaves the issue's state alone; on any other issue, a run of that issue; on a
review from `agent/issue-<n>`, a run of issue n that replies on the review; on a review from any other `agent/<slug>`
branch, a `pr-<n>` run that carries the comment in REQUEST. A review from any other branch gets a note saying so, and a
comment an agent wrote asks for nothing. An issue or review labelled `benchmark` asks with the benchmark limit. It
records its answer to each such comment in the store, and on start and on each reconcile it answers the comments sent
since the last answer, so a comment sent while it was down still asks for its run.

When an agent/* review merges or closes without merging, the dispatcher starts `agent-desk clean <branch> <merged>`
in a unit of its own, from the review's event. It records each cleanup it starts in the store, and on start and on
each reconcile it starts the cleanups of the reviews that ended since the last one, so a review that ended while it
was down, or whose cleanup failed to start, still gets one.
"""

import contextlib
import os
import re

import store
from dispatch_wake import NOTE_MARK
from run_units import CARRIED_ENV, PRIORITY, SLICE, checkout_of

# The workflow states an issue labelled `idea` leaves Ideas for, as AGENT_STATES in inbox/hooks/board.ts names them.
AGENT_STATES = ("queued", "in-progress", "waiting")
WRONG_BRANCH_NOTE = ("Send to agent works only on reviews from `agent/<slug>` branches, and this one is from "
                     "`{branch}`.")
# The branches whose reviews a comment sent to the agent works: an issue's, and any other agent branch.
ISSUE_BRANCH = re.compile(r"agent/issue-(\d+)")
AGENT_BRANCH = re.compile(r"agent/[a-z0-9][a-z0-9-]*")
# A cleanup's time limit.
CLEAN_MINUTES = 15


def is_idea(item):
    """Whether an issue from Store.item is an idea, as isIdea in inbox/hooks/board.ts reads it: open, labelled `idea`,
    and in no agent state."""
    return (item["kind"] == "issue" and item["status"] not in (*AGENT_STATES, "closed")
            and any(label["name"] == "idea" for label in item["labels"]))


def clean_unit(project, branch, event):
    """The unit that cleans up after a branch's review, named with the event that asked, so a review closed again
    later gets a unit of its own."""
    clean = lambda text: re.sub(r"[^A-Za-z0-9_.-]+", "-", text)
    return f"agent-desk-clean-{clean(project)}-{clean(branch.removeprefix('agent/'))}-{event}.service"



class CommentsMixin:
    """The methods of `Dispatcher` that answer comments sent to the agent and start the cleanups of ended reviews."""

    def answer_comments(self):
        """
        Answers each comment sent to the agent since `answered_to`, oldest first, from its `comment-added` event:
        those sent while the dispatcher was down, and those whose answer failed to write. The first start on a store
        with no answer recorded starts from the newest event, so it asks for no runs for old or imported comments.
        """
        if self.desk.schema_newer():
            return
        if self.answered_to is None:
            with self.desk.write():
                self.answered_to = self.desk.last_answer()
                if self.answered_to is None:
                    self.answered_to = self.desk.last_event()
                    self.desk.comment_answered(None, None, self.answered_to, "start")
        for event in self.desk.agent_comments(self.answered_to):
            self.ask_agent(event)
            self.answered_to = event["id"]

    def ask_agent(self, event):
        """
        Asks for the agent run a comment sent to the agent asks for, from its `comment-added` event, as the module's
        docstring describes, and returns the run's id, or None when it asks for none. It records its answer in the
        same transaction, so a restart neither answers the comment again nor skips it.
        """
        comment = event["payload"]["comment"]

        def answered(answer, run=None):
            self.desk.comment_answered(event["project"], event["number"], event["id"], answer, comment, run)
            return run

        with self.desk.write():
            row = self.desk.comment_of(comment)
            if row is None:
                return answered("none")
            project, number = row["project"], row["number"]
            if row["author"] == "agent":
                self.say(f"Ignored comment {comment} on #{number} in {project}: an agent sent it to the agent.")
                return answered("ignored")
            try:
                item = self.desk.item(project, number)
            except store.StoreError:
                return answered("none")
            labels = {label["name"] for label in item["labels"]}
            if is_idea(item):
                # A discussion runs under its own limit, whatever the issue's benchmark label says.
                target, env, labels = f"idea-{number}", {}, set()
            elif item["kind"] == "issue":
                target, env = str(number), {"EVENT": "issue_comment"}
            elif found := ISSUE_BRANCH.fullmatch(item["headRefName"]):
                target, env = found[1], {"EVENT": "issue_comment", "REPLY_PR": str(number)}
                with contextlib.suppress(store.StoreError):
                    labels |= {label["name"] for label in self.desk.item(project, int(found[1]), "issue")["labels"]}
            elif AGENT_BRANCH.fullmatch(item["headRefName"]):
                target, env = f"pr-{number}", {"REQUEST": row["body"]}
            else:
                self.desk.comment(project, number, WRONG_BRANCH_NOTE.format(branch=item["headRefName"]) + NOTE_MARK)
                self.say(f"Comment {comment} on #{number} in {project} asks for no run: its branch isn't an agent's.")
                return answered("note")
            run = self.desk.request_run(project, "agent", target, "benchmark" in labels, env)
            self.say(f"Comment {comment} on #{number} in {project} asked for run {run}.")
            return answered("run", run)

    def catch_up(self):
        """Starts the cleanups of the reviews that ended since `cleaned_to`: those that ended while the dispatcher was
        down, and those whose cleanup failed to start."""
        if self.desk.schema_newer():
            return
        newest, failed = self.cleaned_to, None
        for event in self.desk.review_ends(self.cleaned_to):
            newest = event["id"]
            if not self.clean(event) and failed is None:
                failed = event["id"] - 1
        self.cleaned_to = newest if failed is None else failed
        self.ended = {done for done in self.ended if done > self.cleaned_to}

    def clean(self, event):
        """
        Starts `agent-desk clean <branch> <merged>` for an agent/* review that merged, from its `review-merged` event,
        or closed, from its `state-changed` event, with any close reason. The `state-changed` event a merge writes
        about itself starts nothing more. The cleanup runs in a unit of its own, so it outlives the dispatcher restart
        that its fast-forward of agent-desk's main checkout may cause. Returns False when the cleanup failed to start,
        and True when it started, or when the event asks for none or for one started already.
        """
        if event["id"] <= self.cleaned_to or event["id"] in self.ended:
            return True
        payload = event["payload"]
        project = event["project"]
        if event["type"] == "review-merged":
            merged = "true"
        elif payload.get("state") == "closed" and not (payload.get("reason") == "merged"
                                                        and self.desk.review_merged(project, event["number"])):
            merged = "false"
        else:
            return True
        self.ended.add(event["id"])
        branch = self.desk.review_branch(project, event["number"])
        if not branch or not branch.startswith("agent/"):
            return True
        checkout = checkout_of(self.desk, project)
        if not checkout:
            self.say(f"Review #{event['number']} of {branch} closed in {project}, which has no checkout here, so "
                     "nothing cleaned up after it.")
            return True
        unit = clean_unit(project, branch, event["id"])
        env = {name: os.environ[name] for name in CARRIED_ENV if os.environ.get(name)}
        env["GH_REPO"] = project
        args = ["systemd-run", "--user", "--unit", unit, "--slice", SLICE, "--collect", "--quiet", "--description",
                f"agent-desk cleanup of {branch} in {project}", "--working-directory", str(checkout),
                "-p", f"RuntimeMaxSec={CLEAN_MINUTES}min"]
        for setting in PRIORITY:
            args += ["-p", setting]
        for name, value in env.items():
            args += ["--setenv", f"{name}={value}"]
        args += ["--", str(self.desk_lib.parent / "bin" / "agent-desk"), "clean", branch, merged]
        done = self.systemd(args)
        if done.returncode != 0:
            self.ended.discard(event["id"])
            self.say(f"The cleanup of {branch} failed to start: {(done.stderr or done.stdout).strip()}")
            return False
        self.desk.cleanup_started(project, event["number"], event["id"], unit)
        self.say(f"Started the cleanup of {branch} in {unit}: review #{event['number']} "
                 f"{'merged' if merged == 'true' else 'closed'}.")
        return True
