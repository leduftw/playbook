"""playbook new: start a repo that follows the playbook from its first commit.

The first question for any new repo is whether it's published (other people
install it) or local; this command refuses to guess. It creates the project,
the playbook's files, the GitHub repo and its settings, and lists whatever is
left for a human (such as registry trusted-publishing setup).
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from . import settings
from .config import load
from .sync import sync_here
from .util import Failure, gh, git, run, say

DEVELOPER = Path.home() / "Developer"

GITIGNORE = {
    "rust": ["/target"],
    "dotnet": ["bin/", "obj/", "*.user"],
    "python": ["__pycache__/", "*.pyc", ".venv/", "dist/", "*.egg-info/"],
    "node": ["node_modules/", "dist/"],
}


def validate_description(description: str) -> None:
    """House style for repo descriptions: lowercase first, no full stop, plain words."""
    if not description or not description[0].islower():
        raise Failure('the description must start with a lowercase letter, e.g. "subtitles for any video"')
    if description.endswith("."):
        raise Failure("the description must not end with a full stop")


def config_text(name: str, published: bool, stack: str, description: str) -> str:
    lines = [
        "# Facts about this repo for leduftw/playbook. Everything else comes from the playbook.",
        f'name = "{name}"',
        f"published = {'true' if published else 'false'}",
        "",
        "[[check]]",
        f'stack = "{stack}"',
    ]
    if stack == "node":
        lines += [
            'skip.format = "no formatter is set up yet"',
            'skip.lint = "no linter is set up yet"',
        ]
    if published:
        registry = {"rust": "crates-io", "dotnet": "nuget"}[stack]
        desc = description[0].upper() + description[1:]
        lines += ["", "[release]", f'stack = "{stack}"']
        if stack == "dotnet":
            pascal = "".join(part.capitalize() for part in re.split(r"[-_]", name))
            lines.append(f'project = "{pascal}/{pascal}.csproj"')
        lines += [
            f'channels = ["homebrew", "winget", "installers", "{registry}"]',
            "",
            "[release.homebrew]",
            f'desc = "{desc}"',
        ]
    return "\n".join(lines) + "\n"


def scaffold(root: Path, name: str, stack: str, published: bool) -> None:
    if stack == "rust":
        run(["cargo", "init", "--vcs", "none", "--name", name, "--quiet"], cwd=root)
        manifest = root / "Cargo.toml"
        manifest.write_text(
            re.sub(r'(?m)^version = "[^"]*"', 'version = "0.0.0"', manifest.read_text(encoding="utf-8"), count=1),
            encoding="utf-8",
        )
        run(["cargo", "generate-lockfile", "--quiet"], cwd=root)
    elif stack == "dotnet":
        pascal = "".join(part.capitalize() for part in re.split(r"[-_]", name))
        run(["dotnet", "new", "console", "--name", pascal, "--output", pascal], cwd=root)
        run(["dotnet", "new", "sln", "--name", pascal], cwd=root)
        solution = next(root.glob(f"{pascal}.sln*"))
        run(["dotnet", "sln", solution.name, "add", f"{pascal}/{pascal}.csproj"], cwd=root)
        project = root / pascal / f"{pascal}.csproj"
        text = project.read_text(encoding="utf-8")
        text = text.replace(
            "</PropertyGroup>",
            f"  <AssemblyName>{name}</AssemblyName>\n    <Version>0.0.0</Version>\n  </PropertyGroup>",
            1,
        )
        project.write_text(text, encoding="utf-8")
    elif stack == "python":
        package = name.replace("-", "_")
        (root / "src" / package).mkdir(parents=True)
        (root / "src" / package / "__init__.py").write_text(f'"""{name}."""\n', encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tests" / "test_import.py").write_text(
            "import unittest\n\n\nclass ImportTest(unittest.TestCase):\n"
            f'    def test_imports(self):\n        __import__("{package}")\n',
            encoding="utf-8",
        )
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n\n'
            '[tool.setuptools.packages.find]\nwhere = ["src"]\n',
            encoding="utf-8",
        )
    elif stack == "node":
        run(["npm", "init", "--yes", "--init-version", "0.0.0"], cwd=root, capture=True)
        run(["npm", "pkg", "set", "scripts.test=node --test"], cwd=root)
        run(["npm", "install", "--package-lock-only", "--silent"], cwd=root)


def new(name: str, published: bool, private: bool, stack: str, description: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise Failure("repo names are lowercase letters, digits and hyphens")
    validate_description(description)
    if published and stack not in ("rust", "dotnet"):
        raise Failure(f"publishing a {stack} project isn't supported yet; it's added the first time one ships")
    if published and private:
        raise Failure("a published repo must be public, so people can download its releases")
    login = gh("api", "user", "--jq", ".login")
    repo = f"{login}/{name}"
    root = DEVELOPER / name
    if root.exists():
        raise Failure(f"{root} already exists")
    if run(["gh", "repo", "view", repo], check=False, capture=True).returncode == 0:
        raise Failure(f"{repo} already exists on GitHub")

    root.mkdir(parents=True)
    git("init", "--quiet", "-b", "main", cwd=root)
    scaffold(root, name, stack, published)

    (root / "README.md").write_text(f"# {name}\n\n{description[0].upper() + description[1:]}.\n", encoding="utf-8")
    (root / ".gitignore").write_text("\n".join([".DS_Store", *GITIGNORE[stack]]) + "\n", encoding="utf-8")
    (root / ".gitattributes").write_text("* text=auto\n.githooks/* text eol=lf\n*.sh text eol=lf\n", encoding="utf-8")
    if not private:
        author = gh("api", "user", "--jq", ".login")
        year = datetime.date.today().year
        license_text = (Path(__file__).resolve().parent.parent / "templates" / "scaffold" / "LICENSE").read_text(
            encoding="utf-8"
        )
        (root / "LICENSE").write_text(
            license_text.replace("@@YEAR@@", str(year)).replace("@@AUTHOR@@", author), encoding="utf-8"
        )
    config_path = root / ".github" / "playbook.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text(name, published, stack, description), encoding="utf-8")
    sync_here(root)

    git("add", "--all", cwd=root)
    git("commit", "--quiet", "-m", f"start {name}", cwd=root)
    run(
        [
            "gh",
            "repo",
            "create",
            repo,
            "--private" if private else "--public",
            "--description",
            description,
            "--source",
            str(root),
            "--push",
        ],
    )
    git("config", "core.hooksPath", ".githooks", cwd=root)
    todo = settings.apply(repo, load(root))
    if published:
        binary = name
        todo.append(f"make `{binary} --version` print the version; every release's smoke test runs it")
        registry = "crates.io" if stack == "rust" else "NuGet"
        todo.append(
            f"on {registry}, add a trusted publisher: repository {repo}, workflow playbook.yml, environment release"
        )
        todo.append("after the first release, submit the first WinGet version by hand (wingetcreate new <url>)")
    say(f"created {repo} in {root}")
    if todo:
        say("still to do:")
        for item in todo:
            say(f"  - {item}")
