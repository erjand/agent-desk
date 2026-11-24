"""Tests for agent-desk/lib/landing.py, which restarts the desk's services and runs the sync once the merge queue moved
main in the main checkout. Each landing runs git against a temporary main checkout, with lib/merges.py's git helpers."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import landing  # noqa: E402
import merges  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "lib"
NOW = 1_800_000_000


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True).stdout.strip()


class LandingCase(unittest.TestCase):
    """A main checkout with a file on main."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "gitconfig").write_text("[user]\n\tname = Tester\n\temail = t@example.com\n"
                                             "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"),
                                               "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.main = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.main)], check=True)
        self.commit(self.main, {"shared.txt": "one\ntwo\nthree\n"}, "start")
        self.said = []

    def commit(self, where, files, message):
        for name, text in files.items():
            (Path(where) / name).write_text(text)
            git(where, "add", name)
        git(where, "commit", "-q", "-m", message)
        return git(where, "rev-parse", "HEAD")

    def head(self, branch):
        return git(self.main, "rev-parse", f"refs/heads/{branch}")

    def land(self, landed, **options):
        """Runs after_landing on the main checkout for `landed`, as lib/merges.py's main does once a tick moved main."""
        landing.after_landing(self.main, landed, self.said.append, merges.names, merges.worktree_of,
                              merges.lock_path(self.main), **options)


class AfterLandingTest(LandingCase):
    def test_a_landing_on_the_main_checkout_runs_the_sync_unless_a_program_runs_there(self):
        (self.main / ".agent-desk.json").write_text('{"sync": {"run": "touch synced", "skipWhile": ["nothing-here"]}}')
        landed = [("main", "a", "b")]
        self.land(landed, desk_root=self.root)
        self.assertTrue((self.main / "synced").exists())
        (self.main / "synced").unlink()
        with mock.patch.object(landing, "running_in", return_value="godot"):
            self.land(landed, desk_root=self.root)
        self.assertFalse((self.main / "synced").exists())
        self.assertIn(f"Skipped the sync: godot is running in {self.main.resolve()}.", self.said)

    def test_a_landing_on_another_branch_runs_nothing(self):
        (self.main / ".agent-desk.json").write_text('{"sync": {"run": "touch synced"}}')
        git(self.main, "branch", "local-desk")
        self.land([("local-desk", "a", "b")], desk_root=self.root)
        self.assertFalse((self.main / "synced").exists())

    def test_a_landing_that_changes_the_dispatcher_restarts_it_in_the_desks_own_checkout(self):
        before = self.head("main")
        (self.main / "lib").mkdir()
        after = self.commit(self.main, {"lib/dispatch.py": "x\n"}, "change the dispatcher")
        calls = []

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        self.land([("main", before, after)], desk_root=self.main, run=run)
        self.assertIn(["systemctl", "--user", "restart", landing.DISPATCH_UNIT], calls)
        self.assertNotIn(["systemctl", "--user", "restart", landing.WEB_UNIT], calls)
        calls.clear()
        self.land([("main", before, after)], desk_root=self.root, run=run)
        self.assertEqual(calls, [])

    def test_a_landing_that_changes_the_web_pages_code_restarts_its_server_when_it_runs(self):
        before = self.head("main")
        (self.main / "inbox" / "hooks").mkdir(parents=True)
        after = self.commit(self.main, {"inbox/hooks/board.ts": "x\n"}, "change the board")
        calls, active = [], {"code": 0}

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, active["code"] if "is-active" in args else 0)

        self.land([("main", before, after)], desk_root=self.main, run=run)
        self.assertIn(["systemctl", "--user", "restart", landing.WEB_UNIT], calls)
        self.assertNotIn(["systemctl", "--user", "restart", landing.DISPATCH_UNIT], calls)
        self.assertIn(f"Restarted {landing.WEB_UNIT}.", self.said)
        calls.clear()
        active["code"] = 3
        self.land([("main", before, after)], desk_root=self.main, run=run)
        self.assertEqual([args for args in calls if args[0] == "systemctl"],
                         [["systemctl", "--user", "--quiet", "is-active", landing.WEB_UNIT]])

    def landing_in_lib(self, files, mtime=None):
        """
        Lands a commit of `files` under lib/ on main, in agent-desk's own checkout, with systemctl faked. `mtime` sets
        the files' modification time, since compileall takes bytecode recorded in the same second as current.
        """
        before = self.head("main")
        (self.main / "lib").mkdir(exist_ok=True)
        after = self.commit(self.main, {f"lib/{name}": text for name, text in files.items()}, "change lib")
        for name in files if mtime else ():
            os.utime(self.main / "lib" / name, (mtime, mtime))

        def run(args, **options):
            if args[0] == "systemctl":
                return subprocess.CompletedProcess(args, 3)
            return subprocess.run(args, **options)

        self.land([("main", before, after)], desk_root=self.main, run=run)

    def cached(self, name):
        return Path(importlib.util.cache_from_source(str(self.main / "lib" / name)))

    def test_a_landing_in_the_desks_own_checkout_writes_current_bytecode_for_each_lib_module(self):
        (self.main / ".agent-desk.json").write_text('{"sync": {"run": "touch synced"}}')
        self.landing_in_lib({"dispatch.py": "import store\n", "store.py": "X = 1\n"})
        for name in ("dispatch.py", "store.py"):
            self.assertTrue(self.cached(name).is_file(), name)
        stale = self.cached("store.py").read_bytes()
        self.landing_in_lib({"store.py": "X = 2\n"}, mtime=NOW)
        self.assertNotEqual(self.cached("store.py").read_bytes(), stale)
        self.assertTrue((self.main / "synced").exists())
        self.assertEqual([line for line in self.said if "compile" in line], [])

    def test_a_failed_compile_says_one_line_and_the_sync_still_runs(self):
        (self.main / ".agent-desk.json").write_text('{"sync": {"run": "touch synced"}}')
        self.landing_in_lib({"dispatch.py": "X = 1\n", "broken.py": "def (\n"})
        failed = [line for line in self.said if line.startswith("Couldn't compile")]
        self.assertEqual(len(failed), 1, self.said)
        self.assertIn("broken.py", failed[0])
        self.assertTrue(self.cached("dispatch.py").is_file())
        self.assertTrue((self.main / "synced").exists())

    def test_a_compile_that_cannot_start_says_why_and_the_sync_still_runs(self):
        (self.main / ".agent-desk.json").write_text('{"sync": {"run": "touch synced"}}')
        before = self.head("main")
        after = self.commit(self.main, {"shared.txt": "four\n"}, "change a file")

        def run(args, **options):
            if args[0] == sys.executable:
                raise subprocess.TimeoutExpired(args, landing.COMPILE_WAIT)
            return subprocess.run(args, **options)

        self.land([("main", before, after)], desk_root=self.main, run=run)
        self.assertEqual(len([line for line in self.said if line.startswith("Couldn't compile")]), 1, self.said)
        self.assertTrue((self.main / "synced").exists())

    def test_the_web_pages_code_is_everything_under_web_and_the_pane_hooks(self):
        self.assertTrue(landing.runs_from({"README.md", "web/server.ts"}, landing.WEB_CODE))
        self.assertTrue(landing.runs_from({"inbox/hooks/board.ts"}, landing.WEB_CODE))
        self.assertTrue(landing.runs_from({"web/vendor/preact-htm.js"}, landing.WEB_CODE))
        self.assertFalse(landing.runs_from({"inbox/skills/work-issue/SKILL.md", "lib/web.sh", "webby/x"},
                                           landing.WEB_CODE))
        code = landing.dispatcher_code(LIB.parent)
        self.assertTrue(landing.runs_from({"lib/store.py"}, code))
        self.assertFalse(landing.runs_from({"lib/store.pyc", "lib/storage.py"}, code))

    def test_the_dispatchers_code_is_dispatch_py_and_every_lib_module_it_imports_directly_or_through_another(self):
        lib = self.root / "walk" / "lib"
        lib.mkdir(parents=True)
        files = {
            "dispatch.py": "import os\nimport hold, store  # noqa\nfrom run_units import x\n",
            "hold.py": "from . import nothing\n",
            "store.py": "import json\n\n\ndef later():\n    import review_checks\n",
            "review_checks.py": "from checks.part import y\n",
            "checks.py": "import store\n",
            "run_units.py": "this is not python(\n",
            "web_only.py": "import dispatch\n",
        }
        for name, text in files.items():
            (lib / name).write_text(text)
        self.assertEqual(landing.dispatcher_code(lib.parent), tuple(sorted(
            f"lib/{name}.py" for name in ("dispatch", "hold", "store", "review_checks", "checks", "run_units"))))
        self.assertEqual(landing.dispatcher_code(self.root / "empty"), ("lib/dispatch.py",))

    def test_a_change_to_any_lib_module_the_dispatcher_loads_restarts_it(self):
        with tempfile.TemporaryDirectory() as state:
            loaded = subprocess.run(
                [sys.executable, "-I", "-c", "import os, sys\nsys.path.insert(0, sys.argv[1])\nimport dispatch\n"
                 "print('\\n'.join(sorted(name for name, module in list(sys.modules.items())\n"
                 "    if os.path.dirname(os.path.abspath(getattr(module, '__file__', None) or '')) == sys.argv[1])))",
                 str(LIB)], capture_output=True, text=True, env={**os.environ, "XDG_STATE_HOME": state}, check=True)
        modules = loaded.stdout.split()
        self.assertIn("dispatch", modules)
        self.assertIn("checks", modules)
        code = landing.dispatcher_code(LIB.parent)
        for name in modules:
            self.assertTrue(landing.runs_from({f"lib/{name}.py"}, code), f"a change to lib/{name}.py restarts nothing")

    def test_a_landing_that_changes_a_module_the_dispatcher_imports_restarts_it(self):
        (self.main / "lib").mkdir()
        self.commit(self.main, {"lib/dispatch.py": "import run_units\n", "lib/run_units.py": "import branch_watch\n"},
                    "add the dispatcher")
        calls = []

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        for name, restarts in (("lib/branch_watch.py", True), ("lib/web_only.py", False), ("lib/run_units.py", True)):
            calls.clear()
            before = self.head("main")
            after = self.commit(self.main, {name: "x\n"}, f"change {name}")
            self.land([("main", before, after)], desk_root=self.main, run=run)
            self.assertEqual(["systemctl", "--user", "restart", landing.DISPATCH_UNIT] in calls, restarts, name)


if __name__ == "__main__":
    unittest.main()
