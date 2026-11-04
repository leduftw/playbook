import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook import settings
from tests.fixtures import DBIRD, config


class ReleaseSetupTest(unittest.TestCase):
    def missing(self, environment, repository=frozenset()):
        with (
            mock.patch.object(settings, "environment_secrets", return_value=set(environment)),
            mock.patch.object(settings, "repository_secrets", return_value=set(repository)),
        ):
            return settings.missing_release_setup("leduftw/dbird", config(DBIRD))

    def test_a_homebrew_repo_needs_a_key_of_the_taps_app(self):
        [problem] = self.missing({"WINGET_TOKEN"})
        self.assertIn("HOMEBREW_TAP_APP_PRIVATE_KEY is missing from the release environment", problem)
        self.assertIn("https://github.com/settings/apps/leduftw-tap", problem)
        self.assertIn("playbook settings leduftw/dbird --tap-key <file>", problem)

    def test_the_old_deploy_key_no_longer_counts(self):
        [problem] = self.missing({"HOMEBREW_TAP_DEPLOY_KEY", "WINGET_TOKEN"})
        self.assertIn("HOMEBREW_TAP_APP_PRIVATE_KEY", problem)

    def test_nothing_is_missing_once_the_key_is_stored(self):
        self.assertEqual(self.missing({"HOMEBREW_TAP_APP_PRIVATE_KEY", "WINGET_TOKEN"}), [])


class ApplyTest(unittest.TestCase):
    def apply(self, tap_key):
        calls = []

        def gh_json(*args):
            path = args[1]
            if path == "repos/leduftw/dbird":
                return {"default_branch": "main", "private": False}
            if path.endswith("/deployment-branch-policies"):
                return {"branch_policies": [{"name": "main"}]}
            return []

        def run(command, **kwargs):
            calls.append((command, kwargs.get("input")))
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            mock.patch.object(settings, "gh_json", gh_json),
            mock.patch.object(settings, "_api"),
            mock.patch.object(settings, "find_ruleset", return_value=None),
            mock.patch.object(settings, "run", run),
            mock.patch.object(settings, "missing_release_setup", return_value=[]),
            mock.patch.object(settings, "say"),
        ):
            settings.apply("leduftw/dbird", config(DBIRD), tap_key=tap_key)
        return calls

    def test_a_tap_key_goes_into_the_release_environment(self):
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp)
        key = temp / "leduftw-tap.private-key.pem"
        key.write_text("-----BEGIN RSA PRIVATE KEY-----\nnot a real key\n-----END RSA PRIVATE KEY-----\n")
        calls = self.apply(key)
        self.assertIn(
            (
                ["gh", "secret", "set", "HOMEBREW_TAP_APP_PRIVATE_KEY", "--env", "release", "--repo", "leduftw/dbird"],
                key.read_text(),
            ),
            calls,
        )

    def test_without_a_key_nothing_is_stored_or_generated(self):
        self.assertEqual(self.apply(None), [])


if __name__ == "__main__":
    unittest.main()
