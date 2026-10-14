"""WinGet: opening the version-update PR in microsoft/winget-pkgs.

The first version of a package is a one-time manual submission; every later
release is a PR that WingetCreate opens from the token owner's fork. A missing
token or a missing package fails the job loudly instead of skipping it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path

from .. import stacks
from ..config import Config
from ..util import Failure, gh_json, repository, run, say, warn

WINGETCREATE_URL = "https://github.com/microsoft/winget-create/releases/download/v1.12.13.0/wingetcreate.exe"
WINGETCREATE_SHA256 = "24042bd37915805615e6cf969ac57c6439124c3fe85823327f5f3fb24bd9ffea"
ARCHITECTURES = {"windows-x64": "x64", "windows-arm64": "arm64"}
PULL_REQUEST = re.compile(r"https://github\.com/microsoft/winget-pkgs/pull/\d+")


def manifest_path(package_id: str) -> str:
    publisher, _, name = package_id.partition(".")
    return f"manifests/{publisher[0].lower()}/{publisher}/{name}"


def newest_installer_manifest(package_id: str) -> str | None:
    """The installer manifest of the newest version on winget-pkgs, or None if the package is absent."""
    result = run(
        ["gh", "api", f"repos/microsoft/winget-pkgs/contents/{manifest_path(package_id)}"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        # Only a missing package means "not on WinGet"; anything else (no
        # token, rate limits) must fail loudly instead of reading as absent.
        if "Not Found" in result.stderr or "404" in result.stdout + result.stderr:
            return None
        raise Failure(f"couldn't read {package_id} from winget-pkgs: {result.stderr.strip()}")
    versions = json.loads(result.stdout or "null")
    names = [item["name"] for item in versions or [] if item["type"] == "dir"]
    if not names:
        return None
    newest = max(names, key=lambda v: [int(p) if p.isdigit() else 0 for p in re.split(r"[.-]", v)])
    path = f"{manifest_path(package_id)}/{newest}/{package_id}.installer.yaml"
    content = gh_json("api", f"repos/microsoft/winget-pkgs/contents/{path}")
    return base64.b64decode(content["content"]).decode("utf-8")


def architectures(manifest: str) -> list[str]:
    return sorted(set(re.findall(r"(?m)^\s*-?\s*Architecture:\s*(\w+)", manifest)))


def layout_problem(manifest: str, binary: str) -> str | None:
    """wingetcreate keeps the manifest's path to the program inside the zip, so
    it must match the playbook's archives, which hold <binary>.exe at the top."""
    paths = re.findall(r"(?m)^\s*-?\s*RelativeFilePath:\s*(\S+)", manifest)
    wrong = sorted({path for path in paths if path.replace("\\", "/").lower() != f"{binary}.exe".lower()})
    if not wrong:
        return None
    return (
        f"the WinGet manifest expects {', '.join(wrong)} inside the zip, but the archives hold {binary}.exe "
        "at the top. Submit this version's manifest by hand once (RelativeFilePath: "
        f"{binary}.exe); later releases then update it automatically."
    )


def validation_note(binary: str, version_command: list[str]) -> str:
    """WinGet validation runs the installed program with no arguments, and a
    program that needs a terminal or a token exits 1 there. Telling the
    administrators which command exits 0 clears Validation-Executable-Error."""
    command = " ".join([f"{binary}.exe", *version_command])
    return (
        f"`{binary}.exe` exits 1 when started without arguments in the validation environment. "
        f"`{command}` exits 0 there and can be used for validation; every release's build runs it as a smoke test."
    )


def pull_request(output: str) -> str | None:
    match = PULL_REQUEST.search(output)
    return match.group(0) if match else None


def check(config: Config) -> None:
    """Dry run: the package must already exist, and should cover every Windows target."""
    release = config.release
    assert release is not None
    package_id = release.winget.id
    manifest = newest_installer_manifest(package_id)
    if manifest is None:
        raise Failure(
            f"{package_id} isn't in winget-pkgs yet. The first version is a one-time manual "
            f"submission (wingetcreate new <url>), with a PR comment saying `{release.binaries[0]}.exe "
            f"{' '.join(release.version_command)}` exits 0; later releases update it automatically."
        )
    problem = layout_problem(manifest, release.binaries[0])
    if problem:
        raise Failure(problem)
    present = architectures(manifest)
    wanted = [ARCHITECTURES[t] for t in release.targets if t in ARCHITECTURES]
    missing = sorted(set(wanted) - set(present))
    if missing:
        warn(
            f"{package_id}'s manifest has no {', '.join(missing)} installer yet; this release updates "
            f"{', '.join(present)} only. Add the others to the manifest once by hand."
        )
    say(f"{package_id} is on WinGet with {', '.join(present)}")


def submit(root: Path, config: Config, version: str) -> None:
    release = config.release
    assert release is not None
    token = os.environ.get("WINGET_TOKEN", "")
    if not token:
        raise Failure("WINGET_TOKEN isn't set in this repo's release environment")
    package_id = release.winget.id
    manifest = newest_installer_manifest(package_id)
    if manifest is None:
        raise Failure(f"{package_id} isn't in winget-pkgs yet; submit the first version by hand")
    problem = layout_problem(manifest, release.binaries[0])
    if problem:
        raise Failure(problem)
    present = architectures(manifest)

    repo = repository(root)
    urls = []
    for target in release.targets:
        architecture = ARCHITECTURES.get(target)
        if architecture is None:
            continue
        url = f"https://github.com/{repo}/releases/download/v{version}/{stacks.archive_name(config.name, target)}"
        if architecture in present:
            urls.append(f"{url}|{architecture}")
        else:
            warn(f"{package_id} has no {architecture} installer yet; skipped {url}")
    if not urls:
        raise Failure(f"none of this release's Windows files match {package_id}'s manifest")

    with tempfile.TemporaryDirectory(prefix="playbook-winget-") as temp:
        tool = Path(temp) / "wingetcreate.exe"
        with urllib.request.urlopen(WINGETCREATE_URL) as response:
            tool.write_bytes(response.read())
        digest = hashlib.sha256(tool.read_bytes()).hexdigest()
        if digest != WINGETCREATE_SHA256:
            raise Failure(f"wingetcreate.exe has checksum {digest}, expected {WINGETCREATE_SHA256}")
        result = run(
            [str(tool), "update", package_id, "--version", version, "--urls", *urls, "--submit"],
            env={"WINGET_CREATE_GITHUB_TOKEN": token},
            check=False,
            capture=True,
        )
    print(result.stdout, result.stderr, sep="\n")
    if result.returncode != 0:
        raise Failure(f"wingetcreate couldn't submit {package_id} {version}")
    say(f"submitted {package_id} {version} to winget-pkgs; Microsoft's moderators merge it")

    # The update is already submitted, so a missing comment only costs a manual one.
    url = pull_request(result.stdout + result.stderr)
    note = validation_note(release.binaries[0], release.version_command)
    if url is None:
        warn(f"couldn't find the winget-pkgs PR in wingetcreate's output; comment on it by hand: {note}")
        return
    commented = run(["gh", "pr", "comment", url, "--body", note], env={"GH_TOKEN": token}, check=False, capture=True)
    if commented.returncode != 0:
        warn(f"couldn't comment on {url}: {commented.stderr.strip()}; comment by hand: {note}")
        return
    say(f"told WinGet validation how to run {release.binaries[0]} on {url}")
