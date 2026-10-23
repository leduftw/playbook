import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from playbook import cli, version
from playbook.util import Failure

COMMANDS = ("start", "finish", "release", "new", "sync", "settings", "audit", "help", "version", "ci")


def run(*argv: str, stdout_tty: bool = False, stderr_tty: bool = False) -> tuple[int, str, str]:
    """Runs the command as a shell would and returns its exit code, stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        mock.patch.object(out, "isatty", return_value=stdout_tty),
        mock.patch.object(err, "isatty", return_value=stderr_tty),
    ):
        try:
            code = cli.main(list(argv))
        except SystemExit as stop:
            code = stop.code or 0
    return code, out.getvalue(), err.getvalue()


def forced_colors(**extra: str):
    """An environment where Python colors output even though it isn't a terminal."""
    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "PYTHON_COLORS", "TERM")}
    return mock.patch.dict(os.environ, {**env, "FORCE_COLOR": "1", **extra}, clear=True)


class HelpTest(unittest.TestCase):
    def test_flags_the_help_word_and_no_arguments_print_the_same_help(self):
        code, expected, _ = run("--help")
        self.assertEqual(code, 0)
        self.assertIn("usage: playbook", expected)
        for argv in ([], ["-h"], ["help"]):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv), (0, expected, ""))

    def test_help_of_one_command(self):
        code, expected, _ = run("start", "--help")
        self.assertEqual(code, 0)
        self.assertEqual(run("help", "start"), (0, expected, ""))

    def test_each_command_is_listed_once(self):
        lines = [line.split() for line in run("--help")[1].splitlines()]
        for name in COMMANDS:
            with self.subTest(name=name):
                self.assertEqual(sum(1 for words in lines if words[:1] == [name]), 1)

    def test_the_description_is_the_repos_description(self):
        self.assertIn("one standard for how my repos are worked on, checked and released\n", run("--help")[1])


class VersionTest(unittest.TestCase):
    def test_flags_and_the_version_word_print_the_name_and_version(self):
        for argv in (["-V"], ["--version"], ["version"]):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv), (0, f"playbook {version()}\n", ""))


class PipelineTest(unittest.TestCase):
    def test_pipeline_steps_keep_their_own_version_value(self):
        # The pipeline passes `ci <step> --version X`; the root's -V/--version must not take it.
        args = cli.parser().parse_args(["ci", "build", "--target", "linux-x64", "--version", "1.2.3", "--out", "dist"])
        self.assertEqual((args.step, args.target, args.version, args.out), ("build", "linux-x64", "1.2.3", "dist"))


class MistakeTest(unittest.TestCase):
    def test_wrong_commands_options_and_values_exit_2_with_a_message_on_stderr(self):
        for argv in (["bogus"], ["--bogus"], ["help", "bogus"], ["release", "huge"], ["start"], ["start", "x"]):
            with self.subTest(argv=argv):
                code, out, err = run(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("error:", err)

    def test_a_command_that_runs_and_fails_exits_1(self):
        with mock.patch("playbook.audit.audit_files", side_effect=Failure("cannot read the checkout")):
            code, out, err = run("audit", "--here")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("playbook: cannot read the checkout", err)

    def test_invalid_new_options_and_values_exit_2_before_accessing_github(self):
        base = ["new", "tool", "--stack", "rust", "--description", "a tool"]
        cases = [
            base,
            [*base, "--local", "--published"],
            [*base, "--published"],
            [*base, "--local", "--description", "A tool"],
            [*base, "--local", "--description", "a tool."],
            [*base, "--local", "--topics", "Uppercase"],
            [*base, "--published", "--topics", "rust", "--private"],
            [*base, "--published", "--topics", "python", "--stack", "python"],
            ["new", "Bad-Name", "--local", "--stack", "rust", "--description", "a tool"],
        ]
        with mock.patch("playbook.new.gh", side_effect=AssertionError("invalid input must not reach GitHub")):
            for argv in cases:
                with self.subTest(argv=argv):
                    code, out, err = run(*argv)
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn("error:", err)

    @unittest.skipUnless(sys.version_info >= (3, 14), "argparse suggests fixes from Python 3.14 on")
    def test_a_typo_gets_a_suggestion(self):
        self.assertIn("maybe you meant 'start'?", run("strat")[2])


@unittest.skipUnless(sys.version_info >= (3, 14), "argparse colors its output from Python 3.14 on")
class ColorTest(unittest.TestCase):
    def test_help_is_colored_where_colors_are_allowed(self):
        with forced_colors():
            self.assertIn("\x1b[", run("--help", stdout_tty=True)[1])

    def test_no_color_turns_colors_off_wherever_it_appears(self):
        with forced_colors():
            for argv in (["--no-color", "--help"], ["--help", "--no-color"], ["audit", "--no-color", "-h"]):
                with self.subTest(argv=argv):
                    code, out, _ = run(*argv, stdout_tty=True)
                    self.assertEqual(code, 0)
                    self.assertNotIn("\x1b[", out)

    def test_the_no_color_variable_turns_colors_off(self):
        with forced_colors(NO_COLOR="1"):
            self.assertNotIn("\x1b[", run("--help", stdout_tty=True)[1])

    def test_overrides_do_not_color_redirected_output(self):
        for env in ({}, {"PYTHON_COLORS": "1"}):
            with self.subTest(env=env), forced_colors(**env):
                self.assertNotIn("\x1b[", run("--help")[1])
                code, _, err = run("bogus", stdout_tty=True)
                self.assertEqual(code, 2)
                self.assertNotIn("\x1b[", err)

    def test_no_color_and_dumb_terminal_override_python_color_settings(self):
        for env in ({"NO_COLOR": "1"}, {"TERM": "dumb"}):
            with self.subTest(env=env), forced_colors(PYTHON_COLORS="1", **env):
                self.assertNotIn("\x1b[", run("--help", stdout_tty=True)[1])
                self.assertNotIn("\x1b[", run("bogus", stdout_tty=True, stderr_tty=True)[2])


if __name__ == "__main__":
    unittest.main()
