"""The one-line installers: rendering them for a repo, and proving they work.

The smoke test serves the freshly built archive from a local web server and
runs the real installer against it twice: once to install, once to upgrade in
place. On Windows the second pass also holds the installed binary open, the way
an antivirus scanner would, so the installer's retry path is exercised too.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import re
import shlex
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .. import ROOT, stacks
from ..config import Config
from ..util import Failure, repository, run, say

TEMPLATES = ROOT / "installers"


def env_prefix(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", name.upper())


def _shell_notes(lines: list[str], indent: str = "  ") -> list[str]:
    return [f"{indent}echo {shlex.quote(line)}" for line in lines]


def render_sh(config: Config, repo: str) -> str:
    release = config.release
    assert release is not None
    notes = _shell_notes(release.installer.notes)
    for platform, lines in (("macos", release.installer.macos_notes), ("linux", release.installer.linux_notes)):
        if lines:
            notes += [f'  if [ "$platform" = {platform} ]; then', *_shell_notes(lines, "    "), "  fi"]
    targets = [t for t in release.targets if not t.startswith("windows")]
    values = {
        "NAME": config.name,
        "REPOSITORY": repo,
        "PREFIX": env_prefix(config.name),
        "TARGETS": " ".join(targets),
        "MACOS_MINIMUM": release.macos_minimum or "",
        "BINARIES": " ".join(release.binaries),
        "MACOS_BINARIES": " ".join(release.macos_binaries),
        "MAIN": release.binaries[0],
        "VERSION_ARGS": shlex.join(release.version_command),
        "NOTES": "\n".join(notes),
    }
    return _fill("install.sh", values)


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def render_ps1(config: Config, repo: str) -> str:
    release = config.release
    assert release is not None
    notes = [f"    Write-Host {_ps_quote(line)}" for line in release.installer.notes + release.installer.windows_notes]
    targets = [t for t in release.targets if t.startswith("windows")]
    values = {
        "NAME": config.name,
        "REPOSITORY": repo,
        "PREFIX": env_prefix(config.name),
        "WINDOWS_TARGETS": ", ".join(_ps_quote(t) for t in targets),
        "WINDOWS_BINARIES": ", ".join(_ps_quote(f"{b}.exe") for b in release.binaries),
        "VERSION_ARGS": " ".join(_ps_quote(arg) for arg in release.version_command),
        "NOTES": "\n".join(notes),
    }
    return _fill("install.ps1", values)


def _fill(name: str, values: dict[str, str]) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    missing = set(re.findall(r"@@(\w+)@@", text)) - set(values)
    if missing:
        raise Failure(f"installers/{name}: no value for {', '.join(sorted(missing))}")
    for key, value in values.items():
        text = text.replace(f"@@{key}@@", value)
    return text


def write_installers(config: Config, repo: str, dist: Path) -> list[Path]:
    release = config.release
    assert release is not None
    written = []
    if any(not t.startswith("windows") for t in release.targets):
        path = dist / f"{config.name}-installer.sh"
        path.write_text(render_sh(config, repo), encoding="utf-8", newline="\n")
        path.chmod(0o755)
        written.append(path)
    if any(t.startswith("windows") for t in release.targets):
        path = dist / f"{config.name}-installer.ps1"
        path.write_text(render_ps1(config, repo), encoding="utf-8", newline="\n")
        written.append(path)
    return written


# --- smoke ----------------------------------------------------------------


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def _serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def smoke(root: Path, config: Config, target: str, version: str, dist: Path) -> None:
    release = config.release
    if release is None or "installers" not in release.channels:
        say("this repo doesn't ship the one-line installers; nothing to test")
        return
    archive = dist / stacks.archive_name(config.name, target)
    os_name = stacks.TARGETS[target]["os"]
    repo = repository(root)

    with tempfile.TemporaryDirectory(prefix="playbook-installer-") as temp:
        work = Path(temp)
        served = work / "served"
        served.mkdir()
        shutil.copyfile(archive, served / archive.name)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (served / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        install_dir = work / "bin"
        prefix = env_prefix(config.name)
        server, base = _serve(served)
        env = {
            f"{prefix}_DOWNLOAD_BASE": base,
            f"{prefix}_INSTALL_DIR": str(install_dir),
            f"{prefix}_NO_PATH_UPDATE": "1",
        }
        try:
            if os_name == "windows":
                _smoke_windows(config, repo, work, env, install_dir)
            else:
                script = work / "install.sh"
                script.write_text(render_sh(config, repo), encoding="utf-8")
                run(["sh", str(script)], env=env)
                run(["sh", str(script)], env=env)  # the second pass is an in-place upgrade
        finally:
            server.shutdown()

        suffix = ".exe" if os_name == "windows" else ""
        names = release.binaries + (release.macos_binaries if os_name == "macos" else [])
        for name in names:
            if not (install_dir / f"{name}{suffix}").is_file():
                raise Failure(f"the installer didn't install {name}{suffix}")
        leftovers = [p.name for p in install_dir.iterdir() if p.name.startswith(".")]
        if leftovers:
            raise Failure(f"the installer left staged files behind: {leftovers}")
        reported = run(
            [str(install_dir / f"{release.binaries[0]}{suffix}"), *release.version_command],
            capture=True,
        ).stdout.strip()
        if version not in reported:
            raise Failure(f"the installed {release.binaries[0]} reported {reported!r}, not {version}")
    say(f"the installer installed and upgraded {config.name} {version}")


def _smoke_windows(config: Config, repo: str, work: Path, env: dict[str, str], install_dir: Path) -> None:
    release = config.release
    assert release is not None
    script = work / "install.ps1"
    script.write_text(render_ps1(config, repo), encoding="utf-8")
    command = ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    run(command, env=env)

    # Hold the installed binary open without delete sharing until the upgrade
    # has staged its replacement, so the first replace attempt must fail and
    # the installer has to retry.
    installed = install_dir / f"{release.binaries[0]}.exe"
    stem = release.binaries[0]
    ready = threading.Event()
    failure: list[str] = []

    def hold() -> None:
        try:
            with open(installed, "rb"):
                ready.set()
                deadline = time.monotonic() + 60
                while not any(install_dir.glob(f".{stem}.*.new.exe")):
                    if time.monotonic() > deadline:
                        failure.append("the installer never staged its replacement")
                        return
                    time.sleep(0.01)
                time.sleep(0.75)
        except OSError as error:
            failure.append(str(error))
            ready.set()

    holder = threading.Thread(target=hold)
    holder.start()
    ready.wait(10)
    run(command, env=env)
    holder.join()
    if failure:
        raise Failure(f"the locked-file upgrade test failed: {failure[0]}")
