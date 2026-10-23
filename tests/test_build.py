import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook.ci.build import EXECUTABLE, REGULAR, expected_files, extract, smoke, write_archive
from playbook.util import Failure
from tests.fixtures import DBIRD, MONAD, config
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
            mock.patch("playbook.ci.build._check_binary"),
            self.assertRaisesRegex(Failure, r"`tool --not-an-option-of-this-tool` exited 1, not 2"),
        ):
            smoke(self.work, cfg, "linux-x64", "1.2.3", self.work)


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


if __name__ == "__main__":
    unittest.main()
