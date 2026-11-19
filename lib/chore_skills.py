"""Says which of a project's chores name a skill Claude Code can't find, since a chore runs `/<skill>` as its prompt:

    python3 chore_skills.py <checkout> <skill>...   prints a warning line for each skill that resolves to nothing

A name resolves to a skill folder holding SKILL.md, or a command file `<name>.md`, in the checkout's `.claude/`, in the
user's Claude Code folder (`$CLAUDE_CONFIG_DIR`, or `~/.claude`), or in a plugin. A plugin's skills count by
`<plugin>:<name>`, and by the bare name too, which Claude Code also runs. The plugins are the folders in
CLAUDE_CODE_PLUGIN_DIRS, from the environment or the user's settings.json, and the installed plugins enabled for the
user or for the checkout. It always exits 0, so `agent-desk chores status` and `agent-desk doctor` can show the
warnings beside their own output. docs/reference.md's Chores section says where the optional chore skills come
from.
"""

import json
import os
import sys
from pathlib import Path


def config_dir():
    return Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))


def has_skill(folder, name):
    """Whether a .claude-style folder holds the skill or command `name`."""
    return (folder / "skills" / name / "SKILL.md").is_file() or (folder / "commands" / f"{name}.md").is_file()


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def plugin_name(folder):
    manifest = read_json(folder / ".claude-plugin" / "plugin.json")
    name = manifest.get("name") if isinstance(manifest, dict) else None
    return name if isinstance(name, str) else folder.name


def plugin_dirs(checkout, home):
    """Each plugin Claude Code loads in the checkout, as (name, folder)."""
    entries = os.environ.get("CLAUDE_CODE_PLUGIN_DIRS", "").split(":")
    settings = read_json(home / "settings.json")
    env = settings.get("env") if isinstance(settings, dict) else None
    if isinstance(env, dict) and isinstance(env.get("CLAUDE_CODE_PLUGIN_DIRS"), str):
        entries += env["CLAUDE_CODE_PLUGIN_DIRS"].split(":")
    folders = [Path(os.path.expanduser(entry.strip())) for entry in entries if entry.strip()]
    plugins = [(plugin_name(folder), folder) for folder in folders]
    installed = read_json(home / "plugins" / "installed_plugins.json")
    listed = installed.get("plugins") if isinstance(installed, dict) else None
    here = Path(checkout).resolve()
    for key, installs in (listed or {}).items():
        for install in installs if isinstance(installs, list) else []:
            if not isinstance(install, dict) or not install.get("installPath"):
                continue
            project = install.get("projectPath")
            if install.get("scope") == "user" or (project and Path(project).resolve() == here):
                plugins.append((key.split("@")[0], Path(install["installPath"])))
    return plugins


def resolves(skill, checkout, home=None):
    """Whether Claude Code run in the checkout finds `/<skill>`."""
    home = home or config_dir()
    plugin, _, name = skill.rpartition(":")
    if not plugin and (has_skill(Path(checkout) / ".claude", name) or has_skill(home, name)):
        return True
    return any(has_skill(folder, name) for found, folder in plugin_dirs(checkout, home) if found == plugin or not plugin)


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    checkout, skills = argv[1], argv[2:]
    for skill in skills:
        if not resolves(skill, checkout):
            print(f"Warning: Claude Code finds no skill named {skill}, so the {skill} chore fails each run. Install "
                  "it or take it out of chores.every; docs/reference.md's Chores section says where chore skills "
                  "come from.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
