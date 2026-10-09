import re
import tempfile
import unittest
from pathlib import Path

from playbook import ROOT, major_ref, render
from tests.fixtures import DBIRD, LOCAL, MONAD, config


class ManagedFilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_local_repo_gets_checks_but_no_release_parts(self):
        files = render.managed_files(self.root, config(LOCAL))
        workflow = files[".github/workflows/playbook.yml"]
        self.assertIn(f"leduftw/playbook/.github/workflows/pipeline.yml@{major_ref()}", workflow)
        self.assertIn(f"playbook-ref: {major_ref()}", workflow)
        self.assertNotIn("registry:", workflow)
        self.assertNotIn(".github/release.yml", files)
        self.assertNotIn("@@", "".join(files.values()))

    def test_callers_grant_everything_their_workflow_asks_for(self):
        # GitHub refuses to start a run when the called workflow, or any job in
        # it, asks for more than the caller grants, even a job that's skipped.
        levels = {"none": 0, "read": 1, "write": 2}

        def permissions(text):
            found = {}
            for scope, level in re.findall(r"^\s+([a-z-]+): (read|write)$", text, re.M):
                found[scope] = max(found.get(scope, 0), levels[level])
            return found

        files = render.managed_files(self.root, config(DBIRD))
        pairs = {
            ".github/workflows/playbook.yml": "pipeline.yml",
            ".github/workflows/playbook-title.yml": "title.yml",
        }
        for caller, called in pairs.items():
            with self.subTest(caller=caller):
                wanted = permissions((ROOT / ".github" / "workflows" / called).read_text())
                job = files[caller].split("    uses: ", 1)[1]
                granted = permissions(job.split("\n  registry:", 1)[0])
                self.assertTrue(wanted)
                for scope, level in wanted.items():
                    self.assertGreaterEqual(granted.get(scope, 0), level, f"{caller} must grant {scope}")

    def test_published_repo_with_a_registry_gets_the_registry_job(self):
        files = render.managed_files(self.root, config(DBIRD))
        workflow = files[".github/workflows/playbook.yml"]
        self.assertIn("id-token: write", workflow)
        self.assertIn("attestations: write", workflow)
        self.assertIn(f"leduftw/playbook/.github/actions/registry@{major_ref()}", workflow)
        self.assertIn(".github/release.yml", files)

    def test_the_playbook_calls_its_own_workflows_from_the_same_commit(self):
        files = render.managed_files(self.root, config(LOCAL), self_repo=True)
        workflow = files[".github/workflows/playbook.yml"]
        self.assertIn("uses: ./.github/workflows/pipeline.yml", workflow)
        self.assertIn("github.event.pull_request.head.sha || github.sha", workflow)

    def test_title_workflow_runs_on_title_edits(self):
        files = render.managed_files(self.root, config(LOCAL))
        self.assertIn("types: [opened, edited, reopened, synchronize]", files[".github/workflows/playbook-title.yml"])

    def test_dependabot_covers_each_stack_and_ignores_the_playbook(self):
        (self.root / "leaderboard").mkdir()
        text = render.dependabot(self.root, config(DBIRD))
        self.assertIn("package-ecosystem: cargo", text)
        self.assertIn('package-ecosystem: npm\n    directory: "/leaderboard"', text)
        self.assertIn("package-ecosystem: github-actions", text)
        self.assertIn('dependency-name: "leduftw/playbook*"', text)

    def test_python_without_dependencies_has_no_pip_updates(self):
        text = render.dependabot(self.root, config(LOCAL))
        self.assertNotIn("pip", text)
        (self.root / "requirements.txt").write_text("requests\n")
        self.assertIn("package-ecosystem: pip", render.dependabot(self.root, config(LOCAL)))


class AgentsTest(unittest.TestCase):
    def test_section_is_appended_once_and_replaced_in_place(self):
        local = config(LOCAL)
        original = "# media-library\n\nRepo-specific notes.\n"
        first = render.agents_md(original, local)
        self.assertTrue(first.startswith(original.rstrip("\n")))
        self.assertEqual(first.count(render.BEGIN), 1)
        again = render.agents_md(first, local)
        self.assertEqual(again, first)

        edited = first.replace("## How work lands here", "## Stale heading") + "\nMore notes after.\n"
        restored = render.agents_md(edited, local)
        self.assertIn("## How work lands here", restored)
        self.assertNotIn("Stale heading", restored)
        self.assertTrue(restored.endswith("More notes after.\n"))

    def test_published_section_explains_releasing(self):
        section = render.agents_section(config(MONAD))
        self.assertIn("### Releasing", section)
        self.assertIn("the Homebrew tap, WinGet, the one-line installers and NuGet", section)
        self.assertNotIn("### Publishing", section)

    def test_local_section_says_nothing_is_released(self):
        section = render.agents_section(config(LOCAL))
        self.assertIn("This repo is local", section)
        self.assertIn("~/Developer/.worktrees/media-library/", section)


if __name__ == "__main__":
    unittest.main()
