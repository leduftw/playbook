import unittest
from pathlib import Path
from unittest import mock

from playbook.ci import winget
from playbook.ci.winget import architectures, layout_problem, manifest_path, pull_request
from playbook.util import Failure
from tests.fixtures import SUBTITLER, config

OLD_DBIRD = """PackageIdentifier: leduftw.dbird
InstallerType: zip
NestedInstallerType: portable
NestedInstallerFiles:
- RelativeFilePath: dbird-v1.2.0-x86_64-pc-windows-msvc\\\\dbird.exe
  PortableCommandAlias: dbird
Installers:
- Architecture: x64
  InstallerUrl: https://example.com/dbird.zip
"""

MONAD = """NestedInstallerFiles:
- RelativeFilePath: monad.exe
  PortableCommandAlias: monad
Installers:
- Architecture: x64
- Architecture: arm64
"""


class WingetTest(unittest.TestCase):
    def test_manifest_path(self):
        self.assertEqual(manifest_path("leduftw.dbird"), "manifests/l/leduftw/dbird")

    def test_architectures(self):
        self.assertEqual(architectures(OLD_DBIRD), ["x64"])
        self.assertEqual(architectures(MONAD), ["arm64", "x64"])

    def test_a_manifest_matching_the_archive_layout_passes(self):
        self.assertIsNone(layout_problem(MONAD, "monad"))

    def test_an_old_archive_layout_is_caught_before_publishing(self):
        problem = layout_problem(OLD_DBIRD, "dbird")
        self.assertIn("dbird-v1.2.0-x86_64-pc-windows-msvc", problem)
        self.assertIn("RelativeFilePath: dbird.exe", problem)

    def test_finds_the_pull_request_wingetcreate_opened(self):
        output = "Submitting pull request for manifest...\nPull request can be found here: https://github.com/microsoft/winget-pkgs/pull/452784\n"
        self.assertEqual(pull_request(output), "https://github.com/microsoft/winget-pkgs/pull/452784")
        self.assertIsNone(pull_request("Submitting pull request for manifest...\n"))


class FirstReleaseTest(unittest.TestCase):
    """A package can't be on WinGet before its first release exists."""

    def setUp(self):
        self.config = config(SUBTITLER)
        patcher = mock.patch.object(winget, "newest_installer_manifest", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_first_release_only_warns_on_the_release_pr(self):
        with mock.patch.object(winget, "warn") as warn:
            winget.check(self.config, "1.0.0", "leduftw/subtitler")
        self.assertIn("wingetcreate new", warn.call_args.args[0])

    def test_a_later_release_of_a_missing_package_still_fails(self):
        with self.assertRaisesRegex(Failure, "isn't in winget-pkgs yet"):
            winget.check(self.config, "1.0.1", "leduftw/subtitler")

    def test_a_first_release_says_exactly_what_to_submit(self):
        with (
            mock.patch.dict(winget.os.environ, {"WINGET_TOKEN": "token"}),
            mock.patch.object(winget, "repository", return_value="leduftw/subtitler"),
            mock.patch.object(winget, "warn"),
            mock.patch.object(winget, "summary") as summary,
        ):
            winget.submit(Path("."), self.config, "1.0.0")
        message = summary.call_args.args[0]
        self.assertIn("/v1.0.0/subtitler-windows-x64.zip", message)
        self.assertIn("/v1.0.0/subtitler-windows-arm64.zip", message)

    def test_a_later_submission_of_a_missing_package_still_fails(self):
        with (
            mock.patch.dict(winget.os.environ, {"WINGET_TOKEN": "token"}),
            self.assertRaisesRegex(Failure, "submit the first version by hand"),
        ):
            winget.submit(Path("."), self.config, "1.0.1")


if __name__ == "__main__":
    unittest.main()
