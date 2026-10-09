"""leduftw/playbook: one standard for how every repo is worked on, checked and released."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "leduftw/playbook"


def version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def major_ref() -> str:
    """The moving tag repos pin, such as v1. Before the first release it's v1 too."""
    major = version().split(".")[0]
    return f"v{major}" if major != "0" else "v1"
