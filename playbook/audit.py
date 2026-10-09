"""playbook audit: does a repo still match the playbook?

Checks the repo's settings on GitHub, its ruleset and labels, the files the
playbook owns, and, for published repos, locked releases, the release
environment and the secrets each channel needs. Nothing is changed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import render, version
from .config import ConfigError, load
from .settings import LABELS, REPO_SETTINGS, REQUIRED_CHECKS, find_ruleset, missing_release_setup, ruleset_body
from .sync import _is_playbook, clone
from .util import Failure, gh_json, run, say


def audit_repo(repo: str) -> list[str]:
    """Problems found in one repo; an empty list means it matches the playbook."""
    problems: list[str] = []
    info = gh_json("api", f"repos/{repo}")
    if info["default_branch"] != "main":
        problems.append(f"the default branch is {info['default_branch']}, not main")
    for key, wanted in REPO_SETTINGS.items():
        if info.get(key) != wanted:
            if key == "allow_auto_merge" and info.get("private"):
                problems.append("auto-merge on private repos needs GitHub Pro")
            else:
                problems.append(f"setting {key} is {info.get(key)!r}, not {wanted!r}")

    labels = {label["name"] for label in gh_json("api", f"repos/{repo}/labels?per_page=100") or []}
    missing_labels = sorted(set(LABELS) - labels)
    if missing_labels:
        problems.append(f"labels missing: {', '.join(missing_labels)}")

    try:
        ruleset = find_ruleset(repo)
        if ruleset is None:
            problems.append("main has no playbook ruleset")
        else:
            contexts = set()
            for rule in ruleset.get("rules", []):
                if rule["type"] == "required_status_checks":
                    contexts = {c["context"] for c in rule["parameters"]["required_status_checks"]}
            if contexts != set(REQUIRED_CHECKS):
                problems.append(f"the ruleset requires {sorted(contexts)}, not {REQUIRED_CHECKS}")
            if ruleset.get("enforcement") != "active":
                problems.append("the ruleset isn't active")
            wanted = {rule["type"]: rule.get("parameters") for rule in ruleset_body()["rules"]}
            for rule in ruleset.get("rules", []):
                expected = wanted.get(rule["type"])
                if rule["type"] == "pull_request" and expected:
                    for key, value in expected.items():
                        if rule["parameters"].get(key) != value:
                            problems.append(
                                f"the ruleset's pull_request rule has {key}={rule['parameters'].get(key)!r}"
                            )
    except Failure as error:
        problems.append(str(error))

    with tempfile.TemporaryDirectory(prefix="playbook-audit-") as temp:
        root = clone(repo, Path(temp) / repo.split("/")[1])
        try:
            config = load(root)
        except ConfigError as error:
            problems.append(str(error))
            return problems
        problems += audit_files(root)
        if config.published:
            immutable = run(["gh", "api", f"repos/{repo}/immutable-releases"], check=False, capture=True)
            if '"enabled":true' not in immutable.stdout.replace(" ", ""):
                problems.append("releases aren't locked once published (immutable releases are off)")
            problems += missing_release_setup(repo, config)
            secrets = gh_json("api", f"repos/{repo}/actions/secrets") or {}
            stale = [s["name"] for s in secrets.get("secrets", [])]
            if stale:
                problems.append(f"repo-level secrets should live in the release environment: {', '.join(stale)}")
    return problems


def audit_files(root: Path) -> list[str]:
    config = load(root)
    self_repo = _is_playbook(root)
    problems = []
    for relative, content in render.managed_files(root, config, self_repo).items():
        path = root / relative
        if not path.is_file():
            problems.append(f"{relative} is missing")
        elif path.read_text(encoding="utf-8") != content:
            problems.append(f"{relative} differs from playbook {version()}")
    agents, claude = root / "AGENTS.md", root / "CLAUDE.md"
    if not agents.is_file() or not claude.is_file():
        problems.append("AGENTS.md and CLAUDE.md must both exist")
    else:
        text = agents.read_text(encoding="utf-8")
        if text != claude.read_text(encoding="utf-8"):
            problems.append("AGENTS.md and CLAUDE.md differ")
        if render.agents_md(text, config, self_repo) != text:
            problems.append(f"the playbook section of AGENTS.md differs from playbook {version()}")
    return problems


def report(repo: str, problems: list[str]) -> None:
    if problems:
        say(f"✗ {repo}")
        for problem in problems:
            say(f"    - {problem}")
    else:
        say(f"✓ {repo}")
