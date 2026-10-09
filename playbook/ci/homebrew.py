"""The Homebrew formula: rendering it, test-installing it, and updating the tap.

Homebrew only installs formulae that live in a tap, so both the dry run on a
release PR and the real update install the formula from a tap before anything
is pushed. The dry run points at the archives just built; the real update
points at the published release.
"""

from __future__ import annotations

import hashlib
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
        if os_name == "linux":
            lines += [f"    depends_on {_ruby(dep)}" for dep in release.homebrew.linux_dependencies]
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
    """Point the formula at the published release, prove it installs, then push it."""
    repo = repository(root)
    digests = _digests(config, dist)
    urls = {
        target: f"https://github.com/{repo}/releases/download/v{version}/{stacks.archive_name(config.name, target)}"
        for target in digests
    }
    text = render(config, version, repo, urls, digests)
    formula = tap_dir / "Formula" / f"{config.name}.rb"
    formula.parent.mkdir(parents=True, exist_ok=True)
    formula.write_text(text, encoding="utf-8")
    run(["ruby", "-c", str(formula)])

    git("config", "user.name", "github-actions[bot]", cwd=tap_dir)
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=tap_dir)
    git("add", str(formula.relative_to(tap_dir)), cwd=tap_dir)
    if run(["git", "diff", "--cached", "--quiet"], cwd=tap_dir, check=False).returncode == 0:
        say(f"the tap already has {config.name} {version}")
        return
    git("commit", "-q", "-m", f"update {config.name} to {version}", cwd=tap_dir)

    with tempfile.TemporaryDirectory(prefix="playbook-tap-") as temp:
        copy = Path(temp) / "homebrew-tap"
        shutil.copytree(tap_dir, copy, ignore=shutil.ignore_patterns(".git"))
        git("init", "-q", "-b", "main", cwd=copy)
        git("add", ".", cwd=copy)
        git("-c", "user.name=playbook", "-c", "user.email=playbook@localhost", "commit", "-q", "-m", "verify", cwd=copy)
        _install_and_test(config, copy, "leduftw/playbook-verify", version)

    # Another repo may have updated the tap meanwhile; formulae are separate
    # files, so rebasing onto it never conflicts.
    for attempt in range(1, 6):
        if run(["git", "push", "-q", "origin", "HEAD:main"], cwd=tap_dir, check=False).returncode == 0:
            say(f"pushed {config.name} {version} to {TAP}")
            return
        git("pull", "-q", "--rebase", "origin", "main", cwd=tap_dir)
        say(f"the tap moved on; rebased and retrying ({attempt})")
    raise Failure(f"couldn't push the formula to {TAP}")
