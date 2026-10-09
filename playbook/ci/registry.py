"""Language registries (crates.io, NuGet): dry runs on release PRs, uploads on release.

Uploads run from a job in the repo's own playbook.yml (through the registry
action), because registries match trusted publishing against the workflow file
that does the publishing. No token is stored anywhere: each run exchanges its
GitHub identity for a short-lived registry key.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..config import Config
from ..util import Failure, git, run, say, set_output
from . import winget

NUGET_SOURCE = "https://api.nuget.org/v3/index.json"


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
            package = _pack(root, config, version, Path(temp))
            say(f"NuGet would receive {package.name}")


def _pack(root: Path, config: Config, version: str, out: Path) -> Path:
    release = config.release
    assert release is not None
    package_id = release.package_id or config.name
    run(
        [
            "dotnet",
            "pack",
            str(root / (release.project or "")),
            "--configuration",
            "Release",
            "--output",
            str(out),
            f"-p:PackageId={package_id}",
            f"-p:Version={version}",
            "-p:ContinuousIntegrationBuild=true",
        ],
        cwd=root,
    )
    expected = out / f"{package_id}.{version}.nupkg"
    if not expected.is_file():
        found = [p.name for p in out.glob("*.nupkg")]
        raise Failure(f"dotnet pack made {found}, not {expected.name}")
    return expected


def publish(root: Path, config: Config) -> None:
    release = config.release
    if release is None or not release.registry:
        raise Failure("this repo doesn't publish to a registry")
    from .. import stacks

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
            package = _pack(root, config, version, Path(temp))
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
        say(f"published {package.name} to NuGet")
    else:
        raise Failure(f"publishing to {release.registry} isn't supported yet")
