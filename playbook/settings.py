"""playbook settings: the GitHub-side settings every repo shares.

Squash is the only merge method, merged branches are deleted, main is protected
by a ruleset that requires a PR with the playbook's checks, and the standard
labels exist. Published repos also get locked (immutable) releases and a release
environment that only main can use, which holds a private key of the tap's app.
Applying is idempotent; audit reports anything that has drifted.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import Config
from .util import Failure, gh_json, run, say, warn

RULESET = "playbook"
ACTIONS_APP_ID = 15368  # GitHub Actions, so only workflow runs can satisfy the checks
REQUIRED_CHECKS = ["playbook / checks", "playbook / title"]
TAP = "leduftw/homebrew-tap"
# Releases commit to the tap as this GitHub App, installed only on the tap with
# Contents read and write. GitHub signs what an app commits through its API, so
# the tap shows those commits as Verified; a git push from a workflow can't be
# signed. Each repo's release environment holds one of the app's private keys.
TAP_APP = "leduftw-tap"
TAP_KEY_SECRET = "HOMEBREW_TAP_APP_PRIVATE_KEY"
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
                    # GitHub turns this on unless it's sent. It asks for a second
                    # person's approval, and these repos have nobody to give one.
                    "require_extra_approval_for_unattributed_changes": False,
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


def repository_secrets(repo: str) -> set[str]:
    result = _api("GET", f"repos/{repo}/actions/secrets", check=False)
    if result.returncode != 0:
        return set()
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
    # Jobs in the release environment also see repo-level secrets; audit asks
    # for those to move into the environment, but they work meanwhile.
    secrets |= repository_secrets(repo)
    if "homebrew" in release.channels and TAP_KEY_SECRET not in secrets:
        problems.append(
            f"{TAP_KEY_SECRET} is missing from the release environment: generate a private key at "
            f"https://github.com/settings/apps/{TAP_APP}, run: playbook settings {repo} --tap-key <file>, "
            "then delete the file"
        )
    if "winget" in release.channels and WINGET_SECRET not in secrets:
        problems.append(
            f"{WINGET_SECRET} is missing from the release environment: create a classic token with "
            f"public_repo, then run: gh secret set {WINGET_SECRET} --env release --repo {repo}"
        )
    return problems


def apply(repo: str, config: Config, tap_key: Path | None = None) -> list[str]:
    """Apply every shared setting; returns what still needs a human.

    `tap_key` is a private key of the tap's app, stored in a published repo's release
    environment. GitHub can't create one through its API, so a person generates it."""
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
        if tap_key and config.release and "homebrew" in config.release.channels:
            run(
                ["gh", "secret", "set", TAP_KEY_SECRET, "--env", "release", "--repo", repo],
                input=tap_key.read_text(encoding="utf-8"),
            )
            say(f"{repo}: stored the {TAP_APP} app's key, so its releases can commit to {TAP}")
        todo += missing_release_setup(repo, config)
    return todo
