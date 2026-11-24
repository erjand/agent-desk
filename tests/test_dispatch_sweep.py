"""Tests for the sweep lib/dispatch_sweep.py starts from the dispatcher's reconcile."""

import json
import unittest

from dispatch_case import REPO, DispatchCase
from dispatch_sweep import SWEEP_SECONDS
import dispatch


class SweepTest(DispatchCase):
    def reconcile_at(self, seconds):
        self.time += seconds
        self.dispatcher.tick()

    def sweeps(self):
        return [args for args in self.systemd.started if args[args.index("--") + 2:] == ["sweep"]]

    def test_a_sweep_starts_in_its_own_unit_once_an_hour(self):
        self.reconcile_at(SWEEP_SECONDS - 1)
        self.assertEqual(self.sweeps(), [])

        self.reconcile_at(dispatch.RECONCILE_SECONDS)
        self.assertEqual(len(self.sweeps()), 1)
        self.assertEqual(self.systemd.units(), ["agent-desk-sweep-org-game.service"])
        self.assertEqual(self.systemd.env()["GH_REPO"], REPO)
        args = self.sweeps()[0]
        self.assertEqual(args[args.index("--working-directory") + 1], str(self.checkout))
        self.assertIn("RuntimeMaxSec=15min", self.systemd.properties())
        self.assertIn(f"Started the sweep of {REPO} in agent-desk-sweep-org-game.service.", self.said)

        self.reconcile_at(SWEEP_SECONDS - 1)
        self.assertEqual(len(self.sweeps()), 1)
        self.reconcile_at(dispatch.RECONCILE_SECONDS)
        self.assertEqual(len(self.sweeps()), 2)

    def test_a_restart_starts_no_sweep_before_the_hour(self):
        self.reconcile_at(SWEEP_SECONDS)
        self.assertEqual(len(self.sweeps()), 1)

        self.time += 60
        self.dispatcher = self.new_dispatcher()
        self.reconcile_at(dispatch.RECONCILE_SECONDS)
        self.assertEqual(len(self.sweeps()), 1)
        self.assertEqual(json.loads((self.folder / "sweeps.json").read_text()), {REPO: self.time - 90})

    def test_a_desk_never_swept_sweeps_on_its_first_reconcile(self):
        (self.folder / "sweeps.json").unlink()
        self.reconcile_at(dispatch.RECONCILE_SECONDS)
        self.assertEqual(len(self.sweeps()), 1)

    def test_a_sweep_that_fails_to_start_says_so_and_waits_the_hour(self):
        self.systemd.fail_start = True
        self.reconcile_at(SWEEP_SECONDS)
        self.assertIn(f"The sweep of {REPO} failed to start: Unit already exists.", self.said)

        self.systemd.fail_start = False
        self.reconcile_at(dispatch.RECONCILE_SECONDS)
        self.assertEqual(self.sweeps(), [])
        self.reconcile_at(SWEEP_SECONDS)
        self.assertEqual(len(self.sweeps()), 1)

    def test_a_project_without_a_checkout_here_is_not_swept(self):
        (self.folder / "projects").write_text(f"{REPO} {self.folder / 'gone'}\n")
        self.reconcile_at(SWEEP_SECONDS)
        self.assertEqual(self.sweeps(), [])


if __name__ == "__main__":
    unittest.main()
