import tempfile
import unittest
from pathlib import Path

from playbook import readme
from tests.fixtures import DBIRD, LOCAL, config

DESCRIPTION = "a tool that does one thing well"
SENTENCE = "A tool that does one thing well."


class ProblemsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def problems(self, text: str | None, repo: str = LOCAL, description: str | None = DESCRIPTION) -> list[str]:
        if text is not None:
            (self.root / "README.md").write_text(text, encoding="utf-8")
        return readme.problems(self.root, config(repo), description)

    def local(self, *lines: str) -> str:
        return "\n".join([f"# {config(LOCAL).name}", "", *lines, ""])

    def published(self, *headings: str) -> str:
        name = config(DBIRD).name
        opening = [f"# {name}", "", *readme.badges(name), "", SENTENCE, ""]
        return "\n".join([*opening, *[f"## {heading}\n\nText.\n" for heading in headings]])

    def test_a_missing_readme_is_reported(self):
        self.assertEqual(self.problems(None), ["README.md is missing"])

    def test_a_local_readme_needs_only_its_title_and_description(self):
        self.assertEqual(self.problems(self.local(SENTENCE)), [])

    def test_the_title_is_the_repos_name(self):
        name = config(LOCAL).name
        self.assertEqual(
            self.problems(f"# {name.title()}\n\n{SENTENCE}\n"),
            [f"README.md must start with the title `# {name}`, not `# {name.title()}`"],
        )

    def test_the_description_comes_first(self):
        self.assertEqual(
            self.problems(self.local("Something else first.", "", SENTENCE)),
            [f"README.md must open with the repo's description as a sentence: {SENTENCE}"],
        )

    def test_the_description_may_wrap_and_lead_into_more_text(self):
        self.assertEqual(self.problems(self.local("A tool that does", "one thing well. It also has more to say.")), [])

    def test_with_no_description_given_its_sentence_isnt_checked(self):
        self.assertEqual(self.problems(self.local("Anything at all."), description=None), [])

    def test_a_repo_without_a_description_is_reported(self):
        self.assertEqual(self.problems(self.local(SENTENCE), description=""), ["the repo has no GitHub description"])

    def test_a_published_readme_follows_the_standard(self):
        text = self.published("Install", "Usage", "Development", "License")
        self.assertEqual(self.problems(text, DBIRD), [])

    def test_a_published_readme_needs_both_badges(self):
        text = self.published("Install", "Usage", "Development", "License")
        for badge in readme.badges(config(DBIRD).name):
            with self.subTest(badge):
                self.assertEqual(
                    self.problems(text.replace(badge + "\n", ""), DBIRD),
                    ["README.md must show the CI and latest-release badges right after the title"],
                )

    def test_a_badge_with_the_old_label_is_reported(self):
        text = self.published("Install", "Usage", "Development", "License").replace("[![CI]", "[![playbook]")
        self.assertEqual(
            self.problems(text, DBIRD),
            ["README.md must show the CI and latest-release badges right after the title"],
        )

    def test_a_published_readme_needs_its_sections_in_order(self):
        wanted = ["a published README needs the sections Install, Usage, Development, License, in that order"]
        self.assertEqual(self.problems(self.published("Install", "Usage", "License"), DBIRD), wanted)
        self.assertEqual(self.problems(self.published("Usage", "Install", "Development", "License"), DBIRD), wanted)

    def test_other_sections_may_sit_between_them(self):
        text = self.published("Install", "How it works", "Usage", "Options", "Development", "License")
        self.assertEqual(self.problems(text, DBIRD), [])


class ScaffoldTest(unittest.TestCase):
    def test_new_repos_start_out_following_the_standard(self):
        for repo, published in ((LOCAL, False), (DBIRD, True)):
            with self.subTest(published=published), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                cfg = config(repo)
                text = readme.scaffold(cfg.name, DESCRIPTION, published)
                (root / "README.md").write_text(text, encoding="utf-8")
                self.assertEqual(readme.problems(root, cfg, DESCRIPTION), [])

    def test_a_local_repo_starts_with_only_the_opening(self):
        self.assertEqual(readme.scaffold("notes", "scripts for my notes", False), "# notes\n\nScripts for my notes.\n")


if __name__ == "__main__":
    unittest.main()
