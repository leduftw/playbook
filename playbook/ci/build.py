"""Builds one release target, packages it, and smoke-tests the finished archive.

The archive is what people download, so the smoke test opens the archive itself
rather than the build output: it must contain exactly the expected files, its
binaries must be for the right CPU and portable, and the main binary must
report the version being released.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

from .. import stacks
from ..config import Config, Release
from ..util import Failure, output, run, say

EXECUTABLE = 0o755
REGULAR = 0o644


def executable_names(release: Release, target: str) -> list[str]:
    os_name = stacks.TARGETS[target]["os"]
    names = release.binaries + (release.macos_binaries if os_name == "macos" else [])
    suffix = ".exe" if os_name == "windows" else ""
    return [f"{name}{suffix}" for name in names]


def expected_files(release: Release, target: str) -> list[str]:
    files = executable_names(release, target) + [Path(f).name for f in release.files]
    if release.stack == "dotnet":
        files += list(stacks.DOTNET_NOTICES.values())
    return sorted(files)


# --- build ----------------------------------------------------------------


def build(root: Path, config: Config, target: str, version: str, out: Path) -> Path:
    release = config.release
    if release is None:
        raise Failure("this repo isn't published; there is nothing to build")
    recorded = stacks.read_version(root, release)
    if recorded != version:
        raise Failure(f"the project file says {recorded}, but this run releases {version}")

    with tempfile.TemporaryDirectory(prefix="playbook-build-") as temp:
        work = Path(temp)
        if release.stack == "rust":
            built = _build_rust(root, release, target)
        else:
            built = _build_dotnet(root, release, target, work / "publish")

        stage = work / "stage"
        stage.mkdir()
        modes: dict[str, int] = {}
        for name in executable_names(release, target):
            source = built / name
            if not source.is_file():
                raise Failure(f"the build didn't produce {name} (looked in {built})")
            shutil.copyfile(source, stage / name)
            modes[name] = EXECUTABLE
        if stacks.TARGETS[target]["os"] == "macos":
            _seal_macos(stage, list(modes))
        for file in release.files:
            source = root / file
            if not source.is_file():
                raise Failure(f"{file}, listed in release.files, doesn't exist")
            shutil.copyfile(source, stage / source.name)
            modes[source.name] = REGULAR
        if release.stack == "dotnet":
            for source, name in _dotnet_notices().items():
                shutil.copyfile(source, stage / name)
                modes[name] = REGULAR

        out.mkdir(parents=True, exist_ok=True)
        archive = out / stacks.archive_name(config.name, target)
        write_archive(stage, modes, archive, _source_date(root))
    say(f"packaged {archive}")
    return archive


def _build_rust(root: Path, release: Release, target: str) -> Path:
    triple = stacks.TARGETS[target]["rust"]
    directory = root / release.dir
    run(
        ["cargo", "build", "--release", "--locked", "--target", triple],
        cwd=directory,
        env=_build_variables(release),
    )
    metadata = json.loads(output(["cargo", "metadata", "--format-version", "1", "--no-deps"], cwd=directory))
    return Path(metadata["target_directory"]) / triple / "release"


def _build_dotnet(root: Path, release: Release, target: str, publish: Path) -> Path:
    rid = stacks.TARGETS[target]["dotnet"]
    os_name = stacks.TARGETS[target]["os"]
    properties = [
        "-p:PublishSingleFile=true",
        "-p:SelfContained=true",
        "-p:IncludeNativeLibrariesForSelfExtract=true",
        # The archive already compresses; an uncompressed bundle starts faster.
        "-p:EnableCompressionInSingleFile=false",
        "-p:PublishTrimmed=false",
        "-p:DebugSymbols=false",
        "-p:DebugType=None",
        "-p:IncludeSourceRevisionInInformationalVersion=false",
        "-p:ContinuousIntegrationBuild=true",
    ]
    if any(root.rglob("packages.lock.json")):
        properties.append("-p:RestoreLockedMode=true")
    properties += release.publish_properties
    properties += release.macos_publish_properties if os_name == "macos" else release.other_publish_properties
    run(
        [
            "dotnet",
            "publish",
            str(root / (release.project or "")),
            "--configuration",
            "Release",
            "--runtime",
            rid,
            "--output",
            str(publish),
            *properties,
        ],
        cwd=root,
        env=_build_variables(release),
    )
    return publish


def _build_variables(release: Release) -> dict[str, str]:
    """Repository variables named in release.build-variables, for the build to read."""
    available = json.loads(os.environ.get("PLAYBOOK_VARS") or "{}")
    missing = [name for name in release.build_variables if name not in available]
    if missing:
        raise Failure(f"repository variable(s) {', '.join(missing)} aren't set (release.build-variables)")
    return {name: available[name] for name in release.build_variables}


def _dotnet_notices() -> dict[Path, str]:
    """The .NET licence files a self-contained app must ship, from the SDK in use."""
    candidates = []
    if os.environ.get("DOTNET_ROOT"):
        candidates.append(Path(os.environ["DOTNET_ROOT"]))
    dotnet = shutil.which("dotnet")
    if dotnet:
        candidates.append(Path(dotnet).resolve().parent)
    info = output(["dotnet", "--info"], env={"DOTNET_CLI_UI_LANGUAGE": "en"})
    match = re.search(r"Base Path:\s*(.+)", info)
    if match:
        candidates.append(Path(match.group(1).strip()).parent.parent)
    for directory in candidates:
        if all((directory / name).is_file() for name in stacks.DOTNET_NOTICES):
            return {directory / name: target for name, target in stacks.DOTNET_NOTICES.items()}
    raise Failure("the .NET SDK in use doesn't provide LICENSE.txt and ThirdPartyNotices.txt")


def _seal_macos(stage: Path, names: list[str]) -> None:
    """Ad-hoc sign any binary that isn't signed yet; never re-sign a signed one.

    Apple Silicon refuses to run unsigned code. Re-signing a binary that already
    carries a signature (such as a .NET single-file host) can break it, so only
    unsigned binaries are touched.
    """
    for name in names:
        binary = stage / name
        verified = subprocess.run(["codesign", "--verify", "--strict", str(binary)], capture_output=True)
        if verified.returncode != 0:
            run(["codesign", "--force", "--sign", "-", str(binary)])
            run(["codesign", "--verify", "--strict", str(binary)])


def _source_date(root: Path) -> int:
    """Timestamp for archive entries: the commit's, so rebuilding gives the same bytes."""
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        return int(output(["git", "log", "-1", "--format=%ct"], cwd=root))
    except Failure:
        return int(time.time())


def write_archive(stage: Path, modes: dict[str, int], archive: Path, mtime: int) -> None:
    names = sorted(modes)
    mtime = max(mtime, 315532800)  # zip can't store dates before 1980
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name in names:
                info = zipfile.ZipInfo(name, date_time=time.gmtime(mtime)[:6])
                info.create_system = 3  # Unix, so unzip and Homebrew honour the mode
                info.external_attr = (0o100000 | modes[name]) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, (stage / name).read_bytes())
    else:
        with (
            open(archive, "wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=mtime) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as bundle,
        ):
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = (stage / name).stat().st_size
                info.mode = modes[name]
                info.mtime = mtime
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with open(stage / name, "rb") as handle:
                    bundle.addfile(info, handle)


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                path = bundle.extract(info, destination)
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(path, mode)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")


# --- smoke ----------------------------------------------------------------


def smoke(root: Path, config: Config, target: str, version: str, dist: Path) -> None:
    release = config.release
    if release is None:
        raise Failure("this repo isn't published; there is nothing to smoke-test")
    archive = dist / stacks.archive_name(config.name, target)
    if not archive.is_file():
        raise Failure(f"{archive} doesn't exist")
    os_name = stacks.TARGETS[target]["os"]

    with tempfile.TemporaryDirectory(prefix="playbook-smoke-") as temp:
        payload = Path(temp) / "payload"
        extract(archive, payload)
        actual = sorted(p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file())
        expected = expected_files(release, target)
        if actual != expected:
            raise Failure(f"{archive.name} holds the wrong files\n  expected: {expected}\n  actual:   {actual}")
        binaries = [payload / name for name in executable_names(release, target)]
        for binary in binaries:
            _check_binary(binary, target)
        say(f"{archive.name}: files, CPU and linking are right")

        reported = _run_binary([str(binaries[0]), *release.version_command])
        if release.version_output is not None:
            wanted = release.version_output.replace("{version}", version)
            if reported != wanted:
                raise Failure(f"{binaries[0].name} reported {reported!r}; expected {wanted!r}")
        elif version not in reported:
            raise Failure(f"{binaries[0].name} reported {reported!r}, without {version}")
        say(f"{binaries[0].name} reports {reported!r}")

        groups = ["all"] + (["unix"] if os_name != "windows" else []) + [os_name]
        for group in groups:
            for command in release.smoke.get(group, []):
                filled = (
                    command.replace("{bin}", binaries[0].as_posix())
                    .replace("{dir}", payload.as_posix())
                    .replace("{version}", version)
                )
                say(f"smoke ({group}): {filled}")
                run([_bash(), "-c", filled], cwd=root)
    say(f"{archive.name} passed its smoke test")


def _run_binary(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise Failure(f"`{' '.join(command)}` exited {result.returncode}:\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def _bash() -> str:
    bash = shutil.which("bash")
    if not bash:
        raise Failure("bash is needed to run release.smoke commands")
    return bash


_PE_MACHINES = {"windows-x64": 0x8664, "windows-arm64": 0xAA64}
_ELF_MACHINES = {"linux-x64": 0x3E, "linux-arm64": 0xB7}
_MACHO_ARCHS = {"macos-arm64": "arm64", "macos-x64": "x86_64"}


def _check_binary(binary: Path, target: str) -> None:
    os_name = stacks.TARGETS[target]["os"]
    with open(binary, "rb") as handle:
        data = handle.read(4096)
        if os_name == "windows" and data[:2] == b"MZ":
            handle.seek(struct.unpack_from("<I", data, 0x3C)[0])
            header = handle.read(6)
    if os_name == "windows":
        if data[:2] != b"MZ":
            raise Failure(f"{binary.name} isn't a Windows executable")
        if header[:4] != b"PE\0\0":
            raise Failure(f"{binary.name} has no PE signature")
        machine = struct.unpack_from("<H", header, 4)[0]
        if machine != _PE_MACHINES[target]:
            raise Failure(f"{binary.name} is built for machine {machine:#06x}, not {target}")
    elif os_name == "linux":
        if data[:4] != b"\x7fELF":
            raise Failure(f"{binary.name} isn't a Linux executable")
        machine = struct.unpack_from("<H", data, 18)[0]
        if machine != _ELF_MACHINES[target]:
            raise Failure(f"{binary.name} is built for machine {machine:#x}, not {target}")
        linked = subprocess.run(["ldd", str(binary)], capture_output=True, text=True)
        if "not found" in linked.stdout:
            raise Failure(f"{binary.name} needs libraries this system lacks:\n{linked.stdout}")
    else:
        archs = output(["lipo", "-archs", str(binary)]).split()
        if _MACHO_ARCHS[target] not in archs:
            raise Failure(f"{binary.name} contains {archs}, not {_MACHO_ARCHS[target]}")
        libraries = output(["otool", "-L", str(binary)])
        if re.search(r"/opt/homebrew/|/usr/local/(Cellar|opt)/", libraries):
            raise Failure(f"{binary.name} links to Homebrew paths, so it won't run elsewhere:\n{libraries}")
        output(["codesign", "--verify", "--strict", str(binary)])
