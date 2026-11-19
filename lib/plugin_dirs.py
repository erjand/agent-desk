"""Adds a checkout's inbox folder to CLAUDE_CODE_PLUGIN_DIRS in a Claude Code settings file.

    python3 plugin_dirs.py <settings.json> <inbox folder>

Claude Code splits the variable on `:`, trims each entry, and expands a leading `~`. An entry that already resolves to
the folder leaves the file alone. An entry that names another agent-desk inbox, one whose `.claude-plugin/plugin.json`
is named agent-desk, gives way to the folder in its place, since two copies of the plugin can't load together.
Otherwise the folder joins the end. Settings that enable an agent-desk plugin installed from a marketplace, such as
`agent-desk@agent-desk`, keep the variable as it is, since that plugin already loads the pane and skills. The file is rewritten through any symlink, in Claude Code's own format of two-space
indents, and only when its value changes. It prints one line saying what it did.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

VARIABLE = "CLAUDE_CODE_PLUGIN_DIRS"


def resolve(entry):
    return Path(os.path.expanduser(entry)).resolve()


def is_agent_desk(folder):
    try:
        manifest = json.loads((folder / ".claude-plugin" / "plugin.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "agent-desk"


def updated(value, inbox):
    """The variable's new value and what changed: "ok", "added", or "replaced <entry>"."""
    entries = [entry.strip() for entry in value.split(":") if entry.strip()]
    target = inbox.resolve()
    if any(resolve(entry) == target for entry in entries):
        return value, "ok"
    for index, entry in enumerate(entries):
        if is_agent_desk(resolve(entry)):
            entries[index] = str(inbox)
            return ":".join(entries), f"replaced {entry}"
    return ":".join(entries + [str(inbox)]), "added"


def installed(settings):
    """The enabled plugin id, such as agent-desk@agent-desk, of an agent-desk plugin installed from a marketplace."""
    enabled = settings.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return None
    return next((key for key, on in enabled.items() if key.split("@")[0] == "agent-desk" and "@" in key and on is True),
                None)


def write(path, settings):
    """Writes the settings to the file a symlink points at, leaving the link in place."""
    real = path.resolve()
    real.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    handle, temporary = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        if real.exists():
            os.chmod(temporary, real.stat().st_mode & 0o7777)
        os.replace(temporary, real)
    except BaseException:
        os.unlink(temporary)
        raise


def main(path, inbox):
    path, inbox = Path(path), Path(inbox).resolve()
    try:
        settings = json.loads(path.read_text())
    except FileNotFoundError:
        settings = {}
    except ValueError as error:
        print(f"{path} isn't valid JSON ({error}), so {VARIABLE} was left alone.", file=sys.stderr)
        return 1
    env = settings.setdefault("env", {}) if isinstance(settings, dict) else None
    if not isinstance(env, dict):
        print(f"{path} has no env object, so {VARIABLE} was left alone.", file=sys.stderr)
        return 1
    plugin = installed(settings)
    if plugin is not None:
        print(f"ok      {plugin} is installed from a marketplace, so {VARIABLE} in {path} was left alone")
        duplicates = [entry for entry in str(env.get(VARIABLE, "")).split(":")
                      if entry.strip() and is_agent_desk(resolve(entry.strip()))]
        if duplicates:
            print(f"note    {VARIABLE} also loads {', '.join(entry.strip() for entry in duplicates)}, and two copies "
                  f"of the plugin can't load together. Remove that entry, or run `claude plugin uninstall {plugin}`.")
        return 0
    value, change = updated(str(env.get(VARIABLE, "")), inbox)
    if change == "ok":
        print(f"ok      {VARIABLE} in {path} has {inbox}")
        return 0
    env[VARIABLE] = value
    write(path, settings)
    if change == "added":
        print(f"added   {inbox} to {VARIABLE} in {path}")
    else:
        print(f"updated {VARIABLE} in {path}: {change.removeprefix('replaced ')} -> {inbox}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: plugin_dirs.py <settings.json> <inbox folder>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
