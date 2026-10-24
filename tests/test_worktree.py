import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook.worktree import checkout_of, cleanup, remove_empty_folders, slugify

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


class SlugTest(unittest.TestCase):
    def test_titles_become_short_lowercase_slugs(self):
        self.assertEqual(
            slugify('Rename "master password" to "vault password"'), "rename-master-password-to-vault-password"
        )
        self.assertEqual(slugify("Ünïcode & symbols!!"), "unicode-symbols")
        self.assertEqual(slugify("!!!"), "change")
        self.assertEqual(slugify("pipeline doesn't start"), "pipeline-doesnt-start")

    def test_long_titles_are_cut_at_a_word_boundary(self):
        slug = slugify("Accept Binance Transfer rows and keep their Send dedup identity forever")
        self.assertLessEqual(len(slug), 40)
        self.assertFalse(slug.endswith("-"))
        self.assertTrue("accept-binance-transfer-rows".startswith(slug[:20]))


class RemoveEmptyFoldersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worktrees = Path(self.temp.name) / ".worktrees"

    def tearDown(self):
        self.temp.cleanup()

    def test_an_empty_repo_folder_and_root_are_removed(self):
        repo = self.worktrees / "dbird"
        repo.mkdir(parents=True)
        (self.worktrees / ".DS_Store").write_text("finder")
        remove_empty_folders(repo, self.worktrees)
        self.assertFalse(self.worktrees.exists())

    def test_folders_with_other_worktrees_stay(self):
        repo = self.worktrees / "dbird"
        (repo / "7-other-change").mkdir(parents=True)
        (self.worktrees / "monad").mkdir()
        remove_empty_folders(repo, self.worktrees)
        self.assertTrue((repo / "7-other-change").is_dir())
        remove_empty_folders(self.worktrees / "monad", self.worktrees)
        self.assertFalse((self.worktrees / "monad").exists())
        self.assertTrue(self.worktrees.is_dir())

    def test_nothing_outside_the_worktrees_folder_is_touched(self):
        elsewhere = Path(self.temp.name) / "elsewhere"
        elsewhere.mkdir()
        remove_empty_folders(elsewhere, self.worktrees)
        self.assertTrue(elsewhere.is_dir())


class CheckoutTest(unittest.TestCase):
    """finish finds the PR's branch by name, whichever checkout it was started from."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.root = base / "repo"
        self.worktrees = base / ".worktrees"
        self.enterContext(mock.patch.dict(os.environ, ENV))
        self.enterContext(mock.patch("playbook.worktree.WORKTREES", self.worktrees))
        self.git("init", "-q", "--bare", str(base / "origin.git"), cwd=base)
        self.git("init", "-q", "-b", "main", str(self.root), cwd=base)
        self.git("commit", "-q", "--allow-empty", "-m", "start")
        self.git("remote", "add", "origin", str(base / "origin.git"))
        self.git("push", "-q", "-u", "origin", "main")
        self.change = self.start("dev/test/1-change")
        self.other = self.start("dev/test/2-other")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", *args], cwd=cwd or self.root, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def start(self, branch):
        """A pushed branch in its own worktree, the way playbook start leaves it."""
        path = self.worktrees / "repo" / branch.rsplit("/", 1)[1]
        self.git("worktree", "add", "-q", "-b", branch, str(path))
        self.git("push", "-q", "origin", branch, cwd=path)
        return path

    def test_a_branch_is_found_wherever_it_is_checked_out(self):
        self.assertEqual(checkout_of(self.root, "dev/test/1-change"), self.change)
        self.assertEqual(checkout_of(self.root, "main"), self.root)
        self.git("branch", "dev/test/3-not-checked-out")
        self.assertIsNone(checkout_of(self.root, "dev/test/3-not-checked-out"))

    def test_cleanup_from_the_primary_checkout_removes_only_the_finished_change(self):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            cleanup(self.root, checkout_of(self.root, "dev/test/1-change"), "dev/test/1-change")
        self.assertFalse(self.change.exists())
        self.assertEqual(self.git("branch", "--list", "dev/test/1-change"), "")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", "dev/test/1-change"), "")
        self.assertEqual(checkout_of(self.root, "dev/test/2-other"), self.other)
        self.assertIn("deleted dev/test/1-change and pulled main", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
