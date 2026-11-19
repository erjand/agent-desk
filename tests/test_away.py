"""Tests for desk_away in lib/common.sh and the warning agent-desk chores status prints on a machine without a desktop,
run against stand-ins for loginctl, busctl, and gh."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"


class AwayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.checkout = root / "checkout"
        (self.checkout / ".git").mkdir(parents=True)
        fakes = root / "fakes"
        fakes.mkdir()
        # loginctl lists FAKE_SESSION_TYPE's session for this user when it is set, and answers its properties.
        loginctl = fakes / "loginctl"
        loginctl.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            f'  list-sessions) [ -n "$FAKE_SESSION_TYPE" ] && echo "4 {os.getuid()} me seat0";;\n'
            '  show-session) case "$4" in\n'
            "    Class) echo user;;\n"
            '    Type) echo "$FAKE_SESSION_TYPE";;\n'
            '    LockedHint) echo "${FAKE_LOCKED:-no}";;\n'
            "  esac;;\n"
            "esac\n"
        )
        busctl = fakes / "busctl"
        busctl.write_text('#!/bin/sh\necho "b ${FAKE_SAVER:-false}"\n')
        gh = fakes / "gh"
        gh.write_text("#!/bin/sh\n")
        for fake in (loginctl, busctl, gh):
            fake.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{fakes}:{os.environ['PATH']}", "AGENT_DESK_CHECKOUT": str(self.checkout),
                    "GH_REPO": "o/r", "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache"),
                    "CLAUDE_CONFIG_DIR": str(root / "claude"), "CLAUDE_CODE_PLUGIN_DIRS": "",
                    "FAKE_SESSION_TYPE": "", "FAKE_LOCKED": "", "FAKE_SAVER": ""}

    def tearDown(self):
        self.tmp.cleanup()

    def configure(self, **config):
        (self.checkout / ".agent-desk.json").write_text(json.dumps(config))

    def away(self, **fake):
        result = subprocess.run(["bash", "-c", f'source "{LIB}/common.sh" && desk_away'],
                                env={**self.env, **fake}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_an_unlocked_desktop_counts_as_there(self):
        self.configure()
        self.assertEqual(self.away(FAKE_SESSION_TYPE="wayland"), "")
        self.assertEqual(self.away(FAKE_SESSION_TYPE="x11"), "")

    def test_a_locked_screen_counts_as_away(self):
        self.configure()
        self.assertEqual(self.away(FAKE_SESSION_TYPE="wayland", FAKE_LOCKED="yes"), "the screen is locked")
        self.assertEqual(self.away(FAKE_SESSION_TYPE="x11", FAKE_SAVER="true"), "the screen is locked")

    def test_desktop_presence_without_a_desktop_counts_as_away(self):
        self.configure()
        expected = 'no desktop session is open, and the config\'s presence is "desktop"'
        self.assertEqual(self.away(), expected)
        self.assertEqual(self.away(FAKE_SESSION_TYPE="tty"), expected)
        self.configure(presence="desktop")
        self.assertEqual(self.away(), expected)

    def test_always_presence_counts_as_there_without_a_desktop_or_when_locked(self):
        self.configure(presence="always")
        self.assertEqual(self.away(), "")
        self.assertEqual(self.away(FAKE_SESSION_TYPE="wayland", FAKE_LOCKED="yes"), "")

    def status(self, **config):
        # A paused chore keeps chores status from asking GitHub about its PRs.
        self.configure(runners={"agent": "a"}, chores={"every": {"tidy": 3}, "paused": ["tidy"]}, **config)
        result = subprocess.run(["bash", str(LIB / "chores.sh"), "status"], env=self.env, capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("paused", result.stdout)
        return "no desktop session" in result.stderr

    def test_chores_status_warns_when_desktop_presence_has_no_desktop_to_wait_on(self):
        self.assertTrue(self.status())
        self.assertTrue(self.status(presence="desktop"))
        self.assertFalse(self.status(presence="always"))
        self.env["FAKE_SESSION_TYPE"] = "wayland"
        self.assertFalse(self.status())


if __name__ == "__main__":
    unittest.main()
