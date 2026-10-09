"""Publishing a GitHub release: draft first, every file and checksum, then publish.

The tag is only created at the moment of publishing, after every file is in
place, so a failed run leaves no tag behind and the same version can be retried.
Published releases are locked; a re-run that finds its release already
published only confirms it carries the same files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import stacks
from ..config import Config
from ..util import Failure, gh, gh_json, remote_tags, repository, say, set_output, summary
from .installers import write_installers

CHECKSUMS = "SHA256SUMS"


def assemble(root: Path, config: Config, version: str, dist: Path) -> list[Path]:
    """Check the built archives, add the installers, and write SHA256SUMS."""
    release = config.release
    assert release is not None
    expected = sorted(stacks.archive_name(config.name, t) for t in release.targets)
    actual = sorted(p.name for p in dist.iterdir() if p.is_file())
    if actual != expected:
        raise Failure(f"the built archives don't match the targets\n  expected: {expected}\n  actual:   {actual}")
    if "installers" in release.channels:
        write_installers(config, repository(root), dist)
    files = sorted(p for p in dist.iterdir() if p.is_file() and p.name != CHECKSUMS)
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in files]
    (dist / CHECKSUMS).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    say(f"{len(files)} files and {CHECKSUMS} are ready for {config.name} {version}")
    return files


def _find_release(repo: str, tag: str) -> dict | None:
    releases = gh_json("api", f"repos/{repo}/releases?per_page=100", "--paginate", "--slurp")
    for page in releases or []:
        for item in page:
            if item["tag_name"] == tag:
                return item
    return None


def release(root: Path, config: Config, version: str, dist: Path, sha: str) -> None:
    repo = repository(root)
    tag = f"v{version}"
    files = sorted(p for p in dist.iterdir() if p.is_file())
    names = sorted(p.name for p in files)
    existing = _find_release(repo, tag)

    if existing and not existing["draft"]:
        published = sorted(asset["name"] for asset in existing["assets"])
        if published != names:
            raise Failure(f"{tag} is already published with different files: {published}")
        say(f"{tag} is already published with these files; nothing to do")
        set_output("released", "true")
        return

    if tag in remote_tags(root):
        raise Failure(f"the tag {tag} exists but has no published release; it can't be reused")

    if existing is None:
        gh(
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--draft",
            "--target",
            sha,
            "--title",
            f"{config.name} {version}",
            "--generate-notes",
        )
        say(f"created the draft release {tag}")
    else:
        say(f"reusing the draft release {tag}")
    gh("release", "upload", tag, "--repo", repo, "--clobber", *[str(p) for p in files])

    draft = _find_release(repo, tag)
    uploaded = sorted(asset["name"] for asset in (draft or {}).get("assets", []))
    if uploaded != names:
        raise Failure(f"the draft holds {uploaded}, expected {names}")

    gh("release", "edit", tag, "--repo", repo, "--draft=false", "--latest")
    if tag not in remote_tags(root):
        raise Failure(f"publishing {tag} didn't create its tag")
    url = f"https://github.com/{repo}/releases/tag/{tag}"
    say(f"published {url}")
    summary(f"Published [{config.name} {version}]({url}) with {len(files)} files.")
    set_output("released", "true")
