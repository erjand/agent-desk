"""Tests for agent-desk/lib/failures.py, which prints what failed in a local branch check's log."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import failures  # noqa: E402
import store  # noqa: E402

class ExcerptTest(unittest.TestCase):
    def test_failing_lines_keep_the_lines_after_them_once_without_passing_summaries(self):
        log = "\n".join([
            "Building...",
            "Passed!  - Failed:     0, Passed:    96",
            "Game/A.cs(1,2): error CS1: bad",
            "Game/A.cs(1,2): error CS1: bad",
            "  Failed Game.Tests.Walks [3 ms]",
            "  Error Message:",
            "   Assert.Equal() Failure",
            "Expected: 3",
            "Actual:   4",
            "  Stack Trace:",
            "unrelated",
            "error: recipe `test` failed on line 3 with exit code 1",
        ])
        excerpt, more = failures.failure_excerpt(log, context=2)
        self.assertEqual(excerpt, ["Game/A.cs(1,2): error CS1: bad", "  Failed Game.Tests.Walks [3 ms]",
                                   "  Error Message:", "   Assert.Equal() Failure", "Expected: 3", "Actual:   4"])
        self.assertEqual(more, 0)

    def test_a_compiler_error_keeps_no_lines_after_it(self):
        log = "\n".join(["Game/A.cs(41,9): error CS0103: bad", "  Host.Tests -> Host.dll",
                                              "src/b.rs:3:7: error[E0425]: bad", "Compiling c"])
        self.assertEqual(failures.failure_excerpt(log)[0],
                         ["Game/A.cs(41,9): error CS0103: bad", "src/b.rs:3:7: error[E0425]: bad"])

    def test_a_long_excerpt_says_how_many_lines_it_leaves_out(self):
        excerpt, more = failures.failure_excerpt("\n".join(f"error {n}" for n in range(10)), limit=4)
        self.assertEqual(excerpt, ["error 0", "error 1", "error 2", "error 3"])
        self.assertEqual(more, 6)

    def test_a_log_with_no_failing_line_gives_its_last_lines(self):
        self.assertEqual(failures.failure_excerpt("\n".join("abc"), limit=2), (["b", "c"], 0))

    def test_a_log_of_warnings_only_gives_its_last_lines(self):
        log = "\n".join([
            "<frozen importlib._bootstrap_external>:511: ResourceWarning: unclosed database in <sqlite3.Connection>",
            "ResourceWarning: Enable tracemalloc to get the object allocation traceback",
            "Enable tracemalloc to get the object allocation traceback",
            "a.py:3: DeprecationWarning: failure_hook is deprecated",
            "MESA-EGL: warning: egl: failed to create dri2 screen",
            "done",
        ])
        self.assertEqual(failures.failure_excerpt(log, limit=2),
                         (["MESA-EGL: warning: egl: failed to create dri2 screen", "done"], 0))

    def test_a_compiler_warning_is_dropped_and_its_error_still_shows(self):
        log = "\n".join([
            "Game/A.cs(41,9): warning CS0168: The variable 'error' is declared but never used",
            "src/b.c:3:7: warning: unused variable 'failed'",
            "Compiling c",
            "src/b.rs:3:7: error[E0425]: bad",
        ])
        self.assertEqual(failures.failure_excerpt(log)[0], ["src/b.rs:3:7: error[E0425]: bad"])

    def test_a_failure_word_in_a_path_or_file_name_is_no_failure(self):
        log = "\n".join([
            "  Content/Audio/UI/error.wav",
            "Building 'exception_handler.py'",
            "Reading C:\\game\\panic\\x",
            "Summary: 0 error(s)",
            "    0 Error(s)",
            "panic: index out of range",
        ])
        self.assertEqual(failures.failure_excerpt(log, context=0)[0], ["panic: index out of range"])

    def test_without_a_failing_line_the_line_before_the_runners_says_what_failed(self):
        log = "\n".join([
            "+/build:Textures/a.png",
            "... 1151 more diff lines",
            "",
            "Content/Content.mgcb is stale. Run `just content-manifest` and commit the result.",
            "error: recipe `check-content-manifest` failed on line 224 with exit code 1",
            "error: recipe `ci` failed on line 171 with exit code 1",
        ])
        self.assertEqual(failures.failure_excerpt(log), (
            ["Content/Content.mgcb is stale. Run `just content-manifest` and commit the result."], 0))


class LocalLogTest(unittest.TestCase):
    """A local branch check's log, as lib/checks.py writes it."""

    LOG = "\n".join([
        "Check 3 of agent/issue-5 at abc123 merged with main, attempt 1.",
        "",
        "== Make a scratch worktree at abc123",
        "Preparing worktree (detached HEAD abc123)",
        "== Merge main",
        "Already up to date.",
        "== Test: just test",
        "python3 -B -m unittest discover -s tests",
        ".F",
        "FAIL: test_widget (tests.test_widget.WidgetTest.test_widget)",
        "Traceback (most recent call last):",
        '  File "/home/u/.cache/agent-desk/checks/org__game-3/tests/test_widget.py", line 5, in test_widget',
        "AssertionError: 1 != 2",
        "Ran 2 tests in 0.001s",
        "FAILED (failures=1)",
        "error: Recipe `test` failed on line 3 with exit code 1",
        "",
        "The check failed.",
    ])

    def test_a_check_log_gives_what_failed_without_its_steps_or_scratch_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "3.log"
            path.write_text(self.LOG)
            excerpt, more = failures.log_excerpt(path)
        self.assertEqual(excerpt, [
            "FAIL: test_widget (tests.test_widget.WidgetTest.test_widget)",
            "Traceback (most recent call last):",
            '  File "tests/test_widget.py", line 5, in test_widget',
            "AssertionError: 1 != 2",
            "Ran 2 tests in 0.001s",
            "FAILED (failures=1)",
        ])
        self.assertEqual(more, 0)

    def test_the_log_of_a_check_that_failed_in_a_temporary_repository_names_its_failure(self):
        import checks
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
            git = lambda *args: subprocess.run(["git", "-C", str(root / "game"), *args], check=True,
                                               capture_output=True, text=True).stdout.strip()
            with mock.patch.dict("os.environ", env), mock.patch.object(checks, "WORK", root / "work"):
                (root / "game").mkdir()
                git("init", "-q", "-b", "main")
                (root / "game" / "a.txt").write_text("base\n")
                git("add", "a.txt")
                git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "Base")
                git("branch", "agent/issue-5")
                row = {"id": 4, "project": "org/game", "branch": "agent/issue-5", "attempt": 1,
                       "head_sha": git("rev-parse", "agent/issue-5")}
                config = {"test": "echo 'collecting'; echo 'AssertionError: widget broke' >&2; exit 1"}
                with open(root / "4.log", "w", encoding="utf-8") as log:
                    self.assertEqual(checks.run_check(row, root / "game", config, log), ("failed", "test"))
            self.assertEqual(failures.log_excerpt(root / "4.log"), (["AssertionError: widget broke"], 0))
            self.assertNotIn("work", git("worktree", "list"))

    def test_a_failing_test_after_resource_warnings_comes_first(self):
        log = "\n".join([
            "== Test: just test",
            "......<frozen importlib._bootstrap_external>:511: ResourceWarning: unclosed database in "
            "<sqlite3.Connection object at 0x7f0d7685fd30>",
            "ResourceWarning: Enable tracemalloc to get the object allocation traceback",
            "<frozen importlib._bootstrap_external>:511: ResourceWarning: unclosed database in "
            "<sqlite3.Connection object at 0x7f0d7685ff10>",
            "ResourceWarning: Enable tracemalloc to get the object allocation traceback",
            "......",
            "======================================================================",
            "FAIL: test_each_reading_command_leaves_an_older_database_unchanged (test_store.ReadOnlyTest."
            "test_each_reading_command_leaves_an_older_database_unchanged) (argv=['review', 'checks', '2'])",
            "----------------------------------------------------------------------",
            "Traceback (most recent call last):",
            '  File "/home/u/.cache/agent-desk/checks/erjand__agent-desk-14/tests/test_store.py", line 253, in '
            "test_each_reading_command_leaves_an_older_database_unchanged",
            "    self.assertEqual(command(argv), 0, err.getvalue())",
            "AssertionError: 8 != 0 :",
            "",
            "----------------------------------------------------------------------",
            "Ran 833 tests in 87.358s",
            "",
            "FAILED (failures=1)",
            "error: recipe `test` failed on line 7 with exit code 1",
            "",
            "The check failed.",
        ])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "14.log"
            path.write_text(log)
            with mock.patch("builtins.print") as printed:
                self.assertEqual(failures.main(["failures.py", "log", str(path)]), 0)
        printed_lines = printed.call_args.args[0].split("\n")
        self.assertTrue(printed_lines[0].startswith("FAIL: test_each_reading_command_leaves_an_older_database"))
        self.assertEqual(printed_lines[2:6], [
            "Traceback (most recent call last):",
            '  File "tests/test_store.py", line 253, in test_each_reading_command_leaves_an_older_database_unchanged',
            "    self.assertEqual(command(argv), 0, err.getvalue())",
            "AssertionError: 8 != 0 :",
        ])
        self.assertNotIn("ResourceWarning", printed.call_args.args[0])

    def test_log_mode_prints_the_excerpt(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "3.log"
            path.write_text(self.LOG)
            with mock.patch("builtins.print") as printed:
                self.assertEqual(failures.main(["failures.py", "log", str(path)]), 0)
        self.assertTrue(printed.call_args.args[0].startswith("FAIL: test_widget"))


class ExcerptModeTest(unittest.TestCase):
    """`excerpt <n>` against a temporary store and main checkout, with a gh that fails if anything calls it."""

    REPO = "o/r"
    BRANCH = "agent/issue-5"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.main = self.root / "desk"
        self.main.mkdir()

        def git(*args):
            return subprocess.run(["git", "-C", str(self.main), *args], check=True, capture_output=True,
                                  text=True).stdout.strip()

        git("init", "-q", "-b", "main")
        git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "Base")
        git("branch", self.BRANCH)
        self.head = git("rev-parse", self.BRANCH)
        self.database = self.root / "state" / "desk.sqlite"
        self.desk = store.Store(self.database)
        self.addCleanup(self.desk.close)
        self.number = self.desk.create(self.REPO, "review", "Change", branch=self.BRANCH)

    def check(self, status, log=None, head=None, name=None):
        """A check of the review's head, or of `head`, that ended `status` with `log` as its log's text."""
        number = self.desk.check_queue(self.REPO, self.BRANCH, head or self.head)
        path = self.root / f"{number}.log"
        if log is not None:
            path.write_text(log)
            self.desk.check_started(number, path)
        self.desk.check_ended(number, status, name)
        return path

    def excerpt(self):
        printed = []
        with mock.patch.dict("os.environ", {"GH_REPO": self.REPO}), \
                mock.patch.object(store, "DATABASE", self.database), \
                mock.patch.object(store, "PROJECTS", self.root / "projects"), \
                mock.patch("builtins.print", side_effect=printed.append):
            (self.root / "projects").write_text(f"{self.REPO} {self.main}\n")
            self.assertEqual(failures.main(["failures.py", "excerpt", str(self.number)]), 0)
        return printed

    def test_a_brief_names_the_check_and_its_log_and_quotes_what_failed(self):
        failed = {"name": "test", "status": "failed", "log_path": "/s/4.log"}
        self.assertEqual(failures.brief(failed, ["A.cs(1,2): error CS1061: no CountOf"], 0),
                         "test failed: /s/4.log\n\nA.cs(1,2): error CS1061: no CountOf")
        self.assertTrue(failures.brief(failed, ["bad"], 3).endswith("\n(3 more lines in /s/4.log.)"))
        self.assertTrue(failures.brief({**failed, "status": "timed-out"}, [], 0).startswith(
            "test timed out: /s/4.log\n\nThe check's log shows no lines."))
        self.assertEqual(failures.brief({"name": "test", "status": "lost", "log_path": None}, [], 0),
                         "test was lost before it wrote a log.")

    def test_excerpt_prints_each_failed_check_on_the_review_head_from_its_log(self):
        log = self.check("failed", "== Run test\nA.cs(1,2): error CS1501: BuildTimeline takes 4 arguments\n")
        self.check("failed", "error: an older head\n", head="0" * 40)
        merge = self.check("failed", "CONFLICT (content): Merge conflict in notes.txt\n", name="merge")
        self.assertEqual(self.excerpt(), [
            f"test failed: {log}\n\nA.cs(1,2): error CS1501: BuildTimeline takes 4 arguments\n\n"
            f"merge failed: {merge}\n\nCONFLICT (content): Merge conflict in notes.txt"])

    def test_the_newest_check_of_a_name_counts_so_a_passed_retry_prints_nothing(self):
        self.check("failed", "error: flaky\n")
        self.check("passed", "ok\n")
        self.assertEqual(self.excerpt(), [])

    def test_a_timed_out_check_and_a_lost_one_without_a_log_print_too(self):
        log = self.check("timed-out", "still waiting\n")
        self.check("lost", name="build")
        self.assertEqual(self.excerpt(), [f"test timed out: {log}\n\nstill waiting\n\n"
                                          "build was lost before it wrote a log."])

    def test_a_log_removed_since_quotes_no_lines(self):
        log = self.check("failed", "error: gone\n")
        log.unlink()
        self.assertEqual(self.excerpt(), [f"test failed: {log}\n\nThe check's log shows no lines."])

    def test_a_review_the_store_lacks_fails(self):
        with mock.patch.dict("os.environ", {"GH_REPO": self.REPO}), \
                mock.patch.object(store, "DATABASE", self.database), \
                mock.patch("sys.stderr") as stderr:
            self.assertEqual(failures.main(["failures.py", "excerpt", "40"]), 1)
        self.assertIn("has no review #40", "".join(call.args[0] for call in stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
