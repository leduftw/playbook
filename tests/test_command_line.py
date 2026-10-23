import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook.ci import command_line

# A tool that follows every rule. Each test breaks one of them through FAULTS.
TOOL = """\
import os
import sys
import time
from pathlib import Path

FAULTS = {faults!r}
args = sys.argv[1:]

def say(text, code=0, stream=sys.stdout):
    if ("colors" in FAULTS and os.environ.get("FORCE_COLOR")
            and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"):
        text = "\\x1b[1m" + text + "\\x1b[0m"
    print(text, file=stream)
    sys.exit(code)

if not args:
    if "bare" in FAULTS:
        say("tool: no token", 1, sys.stderr)
    if "hangs" in FAULTS:
        time.sleep(30)
    if any(os.environ.get(name) for name in ("TOOL_TOKEN", "AWS_ACCESS_KEY_ID", "GOOGLE_APPLICATION_CREDENTIALS")):
        time.sleep(30)  # it would start working, which needs a terminal
    for variable in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                     "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        if (Path(os.environ.get(variable, ".")) / "tool-token").is_file():
            say("tool: inherited a config token", 1, sys.stderr)
    for variable in ("XDG_CONFIG_DIRS", "XDG_DATA_DIRS"):
        for directory in os.environ.get(variable, "").split(os.pathsep):
            if (Path(directory) / "tool-token").is_file():
                say("tool: inherited a shared config token", 1, sys.stderr)
    say("tool help\\nTo start, set TOOL_TOKEN.")
if args in (["-h"], ["--help"]) or (args == ["help"] and "help word" not in FAULTS):
    say("tool help")
if args in (["-V"], ["--version"], ["version"]):
    say("1.2.3" if "bare version" in FAULTS else "tool 1.2.3")
if args == ["update", "--help"] or (args == ["upgrade", "--help"] and "alias" not in FAULTS):
    say("update help")
if args == ["upgrade", "--help"]:
    say("something else")
say("tool: unknown argument", 1 if "exit 1" in FAULTS else 2, sys.stderr)
"""


@unittest.skipIf(os.name == "nt", "the fake tool is a script with a shebang")
class CommandLineCheckTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def problems(self, *faults: str) -> list[str]:
        tool = Path(self.temp.name) / "tool"
        tool.write_text(f"#!{sys.executable}\n" + TOOL.format(faults=set(faults)), encoding="utf-8")
        tool.chmod(0o755)
        return command_line.check(tool, "tool", "1.2.3", timeout=5)

    def assertProblem(self, problems: list[str], words: str):
        self.assertTrue(any(words in problem for problem in problems), f"{words!r} not in {problems}")

    def test_a_tool_that_follows_the_rules_passes(self):
        self.assertEqual(self.problems(), [])

    def test_the_version_must_name_the_tool(self):
        self.assertProblem(self.problems("bare version"), "`tool -V` printed '1.2.3', not 'tool 1.2.3'")

    def test_a_mistake_must_exit_2(self):
        self.assertProblem(self.problems("exit 1"), "`tool --not-an-option-of-this-tool` exited 1, not 2")

    def test_the_help_word_must_work(self):
        problems = self.problems("help word")
        self.assertProblem(problems, "`tool help` exited 2, not 0")
        self.assertProblem(problems, "print different help")

    def test_upgrade_must_be_update(self):
        self.assertProblem(self.problems("alias"), "`tool upgrade` isn't the same command as `tool update`")

    def test_a_bare_run_must_exit_0(self):
        self.assertProblem(self.problems("bare"), "`tool` exited 1, not 0")

    def test_a_bare_run_must_finish(self):
        self.assertProblem(self.problems("hangs"), "`tool` was still running after 5 seconds")

    def test_a_bare_run_gets_no_credentials(self):
        # The fake would start working with a token; the check must hide it.
        with mock.patch.dict(
            os.environ,
            {
                "TOOL_TOKEN": "secret",
                "AWS_ACCESS_KEY_ID": "secret",
                "GOOGLE_APPLICATION_CREDENTIALS": "secret",
            },
        ):
            self.assertEqual(self.problems(), [])

    def test_a_bare_run_cannot_read_inherited_profile_or_config_directories(self):
        inherited = Path(self.temp.name) / "inherited"
        inherited.mkdir()
        (inherited / "tool-token").write_text("a planted token", encoding="utf-8")
        locations = (
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "XDG_CONFIG_HOME",
            "XDG_CONFIG_DIRS",
            "XDG_DATA_HOME",
            "XDG_DATA_DIRS",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
        )
        with mock.patch.dict(os.environ, dict.fromkeys(locations, str(inherited))):
            self.assertEqual(self.problems(), [])

    def test_color_codes_never_reach_a_pipe(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "dumb"}):
            self.assertProblem(self.problems("colors"), "printed color codes to stdout, which isn't a terminal")


if __name__ == "__main__":
    unittest.main()
