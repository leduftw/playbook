"""playbook release patch|minor|major: opens the PR that bumps the version.

Nobody types a version number. The next one is worked out from the latest
release (1.0.0 when there is none), written into the project file on its own
branch, and proposed as a PR whose checks build and smoke-test every release
file. Landing that PR publishes the release.
"""

from __future__ import annotations

from pathlib import Path

from . import semver, stacks
from .config import Config, load
from .settings import missing_release_setup
from .sync import _is_playbook
from .util import Failure, gh, git, remote_tags, repository, say
from .worktree import WORKTREES, ensure_hooks, primary_checkout


def _read_version(root: Path, config: Config, self_repo: bool) -> str:
    if self_repo:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    assert config.release is not None
    return stacks.read_version(root, config.release)


def _write_version(root: Path, config: Config, self_repo: bool, version: str) -> list[Path]:
    if self_repo:
        (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        return [root / "VERSION"]
    assert config.release is not None
    return stacks.write_version(root, config.release, version)


def open_release(kind: str) -> str:
    root = primary_checkout()
    config = load(root)
    self_repo = _is_playbook(root)
    release = config.release
    if release is None and not self_repo:
        raise Failure("this repo is local; only published repos have releases")
    repo = repository(root)

    problems = missing_release_setup(repo, config)
    if problems:
        raise Failure("the release can't publish until this is fixed:\n- " + "\n- ".join(problems))

    if git("rev-parse", "--abbrev-ref", "HEAD", cwd=root) != "main" or git("status", "--porcelain", cwd=root):
        raise Failure(f"the primary checkout ({root}) must be on a clean main")
    git("pull", "--quiet", "--ff-only", "origin", "main", cwd=root)

    latest = semver.latest_tag(remote_tags(root))
    current = _read_version(root, config, self_repo)
    expected = str(latest) if latest else semver.UNRELEASED
    if current != expected:
        raise Failure(
            f"the project file says {current}, but the latest release is {expected}; "
            "main must carry the released version before the next release starts"
        )
    version = semver.next_version(latest, kind)

    if latest is not None:
        changes = git("log", "--format=- %s", f"{latest.tag}..HEAD", cwd=root)
        if not changes:
            raise Failure(f"nothing has landed since {latest.tag}; there's nothing to release")
    else:
        changes = git("log", "--format=- %s", "HEAD", cwd=root)

    branch = f"release/{version.tag}"
    path = WORKTREES / config.name / f"release-{version}"
    if path.exists():
        raise Failure(f"{path} already exists; a release of {version} is in progress there")
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_hooks(root)
    git("worktree", "add", "--no-track", "-b", branch, str(path), "HEAD", cwd=root)
    changed = _write_version(path, config, self_repo, str(version))
    git("add", *[str(p.relative_to(path)) for p in changed], cwd=path)
    git("commit", "--quiet", "-m", f"release {version}", cwd=path)
    git("push", "--quiet", "-u", "origin", branch, cwd=path)

    if self_repo:
        effect = (
            f"Merging it publishes playbook {version} and moves v{version.major}, "
            "so every repo on that major picks it up on its next run."
        )
    else:
        assert release is not None
        channels = ", ".join(release.channels) or "nothing beyond GitHub Releases"
        effect = (
            "Its checks build every release file on all of its platforms and smoke-test them. "
            f"Merging it publishes the release on GitHub and updates {channels}."
        )
    body = "\n".join(
        [
            f"Releases {config.name} {version}. This PR only changes the version.",
            "",
            effect,
            "",
            f"Changes since {latest.tag if latest else 'the beginning'}:",
            "",
            changes[:60000],
        ]
    )
    url = gh(
        "pr",
        "create",
        "--repo",
        repo,
        "--base",
        "main",
        "--head",
        branch,
        "--title",
        f"release {version}",
        "--body",
        body,
        "--label",
        "release",
        cwd=path,
    )
    say(f"opened {url}")
    say(f"land it from {path} with: playbook finish")
    print(path)
    return url
