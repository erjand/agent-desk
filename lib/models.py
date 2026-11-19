"""Picks the Claude model and effort an agent-desk run uses:

    python3 models.py <config> agent [labels]    prints the model and the effort, one a line, either empty when unset
    python3 models.py <config> chore <skill>
    python3 models.py <config> fix
    python3 models.py <config> table <skill>...  prints each chore's and agents' {"model", "effort"} as JSON

A run starts from its kind's setting, `agent`, `chore`, or `fix`; a chore's own setting under `chores`, by its skill's
name, overrides that, and so does each of an agent issue's labels under `labels`, in the order the issue lists them. DEFAULTS hold agent-desk's own choices, and a project's .agent-desk.json overrides them
under `models`. A model or effort left unset falls back to the user's own Claude Code settings.
"""

import json
import sys

# Agents work on Opus at high effort and chores on Opus at medium, whatever the user's own default model. The comment
# chore edits prose within one file, which Sonnet does well for less. The test chore judges what code is worth testing,
# which takes Opus. The audit reads the widest and files the issues later agents work, so it takes high effort. A fix
# merges main and resolves the conflicts, which Sonnet does well for less. agent-desk ships none of these chores: the
# comment and test chores are the optional trim-comments and add-tests skills, and the audit is a project's own skill,
# each applying only to a project that installs and lists it, as docs/reference.md's Chores section describes.
DEFAULTS = {
    "agent": {"model": "opus", "effort": "high"},
    "chore": {"model": "opus", "effort": "medium"},
    "fix": {"model": "sonnet", "effort": "medium"},
    "chores": {"trim-comments": {"model": "sonnet"}, "add-tests": {"model": "opus"},
               "audit": {"model": "opus", "effort": "high"}},
    "labels": {},
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def settings(config_path):
    """agent-desk's defaults with the project's `models` laid over them, one entry at a time."""
    try:
        with open(config_path, encoding="utf-8") as handle:
            mine = json.load(handle).get("models", {})
    except (OSError, ValueError):
        mine = {}
    merged = {key: dict(value) for key, value in DEFAULTS.items()}
    for key, value in mine.items():
        if key in ("chores", "labels") and isinstance(value, dict):
            for name, choice in value.items():
                merged[key][name] = {**merged[key].get(name, {}), **choice}
        elif isinstance(value, dict):
            merged[key] = {**merged.get(key, {}), **value}
    return merged


def pick(chosen, kind, skill=None, labels=()):
    """The model and effort for a run: its kind's, then its chore's or each of its labels', each over the last."""
    result = dict(chosen.get(kind, {}))
    if skill:
        result.update(chosen["chores"].get(skill, {}))
    for label in labels:
        result.update(chosen["labels"].get(label, {}))
    effort = result.get("effort")
    return result.get("model") or "", effort if effort in EFFORTS else ""


def main(argv):
    if len(argv) < 3 or argv[2] not in ("agent", "chore", "fix", "table"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    chosen = settings(argv[1])
    if argv[2] == "table":
        picks = {skill: pick(chosen, "chore", skill=skill) for skill in argv[3:]} | {"agent": pick(chosen, "agent")}
        print(json.dumps({name: {"model": model, "effort": effort} for name, (model, effort) in picks.items()}))
        return 0
    if argv[2] == "chore":
        model, effort = pick(chosen, "chore", skill=argv[3] if len(argv) > 3 else None)
    elif argv[2] == "fix":
        model, effort = pick(chosen, "fix")
    else:
        labels = [label for label in (argv[3] if len(argv) > 3 else "").split(",") if label]
        model, effort = pick(chosen, "agent", labels=labels)
    print(model)
    print(effort)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
