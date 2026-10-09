"""Version numbers and the rules for moving from one release to the next.

The first release of anything is 1.0.0. Every later release is exactly one step
from the latest one: the next patch, the next minor or the next major. Nothing
else is accepted, so a project can neither start at 2.0.0 nor skip from 1.2.0
to 1.4.0.
"""

from __future__ import annotations

import re
from typing import NamedTuple

UNRELEASED = "0.0.0"
FIRST = "1.0.0"
BUMPS = ("patch", "minor", "major")

_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class Version(NamedTuple):
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def tag(self) -> str:
        return f"v{self}"


def parse(text: str) -> Version:
    """Parse a plain major.minor.patch version, with an optional leading v."""
    match = _PATTERN.fullmatch(text.strip().removeprefix("v"))
    if not match:
        raise ValueError(f"not a major.minor.patch version: {text!r}")
    return Version(*(int(part) for part in match.groups()))


def bump(version: Version, kind: str) -> Version:
    if kind == "patch":
        return Version(version.major, version.minor, version.patch + 1)
    if kind == "minor":
        return Version(version.major, version.minor + 1, 0)
    if kind == "major":
        return Version(version.major + 1, 0, 0)
    raise ValueError(f"unknown bump {kind!r}; use one of {', '.join(BUMPS)}")


def next_version(latest: Version | None, kind: str) -> Version:
    """The version a release of the given kind gets. A first release is 1.0.0."""
    if kind not in BUMPS:
        raise ValueError(f"unknown bump {kind!r}; use one of {', '.join(BUMPS)}")
    if latest is None:
        return parse(FIRST)
    return bump(latest, kind)


def allowed_next(latest: Version | None) -> list[Version]:
    if latest is None:
        return [parse(FIRST)]
    return [bump(latest, kind) for kind in BUMPS]


def check_next(latest: Version | None, candidate: Version) -> str | None:
    """Return why candidate can't follow latest, or None when it can."""
    allowed = allowed_next(latest)
    if candidate in allowed:
        return None
    if latest is None:
        return f"the first release must be {FIRST}, not {candidate}"
    options = ", ".join(str(version) for version in allowed)
    return f"{candidate} can't follow {latest}; the next release must be one of {options}"


def latest_tag(tags: list[str]) -> Version | None:
    """The highest vX.Y.Z among tags; anything else (v1, v2.0.0-rc1, ...) is ignored."""
    versions = []
    for tag in tags:
        if not tag.startswith("v"):
            continue
        try:
            versions.append(parse(tag))
        except ValueError:
            continue
    return max(versions, default=None)
