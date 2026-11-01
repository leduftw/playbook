"""What every README shares: the same opening, and the same sections when published.

The opening is the repo's name and its GitHub description as a sentence, with
the CI and latest-release badges between them in a published repo. The rest
belongs to the repo, so audit only checks that a published README has the
sections people look for, in the order they look for them.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import REPOSITORY
from .config import Config
from .util import sentence

OWNER = REPOSITORY.split("/")[0]
SECTIONS = ("Install", "Usage", "Development", "License")


def badges(name: str) -> list[str]:
    home = f"https://github.com/{OWNER}/{name}"
    workflow = f"{home}/actions/workflows/playbook.yml"
    return [
        f"[![CI]({workflow}/badge.svg)]({workflow})",
        f"[![Latest release](https://img.shields.io/github/v/release/{OWNER}/{name})]({home}/releases/latest)",
    ]


def scaffold(name: str, description: str, published: bool) -> str:
    """The README `playbook new` starts a repo with."""
    if not published:
        return f"# {name}\n\n{sentence(description)}\n"
    blocks = [
        f"# {name}",
        "\n".join(badges(name)),
        sentence(description),
        f"## Install\n\n```sh\nbrew install {OWNER}/tap/{name}\nwinget install {OWNER}.{name}\n```",
        f"## Usage\n\n```sh\n{name} --help\n```",
        "## Development\n\nThe checks this repo runs are in `.github/playbook.toml`.",
        "## License\n\nReleased under the [MIT License](LICENSE).",
    ]
    return "\n\n".join(blocks) + "\n"


def problems(root: Path, config: Config, description: str | None = None) -> list[str]:
    """How a README differs from the standard. With no description given, its sentence isn't checked."""
    readme = root / "README.md"
    if not readme.is_file():
        return ["README.md is missing"]
    text = readme.read_text(encoding="utf-8")
    found = []
    title, _, body = text.partition("\n")
    if title != f"# {config.name}":
        found.append(f"README.md must start with the title `# {config.name}`, not `{title}`")

    body = body.lstrip("\n")
    shown = re.match(r"(?:\[!\[.*\n)*", body).group()
    body = body[len(shown) :].lstrip("\n")
    if config.published and shown.splitlines() != badges(config.name):
        found.append("README.md must show the CI and latest-release badges right after the title")

    if description == "":
        found.append("the repo has no GitHub description")
    elif description and not " ".join(body.split()).startswith(sentence(description)):
        found.append(f"README.md must open with the repo's description as a sentence: {sentence(description)}")

    if config.published:
        present = [heading for heading in re.findall(r"(?m)^## +(.+?) *$", text) if heading in SECTIONS]
        if present != list(SECTIONS):
            found.append(f"a published README needs the sections {', '.join(SECTIONS)}, in that order")
    return found
