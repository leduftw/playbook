"""Reads .github/playbook.toml: the facts about one repo that the playbook needs.

Everything that is the same for every repo lives in the playbook. This file only
says what the repo is: its name, whether it's published, how its code is
checked and, for a published repo, what a release contains. Unknown keys are
errors, so a typo can't silently switch something off.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(".github") / "playbook.toml"

STACKS = ("rust", "dotnet", "python", "node")
OSES = ("linux", "macos", "windows")
STEPS = ("format", "lint", "test")
TARGETS = (
    "macos-arm64",
    "macos-x64",
    "linux-x64",
    "linux-arm64",
    "windows-x64",
    "windows-arm64",
)
CHANNELS = ("homebrew", "winget", "installers", "crates-io", "nuget", "pypi")
REGISTRIES = {"crates-io": "rust", "nuget": "dotnet", "pypi": "python"}
SMOKE_GROUPS = ("all", "unix", "macos", "linux", "windows")
MACOS_NAMES = {
    11: "big_sur",
    12: "monterey",
    13: "ventura",
    14: "sonoma",
    15: "sequoia",
    26: "tahoe",
}

_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


class ConfigError(Exception):
    pass


@dataclass
class Check:
    stack: str
    dir: str = "."
    os: list[str] = field(default_factory=list)
    apt: list[str] = field(default_factory=list)
    setup: list[str] = field(default_factory=list)
    format: list[str] | None = None
    lint: list[str] | None = None
    test: list[str] | None = None
    skip: dict[str, str] = field(default_factory=dict)
    solution: str | None = None


@dataclass
class Homebrew:
    desc: str = ""
    linux_dependencies: list[str] = field(default_factory=list)
    install: list[str] = field(default_factory=list)


@dataclass
class Winget:
    id: str = ""


@dataclass
class Installer:
    notes: list[str] = field(default_factory=list)
    macos_notes: list[str] = field(default_factory=list)
    linux_notes: list[str] = field(default_factory=list)
    windows_notes: list[str] = field(default_factory=list)


@dataclass
class Release:
    stack: str
    dir: str = "."
    project: str | None = None
    binaries: list[str] = field(default_factory=list)
    macos_binaries: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=lambda: ["LICENSE", "README.md"])
    targets: list[str] = field(default_factory=lambda: list(TARGETS))
    apt: list[str] = field(default_factory=list)
    version_command: list[str] = field(default_factory=lambda: ["--version"])
    version_output: str | None = None
    channels: list[str] = field(default_factory=list)
    license: str = "MIT"
    macos_minimum: str | None = None
    publish_properties: list[str] = field(default_factory=list)
    macos_publish_properties: list[str] = field(default_factory=list)
    other_publish_properties: list[str] = field(default_factory=list)
    package_id: str | None = None
    registry_user: str = "leduftw"
    build_variables: list[str] = field(default_factory=list)
    smoke: dict[str, list[str]] = field(default_factory=dict)
    homebrew: Homebrew = field(default_factory=Homebrew)
    winget: Winget = field(default_factory=Winget)
    installer: Installer = field(default_factory=Installer)

    @property
    def registry(self) -> str | None:
        return next((c for c in self.channels if c in REGISTRIES), None)


@dataclass
class Config:
    name: str
    published: bool
    checks: list[Check]
    release: Release | None = None

    def check_os(self, check: Check) -> list[str]:
        """Where a check runs: what it asked for, else everywhere when published."""
        if check.os:
            return check.os
        return list(OSES) if self.published else ["linux"]


def load(root: Path | str = ".") -> Config:
    path = Path(root) / CONFIG_PATH
    if not path.is_file():
        raise ConfigError(f"{CONFIG_PATH} not found; this repo doesn't follow the playbook yet")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{CONFIG_PATH}: {error}") from error
    return parse(data, default_name=Path(root).resolve().name)


def parse(data: dict[str, Any], default_name: str = "") -> Config:
    table = _Table(data, "playbook.toml")
    name = table.text("name", default_name)
    if not _NAME.fullmatch(name):
        raise ConfigError(f"name {name!r} must be lowercase letters, digits and hyphens")
    published = table.flag("published", False)
    checks = [_check(_Table(item, f"check[{i}]")) for i, item in enumerate(table.tables("check"))]
    if not checks:
        raise ConfigError("playbook.toml needs at least one [[check]]")
    release_data = table.table("release")
    table.done()

    release = None
    if published:
        if release_data is None:
            raise ConfigError("a published repo needs a [release] table")
        release = _release(_Table(release_data, "release"), name)
    elif release_data is not None:
        raise ConfigError("[release] only applies to published repos; set published = true")
    return Config(name=name, published=published, checks=checks, release=release)


def _check(table: _Table) -> Check:
    check = Check(
        stack=table.choice("stack", STACKS),
        dir=table.text("dir", "."),
        os=table.choices("os", OSES),
        apt=table.texts("apt"),
        setup=table.texts("setup"),
        format=table.texts_or_none("format"),
        lint=table.texts_or_none("lint"),
        test=table.texts_or_none("test"),
        solution=table.text_or_none("solution"),
    )
    skip = table.table("skip") or {}
    for step, reason in skip.items():
        if step not in STEPS:
            raise ConfigError(f"{table.where}.skip: unknown step {step!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"{table.where}.skip.{step} needs the reason as text")
        if getattr(check, step):
            raise ConfigError(f"{table.where}: {step} is both configured and skipped")
        check.skip[step] = reason
    table.done()
    return check


def _release(table: _Table, name: str) -> Release:
    release = Release(
        stack=table.choice("stack", ("rust", "dotnet")),
        dir=table.text("dir", "."),
        project=table.text_or_none("project"),
        binaries=table.texts("binaries") or [name],
        macos_binaries=table.texts("macos-binaries"),
        files=table.texts("files", ["LICENSE", "README.md"]),
        targets=table.choices("targets", TARGETS) or list(TARGETS),
        apt=table.texts("apt"),
        version_command=table.texts("version-command", ["--version"]),
        version_output=table.text_or_none("version-output"),
        channels=table.choices("channels", CHANNELS),
        license=table.text("license", "MIT"),
        macos_minimum=table.text_or_none("macos-minimum"),
        publish_properties=table.texts("publish-properties"),
        macos_publish_properties=table.texts("macos-publish-properties"),
        other_publish_properties=table.texts("other-publish-properties"),
        package_id=table.text_or_none("package-id"),
        registry_user=table.text("registry-user", "leduftw"),
        build_variables=table.texts("build-variables"),
    )
    smoke = table.table("smoke") or {}
    for group, commands in smoke.items():
        if group not in SMOKE_GROUPS:
            raise ConfigError(f"release.smoke: unknown group {group!r}")
        release.smoke[group] = _Table({"x": commands}, "release.smoke").texts("x")

    homebrew = _Table(table.table("homebrew") or {}, "release.homebrew")
    release.homebrew = Homebrew(
        desc=homebrew.text("desc", ""),
        linux_dependencies=homebrew.texts("linux-dependencies"),
        install=homebrew.texts("install"),
    )
    homebrew.done()

    winget = _Table(table.table("winget") or {}, "release.winget")
    release.winget = Winget(id=winget.text("id", f"leduftw.{name}"))
    winget.done()

    installer = _Table(table.table("installer") or {}, "release.installer")
    release.installer = Installer(
        notes=installer.texts("notes"),
        macos_notes=installer.texts("macos-notes"),
        linux_notes=installer.texts("linux-notes"),
        windows_notes=installer.texts("windows-notes"),
    )
    installer.done()
    table.done()

    if release.stack == "dotnet" and not release.project:
        raise ConfigError("release.project is required for a dotnet release")
    registry = release.registry
    if registry and REGISTRIES[registry] != release.stack:
        raise ConfigError(f"{registry} can't publish a {release.stack} project")
    if sum(c in REGISTRIES for c in release.channels) > 1:
        raise ConfigError("release.channels can name only one registry")
    if "homebrew" in release.channels and not release.homebrew.desc:
        raise ConfigError("release.homebrew.desc is required when publishing to Homebrew")
    if release.macos_minimum is not None:
        macos_major(release.macos_minimum)
    return release


def macos_major(minimum: str) -> int:
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?", minimum)
    if not match or int(match.group(1)) not in MACOS_NAMES:
        known = ", ".join(str(m) for m in MACOS_NAMES)
        raise ConfigError(f"macos-minimum {minimum!r} must start with one of {known}")
    return int(match.group(1))


class _Table:
    """Typed, strict access to one TOML table; done() rejects leftover keys."""

    def __init__(self, data: Any, where: str) -> None:
        if not isinstance(data, dict):
            raise ConfigError(f"{where} must be a table")
        self.data = dict(data)
        self.where = where

    def _take(self, key: str) -> Any:
        return self.data.pop(key, None)

    def text(self, key: str, default: str) -> str:
        value = self._take(key)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ConfigError(f"{self.where}.{key} must be text")
        return value

    def text_or_none(self, key: str) -> str | None:
        value = self._take(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{self.where}.{key} must be text")
        return value

    def flag(self, key: str, default: bool) -> bool:
        value = self._take(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ConfigError(f"{self.where}.{key} must be true or false")
        return value

    def texts(self, key: str, default: list[str] | None = None) -> list[str]:
        value = self._take(key)
        if value is None:
            return list(default or [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{self.where}.{key} must be a list of text")
        return value

    def texts_or_none(self, key: str) -> list[str] | None:
        if key not in self.data:
            return None
        return self.texts(key)

    def choice(self, key: str, options: tuple[str, ...]) -> str:
        value = self._take(key)
        if value not in options:
            raise ConfigError(f"{self.where}.{key} must be one of {', '.join(options)}")
        return value

    def choices(self, key: str, options: tuple[str, ...]) -> list[str]:
        values = self.texts(key)
        for value in values:
            if value not in options:
                raise ConfigError(f"{self.where}.{key}: {value!r} isn't one of {', '.join(options)}")
        return values

    def tables(self, key: str) -> list[Any]:
        value = self._take(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ConfigError(f"{self.where}.{key} must be a list of tables ([[{key}]])")
        return value

    def table(self, key: str) -> dict[str, Any] | None:
        value = self._take(key)
        if value is not None and not isinstance(value, dict):
            raise ConfigError(f"{self.where}.{key} must be a table")
        return value

    def done(self) -> None:
        if self.data:
            keys = ", ".join(sorted(self.data))
            raise ConfigError(f"{self.where}: unknown key(s) {keys}")
