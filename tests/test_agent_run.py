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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

import agent_run  # noqa: E402
import store  # noqa: E402

RESULT_GRACE = 1
KILL_GRACE = 1
# The graces of a test whose claude outlives its result, which waits each grace out to its end.
QUICK = {"AGENT_DESK_RESULT_GRACE": "0.1", "AGENT_DESK_KILL_GRACE": "0.1"}
BEAT = 0.25
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
        # The project's main checkout, so common.sh never reads the config of the checkout the tests run from, which
        # the public repository leaves out.
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        (self.checkout / ".agent-desk.json").write_text("{}")
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

    def agent_run(self, call='desk_agent_run "$work" "" new "$prompt" "$session"', **extra):
        """Runs desk_agent_run, or another call on the fake given the folder in $work and the stream in $stream, with
        BASH_MAX_TIMEOUT_MS, BASH_DEFAULT_TIMEOUT_MS, and AGENT_DESK_UNIT unset unless extra sets them, so a test that
        runs inside a unit never follows that unit's journal, and returns the result and how long it took."""
        env = {**os.environ, "PATH": f"{self.fakes}:{os.environ['PATH']}", "XDG_STATE_HOME": str(self.root / "state"),
               "AGENT_DESK_RESULT_GRACE": str(RESULT_GRACE), "AGENT_DESK_KILL_GRACE": str(KILL_GRACE),
               "AGENT_DESK_BEAT": str(BEAT), "AGENT_DESK_CHECKOUT": str(self.checkout), "GH_REPO": "o/r",
               "work": str(self.work), "stream": str(self.stream), "prompt": "/agent-desk:work-issue 7",
               "session": SESSION}
        env.pop("BASH_MAX_TIMEOUT_MS", None)
        env.pop("BASH_DEFAULT_TIMEOUT_MS", None)
        env.pop("AGENT_DESK_UNIT", None)
        env.pop("AGENT_DESK_ACCOUNT", None)
        env.pop("AGENT_DESK_RUN", None)
        env.update(extra)
        script = f'source "{LIB}/common.sh"; desk_runner=claude; desk_model_args=(); desk_model=; desk_effort=; {call}'
        began = time.monotonic()
        done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
        return done, time.monotonic() - began

    def test_a_run_ends_at_its_result_ends_the_task_the_agent_left_and_names_it(self):
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
        # desk_left holds the task desk_claude_run wrote to <stream>.left, once desk_agent_run has read it.
        done, took = self.agent_run('run() { desk_agent_run "$@"; local status=$?; echo "left: $desk_left"; '
                                    'return "$status"; }; run "$work" "" new "$prompt" "$session"', **QUICK)
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertLess(took, 5)
        self.assertIn("Agent finished (success) after 3 turns, 1.0 min", done.stdout)
        self.assertIn(f"held by `{bench}`, so the run ends it and stops early.", done.stdout)
        self.assertIn(f"left: `{bench}`\n", done.stdout)
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_a_runs_row_records_the_session_it_takes_up_before_claude_starts(self):
        desk = store.Store(self.root / "state" / "agent-desk" / "desk.sqlite")
        self.addCleanup(desk.close)
        cut, resumed = desk.request_run("o/r", "agent", "7"), desk.request_run("o/r", "agent", "7")
        # A run cut off at its time limit reports no result, and its row still names its session.
        self.claude("exit 143")
        done, _ = self.agent_run(AGENT_DESK_RUN=str(cut))
        self.assertEqual(done.returncode, 143, done.stderr)
        self.assertEqual((desk.run(cut)["session"], desk.run(cut)["cost"]), (SESSION, None))
        event = json.dumps({"type": "result", "session_id": SESSION, "num_turns": 3, "duration_ms": 60000,
                            "total_cost_usd": 4.5})
        self.claude(f"echo '{event}'")
        done, _ = self.agent_run('desk_agent_run "$work" "" resumed "$prompt" "$session" "$session"',
                                 AGENT_DESK_RUN=str(resumed))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((desk.run(resumed)["session"], desk.run(resumed)["cost"]), (SESSION, 4.5))

    def test_desk_left_task_reads_and_removes_the_task_only_for_a_run_that_stopped_early(self):
        tasks = Path(f"{self.stream}.left")
        for status, written, expected in [(75, "`just bench`", "`just bench`"), (75, None, "a task it started"),
                                          (0, "`just bench`", ""), (1, None, "")]:
            if written is not None:
                tasks.write_text(written)
            done = subprocess.run(["bash", "-c", f'source "{LIB}/common.sh"; desk_left_task "$0" {status}',
                                   str(self.stream)], env={**os.environ, "DESK_HELPERS_ONLY": "1"},
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual((done.returncode, done.stdout), (0, expected), done.stderr)
            self.assertFalse(tasks.exists())

    def test_a_failed_result_gives_the_run_a_failed_status(self):
        self.lingering(result(is_error=True))
        done, _ = self.agent_run(**QUICK)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_a_claude_that_ignores_term_is_killed(self):
        self.lingering(result(), trap="trap '' TERM")
        done, took = self.agent_run(**QUICK)
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertLess(took, 5)
        self.assertFalse(alive(int(self.child_pid.read_text())))

    def test_a_task_whose_parent_exited_ends_with_the_run(self):
        self.claude(f'(setsid sleep 300 & echo $! >"{self.child_pid}")\necho \'{result()}\'\nexec sleep 300')
        done, took = self.agent_run(**QUICK)
        self.assertEqual(done.returncode, agent_run.STOPPED, done.stderr)
        self.assertLess(took, 5)
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
        # The claude outlives the result grace, which a run without a result never starts.
        self.claude("echo not json\nsleep 0.5\nexit 4")
        done, took = self.agent_run(**QUICK)
        self.assertEqual(done.returncode, 4, done.stderr)
        self.assertGreaterEqual(took, 0.5)
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
        self.claude(f"{init}\nfor _ in $(seq 12); do\n{tick}\nsleep {BEAT / 5}\ndone\necho '{result()}'")
        done, took = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertGreater(took, BEAT * 2)
        self.assertNotIn("Still in", done.stdout)
        self.assertNotIn("Quiet for", done.stdout)
        self.assertFalse((self.root / "state" / "agent-desk" / "beats").exists())

    def test_progress_resets_the_silence_and_a_heartbeat_does_not(self):
        progress = {
            "an assistant message": {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi"}]}},
            "a tool result": {"type": "user", "message": {"content": [{"type": "tool_result",
                                                                       "tool_use_id": "toolu_1"}]}},
            "a task starting": {"type": "system", "subtype": "task_started", "task_id": "b1",
                                "tool_use_id": "toolu_1", "description": "Build"},
            "a task ending": {"type": "system", "subtype": "task_notification", "task_id": "b1",
                              "status": "completed"},
            "a line that is not JSON": None,
        }
        heartbeat = {"type": "tool_progress", "tool_use_id": "toolu_1-heartbeat-0", "tool_name": "Bash",
                     "parent_tool_use_id": "toolu_1", "elapsed_time_seconds": 30, "heartbeat": True}
        for name, event in [*progress.items(), ("a heartbeat", heartbeat)]:
            with self.subTest(name):
                tasks = agent_run.Tasks()
                tasks.heard = 0.0
                line = b"not json\n" if event is None else json.dumps(event).encode() + b"\n"
                with redirect_stdout(io.StringIO()):
                    agent_run.copy([line], io.BytesIO(), [], tasks)
                if event is heartbeat:
                    self.assertEqual(tasks.heard, 0.0)
                else:
                    self.assertGreater(tasks.heard, 0.0)

    def test_a_failed_call_logs_its_error_when_its_result_arrives(self):
        self.claude(self.events(
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                                           "input": {"command": "cargo test",
                                                                     "description": "Run the GPU tests"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
                                                      "content": "Exit code 101\nerror: test failed"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1"}]}},
        ) + f"\necho '{result()}'")
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = [line.split(" ", 1)[1] for line in done.stdout.splitlines() if " Bash" in line]
        self.assertEqual(lines, ["→ Bash: Run the GPU tests · cargo test", "← Bash 0 s, failed: error: test failed"])

    def test_a_crash_under_the_run_logs_one_line_and_the_beats_file_keeps_it(self):
        unit = "agent-desk-agent-o-r-7-1.service"
        entry = {"COREDUMP_COMM": "gpu-aa4bea58f34", "COREDUMP_SIGNAL_NAME": "SIGSEGV", "COREDUMP_SIGNAL": "11",
                 "__CURSOR": "s=1"}
        follower, args = self.root / "journalctl.pid", self.root / "journalctl.args"
        journalctl = self.fakes / "journalctl"
        journalctl.write_text(f'#!/bin/bash\necho $$ >"{follower}"\nprintf "%s\\n" "$@" >"{args}"\nsleep 0.1\n'
                              f"echo {shlex.quote(json.dumps(entry))}\nexec sleep 300\n")
        journalctl.chmod(0o755)
        init = self.events({"type": "system", "subtype": "init", "session_id": SESSION, "model": "opus"})
        self.claude(f"{init}\nsleep 0.6\necho '{result()}'")
        done, _ = self.agent_run(AGENT_DESK_UNIT=unit)
        self.assertEqual(done.returncode, 0, done.stderr)
        crashes = [line.split(" ", 1)[1] for line in done.stdout.splitlines() if "crashed" in line]
        self.assertEqual(crashes, ["A process under the run crashed: gpu-aa4bea58f34 (SIGSEGV)"])
        self.assertIn(f"COREDUMP_USER_UNIT={unit}", args.read_text().splitlines())
        written = [json.loads(line) for line in
                   (self.root / "state" / "agent-desk" / "beats" / SESSION).read_text().splitlines()]
        self.assertEqual([line["text"] for line in written if line["type"] == "crash"], crashes)
        self.assertFalse(alive(int(follower.read_text())))

    def test_the_run_keeps_no_core_files_unless_a_command_asks(self):
        limits = self.root / "limits"
        self.claude(f'echo "$(ulimit -Sc) $(ulimit -Hc)" >"{limits}"\necho \'{result()}\'')
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        hard = subprocess.run(["bash", "-c", "ulimit -Hc"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(limits.read_text().split(), ["0", hard])

    def stalling(self, body):
        """
        A claude that starts a Bash call to `cargo test`, runs body while the call waits, then returns the call's
        result and its own, and a run of it under a stall rule of half a second's wait and a half-second window. body
        copies the run's saved stall to seen.json while it stands. Returns the run's result, the notifications sent,
        and the stall the body saw, or None.
        """
        before = self.events(
            {"type": "system", "subtype": "init", "session_id": SESSION, "model": "opus"},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                                           "input": {"command": "cargo test --workspace"}}]}})
        after = self.events({"type": "user", "message": {"content": [{"type": "tool_result",
                                                                      "tool_use_id": "toolu_1"}]}})
        saved = self.root / "state" / "agent-desk" / "stalls" / "7.json"
        seen = self.root / "seen.json"
        told = self.root / "notify.log"
        notify = self.fakes / "notify-send"
        notify.write_text(f'#!/bin/bash\nprintf "%s|" "$@" >>"{told}"\necho >>"{told}"\n')
        notify.chmod(0o755)
        self.claude(f"{before}\n( {body} ) &\nbody=$!\nwhile kill -0 $body 2>/dev/null; do "
                    f"[ -e \"{saved}\" ] && cp \"{saved}\" \"{seen}\"; sleep 0.05; done\n{after}\necho '{result()}'")
        done, _ = self.agent_run(AGENT_DESK_STALL_AFTER="0.5", AGENT_DESK_STALL_WINDOW="0.5", AGENT_DESK_RUN="7",
                                 AGENT_DESK_UNIT="")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(saved.exists(), "the mark clears once the call returns")
        return (done, told.read_text().splitlines() if told.exists() else [],
                json.loads(seen.read_text()) if seen.exists() else None)

    def test_a_call_on_a_command_that_left_the_cpu_idle_stalls_once_until_it_returns(self):
        done, told, seen = self.stalling("sleep 1")
        self.assertEqual((seen["tool"], seen["summary"]), ("Bash", "cargo test --workspace"))
        self.assertRegex(seen["since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(told, ["--app-name=agent-desk|An agent run stalled|run 7: stalled 0 min in Bash: "
                                "cargo test --workspace|"])
        self.assertEqual(len([line for line in done.stdout.splitlines() if " Stalled: " in line]), 1, done.stdout)

    def test_a_long_command_that_keeps_the_cpu_busy_never_stalls(self):
        (self.fakes / "spin.py").write_text("while True:\n    pass\n")
        done, told, seen = self.stalling(f'PYTHONPATH="{self.fakes}" timeout 1 python3 -m spin')
        self.assertEqual((told, seen), ([], None))
        self.assertNotIn("Stalled:", done.stdout)

    def test_a_call_waiting_on_the_gpu_lock_never_stalls(self):
        env = {**os.environ, "XDG_RUNTIME_DIR": str(self.root / "runtime"), "XDG_STATE_HOME": str(self.root / "gpu"),
               "AGENT_DESK_GPU_SLOTS": "1", "AGENT_DESK_RUN": ""}
        (self.root / "runtime").mkdir()
        holder = subprocess.Popen(["bash", str(LIB / "gpu.sh"), "--", "sleep", "60"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.terminate)
        for _ in range(100):
            if list((self.root / "runtime" / "agent-desk").glob("gpu.held.*")):
                break
            time.sleep(0.05)
        wait = (f'XDG_RUNTIME_DIR="{self.root / "runtime"}" AGENT_DESK_GPU_SLOTS=1 bash "{LIB / "gpu.sh"}" -- true '
                '2>/dev/null & gpu=$!; sleep 1; kill $gpu')
        done, told, seen = self.stalling(wait)
        self.assertEqual((told, seen), ([], None))
        self.assertNotIn("Stalled:", done.stdout)

    def test_the_run_forbids_tools_that_outlive_the_agents_last_turn(self):
        self.claude(f"echo '{result()}'")
        self.agent_run()
        args = self.args_log.read_text().splitlines()
        self.assertEqual(args[:2], ["-p", "/agent-desk:work-issue 7"])
        self.assertEqual(args[args.index("--disallowedTools") + 1], "ScheduleWakeup,CronCreate,Monitor")
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")

    def timeouts(self):
        """A claude that writes the command limit and the default timeout it runs under to the file timeouts."""
        self.claude(f"echo \"$BASH_MAX_TIMEOUT_MS $BASH_DEFAULT_TIMEOUT_MS\" >\"{self.root / 'timeouts'}\"\n"
                    f"echo '{result()}'")

    def test_a_run_may_wait_two_hours_on_one_command_and_fifteen_minutes_on_one_given_no_timeout(self):
        self.timeouts()
        done, _ = self.agent_run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "timeouts").read_text(), "7200000 900000\n")

    def test_a_run_keeps_the_users_own_command_limit_and_default_timeout(self):
        self.timeouts()
        done, _ = self.agent_run('desk_claude_run "$work" "$stream" "$prompt"', BASH_MAX_TIMEOUT_MS="900000",
                                 BASH_DEFAULT_TIMEOUT_MS="300000")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / "timeouts").read_text(), "900000 300000\n")


class StreamTest(unittest.TestCase):
    def test_claude_attribution_uses_the_model_the_stream_reports(self):
        init = {"type": "system", "subtype": "init", "model": "claude-opus-4-6"}
        with mock.patch.object(agent_run.run_log, "record_identity", return_value=True) as record:
            agent_run.copy([json.dumps(init).encode()], io.BytesIO(), [], agent_run.Tasks(),
                           identity=(3, "1.2.3", datetime.now(timezone.utc)))
        record.assert_called_once_with(3, "claude-opus-4-6", "anthropic", "1.2.3")

    def test_codex_attribution_reads_the_resumed_turn_and_ignores_the_previous_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            rollout = home / "sessions" / "2026" / f"rollout-2026-11-14-{SESSION}.jsonl"
            rollout.parent.mkdir(parents=True)
            rows = [{"type": "session_meta", "payload": {"id": SESSION, "model_provider": "openai"}},
                    {"type": "turn_context", "timestamp": "2026-11-14T18:00:00Z", "payload": {"model": "gpt-old"}}]
            rollout.write_text("\n".join(map(json.dumps, rows)) + "\n", encoding="utf-8")
            launch = datetime(2026, 11, 14, 19, tzinfo=timezone.utc)
            self.assertIsNone(agent_run.codex_stream.turn_model(home, SESSION, launch))
            with rollout.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"type": "turn_context", "timestamp": "2026-11-14T19:00:01Z",
                                       "payload": {"model": "gpt-new"}}) + "\n")

            stream = [{"type": "thread.started", "thread_id": SESSION}, {"type": "turn.completed"}]
            with (mock.patch.dict(os.environ, {"CODEX_HOME": tmp, "AGENT_DESK_LOCAL_PROVIDER": ""}),
                  mock.patch.object(agent_run, "beats_folder", return_value=home / "beats"),
                  mock.patch.object(agent_run, "codex_session"),
                  mock.patch.object(agent_run.run_log, "record_identity", return_value=True) as record):
                agent_run.copy([json.dumps(event).encode() for event in stream], io.BytesIO(), [],
                               agent_run.Tasks(), codex=True, identity=(9, "1.2.3", launch))
            record.assert_called_once_with(9, "gpt-new", "openai", "1.2.3")

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

    def test_a_crash_line_names_the_program_and_its_signal(self):
        for entry, line in [
            ({"COREDUMP_COMM": "gpu-aa4", "COREDUMP_SIGNAL_NAME": "SIGABRT", "COREDUMP_SIGNAL": "6"},
             "gpu-aa4 (SIGABRT)"),
            ({"COREDUMP_EXE": "/srv/target/debug/gpu", "COREDUMP_SIGNAL": "11"}, "gpu (signal 11)"),
            ({"COREDUMP_COMM": ["not", "text"]}, "a program"),
        ]:
            self.assertEqual(agent_run.crash_line(entry), f"A process under the run crashed: {line}")

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
        deadline = time.monotonic() + 10
        while True:
            sample = agent_run.cpu_sample(os.getpid())
            names = {name: ticks for (pid, _), (name, ticks) in sample.items() if pid == spin.pid}
            if names.get("python -c") or time.monotonic() > deadline:
                break
            time.sleep(0.02)
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
