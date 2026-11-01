import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook import stacks
from playbook.ci.build import EXECUTABLE, REGULAR, write_archive
from playbook.ci.installers import env_prefix, render_ps1, render_sh, smoke
from playbook.util import Failure
from tests.fixtures import DBIRD, MONAD, SUBTITLER, config


class RenderTest(unittest.TestCase):
    def test_every_placeholder_is_filled(self):
        for fixture in (DBIRD, MONAD, SUBTITLER):
            with self.subTest(fixture=fixture[:20]):
                self.assertNotIn("@@", render_sh(config(fixture), "leduftw/x"))
                self.assertNotIn("@@", render_ps1(config(fixture), "leduftw/x"))

    def test_monad_installer_checks_macos_and_installs_the_helper(self):
        text = render_sh(config(MONAD), "leduftw/monad")
        self.assertIn('minimum="14.2"', text)
        self.assertIn('binaries="$binaries monad-audiotap"', text)
        self.assertIn('installed=$("$install_dir/.monad.$$.new" version)', text)
        self.assertIn("Linux capture also needs parec", text)

    def test_windows_installer_lists_exe_names_and_targets(self):
        text = render_ps1(config(DBIRD), "leduftw/dbird")
        self.assertIn("$binaries = @('dbird.exe')", text)
        self.assertIn("$supported = @('windows-x64', 'windows-arm64')", text)
        self.assertIn("& $staged[0] '--version'", text)

    def test_env_prefix(self):
        self.assertEqual(env_prefix("media-library"), "MEDIA_LIBRARY")


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_shell_installer_parses_and_passes_shellcheck(self):
        for fixture in (DBIRD, MONAD, SUBTITLER):
            script = self.dir / "install.sh"
            script.write_text(render_sh(config(fixture), "leduftw/x"))
            subprocess.run(["sh", "-n", str(script)], check=True)
            if shutil.which("shellcheck"):
                result = subprocess.run(["shellcheck", "-s", "sh", str(script)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell isn't installed")
    def test_powershell_installer_parses(self):
        script = self.dir / "install.ps1"
        script.write_text(render_ps1(config(MONAD), "leduftw/monad"))
        command = (
            "$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script}', [ref]$null, [ref]$errors) | Out-Null; if ($errors) {{ $errors; exit 1 }}"
        )
        result = subprocess.run(["pwsh", "-NoProfile", "-Command", command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipIf(os.name == "nt", "the fake program is a shell script")
class RuntimeFolderTest(unittest.TestCase):
    """The real shell installer, served an archive the way a release's smoke test does."""

    VERSION = "1.2.3"
    PROGRAM = '#!/bin/sh\ncat "$(dirname "$0")/subtitler-runtime-1.2.3/version"\n'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.stage = self.work / "stage"
        self.stage.mkdir()
        self.config = config(SUBTITLER)
        system = {"Darwin": "macos", "Linux": "linux"}[platform.system()]
        cpu = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}[platform.machine().lower()]
        self.target = f"{system}-{cpu}"

    def archive(self, *runtime_files):
        (self.stage / "subtitler").write_text(self.PROGRAM)
        modes = {"subtitler": EXECUTABLE}
        for name in runtime_files:
            path = self.stage / f"subtitler-runtime-{self.VERSION}" / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"subtitler {self.VERSION}\n")
            modes[path.relative_to(self.stage).as_posix()] = REGULAR
        dist = self.work / "dist"
        dist.mkdir()
        write_archive(self.stage, modes, dist / stacks.archive_name("subtitler", self.target), 1_700_000_000)
        return dist

    def smoke(self, dist):
        with mock.patch("playbook.ci.installers.repository", return_value="leduftw/subtitler"):
            smoke(self.work, self.config, self.target, self.VERSION, dist)

    def test_an_upgrade_brings_the_runtime_and_tidies_away_the_older_one(self):
        # smoke() fails if the folder is missing, the older one stays, or a staged
        # file is left behind.
        self.smoke(self.archive("version"))

    def test_a_program_whose_runtime_didnt_arrive_is_never_installed(self):
        with self.assertRaises(Failure):
            self.smoke(self.archive())

    def test_a_program_with_no_runtime_installs_as_it_always_has(self):
        self.config = config(DBIRD)
        (self.stage / "dbird").write_text('#!/bin/sh\necho "dbird 1.2.3"\n')
        dist = self.work / "dist"
        dist.mkdir()
        write_archive(
            self.stage, {"dbird": EXECUTABLE}, dist / stacks.archive_name("dbird", self.target), 1_700_000_000
        )
        self.smoke(dist)


if __name__ == "__main__":
    unittest.main()
