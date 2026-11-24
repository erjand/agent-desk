"""Tests for lib/work-issue.sh and lib/work-pr.sh: each runs against a temporary store, a real origin and main checkout
in a temporary folder, a stand-in for claude that logs its arguments and can act as the agent, and a stand-in for gh
that logs lib/budget.py's read of the API budget, the one part of a run that still reads GitHub."""

import compileall
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
DESK = ROOT / "bin" / "agent-desk"
sys.path.insert(0, str(LIB))

import store  # noqa: E402

REPO = "o/r"
UNIT = "agent-desk-agent-o-r-1-1.service"
SIGNED = "\n\n<sub>Posted by agent-desk in agent-desk-agent-o-r-1-1</sub>"
SESSION = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
# The agent sends its branch to origin, as a push of the branch before the agent's first commit would.
PUSH = 'git push -q origin "HEAD:refs/heads/$(git branch --show-current)"'
# The API key the desk keeps in its config folder, made up.
DESK_KEY = "sk-ant-made-up-for-tests-KEYS"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Desk(unittest.TestCase):
    """An origin with main, a main checkout cloned from it, a store in its own state folder, and the stand-ins."""

    @classmethod
    def setUpClass(cls):
        # The scripts start python3 -B many times a run, and -B writes no bytecode, so each start would compile lib/
        # again. lib/__pycache__, which git ignores, holds it compiled once, as the main checkout's does.
        compileall.compile_dir(LIB, quiet=1, maxlevels=0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = root = Path(self.tmp.name)
        origin = root / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=root)
        self.main = root / "desk"
        # Every clone the test makes, and every script it runs, commits as this author, so the tests pass on a machine
        # with no git identity of its own.
        patch = mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                                             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
        patch.start()
        self.addCleanup(patch.stop)
        git("clone", "-q", str(origin), str(self.main), cwd=root)
        git("commit", "-q", "--allow-empty", "-m", "on main", cwd=self.main)
        git("push", "-q", "origin", "main", cwd=self.main)
        # The run needs no memory and ignores memory pressure, so it never waits on the machine running the tests.
        (self.main / ".agent-desk.json").write_text(json.dumps({"resources": {
            "needGB": {"agent": 0}, "reserveGB": 0, "maxMemoryPressure": 100}}))

        fakes = root / "fakes"
        fakes.mkdir()
        self.gh_log = root / "gh.log"
        self.gh_log.touch()
        # Each call logs on one line.
        (fakes / "gh").write_text(f'#!/bin/bash\nargs="$*"\necho "${{args//$\'\\n\'/ }}" >>"{self.gh_log}"\n')
        self.claude_log = root / "claude.log"
        # The agent is FAKE_AGENT, a shell command run in the worktree, such as one that opens a review.
        (fakes / "claude").write_text(f'#!/bin/bash\n{{ echo "$PWD"; printf "%s\\n" "$@"; }} >"{self.claude_log}"\n'
                                      'status=0\n'
                                      'if [[ -n ${FAKE_AGENT:-} ]]; then\n'
                                      '    bash -c "$FAKE_AGENT" >/dev/null || status=$?\nfi\n'
                                      # FAKE_STREAM is stream-json the agent prints, and FAKE_LINGER keeps it running
                                      # after that on a task in a session of its own, as a background command does.
                                      'if [[ -n ${FAKE_STREAM:-} ]]; then cat "$FAKE_STREAM"; fi\n'
                                      'if [[ -n ${FAKE_LINGER:-} ]]; then setsid sleep 300 & wait; fi\n'
                                      'exit "$status"\n')
        for fake in fakes.iterdir():
            fake.chmod(0o755)

        self.home = root / "home"
        self.home.mkdir()
        identity = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("REPLY_PR", "EVENT", "AGENT_DESK_BASE", "AGENT_DESK_AUTHOR", "REQUEST",
                                   "AGENT_DESK_ACCOUNT", "AGENT_DESK_ISSUE_RUNNER")}
        # A unit the dispatcher started carries AGENT_DESK_RUN, which the agent's writes keep and the script's drop.
        self.env.update({"PATH": f"{fakes}:{os.environ['PATH']}", "AGENT_DESK_CHECKOUT": str(self.main),
                         "GH_REPO": REPO, "XDG_STATE_HOME": str(root / "state"),
                         "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
                         "HOME": str(self.home), "AGENT_DESK_RUN": "1", "AGENT_DESK_UNIT": UNIT, "DESK": str(DESK),
                         **identity})
        self.database = root / "state" / "agent-desk" / "desk.sqlite"
        self.store = store.Store(self.database)
        self.addCleanup(self.store.close)

    def run_lib(self, script, *args, **env):
        return subprocess.run(["bash", str(LIB / script), *args], cwd=self.main, env={**self.env, **env},
                              capture_output=True, text=True, timeout=60)

    def issue(self, title="Work", labels=(), status="in-progress"):
        number = self.store.create(REPO, "issue", title, labels=labels)
        self.store.set_state(REPO, number, status)
        return number

    def review(self, branch, fixes=None):
        """A review of a local branch, cut from main."""
        git("branch", branch, "main", cwd=self.main)
        number = self.store.create(REPO, "review", "Change", branch=branch, draft=True)
        if fixes:
            self.store.link(REPO, number, fixes, "fixes")
        return number

    def item(self, number):
        """An item as the store holds it now, read afresh."""
        fresh = store.Store(self.database)
        try:
            return fresh.item(REPO, number)
        finally:
            fresh.close()

    def notes(self, number):
        return [(one["author"]["login"], one["body"]) for one in self.item(number)["comments"]]

    def claude_args(self):
        return self.claude_log.read_text().splitlines() if self.claude_log.exists() else []

    def codex_fake(self, *events):
        fake = self.root / "fakes" / "codex"
        self.codex_log = self.root / "codex.log"
        fake.write_text((fake.parent / "claude").read_text().replace(str(self.claude_log), str(self.codex_log)))
        fake.chmod(0o755)
        stream = self.root / "codex.jsonl"
        stream.write_text("".join(json.dumps(event) + "\n" for event in [
            {"type": "thread.started", "thread_id": SESSION}, *events,
            {"type": "turn.completed", "usage": {"input_tokens": 42}},
        ]))
        return {"FAKE_STREAM": str(stream)}

    def worktree(self, slug):
        return self.main.parent / f"desk-{slug}"

    def lose_origin(self):
        """Points origin at a path that doesn't exist, so any fetch or push fails."""
        git("remote", "set-url", "origin", str(self.root / "missing.git"), cwd=self.main)

    def stopped_early(self, command):
        """Stream-json in which the agent starts command in the background and reports a successful result, and the
        environment that makes the stand-in leave the command running after it."""
        stream = self.root / "stream.jsonl"
        stream.write_text("".join(json.dumps(event) + "\n" for event in [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": command}}]}},
            {"type": "system", "subtype": "task_started", "task_id": "b1", "tool_use_id": "toolu_1"},
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 60000}]))
        # The stand-in never exits by itself, so the shortest grace still ends the run early.
        return {"FAKE_STREAM": str(stream), "FAKE_LINGER": "1", "AGENT_DESK_RESULT_GRACE": "0.1",
                "AGENT_DESK_KILL_GRACE": "1"}

    def billing(self, account, agent=""):
        """The environment of a run the dispatcher started on `account`, with the desk's key saved, ANTHROPIC_API_KEY
        set where the run starts, and an agent that writes the ANTHROPIC_API_KEY it sees and then runs `agent`."""
        key = self.root / "config" / "agent-desk" / "anthropic-api-key"
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(DESK_KEY)
        key.chmod(0o600)
        self.seen_key = self.root / "seen-key"
        # A run that starts no claude leaves no log from an earlier run to pass for its own.
        self.seen_key.unlink(missing_ok=True)
        self.claude_log.unlink(missing_ok=True)
        return {"AGENT_DESK_ACCOUNT": account, "ANTHROPIC_API_KEY": "sk-ant-from-the-environment",
                "FAKE_AGENT": f'echo "${{ANTHROPIC_API_KEY-unset}}" >"{self.seen_key}"; {agent or ":"}'}

    def assert_billed(self, account, result):
        """claude got the flags that bill `account`, and neither it nor the agent's commands saw ANTHROPIC_API_KEY."""
        self.assertIn(f"Account: {account}.", result.stdout)
        args = self.claude_args()
        self.assertEqual(self.seen_key.read_text(), "unset\n")
        self.assertNotIn(DESK_KEY, "\n".join(args) + result.stdout + result.stderr)
        if account == "api":
            helper = json.loads(args[args.index("--settings") + 1])["apiKeyHelper"]
            self.assertEqual(subprocess.run(helper, shell=True, capture_output=True, text=True).stdout, DESK_KEY)
        else:
            self.assertNotIn("--settings", args)

    def assert_no_github_writes(self):
        """The gh stand-in heard nothing."""
        self.assertEqual(self.gh_log.read_text() if self.gh_log.exists() else "", "")


class WorkIssueTest(Desk):
    def test_a_first_run_posts_the_session_and_the_review_the_agent_opened(self):
        issue = self.issue(labels=["enhancement"])
        # The run reads only local branches, so it starts with origin out of reach.
        self.lose_origin()
        agent = f'"$DESK" review create --branch agent/issue-{issue} --title Work --draft --fixes {issue}'
        result = self.run_lib("work-issue.sh", str(issue), FAKE_AGENT=agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.worktree(f"issue-{issue}")
        args = self.claude_args()
        self.assertEqual(args[0], str(dest))
        self.assertEqual(args[1:3], ["-p", f"/agent-desk:work-issue {issue}"])
        session = args[args.index("--session-id") + 1]
        review = issue + 1
        log = f" (log: `journalctl --user -u {UNIT}`)"
        self.assertEqual(self.notes(issue), [
            ("desk", f"An agent started on `agent/issue-{issue}`{log}. To take over, run `claude --resume {session}` "
                     f"in `{dest}`.{SIGNED}"),
            ("desk", f"The agent finished: review #{review} (still a draft){log}.{SIGNED}"),
        ])
        self.assertEqual(self.notes(review), [("desk", f"The agent finished: review #{review} (still a draft){log}."
                                                       f"{SIGNED}")])
        self.assertEqual(self.item(review)["author"]["login"], "agent")
        self.assertEqual(self.item(issue)["status"], "in-progress")
        self.assertTrue(dest.is_dir())
        self.assert_no_github_writes()

    def test_codex_works_the_issue_and_resumes_its_thread_after_a_timeout(self):
        self.check_codex_launch_and_resume("codex", ["--approve-for-me"])

    def test_local_codex_works_the_issue_and_resumes_with_the_same_provider_and_model(self):
        self.check_codex_launch_and_resume("local", ["--oss", "--local-provider", "ollama", "--model=example:24b",
                                                    "--sandbox", "workspace-write", "-c", 'approval_policy="never"'])

    def check_codex_launch_and_resume(self, runner, options):
        config_path = self.main / ".agent-desk.json"
        config = json.loads(config_path.read_text())
        config["issueRunner"] = runner
        if runner == "local":
            config["localRunner"] = {"provider": "ollama", "model": "example:24b"}
        config_path.write_text(json.dumps(config))
        issue = self.issue()
        self.assertEqual(self.store.request_run(REPO, "agent", str(issue), False, {}), 1)
        env = self.codex_fake(
            {"type": "item.started", "item": {"type": "command_execution", "id": "item_1", "command": "just test"}},
            {"type": "item.completed", "item": {"type": "command_execution", "id": "item_1", "exit_code": 0}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Opened the review."}},
        )
        agent = f'"$DESK" review create --branch agent/issue-{issue} --title Work --draft --fixes {issue}'
        result = self.run_lib("work-issue.sh", str(issue), FAKE_AGENT=agent, AGENT_DESK_ACCOUNT="api", **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.worktree(f"issue-{issue}")
        self.assertEqual(self.claude_args(), [])
        args = self.codex_log.read_text().splitlines()
        prefix = [str(dest), "exec", *options, "--json"]
        self.assertEqual(args[:len(prefix) + 1], [*prefix, "--"])
        self.assertIn(f"Work issue #{issue}.", args[len(prefix) + 1])
        self.assertIn("inbox/skills/work-issue/SKILL.md", args[len(prefix) + 1])
        resume_options = "--oss --local-provider ollama --model=example:24b " if runner == "local" else ""
        self.assertIn(f"codex resume {resume_options}{SESSION}", result.stdout)
        self.assertIn("→ Shell: just test", result.stdout)
        self.assertIn("Opened the review.", result.stdout)
        self.assertIn("Codex session ID is in the run log", self.notes(issue)[0][1])
        self.assertEqual(self.store.run(1)["session"], SESSION)
        self.assertIsNone(self.store.run(1)["cost"])
        path_key = hashlib.sha256(os.fsencode(dest)).hexdigest()
        transcript = self.database.parent / "codex-logs" / path_key / f"{SESSION}.jsonl"
        events = [json.loads(line) for line in transcript.read_text().splitlines()]
        self.assertEqual([event["type"] for event in events], ["system", "assistant", "user", "assistant", "result"])
        self.assertEqual(events[0].get("local_runner"), config.get("localRunner"))
        self.assertTrue(all(event.get("timestamp") for event in events))
        self.store.comment(REPO, issue, "The agent run was cancelled or timed out (log: x).")
        result = self.run_lib("work-issue.sh", str(issue), **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        resumed = self.codex_log.read_text().splitlines()
        self.assertEqual(resumed[:len(prefix) + 3], [*prefix, "resume", "--", SESSION])
        self.assertIn("takes up the same session", " ".join(resumed))
        self.assertEqual(self.store.run(1)["start"], "resumed")
        self.assertEqual(len(transcript.read_text().splitlines()), len(events) * 2)

        state = self.database.with_name("hold.json")
        state.write_text(json.dumps({"state": "running", "issueRunners": {REPO: "claude"}}))
        self.store.comment(REPO, issue, "The agent run was cancelled or timed out (log: x).")
        result = self.run_lib("work-issue.sh", str(issue))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--session-id", self.claude_args())
        self.assertNotIn("--resume", self.claude_args())
        state.unlink()
        self.store.comment(REPO, issue, "The agent run was cancelled or timed out (log: x).")
        result = self.run_lib("work-issue.sh", str(issue), **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.codex_log.read_text().splitlines()[len(prefix)], "--")

    def test_local_codex_reports_missing_settings_and_unavailable_provider(self):
        config_path = self.main / ".agent-desk.json"
        config = json.loads(config_path.read_text())
        self.database.with_name("hold.json").write_text(json.dumps({"issueRunners": {REPO: "local"}}))
        codex = self.root / "fakes" / "codex"
        codex.write_text('#!/bin/bash\necho "LM Studio is not responding" >&2\nexit 1\n')
        codex.chmod(0o755)

        for settings, error in (({}, "set localRunner.provider"),
                                ({"provider": "lmstudio", "model": ["example"]}, "set localRunner.model")):
            with self.subTest(settings=settings):
                config["localRunner"] = settings
                config_path.write_text(json.dumps(config))
                issue = self.issue()
                result = self.run_lib("work-issue.sh", str(issue))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.item(issue)["status"], "open")
                self.assertIn(error, self.notes(issue)[-1][1])
                self.assertFalse(self.worktree(f"issue-{issue}").exists())

        config["localRunner"] = {"provider": "lmstudio", "model": "example"}
        config_path.write_text(json.dumps(config))
        issue = self.issue()
        result = self.run_lib("work-issue.sh", str(issue))
        self.assertEqual(result.returncode, 1)
        self.assertIn("LM Studio is not responding", result.stderr)
        self.assertIn("Local Codex using lmstudio exited with status 1", self.notes(issue)[-1][1])
        self.assertEqual(self.item(issue)["status"], "open")

    def test_a_run_without_a_review_frees_the_issue_and_removes_the_worktree_and_its_untouched_branch(self):
        # The run claims its queued issue in the store, so the agent works an issue in progress, and the run's end
        # frees it. The agent sent its branch to origin before any commit, so origin's main holds it.
        issue = self.issue(status="queued")
        seen = self.root / "status"
        result = self.run_lib("work-issue.sh", str(issue), EVENT="issues",
                              FAKE_AGENT=f'{PUSH} && "$DESK" issue view {issue} --json status >"{seen}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(seen.read_text()), {"status": "in-progress"})
        self.assertEqual(self.item(issue)["status"], "open")
        self.assertTrue(self.notes(issue)[-1][1].startswith("The agent stopped without opening a PR (log: "))
        self.assertFalse(self.worktree(f"issue-{issue}").exists())
        self.assertIn(f"Deleted agent/issue-{issue} on origin.", result.stdout)
        self.assertFalse(self.origin_has(f"agent/issue-{issue}"))

    def test_a_serial_issue_whose_slot_is_held_waits_without_an_agent(self):
        holder = self.issue(labels=["serial"])
        issue = self.issue(labels=["serial"], status="queued")
        result = self.run_lib("work-issue.sh", str(issue), EVENT="issues")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"#{issue} waits for #{holder}.", result.stdout)
        self.assertEqual(self.item(issue)["status"], "waiting")
        [(author, body)] = self.notes(issue)
        self.assertEqual(author, "desk")
        self.assertTrue(body.startswith("Waiting for the serial slot."))
        self.assertEqual(self.claude_args(), [])
        self.assert_no_github_writes()

    def test_a_run_that_stops_for_a_decision_posts_nothing_more_and_deletes_its_untouched_branch(self):
        issue = self.issue()
        result = self.run_lib("work-issue.sh", str(issue),
                              FAKE_AGENT=f'{PUSH} && "$DESK" issue edit {issue} --status needs-design')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.item(issue)["status"], "needs-design")
        self.assertEqual(len(self.notes(issue)), 1)
        self.assertFalse(self.worktree(f"issue-{issue}").exists())
        self.assertEqual(git("branch", "--list", f"agent/issue-{issue}", cwd=self.main), "")
        self.assertFalse(self.origin_has(f"agent/issue-{issue}"))

    def origin_has(self, branch):
        return bool(git("ls-remote", "origin", f"refs/heads/{branch}", cwd=self.main))

    def test_a_run_without_a_review_leaves_a_closed_issue_closed_and_keeps_a_branch_beyond_main_on_origin(self):
        issue = self.issue()
        result = self.run_lib("work-issue.sh", str(issue),
                              FAKE_AGENT=f'git commit -q --allow-empty -m wip && {PUSH} && "$DESK" issue close {issue}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.item(issue)["status"], "closed")
        self.assertIn(f"Kept agent/issue-{issue} on origin: it has commits that aren't on refs/remotes/origin/main.",
                      result.stdout)
        self.assertTrue(self.origin_has(f"agent/issue-{issue}"))

    def test_a_run_without_an_open_review_keeps_the_branch_of_a_closed_one_on_origin(self):
        # A closed review's comments may tell the user to restore its work from origin's copy.
        issue = self.issue()
        review = self.review(f"agent/issue-{issue}", fixes=issue)
        self.store.set_state(REPO, review, "closed", "closed")
        result = self.run_lib("work-issue.sh", str(issue),
                              FAKE_AGENT=f'{PUSH} && "$DESK" issue edit {issue} --status needs-design')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.worktree(f"issue-{issue}").exists())
        self.assertTrue(self.origin_has(f"agent/issue-{issue}"))

    def test_a_run_that_stops_for_a_decision_on_its_review_keeps_the_worktree(self):
        issue = self.issue()
        agent = (f'"$DESK" review create --branch agent/issue-{issue} --title Work --draft --fixes {issue} && '
                 f'"$DESK" issue edit {issue} --status needs-design')
        result = self.run_lib("work-issue.sh", str(issue), FAKE_AGENT=agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.item(issue)["status"], "needs-design")
        self.assertEqual(len(self.notes(issue)), 1)
        self.assertTrue(self.worktree(f"issue-{issue}").is_dir())

    def test_a_return_to_an_open_review_takes_up_a_cut_off_session_and_merges_main(self):
        issue = self.issue()
        review = self.review(f"agent/issue-{issue}", fixes=issue)
        dest = self.worktree(f"issue-{issue}")
        git("worktree", "add", "-q", str(dest), f"agent/issue-{issue}", cwd=self.main)
        # The merge queue moves the local main after the last run, so the run merges it into the branch, with origin
        # out of reach.
        git("commit", "-q", "--allow-empty", "-m", "landed", cwd=self.main)
        self.lose_origin()
        # The last run's start is on the issue, and its session is on this machine. Its unit was stopped before it
        # reported, and its exit handler's release left the cut-off notes the next run reads.
        self.store.comment(REPO, issue, f"An agent started on `agent/issue-{issue}`. To take over, run "
                                        f"`claude --resume {SESSION}` in `{dest}`.")
        released = subprocess.run([sys.executable, "-B", str(LIB / "release.py"), str(issue), "cancelled"],
                                  cwd=self.main, env={**self.env, "AGENT_DESK_UNIT": "agent-desk-agent-o-r-1-0.service",
                                                      "EVENT": "issue_comment", "REPLY_PR": str(review)},
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertTrue(self.notes(review)[0][1].startswith("The agent run was cancelled or timed out"))
        sessions = self.home / ".claude" / "projects" / "desk"
        sessions.mkdir(parents=True)
        (sessions / f"{SESSION}.jsonl").touch()

        result = self.run_lib("work-issue.sh", str(issue), REPLY_PR=str(review))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agent-desk merged main into the branch, so it has what landed since.", result.stdout)
        self.assertEqual(git("log", "-1", "--format=%s", cwd=dest), "landed")
        args = self.claude_args()
        self.assertEqual(args[args.index("--resume") + 1], SESSION)
        self.assertNotIn("--session-id", args)
        # The prompt says what the skill says: work reaches the review by a commit, with no push and no PR.
        prompt = args[args.index("-p") + 1]
        self.assertIn("since your last commit", prompt)
        self.assertIn("open or update the draft review", prompt)
        self.assertNotIn("push", prompt)
        self.assertNotIn("PR", prompt)
        review_notes = [body for _, body in self.notes(review)]
        self.assertTrue(review_notes[1].startswith(f"An agent started on `agent/issue-{issue}`, taking up the session "
                                                   f"stopped last time, (log: "))
        self.assertIn(f"claude --resume {SESSION}", review_notes[1])
        self.assertTrue(review_notes[2].startswith(f"The agent finished: review #{review} (still a draft)"))
        issue_notes = [body for _, body in self.notes(issue)]
        self.assertTrue(issue_notes[2].startswith(f"An agent started on feedback from #{review} (log: "))
        self.assertTrue(issue_notes[3].startswith(f"The agent finished: review #{review}"))
        self.assert_no_github_writes()

    def test_a_run_that_stops_early_says_so_keeps_its_worktree_and_exits_stopped(self):
        issue = self.issue()
        agent = f'"$DESK" review create --branch agent/issue-{issue} --title Work --draft --fixes {issue}'
        result = self.run_lib("work-issue.sh", str(issue), FAKE_AGENT=agent, **self.stopped_early("just bench"))
        self.assertEqual(result.returncode, 75, result.stderr)
        review = issue + 1
        log = f" (log: `journalctl --user -u {UNIT}`)"
        early = "The agent stopped early: it ended its turn while `just bench` still ran, so the run stopped it."
        self.assertEqual(self.notes(issue)[-1], ("desk", f"{early} Its work is on review #{review} (still a draft), "
                                                         f"and the next run on the issue takes up the same session"
                                                         f"{log}.{SIGNED}"))
        self.assertEqual(self.notes(review), [("desk", f"{early} The next run on the issue takes up the same session"
                                                       f"{log}.{SIGNED}")])
        self.assertEqual(self.item(issue)["status"], "in-progress")
        self.assertTrue(self.worktree(f"issue-{issue}").is_dir())

    def test_a_run_that_stops_early_without_a_review_keeps_the_issue_and_its_worktree(self):
        issue = self.issue()
        result = self.run_lib("work-issue.sh", str(issue), **self.stopped_early("just bench"))
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertTrue(self.notes(issue)[-1][1].startswith(
            "The agent stopped early: it ended its turn while `just bench` still ran, so the run stopped it. It "
            "opened no review yet, and the next run on the issue takes up the same session (log: "))
        self.assertEqual(self.item(issue)["status"], "in-progress")
        self.assertTrue(self.worktree(f"issue-{issue}").is_dir())

    def test_a_run_after_one_that_stopped_early_takes_up_its_session_and_reruns_the_task(self):
        issue = self.issue()
        dest = self.worktree(f"issue-{issue}")
        git("worktree", "add", "-q", "-b", f"agent/issue-{issue}", str(dest), "main", cwd=self.main)
        sessions = self.home / ".claude" / "projects" / "desk"
        sessions.mkdir(parents=True)
        (sessions / f"{SESSION}.jsonl").touch()
        self.store.comment(REPO, issue, f"An agent started on `agent/issue-{issue}`. To take over, run "
                                        f"`claude --resume {SESSION}` in `{dest}`.")
        self.store.comment(REPO, issue, "The agent stopped early: it ended its turn while ``just bench `x` `` still "
                                        "ran, so the run stopped it. It opened no review yet, and the next run on the "
                                        "issue takes up the same session (log: x).")

        result = self.run_lib("work-issue.sh", str(issue), EVENT="issues")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.claude_args()
        self.assertEqual(args[args.index("--resume") + 1], SESSION)
        prompt = args[args.index("-p") + 1]
        self.assertIn("you ended your turn while ``just bench `x` `` still ran, so the run stopped it", prompt)
        self.assertIn("Run it again in the foreground, and don't end your turn until it returns", prompt)
        self.assertIn("carry on with the /agent-desk:work-issue skill", prompt)
        self.assertIn("taking up the session stopped last time", self.notes(issue)[-2][1])

    def test_a_parent_issue_with_open_sub_issues_starts_no_agent(self):
        parent = self.issue(title="Parent", labels=["agent-ready"], status="queued")
        open_sub = self.issue(title="Open part", status="open")
        closed_sub = self.issue(title="Done part", status="closed")
        for sub in (open_sub, closed_sub):
            self.store.link(REPO, sub, parent, "parent")
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issues")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.item(parent)["status"], "open")
        [(author, body)] = self.notes(parent)
        self.assertEqual(author, "desk")
        self.assertTrue(body.startswith("This parent issue's work is in its open sub-issues"))
        self.assertIn(f"- #{open_sub} Open part", body)
        self.assertNotIn("Done part", body)
        self.assertEqual(self.claude_args(), [])
        self.assertEqual(self.gh_log.read_text(), "")

    def test_a_parent_issue_an_agent_comment_reaches_still_runs(self):
        parent = self.issue(title="Parent")
        self.store.link(REPO, self.issue(title="Open part", status="open"), parent, "parent")
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issue_comment")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.claude_args()[2], f"/agent-desk:work-issue {parent}")


class CloseParentTest(Desk):
    """A parent issue labelled agent-ready once its sub-issues have all closed gets lib/close-parent.sh's check."""

    def parent(self):
        parent = self.issue(title="Parent", labels=["agent-ready", "serial"], status="queued")
        for title in ("Done part", "Dropped part"):
            self.store.link(REPO, self.issue(title=title, status="closed"), parent, "parent")
        return parent

    def tree(self, number):
        return self.root / "cache" / "agent-desk" / "parents" / "o__r" / str(number)

    def test_a_parent_whose_sub_issues_all_closed_gets_a_check_the_agent_closes(self):
        parent = self.parent()
        # Another serial issue holds the slot, which would hold back a full run of the parent.
        self.issue(labels=["serial"])
        self.lose_origin()
        seen = self.root / "seen"
        # The agent works the issue in progress, from main's commit in a detached tree, and closes it with a comment.
        agent = (f'{{ "$DESK" issue view {parent} --json status; git rev-parse --abbrev-ref HEAD; }} >"{seen}"; '
                 f'"$DESK" issue close {parent} --reason completed --comment "All met."')
        branches = git("branch", "--list", cwd=self.main)
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issues", FAKE_AGENT=agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        tree = self.tree(parent)
        args = self.claude_args()
        self.assertEqual(args[0], str(tree))
        self.assertEqual(args[1:3], ["-p", f"/agent-desk:close-parent {parent}"])
        session = args[args.index("--session-id") + 1]
        status, head = seen.read_text().splitlines()
        self.assertEqual(json.loads(status), {"status": "in-progress"})
        self.assertEqual(head, "HEAD")
        log = f" (log: `journalctl --user -u {UNIT}`)"
        self.assertEqual(self.notes(parent), [
            ("desk", f"A parent check started on this issue, to close it once its closed sub-issues meet its Done "
                     f"When{log}. To take over, run `claude --resume {session}` in `{tree}` while it runs.{SIGNED}"),
            ("agent", "All met."),
        ])
        item = self.item(parent)
        self.assertEqual((item["status"], item["stateReason"]), ("closed", "completed"))
        # The check took no serial wait, made no worktree under the project or branch, and left no tree behind.
        self.assertFalse(self.worktree(f"issue-{parent}").exists())
        self.assertFalse(tree.exists())
        self.assertNotIn(str(tree), git("worktree", "list", cwd=self.main))
        self.assertEqual(git("branch", "--list", cwd=self.main), branches)
        self.assert_no_github_writes()

    def test_a_parent_check_uses_codex_and_reads_the_close_parent_skill(self):
        parent = self.parent()
        env = self.codex_fake()
        agent = f'"$DESK" issue close {parent} --reason completed'
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issues", AGENT_DESK_ISSUE_RUNNER="codex",
                              FAKE_AGENT=agent, **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"/inbox/skills/close-parent/SKILL.md for issue #{parent}", self.codex_log.read_text())
        self.assertEqual(self.item(parent)["status"], "closed")
        self.assertEqual(self.claude_args(), [])

    def test_a_check_that_asks_for_a_decision_leaves_the_parent_in_needs_design(self):
        parent = self.parent()
        agent = (f'"$DESK" issue comment {parent} --body "## Decisions Needed" && '
                 f'"$DESK" issue edit {parent} --status needs-design')
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issues", FAKE_AGENT=agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.item(parent)["status"], "needs-design")
        self.assertEqual([author for author, _ in self.notes(parent)], ["desk", "agent"])
        self.assertFalse(self.tree(parent).exists())

    def test_a_check_that_settles_nothing_moves_the_parent_back_to_open(self):
        parent = self.parent()
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issues", FAKE_AGENT="exit 3")
        # The unit fails with the agent's status, so the exit handler says so on the issue too.
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.item(parent)["status"], "open")
        author, body = self.notes(parent)[-1]
        self.assertEqual(author, "desk")
        self.assertEqual(body, f"The parent check stopped without closing this issue or asking for a decision (log: "
                               f"`journalctl --user -u {UNIT}`). Label the issue agent-ready to check it again."
                               f"{SIGNED}")
        self.assertFalse(self.tree(parent).exists())


    def test_a_check_that_stops_early_after_closing_the_parent_says_so_and_exits_stopped(self):
        parent = self.parent()
        agent = f'"$DESK" issue close {parent} --reason completed --comment "All met."'
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issues", FAKE_AGENT=agent,
                              **self.stopped_early("just bench"))
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.item(parent)["status"], "closed")
        self.assertEqual(self.notes(parent)[-1], (
            "desk", f"The parent check stopped early: the agent ended its turn while `just bench` still ran, so the "
                    f"run stopped it (log: `journalctl --user -u {UNIT}`). It closed this issue first.{SIGNED}"))
        self.assertFalse(self.tree(parent).exists())

    def test_a_check_that_stops_early_without_settling_moves_the_parent_back_to_open(self):
        parent = self.parent()
        result = self.run_lib("work-issue.sh", str(parent), EVENT="issues", **self.stopped_early("just bench"))
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.item(parent)["status"], "open")
        self.assertEqual(self.notes(parent)[-1], (
            "desk", f"The parent check stopped early: the agent ended its turn while `just bench` still ran, so the "
                    f"run stopped it (log: `journalctl --user -u {UNIT}`). It neither closed this issue nor asked for "
                    f"a decision. Label the issue agent-ready to check it again.{SIGNED}"))

class DiscussIdeaTest(Desk):
    def idea_tree(self, number):
        return self.root / "cache" / "agent-desk" / "ideas" / "o__r" / str(number)

    def test_codex_discussions_keep_their_thread_until_the_project_switches_runners(self):
        idea = self.issue(labels=["idea"], status="open")
        env = self.codex_fake()

        def discuss(runner, **extra):
            result = self.run_lib("discuss-idea.sh", str(idea), AGENT_DESK_ISSUE_RUNNER=runner, **extra)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(self.idea_tree(idea).exists())

        discuss("codex", **env)
        args = self.codex_log.read_text().splitlines()
        self.assertEqual(args[4], "--")
        self.assertIn(f"/inbox/skills/discuss-idea/SKILL.md for issue #{idea}", args[5])
        discuss("codex", **env)
        self.assertEqual(self.codex_log.read_text().splitlines()[4:7], ["resume", "--", SESSION])
        discuss("claude")
        self.assertIn("--session-id", self.claude_args())
        discuss("codex", **env)
        self.assertEqual(self.codex_log.read_text().splitlines()[4], "--")

    def test_a_discussion_answers_from_a_worktree_of_main_it_removes_and_leaves_the_idea_as_it_was(self):
        idea = self.issue(title="Maybe a map", labels=["idea", "serial:web"], status="open")
        self.lose_origin()
        tree = self.idea_tree(idea)
        seen = self.root / "seen"
        # The agent sees main's commit in a detached tree, and answers with one comment.
        agent = (f'git rev-parse --abbrev-ref HEAD >"{seen}"; '
                 f'"$DESK" issue comment {idea} --body "Three options."')
        branches = git("branch", "--list", cwd=self.main)
        result = self.run_lib("discuss-idea.sh", str(idea), FAKE_AGENT=agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.claude_args()
        self.assertEqual(args[0], str(tree))
        self.assertEqual(args[1:3], ["-p", f"/agent-desk:discuss-idea {idea}"])
        session = args[args.index("--session-id") + 1]
        self.assertEqual(seen.read_text().strip(), "HEAD")
        log = f" (log: `journalctl --user -u {UNIT}`)"
        self.assertEqual(self.notes(idea), [
            ("desk", f"A discussion run started on this idea{log}. To take over, run `claude --resume {session}` in "
                     f"`{tree}` while it runs.{SIGNED}"),
            ("agent", "Three options."),
        ])
        # The run made no branch, left no worktree, and moved nothing.
        self.assertFalse(tree.exists())
        self.assertEqual(git("branch", "--list", cwd=self.main), branches)
        self.assertNotIn(str(tree), git("worktree", "list", cwd=self.main))
        self.assertEqual(self.item(idea)["status"], "open")
        self.assertEqual([label["name"] for label in self.item(idea)["labels"]], ["idea", "serial:web"])
        self.assert_no_github_writes()

    def test_a_later_discussion_takes_up_the_last_ones_session_in_the_same_folder(self):
        idea = self.issue(labels=["idea"], status="open")
        tree = self.idea_tree(idea)
        self.store.comment(REPO, idea, f"A discussion run started on this idea (log: x). To take over, run "
                                       f"`claude --resume {SESSION}` in `{tree}` while it runs.")
        sessions = self.home / ".claude" / "projects" / "ideas"
        sessions.mkdir(parents=True)
        (sessions / f"{SESSION}.jsonl").touch()
        result = self.run_lib("discuss-idea.sh", str(idea))
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.claude_args()
        self.assertEqual(args[0], str(tree))
        self.assertEqual(args[args.index("--resume") + 1], SESSION)
        self.assertNotIn("--session-id", args)
        self.assertTrue(self.notes(idea)[-1][1].startswith(
            "A discussion run started on this idea, taking up the session of the last discussion (log: "))
        self.assertFalse(tree.exists())

    def test_a_failed_agent_fails_the_run_and_still_removes_the_worktree(self):
        idea = self.issue(labels=["idea"], status="open")
        tree = self.idea_tree(idea)
        # A worktree a cut-off run left behind gives way to a new one.
        tree.parent.mkdir(parents=True)
        git("worktree", "add", "-q", "--detach", str(tree), "main", cwd=self.main)
        (tree / "left.txt").write_text("from the last run")
        result = self.run_lib("discuss-idea.sh", str(idea), FAKE_AGENT="exit 3")
        # The unit fails with the agent's status, so the exit handler says so on the idea.
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.claude_args()[0], str(tree))
        self.assertFalse(tree.exists())
        self.assertEqual(self.item(idea)["status"], "open")


    def test_a_discussion_that_stops_early_says_so_on_the_idea_and_exits_stopped(self):
        idea = self.issue(labels=["idea"], status="open")
        agent = f'"$DESK" issue comment {idea} --body "Three options."'
        result = self.run_lib("discuss-idea.sh", str(idea), FAKE_AGENT=agent, **self.stopped_early("just bench"))
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.notes(idea)[-1], (
            "desk", f"The discussion run stopped early: the agent ended its turn while `just bench` still ran, so the "
                    f"run stopped it (log: `journalctl --user -u {UNIT}`). The next discussion takes up the same "
                    f"session.{SIGNED}"))
        self.assertEqual(self.notes(idea)[-2], ("agent", "Three options."))
        self.assertFalse(self.idea_tree(idea).exists())
        self.assertEqual(self.item(idea)["status"], "open")

class WorkPrTest(Desk):
    BRANCH = "agent/chore-audit-20261111-1200"

    def test_a_review_follow_up_uses_codex_and_receives_the_request(self):
        review = self.review(self.BRANCH)
        env = self.codex_fake()
        result = self.run_lib("work-pr.sh", str(review), REQUEST="Merge main", AGENT_DESK_ISSUE_RUNNER="codex", **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<request>\nMerge main\n</request>", self.codex_log.read_text())
        self.assertIn("Codex session ID is in the run log", self.notes(review)[0][1])
        self.assertEqual(self.claude_args(), [])

    def test_a_run_posts_its_session_and_outcome_on_the_review(self):
        review = self.review(self.BRANCH)
        self.lose_origin()
        result = self.run_lib("work-pr.sh", str(review), REQUEST="@agent Merge main")
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.worktree("chore-audit-20261111-1200")
        args = self.claude_args()
        self.assertEqual(args[0], str(dest))
        prompt = "\n".join(args[2:args.index("--permission-mode")])
        self.assertIn(f"A chore run of the audit skill opened review #{review}", prompt)
        self.assertIn("<request>\n@agent Merge main\n</request>", prompt)
        self.assertIn(f"`agent-desk review view {review}`", prompt)
        self.assertIn("`git diff main...HEAD`", prompt)
        self.assertIn("each commit reaches the\nreview without a push", prompt)
        session = args[args.index("--session-id") + 1]
        log = f" (log: `journalctl --user -u {UNIT}`)"
        self.assertEqual(self.notes(review), [
            ("desk", f"An agent started on `{self.BRANCH}`{log}. To take over, run `claude --resume {session}` in "
                     f"`{dest}`.{SIGNED}"),
            ("desk", f"The agent finished on `{self.BRANCH}`{log}.{SIGNED}"),
        ])
        self.assert_no_github_writes()

    def test_a_run_that_stops_early_says_so_on_the_review_and_exits_stopped(self):
        review = self.review(self.BRANCH)
        result = self.run_lib("work-pr.sh", str(review), REQUEST="@agent Merge main",
                              **self.stopped_early("just bench"))
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(self.notes(review)[-1], (
            "desk", f"The agent stopped early on `{self.BRANCH}`: it ended its turn while `just bench` still ran, so "
                    f"the run stopped it (log: `journalctl --user -u {UNIT}`). Any commits it made are on the branch."
                    f"{SIGNED}"))

    def test_a_worktree_in_use_stops_the_run(self):
        review = self.review(self.BRANCH)
        dest = self.worktree("chore-audit-20261111-1200")
        dest.mkdir()
        worker = subprocess.Popen(["sleep", "30"], cwd=dest)
        self.addCleanup(worker.wait)
        self.addCleanup(worker.kill)
        result = self.run_lib("work-pr.sh", str(review))
        self.assertEqual(result.returncode, 0, result.stderr)
        [(author, body)] = self.notes(review)
        self.assertEqual(author, "desk")
        self.assertEqual(body, f"The agent didn't start: someone works in `{dest}` (log: `journalctl --user -u {UNIT}`)."
                               f"{SIGNED}")
        self.assertEqual(self.claude_args(), [])

    def test_a_review_whose_branch_the_main_checkout_lacks_stops_the_run(self):
        review = self.review(self.BRANCH)
        git("branch", "-q", "-D", self.BRANCH, cwd=self.main)
        result = self.run_lib("work-pr.sh", str(review))
        self.assertEqual(result.returncode, 1, result.stderr)
        [(author, body)] = self.notes(review)
        self.assertEqual(author, "desk")
        self.assertEqual(body, f"The agent didn't start: the main checkout has no branch `{self.BRANCH}` (log: "
                               f"`journalctl --user -u {UNIT}`).{SIGNED}")
        self.assertFalse(self.worktree("chore-audit-20261111-1200").exists())
        self.assertEqual(self.claude_args(), [])

    def test_an_issue_branch_is_refused(self):
        review = self.review("agent/issue-3")
        result = self.run_lib("work-pr.sh", str(review))
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"Review #{review} is from agent/issue-3", result.stderr)
        self.assertEqual(self.notes(review), [])

    def test_a_review_the_store_lacks_stops_the_run(self):
        result = self.run_lib("work-pr.sh", "40")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has no review #40", result.stderr)
        self.assertEqual(self.claude_args(), [])


if __name__ == "__main__":
    unittest.main()
