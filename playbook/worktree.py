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

from .config import ConfigError, load
from .util import Failure, gh, gh_json, git, repository, run, say, warn

WORKTREES = Path(os.environ.get("PLAYBOOK_WORKTREES", Path.home() / "Developer" / ".worktrees"))


def slugify(title: str, limit: int = 40) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    ascii_title = re.sub(r"['’]", "", ascii_title)  # "doesn't" -> "doesnt", not "doesn-t"
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


def repo_name(root: Path) -> str:
    """The name worktrees are grouped under: playbook.toml's, else the repo's own."""
    try:
        return load(root).name
    except ConfigError:
        return repository(root).split("/")[1]


def start(issue: int) -> Path:
    root = primary_checkout()
    name = repo_name(root)
    ensure_hooks(root)
    data = gh_json("issue", "view", str(issue), "--json", "title,state,url", cwd=root)
    if data["state"] != "OPEN":
        raise Failure(f"issue #{issue} is {data['state'].lower()}; reopen it or pick an open one")
    slug = f"{issue}-{slugify(data['title'])}"
    branch = f"dev/{github_user()}/{slug}"
    path = WORKTREES / name / slug
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


def wait_for_checks(url: str, timeout: int = 3600) -> None:
    """Wait until the playbook's two checks have passed on the PR's latest commit.

    They're waited for by name: the summary check only appears once the jobs
    it waits for are done, so "every reported check passed" can be true while
    it doesn't exist yet. Repos without a ruleset (private ones before GitHub
    Pro) get the same wait.
    """
    from .settings import REQUIRED_CHECKS

    deadline = time.monotonic() + timeout
    shown = None
    while True:
        rollup = gh_json("pr", "view", url, "--json", "statusCheckRollup")["statusCheckRollup"] or []
        latest: dict[str, dict] = {}
        for item in rollup:  # a re-run leaves several entries; the newest counts
            name = item.get("name") or item.get("context")
            if name not in latest or (item.get("startedAt") or "") >= (latest[name].get("startedAt") or ""):
                latest[name] = item
        states = {}
        for name in REQUIRED_CHECKS:
            item = latest.get(name)
            if item is None:
                states[name] = "not started"
            elif item.get("status", "COMPLETED") != "COMPLETED":
                states[name] = "running"
            else:
                states[name] = (item.get("conclusion") or item.get("state") or "").lower()
        failed = {n: s for n, s in states.items() if s not in ("success", "running", "not started")}
        if failed:
            details = ", ".join(f"{n}: {s}" for n, s in failed.items())
            raise Failure(f"{details}; fix it on the branch, push, and run finish again")
        if all(state == "success" for state in states.values()):
            say("checks passed: " + ", ".join(REQUIRED_CHECKS))
            return
        summary = ", ".join(f"{n}: {s}" for n, s in states.items())
        if summary != shown:
            say(f"waiting: {summary}")
            shown = summary
        if time.monotonic() > deadline:
            raise Failure(f"the checks didn't finish within {timeout // 60} minutes")
        time.sleep(15)


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

        wait_for_checks(url)
        for attempt in range(1, 6):
            merged = run(["gh", "pr", "merge", url, "--squash"], check=False, capture=True)
            if merged.returncode == 0:
                break
            if attempt == 5:
                raise Failure(f"GitHub refused to merge {url}: {merged.stderr.strip()}")
            time.sleep(10)
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


def remove_empty_folders(folder: Path, worktrees: Path) -> None:
    """Remove a repo's folder under worktrees, then worktrees itself, once empty.

    git worktree remove only deletes the worktree; the folder start created
    above it would otherwise stay behind. Only folders inside worktrees are
    touched, and a lone .DS_Store (Finder's) counts as empty.
    """
    candidates = [folder, worktrees] if folder.parent == worktrees else [worktrees]
    for directory in candidates:
        if not directory.is_dir():
            continue
        entries = list(directory.iterdir())
        if any(entry.name != ".DS_Store" for entry in entries):
            return
        for entry in entries:
            entry.unlink()
        directory.rmdir()


def cleanup(root: Path, cwd: Path, branch: str) -> None:
    linked = in_linked_worktree(cwd)
    if linked:
        git("worktree", "remove", str(cwd), cwd=root)
        say(f"removed the worktree {cwd}")
        remove_empty_folders(cwd.parent, WORKTREES)
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
