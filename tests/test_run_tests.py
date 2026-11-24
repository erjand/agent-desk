"""Tests for scripts/run_tests.py, which runs the Python test modules in parallel worker processes. They run it on a
temporary folder of small test modules."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import failures  # noqa: E402

PASSING = """
import unittest

class PassingTest(unittest.TestCase):
    def test_one(self):
        pass

    def test_two(self):
        pass

    @unittest.skip("not here")
    def test_skipped(self):
        pass
"""
FAILING = """
import unittest

class BrokenTest(unittest.TestCase):
    def test_fails(self):
        self.assertEqual(1, 2)

    def test_errors(self):
        raise RuntimeError("the fixture is gone")

    def test_passes(self):
        pass
"""
CRASHING = """
import os

os._exit(3)
"""


def run(folder, *args):
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "run_tests.py"), "-s", str(folder), *args],
                          capture_output=True, text=True)


class RunTestsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name, text):
        (self.folder / f"{name}.py").write_text(textwrap.dedent(text))

    def test_a_passing_suite_exits_0_and_ends_with_the_count_and_the_wall_time(self):
        self.write("test_pass", PASSING)
        self.write("test_pass_too", PASSING)
        done = run(self.folder, "-j", "2")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("test_pass: 3 tests in ", done.stdout)
        self.assertRegex(done.stdout, r"\nRan 6 tests in \d+\.\d{3}s\n\nOK \(skipped=2\)\n$")

    def test_a_failing_module_prints_unittests_output_and_the_run_exits_1(self):
        self.write("test_pass", PASSING)
        self.write("test_broken", FAILING)
        done = run(self.folder)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("FAIL: test_fails (test_broken.BrokenTest.test_fails)", done.stdout)
        self.assertIn("ERROR: test_errors (test_broken.BrokenTest.test_errors)", done.stdout)
        self.assertIn("AssertionError: 1 != 2", done.stdout)
        self.assertIn("RuntimeError: the fixture is gone", done.stdout)
        self.assertRegex(done.stdout, r"\nRan 6 tests in \d+\.\d{3}s\n\nFAILED \(failures=1, errors=1, skipped=1\)\n$")
        self.assertEqual(failures.failed_tests(done.stdout),
                         ["test_broken.BrokenTest.test_errors", "test_broken.BrokenTest.test_fails"])

    def test_a_module_that_exits_before_unittests_summary_fails_the_run(self):
        self.write("test_pass", PASSING)
        self.write("test_crash", CRASHING)
        done = run(self.folder)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("test_crash failed: 0 tests in ", done.stdout)
        self.assertIn("exit status 3", done.stdout)
        self.assertTrue(done.stdout.endswith("\nFAILED (errors=1, skipped=1)\n"), done.stdout)

    def test_it_runs_only_the_modules_named(self):
        self.write("test_pass", PASSING)
        self.write("test_broken", FAILING)
        done = run(self.folder, "test_pass")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("Running 1 part of 1 test module in 1 worker process.", done.stdout)
        self.assertNotIn("test_broken", done.stdout)

    def test_it_splits_modules_by_class_and_large_classes_by_chunk(self):
        many = "".join(f"\n    def test_{n}(self):\n        pass\n" for n in range(10))
        self.write("test_many", f"import unittest\n\nclass ManyTest(unittest.TestCase):{many}\n"
                                f"class OtherTest(unittest.TestCase):\n    def test_x(self):\n        pass\n")
        done = run(self.folder)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("Running 3 parts of 1 test module", done.stdout)
        self.assertIn("test_many: 11 tests in ", done.stdout)

    def test_it_refuses_a_module_that_does_not_exist(self):
        done = run(self.folder, "test_missing")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no test module test_missing.py", done.stderr)


if __name__ == "__main__":
    unittest.main()
