"""Checks a published tool against the playbook's command-line rules.

The rules are in the README's "How command-line tools behave". The release
smoke test runs this against the real binary on every platform. It tests
behavior, not layout: which words and flags work, what goes to stdout and
stderr, the exit codes, and that output away from a terminal stays plain.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

UNKNOWN_OPTION = "--not-an-option-of-this-tool"

# A bare run gets no credentials, as on a fresh machine or in WinGet's validation.
_CREDENTIAL = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|CREDENTIALS?)(_|$)", re.IGNORECASE)

# Settings that ask programs to color their output even into a pipe. Colors
# reach only a terminal, so every run sets them and expects plain text.
_FORCE_COLORS = {"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "PYTHON_COLORS": "1", "TERM": "xterm-256color"}


def check(binary: Path, name: str, version: str, timeout: float = 60) -> list[str]:
    """Runs the binary the ways the rules describe and returns what's wrong, if anything."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="playbook-command-line-") as temp:
        home = Path(temp)
        environment = _fresh_environment(home)

        def run(*args: str, code: int, stdout: bool | None = True, stderr: bool | None = False) -> str:
            shown = " ".join([name, *args])
            try:
                result = subprocess.run(
                    [str(binary), *args],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=environment,
                    cwd=home,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                problems.append(f"`{shown}` was still running after {timeout:g} seconds")
                return ""
            if result.returncode != code:
                problems.append(f"`{shown}` exited {result.returncode}, not {code}")
            for stream, wanted, text in (("stdout", stdout, result.stdout), ("stderr", stderr, result.stderr)):
                if wanted is True and not text.strip():
                    problems.append(f"`{shown}` printed nothing to {stream}")
                elif wanted is False and text.strip():
                    problems.append(f"`{shown}` printed to {stream}: {text.strip()[:200]!r}")
                if "\x1b[" in text:
                    problems.append(f"`{shown}` printed color codes to {stream}, which isn't a terminal")
            return result.stdout

        helps = {form: run(form, code=0) for form in ("-h", "--help", "help")}
        if len(set(helps.values())) > 1:
            problems.append(f"`{name} -h`, `{name} --help` and `{name} help` print different help")

        wanted = f"{name} {version}"
        for form in ("-V", "--version", "version"):
            printed = run(form, code=0).strip()
            if printed and printed != wanted:
                problems.append(f"`{name} {form}` printed {printed!r}, not {wanted!r}")

        run(UNKNOWN_OPTION, code=2, stdout=False, stderr=True)

        updates = [run(form, "--help", code=0) for form in ("update", "upgrade")]
        if updates[0] != updates[1]:
            problems.append(f"`{name} upgrade` isn't the same command as `{name} update`")

        # WinGet's validation starts the program this way: no arguments, no
        # terminal, no credentials. Something it can't do yet isn't a failure.
        run(code=0, stderr=None)
    return problems


def _fresh_environment(home: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not _CREDENTIAL.search(key)}
    environment.pop("NO_COLOR", None)
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CONFIG_DIRS": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_DATA_DIRS": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            **_FORCE_COLORS,
        }
    )
    if os.name == "nt":
        environment.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive) :])
    return environment
