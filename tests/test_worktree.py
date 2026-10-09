import unittest

from playbook.worktree import slugify


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


if __name__ == "__main__":
    unittest.main()
