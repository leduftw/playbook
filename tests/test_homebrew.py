import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from playbook.ci.homebrew import class_name, render
from tests.fixtures import DBIRD, MONAD, SUBTITLER, config

TARGETS = ("macos-arm64", "macos-x64", "linux-x64", "linux-arm64")


def urls(name):
    return {t: f"https://github.com/leduftw/{name}/releases/download/v1.0.0/{name}-{t}.zip" for t in TARGETS}


DIGESTS = {t: str(i) * 64 for i, t in enumerate(TARGETS)}


class FormulaTest(unittest.TestCase):
    def test_class_names(self):
        self.assertEqual(class_name("dbird"), "Dbird")
        self.assertEqual(class_name("media-library"), "MediaLibrary")

    def test_dbird_formula_covers_both_cpus_on_both_systems(self):
        text = render(config(DBIRD), "1.3.0", "leduftw/dbird", urls("dbird"), DIGESTS)
        self.assertIn('desc "Fully playable terminal recreation of the classic Flappy Bird game"', text)
        self.assertIn('homepage "https://github.com/leduftw/dbird"', text)
        self.assertEqual(text.count("if Hardware::CPU.arm?"), 2)
        self.assertIn('bin.install "dbird"', text)
        self.assertIn('assert_match version.to_s, shell_output("#{bin}/dbird --version")', text)
        self.assertNotIn("depends_on macos", text)

    def test_monad_formula_keeps_its_requirements(self):
        text = render(config(MONAD), "2.0.2", "leduftw/monad", urls("monad"), DIGESTS)
        self.assertIn("depends_on macos: :sonoma", text)
        self.assertIn('odie "monad needs macOS 14.2 or newer" if OS.mac? && MacOS.version < "14.2"', text)
        self.assertIn('depends_on "pulseaudio"', text)
        self.assertIn('bin.install "monad-audiotap" if OS.mac?', text)
        self.assertIn('pkgshare.install "monad.example.json"', text)
        self.assertIn('shell_output("#{bin}/monad version")', text)

    def test_dependencies_can_be_declared_per_system(self):
        text = render(config(SUBTITLER), "1.0.0", "leduftw/subtitler", urls("subtitler"), DIGESTS)
        macos = text.split("on_macos do", 1)[1].split("on_linux do", 1)[0]
        linux = text.split("on_linux do", 1)[1].split("def install", 1)[0]
        self.assertIn('depends_on "ffmpeg"', macos)
        self.assertIn('depends_on "ffmpeg"', linux)

    def test_a_python_program_stays_beside_its_runtime_behind_a_link(self):
        text = render(config(SUBTITLER), "1.2.3", "leduftw/subtitler", urls("subtitler"), DIGESTS)
        self.assertIn('libexec.install "subtitler", "subtitler-runtime-1.2.3"', text)
        self.assertIn('bin.install_symlink libexec/"subtitler"', text)
        self.assertNotIn('bin.install "subtitler"', text)
        self.assertIn('assert_match version.to_s, shell_output("#{bin}/subtitler --version")', text)

    def test_other_programs_still_go_straight_into_bin(self):
        text = render(config(DBIRD), "1.3.0", "leduftw/dbird", urls("dbird"), DIGESTS)
        self.assertNotIn("libexec", text)

    def test_a_linux_dependency_stays_off_macos(self):
        text = render(config(MONAD), "2.0.2", "leduftw/monad", urls("monad"), DIGESTS)
        macos = text.split("on_macos do", 1)[1].split("on_linux do", 1)[0]
        self.assertNotIn("pulseaudio", macos)

    @unittest.skipUnless(shutil.which("ruby"), "Ruby isn't installed")
    def test_formulae_are_valid_ruby(self):
        with tempfile.TemporaryDirectory() as temp:
            for fixture, name in ((DBIRD, "dbird"), (MONAD, "monad"), (SUBTITLER, "subtitler")):
                path = Path(temp) / f"{name}.rb"
                path.write_text(render(config(fixture), "1.0.0", f"leduftw/{name}", urls(name), DIGESTS))
                subprocess.run(["ruby", "-c", str(path)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
