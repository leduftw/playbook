"""The playbook command. Run `playbook --help` for the list of commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import semver, version
from .config import ConfigError, load
from .util import Failure, say

DESCRIPTION = """\
One standard for how every repo is worked on, checked and released.

Everyday commands:
  start <issue>        create the worktree and branch for an issue
  finish               wait for checks, squash-merge, clean up
  release <bump>       open the PR that releases the next patch, minor or major

Keeping repos in line:
  new <name>           start a repo that follows the playbook
  sync                 bring the playbook's files in a repo up to date
  settings             apply the shared GitHub settings
  audit                report anything that differs from the playbook
"""


def _repos(args: argparse.Namespace) -> list[str]:
    from .sync import playbook_repos
    from .util import repository

    if getattr(args, "all", False):
        return playbook_repos()
    if args.repos:
        return [r if "/" in r else f"{_owner()}/{r}" for r in args.repos]
    return [repository(Path.cwd())]


def _owner() -> str:
    from .sync import owner

    return owner()


def cmd_start(args: argparse.Namespace) -> None:
    from .worktree import start

    start(args.issue)


def cmd_finish(args: argparse.Namespace) -> None:
    from .worktree import finish

    finish(args.pr)


def cmd_release(args: argparse.Namespace) -> None:
    from .release import open_release

    open_release(args.bump)


def cmd_new(args: argparse.Namespace) -> None:
    from .new import new

    if args.published == args.local:
        raise Failure("say whether the repo is --published (other people install it) or --local")
    new(args.name, args.published, args.private, args.stack, args.description)


def cmd_sync(args: argparse.Namespace) -> None:
    from .sync import sync_here, sync_repo

    if args.here:
        changed = sync_here(Path.cwd())
        say("updated: " + ", ".join(changed) if changed else f"already matches playbook {version()}")
        return
    for repo in _repos(args):
        sync_repo(repo, dry_run=args.dry_run)


def cmd_settings(args: argparse.Namespace) -> None:
    import tempfile

    from . import settings
    from .sync import clone

    todo = []
    for repo in _repos(args):
        with tempfile.TemporaryDirectory(prefix="playbook-settings-") as temp:
            root = clone(repo, Path(temp) / repo.split("/")[1])
            todo += settings.apply(repo, load(root))
    if todo:
        say("still to do:")
        for item in todo:
            say(f"  - {item}")


def cmd_audit(args: argparse.Namespace) -> int:
    from .audit import audit_files, audit_repo, report

    failed = False
    if args.here:
        problems = audit_files(Path.cwd())
        report("this checkout", problems)
        return 1 if problems else 0
    for repo in _repos(args):
        problems = audit_repo(repo)
        report(repo, problems)
        failed = failed or bool(problems)
    return 1 if failed else 0


def cmd_version(args: argparse.Namespace) -> None:
    print(version())


# --- pipeline steps --------------------------------------------------------


def cmd_ci(args: argparse.Namespace) -> None:
    root = Path.cwd()
    step = args.step
    if step == "plan":
        from .ci import plan

        plan.main(root)
        return
    if step == "self-release":
        from .ci import self_release

        self_release.main(root, args.sha)
        return

    config = load(root)
    dist = Path(args.dist) if args.dist else None
    if step == "build":
        from .ci import build

        build.build(root, config, args.target, args.version, Path(args.out))
    elif step == "smoke":
        from .ci import build

        build.smoke(root, config, args.target, args.version, dist)
    elif step == "smoke-installer":
        from .ci import installers

        installers.smoke(root, config, args.target, args.version, dist)
    elif step == "homebrew":
        from .ci import homebrew

        if args.dry_run:
            homebrew.dry_run(root, config, args.version, dist)
        else:
            homebrew.publish(root, config, args.version, dist, Path(args.tap))
    elif step == "channels":
        from .ci import registry

        registry.channels_dry_run(root, config, args.version)
    elif step == "assemble":
        from .ci import publish

        publish.assemble(root, config, args.version, dist)
    elif step == "release":
        from .ci import publish

        publish.release(root, config, args.version, dist, args.sha)
    elif step == "winget":
        from .ci import winget

        winget.submit(root, config, args.version)
    elif step == "registry-info":
        from .ci import registry

        registry.info(root, config)
    elif step == "registry":
        from .ci import registry

        registry.publish(root, config)
    elif step == "apt":
        from .util import run

        packages = config.release.apt if config.release else []
        if packages:
            run(["sudo", "apt-get", "update", "-q"])
            run(["sudo", "apt-get", "install", "-q", "-y", "--no-install-recommends", *packages])
    else:  # pragma: no cover - argparse rejects unknown steps
        raise Failure(f"unknown step {step}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="playbook", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = root.add_subparsers(dest="command", required=True, metavar="<command>")

    start = commands.add_parser("start", help="create the worktree and branch for an issue")
    start.add_argument("issue", type=int)
    start.set_defaults(func=cmd_start)

    finish = commands.add_parser("finish", help="wait for checks, squash-merge, clean up")
    finish.add_argument("pr", nargs="?", help="PR number or URL (default: this branch's PR)")
    finish.set_defaults(func=cmd_finish)

    release = commands.add_parser("release", help="open the PR that releases the next version")
    release.add_argument("bump", choices=semver.BUMPS)
    release.set_defaults(func=cmd_release)

    new = commands.add_parser("new", help="start a repo that follows the playbook")
    new.add_argument("name")
    new.add_argument("--published", action="store_true", help="other people install it")
    new.add_argument("--local", action="store_true", help="it stays on your machines")
    new.add_argument("--private", action="store_true", help="a private GitHub repo (local repos only)")
    new.add_argument("--stack", required=True, choices=("rust", "dotnet", "python", "node"))
    new.add_argument("--description", required=True, help='lowercase first, no full stop: "subtitles for any video"')
    new.set_defaults(func=cmd_new)

    for name, func, text in (
        ("sync", cmd_sync, "bring the playbook's files up to date"),
        ("settings", cmd_settings, "apply the shared GitHub settings"),
        ("audit", cmd_audit, "report anything that differs from the playbook"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("repos", nargs="*", help="owner/name or name (default: this repo)")
        command.add_argument("--all", action="store_true", help="every repo with .github/playbook.toml")
        if name in ("sync", "audit"):
            command.add_argument("--here", action="store_true", help="the current checkout, without GitHub")
        if name == "sync":
            command.add_argument("--dry-run", action="store_true", help="show what would change")
        command.set_defaults(func=func)

    show_version = commands.add_parser("version", help="print the playbook version")
    show_version.set_defaults(func=cmd_version)

    ci = commands.add_parser("ci", help="steps the shared pipeline runs (not for everyday use)")
    ci.add_argument(
        "step",
        choices=(
            "plan",
            "build",
            "smoke",
            "smoke-installer",
            "homebrew",
            "channels",
            "assemble",
            "release",
            "winget",
            "registry-info",
            "registry",
            "apt",
            "self-release",
        ),
    )
    ci.add_argument("--target")
    ci.add_argument("--version")
    ci.add_argument("--out")
    ci.add_argument("--dist")
    ci.add_argument("--tap")
    ci.add_argument("--sha")
    ci.add_argument("--dry-run", action="store_true")
    ci.add_argument("--publish", action="store_true")
    ci.add_argument("--release", action="store_true")
    ci.set_defaults(func=cmd_ci)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = args.func(args)
    except (Failure, ConfigError) as error:
        print(f"playbook: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return result if isinstance(result, int) else 0
