"""Repo topics and README badges: the parts of a repo's GitHub page that go stale.

A published repo's topics are how strangers find it, so they must be there and
must not mislead. A script can't judge whether a topic is apt, but it can check
that platform topics match where the program runs. Local repos need no topics.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .stacks import TARGETS
from .util import Failure

PLATFORMS = ("macos", "windows", "linux")
MAX_TOPICS = 20  # GitHub's limit


def validate(topics: list[str]) -> None:
    """GitHub's rules for topic names, checked before anything is created."""
    if len(topics) > MAX_TOPICS:
        raise Failure(f"GitHub allows at most {MAX_TOPICS} topics")
    for topic in topics:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", topic):
            raise Failure(f"topic {topic!r} must be lowercase letters, digits and hyphens, at most 50 characters")


def platforms(config: Config) -> list[str]:
    """The platform topics a published repo carries: every OS its releases target."""
    if config.release is None:
        return []
    released = {TARGETS[target]["os"] for target in config.release.targets}
    return [name for name in PLATFORMS if name in released]


def topic_problems(topics: list[str], config: Config) -> list[str]:
    if not config.published:
        return []
    if not topics:
        return ["a published repo needs topics (what it is, its language, its platforms)"]
    problems = []
    wanted = platforms(config)
    present = [name for name in PLATFORMS if name in topics]
    if present and present != wanted:
        problems.append(
            f"platform topics are {', '.join(present)}, but releases run on {', '.join(wanted)}; list them all or none"
        )
    return problems


def badge_problems(root: Path) -> list[str]:
    """README links to workflows that no longer exist, such as a removed ci.yml badge."""
    readme = root / "README.md"
    if not readme.is_file():
        return []
    workflows = root / ".github" / "workflows"
    missing = sorted(
        {
            name
            for name in re.findall(r"actions/workflows/([\w.-]+\.ya?ml)", readme.read_text(encoding="utf-8"))
            if not (workflows / name).is_file()
        }
    )
    return [f"README.md links to a workflow that doesn't exist: {name}" for name in missing]
