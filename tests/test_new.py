import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from playbook import new, stacks
from playbook.config import load, parse


class PublishedPythonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def start(self, published=True):
        new.scaffold(self.root, "my-tool", "python", published)
        config_file = self.root / ".github" / "playbook.toml"
        config_file.parent.mkdir()
        config_file.write_text(new.config_text("my-tool", published, "python", "a tool"))
        return load(self.root)

    def test_its_config_publishes_to_pypi(self):
        release = self.start().release
        self.assertEqual(release.stack, "python")
        self.assertEqual(release.channels, ["homebrew", "winget", "installers", "pypi"])

    def test_it_starts_with_an_entry_point_a_release_can_build(self):
        release = self.start().release
        self.assertEqual(stacks.python_entry_points(self.root, release), {"my-tool": ("my_tool.cli", "main")})
        self.assertTrue((self.root / "src" / "my_tool" / "cli.py").is_file())

    def test_its_checks_pass_from_the_first_commit(self):
        self.start()
        command = stacks.check_commands(self.root, self.start_check())["test"][0].split()
        result = subprocess.run(
            [sys.executable, *command[1:]], cwd=self.root, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def start_check(self):
        return load(self.root).checks[0]

    def test_it_starts_unreleased(self):
        self.assertEqual(stacks.read_version(self.root, self.start().release), "0.0.0")

    def test_a_local_repo_gets_no_entry_point(self):
        self.start(published=False)
        project = tomllib.loads((self.root / "pyproject.toml").read_text())
        self.assertNotIn("scripts", project["project"])
        self.assertFalse((self.root / "src" / "my_tool" / "cli.py").exists())

    def test_node_still_cant_be_published(self):
        with (
            mock.patch("playbook.new.gh", side_effect=AssertionError("must not reach GitHub")),
            self.assertRaisesRegex(new.UsageError, "publishing a node project isn't supported yet"),
        ):
            new.new("tool", True, False, "node", "a tool", ["node"])

    def test_every_release_stack_has_a_registry(self):
        for stack in ("rust", "dotnet", "python"):
            with self.subTest(stack=stack):
                text = new.config_text("tool", True, stack, "a tool")
                self.assertTrue(parse(tomllib.loads(text)).release.registry)


if __name__ == "__main__":
    unittest.main()
