import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from playbook.ci.installers import env_prefix, render_ps1, render_sh
from tests.fixtures import DBIRD, MONAD, config


class RenderTest(unittest.TestCase):
    def test_every_placeholder_is_filled(self):
        for fixture in (DBIRD, MONAD):
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
        for fixture in (DBIRD, MONAD):
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


if __name__ == "__main__":
    unittest.main()
