"""playbook settings: the GitHub-side settings every repo shares.

Squash is the only merge method, merged branches are deleted, main is protected
by a ruleset that requires a PR with the playbook's checks, and the standard
labels exist. Published repos also get locked (immutable) releases, a release
environment that only main can use, and their own deploy key for the tap.
Applying is idempotent; audit reports anything that has drifted.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .config import Config
from .util import Failure, gh_json, run, say, warn

RULESET = "playbook"
ACTIONS_APP_ID = 15368  # GitHub Actions, so only workflow runs can satisfy the checks
REQUIRED_CHECKS = ["playbook / checks", "playbook / title"]
TAP = "leduftw/homebrew-tap"
TAP_SECRET = "HOMEBREW_TAP_DEPLOY_KEY"
WINGET_SECRET = "WINGET_TOKEN"

REPO_SETTINGS = {
    "allow_squash_merge": True,
    "allow_merge_commit": False,
    "allow_rebase_merge": False,
    "delete_branch_on_merge": True,
    "allow_auto_merge": True,
    "allow_update_branch": True,
    "squash_merge_commit_title": "PR_TITLE",
    "squash_merge_commit_message": "PR_BODY",
}

LABELS = {
    "bug": ("d73a4a", "Something isn't working"),
    "enhancement": ("a2eeef", "New feature or request"),
    "documentation": ("0075ca", "Improvements or additions to documentation"),
    "dependencies": ("0366d6", "Updates a dependency"),
    "security": ("b60205", "Fixes a vulnerability or hardens something"),
    "ci": ("ededed", "Checks, builds and releases"),
    "release": ("5319e7", "Releases a new version"),
    "playbook": ("1d76db", "Keeps this repo in line with leduftw/playbook"),
}


def ruleset_body() -> dict:
    return {
        "name": RULESET,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                    "allowed_merge_methods": ["squash"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [
                        {"context": context, "integration_id": ACTIONS_APP_ID} for context in REQUIRED_CHECKS
                    ],
                },
            },
        ],
    }


def _api(method: str, path: str, body: dict | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["gh", "api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    if body is not None:
        command += ["--input", "-"]
    return run(command, check=check, capture=True, input=json.dumps(body) if body is not None else None)


def find_ruleset(repo: str) -> dict | None:
    result = _api("GET", f"repos/{repo}/rulesets", check=False)
    if result.returncode != 0:
        if "Upgrade to GitHub Pro" in result.stdout + result.stderr:
            raise Failure("rulesets on private repos need GitHub Pro")
        raise Failure(f"couldn't read rulesets: {result.stdout or result.stderr}")
    for item in json.loads(result.stdout or "[]"):
        if item["name"] == RULESET:
            return gh_json("api", f"repos/{repo}/rulesets/{item['id']}")
    return None


def environment_secrets(repo: str) -> set[str] | None:
    result = _api("GET", f"repos/{repo}/environments/release/secrets", check=False)
    if result.returncode != 0:
        return None
    return {secret["name"] for secret in json.loads(result.stdout).get("secrets", [])}


def missing_release_setup(repo: str, config: Config) -> list[str]:
    """What a published repo still needs before a release can publish everywhere."""
    release = config.release
    if release is None:
        return []
    problems = []
    secrets = environment_secrets(repo)
    if secrets is None:
        problems.append("the release environment doesn't exist (run: playbook settings)")
        secrets = set()
    if "homebrew" in release.channels and TAP_SECRET not in secrets:
        problems.append(f"{TAP_SECRET} is missing from the release environment (run: playbook settings)")
    if "winget" in release.channels and WINGET_SECRET not in secrets:
        problems.append(
            f"{WINGET_SECRET} is missing from the release environment: create a classic token with "
            f"public_repo, then run: gh secret set {WINGET_SECRET} --env release --repo {repo}"
        )
    return problems


def apply(repo: str, config: Config) -> list[str]:
    """Apply every shared setting; returns what still needs a human."""
    todo: list[str] = []
    info = gh_json("api", f"repos/{repo}")
    if info["default_branch"] != "main":
        raise Failure(f"{repo}'s default branch is {info['default_branch']}; the playbook requires main")

    _api("PATCH", f"repos/{repo}", REPO_SETTINGS)
    _api(
        "PUT",
        f"repos/{repo}/actions/permissions/workflow",
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
    )
    _api("PUT", f"repos/{repo}/vulnerability-alerts", check=False)
    _api("PUT", f"repos/{repo}/automated-security-fixes", check=False)
    say(f"{repo}: squash-only merges, auto-delete, read-only workflow token, Dependabot alerts")

    existing = {label["name"] for label in gh_json("api", f"repos/{repo}/labels?per_page=100") or []}
    for name, (color, description) in LABELS.items():
        if name not in existing:
            _api("POST", f"repos/{repo}/labels", {"name": name, "color": color, "description": description})
    say(f"{repo}: standard labels present")

    try:
        current = find_ruleset(repo)
        body = ruleset_body()
        if current is None:
            _api("POST", f"repos/{repo}/rulesets", body)
        else:
            _api("PUT", f"repos/{repo}/rulesets/{current['id']}", body)
        say(f"{repo}: main is protected (PR required, checks: {', '.join(REQUIRED_CHECKS)})")
    except Failure as error:
        warn(f"{repo}: {error}; main is protected only by the local hooks")
        todo.append(f"{repo}: subscribe to GitHub Pro, then run playbook settings again")

    if config.published:
        if info["private"]:
            todo.append(f"{repo} is published but private; people can't download its releases")
        _api("PUT", f"repos/{repo}/immutable-releases", check=False)
        _api(
            "PUT",
            f"repos/{repo}/environments/release",
            {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}},
        )
        policies = gh_json("api", f"repos/{repo}/environments/release/deployment-branch-policies") or {}
        if not any(p["name"] == "main" for p in policies.get("branch_policies", [])):
            _api(
                "POST",
                f"repos/{repo}/environments/release/deployment-branch-policies",
                {"name": "main", "type": "branch"},
            )
        say(f"{repo}: releases lock once published; the release environment only runs on main")
        if config.release and "homebrew" in config.release.channels:
            _ensure_tap_key(repo, config.name)
        todo += missing_release_setup(repo, config)
    return todo


def _ensure_tap_key(repo: str, name: str) -> None:
    """Give the repo its own deploy key on the tap, stored in its release environment."""
    secrets = environment_secrets(repo) or set()
    if TAP_SECRET in secrets:
        return
    title = f"{name} release"
    for key in gh_json("api", f"repos/{TAP}/keys") or []:
        if key["title"] == title:
            _api("DELETE", f"repos/{TAP}/keys/{key['id']}")
    with tempfile.TemporaryDirectory(prefix="playbook-key-") as temp:
        key_path = Path(temp) / "key"
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{repo} release", "-f", str(key_path)])
        public = key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()
        _api("POST", f"repos/{TAP}/keys", {"title": title, "key": public, "read_only": False})
        run(
            ["gh", "secret", "set", TAP_SECRET, "--env", "release", "--repo", repo],
            input=key_path.read_text(encoding="utf-8"),
        )
    say(f"{repo}: created its deploy key on {TAP} ({title})")


def tap_keys() -> list[str]:
    return [key["title"] for key in gh_json("api", f"repos/{TAP}/keys") or []]
