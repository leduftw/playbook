"""The files the playbook owns inside every repo, rendered for one repo.

These are identical across repos apart from the facts in playbook.toml. `sync`
writes them, `audit` compares them, and nobody edits them by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import REPOSITORY, ROOT, major_ref
from .config import Config

TEMPLATES = ROOT / "templates"
BEGIN = "<!-- playbook:begin -->"
END = "<!-- playbook:end -->"

CHANNEL_NAMES = {
    "homebrew": "the Homebrew tap",
    "winget": "WinGet",
    "installers": "the one-line installers",
    "crates-io": "crates.io",
    "nuget": "NuGet",
    "pypi": "PyPI",
}


def template(name: str, values: dict[str, str], flags: dict[str, bool]) -> str:
    """Fill @@name@@ values and keep @@#flag@@/@@^flag@@ blocks by their flags."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")

    def block(match: re.Match[str]) -> str:
        negate, flag, body = match.group(1) == "^", match.group(2), match.group(3)
        if flag not in flags:
            raise KeyError(f"{name}: unknown flag {flag}")
        return body if flags[flag] != negate else ""

    text = re.sub(r"@@([#^])(\w+)@@\n(.*?)@@/\2@@\n", block, text, flags=re.S)

    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"{name}: unknown value {key}")
        return values[key]

    return re.sub(r"@@(\w+)@@", value, text)


def managed_files(root: Path, config: Config, self_repo: bool = False) -> dict[str, str]:
    """Every file the playbook owns in a repo, except AGENTS.md (see agents_md)."""
    ref = major_ref()
    registry = config.release.registry if config.release else None
    if self_repo:
        pipeline = "./.github/workflows/pipeline.yml"
        title = "./.github/workflows/title.yml"
        script_ref = "${{ github.event.pull_request.head.sha || github.sha }}"
    else:
        pipeline = f"{REPOSITORY}/.github/workflows/pipeline.yml@{ref}"
        title = f"{REPOSITORY}/.github/workflows/title.yml@{ref}"
        script_ref = ref
    values = {
        "major": ref,
        "pipeline": pipeline,
        "title": title,
        "ref": script_ref,
        "registry_action": f"{REPOSITORY}/.github/actions/registry@{ref}",
    }
    flags = {"published": config.published, "registry": bool(registry)}

    files = {
        ".github/workflows/playbook.yml": template("workflows/playbook.yml", values, flags),
        ".github/workflows/playbook-title.yml": template("workflows/playbook-title.yml", values, flags),
        ".github/dependabot.yml": dependabot(root, config),
        ".githooks/pre-commit": (TEMPLATES / "githooks/pre-commit").read_text(encoding="utf-8"),
        ".githooks/pre-push": (TEMPLATES / "githooks/pre-push").read_text(encoding="utf-8"),
    }
    if config.published:
        files[".github/release.yml"] = template("release.yml", values, flags)
    return files


EXECUTABLE = {".githooks/pre-commit", ".githooks/pre-push"}


def agents_section(config: Config, self_repo: bool = False) -> str:
    channels = []
    if config.release:
        channels = [CHANNEL_NAMES[c] for c in config.release.channels]
    if len(channels) > 1:
        channel_text = ", ".join(channels[:-1]) + " and " + channels[-1]
    else:
        channel_text = "".join(channels) or "nothing else"
    return template(
        "agents.md",
        {"major": major_ref(), "name": config.name, "channels": channel_text},
        {"published": config.published, "local": not config.published and not self_repo, "self": self_repo},
    )


def agents_md(existing: str | None, config: Config, self_repo: bool = False) -> str:
    """AGENTS.md with the managed section replaced, or appended if it's missing."""
    section = agents_section(config, self_repo)
    if existing is None:
        return f"# {config.name}\n\n{section}"
    start, end = existing.find(BEGIN), existing.find(END)
    if start != -1 and end != -1 and start < end:
        tail = existing[end + len(END) :]
        return existing[:start] + section.rstrip("\n") + tail
    return existing.rstrip("\n") + "\n\n" + section


def dependabot(root: Path, config: Config) -> str:
    entries: list[tuple[str, str]] = []

    def add(ecosystem: str, directory: str) -> None:
        location = "/" + directory.strip("./").strip("/") if directory not in (".", "") else "/"
        if (ecosystem, location) not in entries:
            entries.append((ecosystem, location))

    for check in config.checks:
        directory = root / check.dir
        if check.stack == "rust":
            add("cargo", check.dir)
        elif check.stack == "dotnet":
            add("nuget", check.dir)
        elif check.stack == "node":
            add("npm", check.dir)
        elif check.stack == "python":
            if (directory / "uv.lock").is_file():
                add("uv", check.dir)
            elif list(directory.glob("requirements*.txt")) or _has_python_dependencies(directory):
                add("pip", check.dir)
    add("github-actions", ".")

    lines = [
        f"# Managed by leduftw/playbook {major_ref()}. Change it there, not here.",
        "# Minor and patch updates are grouped and merge themselves once checks pass;",
        "# each major update arrives as its own PR for an agent session to handle.",
        "version: 2",
        "updates:",
    ]
    for ecosystem, location in entries:
        lines += [
            f"  - package-ecosystem: {ecosystem}",
            f'    directory: "{location}"',
            "    schedule:",
            "      interval: weekly",
            "      day: monday",
            "      timezone: Europe/Belgrade",
            "    labels:",
            "      - dependencies",
            "    groups:",
            "      minor-and-patch:",
            "        patterns:",
            '          - "*"',
            "        update-types:",
            "          - minor",
            "          - patch",
        ]
        if ecosystem == "github-actions":
            lines += [
                "    # The playbook itself moves through playbook sync, not Dependabot.",
                "    ignore:",
                '      - dependency-name: "leduftw/playbook*"',
            ]
    return "\n".join(lines) + "\n"


def _has_python_dependencies(directory: Path) -> bool:
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
        return False
    import tomllib

    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    optional = project.get("optional-dependencies", {})
    return bool(project.get("dependencies")) or any(optional.values())
