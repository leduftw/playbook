import tempfile
import unittest
from pathlib import Path

from playbook import topics
from playbook.util import Failure
from tests.fixtures import DBIRD, LOCAL, config


class TopicTest(unittest.TestCase):
    def test_local_repos_need_none(self):
        self.assertEqual(topics.topic_problems([], config(LOCAL)), [])

    def test_published_repos_need_some(self):
        self.assertEqual(len(topics.topic_problems([], config(DBIRD))), 1)

    def test_no_platform_topics_is_fine(self):
        self.assertEqual(topics.topic_problems(["game", "rust"], config(DBIRD)), [])

    def test_platform_topics_must_cover_every_target(self):
        problems = topics.topic_problems(["audio", "windows"], config(DBIRD))
        self.assertEqual(len(problems), 1)
        self.assertIn("macos, windows, linux", problems[0])

    def test_all_platform_topics_pass(self):
        self.assertEqual(topics.topic_problems(["game", "linux", "macos", "windows"], config(DBIRD)), [])

    def test_fewer_targets_mean_fewer_platforms(self):
        text = DBIRD.replace(
            '[release]\nstack = "rust"\n', '[release]\nstack = "rust"\ntargets = ["macos-arm64", "macos-x64"]\n'
        )
        self.assertEqual(topics.topic_problems(["macos"], config(text)), [])

    def test_names_follow_github_rules(self):
        topics.validate(["music-recognition", "c99"])
        for bad in (["Windows"], ["two words"], ["-x"], ["a" * 51], [f"t{i}" for i in range(21)]):
            with self.assertRaises(Failure):
                topics.validate(bad)


class BadgeTest(unittest.TestCase):
    def test_badges_must_point_at_existing_workflows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "playbook.yml").write_text("", encoding="utf-8")
            (root / "README.md").write_text(
                "[![CI](https://github.com/o/r/actions/workflows/ci.yml/badge.svg)](https://github.com/o/r/actions/workflows/ci.yml)\n"
                "[![p](https://github.com/o/r/actions/workflows/playbook.yml/badge.svg)]\n",
                encoding="utf-8",
            )
            self.assertEqual(topics.badge_problems(root), ["README.md links to a workflow that doesn't exist: ci.yml"])

    def test_no_readme_is_fine(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(topics.badge_problems(Path(temp)), [])


if __name__ == "__main__":
    unittest.main()
