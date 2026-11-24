"""Translate the Codex CLI's JSONL events for the run supervisor and Agent Log."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


def transcript(state, cwd, thread):
    """The desk's copy of a Codex thread's events, scoped to its working directory."""
    key = hashlib.sha256(str(Path(cwd).resolve()).encode()).hexdigest()
    return state / "codex-logs" / key / f"{thread}.jsonl"


def turn_model(home, session, launched_at):
    """Read the effective model for the turn this process started, including on a resumed thread."""
    for path in (home / "sessions").glob(f"**/rollout-*-{session}.jsonl"):
        try:
            with path.open(encoding="utf-8") as lines:
                provider = None
                for line in lines:
                    try:
                        event = json.loads(line)
                        payload = event["payload"]
                        if event["type"] == "session_meta" and payload.get("id") == session:
                            provider = payload.get("model_provider")
                        if event["type"] == "turn_context" and payload.get("model"):
                            timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                            if timestamp >= launched_at:
                                return payload["model"], provider
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
        except OSError:
            continue
    return None


def message(role, block):
    return {"type": role, "message": {"content": [block]}}


def events(event, elapsed):
    kind = event.get("type")
    if kind == "thread.started":
        thread = event.get("thread_id")
        if isinstance(thread, str) and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", thread):
            yield {"type": "system", "subtype": "init", "session_id": thread, "model": "Codex"}
    elif kind in ("turn.completed", "turn.failed"):
        failed = kind == "turn.failed"
        if failed:
            error = event.get("error") or {}
            yield message("assistant", {"type": "text", "text": error.get("message") or "Codex turn failed."})
        yield {"type": "result", "subtype": "error" if failed else "success", "is_error": failed,
               "duration_ms": round(elapsed * 1000), "num_turns": 1}
    elif kind == "error":
        yield message("assistant", {"type": "text", "text": event.get("message") or "Codex reported an error."})
    elif kind in ("item.started", "item.completed"):
        item = event.get("item") or {}
        if item.get("type") == "command_execution":
            if kind == "item.started":
                yield message("assistant", {"type": "tool_use", "id": item["id"], "name": "Shell",
                                            "input": {"command": item.get("command", "")}})
            else:
                failed = item.get("status") == "failed" or item.get("exit_code") not in (0, None)
                yield message("user", {"type": "tool_result", "tool_use_id": item["id"], "is_error": failed,
                                       "content": item.get("aggregated_output") or ""})
        elif kind == "item.completed" and item.get("type") == "agent_message":
            yield message("assistant", {"type": "text", "text": item.get("text") or ""})
