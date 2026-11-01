import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

from playbook.ci import registry
from playbook.ci.registry import tool_packages
from playbook.util import Failure
from tests.fixtures import MONAD, SUBTITLER, config

# DotnetToolSettings.xml as `dotnet pack` writes it, byte order mark included.
POINTER = """﻿<?xml version="1.0" encoding="utf-8"?>
<DotNetCliTool Version="2">
  <Commands>
    <Command Name="monad" />
  </Commands>
  <RuntimeIdentifierPackages>
    <RuntimeIdentifierPackage RuntimeIdentifier="osx-arm64" Id="leduftw.monad.osx-arm64" />
    <RuntimeIdentifierPackage RuntimeIdentifier="linux-x64" Id="leduftw.monad.linux-x64" />
  </RuntimeIdentifierPackages>
</DotNetCliTool>
"""

EXECUTABLE = """﻿<?xml version="1.0" encoding="utf-8"?>
<DotNetCliTool Version="2">
  <Commands>
    <Command Name="monad" EntryPoint="monad" Runner="executable" />
  </Commands>
</DotNetCliTool>
"""

FRAMEWORK = """﻿<?xml version="1.0" encoding="utf-8"?>
<DotNetCliTool Version="1">
  <Commands>
    <Command Name="monad" EntryPoint="monad.dll" Runner="dotnet" />
  </Commands>
</DotNetCliTool>
"""


class ToolPackagesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def package(self, name: str, files: dict[str, str]) -> None:
        with zipfile.ZipFile(self.out / name, "w") as bundle:
            for path, text in files.items():
                bundle.writestr(path, text.encode("utf-8"))

    def per_platform(self, osx_files: dict[str, str] | None = None) -> None:
        self.package("leduftw.monad.2.2.0.nupkg", {"tools/net10.0/any/DotnetToolSettings.xml": POINTER})
        self.package(
            "leduftw.monad.osx-arm64.2.2.0.nupkg",
            osx_files
            if osx_files is not None
            else {
                "tools/any/osx-arm64/DotnetToolSettings.xml": EXECUTABLE,
                "tools/any/osx-arm64/monad": "",
                "tools/any/osx-arm64/monad-audiotap": "",
            },
        )
        self.package(
            "leduftw.monad.linux-x64.2.2.0.nupkg",
            {"tools/any/linux-x64/DotnetToolSettings.xml": EXECUTABLE, "tools/any/linux-x64/monad": ""},
        )

    def names(self, packages: list[Path]) -> list[str]:
        return [package.name for package in packages]

    def test_per_platform_packages_go_first_and_the_one_naming_them_last(self):
        self.per_platform()
        packages = tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])
        self.assertEqual(
            self.names(packages),
            [
                "leduftw.monad.linux-x64.2.2.0.nupkg",
                "leduftw.monad.osx-arm64.2.2.0.nupkg",
                "leduftw.monad.2.2.0.nupkg",
            ],
        )

    def test_a_mac_package_without_the_mac_only_binaries_fails(self):
        self.per_platform(
            {"tools/any/osx-arm64/DotnetToolSettings.xml": EXECUTABLE, "tools/any/osx-arm64/monad": ""},
        )
        with self.assertRaisesRegex(Failure, "osx-arm64.2.2.0.nupkg runs on macOS but doesn't carry monad-audiotap"):
            tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])

    def test_without_mac_only_binaries_nothing_is_required(self):
        self.per_platform(
            {"tools/any/osx-arm64/DotnetToolSettings.xml": EXECUTABLE, "tools/any/osx-arm64/monad": ""},
        )
        self.assertEqual(len(tool_packages(self.out, "leduftw.monad", "2.2.0", [])), 3)

    def test_a_missing_platform_package_fails(self):
        self.per_platform()
        (self.out / "leduftw.monad.linux-x64.2.2.0.nupkg").unlink()
        with self.assertRaisesRegex(Failure, "needs exactly"):
            tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])

    def test_an_unexpected_package_fails(self):
        self.per_platform()
        self.package("leduftw.monad.win-x64.2.2.0.nupkg", {"tools/any/win-x64/DotnetToolSettings.xml": EXECUTABLE})
        with self.assertRaisesRegex(Failure, "needs exactly"):
            tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])

    def test_one_package_for_every_platform_carries_the_mac_only_binaries(self):
        settings = {"tools/net10.0/any/DotnetToolSettings.xml": FRAMEWORK, "tools/net10.0/any/monad.dll": ""}
        self.package("leduftw.monad.2.2.0.nupkg", settings)
        with self.assertRaisesRegex(Failure, "runs on macOS but doesn't carry monad-audiotap"):
            tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])
        self.package("leduftw.monad.2.2.0.nupkg", {**settings, "tools/net10.0/any/monad-audiotap": ""})
        packages = tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])
        self.assertEqual(self.names(packages), ["leduftw.monad.2.2.0.nupkg"])

    def test_a_package_that_isnt_a_tool_fails(self):
        self.package("leduftw.monad.2.2.0.nupkg", {"lib/net10.0/monad.dll": ""})
        with self.assertRaisesRegex(Failure, "isn't a .NET tool"):
            tool_packages(self.out, "leduftw.monad", "2.2.0", [])

    def test_a_referenced_package_that_isnt_a_tool_fails_even_without_mac_only_binaries(self):
        self.per_platform({"tools/any/osx-arm64/monad": ""})
        for binaries in ([], ["monad-audiotap"]):
            with (
                self.subTest(binaries=binaries),
                self.assertRaisesRegex(Failure, "osx-arm64.2.2.0.nupkg isn't a .NET tool"),
            ):
                tool_packages(self.out, "leduftw.monad", "2.2.0", binaries)

    def test_an_xml_file_without_tool_commands_isnt_tool_metadata(self):
        self.package(
            "leduftw.monad.2.2.0.nupkg",
            {"tools/net10.0/any/DotnetToolSettings.xml": "<DotNetCliTool Version='1' />"},
        )
        with self.assertRaisesRegex(Failure, "isn't a .NET tool"):
            tool_packages(self.out, "leduftw.monad", "2.2.0", [])

    def test_the_main_package_must_exist(self):
        self.package("other.2.2.0.nupkg", {"tools/net10.0/any/DotnetToolSettings.xml": FRAMEWORK})
        with self.assertRaisesRegex(Failure, "not leduftw.monad.2.2.0.nupkg"):
            tool_packages(self.out, "leduftw.monad", "2.2.0", [])

    def test_each_platform_package_gets_the_archive_checks_for_its_target(self):
        self.per_platform()
        packages = tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])
        with mock.patch.object(registry.build, "check_binary") as check:
            registry.check_binaries(packages, config(MONAD).release)
        self.assertEqual(
            [(call.args[0].name, call.args[1]) for call in check.call_args_list],
            [("monad", "linux-x64"), ("monad", "macos-arm64"), ("monad-audiotap", "macos-arm64")],
        )

    def test_a_platform_package_without_its_binary_fails(self):
        self.per_platform()
        self.package("leduftw.monad.linux-x64.2.2.0.nupkg", {"tools/any/linux-x64/DotnetToolSettings.xml": EXECUTABLE})
        packages = tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])
        with (
            mock.patch.object(registry.build, "check_binary"),
            self.assertRaisesRegex(Failure, "linux-x64.2.2.0.nupkg doesn't hold monad"),
        ):
            registry.check_binaries(packages, config(MONAD).release)

    def test_a_failed_binary_check_names_the_package(self):
        self.per_platform()
        packages = tool_packages(self.out, "leduftw.monad", "2.2.0", ["monad-audiotap"])
        broken = Failure("monad links to Homebrew paths, so it won't run elsewhere")
        with (
            mock.patch.object(registry.build, "check_binary", side_effect=broken),
            self.assertRaisesRegex(Failure, r"^leduftw\.monad\.linux-x64\.2\.2\.0\.nupkg: monad links to Homebrew"),
        ):
            registry.check_binaries(packages, config(MONAD).release)

    def test_a_runtime_the_release_doesnt_build_is_left_unchecked(self):
        name = "leduftw.monad.linux-musl-x64.2.2.0.nupkg"
        self.package(
            name,
            {"tools/any/linux-musl-x64/DotnetToolSettings.xml": EXECUTABLE, "tools/any/linux-musl-x64/monad": ""},
        )
        with (
            mock.patch.object(registry.build, "check_binary") as check,
            mock.patch.object(registry, "warn") as warned,
        ):
            registry.check_binaries([self.out / name], config(MONAD).release)
        check.assert_not_called()
        self.assertIn("linux-musl-x64", warned.call_args.args[0])


class ToolInstallTest(unittest.TestCase):
    def test_only_freshly_packed_packages_can_supply_the_installed_command(self):
        cfg = config(MONAD)
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            packages = work / "packages"
            packages.mkdir()
            for package_id in ("leduftw.monad", "leduftw.monad.osx-arm64"):
                (packages / f"{package_id}.2.2.0.nupkg").touch()
            tools = work / "tools"
            tools.mkdir()
            command = tools / ("monad.exe" if registry.os.name == "nt" else "monad")
            command.touch()
            with (
                mock.patch.object(registry, "run") as install,
                mock.patch.object(registry.build, "check_main_binary") as check,
            ):
                registry._check_tool(cfg, "2.2.0", packages, work)
            args = install.call_args.args[0]
            self.assertNotIn("--add-source", args)
            settings = ET.parse(args[args.index("--configfile") + 1]).getroot()
            self.assertIsNotNone(settings.find("packageSources/clear"))
            self.assertEqual(settings.find("packageSources/add[@key='packed']").get("value"), str(packages))
            self.assertEqual(settings.find("packageSources/add[@key='nuget.org']").get("value"), registry.NUGET_SOURCE)
            self.assertEqual(
                [
                    package.get("pattern")
                    for package in settings.findall("packageSourceMapping/packageSource[@key='packed']/package")
                ],
                ["leduftw.monad", "leduftw.monad.osx-arm64"],
            )
            self.assertEqual(
                [
                    package.get("pattern")
                    for package in settings.findall("packageSourceMapping/packageSource[@key='nuget.org']/package")
                ],
                ["*"],
            )
            self.assertEqual(install.call_args.kwargs["env"]["NUGET_PACKAGES"], str(work / "nuget"))
            check.assert_called_once_with(command, cfg, "2.2.0")


def fake_uv(wheel="leduftw_subtitler-1.0.0-py3-none-any.whl", source="leduftw_subtitler-1.0.0.tar.gz"):
    """Stands in for `run`: `uv build` leaves these files; anything else does nothing."""

    def run(command, **_):
        if command[:2] == ["uv", "build"]:
            out = Path(command[command.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / ".gitignore").write_text("*")
            for name in (wheel, source):
                if name:
                    (out / name).touch()
        return mock.Mock(returncode=0, stdout="", stderr="")

    return run


class PythonDistributionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.config = config(SUBTITLER)

    def build(self, version="1.0.0", **names):
        with (
            mock.patch.object(registry, "run", side_effect=fake_uv(**names)) as run,
            mock.patch.object(registry.build, "source_date", return_value=1_700_000_000),
        ):
            result = registry._python_distributions(self.work, self.config, version, self.work / "dist")
        return result, run

    def test_finds_the_wheel_and_the_source_archive(self):
        (wheel, source), _ = self.build()
        self.assertEqual(wheel.suffix, ".whl")
        self.assertTrue(source.name.endswith(".tar.gz"))

    def test_retries_build_the_same_timestamps(self):
        _, run = self.build()
        self.assertEqual(run.call_args.kwargs["env"], {"SOURCE_DATE_EPOCH": "1700000000"})

    def test_a_missing_file_is_a_failure(self):
        with self.assertRaisesRegex(Failure, "not one wheel and one source archive"):
            self.build(source=None)

    def test_a_package_of_another_version_is_a_failure(self):
        with self.assertRaisesRegex(Failure, "not version 2.0.0"):
            self.build(version="2.0.0")


class PythonDryRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = config(SUBTITLER)
        self.config.release.channels = ["pypi"]

    def dry_run(self):
        def run(command, **kwargs):
            if command[:3] == ["uv", "tool", "install"]:
                folder = Path(kwargs["env"]["UV_TOOL_BIN_DIR"])
                folder.mkdir()
                (folder / ("subtitler.exe" if registry.os.name == "nt" else "subtitler")).touch()
            return fake_uv()(command, **kwargs)

        with (
            mock.patch.object(registry, "run", side_effect=run) as commands,
            mock.patch.object(registry, "repository", return_value="leduftw/subtitler"),
            mock.patch.object(registry.build, "source_date", return_value=1),
            mock.patch.object(registry.build, "check_main_binary") as check,
        ):
            registry.channels_dry_run(self.root, self.config, "1.0.0")
        return [call.args[0] for call in commands.call_args_list], check

    def test_it_checks_the_files_without_any_credentials(self):
        commands, _ = self.dry_run()
        self.assertIn(["uvx", registry.stacks.TWINE, "check", "--strict"], [c[:4] for c in commands])
        publish = next(c for c in commands if c[:2] == ["uv", "publish"])
        self.assertIn("--dry-run", publish)
        self.assertEqual(publish[publish.index("--trusted-publishing") + 1], "never")

    def test_it_holds_the_installed_command_to_the_same_rules(self):
        _, check = self.dry_run()
        command, cfg, version = check.call_args.args
        self.assertEqual(command.stem, "subtitler")
        self.assertEqual((cfg, version), (self.config, "1.0.0"))

    def test_a_wheel_without_the_command_is_a_failure(self):
        with (
            mock.patch.object(registry, "run", side_effect=fake_uv()),
            self.assertRaisesRegex(Failure, "gives no `subtitler` command"),
        ):
            registry._check_python_tool(self.config, "1.0.0", Path("x.whl"), self.root)


class PythonPublishTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "leduftw-subtitler"\nversion = "1.0.0"\n\n'
            '[project.scripts]\nsubtitler = "subtitler.cli:main"\n'
        )
        self.config = config(SUBTITLER)

    def publish(self, code=0, stderr=""):
        def run(command, **kwargs):
            if command[:2] == ["uv", "publish"]:
                return mock.Mock(returncode=code, stdout="", stderr=stderr)
            return fake_uv()(command, **kwargs)

        with (
            mock.patch.object(registry, "run", side_effect=run) as commands,
            mock.patch.object(registry.build, "source_date", return_value=1),
            mock.patch("builtins.print"),
        ):
            registry.publish(self.root, self.config)
        return next(call.args[0] for call in commands.call_args_list if call.args[0][:2] == ["uv", "publish"])

    def test_it_uploads_through_trusted_publishing_only(self):
        command = self.publish()
        self.assertEqual(command[command.index("--trusted-publishing") + 1], "always")

    def test_a_retry_skips_what_pypi_already_has(self):
        command = self.publish()
        self.assertEqual(command[command.index("--check-url") + 1], registry.PYPI_INDEX)

    def test_a_file_already_on_pypi_isnt_a_failure(self):
        self.publish(code=1, stderr="400 Bad Request: File already exists")

    def test_any_other_failure_is(self):
        with self.assertRaisesRegex(Failure, "uv publish failed"):
            self.publish(code=1, stderr="invalid-publisher")


class PythonRegistryInfoTest(unittest.TestCase):
    def test_the_action_hears_which_registry_to_set_up(self):
        outputs = {}
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(registry, "set_output", outputs.__setitem__):
            registry.info(Path(temp), config(SUBTITLER))
        self.assertEqual(outputs["registry"], "pypi")


if __name__ == "__main__":
    unittest.main()
