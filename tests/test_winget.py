import unittest

from playbook.ci.winget import architectures, layout_problem, manifest_path

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


if __name__ == "__main__":
    unittest.main()
