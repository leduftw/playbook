import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

from playbook.ci import registry
from playbook.ci.registry import tool_packages
from playbook.util import Failure
from tests.fixtures import MONAD, config

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


if __name__ == "__main__":
    unittest.main()
