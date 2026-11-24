"""Tests for lib/run_stop.py through lib/agent_run.py: a stand-in for claude waits on a Bash call whose command runs in a
session of its own with child processes, as Claude Code runs it, while a background task it started earlier runs, and
`agent-desk stop-command` must end the call's processes alone and leave the run going."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

import agent_log  # noqa: E402
import run_stop  # noqa: E402

SESSION = "5f1c2a9e-0000-4000-8000-000000000613"
RUN = "7"
COMMAND = "sleep 300 & sleep 301 & wait"


def alive(pid):
    """Whether a process runs, counting a zombie as gone."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def event(**fields):
    return json.dumps(fields)


def tool_use(command):
    return event(type="assistant", message={"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                                         "input": {"command": command}}]})


class StopCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.fake = self.root / "claude"
        self.pids = self.root / "pids"
        self.pids.mkdir()
        self.output = self.root / "call.output"
        self.status = self.root / "call.status"
        self.addCleanup(self.end_all)
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)})
        patch.start()
        self.addCleanup(patch.stop)

    def end_all(self):
        for one in self.pids.iterdir():
            try:
                os.kill(int(one.read_text()), 9)
            except (ProcessLookupError, ValueError):
                pass

    def claude(self, body):
        self.fake.write_text(f"#!/bin/bash\necho '{event(type='system', subtype='init', session_id=SESSION)}'\n{body}\n")
        self.fake.chmod(0o755)

    def start(self, codex=False):
        """Starts agent_run.py on the stand-in for run RUN, and returns it."""
        env = {**os.environ, "AGENT_DESK_RUN": RUN, "AGENT_DESK_RESULT_GRACE": "1", "AGENT_DESK_KILL_GRACE": "1",
               "AGENT_DESK_BEAT": "300", "AGENT_DESK_TERM_GRACE": "1"}
        env.pop("AGENT_DESK_UNIT", None)
        if codex:
            env["AGENT_DESK_CODEX"] = "1"
        run = subprocess.Popen([sys.executable, "-B", str(LIB / "agent_run.py"), str(self.root / "stream"),
                                str(self.fake)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.end_run, run)
        return run

    def end_run(self, run):
        """Ends a run still going, as its unit's stop would, which reaches claude through agent_run.py."""
        if run.poll() is None:
            run.terminate()
        try:
            run.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            run.kill()
            run.communicate()

    def wait_for(self, path):
        for _ in range(200):
            if path.exists() and path.read_text().strip():
                return int(path.read_text())
            time.sleep(0.05)
        self.fail(f"{path.name} never appeared")

    def waiting_call(self, command=COMMAND, background=True):
        """
        A claude that starts a background task in a session of its own, then waits on a Bash call whose command runs in
        another, quoted in its shell's arguments as Claude Code quotes it, with its output in a file. Once the call
        ends it writes the call's status and reports the result.
        """
        quoted = "'" + command.replace("'", "'\"'\"'") + "'"
        leader = f"source /dev/null && eval {quoted} && pwd -P >| /dev/null"
        task = f'setsid sleep 302 >/dev/null 2>&1 & echo $! >"{self.pids / "task"}"\n' if background else ""
        self.claude(f"{task}"
                    f"echo {shlex.quote(tool_use(command))}\n"
                    f"setsid /bin/bash -c {shlex.quote(leader)} >\"{self.output}\" 2>&1 &\n"
                    f"call=$!\necho $call >\"{self.pids / 'leader'}\"\n"
                    f"wait $call\necho $? >\"{self.status}\"\n"
                    f"echo '{event(type='user', message={'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_1'}]})}'\n"
                    f"echo '{event(type='result', subtype='success', is_error=False)}'")

    def children(self, leader):
        found = Path(f"/proc/{leader}/task/{leader}/children")
        for _ in range(100):
            pids = [int(one) for one in found.read_text().split()] if found.exists() else []
            if len(pids) == 2:
                return pids
            time.sleep(0.05)
        self.fail("the call's command never started its children")

    def test_a_waiting_call_with_children_ends_and_the_run_and_its_background_task_go_on(self):
        self.waiting_call()
        run = self.start()
        task = self.wait_for(self.pids / "task")
        leader = self.wait_for(self.pids / "leader")
        children = self.children(leader)
        said = run_stop.ask(RUN, wait=10)
        self.assertEqual(sorted(said["ended"]), ["bash", "sleep", "sleep"], said)
        self.assertIn("stopped the command the run waited on", said["text"])
        self.assertIn("in Bash: sleep 300 & sleep 301 & wait", said["text"])
        for pid in [leader, *children]:
            self.assertFalse(alive(pid), f"{pid} still runs")
        self.assertTrue(alive(task), "the background task must survive")
        out, err = run.communicate(timeout=20)
        self.assertEqual(int(self.status.read_text()), 143, "claude -p sees the command fail on TERM")
        self.assertIn("the user stopped this command", self.output.read_text())
        self.assertIn("The user stopped the command", out, err)
        beats = [json.loads(line) for line in (self.state / "agent-desk" / "beats" / SESSION).read_text().splitlines()]
        self.assertEqual([one["type"] for one in beats], ["stop"])
        self.assertFalse(list((self.state / "agent-desk" / "stops").glob("*.ask")), "the ask is taken")

    def test_a_command_that_ignores_term_is_killed(self):
        self.waiting_call("trap '' TERM; sleep 300 & sleep 301 & wait", background=False)
        run = self.start()
        leader = self.wait_for(self.pids / "leader")
        self.children(leader)
        said = run_stop.ask(RUN, wait=10)
        self.assertIn("killed", said["text"])
        self.assertFalse(alive(leader))
        run.communicate(timeout=20)
        self.assertEqual(int(self.status.read_text()), 137)

    def test_codex_stops_the_matching_shell_with_children_and_keeps_the_cli_and_other_tasks(self):
        command = shlex.join(["/bin/bash", "-lc", COMMAND])
        start = event(type="item.started", item={"type": "command_execution", "id": "item_1", "command": command})
        complete = event(type="item.completed", item={"type": "command_execution", "id": "item_1", "exit_code": 143})
        self.claude(f"echo {shlex.quote(event(type='thread.started', thread_id=SESSION))}\n"
                    f"echo {shlex.quote(start)}\n"
                    f'setsid sleep 302 >/dev/null 2>&1 & echo $! >"{self.pids / "task"}"\n'
                    f'{shlex.join(["/bin/bash", "-c", COMMAND])} >"{self.output}" 2>&1 &\n'
                    f'call=$!\necho $call >"{self.pids / "leader"}"\n'
                    f'wait $call\necho $? >"{self.status}"\n'
                    f"echo {shlex.quote(complete)}\n"
                    f"echo {shlex.quote(event(type='turn.completed'))}")
        run = self.start(codex=True)
        task = self.wait_for(self.pids / "task")
        leader = self.wait_for(self.pids / "leader")
        children = self.children(leader)
        said = run_stop.ask(RUN, wait=10)
        self.assertEqual(sorted(said["ended"]), ["bash", "sleep", "sleep"], said)
        self.assertTrue(alive(task))
        self.assertFalse(any(alive(pid) for pid in [leader, *children]))
        out, err = run.communicate(timeout=20)
        self.assertEqual(run.returncode, 0, (out, err))
        self.assertEqual(int(self.status.read_text()), 143)

    def test_codex_stops_a_simple_command_that_replaced_its_shell(self):
        start = event(type="item.started", item={"type": "command_execution", "id": "item_1",
                                                 "command": "/bin/bash -lc 'sleep 300'"})
        self.claude(f"echo {shlex.quote(start)}\n"
                    f'sleep 300 &\ncall=$!\necho $call >"{self.pids / "leader"}"\nwait $call\n'
                    f'echo $? >"{self.status}"')
        run = self.start(codex=True)
        leader = self.wait_for(self.pids / "leader")
        self.assertEqual(run_stop.ask(RUN, wait=10)["ended"], ["sleep"])
        self.assertFalse(alive(leader))
        run.communicate(timeout=20)
        self.assertEqual(int(self.status.read_text()), 143)

    def test_codex_leaves_a_new_session_running_when_its_command_does_not_match(self):
        start = event(type="item.started", item={"type": "command_execution", "id": "item_1",
                                                 "command": "/bin/bash -lc 'sleep 300; wait'"})
        self.claude(f"echo {shlex.quote(start)}\n"
                    f'setsid sleep 302 >/dev/null 2>&1 & echo $! >"{self.pids / "task"}"\n'
                    "wait")
        run = self.start(codex=True)
        task = self.wait_for(self.pids / "task")
        said = run_stop.ask(RUN, wait=10)
        self.assertEqual(said["ended"], [])
        self.assertIn("Could not identify one matching Codex shell", said["text"])
        self.assertTrue(alive(task))
        self.assertIsNone(run.poll())

    def test_a_run_waiting_on_no_call_stops_nothing(self):
        self.claude(f'setsid sleep 303 >/dev/null 2>&1 & echo $! >"{self.pids / "task"}"\nexec sleep 300')
        run = self.start()
        task = self.wait_for(self.pids / "task")
        said = run_stop.ask(RUN, wait=10)
        self.assertEqual(said["ended"], [])
        self.assertEqual(said["text"], "The run waits on no tool call, so nothing was stopped.")
        self.assertTrue(alive(task))
        self.assertIsNone(run.poll(), "the run goes on")

    def test_a_run_that_never_answers_gives_up_and_takes_back_its_ask(self):
        with self.assertRaisesRegex(run_stop.StopError, "didn't answer within 0.3 s"):
            run_stop.ask("9", wait=0.3)
        self.assertFalse(list((self.state / "agent-desk" / "stops").glob("*.ask")))


class StopCommandArgsTest(unittest.TestCase):
    class Desk:
        def __init__(self, rows):
            self.rows = rows

        def runs(self, statuses):
            return [row for row in self.rows if row["status"] in statuses]

    def test_an_issue_no_agent_run_works_has_no_command_to_stop(self):
        desk = self.Desk([{"id": 3, "project": "o/r", "kind": "agent", "target": "8", "status": "running"},
                          {"id": 4, "project": "o/r", "kind": "chore", "target": "7", "status": "running"}])
        with self.assertRaisesRegex(run_stop.StopError, "No agent run works #7 in o/r"):
            run_stop.stop_command(desk, "o/r", 7)

    def test_the_command_asks_the_run_working_the_issue_and_fails_when_it_ended_nothing(self):
        desk = self.Desk([{"id": 3, "project": "o/r", "kind": "agent", "target": "7", "status": "running"}])
        with mock.patch.object(run_stop, "ask", return_value={"ended": [], "text": "nothing"}) as asked:
            with self.assertRaisesRegex(run_stop.StopError, "nothing"):
                run_stop.stop_command(desk, "o/r", 7)
        asked.assert_called_once_with(3, run_stop.ASK_WAIT)
        with mock.patch.object(run_stop, "ask", return_value={"ended": ["bash"], "text": "Stopped it."}):
            self.assertEqual(run_stop.stop_command(desk, "o/r", 7), "Stopped it.")

    def test_a_bash_call_matches_its_command_as_given_or_quoted(self):
        one = {"argv": ["/bin/bash", "-c", "source x && eval 'echo '\"'\"'hi'\"'\"'' && pwd"]}
        self.assertTrue(run_stop.runs_command(one, "echo 'hi'"))
        self.assertTrue(run_stop.runs_command({"argv": ["bash", "-c", "cargo test"]}, "cargo test"))
        self.assertFalse(run_stop.runs_command({"argv": ["cargo", "test"]}, "cargo test"))

    def test_codex_rejects_two_matching_processes(self):
        table = {pid: {"pid": pid, "ppid": 10, "start": 100, "argv": ["sleep", "300"]} for pid in (11, 12)}
        with mock.patch.object(run_stop, "uptime", return_value=102):
            self.assertIsNone(run_stop.codex_shell(table, 10, (), "/bin/bash -lc 'sleep 300'", 2))

    def test_a_stop_line_joins_the_agent_log_in_plain(self):
        beats = [json.dumps({"type": "stop", "timestamp": "2026-11-21T10:00:00Z", "text": "The user stopped it."})]
        lines = list(agent_log.merged([], beats))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0][0].endswith("The user stopped it."))
        self.assertFalse(lines[0][1])


if __name__ == "__main__":
    unittest.main()
