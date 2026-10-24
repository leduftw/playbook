"""Language registries (crates.io, NuGet): dry runs on release PRs, uploads on release.

Uploads run from a job in the repo's own playbook.yml (through the registry
action), because registries match trusted publishing against the workflow file
that does the publishing. No token is stored anywhere: each run exchanges its
GitHub identity for a short-lived registry key.

On NuGet a tool is whatever `dotnet pack` makes of it: one package, or, for a
tool built per platform, a small package that names one package per runtime.
Every package is pushed, the per-platform ones first, so the package people
install never names one that isn't there yet.
"""

from __future__ import annotations

import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

from .. import stacks
from ..config import Config, Release
from ..util import Failure, git, run, say, set_output, warn
from . import build, winget

NUGET_SOURCE = "https://api.nuget.org/v3/index.json"
TOOL_SETTINGS = "DotnetToolSettings.xml"


def info(root: Path, config: Config) -> None:
    """Outputs the registry action needs before it can set anything up."""
    release = config.release
    if release is None or not release.registry:
        raise Failure("this repo doesn't publish to a registry")
    global_json = "global.json" if (root / "global.json").is_file() else ""
    set_output("registry", release.registry)
    set_output("apt", " ".join(release.apt))
    set_output("global_json", global_json)
    set_output("registry_user", release.registry_user)


def channels_dry_run(root: Path, config: Config, version: str) -> None:
    """Everything a release PR can check about the channels without publishing."""
    release = config.release
    assert release is not None
    if "winget" in release.channels:
        winget.check(config)
    if "homebrew" in release.channels:
        git("ls-remote", "--exit-code", "https://github.com/leduftw/homebrew-tap", "HEAD")
        say("the Homebrew tap is reachable")
    if release.registry == "crates-io":
        run(["cargo", "publish", "--dry-run", "--locked"], cwd=root / release.dir)
        say(f"crates.io would accept {config.name} {version}")
    elif release.registry == "nuget":
        with tempfile.TemporaryDirectory(prefix="playbook-nuget-") as temp:
            work = Path(temp)
            packages = _pack(root, config, version, work / "packages")
            say(f"NuGet would receive {', '.join(p.name for p in packages)}")
            _check_tool(config, version, work / "packages", work)


def _pack(root: Path, config: Config, version: str, out: Path) -> list[Path]:
    """Packs the tool and returns its packages in the order they're pushed."""
    release = config.release
    assert release is not None
    package_id = release.package_id or config.name
    # The release build's settings, except the ones that decide what the
    # archive holds: whether the tool is one package or one per platform is
    # the project's to say.
    properties = [
        f"-p:PackageId={package_id}",
        f"-p:Version={version}",
        "-p:DebugSymbols=false",
        "-p:DebugType=None",
        "-p:IncludeSourceRevisionInInformationalVersion=false",
        "-p:ContinuousIntegrationBuild=true",
    ]
    if any(root.rglob("packages.lock.json")):
        properties.append("-p:RestoreLockedMode=true")
    properties += release.publish_properties
    on_mac = sys.platform == "darwin"
    properties += release.macos_publish_properties if on_mac else release.other_publish_properties
    run(
        [
            "dotnet",
            "pack",
            str(root / (release.project or "")),
            "--configuration",
            "Release",
            "--output",
            str(out),
            *properties,
        ],
        cwd=root,
    )
    packages = tool_packages(out, package_id, version, release.macos_binaries)
    check_binaries(packages, release)
    return packages


def tool_packages(out: Path, package_id: str, version: str, macos_binaries: list[str]) -> list[Path]:
    """The packages `dotnet pack` made for one tool, checked, in the order they're pushed."""
    main = out / f"{package_id}.{version}.nupkg"
    found = sorted(p.name for p in out.glob("*.nupkg"))
    if not main.is_file():
        raise Failure(f"dotnet pack made {found}, not {main.name}")
    settings = _tool_settings(main)
    runtime_ids = [
        package.get("Id", "")
        for _, root in settings
        for package in root.iterfind("RuntimeIdentifierPackages/RuntimeIdentifierPackage")
    ]
    runtime_packages = sorted(f"{package}.{version}.nupkg" for package in runtime_ids)
    expected = sorted([main.name, *runtime_packages])
    if found != expected:
        raise Failure(f"dotnet pack made {found}; the tool needs exactly {expected}")
    packages = [out / name for name in runtime_packages] + [main]
    for package in packages:
        missing = _missing_macos_binaries(package, macos_binaries)
        if missing:
            raise Failure(
                f"{package.name} runs on macOS but doesn't carry {', '.join(missing)} "
                "(release.macos-binaries); only a Mac can build them, so pack the tool on one"
            )
    return packages


def _tool_settings(package: Path) -> list[tuple[PurePosixPath, ET.Element]]:
    """Each tool folder in a package, with the DotnetToolSettings.xml it holds."""
    with zipfile.ZipFile(package) as bundle:
        settings = [
            (PurePosixPath(name).parent, ET.fromstring(bundle.read(name)))
            for name in bundle.namelist()
            if PurePosixPath(name).name == TOOL_SETTINGS
        ]
    if not settings or any(
        root.tag != "DotNetCliTool" or root.find("Commands/Command") is None for _, root in settings
    ):
        raise Failure(f"{package.name} isn't a .NET tool; the project needs <PackAsTool>true</PackAsTool>")
    return settings


def _missing_macos_binaries(package: Path, names: list[str]) -> list[str]:
    """The Mac-only binaries absent from a tool folder that macOS installs, if any.

    A tool built for every platform lives in tools/<framework>/any/; one built
    per platform in tools/any/<runtime>/, and macOS installs the osx-* ones. A
    package naming per-platform packages holds no tool of its own.
    """
    settings = _tool_settings(package)
    if not names:
        return []
    with zipfile.ZipFile(package) as bundle:
        files = set(bundle.namelist())
    missing: set[str] = set()
    for folder, root in settings:
        if root.find("RuntimeIdentifierPackages") is not None:
            continue
        runtime = folder.name if folder.parent.name == "any" else "any"
        if runtime == "any" or runtime.startswith("osx"):
            missing.update(name for name in names if (folder / name).as_posix() not in files)
    return sorted(missing)


def check_binaries(packages: list[Path], release: Release) -> None:
    """Each per-platform package's binaries pass the checks the release archives get.

    Only runtimes the release builds have a target to check against. A tool
    built for every platform has no per-platform packages; the install check
    is what runs it.
    """
    targets = {stacks.TARGETS[target]["dotnet"]: target for target in release.targets}
    checked = 0
    with tempfile.TemporaryDirectory(prefix="playbook-nuget-binaries-") as temp:
        for package in packages:
            with zipfile.ZipFile(package) as bundle:
                files = set(bundle.namelist())
                for folder, _ in _tool_settings(package):
                    if folder.parent.name != "any":
                        continue
                    target = targets.get(folder.name)
                    if target is None:
                        warn(f"{package.name} is for {folder.name}, which the release doesn't build; not checked")
                        continue
                    for name in build.executable_names(release, target):
                        entry = (folder / name).as_posix()
                        if entry not in files:
                            raise Failure(f"{package.name} doesn't hold {name}")
                        binary = Path(bundle.extract(entry, Path(temp) / package.name))
                        try:
                            build.check_binary(binary, target)
                        except Failure as error:
                            raise Failure(f"{package.name}: {error}") from error
                        checked += 1
    if checked:
        say(f"{checked} binaries in the per-platform packages pass the release archives' checks")


def _check_tool(config: Config, version: str, packages: Path, work: Path) -> None:
    """Installs the packed tool the way people will, and checks what they'd run."""
    release = config.release
    assert release is not None
    package_id = release.package_id or config.name
    tools = work / "tools"
    # Tool installation may also fetch SDK host packages. Allow those from
    # NuGet, but map every freshly packed ID exclusively to this build. An
    # additional source alone can select a published copy of the same version.
    configuration = ET.Element("configuration")
    sources = ET.SubElement(configuration, "packageSources")
    ET.SubElement(sources, "clear")
    ET.SubElement(sources, "add", key="packed", value=str(packages))
    ET.SubElement(sources, "add", key="nuget.org", value=NUGET_SOURCE)
    mapping = ET.SubElement(configuration, "packageSourceMapping")
    packed = ET.SubElement(mapping, "packageSource", key="packed")
    for package in sorted(packages.glob("*.nupkg")):
        ET.SubElement(packed, "package", pattern=package.name.removesuffix(f".{version}.nupkg"))
    online = ET.SubElement(mapping, "packageSource", key="nuget.org")
    ET.SubElement(online, "package", pattern="*")
    settings = work / "nuget.config"
    ET.ElementTree(configuration).write(settings, encoding="utf-8", xml_declaration=True)
    run(
        [
            "dotnet",
            "tool",
            "install",
            package_id,
            "--version",
            version,
            "--configfile",
            str(settings),
            "--tool-path",
            str(tools),
        ],
        # Cached packages bypass source mapping, so use an empty cache too.
        env={"NUGET_PACKAGES": str(work / "nuget")},
    )
    command = tools / (config.name + (".exe" if os.name == "nt" else ""))
    if not command.exists():
        raise Failure(f"installing {package_id} gives no `{config.name}` command")
    say(f"dotnet tool install {package_id} gives `{config.name}`")
    build.check_main_binary(command, config, version)


def publish(root: Path, config: Config) -> None:
    release = config.release
    if release is None or not release.registry:
        raise Failure("this repo doesn't publish to a registry")
    version = stacks.read_version(root, release)
    if release.registry == "crates-io":
        if not os.environ.get("CARGO_REGISTRY_TOKEN"):
            raise Failure("no crates.io token; is trusted publishing set up for playbook.yml?")
        result = run(["cargo", "publish", "--locked"], cwd=root / release.dir, check=False, capture=True)
        print(result.stdout, result.stderr, sep="\n")
        if result.returncode != 0:
            if "already exists" in result.stderr or "already uploaded" in result.stderr:
                say(f"crates.io already has {config.name} {version}")
                return
            raise Failure("cargo publish failed")
        say(f"published {config.name} {version} to crates.io")
    elif release.registry == "nuget":
        key = os.environ.get("NUGET_API_KEY", "")
        if not key:
            raise Failure("no NuGet key; is trusted publishing set up for playbook.yml?")
        with tempfile.TemporaryDirectory(prefix="playbook-nuget-") as temp:
            packages = _pack(root, config, version, Path(temp))
            for package in packages:
                run(
                    [
                        "dotnet",
                        "nuget",
                        "push",
                        str(package),
                        "--api-key",
                        key,
                        "--source",
                        NUGET_SOURCE,
                        "--skip-duplicate",
                    ]
                )
        say(f"published {', '.join(p.name for p in packages)} to NuGet")
    else:
        raise Failure(f"publishing to {release.registry} isn't supported yet")
