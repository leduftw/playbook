"""playbook start / finish: the life of one change, from issue to merged PR.

start creates the change's worktree and branch from the latest main. finish
waits for the required checks, squash-merges, and removes every trace of the
branch, so worktrees never pile up.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path

from .config import load
from .util import Failure, gh, gh_json, git, run, say, warn

WORKTREES = Path(os.environ.get("PLAYBOOK_WORKTREES", Path.home() / "Developer" / ".worktrees"))


def slugify(title: str, limit: int = 40) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].rsplit("-", 1)[0] or slug[:limit]
    return slug or "change"


def primary_checkout(start: Path | None = None) -> Path:
    """The repo's main working tree, even when called from inside a linked worktree."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=start)
    return Path(common).parent


def in_linked_worktree(path: Path) -> bool:
    git_dir = git("rev-parse", "--path-format=absolute", "--git-dir", cwd=path)
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path)
    return Path(git_dir) != Path(common)


def github_user() -> str:
    return gh("api", "user", "--jq", ".login")


def ensure_hooks(root: Path) -> None:
    if (root / ".githooks").is_dir() and run(
        ["git", "config", "core.hooksPath"], cwd=root, check=False, capture=True
    ).stdout.strip() != ".githooks":
        git("config", "core.hooksPath", ".githooks", cwd=root)
        say("turned on the repo's git hooks (core.hooksPath=.githooks)")


def start(issue: int) -> Path:
    root = primary_checkout()
    config = load(root)
    ensure_hooks(root)
    data = gh_json("issue", "view", str(issue), "--json", "title,state,url", cwd=root)
    if data["state"] != "OPEN":
        raise Failure(f"issue #{issue} is {data['state'].lower()}; reopen it or pick an open one")
    slug = f"{issue}-{slugify(data['title'])}"
    branch = f"dev/{github_user()}/{slug}"
    path = WORKTREES / config.name / slug
    if path.exists():
        say(f"{path} already exists; continue there")
        print(path)
        return path

    git("fetch", "--quiet", "origin", cwd=root)
    local = run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=root, check=False, capture=True)
    remote = run(
        ["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"], cwd=root, check=False, capture=True
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if local.returncode == 0:
        git("worktree", "add", str(path), branch, cwd=root)
        say(f"resumed {branch}")
    elif remote.returncode == 0:
        git("worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}", cwd=root)
        say(f"resumed {branch} from GitHub")
    else:
        git("worktree", "add", "--no-track", "-b", branch, str(path), "origin/main", cwd=root)
        say(f"started {branch} from the latest main for {data['url']}")
    print(path)
    return path


def _has_required_checks(root: Path) -> bool:
    """Whether main has a ruleset with required checks (private repos need GitHub Pro)."""
    from .util import repository

    rules = gh_json("api", f"repos/{repository(root)}/rules/branches/main", cwd=root) or []
    return any(rule.get("type") == "required_status_checks" for rule in rules)


def _pr(cwd: Path, pr: str | None) -> dict:
    fields = "number,url,state,isDraft,headRefName,closingIssuesReferences,title"
    args = ["pr", "view", *([pr] if pr else []), "--json", fields]
    result = run(["gh", *args], cwd=cwd, check=False, capture=True)
    if result.returncode != 0:
        raise Failure("this branch has no PR yet; open one with: gh pr create --base main")
    return json.loads(result.stdout)


def finish(pr: str | None = None) -> None:
    cwd = Path.cwd()
    root = primary_checkout(cwd)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    if branch == "main" and pr is None:
        raise Failure("you're on main; run finish from the change's worktree, or pass the PR number")
    data = _pr(cwd, pr)
    branch = data["headRefName"]
    url = data["url"]

    if data["state"] == "OPEN":
        if data["isDraft"]:
            raise Failure(f"{url} is a draft; mark it ready for review first")
        if git("status", "--porcelain", cwd=cwd):
            raise Failure("there are uncommitted changes; commit them (or drop them) and run finish again")
        git("push", "--quiet", "-u", "origin", "HEAD", cwd=cwd)

        git("fetch", "--quiet", "origin", "main", cwd=cwd)
        behind = run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=cwd, check=False)
        if behind.returncode != 0:
            merged = run(["git", "merge", "--no-edit", "origin/main"], cwd=cwd, check=False, capture=True)
            if merged.returncode != 0:
                raise Failure("merging main into the branch hit conflicts; resolve them, commit, and run finish again")
            git("push", "--quiet", "origin", "HEAD", cwd=cwd)
            say("merged the latest main into the branch; checks start again")
            time.sleep(5)

        required = ["--required"] if _has_required_checks(root) else []
        say(f"waiting for the {'required ' if required else ''}checks on {url}")
        time.sleep(10)  # give GitHub a moment to register the checks of a fresh push
        checks = run(["gh", "pr", "checks", url, "--watch", *required, "--fail-fast", "--interval", "15"], check=False)
        if checks.returncode != 0:
            raise Failure("a required check failed; fix it on the branch, push, and run finish again")
        run(["gh", "pr", "merge", url, "--squash"])
        for _ in range(30):
            if _pr(cwd, url)["state"] == "MERGED":
                break
            time.sleep(2)
        else:
            raise Failure(f"{url} didn't show as merged; check it on GitHub")
        say(f"squash-merged {url}")
    elif data["state"] == "MERGED":
        say(f"{url} is already merged; cleaning up")
    else:
        raise Failure(f"{url} is closed without merging; nothing to land")

    cleanup(root, cwd, branch)
    for issue in data.get("closingIssuesReferences") or []:
        state = gh("issue", "view", str(issue["number"]), "--json", "state", "--jq", ".state", cwd=root)
        if state != "CLOSED":
            warn(f"issue #{issue['number']} is still open; close it if the work is done")
        else:
            say(f"issue #{issue['number']} is closed")
    say(f"done; the primary checkout is {root}")


def cleanup(root: Path, cwd: Path, branch: str) -> None:
    linked = in_linked_worktree(cwd)
    if linked:
        git("worktree", "remove", str(cwd), cwd=root)
        say(f"removed the worktree {cwd}")
    else:
        git("checkout", "--quiet", "main", cwd=root)
    run(["git", "branch", "-D", branch], cwd=root, check=False, capture=True)
    run(["git", "push", "--quiet", "origin", "--delete", branch], cwd=root, check=False, capture=True)
    if git("rev-parse", "--abbrev-ref", "HEAD", cwd=root) == "main":
        git("pull", "--quiet", "--ff-only", "origin", "main", cwd=root)
    else:
        warn("the primary checkout isn't on main, so it wasn't pulled")
    git("fetch", "--quiet", "--prune", "origin", cwd=root)
    say(f"deleted {branch} and pulled main")
