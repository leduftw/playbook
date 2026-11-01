import os
import sys
import tempfile
import unittest
import urllib.error
from collections import namedtuple
from pathlib import Path
from unittest import mock

from playbook import stacks
from playbook.ci import build
from playbook.ci.build import (
    EXECUTABLE,
    REGULAR,
    check_binary,
    expected_files,
    extract,
    repo_description,
    smoke,
    write_archive,
)
from playbook.config import ConfigError
from playbook.util import Failure
from tests.fixtures import DBIRD, MONAD, PYPROJECT, SUBTITLER, config
from tests.test_command_line import TOOL


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.stage = self.work / "stage"
        self.stage.mkdir()
        (self.stage / "tool").write_bytes(b"#!/bin/sh\necho tool 1.0.0\n")
        (self.stage / "README.md").write_text("readme\n")
        self.modes = {"tool": EXECUTABLE, "README.md": REGULAR}

    def tearDown(self):
        self.temp.cleanup()

    def roundtrip(self, name):
        archive = self.work / name
        write_archive(self.stage, self.modes, archive, 1_700_000_000)
        out = self.work / f"out-{name}"
        extract(archive, out)
        return archive, out

    def test_zip_keeps_the_executable_bit(self):
        _, out = self.roundtrip("tool-macos-arm64.zip")
        self.assertTrue(os.access(out / "tool", os.X_OK))
        self.assertFalse(os.access(out / "README.md", os.X_OK))

    def test_tarball_keeps_the_executable_bit(self):
        _, out = self.roundtrip("tool-linux-x64.tar.gz")
        self.assertTrue(os.access(out / "tool", os.X_OK))

    def test_archives_are_reproducible(self):
        for name in ("a.zip", "a.tar.gz"):
            with self.subTest(name=name):
                first = self.work / f"1-{name}"
                second = self.work / f"2-{name}"
                write_archive(self.stage, self.modes, first, 1_700_000_000)
                write_archive(self.stage, self.modes, second, 1_700_000_000)
                self.assertEqual(first.read_bytes(), second.read_bytes())

    @unittest.skipIf(os.name == "nt", "a build made on Windows has no links")
    def test_a_runtime_folder_keeps_its_links_and_modes(self):
        runtime = self.stage / "tool-runtime-1.0.0"
        versions = runtime / "Python.framework" / "Versions"
        (versions / "3.14").mkdir(parents=True)
        (versions / "3.14" / "Python").write_bytes(b"a library")
        (versions / "3.14" / "Python").chmod(0o755)
        (runtime / "notes.txt").write_text("notes\n")
        (runtime / "Python").symlink_to("Python.framework/Versions/3.14/Python")
        (versions / "Current").symlink_to("3.14")
        self.modes |= build._modes_inside(self.stage, runtime.name)
        for name in ("tool-macos-arm64.zip", "tool-linux-x64.tar.gz"):
            with self.subTest(name=name):
                _, out = self.roundtrip(name)
                folder = out / runtime.name
                self.assertEqual(os.readlink(folder / "Python"), "Python.framework/Versions/3.14/Python")
                self.assertEqual((folder / "Python").read_bytes(), b"a library")
                self.assertEqual(os.readlink(folder / "Python.framework" / "Versions" / "Current"), "3.14")
                self.assertTrue(os.access(folder / "Python.framework" / "Versions" / "3.14" / "Python", os.X_OK))
                self.assertFalse(os.access(folder / "notes.txt", os.X_OK))

    @unittest.skipIf(os.name == "nt", "the fake tool is a script with a shebang")
    def test_smoke_rejects_an_archived_tool_that_breaks_the_command_line_rules(self):
        cfg = config(DBIRD)
        cfg.name = "tool"
        cfg.release.binaries = ["tool"]
        cfg.release.files = ["README.md"]
        (self.stage / "tool").write_text(f"#!{sys.executable}\n" + TOOL.format(faults={"exit 1"}), encoding="utf-8")
        archive = self.work / "tool-linux-x64.tar.gz"
        write_archive(self.stage, self.modes, archive, 1_700_000_000)
        # It is a script in this test; all the smoke test's process calls are real.
        with (
            mock.patch("playbook.ci.build.check_binary"),
            mock.patch("playbook.ci.build.repo_description", return_value="a tool that follows the rules"),
            self.assertRaisesRegex(Failure, r"`tool --not-an-option-of-this-tool` exited 1, not 2"),
        ):
            smoke(self.work, cfg, "linux-x64", "1.2.3", self.work)

    def python_archive(self, *folders):
        """A subtitler archive with the right files, and these folders beside them."""
        cfg = config(SUBTITLER)
        modes = dict.fromkeys(expected_files(cfg.release, "linux-x64"), REGULAR)
        for name in modes:
            (self.stage / name).write_text(name)
        for folder in folders:
            (self.stage / folder).mkdir()
            (self.stage / folder / "library").write_text("a library")
            modes[f"{folder}/library"] = REGULAR
        write_archive(self.stage, modes, self.work / "subtitler-linux-x64.tar.gz", 1_700_000_000)
        return cfg

    def test_smoke_runs_a_python_program_through_a_link_too(self):
        cfg = self.python_archive("subtitler-runtime-1.2.3")
        with (
            mock.patch("playbook.ci.build.check_binary"),
            mock.patch("playbook.ci.build.check_main_binary"),
            mock.patch("playbook.ci.build.check_found_through_a_link") as check,
        ):
            smoke(self.work, cfg, "linux-x64", "1.2.3", self.work)
        check.assert_called_once()

    def test_smoke_refuses_a_python_archive_without_its_runtime(self):
        cfg = self.python_archive()
        with self.assertRaisesRegex(Failure, "holds the wrong files"):
            smoke(self.work, cfg, "linux-x64", "1.2.3", self.work)

    def test_smoke_refuses_a_python_archive_with_another_versions_runtime(self):
        cfg = self.python_archive("subtitler-runtime-1.2.2")
        with self.assertRaisesRegex(Failure, "holds the wrong files"):
            smoke(self.work, cfg, "linux-x64", "1.2.3", self.work)

    def test_smoke_still_refuses_a_stray_folder_in_a_flat_archive(self):
        cfg = config(DBIRD)
        cfg.release.files = ["README.md"]
        cfg.name = "tool"
        cfg.release.binaries = ["tool"]
        (self.stage / "stray").mkdir()
        (self.stage / "stray" / "file").write_text("left over")
        self.modes["stray/file"] = REGULAR
        write_archive(self.stage, self.modes, self.work / "tool-linux-x64.tar.gz", 1_700_000_000)
        with self.assertRaisesRegex(Failure, "holds the wrong files"):
            smoke(self.work, cfg, "linux-x64", "1.2.3", self.work)

    @unittest.skipIf(os.name == "nt", "the fake tool is a script with a shebang")
    def test_a_program_that_only_works_from_its_own_folder_is_refused(self):
        cfg = config(SUBTITLER)
        tool = self.stage / "subtitler"
        tool.write_text('#!/bin/sh\ncat "$(dirname "$0")/runtime"\n')
        tool.chmod(0o755)
        (self.stage / "runtime").write_text("subtitler 1.2.3\n")
        with self.assertRaisesRegex(Failure, "exited 1"):
            build.check_found_through_a_link(tool, cfg, "1.2.3", self.work / "links")

    @unittest.skipIf(os.name == "nt", "the fake tool is a script with a shebang")
    def test_a_program_that_finds_itself_through_a_link_passes(self):
        cfg = config(SUBTITLER)
        tool = self.stage / "subtitler"
        tool.write_text(f'#!/bin/sh\ncat "{self.stage}/runtime"\n')
        tool.chmod(0o755)
        (self.stage / "runtime").write_text("subtitler 1.2.3\n")
        build.check_found_through_a_link(tool, cfg, "1.2.3", self.work / "links")

    def test_a_repo_needs_a_description_for_its_help_to_use(self):
        with (
            mock.patch("playbook.ci.build.repository", return_value="o/r"),
            mock.patch("playbook.ci.build.gh", return_value="") as gh,
            self.assertRaisesRegex(Failure, "no GitHub description"),
        ):
            repo_description()
        gh.assert_called_once_with("api", "repos/o/r", "--jq", ".description")


class CheckBinaryTest(unittest.TestCase):
    def test_only_linux_resolves_a_linux_binarys_libraries(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "tool"
            # An ELF header: class, byte order and version, then e_type at
            # offset 16 and e_machine at 18 (0x3E, x86-64).
            binary.write_bytes(b"\x7fELF\x02\x01\x01" + bytes(9) + b"\x02\x00\x3e\x00" + bytes(44))
            for platform, resolves in (("darwin", False), ("linux", True)):
                with (
                    self.subTest(platform=platform),
                    mock.patch("playbook.ci.build.sys.platform", platform),
                    mock.patch("playbook.ci.build.subprocess.run") as ldd,
                ):
                    ldd.return_value.stdout = ""
                    check_binary(binary, "linux-x64")
                    self.assertEqual(ldd.called, resolves)


class ExpectedFilesTest(unittest.TestCase):
    def test_windows_binaries_get_exe_and_macos_extras_only_on_macos(self):
        monad = config(MONAD).release
        mac = expected_files(monad, "macos-arm64")
        self.assertIn("monad-audiotap", mac)
        self.assertIn("DOTNET-LICENSE.txt", mac)
        windows = expected_files(monad, "windows-x64")
        self.assertIn("monad.exe", windows)
        self.assertNotIn("monad-audiotap", windows)

    def test_rust_archives_hold_binary_licence_and_readme(self):
        self.assertEqual(expected_files(config(DBIRD).release, "linux-x64"), ["LICENSE", "README.md", "dbird"])

    def test_python_archives_also_hold_the_licence_of_the_python_inside(self):
        subtitler = config(SUBTITLER).release
        self.assertEqual(
            expected_files(subtitler, "windows-arm64"),
            ["LICENSE", "PYTHON-LICENSE.txt", "PYTHON-THIRD-PARTY-NOTICES.txt", "README.md", "subtitler.exe"],
        )


class BuildPythonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "pyproject.toml").write_text(PYPROJECT)
        self.work = Path(self.temp.name) / "work"
        self.work.mkdir()
        self.release = config(SUBTITLER).release

    def build(self):
        def bundle(command, **_):
            if "PyInstaller" in command:
                folder = Path(command[command.index("--distpath") + 1]) / command[command.index("--name") + 1]
                (folder / command[command.index("--contents-directory") + 1]).mkdir(parents=True)
                (folder / "subtitler").write_text("a built program")

        with mock.patch.object(build, "run", side_effect=bundle) as run:
            built = build._build_python(self.root, self.release, "1.2.3", self.work)
        return built, [call.args[0] for call in run.call_args_list]

    def bundler(self):
        return self.build()[1][-1]

    def test_the_bundler_is_pinned_and_only_in_a_throwaway_environment(self):
        _, commands = self.build()
        self.assertEqual(commands[0], [sys.executable, "-m", "venv", str(self.work / "environment")])
        install = commands[1]
        self.assertIn("environment", install[0])
        self.assertRegex(stacks.PYINSTALLER, r"^pyinstaller==\d+\.\d+\.\d+$")
        self.assertIn(stacks.PYINSTALLER, install)
        self.assertIn(str(self.root), install)

    def test_it_makes_a_program_with_the_runtime_hook(self):
        command = self.bundler()
        self.assertEqual(command[command.index("--name") + 1], "subtitler")
        hook = Path(command[command.index("--runtime-hook") + 1])
        self.assertTrue(hook.is_file(), hook)

    def test_it_keeps_python_in_a_folder_named_for_the_tool_and_version(self):
        command = self.bundler()
        self.assertIn("--onedir", command)
        self.assertNotIn("--onefile", command)
        self.assertEqual(command[command.index("--contents-directory") + 1], "subtitler-runtime-1.2.3")

    def test_the_program_and_its_folder_end_up_side_by_side(self):
        built, _ = self.build()
        self.assertEqual(sorted(p.name for p in built.iterdir()), ["subtitler", "subtitler-runtime-1.2.3"])

    def test_it_keeps_what_the_bundler_cant_see(self):
        command = self.bundler()
        self.assertEqual(command[command.index("--collect-submodules") + 1], "subtitler")
        self.assertEqual(command[command.index("--collect-data") + 1], "subtitler")

    def test_the_program_starts_the_scripts_entry_point_and_passes_on_its_exit_code(self):
        script = Path(self.bundler()[-1])
        self.assertEqual(script.read_text(), "import sys\n\nimport subtitler.cli\n\nsys.exit(subtitler.cli.main())\n")

    def test_a_missing_entry_point_stops_before_installing_anything(self):
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.0.0"\n')
        with mock.patch.object(build, "run") as run, self.assertRaises(ConfigError):
            build._build_python(self.root, self.release, "1.2.3", self.work)
        run.assert_not_called()

    def test_the_licence_comes_from_the_python_in_use(self):
        notices = build._python_notices()
        self.assertEqual(list(notices.values()), ["PYTHON-LICENSE.txt"])
        self.assertIn("PYTHON SOFTWARE FOUNDATION LICENSE", next(iter(notices)).read_text(encoding="utf-8"))

    def test_the_finished_archive_carries_both_python_and_third_party_notices(self):
        for name in self.release.files:
            (self.root / name).write_text(name)
        (self.work / "subtitler").write_text("a built program")
        (self.work / "subtitler-runtime-1.2.3").mkdir()
        (self.work / "subtitler-runtime-1.2.3" / "library").write_text("a library")
        licence = self.root / "python-licence"
        licence.write_text("Python's licence")
        with (
            mock.patch.object(build, "_build_python", return_value=self.work),
            mock.patch.object(build, "_python_notices", return_value={licence: "PYTHON-LICENSE.txt"}),
            mock.patch.object(build, "_python_third_party_notices", return_value="third-party licences"),
            mock.patch.object(build, "source_date", return_value=1_700_000_000),
        ):
            archive = build.build(self.root, config(SUBTITLER), "linux-x64", "1.2.3", self.work / "archives")
        payload = self.work / "payload"
        extract(archive, payload)
        self.assertEqual(
            sorted(p.name for p in payload.iterdir()),
            sorted([*expected_files(self.release, "linux-x64"), "subtitler-runtime-1.2.3"]),
        )
        self.assertEqual((payload / "subtitler-runtime-1.2.3" / "library").read_text(), "a library")
        self.assertEqual((payload / "PYTHON-LICENSE.txt").read_text(), "Python's licence")
        self.assertEqual((payload / "PYTHON-THIRD-PARTY-NOTICES.txt").read_text(), "third-party licences")

    def test_a_build_without_its_runtime_stops_before_packaging(self):
        (self.work / "subtitler").write_text("a built program")
        with (
            mock.patch.object(build, "_build_python", return_value=self.work),
            self.assertRaisesRegex(Failure, "didn't produce subtitler-runtime-1.2.3"),
        ):
            build.build(self.root, config(SUBTITLER), "linux-x64", "1.2.3", self.work / "archives")


class PythonThirdPartyNoticesTest(unittest.TestCase):
    VERSION = namedtuple("Version", "major minor micro releaselevel serial")

    def test_the_build_includes_attributed_notices_for_the_exact_python_and_its_libraries(self):
        texts = [
            b"CPython OpenSSL libffi zlib expat libmpdec",
            b"bzip2 copyright",
            b"XZ licensing overview",
            b"0BSD",
            b"Meta BSD",
            b"liblzma is in the public domain",
            b"2016-present, Facebook, Inc.",
        ]
        responses = []
        for text in texts:
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = text
            responses.append(response)
        with (
            mock.patch.object(build.sys, "version_info", self.VERSION(3, 14, 6, "final", 0)),
            mock.patch.object(build.urllib.request, "urlopen", side_effect=responses) as fetch,
        ):
            notices = build._python_third_party_notices()
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertIn("/python/cpython/v3.14.6/Doc/license.rst", urls[0])
        for text, url in zip(texts, urls, strict=True):
            self.assertIn(text.decode(), notices)
            self.assertIn(f"Source: {url}", notices)
        self.assertIn("/bzip2-1.0.8/LICENSE", urls[1])
        self.assertIn("/xz/v5.8.1/COPYING", urls[2])
        self.assertIn("/xz/v5.8.1/COPYING.0BSD", urls[3])
        self.assertIn("/zstd/v1.5.7/LICENSE", urls[4])
        self.assertIn("/xz/v5.2.5/COPYING", urls[5])
        self.assertIn("/zstd/v1.4.8/LICENSE", urls[6])
        self.assertIn("Additional project", notices)

    def test_prerelease_python_uses_its_exact_source_tag(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"a licence"
        with (
            mock.patch.object(build.sys, "version_info", self.VERSION(3, 15, 0, "candidate", 2)),
            mock.patch.object(build.urllib.request, "urlopen", return_value=response) as fetch,
        ):
            build._python_third_party_notices()
        self.assertIn("/v3.15.0rc2/Doc/license.rst", fetch.call_args_list[0].args[0])

    def test_a_missing_notice_stops_the_release_with_its_source(self):
        with (
            mock.patch.object(build.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")),
            self.assertRaisesRegex(
                Failure, r"couldn't fetch.*https://raw.githubusercontent.com/python/cpython/.*offline"
            ),
        ):
            build._python_third_party_notices()

    def test_an_empty_response_cannot_silently_omit_a_notice(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"\n"
        with (
            mock.patch.object(build.urllib.request, "urlopen", return_value=response),
            self.assertRaisesRegex(Failure, "notice from .* is empty"),
        ):
            build._python_third_party_notices()


if __name__ == "__main__":
    unittest.main()
