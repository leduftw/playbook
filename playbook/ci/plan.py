"""Decides what one pipeline run does, so every later job only follows orders.

Reads playbook.toml and the event, then answers: which checks run where, and
whether this run verifies a release (a PR that changes the version), publishes
one (that PR merged to main), or neither.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import semver, stacks
from ..config import Config, load
from ..util import Failure, remote_tags, set_output, summary


def release_mode(event: str, ref: str, current: str, tags: list[str]) -> str:
    """none, verify or publish, after checking the version is a legal next step."""
    latest = semver.latest_tag(tags)
    version = semver.parse(current)
    if str(version) == semver.UNRELEASED:
        if latest is not None:
            raise Failure(f"the version went back to {semver.UNRELEASED} after {latest} was released")
        return "none"
    if latest is not None and version == latest:
        return "none"
    problem = semver.check_next(latest, version)
    if problem:
        raise Failure(problem)
    if event in ("push", "workflow_dispatch") and ref == "refs/heads/main":
        return "publish"
    return "verify"


def check_matrix(root: Path, config: Config) -> list[dict[str, object]]:
    entries = []
    for check in config.checks:
        commands = stacks.check_commands(root, check)
        oses = config.check_os(check)
        # Formatting and lint results don't depend on the OS, so they run once.
        once_on = "linux" if "linux" in oses else oses[0]
        for os_name in oses:
            steps = ["format", "lint", "test"] if os_name == once_on else ["test"]
            lines = ["set -euo pipefail"]
            for command in commands["prepare"]:
                lines += [f"echo '::group::{_quote(command)}'", command, "echo '::endgroup::'"]
            for step in steps:
                for command in commands[step]:
                    lines += [f"echo '▶ {step}: {_quote(command)}'", command]
            skipped = [f"{step} ({check.skip[step]})" for step in steps if step in check.skip]
            entries.append(
                {
                    "label": f"{check.stack} in {check.dir} on {os_name}",
                    "stack": check.stack,
                    "dir": check.dir,
                    "os": os_name,
                    "runner": stacks.CHECK_RUNNERS[os_name],
                    "apt": " ".join(check.apt),
                    "script": "\n".join(lines),
                    "skipped": "; ".join(skipped),
                    "rust_components": "rustfmt, clippy" if "format" in steps else "",
                    "global_json": _find(root, check.dir, "global.json"),
                    "node_version_file": _find(root, check.dir, ".nvmrc", ".node-version"),
                    "python_version_file": _find(root, check.dir, ".python-version"),
                    "node_manager": stacks.node_manager(root / check.dir) if check.stack == "node" else "",
                }
            )
    return entries


def build_matrix(root: Path, config: Config) -> list[dict[str, object]]:
    release = config.release
    assert release is not None
    entries = []
    for target in release.targets:
        info = stacks.TARGETS[target]
        entries.append(
            {
                "target": target,
                "runner": info["runner"],
                "os": info["os"],
                "stack": release.stack,
                "rust_target": info["rust"],
                "dotnet_rid": info["dotnet"],
                "apt": " ".join(release.apt),
                "global_json": _find(root, release.dir, "global.json"),
            }
        )
    return entries


def _find(root: Path, directory: str, *names: str) -> str:
    for base in (Path(directory), Path(".")):
        for name in names:
            if (root / base / name).is_file():
                return (base / name).as_posix()
    return ""


def _quote(command: str) -> str:
    return command.replace("'", "'\\''")


def main(root: Path) -> None:
    config = load(root)
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    ref = os.environ.get("GITHUB_REF", "")

    mode, version = "none", ""
    if config.release:
        version = stacks.read_version(root, config.release)
        mode = release_mode(event, ref, version, remote_tags(root))

    checks = check_matrix(root, config)
    build = build_matrix(root, config) if mode != "none" else []
    release = config.release
    channels = release.channels if release else []

    set_output("name", config.name)
    set_output("published", "true" if config.published else "false")
    set_output("mode", mode)
    set_output("version", version)
    set_output("tag", f"v{version}" if version else "")
    set_output("checks", {"include": checks})
    set_output("build", {"include": build})
    set_output("homebrew", "true" if "homebrew" in channels else "false")
    set_output("winget", "true" if "winget" in channels else "false")
    set_output("registry", (release.registry or "") if release else "")

    lines = [f"### {config.name}", ""]
    lines.append(f"- checks: {len(checks)} job(s)")
    if config.published:
        verb = {"none": "no release", "verify": "verifies", "publish": "publishes"}[mode]
        lines.append(f"- release: {verb}" + (f" {version}" if mode != "none" else ""))
        if mode != "none":
            lines.append(f"- channels: {', '.join(channels) or 'GitHub Releases only'}")
    summary("\n".join(lines))
    if os.environ.get("PLAYBOOK_DEBUG"):
        print(json.dumps({"checks": checks, "build": build}, indent=2))
