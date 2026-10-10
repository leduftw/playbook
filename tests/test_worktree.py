import tempfile
import unittest
from pathlib import Path

from playbook.worktree import remove_empty_folders, slugify


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


if __name__ == "__main__":
    unittest.main()
