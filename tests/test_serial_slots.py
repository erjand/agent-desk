"""Tests for agent-desk/lib/serial_slots.py, which keeps one serial slot for each serial group."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

import serial_slots  # noqa: E402


def issue(number, *labels, status="open"):
    """An issue as `agent-desk issue list --json number,labels,status` prints it."""
    return {"number": number, "labels": [{"name": name} for name in labels], "status": status}


class SerialSlotsTest(unittest.TestCase):
    def test_serial_and_serial_group_labels_name_groups_and_other_labels_do_not(self):
        self.assertEqual(serial_slots.groups(["serial", "serial:audio", "bug", "serial:", "serialize"]),
                         {"serial", "serial:audio"})

    def test_an_issue_waits_only_on_an_in_progress_issue_in_one_of_its_groups(self):
        open_issues = [issue(435, "serial:audio", status="in-progress"), issue(440, "serial:audio", status="queued"),
                       issue(500, "serial", status="in-progress"),
                       issue(380, "serial:terrain", "serial:town", status="in-progress")]
        self.assertEqual(serial_slots.holder(564, ["serial:terrain"], open_issues), 380)
        self.assertEqual(serial_slots.holder(564, ["serial:water"], open_issues), None)
        self.assertEqual(serial_slots.holder(441, ["serial:audio", "serial"], open_issues), 435)
        self.assertEqual(serial_slots.holder(435, ["serial:audio"], open_issues), None)

    def test_release_starts_the_oldest_waiting_issue_of_each_free_group_and_every_unserial_one(self):
        open_issues = [issue(435, "serial:audio", status="in-progress"), issue(470, "serial:water", status="queued")]
        waiting = [issue(600, "serial:terrain"), issue(564, "serial:terrain"), issue(590, "serial:audio"),
                   issue(595, "serial:water"), issue(598), issue(599, "serial:town", "serial:terrain"),
                   issue(601, "serial:town")]
        self.assertEqual(serial_slots.release(waiting, open_issues), [564, 598, 601])

    def test_release_starts_each_free_groups_first_issue_in_the_start_order(self):
        def placed(number, position, *labels):
            return {**issue(number, *labels), "position": position}
        waiting = [placed(564, 2, "serial:terrain"), placed(600, 1, "serial:terrain"), issue(598),
                   issue(590, "serial:terrain"), placed(601, 3)]
        self.assertEqual(serial_slots.release(waiting, []), [600, 601, 598])

    def test_plain_serial_issues_still_work_one_at_a_time(self):
        self.assertEqual(serial_slots.release([issue(12, "serial"), issue(11, "serial")], []), [11])
        self.assertEqual(serial_slots.release([issue(12, "serial")], [issue(9, "serial", status="in-progress")]), [])

    def test_in_manual_an_unpicked_queued_issue_leaves_its_slot_to_the_oldest_picked_waiting_one(self):
        # #506 was released to queued and waits for Start; #519 and #619 are picked.
        open_issues = [issue(506, "serial:atlas", status="queued"), issue(435, "serial:audio", status="in-progress")]
        waiting = [issue(518, "serial:atlas"), issue(619, "serial:atlas"), issue(519, "serial:atlas"),
                   issue(590, "serial:audio"), issue(598)]
        self.assertEqual(serial_slots.release(waiting, open_issues, {519, 590, 619}), [519, 598])

    def test_in_manual_with_nothing_picked_only_unserial_issues_start(self):
        waiting = [issue(518, "serial:atlas"), issue(598)]
        self.assertEqual(serial_slots.release(waiting, [issue(506, "serial:atlas", status="queued")], set()), [598])

    def test_in_manual_a_picked_queued_issue_holds_its_slot(self):
        open_issues = [issue(506, "serial:atlas", status="queued")]
        self.assertEqual(serial_slots.release([issue(619, "serial:atlas")], open_issues, {506, 619}), [])

    def test_in_auto_every_queued_issue_holds_its_slot(self):
        open_issues = [issue(506, "serial:atlas", status="queued")]
        self.assertEqual(serial_slots.release([issue(518, "serial:atlas"), issue(598)], open_issues), [598])

    def test_a_label_named_after_a_github_state_holds_no_slot(self):
        open_issues = [issue(435, "serial:audio", "in-progress"), issue(440, "serial:audio", "agent-ready")]
        self.assertEqual(serial_slots.holder(564, ["serial:audio"], open_issues), None)
        self.assertEqual(serial_slots.release([issue(564, "serial:audio")], open_issues), [564])

    def test_the_command_reads_store_lists_printed_one_after_another(self):
        stdin = (json.dumps([issue(435, "serial:audio", status="in-progress")]) + "\n"
                 + json.dumps([issue(470, status="queued")]))
        self.assertEqual(len(serial_slots.read_issues(stdin)), 2)
        holder = subprocess.run([sys.executable, str(LIB / "serial_slots.py"), "holder", "564", "serial:audio,bug"],
                                input=stdin, capture_output=True, text=True, check=True)
        self.assertEqual(holder.stdout, "435\n")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as file:
            json.dump([issue(564, "serial:terrain"), issue(590, "serial:audio")], file)
        try:
            release = subprocess.run([sys.executable, str(LIB / "serial_slots.py"), "release", file.name],
                                     input=stdin, capture_output=True, text=True, check=True)
            manual = subprocess.run([sys.executable, str(LIB / "serial_slots.py"), "release", file.name, "590,12"],
                                    input=stdin, capture_output=True, text=True, check=True)
            nothing_picked = subprocess.run([sys.executable, str(LIB / "serial_slots.py"), "release", file.name, ""],
                                            input=stdin, capture_output=True, text=True, check=True)
        finally:
            Path(file.name).unlink()
        self.assertEqual(release.stdout, "564\n")
        self.assertEqual(manual.stdout, "")
        self.assertEqual(nothing_picked.stdout, "")


if __name__ == "__main__":
    unittest.main()
