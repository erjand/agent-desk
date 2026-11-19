"""Tests for lib/agent_run.py through desk_agent_run in lib/common.sh: a stand-in for claude prints a result event and
then waits on a child it started in a session of its own, as Claude Code's background Bash commands run, and the run
must end at the result anyway."""

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

import agent_run  # noqa: E402

RESULT_GRACE = 1
KILL_GRACE = 1
BEAT = 0.5
SESSION = "5f1c2a9e-0000-4000-8000-000000000518"


def result(is_error=False):
    return json.dumps({"type": "result", "subtype": "error_during_execution" if is_error else "success",
                       "is_error": is_error, "num_turns": 3, "duration_ms": 60000})


def alive(pid):
    """Whether a process runs, counting a zombie as gone."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class AgentRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fakes = self.root / "fakes"
        self.fakes.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.args_log = self.root / "claude.args"
        self.child_pid = self.root / "child.pid"
        self.stream = self.root / "stream"
        self.addCleanup(self.end_child)

    def end_child(self):
        if self.child_pid.exists():
            try:
                os.kill(int(self.child_pid.read_text()), 9)
            except ProcessLookupError:
                pass

    def claude(self, body):
        """A claude on PATH that logs its arguments and then runs body."""
        fake = self.fakes / "claude"
        fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >"{self.args_log}"\n{body}\n')
        fake.chmod(0o755)

    def lingering(self, line, trap=""):
        """A claude that prints line, then waits on a child that runs in its own session for five minutes."""
        self.claude(f"{trap}\nsetsid sleep 300 &\necho $! >\"{self.child_pid}\"\necho '{line}'\nwait")

    def agent_run(self, call="desk_agent_run \"$work\" \"\" new", **extra):
        """Runs desk_agent_run, or another call on the fake given the folder in $work and the stream in $stream, with
        BASH_MAX_TIMEOUT_MS and BASH_DEFAULT_TIMEOUT_MS unset unless extra sets them, and returns the result and how
        long it took."""
        env = {**os.environ, "PATH": f"{self.fakes}:{os.environ['PATH']}", "XDG_STATE_HOME": str(self.root / "state"),
               "AGENT_DESK_RESULT_GRACE": str(RESULT_GRACE), "AGENT_DESK_KILL_GRACE": str(KILL_GRACE),
               "AGENT_DESK_BEAT": str(BEAT),
               "work": str(self.work), "stream": str(self.stream)}
        env.pop("BASH_MAX_TIMEOUT_MS", None)
        env.pop("BASH_DEFAULT_TIMEOUT_MS", None)
        env.update(extra)
        script = (f'source "{LIB}/common.sh"; desk_model_args=(); desk_model=; desk_effort=; '
                  f'{call} "/agent-desk:work-issue 7"')
        began = time.monotonic()
        done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
        return done, time.monotonic() - began

    def test_a_run_ends_at_its_result_and_ends_the_task_left_running(self):
        self.lingering(result())
        done, took = self.agent_run()
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertLess(took, RESULT_GRACE + KILL_GRACE + 5)
        self.assertIn("Agent finished (success) after 3 turns, 1.0 min", done.stdout)
        self.assertIn("so the run ends it", done.stdout)
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_a_run_that_ends_a_task_the_agent_left_stops_early_and_names_the_task(self):
        bench = "just bench --ride=c-halcrow > .scratch/bench.log 2>&1"
        events = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                                           "input": {"command": bench, "run_in_background": True}}]}},
            {"type": "system", "subtype": "background_tasks_changed",
             "tasks": [{"task_id": "b1", "description": "Benchmark a ride"}]},
            {"type": "system", "subtype": "task_started", "task_id": "b1", "tool_use_id": "toolu_1",
             "description": "Benchmark a ride"},
        ]
        lines = "\n".join(f"echo '{json.dumps(event)}'" for event in events)
        self.lingering(result(), trap=lines)
        done, took = self.agent_run('desk_claude_run "$work" "$stream"')
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertLess(took, RESULT_GRACE + KILL_GRACE + 5)
        self.assertIn(f"held by `{bench}`, so the run ends it and stops early.", done.stdout)
        self.assertEqual(Path(f"{self.stream}.left").read_text(), f"`{bench}`")
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_desk_agent_run_leaves_the_task_a_run_that_stopped_early_left(self):
        self.lingering(result())
        done, _ = self.agent_run('run() { desk_agent_run "$@"; local status=$?; echo "left: $desk_left"; '
                                 'return "$status"; }; run "$work" "" new')
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertIn("left: a task it started\n", done.stdout)

    def test_desk_left_task_reads_and_removes_the_task_only_for_a_run_that_stopped_early(self):
        tasks = Path(f"{self.stream}.left")
        for status, written, expected in [(75, "`just bench`", "`just bench`"), (75, None, "a task it started"),
                                          (0, "`just bench`", ""), (1, None, "")]:
            if written is not None:
                tasks.write_text(written)
            done = subprocess.run(["bash", "-c", f'source "{LIB}/common.sh"; desk_left_task "$0" {status}',
                                   str(self.stream)], capture_output=True, text=True, timeout=10)
            self.assertEqual((done.returncode, done.stdout), (0, expected), done.stderr)
            self.assertFalse(tasks.exists())

    def test_a_failed_result_gives_the_run_a_failed_status(self):
        self.lingering(result(is_error=True))
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_a_claude_that_ignores_term_is_killed(self):
        self.lingering(result(), trap="trap '' TERM")
        done, took = self.agent_run()
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertLess(took, RESULT_GRACE + KILL_GRACE + 5)
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_a_task_whose_parent_exited_ends_with_the_run(self):
        self.claude(f'(setsid sleep 300 & echo $! >"{self.child_pid}")\necho \'{result()}\'\nexec sleep 300')
        done, took = self.agent_run()
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertLess(took, RESULT_GRACE + KILL_GRACE + 5)
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_an_adopted_orphan_leaves_no_zombie_once_it_exits(self):
        orphan, seen = self.root / "orphan", self.root / "seen"
        # The subshell exits at once, so its short sleep reparents to agent_run.py. The fake then watches the sleep's
        # state for up to five seconds, until /proc no longer lists it, and records its parent and its last state.
        self.claude(f'(sleep 0.2 & echo $! >"{orphan}")\n'
                    f'pid=$(<"{orphan}")\nparent=$(cut -d" " -f4 "/proc/$pid/stat")\n'
                    f'for _ in $(seq 50); do\n'
                    f'    state=$(cut -d" " -f3 "/proc/$pid/stat" 2>/dev/null) || break\n    sleep 0.1\ndone\n'
                    f'echo "$((parent == PPID)) ${{state:-gone}}" >"{seen}"\n'
                    f"echo '{result()}'\nexit 5")
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 5, done.stderr)
        adopted, state = seen.read_text().split()
        self.assertEqual(adopted, "1")
        self.assertEqual(state, "gone")

    def test_a_claude_that_exits_after_its_result_keeps_its_own_status(self):
        self.claude(f"echo '{result()}'\nexit 3")
        done, took = self.agent_run()
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertLess(took, RESULT_GRACE)
        self.assertIn("Agent finished (success)", done.stdout)
        self.assertNotIn("so the run ends it", done.stdout)

    def test_a_claude_that_never_reports_a_result_runs_until_it_exits(self):
        self.claude("echo not json\nsleep 2\nexit 4")
        done, took = self.agent_run()
        self.assertEqual(done.returncode, 4, done.stderr)
        self.assertGreaterEqual(took, 2)
        self.assertIn("not json", done.stdout)

    def events(self, *events):
        return "\n".join(f"echo {shlex.quote(json.dumps(event))}" for event in events)

    def test_a_quiet_run_logs_the_call_it_waits_on_and_its_busy_child(self):
        wait = "timeout 7000 bash -c 'until grep -q \"^exit\" .scratch/fill.log; do sleep 30; done'"
        fill = "just bank-fill algebra-1 > .scratch/fill.log 2>&1"
        (self.fakes / "spin.py").write_text("while True:\n    pass\n")
        before = self.events(
            {"type": "system", "subtype": "init", "session_id": SESSION, "model": "opus"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                                           "input": {"command": fill, "run_in_background": True}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1"}]}},
            {"type": "system", "subtype": "task_started", "task_id": "b1", "tool_use_id": "toolu_1",
             "description": "Fill the bank"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash",
                                                           "input": {"command": wait}}]}})
        after = self.events({"type": "user", "message": {"content": [{"type": "tool_result",
                                                                      "tool_use_id": "toolu_2"}]}})
        # The fake goes quiet for three beats while a python module spins, then reports the call's result.
        self.claude(f'{before}\nPYTHONPATH="{self.fakes}" python3 -m spin &\nspin=$!\nsleep {BEAT * 3.6}\n'
                    f'kill $spin\n{after}\necho \'{result()}\'')
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        beats = [line for line in done.stdout.splitlines() if " Still in Bash for " in line]
        self.assertGreaterEqual(len(beats), 2, done.stdout)
        self.assertLessEqual(len(beats), 4, done.stdout)
        first = beats[0].split(" ", 1)[1]
        self.assertRegex(first, r"^Still in Bash for \d+ s: timeout 7000 bash -c 'until grep -q \"\^exit\" "
                                r"\.scratch/fill\.log; do sleep 30; don … · background: just bank-fill algebra-1 "
                                r"> \.scratch/fill\.log 2>&1 · busy: python -m spin \(\d+% CPU\)")
        written = (self.root / "state" / "agent-desk" / "beats" / SESSION).read_text().splitlines()
        self.assertEqual([json.loads(line)["text"] for line in written], [line.split(" ", 1)[1] for line in beats])
        self.assertEqual({json.loads(line)["type"] for line in written}, {"beat"})
        self.assertRegex(json.loads(written[0])["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_a_run_with_steady_events_logs_no_beat(self):
        tick = self.events({"type": "system", "subtype": "status"})
        init = self.events({"type": "system", "subtype": "init", "session_id": SESSION, "model": "opus"})
        self.claude(f"{init}\nfor _ in $(seq 15); do\n{tick}\nsleep 0.1\ndone\necho '{result()}'")
        done, took = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertGreater(took, BEAT * 2)
        self.assertNotIn("Still in", done.stdout)
        self.assertNotIn("Quiet for", done.stdout)
        self.assertFalse((self.root / "state" / "agent-desk" / "beats").exists())

    def test_the_run_forbids_tools_that_outlive_the_agents_last_turn(self):
        self.claude(f"echo '{result()}'")
        self.agent_run()
        args = self.args_log.read_text().splitlines()
        self.assertEqual(args[:2], ["-p", "/agent-desk:work-issue 7"])
        self.assertEqual(args[args.index("--disallowedTools") + 1], "ScheduleWakeup,CronCreate,Monitor")
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")

    def test_a_run_may_wait_two_hours_on_one_command(self):
        self.claude(f"echo \"$BASH_MAX_TIMEOUT_MS\" >\"{self.root / 'max'}\"\necho '{result()}'")
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "max").read_text(), "7200000\n")

    def test_a_run_keeps_the_users_own_command_limit(self):
        self.claude(f"echo \"$BASH_MAX_TIMEOUT_MS\" >\"{self.root / 'max'}\"\necho '{result()}'")
        done, _ = self.agent_run('desk_claude_run "$work" "$stream"', BASH_MAX_TIMEOUT_MS="900000")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "max").read_text(), "900000\n")

    def test_a_command_given_no_timeout_waits_fifteen_minutes(self):
        self.claude(f"echo \"$BASH_DEFAULT_TIMEOUT_MS\" >\"{self.root / 'default'}\"\necho '{result()}'")
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "default").read_text(), "900000\n")

    def test_a_run_keeps_the_users_own_default_timeout(self):
        self.claude(f"echo \"$BASH_DEFAULT_TIMEOUT_MS\" >\"{self.root / 'default'}\"\necho '{result()}'")
        done, _ = self.agent_run('desk_claude_run "$work" "$stream"', BASH_DEFAULT_TIMEOUT_MS="300000")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "default").read_text(), "300000\n")


class StreamTest(unittest.TestCase):
    def test_the_stream_file_holds_claudes_output_as_it_came(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream = Path(tmp) / "stream"
            lines = ['{"type": "system", "subtype": "init"}', "plain text", result()]
            with redirect_stdout(io.StringIO()) as log:
                status = agent_run.run(["printf", "%s\\n", *lines], stream, result_grace=1, kill_grace=1)
            self.assertEqual(status, 0)
            self.assertEqual(stream.read_text().splitlines(), lines)
            self.assertIn("plain text", log.getvalue())

    def test_the_task_left_running_is_the_newest_the_stream_still_lists(self):
        tasks = agent_run.Tasks()
        self.assertEqual(tasks.left(), "")
        tasks.see({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "make all\nmake check"}},
            {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"prompt": "Explore"}},
            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "echo `date`"}}]}})
        for task, call in (("b1", "t1"), ("a2", "t2"), ("b3", "t3")):
            tasks.see({"type": "system", "subtype": "task_started", "task_id": task, "tool_use_id": call,
                       "description": f"Task {task}"})
        self.assertEqual(tasks.left(), "`` echo `date` ``")
        tasks.see({"type": "system", "subtype": "task_notification", "task_id": "b3", "status": "completed"})
        self.assertEqual(tasks.left(), "the task “Task a2”")
        tasks.see({"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "b1"}]})
        self.assertEqual(tasks.left(), "`make all`")
        tasks.see({"type": "system", "subtype": "background_tasks_changed", "tasks": []})
        self.assertEqual(tasks.left(), "")

    def test_a_beat_names_the_newest_call_still_waiting_or_says_none_is(self):
        tasks = agent_run.Tasks()
        self.assertRegex(tasks.waiting(), r"^Quiet for \d+ s with no tool call waiting$")
        tasks.see({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"description": "Explore the store"}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "x" * 200}}]}})
        self.assertEqual(tasks.waiting(), f"Still in Bash for 0 s: {'x' * 79} …")
        tasks.see({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2"}]}})
        self.assertEqual(tasks.waiting(), "Still in Agent for 0 s: Explore the store")
        tasks.see({"type": "system", "subtype": "task_started", "task_id": "a1", "description": "Judge item 4"})
        self.assertEqual(tasks.waiting(), "Still in Agent for 0 s: Explore the store · background: Judge item 4")

    def test_a_stretch_reads_in_seconds_then_minutes(self):
        self.assertEqual(agent_run.span(42.9), "42 s")
        self.assertEqual(agent_run.span(12 * 60 + 59), "12 min")

    def test_a_process_is_named_by_what_it_runs(self):
        for argv, name in [
            (["/usr/bin/python3.13", "-B", "-m", "quiz.bank_fill", "--set", "algebra-1"], "python -m quiz.bank_fill"),
            (["python3", "-W", "ignore", "-mquiz.judge"], "python -m quiz.judge"),
            (["python", "-u", "/srv/lib/run_log.py", "record"], "python run_log.py"),
            (["python3", "-c", "print(1)"], "python -c"),
            (["uv", "run", "--with", "rich", "python", "-m", "quiz.bank_fill"], "python -m quiz.bank_fill"),
            (["/home/u/.local/bin/uv", "run", "--frozen", "pytest", "-x"], "pytest"),
            (["uv", "run"], "uv run"),
            (["claude", "-p", "Judge this item", "--output-format", "json"], "claude -p"),
            (["claude", "--resume", "abc"], "claude"),
            (["/usr/bin/node", "/srv/cli.js"], "node"),
            ([], ""),
        ]:
            self.assertEqual(agent_run.command_name(argv), name, argv)

    def test_the_busy_list_ranks_three_commands_by_cpu_since_the_last_line(self):
        ticks = agent_run.CLOCK_TICKS
        before = {(1, "1"): ("claude -p", 0), (2, "2"): ("claude -p", 10 * ticks), (3, "3"): ("bash", 5 * ticks),
                  (4, "4"): ("python -m quiz.bank_fill", 0)}
        after = {(1, "1"): ("claude -p", 30 * ticks), (2, "2"): ("claude -p", 30 * ticks),
                 (3, "3"): ("bash", 5 * ticks), (4, "4"): ("python -m quiz.bank_fill", 4 * ticks),
                 (5, "5"): ("claude -p", 35 * ticks), (6, "6"): ("sleep", 1), (7, "7"): ("timeout", 2 * ticks)}
        self.assertEqual(agent_run.busy(before, after, 100),
                         "busy: claude -p ×3 (85% CPU), python -m quiz.bank_fill (4%), timeout (2%)")
        self.assertEqual(agent_run.busy(before, before, 100), "busy: nothing")
        self.assertEqual(agent_run.busy({}, {(6, "6"): ("sleep", 1)}, 300), "busy: sleep (<1% CPU)")

    def test_the_cpu_sample_holds_a_busy_child(self):
        spin = subprocess.Popen([sys.executable, "-c", "while True: pass"])
        self.addCleanup(spin.wait)
        self.addCleanup(spin.kill)
        time.sleep(0.3)
        sample = agent_run.cpu_sample(os.getpid())
        names = {name: ticks for (pid, _), (name, ticks) in sample.items() if pid == spin.pid}
        self.assertEqual(list(names), ["python -c"])
        self.assertGreater(names["python -c"], 0)

    def test_a_code_span_fences_the_backticks_inside_it(self):
        self.assertEqual(agent_run.code("just test"), "`just test`")
        self.assertEqual(agent_run.code("a `b` c"), "``a `b` c``")
        self.assertEqual(agent_run.code("`b`"), "`` `b` ``")

    def test_a_signal_ending_claude_gives_the_shells_status(self):
        self.assertEqual(agent_run.status_of(-15), 143)
        self.assertEqual(agent_run.status_of(2), 2)


if __name__ == "__main__":
    unittest.main()
