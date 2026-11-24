#!/usr/bin/env python3
"""Runs the Python tests in tests/ in parallel worker processes, as `just test` and the GitHub-hosted test workflows
do:

    python3 -B scripts/run_tests.py [--jobs N] [--start-directory DIR] [test_module ...]

The script finds the tests as `unittest discover` does and splits them into parts of one test class each, with a class
of more than CHUNK tests split into parts of up to CHUNK tests, since one module's or one class's tests can take a
minute on their own. Each part runs as `python3 -B -m unittest <test id>...` from the tests folder, so a test keeps the
id it has under a plain `unittest discover`, such as `test_store.StoreTest.test_x`. The pool runs the CPU count, capped
at MAX_JOBS, parts at once unless --jobs sets it, and starts the parts of the largest module files first, so a long
part doesn't start last.

Before the parts start, the script writes current bytecode for lib/, which the tests import and run as scripts under
`python3 -B`, so no part compiles lib/ from source. A module that doesn't compile fails in the tests that load it.

A module whose parts all pass prints one line with its test count and the time its parts took. A part that fails or
errors prints unittest's whole output, with each `FAIL:` or `ERROR:` line and its traceback, so lib/failures.py names
the failed tests from a branch check's log. The run ends with unittest's own summary, the total count of tests run and
the wall time, then `OK` or `FAILED (...)`, and exits 1 when any part failed, errored, or exited before unittest's
summary.
"""

import argparse
import compileall
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_JOBS = 8
CHUNK = 8
RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
RESULT = re.compile(r"^(OK|FAILED)(?: \((.*)\))?$", re.M)
# The order unittest's summary names the counts in.
COUNTS = ("failures", "errors", "skipped", "expected failures", "unexpected successes")


def plural(count, ending="s"):
    return "" if count == 1 else ending


def default_jobs():
    # os.process_cpu_count, which counts only the CPUs this process may use, came in Python 3.13.
    return min(getattr(os, "process_cpu_count", os.cpu_count)() or 1, MAX_JOBS)


def module_paths(start, names):
    """The test modules to run, largest file first: those `names` gives, or every test_*.py in `start`."""
    paths = [start / f"{name.removesuffix('.py')}.py" for name in names] or sorted(start.glob("test_*.py"))
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"run_tests.py: no test module {', '.join(missing)} in {start}")
    return sorted(paths, key=lambda path: path.stat().st_size, reverse=True)


# Prints, as JSON, each named module's test ids, or null for a module that fails to import, run in a child process so
# a module that exits as it loads can't end this one.
LIST = """
import json, sys, unittest

def ids(suite):
    for test in suite:
        yield from ids(test) if isinstance(test, unittest.TestSuite) else [test.id()]

start, found = sys.argv[1], {}
for name in sys.argv[2:]:
    tests = list(ids(unittest.TestLoader().discover(start, pattern=name + ".py", top_level_dir=start)))
    found[name] = None if any(test.startswith("unittest.loader._FailedTest.") for test in tests) else tests
print(json.dumps(found))
"""


def parts(start, names):
    """
    Each module's tests split into lists of test ids, one list for each class, with a class of more than CHUNK tests
    split into lists of up to CHUNK, as (module, ids) pairs. A module that fails to import, or every module when the
    listing fails, gives one part, its name, which runs the whole module and reports its error.
    """
    listing = subprocess.run([sys.executable, "-B", "-c", LIST, str(start), *names], cwd=start,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True)
    try:
        found = json.loads(listing.stdout.splitlines()[-1])
    except (IndexError, ValueError):
        found = {}
    work = []
    for name in names:
        if not found.get(name):
            work.append((name, [name]))
            continue
        classes = {}
        for test in found[name]:
            classes.setdefault(test.rpartition(".")[0], []).append(test)
        work += [(name, tests[at:at + CHUNK]) for tests in classes.values() for at in range(0, len(tests), CHUNK)]
    return work


def run_part(start, tests):
    """Runs a part's tests and returns its output, its exit status, and its time in seconds."""
    began = time.monotonic()
    done = subprocess.run([sys.executable, "-B", "-m", "unittest", *tests], cwd=start, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    return done.stdout, done.returncode, time.monotonic() - began


def tally(output, status):
    """The tests a part's output says it ran, and its counts by COUNTS, with an error for a run that ended early."""
    ran = RAN.findall(output)
    result = RESULT.findall(output)
    counts = dict.fromkeys(COUNTS, 0)
    if result:
        for part in filter(None, result[-1][1].split(", ")):
            name, _, value = part.rpartition("=")
            counts[name] += int(value)
    if not ran or not result or (status != 0 and result[-1][0] == "OK"):
        counts["errors"] += 1
    return (int(ran[-1]) if ran else 0), counts


def failed(counts):
    return bool(counts["failures"] or counts["errors"] or counts["unexpected successes"])


def summary(counts):
    """unittest's last line for a run, such as `OK (skipped=2)` or `FAILED (failures=1, errors=2)`."""
    parts = ", ".join(f"{name}={counts[name]}" for name in COUNTS if counts[name])
    word = "FAILED" if failed(counts) else "OK"
    return f"{word} ({parts})" if parts else word


class Module:
    """One module's tally as its parts finish."""

    def __init__(self, parts):
        self.left, self.ran, self.seconds, self.counts = parts, 0, 0.0, dict.fromkeys(COUNTS, 0)

    def add(self, ran, counts, seconds):
        self.left -= 1
        self.ran += ran
        self.seconds += seconds
        for key in COUNTS:
            self.counts[key] += counts[key]


def run_tests_main(argv):
    parser = argparse.ArgumentParser(description="Run the Python tests in parallel worker processes.")
    parser.add_argument("-j", "--jobs", type=int, default=default_jobs(),
                        help=f"parts to run at once (default: the CPU count, capped at {MAX_JOBS})")
    parser.add_argument("-s", "--start-directory", type=Path, default=ROOT / "tests",
                        help="the folder that holds the test modules (default: tests/)")
    parser.add_argument("modules", nargs="*", help="test modules to run, such as test_store (default: all)")
    args = parser.parse_args(argv)
    began = time.monotonic()
    compileall.compile_dir(ROOT / "lib", maxlevels=0, quiet=2)
    start = args.start_directory.resolve()
    work = parts(start, [path.stem for path in module_paths(start, args.modules)])
    modules = {}
    for name, _ in work:
        modules.setdefault(name, Module(0)).left += 1
    jobs = max(1, min(args.jobs, len(work)))
    print(f"Running {len(work)} part{plural(len(work))} of {len(modules)} test module{plural(len(modules))} in "
          f"{jobs} worker process{plural(jobs, 'es')}.", flush=True)

    total, counts = 0, dict.fromkeys(COUNTS, 0)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        runs = {pool.submit(run_part, start, tests): name for name, tests in work}
        for run in as_completed(runs):
            name, module = runs[run], modules[runs[run]]
            output, status, seconds = run.result()
            ran, part_counts = tally(output, status)
            module.add(ran, part_counts, seconds)
            if failed(part_counts):
                print(f"\n{name} failed: {ran} test{plural(ran)} in {seconds:.1f}s, exit status {status}", flush=True)
                print(output.rstrip("\n") + "\n", flush=True)
            if not module.left:
                total += module.ran
                for key in COUNTS:
                    counts[key] += module.counts[key]
                print(f"{name}: {module.ran} test{plural(module.ran)} in {module.seconds:.1f}s, "
                      f"{summary(module.counts)}", flush=True)

    wall = time.monotonic() - began
    failed_modules = sorted(name for name, module in modules.items() if failed(module.counts))
    print("\n" + "-" * 70)
    if failed_modules:
        print(f"Failed modules: {', '.join(failed_modules)}")
    print(f"Ran {total} test{plural(total)} in {wall:.3f}s\n")
    print(summary(counts), flush=True)
    return 1 if failed_modules else 0


if __name__ == "__main__":
    sys.exit(run_tests_main(sys.argv[1:]))
