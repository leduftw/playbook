import tempfile
import unittest
from pathlib import Path

from playbook import stacks
from playbook.config import Check, ConfigError
from tests.fixtures import DBIRD, MONAD, PYPROJECT, SUBTITLER, config

CARGO_TOML = """[package]
name = "dbird"
version = "1.2.0"
edition = "2024"

[dependencies]
serde = { version = "1.0.228" }
"""

CARGO_LOCK = """version = 4

[[package]]
name = "dbird"
version = "1.2.0"
dependencies = [
 "serde",
]

[[package]]
name = "serde"
version = "1.2.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rust_version_moves_in_manifest_and_lock_only_for_the_crate(self):
        (self.root / "Cargo.toml").write_text(CARGO_TOML)
        (self.root / "Cargo.lock").write_text(CARGO_LOCK)
        release = config(DBIRD).release
        self.assertEqual(stacks.read_version(self.root, release), "1.2.0")
        changed = stacks.write_version(self.root, release, "1.3.0")
        self.assertEqual({p.name for p in changed}, {"Cargo.toml", "Cargo.lock"})
        self.assertIn('version = "1.3.0"', (self.root / "Cargo.toml").read_text())
        self.assertIn('serde = { version = "1.0.228" }', (self.root / "Cargo.toml").read_text())
        lock = (self.root / "Cargo.lock").read_text()
        self.assertIn('name = "dbird"\nversion = "1.3.0"', lock)
        self.assertIn('name = "serde"\nversion = "1.2.0"', lock)

    def test_workspace_version_is_found_in_the_root_manifest(self):
        (self.root / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["app"]\n\n[workspace.package]\nversion = "0.0.0"\n'
        )
        (self.root / "app").mkdir()
        (self.root / "app" / "Cargo.toml").write_text('[package]\nname = "app"\nversion.workspace = true\n')
        release = config(DBIRD).release
        release.dir = "app"
        self.assertEqual(stacks.read_version(self.root, release), "0.0.0")

    def test_dotnet_version_lives_in_the_project(self):
        (self.root / "Monad").mkdir()
        project = self.root / "Monad" / "Monad.csproj"
        project.write_text("<Project><PropertyGroup><Version>2.0.1</Version></PropertyGroup></Project>")
        release = config(MONAD).release
        self.assertEqual(stacks.read_version(self.root, release), "2.0.1")
        stacks.write_version(self.root, release, "2.0.2")
        self.assertIn("<Version>2.0.2</Version>", project.read_text())


class PythonVersionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = config(SUBTITLER).release
        self.pyproject = self.root / "pyproject.toml"
        self.pyproject.write_text(PYPROJECT)

    def package(self, version_line: str, layout: str = "src/subtitler") -> Path:
        (self.root / layout).mkdir(parents=True)
        module = self.root / layout / "__init__.py"
        module.write_text(f'"""A package."""\n\n{version_line}\n')
        return module

    def test_the_version_lives_in_pyproject(self):
        self.assertEqual(stacks.read_version(self.root, self.release), "1.2.3")
        changed = stacks.write_version(self.root, self.release, "1.2.4")
        self.assertEqual(changed, [self.pyproject])
        self.assertIn('version = "1.2.4"', self.pyproject.read_text())
        self.assertIn('name = "leduftw-subtitler"', self.pyproject.read_text())

    def test_a_release_moves_the_packages_own_version_too(self):
        for layout in ("src/subtitler", "subtitler"):
            with self.subTest(layout=layout):
                self.pyproject.write_text(PYPROJECT)
                module = self.package('__version__ = "1.2.3"', layout)
                self.assertEqual(stacks.version_files(self.root, self.release), [self.pyproject, module])
                changed = stacks.write_version(self.root, self.release, "2.0.0")
                self.assertEqual(changed, [self.pyproject, module])
                self.assertIn('__version__ = "2.0.0"', module.read_text())
                self.assertEqual(stacks.read_version(self.root, self.release), "2.0.0")
                module.unlink()
                module.parent.rmdir()

    def test_two_versions_that_disagree_are_refused(self):
        self.package('__version__ = "9.9.9"')
        with self.assertRaises(ConfigError) as caught:
            stacks.read_version(self.root, self.release)
        self.assertIn("says 9.9.9", str(caught.exception))
        self.assertIn("says 1.2.3", str(caught.exception))

    def test_a_single_quoted_runtime_version_is_also_checked(self):
        self.package("__version__ = '9.9.9'")
        with self.assertRaises(ConfigError) as caught:
            stacks.read_version(self.root, self.release)
        self.assertIn("says 9.9.9", str(caught.exception))
        self.assertIn("says 1.2.3", str(caught.exception))

    def test_a_release_preserves_a_single_quoted_runtime_version(self):
        module = self.package("__version__ = '1.2.3'  # The command's version.")
        self.assertEqual(stacks.version_files(self.root, self.release), [self.pyproject, module])
        changed = stacks.write_version(self.root, self.release, "1.2.4")
        self.assertEqual(changed, [self.pyproject, module])
        self.assertIn("__version__ = '1.2.4'  # The command's version.", module.read_text())
        self.assertEqual(stacks.read_version(self.root, self.release), "1.2.4")

    def test_a_computed_version_is_left_alone(self):
        module = self.package('__version__ = importlib.metadata.version("x")')
        self.assertEqual(stacks.version_files(self.root, self.release), [self.pyproject])
        self.assertEqual(stacks.read_version(self.root, self.release), "1.2.3")
        self.assertIn("importlib", module.read_text())

    def test_no_pyproject_is_a_clear_error(self):
        self.pyproject.unlink()
        with self.assertRaises(ConfigError) as caught:
            stacks.read_version(self.root, self.release)
        self.assertIn("no pyproject.toml", str(caught.exception))


class PythonEntryPointTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = config(SUBTITLER).release

    def test_each_binary_comes_from_project_scripts(self):
        (self.root / "pyproject.toml").write_text(PYPROJECT)
        self.assertEqual(stacks.python_entry_points(self.root, self.release), {"subtitler": ("subtitler.cli", "main")})

    def test_a_binary_without_a_script_is_a_clear_error(self):
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.0.0"\n')
        with self.assertRaises(ConfigError) as caught:
            stacks.python_entry_points(self.root, self.release)
        self.assertIn('subtitler = "<module>:<function>"', str(caught.exception))


class CheckCommandsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rust_defaults_treat_warnings_as_errors(self):
        commands = stacks.check_commands(self.root, Check(stack="rust"))
        self.assertIn("-D warnings", commands["lint"][0])
        self.assertEqual(commands["format"], ["cargo fmt --all --check"])

    def test_dotnet_finds_its_solution_and_lock_mode(self):
        (self.root / "Monad.slnx").write_text("<Solution />")
        (self.root / "packages.lock.json").write_text("{}")
        commands = stacks.check_commands(self.root, Check(stack="dotnet"))
        self.assertEqual(commands["prepare"], ["dotnet restore Monad.slnx --locked-mode"])
        self.assertIn("-warnaserror", commands["lint"][0])

    def test_node_needs_format_and_lint_or_a_reason(self):
        (self.root / "pnpm-lock.yaml").write_text("")
        with self.assertRaises(ConfigError) as caught:
            stacks.check_commands(self.root, Check(stack="node"))
        self.assertIn("has no format command", str(caught.exception))
        check = Check(stack="node", skip={"format": "none yet", "lint": "none yet"})
        commands = stacks.check_commands(self.root, check)
        self.assertEqual(commands["prepare"], ["pnpm install --frozen-lockfile"])
        self.assertEqual(commands["test"], ["pnpm test"])

    def test_python_runs_unittest_when_there_are_tests(self):
        (self.root / "tests").mkdir()
        commands = stacks.check_commands(self.root, Check(stack="python"))
        self.assertEqual(commands["test"], ["python -m unittest discover -s tests"])
        self.assertTrue(commands["lint"][0].startswith("uvx ruff@"), "ruff must be pinned")


class ArchiveNameTest(unittest.TestCase):
    def test_names_have_no_version_so_latest_links_stay_stable(self):
        self.assertEqual(stacks.archive_name("dbird", "macos-arm64"), "dbird-macos-arm64.zip")
        self.assertEqual(stacks.archive_name("dbird", "linux-x64"), "dbird-linux-x64.tar.gz")
        self.assertEqual(stacks.archive_name("dbird", "windows-arm64"), "dbird-windows-arm64.zip")


if __name__ == "__main__":
    unittest.main()
