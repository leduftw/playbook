"""Releasing the playbook itself: vX.Y.Z, then moving the vX tag repos pin.

Uses the same version rules as every other repo: the first release is 1.0.0
and each later one is exactly the next patch, minor or major.
"""

from __future__ import annotations

from pathlib import Path

from .. import semver
from ..util import Failure, gh, git, remote_tags, repository, say, summary


def main(root: Path, sha: str) -> None:
    version = semver.parse((root / "VERSION").read_text(encoding="utf-8"))
    if str(version) == semver.UNRELEASED:
        say("VERSION is 0.0.0: the playbook hasn't been released yet")
        return
    tags = remote_tags(root)
    latest = semver.latest_tag(tags)
    if latest == version:
        say(f"{version.tag} is already released")
    else:
        problem = semver.check_next(latest, version)
        if problem:
            raise Failure(problem)
        repo = repository(root)
        gh(
            "release",
            "create",
            version.tag,
            "--repo",
            repo,
            "--target",
            sha,
            "--title",
            f"playbook {version}",
            "--generate-notes",
            "--latest",
        )
        say(f"published playbook {version}")

    major = f"v{version.major}"
    git("tag", "--force", major, sha, cwd=root)
    git("push", "--force", "origin", f"refs/tags/{major}", cwd=root)
    summary(f"playbook {version} is out; `{major}` now points at it, so repos pick it up on their next run.")
    say(f"moved {major} to {sha[:12]}")
