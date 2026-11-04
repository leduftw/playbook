"""The Homebrew formula: rendering it, test-installing it, and updating the tap.

Homebrew only installs formulae that live in a tap, so both the dry run on a
release PR and the real update install the formula from a tap before anything
is committed. The dry run points at the archives just built; the real update
points at the published release.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .. import stacks
from ..config import MACOS_NAMES, Config, macos_major
from ..util import Failure, git, repository, run, say

TAP = "leduftw/homebrew-tap"
TAP_NAME = "leduftw/tap"


def class_name(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[-_]", name) if part)


def _ruby(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(config: Config, version: str, repo: str, urls: dict[str, str], digests: dict[str, str]) -> str:
    release = config.release
    assert release is not None
    repo_url = f"https://github.com/{repo}"
    lines = [
        f"class {class_name(config.name)} < Formula",
        f"  desc {_ruby(release.homebrew.desc)}",
        f"  homepage {_ruby(repo_url)}",
        f"  version {_ruby(version)}",
        f"  license {_ruby(release.license)}",
    ]

    for os_name, block in (("macos", "on_macos"), ("linux", "on_linux")):
        arm, intel = f"{os_name}-arm64", f"{os_name}-x64"
        present = [t for t in (arm, intel) if t in release.targets]
        if not present:
            continue
        lines += ["", f"  {block} do"]
        if os_name == "macos" and release.macos_minimum:
            lines.append(f"    depends_on macos: :{MACOS_NAMES[macos_major(release.macos_minimum)]}")
        dependencies = {"macos": release.homebrew.macos_dependencies, "linux": release.homebrew.linux_dependencies}
        lines += [f"    depends_on {_ruby(dep)}" for dep in dependencies[os_name]]
        if len(present) == 2:
            lines += [
                "    if Hardware::CPU.arm?",
                f"      url {_ruby(urls[arm])}",
                f"      sha256 {_ruby(digests[arm])}",
                "    else",
                f"      url {_ruby(urls[intel])}",
                f"      sha256 {_ruby(digests[intel])}",
                "    end",
            ]
        else:
            only = present[0]
            lines += [f"    url {_ruby(urls[only])}", f"    sha256 {_ruby(digests[only])}"]
        lines.append("  end")

    lines += ["", "  def install"]
    if release.macos_minimum and "." in release.macos_minimum:
        minimum = release.macos_minimum
        lines.append(
            f'    odie "{config.name} needs macOS {minimum} or newer" if OS.mac? && MacOS.version < {_ruby(minimum)}'
        )
    if stacks.runtime_folders(release, version):
        # The program finds its runtime beside it, and follows the link to get there.
        for binary in release.binaries:
            lines.append(f"    libexec.install {_ruby(binary)}, {_ruby(stacks.python_runtime(binary, version))}")
            lines.append(f"    bin.install_symlink libexec/{_ruby(binary)}")
    else:
        lines += [f"    bin.install {_ruby(binary)}" for binary in release.binaries]
    lines += [f"    bin.install {_ruby(binary)} if OS.mac?" for binary in release.macos_binaries]
    lines += [f"    {line}" for line in release.homebrew.install]
    lines.append("  end")

    command = " ".join([f"#{{bin}}/{release.binaries[0]}", *release.version_command])
    lines += [
        "",
        "  test do",
        f'    assert_match version.to_s, shell_output("{command}")',
        "  end",
        "end",
    ]
    return "\n".join(lines) + "\n"


def _digests(config: Config, dist: Path) -> dict[str, str]:
    release = config.release
    assert release is not None
    digests = {}
    for target in release.targets:
        if target.startswith("windows"):
            continue
        archive = dist / stacks.archive_name(config.name, target)
        if not archive.is_file():
            raise Failure(f"{archive} is missing")
        digests[target] = hashlib.sha256(archive.read_bytes()).hexdigest()
    return digests


def _install_and_test(config: Config, tap_dir: Path, tap_name: str, version: str) -> None:
    formula = f"{tap_name}/{config.name}"
    run(["brew", "tap", tap_name, str(tap_dir)])
    try:
        run(["brew", "install", "--formula", formula])
        run(["brew", "test", formula])
        prefix = run(["brew", "--prefix", formula], capture=True).stdout.strip()
        release = config.release
        assert release is not None
        reported = run([f"{prefix}/bin/{release.binaries[0]}", *release.version_command], capture=True).stdout.strip()
        if version not in reported:
            raise Failure(f"the Homebrew install reported {reported!r}, not {version}")
        for binary in release.macos_binaries:
            if not Path(prefix, "bin", binary).exists():
                raise Failure(f"the Homebrew install is missing {binary}")
        say(f"brew installed and tested {formula} {version}")
    finally:
        run(["brew", "uninstall", "--formula", formula], check=False)
        run(["brew", "untap", tap_name], check=False)


def dry_run(root: Path, config: Config, version: str, dist: Path) -> None:
    """Install the formula from a throwaway local tap, using the archives just built."""
    repo = repository(root)
    digests = _digests(config, dist)
    urls = {target: (dist / stacks.archive_name(config.name, target)).resolve().as_uri() for target in digests}
    text = render(config, version, repo, urls, digests)

    with tempfile.TemporaryDirectory(prefix="playbook-tap-") as temp:
        tap_dir = Path(temp) / "homebrew-dry-run"
        (tap_dir / "Formula").mkdir(parents=True)
        (tap_dir / "Formula" / f"{config.name}.rb").write_text(text, encoding="utf-8")
        run(["ruby", "-c", str(tap_dir / "Formula" / f"{config.name}.rb")])
        git("init", "-q", "-b", "main", cwd=tap_dir)
        git("add", ".", cwd=tap_dir)
        git(
            "-c",
            "user.name=playbook",
            "-c",
            "user.email=playbook@localhost",
            "commit",
            "-q",
            "-m",
            "dry run",
            cwd=tap_dir,
        )
        _install_and_test(config, tap_dir, "leduftw/playbook-dry-run", version)
    say("the formula installs and passes its test")


def publish(root: Path, config: Config, version: str, dist: Path, tap_dir: Path) -> None:
    """Point the formula at the published release, prove it installs, then commit it to the tap."""
    token = os.environ.get("HOMEBREW_TAP_TOKEN")
    if not token:
        raise Failure("HOMEBREW_TAP_TOKEN isn't set; the pipeline gets it from the leduftw-tap app")
    repo = repository(root)
    digests = _digests(config, dist)
    urls = {
        target: f"https://github.com/{repo}/releases/download/v{version}/{stacks.archive_name(config.name, target)}"
        for target in digests
    }
    text = render(config, version, repo, urls, digests)
    formula = tap_dir / "Formula" / f"{config.name}.rb"
    previous = formula.read_bytes().decode("utf-8") if formula.is_file() else None
    if previous == text:
        say(f"the tap already has {config.name} {version}")
        return
    formula.parent.mkdir(parents=True, exist_ok=True)
    formula.write_text(text, encoding="utf-8")
    run(["ruby", "-c", str(formula)])

    with tempfile.TemporaryDirectory(prefix="playbook-tap-") as temp:
        copy = Path(temp) / "homebrew-tap"
        shutil.copytree(tap_dir, copy, ignore=shutil.ignore_patterns(".git"))
        git("init", "-q", "-b", "main", cwd=copy)
        git("add", ".", cwd=copy)
        git("-c", "user.name=playbook", "-c", "user.email=playbook@localhost", "commit", "-q", "-m", "verify", cwd=copy)
        _install_and_test(config, copy, "leduftw/playbook-verify", version)

    head = git("rev-parse", "HEAD", cwd=tap_dir)
    commit_to_tap(
        formula.relative_to(tap_dir).as_posix(), text, f"update {config.name} to {version}", head, token, previous
    )


COMMIT_ON_BRANCH = """
mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { url } }
}
"""

FORMULA_AT_COMMIT = """
query($owner: String!, $name: String!, $expression: String!) {
  repository(owner: $owner, name: $name) {
    object(expression: $expression) { ... on Blob { text } }
  }
}
"""


def _tap_formula(path: str, head: str, env: dict[str, str]) -> str | None:
    owner, name = TAP.split("/")
    body = {
        "query": FORMULA_AT_COMMIT,
        "variables": {"owner": owner, "name": name, "expression": f"{head}:{path}"},
    }
    result = run(["gh", "api", "graphql", "--input", "-"], input=json.dumps(body), capture=True, env=env)
    blob = json.loads(result.stdout)["data"]["repository"]["object"]
    if blob is None:
        return None
    text = blob.get("text")
    if not isinstance(text, str):
        raise Failure(f"couldn't read {path} at {head}: it isn't a text file")
    return text


def commit_to_tap(path: str, text: str, message: str, head: str, token: str, previous: str | None) -> None:
    """Commit one file to the tap's main through GitHub's API, as the app `token` belongs to.

    GitHub signs a commit that an app creates this way, so the tap shows it as Verified. The
    commit applies only on top of `head`; if another repo updated the tap meanwhile, it goes on
    top of that instead, provided this formula hasn't changed.
    """
    env = {"GH_TOKEN": token}
    contents = base64.b64encode(text.encode("utf-8")).decode("ascii")
    for attempt in range(1, 6):
        body = {
            "query": COMMIT_ON_BRANCH,
            "variables": {
                "input": {
                    "branch": {"repositoryNameWithOwner": TAP, "branchName": "main"},
                    "message": {"headline": message},
                    "expectedHeadOid": head,
                    "fileChanges": {"additions": [{"path": path, "contents": contents}]},
                }
            },
        }
        result = run(
            ["gh", "api", "graphql", "--input", "-"], input=json.dumps(body), capture=True, check=False, env=env
        )
        if result.returncode == 0:
            url = json.loads(result.stdout)["data"]["createCommitOnBranch"]["commit"]["url"]
            say(f"committed {message} to {TAP}: {url}")
            return
        latest = run(
            ["gh", "api", f"repos/{TAP}/git/ref/heads/main", "--jq", ".object.sha"], capture=True, env=env
        ).stdout.strip()
        if latest == head:
            raise Failure(f"couldn't commit the formula to {TAP}: {(result.stdout + result.stderr).strip()}")
        current = _tap_formula(path, latest, env)
        if current == text:
            say(f"the tap already has {path}")
            return
        if current != previous:
            raise Failure(f"{path} changed in the tap; refusing to overwrite its concurrent update")
        say(f"the tap moved on; committing on top of it ({attempt})")
        head = latest
    raise Failure(f"couldn't commit the formula to {TAP}: it kept moving")
