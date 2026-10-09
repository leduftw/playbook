"""playbook sync: bring the files the playbook owns in a repo up to date.

`sync --here` rewrites them in the current checkout. `sync <repo>...` (or
`--all`) does it in a fresh clone of each repo and proposes the result as a PR
labelled playbook, which merges itself once its checks pass.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import REPOSITORY, render, version
from .config import CONFIG_PATH, load
from .util import Failure, gh, gh_json, git, repository, run, say, warn


def sync_here(root: Path) -> list[str]:
    """Write the managed files into root; returns the paths that changed."""
    config = load(root)
    self_repo = _is_playbook(root)
    changed = []
    for relative, content in render.managed_files(root, config, self_repo).items():
        path = root / relative
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            changed.append(relative)
        if relative in render.EXECUTABLE:
            path.chmod(0o755)

    agents, claude = root / "AGENTS.md", root / "CLAUDE.md"
    existing = (
        agents.read_text(encoding="utf-8")
        if agents.is_file()
        else (claude.read_text(encoding="utf-8") if claude.is_file() else None)
    )
    updated = render.agents_md(existing, config, self_repo)
    for path in (agents, claude):
        if not path.is_file() or path.read_text(encoding="utf-8") != updated:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed.append(path.name)
    return changed


def _is_playbook(root: Path) -> bool:
    try:
        return repository(root) == REPOSITORY
    except Failure:
        return False


def owner() -> str:
    return gh("api", "user", "--jq", ".login")


def playbook_repos() -> list[str]:
    """Every repo of the signed-in user that has .github/playbook.toml."""
    login = owner()
    repos = gh_json("repo", "list", login, "--limit", "300", "--json", "nameWithOwner,isArchived,isFork") or []
    found = []
    for repo in repos:
        if repo["isArchived"] or repo["isFork"]:
            continue
        name = repo["nameWithOwner"]
        probe = run(["gh", "api", f"repos/{name}/contents/{CONFIG_PATH.as_posix()}"], check=False, capture=True)
        if probe.returncode == 0:
            found.append(name)
    return sorted(found)


def clone(repo: str, destination: Path) -> Path:
    run(["gh", "repo", "clone", repo, str(destination), "--", "--quiet", "--depth", "1"])
    return destination


def sync_repo(repo: str, dry_run: bool = False) -> str | None:
    """Sync one repo through a PR; returns its URL, or None when nothing changed."""
    with tempfile.TemporaryDirectory(prefix="playbook-sync-") as temp:
        root = clone(repo, Path(temp) / repo.split("/")[1])
        changed = sync_here(root)
        if not changed:
            say(f"{repo}: up to date with playbook {version()}")
            return None
        say(f"{repo}: {len(changed)} file(s) differ: {', '.join(changed)}")
        if dry_run:
            return None

        branch = f"playbook/sync-{version()}"
        open_prs = gh_json("pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "url") or []
        if open_prs:
            say(f"{repo}: a sync PR is already open: {open_prs[0]['url']}")
            return open_prs[0]["url"]
        git("switch", "--quiet", "-c", branch, cwd=root)
        git("add", "-A", cwd=root)
        git("commit", "--quiet", "-m", f"update playbook files to {version()}", cwd=root)
        pushed = run(["git", "push", "--quiet", "-u", "origin", branch], cwd=root, check=False, capture=True)
        if pushed.returncode != 0:
            raise Failure(f"{repo}: couldn't push {branch} (an old sync branch may exist): {pushed.stderr.strip()}")
        body = (
            f"Brings the files leduftw/playbook owns up to playbook {version()}: "
            + ", ".join(f"`{path}`" for path in changed)
            + ".\n\nNothing here is edited by hand; it merges itself once the checks pass."
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
            f"update playbook files to {version()}",
            "--body",
            body,
            "--label",
            "playbook",
            cwd=root,
        )
        # Without a ruleset, auto-merge has nothing to wait for and would merge
        # before the checks ran; such PRs are landed with playbook finish.
        if has_required_checks(repo):
            merged = run(["gh", "pr", "merge", url, "--auto", "--squash"], check=False, capture=True)
            if merged.returncode != 0:
                warn(f"{repo}: couldn't turn on auto-merge; land {url} with playbook finish")
        else:
            warn(f"{repo} has no ruleset on main (private repos need GitHub Pro); land {url} with playbook finish")
        say(f"{repo}: opened {url}")
        return url


def has_required_checks(repo: str) -> bool:
    """Whether main has a ruleset that makes PRs wait for required checks."""
    result = run(["gh", "api", f"repos/{repo}/rules/branches/main"], check=False, capture=True)
    if result.returncode != 0:
        return False
    return any(rule.get("type") == "required_status_checks" for rule in json.loads(result.stdout or "[]"))
