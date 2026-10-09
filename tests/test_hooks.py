import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from playbook import ROOT

HOOKS = ROOT / "templates" / "githooks"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


class HooksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        shutil.copytree(HOOKS, self.repo / ".githooks")
        self.git("config", "core.hooksPath", ".githooks")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.repo, env=ENV, capture_output=True, text=True, check=check)

    def commit(self, message, check=True):
        return self.git("commit", "-q", "-m", message, check=check)

    def test_first_commit_on_main_is_allowed_and_later_ones_are_blocked(self):
        (self.repo / "a").write_text("a")
        self.git("add", "a")
        self.commit("start")
        (self.repo / "b").write_text("b")
        self.git("add", "b")
        result = self.commit("sneak onto main", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committing on main is blocked", result.stderr)

    def test_branches_can_commit_and_agent_files_stay_identical(self):
        (self.repo / "a").write_text("a")
        self.git("add", "a")
        self.commit("start")
        self.git("switch", "-q", "-c", "dev/test/1-change")
        (self.repo / "AGENTS.md").write_text("# rules\n")
        self.git("add", "AGENTS.md")
        self.commit("add agent instructions")
        self.assertEqual((self.repo / "CLAUDE.md").read_text(), "# rules\n")
        self.assertIn("CLAUDE.md", self.git("ls-files").stdout)

    def test_conflicting_agent_files_are_refused(self):
        (self.repo / "AGENTS.md").write_text("one\n")
        self.git("add", "AGENTS.md")
        self.commit("start")
        self.git("switch", "-q", "-c", "dev/test/2-change")
        (self.repo / "AGENTS.md").write_text("two\n")
        (self.repo / "CLAUDE.md").write_text("three\n")
        self.git("add", "AGENTS.md", "CLAUDE.md")
        result = self.commit("diverge", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both edited", result.stderr)

    def test_pre_push_blocks_main_but_allows_creating_it(self):
        hook = self.repo / ".githooks" / "pre-push"
        zero = "0" * 40
        sha = "1" * 40
        create = f"refs/heads/main {sha} refs/heads/main {zero}\n"
        update = f"refs/heads/main {sha} refs/heads/main {sha}\n"
        branch = f"refs/heads/x {sha} refs/heads/x {sha}\n"

        def run(lines):
            return subprocess.run(["sh", str(hook), "origin", "url"], input=lines, text=True, capture_output=True)

        self.assertEqual(run(create).returncode, 0)
        self.assertEqual(run(branch).returncode, 0)
        blocked = run(update)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("pushing to main is blocked", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
