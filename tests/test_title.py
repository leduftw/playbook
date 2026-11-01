"""Runs the PR title check and the Dependabot merge the way GitHub does, with a fake gh."""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from playbook import ROOT

PR = "https://github.com/o/r/pull/7"
FAKE_GH = """\
#!/bin/sh
printf '%s\\n' "$*" >> "$GH_CALLS"
case " $* " in
  *" --auto "*) ;;
  *) exit "${GH_EXIT:-0}" ;;
esac
"""


def script(workflow: str, step: str) -> str:
    """The shell script of one step in a workflow: its `run: |` block."""
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    lines = text.split(f"- name: {step}\n", 1)[1].split("run: |\n", 1)[1].splitlines()
    block = []
    for line in lines:
        if line.strip() and len(line) - len(line.lstrip()) < 10:
            break
        block.append(line)
    return textwrap.dedent("\n".join(block))


@unittest.skipIf(os.name == "nt", "the fake gh is a shell script")
class StepTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.log = folder / "calls"
        self.log.write_text("", encoding="utf-8")
        gh = folder / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)

    def step(self, workflow: str, step: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script(workflow, step)],
            env={"PATH": f"{self.temp.name}{os.pathsep}{os.environ['PATH']}", "GH_CALLS": str(self.log), **env},
            capture_output=True,
            text=True,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()


class TitleCheckTest(StepTest):
    def check(self, title: str, author: str = "leduftw") -> subprocess.CompletedProcess[str]:
        return self.step("title.yml", "Check the PR title", TITLE=title, AUTHOR=author, PR=PR)

    def test_a_capital_first_letter_passes(self):
        self.assertEqual(self.check("Fix Windows installer replacement").returncode, 0)

    def test_a_lowercase_first_letter_fails(self):
        result = self.check("fix Windows installer replacement")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start with a capital letter", result.stdout)

    def test_a_full_stop_fails(self):
        result = self.check("Fix Windows installer replacement.")
        self.assertEqual(result.returncode, 1)
        self.assertIn("drop the full stop", result.stdout)

    def test_a_type_prefix_fails(self):
        result = self.check("Fix(installer): replace it on Windows")
        self.assertEqual(result.returncode, 1)
        self.assertIn('drop the "type:" prefix', result.stdout)

    def test_dependabots_titles_are_fixed_in_place(self):
        for title in (
            "bump vite from 7 to 8",
            "npm(deps): bump vite from 7 to 8",
            "build(deps)!: Bump vite from 7 to 8",
        ):
            with self.subTest(title):
                self.log.write_text("", encoding="utf-8")
                self.assertEqual(self.check(title, author="dependabot[bot]").returncode, 0)
                self.assertEqual(self.calls(), [f"pr edit {PR} --title Bump vite from 7 to 8"])

    def test_a_good_dependabot_title_is_left_alone(self):
        self.assertEqual(self.check("Bump vite from 7 to 8", author="dependabot[bot]").returncode, 0)
        self.assertEqual(self.calls(), [])


class DependabotMergeTest(StepTest):
    def merge(self, title: str, **env: str) -> subprocess.CompletedProcess[str]:
        return self.step("pipeline.yml", "Merge minor and patch updates", TITLE=title, NUMBER="7", PR=PR, **env)

    def test_the_commit_keeps_the_commit_style(self):
        for title in ("Bump vite from 7 to 8", "bump vite from 7 to 8", "build(deps): Bump vite from 7 to 8"):
            with self.subTest(title):
                self.log.write_text("", encoding="utf-8")
                self.assertEqual(self.merge(title).returncode, 0)
                self.assertEqual(self.calls(), [f"pr merge --squash --subject bump vite from 7 to 8 (#7) {PR}"])

    def test_auto_merge_keeps_it_too(self):
        self.assertEqual(self.merge("Bump vite from 7 to 8", GH_EXIT="1").returncode, 0)
        self.assertEqual(self.calls()[-1], f"pr merge --auto --squash --subject bump vite from 7 to 8 (#7) {PR}")


if __name__ == "__main__":
    unittest.main()
