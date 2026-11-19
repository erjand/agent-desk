"""Checks every `agent-desk issue` and `agent-desk review` command the skills give agents against lib/store_cli.py's
parser, so a skill that names a verb or flag the store lacks fails the tests."""

import io
import re
import shlex
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import store_cli  # noqa: E402
from store import ISSUE_FIELDS, REVIEW_FIELDS  # noqa: E402

SKILLS = sorted([*ROOT.glob("inbox/skills/*/SKILL.md"), *ROOT.glob(".agent-skills/*/SKILL.md")])
FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.S | re.M)
SPAN = re.compile(r"`([^`]+)`")
COMMAND = re.compile(r"agent-desk (?:issue|review) \S")
# An optional part such as [--label serial | --label serial:<group>]: its brackets hold flags, and | separates the
# alternatives.
OPTIONAL = re.compile(r"\[(--[^\]]*)\]")


def commands(text):
    """Each agent-desk issue or review command in a skill: inline code spans, and lines of its code blocks, with a
    backslash continuing a line."""
    found = []
    for block in FENCE.findall(text):
        for line in block.replace("\\\n", " ").splitlines():
            line = " ".join(line.split())
            if COMMAND.match(line):
                found.append(line)
    for span in SPAN.findall(FENCE.sub("", text)):
        span = " ".join(span.split())
        if COMMAND.match(span):
            found.append(span)
    return found


def variants(command):
    """The argument lists one command stands for: without its optional parts, and with each alternative of each one.
    Placeholders such as <n> become 1, and a pipe ends the command. A command named in prose with no arguments, such
    as `agent-desk issue create`, stands for its help, so only its verb has to exist."""
    command = re.sub(r"<[^>]+>", "1", command)
    groups = [[" ".join(choice.split()) for choice in group.split("|")] for group in OPTIONAL.findall(command)]
    bare = OPTIONAL.sub("", command)
    lines = [bare] + [f"{bare} {choice}" for group in groups for choice in group]
    out = []
    for line in lines:
        lexer = shlex.shlex(line, posix=True, punctuation_chars="|")
        lexer.whitespace_split = True
        words = list(lexer)
        if "|" in words:
            words = words[:words.index("|")]
        out.append(words[1:] + (["--help"] if len(words) == 3 else []))
    return out


class SkillCommandsTest(unittest.TestCase):
    def test_every_skill_command_parses(self):
        checked = {}
        for skill in SKILLS:
            for command in commands(skill.read_text()):
                for argv in variants(command):
                    with self.subTest(skill=str(skill.relative_to(ROOT)), command=command, argv=argv):
                        stderr = io.StringIO()
                        try:
                            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                                store_cli.parser().parse_args(argv)
                        except SystemExit as done:
                            if done.code != 0:
                                self.fail(f"{skill.relative_to(ROOT)}: `{command}` doesn't parse: "
                                          f"{stderr.getvalue().strip()}")
                        checked[" ".join(argv[:2])] = True
        # The search found the commands the skills are known to give, so a change to how they're written can't leave
        # this test checking nothing.
        for one in ("issue view", "issue list", "issue create", "issue comment", "issue edit", "review list",
                    "review view", "review create", "review comment", "review edit", "review checks",
                    "review ready"):
            self.assertIn(one, checked)

    def test_every_skill_json_field_exists(self):
        named = set()
        for skill in SKILLS:
            for command in commands(skill.read_text()):
                for argv in variants(command):
                    if "--json" not in argv:
                        continue
                    known = ISSUE_FIELDS if argv[0] == "issue" else REVIEW_FIELDS
                    for field in argv[argv.index("--json") + 1].split(","):
                        named.add(field)
                        with self.subTest(skill=str(skill.relative_to(ROOT)), command=command):
                            self.assertIn(field, known)
        # The fields the close-parent skill reads to follow a duplicate to its original.
        self.assertTrue({"stateReason", "duplicateOf", "closingIssuesReferences"} <= named)

    def test_a_wrong_flag_fails(self):
        text = "Read it with `agent-desk issue view <n> --commments`."
        self.assertEqual(commands(text), ["agent-desk issue view <n> --commments"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            store_cli.parser().parse_args(variants(commands(text)[0])[0])

    def test_commands_come_from_spans_and_code_blocks(self):
        text = ("Use `agent-desk issue` and `agent-desk review` for issues. List them with `agent-desk issue\n"
                "list --search \"<term>\"`.\n\n```bash\nagent-desk issue list --label audit \\\n"
                "  | jq '[.[] | select(.x)]'\nagent-desk review ready <review>\n```\n\n"
                "   ```markdown\n   A note `with a span\n   ```\n")
        self.assertEqual(commands(text), ["agent-desk issue list --label audit | jq '[.[] | select(.x)]'",
                                          "agent-desk review ready <review>",
                                          'agent-desk issue list --search "<term>"'])
        self.assertEqual(variants(commands(text)[0]), [["issue", "list", "--label", "audit"]])

    def test_a_command_named_without_arguments_needs_only_its_verb(self):
        self.assertEqual(variants("agent-desk issue create"), [["issue", "create", "--help"]])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as done:
            store_cli.parser().parse_args(variants("agent-desk issue publish")[0])
        self.assertEqual(done.exception.code, 2)

    def test_optional_parts_expand_to_each_alternative(self):
        command = "agent-desk issue create --title \"<title>\" [--label serial | --label serial:<group>] [--parent <n>]"
        self.assertEqual(variants(command), [
            ["issue", "create", "--title", "1"],
            ["issue", "create", "--title", "1", "--label", "serial"],
            ["issue", "create", "--title", "1", "--label", "serial:1"],
            ["issue", "create", "--title", "1", "--parent", "1"],
        ])


if __name__ == "__main__":
    unittest.main()
