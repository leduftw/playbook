"""What each language stack means for checks, version numbers and builds.

A [[check]] in playbook.toml only names its stack and directory; the commands
come from here unless the repo overrides them. Every check ends up with a
format, a lint and a test step, or an explicit reason for skipping one.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Check, ConfigError, Release

# Release targets: the GitHub runner that builds each one natively, and the
# names each toolchain uses for it.
TARGETS = {
    "macos-arm64": {
        "runner": "macos-15",
        "os": "macos",
        "rust": "aarch64-apple-darwin",
        "dotnet": "osx-arm64",
        "archive": "zip",
    },
    "macos-x64": {
        "runner": "macos-15-intel",
        "os": "macos",
        "rust": "x86_64-apple-darwin",
        "dotnet": "osx-x64",
        "archive": "zip",
    },
    "linux-x64": {
        "runner": "ubuntu-22.04",
        "os": "linux",
        "rust": "x86_64-unknown-linux-gnu",
        "dotnet": "linux-x64",
        "archive": "tar.gz",
    },
    "linux-arm64": {
        "runner": "ubuntu-22.04-arm",
        "os": "linux",
        "rust": "aarch64-unknown-linux-gnu",
        "dotnet": "linux-arm64",
        "archive": "tar.gz",
    },
    "windows-x64": {
        "runner": "windows-2025",
        "os": "windows",
        "rust": "x86_64-pc-windows-msvc",
        "dotnet": "win-x64",
        "archive": "zip",
    },
    "windows-arm64": {
        "runner": "windows-11-arm",
        "os": "windows",
        "rust": "aarch64-pc-windows-msvc",
        "dotnet": "win-arm64",
        "archive": "zip",
    },
}

# Where checks run. Linux builds of releases use 22.04 for an older glibc;
# checks use the current LTS.
CHECK_RUNNERS = {"linux": "ubuntu-24.04", "macos": "macos-15", "windows": "windows-2025"}

# Tools the checks run, pinned so a new release of one can't turn every repo
# red overnight; they move with a playbook release.
RUFF = "ruff@0.17.0"

# Files a self-contained .NET app must carry beside its own licence.
DOTNET_NOTICES = {
    "LICENSE.txt": "DOTNET-LICENSE.txt",
    "ThirdPartyNotices.txt": "DOTNET-THIRD-PARTY-NOTICES.txt",
}


def archive_name(name: str, target: str) -> str:
    return f"{name}-{target}.{TARGETS[target]['archive']}"


# --- checks -------------------------------------------------------------


def check_commands(root: Path, check: Check) -> dict[str, list[str]]:
    """prepare/format/lint/test commands for one check, with stack defaults filled in."""
    directory = root / check.dir
    if check.stack == "rust":
        defaults = {
            "prepare": [],
            "format": ["cargo fmt --all --check"],
            "lint": ["cargo clippy --all-targets --all-features --locked -- -D warnings"],
            "test": ["cargo test --all-features --locked"],
        }
    elif check.stack == "dotnet":
        target = check.solution or _dotnet_target(directory)
        locked = " --locked-mode" if any(directory.rglob("packages.lock.json")) else ""
        defaults = {
            "prepare": [f"dotnet restore {target}{locked}"],
            "format": [f"dotnet format {target} --no-restore --verify-no-changes"],
            "lint": [f"dotnet build {target} --configuration Release --no-restore -warnaserror"],
            "test": [f"dotnet test {target} --configuration Release --no-restore"],
        }
    elif check.stack == "python":
        tests = ["python -m unittest discover -s tests"] if (directory / "tests").is_dir() else []
        defaults = {
            "prepare": [],
            "format": [f"uvx {RUFF} format --check ."],
            "lint": [f"uvx {RUFF} check ."],
            "test": tests,
        }
    else:  # node
        manager = node_manager(directory)
        install = {
            "pnpm": "pnpm install --frozen-lockfile",
            "yarn": "yarn install --immutable",
            "npm": "npm ci",
        }[manager]
        defaults = {"prepare": [install], "format": [], "lint": [], "test": [f"{manager} test"]}

    commands = {"prepare": check.setup + defaults["prepare"]}
    for step in ("format", "lint", "test"):
        configured = getattr(check, step)
        commands[step] = [] if step in check.skip else (configured if configured is not None else defaults[step])
        if not commands[step] and step not in check.skip:
            raise ConfigError(
                f"the {check.stack} check in {check.dir!r} has no {step} command; "
                f'set {step} = [...] or skip.{step} = "<reason>"'
            )
    return commands


def node_manager(directory: Path) -> str:
    if (directory / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (directory / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _dotnet_target(directory: Path) -> str:
    for pattern in ("*.slnx", "*.sln", "*.csproj"):
        found = sorted(directory.glob(pattern))
        if len(found) == 1:
            return found[0].name
        if len(found) > 1:
            names = ", ".join(path.name for path in found)
            raise ConfigError(f"several {pattern} files in {directory}: {names}; set solution")
    raise ConfigError(f"no .slnx, .sln or .csproj in {directory}; set solution")


# --- versions -------------------------------------------------------------


def version_files(root: Path, release: Release) -> list[Path]:
    """Files whose version a release changes."""
    if release.stack == "rust":
        manifest, _ = _cargo_version_location(root, release)
        lock = _cargo_lock(root, release)
        return [manifest] + ([lock] if lock else [])
    return [_dotnet_version_file(root, release)]


def read_version(root: Path, release: Release) -> str:
    if release.stack == "rust":
        manifest, table = _cargo_version_location(root, release)
        return _toml_table_value(manifest.read_text(encoding="utf-8"), table, "version", manifest)
    path = _dotnet_version_file(root, release)
    return _single_match(_DOTNET_VERSION, path.read_text(encoding="utf-8"), path).group(2)


def write_version(root: Path, release: Release, version: str) -> list[Path]:
    """Set the release version everywhere it's recorded; returns the files changed."""
    old = read_version(root, release)
    changed = []
    if release.stack == "rust":
        manifest, table = _cargo_version_location(root, release)
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(_set_toml_table_value(text, table, "version", version, manifest))
        changed.append(manifest)
        lock = _cargo_lock(root, release)
        if lock:
            lock.write_text(_set_cargo_lock_version(lock, _crate_name(root, release), old, version))
            changed.append(lock)
    else:
        path = _dotnet_version_file(root, release)
        text = path.read_text(encoding="utf-8")
        _single_match(_DOTNET_VERSION, text, path)
        path.write_text(_DOTNET_VERSION.sub(lambda m: f"{m.group(1)}{version}{m.group(3)}", text))
        changed.append(path)
    return changed


_DOTNET_VERSION = re.compile(r"(<Version>)([^<]+)(</Version>)")


def _dotnet_version_file(root: Path, release: Release) -> Path:
    project = root / (release.project or "")
    candidates = [project, root / "Directory.Build.props"]
    for path in candidates:
        if path.is_file() and _DOTNET_VERSION.search(path.read_text(encoding="utf-8")):
            return path
    raise ConfigError(f"no <Version> in {release.project} or Directory.Build.props")


def _cargo_version_location(root: Path, release: Release) -> tuple[Path, str]:
    """The manifest and table holding the crate's version (a workspace can own it)."""
    manifest = root / release.dir / "Cargo.toml"
    if not manifest.is_file():
        raise ConfigError(f"no Cargo.toml in {release.dir}")
    text = manifest.read_text(encoding="utf-8")
    if re.search(r"(?m)^version\.workspace\s*=\s*true", text):
        return root / "Cargo.toml", "workspace.package"
    return manifest, "package"


def _crate_name(root: Path, release: Release) -> str:
    manifest = root / release.dir / "Cargo.toml"
    return _toml_table_value(manifest.read_text(encoding="utf-8"), "package", "name", manifest)


def _cargo_lock(root: Path, release: Release) -> Path | None:
    for directory in (root / release.dir, root):
        lock = directory / "Cargo.lock"
        if lock.is_file():
            return lock
    return None


def _set_cargo_lock_version(lock: Path, crate: str, old: str, new: str) -> str:
    text = lock.read_text(encoding="utf-8")
    block = re.compile(rf'(\[\[package\]\]\nname = "{re.escape(crate)}"\nversion = ")({re.escape(old)})(")')
    if len(block.findall(text)) != 1:
        raise ConfigError(f"could not find {crate} {old} exactly once in {lock}")
    return block.sub(lambda m: f"{m.group(1)}{new}{m.group(3)}", text)


def _table_span(text: str, table: str) -> tuple[int, int] | None:
    header = re.search(rf"(?m)^\[{re.escape(table)}\]\s*$", text)
    if not header:
        return None
    following = re.compile(r"(?m)^\[").search(text, header.end())
    return header.end(), following.start() if following else len(text)


def _toml_table_value(text: str, table: str, key: str, path: Path) -> str:
    span = _table_span(text, table)
    if span:
        match = re.compile(rf'(?m)^{key}\s*=\s*"([^"]*)"').search(text, *span)
        if match:
            return match.group(1)
    raise ConfigError(f'no {key} = "..." in [{table}] of {path}')


def _set_toml_table_value(text: str, table: str, key: str, value: str, path: Path) -> str:
    span = _table_span(text, table)
    pattern = re.compile(rf'(?m)^({key}\s*=\s*")([^"]*)(")')
    match = pattern.search(text, *span) if span else None
    if not match:
        raise ConfigError(f'no {key} = "..." in [{table}] of {path}')
    return text[: match.start(2)] + value + text[match.end(2) :]


def _single_match(pattern: re.Pattern[str], text: str, path: Path) -> re.Match[str]:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ConfigError(f"expected exactly one {pattern.pattern} in {path}, found {len(matches)}")
    return matches[0]
