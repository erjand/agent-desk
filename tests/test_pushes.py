"""Tests for agent-desk/lib/pushes.py, which pushes main and the agent branches to origin and retries while origin is
out of reach. Each runs git against a temporary bare origin, a main checkout cloned from it, and another clone that
moves origin as someone else would."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import pushes  # noqa: E402

NOW = 1_800_000_000
REPO = "owner/repo"


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True).stdout.strip()


class PushCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "gitconfig").write_text("[user]\n\tname = Tester\n\temail = t@example.com\n"
                                             "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        for patcher in (mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"),
                                                     "GIT_CONFIG_NOSYSTEM": "1"}),
                        mock.patch.object(pushes, "STATE", self.root / "pushes")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.origin = self.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        self.main = self.root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.main)], check=True, capture_output=True)
        self.commit(self.main, "start")
        git(self.main, "push", "-q", "origin", "main")
        self.said = []
        self.state = {}

    def commit(self, where, message):
        (Path(where) / f"{message}.txt").write_text(message)
        git(where, "add", ".")
        git(where, "commit", "-q", "-m", message)
        return git(where, "rev-parse", "HEAD")

    def tick(self, now=NOW, run=None):
        return pushes.tick(self.main, self.state, now, self.said.append, desk_root=self.root, run=run)

    def on_origin(self, branch):
        return git(self.origin, "rev-parse", f"refs/heads/{branch}")

    def shown(self, now=NOW):
        pushes.save(pushes.state_path(REPO), self.state)
        return pushes.shown(self.main, REPO, now)


class MainTest(PushCase):
    def test_main_ahead_of_origin_is_pushed(self):
        sha = self.commit(self.main, "merged")
        self.assertEqual(self.shown()["ahead"], 1)
        self.tick()
        self.assertEqual(self.on_origin("main"), sha)
        self.assertEqual(self.shown(), {"ahead": 0, "push": {"state": "ok", "pushedAt": NOW}, "agentBranches": {"pushed": 0, "waiting": 0}})
        self.assertEqual(pushes.describe(self.shown()), "")

    def test_a_push_that_fails_waits_longer_each_time_and_later_succeeds(self):
        sha = self.commit(self.main, "merged")
        git(self.main, "remote", "set-url", "origin", str(self.root / "unreachable.git"))
        self.tick()
        self.assertEqual((self.state["failures"], self.state["retryAt"]), (1, NOW + pushes.RETRY))
        # A fetch that fails counts as the host being down.
        self.assertTrue(self.state["error"].startswith("couldn't fetch origin: "))
        view = self.shown(NOW + 10)
        self.assertEqual((view["ahead"], view["push"]["state"], view["push"]["retryIn"]), (1, "retrying", 20))
        self.assertIn("Main is 1 commit ahead of origin; push failed", pushes.describe(view))
        # A tick before the wait ends doesn't try.
        self.tick(NOW + 10)
        self.assertEqual(self.state["failures"], 1)
        self.tick(NOW + pushes.RETRY)
        self.assertEqual((self.state["failures"], self.state["retryAt"]),
                         (2, NOW + pushes.RETRY + 2 * pushes.RETRY))
        self.assertEqual(pushes.wait_after(20), pushes.MAX_WAIT)

        git(self.main, "remote", "set-url", "origin", str(self.origin))
        self.tick(NOW + 3 * pushes.RETRY)
        self.assertEqual(self.on_origin("main"), sha)
        self.assertNotIn("failures", self.state)
        self.assertEqual(self.shown(), {"ahead": 0, "push": {"state": "ok", "pushedAt": NOW + 3 * pushes.RETRY},
                                        "agentBranches": {"pushed": 0, "waiting": 0}})

    def test_a_push_origin_rejects_because_it_moved_stops_until_main_moves(self):
        other = self.root / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], check=True, capture_output=True)
        theirs = self.commit(other, "theirs")
        git(other, "push", "-q", "origin", "main")
        self.commit(self.main, "merged")

        # The tick fetches origin's main first, so it stops without trying a push it knows origin refuses.
        with mock.patch.object(pushes, "git", wraps=pushes.git) as called:
            self.tick()
        self.assertFalse(any(call.args[1] == "push" for call in called.call_args_list))
        self.assertEqual(self.on_origin("main"), theirs)
        self.assertEqual(git(self.main, "rev-parse", pushes.ORIGIN_MAIN), theirs)
        view = self.shown()
        self.assertEqual(view["push"], {"state": "rejected", "error": "origin's main has 1 commit main lacks"})
        self.assertIn("origin refused the push", pushes.describe(view))
        # It doesn't try again while main stands where origin refused it.
        with mock.patch.object(pushes, "git", wraps=pushes.git) as called:
            self.tick(NOW + 3600)
        self.assertFalse(any(call.args[1] == "push" and "refs/heads/main:refs/heads/main" in call.args
                             for call in called.call_args_list))

        # The person merges origin's main into main, and the next tick pushes it.
        git(self.main, "fetch", "-q", "origin")
        git(self.main, "merge", "-q", "--no-edit", "origin/main")
        self.tick(NOW + 3660)
        self.assertEqual(self.on_origin("main"), git(self.main, "rev-parse", "main"))
        self.assertEqual(self.shown()["push"], {"state": "ok", "pushedAt": NOW + 3660})

    def test_a_fetch_finds_what_origin_took_since_and_counts_from_there(self):
        sha = self.commit(self.main, "merged")
        # Origin took main some other way, and the checkout's copy of origin's main doesn't know yet.
        git(self.main, "push", "-q", "origin", "main:main")
        git(self.main, "update-ref", pushes.ORIGIN_MAIN, f"{sha}^")
        with mock.patch.object(pushes, "git", wraps=pushes.git) as called:
            self.tick()
        self.assertFalse(any(call.args[1] == "push" for call in called.call_args_list))
        self.assertEqual(git(self.main, "rev-parse", pushes.ORIGIN_MAIN), sha)
        self.assertEqual(self.shown(), {"ahead": 0, "push": {"state": "ok"},
                                        "agentBranches": {"pushed": 0, "waiting": 0}})

    def test_an_origin_without_main_takes_it(self):
        empty = self.root / "empty.git"
        subprocess.run(["git", "init", "-q", "--bare", str(empty)], check=True)
        git(self.main, "remote", "set-url", "origin", str(empty))
        git(self.main, "update-ref", "-d", pushes.ORIGIN_MAIN)
        self.tick()
        self.assertEqual(git(empty, "rev-parse", "refs/heads/main"), git(self.main, "rev-parse", "main"))
        self.assertNotIn("failures", self.state)

    def test_a_main_checkout_detached_or_on_another_branch_pushes_main_alone(self):
        sha = self.commit(self.main, "merged")
        git(self.main, "checkout", "-q", "--detach")
        self.tick()
        self.assertEqual(self.on_origin("main"), sha)
        git(self.main, "checkout", "-q", "-b", "mine")
        self.commit(self.main, "mine")
        # The merge queue moves main without the checkout, as with update-ref.
        merged = git(self.main, "commit-tree", "-p", sha, "-m", "next", f"{sha}^{{tree}}")
        git(self.main, "update-ref", "refs/heads/main", merged)
        self.tick()
        self.assertEqual(self.on_origin("main"), merged)
        with self.assertRaises(subprocess.CalledProcessError):
            self.on_origin("mine")

    def test_a_project_whose_host_is_none_pushes_nothing(self):
        (self.main / ".agent-desk.json").write_text('{"host": "none"}')
        before = self.on_origin("main")
        self.commit(self.main, "merged")
        git(self.main, "branch", "agent/issue-7")
        with mock.patch.object(pushes, "git", wraps=pushes.git) as called:
            self.assertEqual(self.tick(), {})
        self.assertFalse(any(call.args[1] in ("push", "fetch") for call in called.call_args_list))
        self.assertEqual(self.on_origin("main"), before)
        self.assertEqual(self.shown(), {"ahead": 0, "push": {"state": "none"}})

    def test_git_never_asks_for_a_passphrase(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_SSH_COMMAND", None)
            with mock.patch.object(pushes.subprocess, "run") as run:
                pushes.git(self.main, "fetch")
            env = run.call_args.kwargs["env"]
            self.assertEqual((env["GIT_TERMINAL_PROMPT"], env["GIT_SSH_COMMAND"]), ("0", pushes.SSH))
            self.assertIn("BatchMode=yes", pushes.SSH)

    def test_a_checkout_without_origin_has_nothing_to_push(self):
        git(self.main, "remote", "remove", "origin")
        self.commit(self.main, "merged")
        self.assertEqual(self.tick(), {})
        self.assertEqual(self.shown(), {"ahead": 0, "push": {"state": "none"}})

    def test_a_push_that_changes_version_in_the_desks_own_checkout_publishes_the_release(self):
        calls = []

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        self.commit(self.main, "merged")
        pushes.tick(self.main, self.state, NOW, self.said.append, desk_root=self.main, run=run)
        self.assertEqual(calls, [])
        (self.main / "VERSION").write_text("1.2.0\n")
        git(self.main, "add", "VERSION")
        git(self.main, "commit", "-q", "-m", "release")
        pushes.tick(self.main, self.state, NOW, self.said.append, desk_root=self.main, run=run)
        self.assertEqual([args[-2:] for args in calls], [["scripts/release.py", "publish"]])
        # Another project's checkout never publishes.
        (self.main / "VERSION").write_text("1.3.0\n")
        git(self.main, "commit", "-q", "-am", "release")
        calls.clear()
        self.tick(run=run)
        self.assertEqual(calls, [])


    def test_a_failed_publish_says_how_to_finish(self):
        (self.main / "VERSION").write_text("1.2.0\n")
        git(self.main, "add", "VERSION")
        git(self.main, "commit", "-q", "-m", "release")
        pushes.tick(self.main, self.state, NOW, self.said.append, desk_root=self.main,
                    run=lambda args, **_: subprocess.CompletedProcess(args, 1))
        self.assertIn(f"Couldn't publish 1.2.0; run `just release publish` in {self.main} to finish.", self.said)


class BranchesTest(PushCase):
    def test_agent_branches_that_moved_are_pushed_and_a_failed_one_blocks_nothing(self):
        git(self.main, "branch", "agent/issue-7")
        git(self.main, "branch", "agent/issue-8")
        git(self.main, "branch", "feature/mine")
        # Origin's agent/issue-8 went its own way, so a push that never forces fails for it.
        other = self.root / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], check=True, capture_output=True)
        git(other, "checkout", "-q", "-b", "agent/issue-8")
        self.commit(other, "elsewhere")
        git(other, "push", "-q", "origin", "agent/issue-8")
        git(self.main, "fetch", "-q", "origin")
        merged = self.commit(self.main, "merged")

        self.tick()
        self.assertEqual(self.on_origin("main"), merged)
        self.assertEqual(self.on_origin("agent/issue-7"), git(self.main, "rev-parse", "agent/issue-7"))
        self.assertEqual(self.state["branches"], ["agent/issue-8"])
        self.assertEqual(self.shown()["push"], {"state": "ok", "pushedAt": NOW, "branches": ["agent/issue-8"]})
        self.assertEqual(pushes.describe(self.shown()), "Couldn't push agent/issue-8.")
        # The board counts the branch that went and the one that waits, with git's reason for the one that failed.
        branches = self.shown()["agentBranches"]
        self.assertEqual({key: branches[key] for key in ("pushed", "waiting", "pushedAt", "failed")},
                         {"pushed": 1, "waiting": 1, "pushedAt": NOW, "failed": ["agent/issue-8"]})
        self.assertIn("rejected", branches["error"])
        with self.assertRaises(subprocess.CalledProcessError):
            self.on_origin("feature/mine")

    def test_a_branch_that_moves_waits_for_the_next_tick_and_a_push_clears_the_failure(self):
        git(self.main, "branch", "agent/issue-7")
        self.tick()
        self.assertEqual(self.shown()["agentBranches"], {"pushed": 1, "waiting": 0, "pushedAt": NOW})
        git(self.main, "checkout", "-q", "agent/issue-7")
        self.commit(self.main, "more")
        git(self.main, "checkout", "-q", "main")
        self.assertEqual(self.shown()["agentBranches"], {"pushed": 0, "waiting": 1, "pushedAt": NOW})
        self.state.update(branches=["agent/issue-7"], branchesError="the host is down")
        self.tick(NOW + 60)
        self.assertEqual(self.shown()["agentBranches"], {"pushed": 1, "waiting": 0, "pushedAt": NOW + 60})
        self.assertNotIn("branchesError", self.state)

    def test_branches_wait_while_origin_is_out_of_reach(self):
        git(self.main, "branch", "agent/issue-7")
        self.commit(self.main, "merged")
        git(self.main, "remote", "set-url", "origin", str(self.root / "unreachable.git"))
        with mock.patch.object(pushes, "push_branches") as branches:
            self.tick()
        branches.assert_not_called()


if __name__ == "__main__":
    unittest.main()
