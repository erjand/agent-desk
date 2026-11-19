"""Tests for agent-desk/lib/desk_path.py, which install runs to check the runs will find agent-desk."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import desk_path  # noqa: E402


class DeskPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.checkout = self.make_checkout("desk")
        self.bin = self.root / "home" / ".local" / "bin"
        self.bin.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def make_checkout(self, name):
        command = self.root / name / "bin" / "agent-desk"
        command.parent.mkdir(parents=True)
        command.write_text("#!/bin/sh\n")
        command.chmod(0o755)
        return self.root / name

    def link(self, checkout):
        (self.bin / "agent-desk").symlink_to(checkout / "bin" / "agent-desk")

    def warn(self, path=None):
        return desk_path.warnings(str(self.checkout), path or f"{self.bin}:/usr/bin")

    def test_a_linked_command_passes(self):
        self.link(self.checkout)
        self.assertEqual(self.warn(), [])

    def test_a_missing_command_says_how_to_link_it(self):
        lines = self.warn()
        self.assertEqual(len(lines), 1)
        self.assertIn("isn't on PATH", lines[0])
        self.assertIn(str(self.checkout / "bin" / "agent-desk"), lines[0])

    def test_a_command_from_another_checkout_names_both(self):
        self.link(self.make_checkout("other"))
        lines = self.warn()
        self.assertEqual(len(lines), 1)
        self.assertIn(str(self.root / "other"), lines[0])
        self.assertIn(str(self.checkout), lines[0])

    def test_main_prints_nothing_and_exits_0_when_all_is_well(self):
        self.link(self.checkout)
        saved = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.bin)
        try:
            self.assertEqual(desk_path.main([str(self.checkout)]), 0)
        finally:
            os.environ["PATH"] = saved


if __name__ == "__main__":
    unittest.main()
