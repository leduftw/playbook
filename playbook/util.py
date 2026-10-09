"""Small helpers shared by the CLI and the pipeline steps."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


class Failure(Exception):
    """An expected failure: printed as a message, without a traceback."""


def say(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{message}", flush=True)
    else:
        print(f"warning: {message}", file=sys.stderr, flush=True)


def run(
    command: list[str],
    cwd: Path | str | None = None,
    check: bool = True,
    capture: bool = False,
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **env} if env else None
    result = subprocess.run(
        command,
        cwd=cwd,
        env=merged,
        text=True,
        input=input,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        shown = shlex.join(command)
        raise Failure(f"`{shown}` failed with exit code {result.returncode}" + (f":\n{detail}" if detail else ""))
    return result


def output(command: list[str], cwd: Path | str | None = None, env: dict[str, str] | None = None) -> str:
    return run(command, cwd=cwd, capture=True, env=env).stdout.strip()


def git(*args: str, cwd: Path | str | None = None) -> str:
    return output(["git", *args], cwd=cwd)


def gh(*args: str, cwd: Path | str | None = None) -> str:
    return output(["gh", *args], cwd=cwd)


def gh_json(*args: str, cwd: Path | str | None = None) -> Any:
    text = gh(*args, cwd=cwd)
    return json.loads(text) if text else None


def repository(root: Path | str = ".") -> str:
    """owner/name of the repo: from GitHub Actions, else from the origin remote."""
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = git("remote", "get-url", "origin", cwd=root)
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not match:
        raise Failure(f"origin ({url}) isn't a GitHub repository")
    return match.group(1)


def set_output(name: str, value: Any) -> None:
    """Write a step output for later jobs (stdout when run outside GitHub Actions)."""
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        say(f"{name}={text}")
        return
    with open(path, "a", encoding="utf-8") as handle:
        if "\n" in text:
            delimiter = f"EOF_{uuid.uuid4().hex}"
            handle.write(f"{name}<<{delimiter}\n{text}\n{delimiter}\n")
        else:
            handle.write(f"{name}={text}\n")


def summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip("\n") + "\n")
    else:
        say(markdown)


def remote_tags(root: Path | str = ".", remote: str = "origin") -> list[str]:
    lines = git("ls-remote", "--tags", "--refs", remote, cwd=root).splitlines()
    return [line.split("refs/tags/", 1)[1] for line in lines if "refs/tags/" in line]
