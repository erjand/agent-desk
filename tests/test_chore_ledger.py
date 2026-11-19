"""Tests for agent-desk/lib/chore_ledger.py, which tells each chore run what the runs before it did."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import chore_ledger  # noqa: E402
import store  # noqa: E402

REPO = "o/r"

NOW = 1_800_000_000
DAY = chore_ledger.DAY
PREFIX = "agent/chore-refactor-"


def pr(number, state, files, days_ago=1, head=PREFIX + "20261028-0900"):
    when = datetime.fromtimestamp(NOW - days_ago * DAY, timezone.utc).isoformat()
    return {"number": number, "title": f"Change {number}", "state": state, "headRefName": head,
            "mergedAt": when if state == "MERGED" else None, "closedAt": when if state != "OPEN" else None,
            "files": [{"path": path} for path in files]}


class ReportTest(unittest.TestCase):
    def test_the_report_comes_from_the_last_chore_report_block_of_the_final_message(self):
        final = 'Opened #5.\n\n```chore-report\n{"passed": [{"path": "a.py", "why": "fine"}], "leads": []}\n```'
        stream = "\n".join(json.dumps(event) for event in [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "```chore-report\n{}\n```"}]}},
            {"type": "result", "result": final},
        ])
        self.assertEqual(chore_ledger.report_of(stream), {"passed": [{"path": "a.py", "why": "fine"}], "leads": []})
        self.assertIsNone(chore_ledger.report_of('{"type": "result", "result": "no report"}'))
        self.assertIsNone(chore_ledger.report_of('{"type": "result", "result": "```chore-report\\nnot json\\n```"}'))

    def test_a_report_keeps_only_well_formed_one_line_entries(self):
        passed, leads, settled = chore_ledger.clean({
            "passed": [{"path": "a.py", "why": "one\n  two"}, {"why": "no path"}],
            "leads": [{"note": "do b", "paths": ["b.py"]}, "stray"],
            "settled": ["fix\n  c.py", "", "  ", {"note": "not a string"}, "x" * 300] + ["more"] * 30})
        self.assertEqual(passed, [{"path": "a.py", "why": "one two"}])
        self.assertEqual(leads, [{"note": "do b", "paths": ["b.py"]}])
        self.assertEqual(settled[:2], ["fix c.py", "x" * 200])
        self.assertEqual(len(settled), 20)

    def test_a_report_without_settled_leads_settles_nothing(self):
        self.assertEqual(chore_ledger.clean({"passed": [], "leads": []})[2], [])
        self.assertEqual(chore_ledger.clean(None)[2], [])

    def test_the_ledger_records_the_leads_a_run_settled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chore.jsonl"
            chore_ledger.record(path, "c1", {"settled": ["fix  b.py"]}, 7, NOW)
            self.assertEqual(chore_ledger.entries(path)[0]["settled"], ["fix b.py"])


class LedgerTest(unittest.TestCase):
    def test_the_ledger_drops_runs_older_than_its_lifespan_and_keeps_its_newest_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chore.jsonl"
            path.write_text(json.dumps({"at": NOW - (chore_ledger.KEEP_DAYS + 1) * DAY, "passed": []}) + "\n")
            for index in range(chore_ledger.KEEP_RUNS + 5):
                chore_ledger.record(path, f"c{index}", None, None, NOW + index)
            runs = chore_ledger.entries(path)
            self.assertEqual(len(runs), chore_ledger.KEEP_RUNS)
            self.assertEqual(runs[-1]["commit"], f"c{chore_ledger.KEEP_RUNS + 4}")


class BriefTest(unittest.TestCase):
    def test_the_brief_lists_open_merged_and_rejected_prs_of_this_chore_only(self):
        prs = [pr(1, "OPEN", ["open.py"]), pr(2, "MERGED", ["merged.py"]), pr(3, "MERGED", ["old.py"], days_ago=40),
               pr(4, "CLOSED", ["rejected.py"]), pr(5, "OPEN", ["other.py"], head="agent/issue-9")]
        text = chore_ledger.brief(prs, [], lambda commit: set(), PREFIX, NOW)
        self.assertIn("- #1 Change 1: open.py", text)
        self.assertIn("- merged.py", text)
        self.assertNotIn("old.py", text)
        self.assertIn("- #4 Change 4: rejected.py", text)
        self.assertNotIn("other.py", text)
        self.assertTrue(text.endswith(chore_ledger.REPORT))

    def test_files_passed_over_stay_skipped_until_they_change_and_leads_go_once_their_files_change(self):
        runs = [{"at": NOW - 2 * DAY, "commit": "abc",
                 "passed": [{"path": "still.py", "why": "fine"}, {"path": "edited.py", "why": "fine"}],
                 "leads": [{"note": "fix still.py", "paths": ["still.py"]},
                           {"note": "fix edited.py", "paths": ["edited.py"]}]},
                {"at": NOW - 70 * DAY, "commit": "old", "passed": [{"path": "stale.py", "why": ""}], "leads": []}]
        text = chore_ledger.brief([], runs, lambda commit: {"edited.py"} if commit == "abc" else set(), PREFIX, NOW)
        self.assertIn("- still.py: fine", text)
        self.assertIn("- fix still.py", text)
        self.assertNotIn("edited.py", text)
        self.assertNotIn("stale.py", text)

    def test_a_lead_leaves_the_brief_once_a_newer_run_settles_it(self):
        runs = [{"at": NOW - 5 * DAY, "commit": None, "passed": [], "settled": ["fix b.py"],
                 "leads": [{"note": "fix a.py", "paths": ["a.py"]}, {"note": "fix c.py", "paths": []}]},
                {"at": NOW - 3 * DAY, "commit": None, "passed": [],
                 "leads": [{"note": "fix b.py", "paths": ["b.py"]}]},
                {"at": NOW - DAY, "commit": None, "passed": [], "leads": [], "settled": ["fix  a.py", "fix d.py"]}]
        text = chore_ledger.brief([], runs, lambda commit: set(), PREFIX, NOW)
        self.assertNotIn("- fix a.py", text)
        self.assertIn("- fix b.py", text)
        self.assertIn("- fix c.py", text)

    def test_leads_of_ledger_lines_without_settled_stay_in_the_brief(self):
        runs = [{"at": NOW - 2 * DAY, "commit": None, "passed": [], "leads": [{"note": "fix a.py", "paths": []}]},
                {"at": NOW - DAY, "commit": None, "passed": [], "leads": []}]
        text = chore_ledger.brief([], runs, lambda commit: set(), PREFIX, NOW)
        self.assertIn("- fix a.py", text)

    def test_the_brief_stays_within_its_size_however_long_the_history(self):
        runs = [{"at": NOW - DAY, "commit": None, "passed": [{"path": f"dir/file_{run}_{index}.py", "why": "x" * 80}
                 for index in range(100)], "leads": [{"note": f"lead {run}", "paths": []}]} for run in range(50)]
        text = chore_ledger.brief([], runs, lambda commit: set(), PREFIX, NOW)
        self.assertLessEqual(len(text), chore_ledger.MAX_BRIEF + len(chore_ledger.REPORT) + 100)
        self.assertIn("and 40 more", text)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class StoreReviewsTest(unittest.TestCase):
    """The brief's reviews from a temporary store, with their files from a temporary main checkout."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.main = self.root / "desk"
        self.main.mkdir()
        git("init", "-q", "-b", "main", cwd=self.main)
        for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
            git("config", key, value, cwd=self.main)
        self.commit("base.py", "Base")
        self.desk = store.Store(self.root / "state" / "desk.sqlite")
        self.addCleanup(self.desk.close)

    def commit(self, path, message):
        (self.main / path).write_text(f"{message}\n")
        git("add", path, cwd=self.main)
        git("commit", "-q", "-m", message, cwd=self.main)

    def branch(self, name, path):
        """A branch off main that adds `path`, left checked out on main again, and its head."""
        git("checkout", "-q", "-b", name, "main", cwd=self.main)
        self.commit(path, name)
        git("checkout", "-q", "main", cwd=self.main)
        return git("rev-parse", name, cwd=self.main)

    def review(self, branch, path, head_sha=None):
        self.branch(branch, path)
        return self.desk.create(REPO, "review", f"Change {path}", branch=branch, head_sha=head_sha)

    def seed(self):
        """An open, a merged, and a closed review of the chore, one whose branch is gone, and another branch's."""
        self.review(PREFIX + "1", "open.py")
        merged = self.review(PREFIX + "2", "merged.py")
        self.commit("later.py", "Main moved on")
        git("merge", "-q", "--no-ff", "-m", "Merge", PREFIX + "2", cwd=self.main)
        self.desk.merge(REPO, merged, git("rev-parse", "HEAD", cwd=self.main))
        closed = self.review(PREFIX + "3", "rejected.py")
        self.desk.set_state(REPO, closed, "closed")
        head = self.branch(PREFIX + "4", "gone.py")
        gone = self.desk.create(REPO, "review", "Change gone.py", branch=PREFIX + "4", head_sha=head)
        git("branch", "-q", "-D", PREFIX + "4", cwd=self.main)
        self.desk.set_state(REPO, gone, "closed")
        self.review("agent/issue-9", "other.py")

    def test_each_review_of_the_chore_carries_its_state_and_the_files_git_says_it_changed(self):
        self.seed()
        found = chore_ledger.reviews(self.desk, REPO, PREFIX, chore_ledger.git_files(str(self.main)))
        shown = {review["headRefName"]: (review["state"], [one["path"] for one in review["files"]])
                 for review in found}
        self.assertEqual(shown, {PREFIX + "1": ("OPEN", ["open.py"]), PREFIX + "2": ("MERGED", ["merged.py"]),
                                 PREFIX + "3": ("CLOSED", ["rejected.py"]), PREFIX + "4": ("CLOSED", ["gone.py"])})
        merged = next(review for review in found if review["state"] == "MERGED")
        self.assertTrue(merged["mergedAt"])
        self.assertEqual(merged["mergedAt"], merged["closedAt"])

    def test_a_prefix_matches_its_characters_literally(self):
        self.review("agent/chore_x-1", "underscore.py")
        self.review("agent/chorex-1", "letter.py")
        found = self.desk.items(REPO, "review", state="all", head_prefix="agent/chore_x-")
        self.assertEqual([review["headRefName"] for review in found], ["agent/chore_x-1"])

    def test_a_review_whose_commits_git_lacks_changed_nothing(self):
        number = self.desk.create(REPO, "review", "Lost", branch=PREFIX + "9", head_sha="0" * 40)
        files = chore_ledger.git_files(str(self.main))
        self.assertEqual(files(self.desk.item(REPO, number)), [])

    def test_brief_reads_the_store_and_git_without_gh(self):
        self.seed()
        ran = []
        real = subprocess.run

        def spy(args, *rest, **kwargs):
            ran.append(args[0])
            return real(args, *rest, **kwargs)

        printed = []
        self.desk.close()
        with mock.patch.object(store, "DATABASE", self.root / "state" / "desk.sqlite"), \
                mock.patch.object(chore_ledger, "STATE", self.root / "chores"), \
                mock.patch.object(chore_ledger.subprocess, "run", side_effect=spy), \
                mock.patch("builtins.print", side_effect=printed.append):
            status = chore_ledger.main(["chore_ledger.py", "brief", REPO, "refactor", str(self.main)])
        self.assertEqual(status, 0)
        self.assertNotIn("gh", ran)
        text = printed[0]
        self.assertIn("- #1 Change open.py: open.py", text)
        self.assertIn("- merged.py", text)
        self.assertIn("- #3 Change rejected.py: rejected.py", text)
        self.assertIn("- #4 Change gone.py: gone.py", text)
        self.assertNotIn("other.py", text)


if __name__ == "__main__":
    unittest.main()
