# playbook

The standard every one of my repos follows, and the tooling that applies it. Read
`README.md` first: it is the standard, written for people.

## Layout

- `bin/playbook`: the command. Plain Python 3.11+, standard library only; it runs on
  this Mac and on every GitHub runner, so it must stay dependency-free.
- `playbook/`: the code. `config.py` reads a repo's `.github/playbook.toml`,
  `stacks.py` holds what each language stack means, `render.py` produces the files
  the playbook owns in every repo, and `ci/` holds the steps the shared pipeline runs.
- `templates/`: the source of every file synced into repos (hooks, the two small
  workflow files, release-note settings, the shared `AGENTS.md` section).
- `installers/`: the one-line installer templates attached to every release.
- `.github/workflows/pipeline.yml`: the shared pipeline repos call as `@v1`.
  `title.yml` is the PR-title check; `self-release.yml` releases the playbook.
- `.github/actions/registry/`: the registry upload, which runs inside each repo's
  own workflow because registries match trusted publishing against that file.

## Checks

```sh
python3 -m unittest discover -s tests -t .
uvx ruff@0.17.0 check . && uvx ruff@0.17.0 format --check .
actionlint
```

## Things that are the way they are for a reason

- **Repos pin `@v1`, and the pipeline is told its own version.** A reusable
  workflow can't discover which playbook commit it runs from, so every repo's
  managed workflow passes `playbook-ref: v1` next to `@v1`. Both come from one
  template, so they can't disagree. This repo calls `./.github/workflows/pipeline.yml`
  with its own commit, so a PR here tests the pipeline it changes.
- **Pipeline scripts are fetched into `$RUNNER_TEMP`, not checked out into the
  workspace.** A copy inside the workspace would be linted and tested as part of the
  calling repo.
- **The registry upload is a job in each repo's own `playbook.yml`.** crates.io,
  NuGet and PyPI match trusted publishing against the workflow file that publishes;
  none of them documents support for a workflow in another repo.
- **NuGet packages are packed on a Mac.** A .NET tool built per platform is one
  package per runtime, and only a Mac can build the Mac-only binaries a macOS
  package carries (`macos-binaries`, such as monad's Swift capture helper). So a
  NuGet repo's channels dry run and registry job run on macOS; crates.io and PyPI
  uploads are the same from any system and stay on Linux.
- **A standalone Python program is a single file.** The archives, the installers,
  Homebrew and WinGet all expect one program, so PyInstaller's folder mode would
  change all of them. The cost is that the program unpacks itself on every start,
  which takes a couple of seconds on a Mac, because macOS scans each new copy. That
  suits tools whose real work takes minutes; a tool that starts often shouldn't be
  shipped this way.
- **`ci/frozen_runtime.py` runs first in every standalone Python program.** The
  bundle holds the build machine's Python, whose OpenSSL looks for certificates
  where that machine keeps them, and PyInstaller's bootloader hands its bundled
  libraries to any program the tool starts. Neither shows up on the machine that
  built it. A tool's own release smoke test has to prove them, as subtitler's does,
  because only the tool knows how to start another program.
- **A standalone Python program's archive holds Python's licence, and no others.**
  It also bundles libraries with licences of their own, such as OpenSSL, and which
  ones depends on the system: no single file on a build machine lists them all.
  Python's licence file only covers them in part, and only on Windows.
- **A first release can't be on WinGet.** The first submission is by hand and needs
  the release's files, so for version 1.0.0 a missing package only warns.
- **A change to anything that renders into repos ships as a release.** Merged
  changes reach repos only when `VERSION` moves. A change that needs repos to change
  is a new major.
- **Whatever merges a PR sets the commit's subject.** PR titles start with a capital
  and commits don't, and GitHub would copy the title. So `finish`, `sync`'s auto-merge
  and the pipeline's Dependabot job all pass `--subject`, and anything new that merges
  a PR must too.
- **The workflow is called `CI`, but its file stays `playbook.yml`.** The README badge
  shows the display name, and crates.io, NuGet and PyPI match trusted publishing
  against the file name. `gh workflow run` and `workflow_run` triggers go by the
  display name, so documented commands name the file, and horizon's `Release` trigger
  lists `CI`.
- **The installers keep their whole body in `main`, called on the last line.**
  People pipe them into `sh`, and a shell reading a pipe runs each command as it
  arrives, so a dropped connection must not run half the script.

<!-- playbook:begin -->
## How work lands here

> Managed by [leduftw/playbook](https://github.com/leduftw/playbook) v1. Change it there, not here; `playbook sync` brings this section back in line.

The facts about this repo live in `.github/playbook.toml`. Everything in this section is the same in every repo that follows the playbook.

### Every change

1. **Start from an issue.** Reuse the issue that describes the change, or create one assigned to `leduftw` with at least one label.
2. **Work in your own worktree.** Run `playbook start <issue>`: it creates `~/Developer/.worktrees/playbook/<issue>-<slug>` on branch `dev/leduftw/<issue>-<slug>`, based on the latest `main`. Work only there. Another session may be working at the same time, so never edit the primary checkout; it stays on `main` and only moves with `git pull`.
3. **Commit and push** each finished slice to that branch, without pausing for confirmation.
4. **Open the PR** with `gh pr create --base main`. Its title follows the PR title style below. Put `Closes #<issue>` in the body when the PR fully resolves the issue.
5. **Land it with `playbook finish`.** It waits for the required checks, merges `main` into the branch if `main` has moved on, squash-merges, removes the worktree and the branch, pulls `main`, and confirms the issue is closed. If a check fails, fix it on the branch and run `playbook finish` again.

Sessions that don't change this repo's files (reading, planning, answering questions, running its tools) need no issue and no worktree.

**Never** commit or push to `main` (hooks and a GitHub ruleset refuse it), rebase a branch that's already pushed, or force-push. To catch up with `main`, merge it into your branch.

**Commit style:** start with a lowercase verb that says what the change does, then plain words, with no `feat:`-style prefix and no full stop. Names keep their capitals: `fix Windows installer replacement`, `add opt-in global leaderboard`.

**PR title style:** the same words with a capital first letter: `Fix Windows installer replacement`. The `playbook / title` check rejects titles that break this. A PR title doesn't become the commit: `playbook finish` gives the squash commit on `main` the commit style, so don't use GitHub's merge button.

**Without the `playbook` command** (a cloud session, a fresh machine), do the same by hand:

- start: `git fetch origin`, then `git worktree add -b dev/leduftw/<issue>-<slug> ~/Developer/.worktrees/playbook/<issue>-<slug> origin/main` (a cloud session is already isolated, so a branch from `origin/main` is enough)
- finish: `gh pr checks --watch --required`, then `gh pr merge --squash --subject "<the PR title with a lowercase first letter> (#<PR number>)"`; once the PR shows `MERGED`, `git worktree remove <path>`, `git branch -D <branch>` (`-d` refuses, because squash commits aren't ancestors of `main`) and `git pull --ff-only` in the primary checkout

### Command-line tools

A command-line tool here follows the [Command Line Interface Guidelines](https://clig.dev) and the playbook's [rules for command-line tools](https://github.com/leduftw/playbook#how-command-line-tools-behave), in short:

- `-h`/`--help` print the help, which describes the tool with the repo's GitHub description written as a sentence (a capital first letter and a full stop), and `-V`/`--version` print exactly `<name> <version>`, both to stdout with exit 0. Every tool also takes `help [<command>]` and `version`, even one whose arguments are data: `./help` reaches a file with that name.
- A wrong command, option or value prints a message to stderr and exits 2. A command that ran and failed exits 1.
- Started with no arguments at all, the tool does its default job. If something that job needs isn't set up yet (a token, a terminal), it prints the help plus one line saying what's missing, and exits 0. An explicit command still fails when its requirements are missing; an existing config that can't be read is an error either way.
- Help uses the colors of Python 3.14's `argparse`, only in a terminal, and none with `NO_COLOR`, `TERM=dumb` or `--no-color`.
- Arguments are parsed by the language's standard parser (`argparse`, `clap`, `System.CommandLine`), never by hand.

### Releasing the playbook

Repos pin the playbook as `v1`, so a release is what carries a change to them.

- `playbook release patch|minor|major` opens a PR that only bumps `VERSION`. Use patch for fixes, minor for anything repos can take without changing, and major when repos must change too; a new major reaches each repo through its own PR.
- Landing that PR publishes `vX.Y.Z` and moves `vX`, so every repo on that major picks the change up on its next run.
<!-- playbook:end -->
