"""Says which of a project's chores name a skill Claude Code can't find, since a chore runs `/<skill>` as its prompt:

    python3 chore_skills.py <checkout> <skill>...       prints a warning line for each skill that resolves to nothing
    python3 chore_skills.py --file <checkout> <skill>   prints {"path": <file>} for the file the skill resolves to, or
                                                        {"warning": <line>} for one that resolves to nothing

A name resolves to a skill folder holding SKILL.md, or a command file `<name>.md`, in the checkout's `.claude/`, in the
user's Claude Code folder (`$CLAUDE_CONFIG_DIR`, or `~/.claude`), or in a plugin. A plugin's skills count by
`<plugin>:<name>`, and by the bare name too, which Claude Code also runs. The plugins are the folders in
CLAUDE_CODE_PLUGIN_DIRS, from the environment or the user's settings.json, and the installed plugins enabled for the
user or for the checkout. It always exits 0, so `agent-desk chores status` and `agent-desk doctor` can show the
warnings beside their own output, and the web page's chore panel can show the file or the warning.
docs/reference.md's Chores section says where the optional chore skills come from.
"""

import json
import os
import sys
from pathlib import Path


def config_dir():
    return Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))


def skill_in(folder, name):
    """The SKILL.md or command file of `name` in a .claude-style folder, the skill first, or None."""
    for path in (folder / "skills" / name / "SKILL.md", folder / "commands" / f"{name}.md"):
        if path.is_file():
            return path
    return None


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


def skill_file(skill, checkout, home=None):
    """The file Claude Code run in the checkout reads for `/<skill>`, or None when it finds none."""
    home = home or config_dir()
    plugin, _, name = skill.rpartition(":")
    if not name or name in (".", "..") or "/" in name:
        return None
    folders = [] if plugin else [Path(checkout) / ".claude", home]
    folders += [folder for found, folder in plugin_dirs(checkout, home) if found == plugin or not plugin]
    return next((path for path in (skill_in(folder, name) for folder in folders) if path), None)


def resolves(skill, checkout, home=None):
    """Whether Claude Code run in the checkout finds `/<skill>`."""
    return skill_file(skill, checkout, home) is not None


def warning(skill):
    return (f"Warning: Claude Code finds no skill named {skill}, so the {skill} chore fails each run. Install it or "
            "take it out of chores.every; docs/reference.md's Chores section says where chore skills come from.")


def main(argv):
    if len(argv) == 4 and argv[1] == "--file":
        found = skill_file(argv[3], argv[2])
        print(json.dumps({"path": str(found)} if found else {"warning": warning(argv[3])}))
        return 0
    if len(argv) < 2 or argv[1].startswith("--"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    checkout, skills = argv[1], argv[2:]
    for skill in skills:
        if not resolves(skill, checkout):
            print(warning(skill))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
